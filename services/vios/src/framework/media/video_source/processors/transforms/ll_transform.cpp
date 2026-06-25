/*
 * SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "ll_transform.h"
#include "logger.h"
#include "nvbufwrapper.h"
#include "nvvideoencoder.h"

constexpr int MAX_BUFFERS = 4;

NvLLTransform::NvLLTransform (const std::string& consumer_name) : IMediaDataConsumer(consumer_name)
{
    m_surfacePool = std::make_shared<NvSurfacePool>();
    m_transformThread = std::thread(&NvLLTransform::doTransformTask, this);
    m_swSurf = nullptr;
}

void NvLLTransform::setOriginalFrameSize(int w, int h)
{
    // Send to overlay if its the consumer of transform
    if (m_consumer)
    {
        m_consumer->setOriginalFrameSize(w, h);
    }
    m_width = w;
    m_height = h;
    m_sourceWidth = w;
    m_sourceHeight = h;
#ifdef JETSON_PLATFORM
    /* Update the overlay resolution when out-resolution is not specified and enc is present */
    Resolution resolution;
    resolution = GET_CONFIG().webrtc_out_default_resolution;
    if (resolution.empty() && NvHwDetection::getInstance()->m_useNvV4l2Enc == true)
    {
        m_width = w;
        m_height = h;
    }
#endif
}

void NvLLTransform::setIPCMeta ()
{
    if (GET_CONFIG().enable_ipc_path == true)
    {
        m_isIPCMeta = true;
        if (m_consumer)
        {
            m_consumer->setIPCMeta();
        }
    }
}

NvLLTransform::~NvLLTransform ()
{
    LOG(info) << "Entry " << __METHOD_NAME__ <<  endl;
    
    // Stop the transform properly
    m_stop = true;
    m_flowData = true;
    m_condVar.notify_all();
    
    // Wait for the thread to finish
    if (m_transformThread.joinable())
    {
        LOG(info) << "Waiting for transform thread to join in destructor" << endl;
        m_transformThread.join();
        LOG(info) << "Transform thread joined successfully in destructor" << endl;
    }
    
    // Clear the queue to prevent any pending frames
    {
        std::lock_guard<std::mutex> queueLock(m_queueLock);
        while (!m_queue.empty()) {
            m_queue.pop();
        }
    }
    if (m_surfacePool->m_surfacesAllocated == true)
    {
        m_surfacePool->freeSurfacesAndDataStructure(false);
    }
    m_surfacePool.reset();
    if (m_swSurf != nullptr)
    {
        if (m_swSurf->surfaceList != nullptr)
        {
            free(m_swSurf->surfaceList);
            m_swSurf->surfaceList = nullptr;
        }
        free(m_swSurf);
        m_swSurf = nullptr;
    }
    LOG(info) << "Exit " << __METHOD_NAME__ <<  endl;
}

void NvLLTransform::setOriginalFrameSize()
{
    if (m_consumer)
    {
        m_consumer->setOriginalFrameSize(m_sourceWidth, m_sourceHeight);
    }
}

void NvLLTransform::stopTransform()
{
    LOG(info) << "Stopping transform..." << endl;
    
    // First, stop the thread
    m_stop = true;
    m_flowData = true;
    m_condVar.notify_all();
    
    // Wait for the thread to finish
    if (m_transformThread.joinable())
    {
        LOG(info) << "Waiting for transform thread to join" << endl;
        m_transformThread.join();
        LOG(info) << "Transform thread joined successfully" << endl;
    }
    
    // Clear the queue to prevent any pending frames
    {
        std::lock_guard<std::mutex> queueLock(m_queueLock);
        while (!m_queue.empty()) {
            m_queue.pop();
        }
    }
    
    // Finally, reset the consumer after the thread is stopped
    m_consumer.reset();
    
    LOG(info) << "Transform stopped successfully" << endl;
}

void NvLLTransform::setConsumer(std::shared_ptr<IMediaDataConsumer> consumer)
{
    m_consumer = consumer;
    if (m_consumer)
    {
        m_consumer->setOriginalFrameSize(m_sourceWidth, m_sourceHeight);
    }
}

void NvLLTransform::updateStartTime(string start_time)
{
    if (m_consumer)
    {
        m_consumer->updateStartTime(start_time);
    }
}

void NvLLTransform::reset()
{
    if (m_consumer)
    {
        m_consumer->reset();
    }
}

void NvLLTransform::onLastFrame()
{
    if (m_consumer)
    {
        m_consumer->onLastFrame();
    }
}

void NvLLTransform::onFrame(std::shared_ptr<RawFrameParams> frame_data)
{
    // Start performance tracking when frame is received
    m_transcodeStats.startProcessing();

    bool is_sw_transform = NvHwDetection::getInstance()->m_useNvV4l2Enc == false && m_consumer && (m_consumer->getConsumerType() == ConsumerType::webrtcConsumer);
    bool is_transform_needed = frame_data->m_sourceHeight != frame_data->m_targetHeight ||
                            frame_data->m_sourceWidth != frame_data->m_targetWidth ||
                            frame_data->m_sourceLayout != frame_data->m_targetLayout ||
                            frame_data->m_sourceColorFormat != frame_data->m_targetColorFormat ||
                            is_sw_transform;

    // Send to consumer directly if no transform is needed
    if (!is_transform_needed || m_stop)
    {
        if (m_consumer && !m_stop)
        {
            m_transcodeStats.finishProcessing();
            m_consumer->onFrame(frame_data);
        }
        return;
    }

    std::shared_ptr<RawFrameParams> consumer_frame_data = std::static_pointer_cast<RawFrameParams>(frame_data);
    if (frame_data->m_sample)
    {
        gst_sample_ref ((GstSample *)frame_data->m_sample);
    }
    std::lock_guard<std::mutex> queueLock(m_queueLock);
    m_queue.push(consumer_frame_data);
    m_flowData = true;
    m_condVar.notify_all();
}

void NvLLTransform::doTransformTask()
{
    LOG(warning) << "Transform thread created" << endl;
    while(m_stop == false)
    {
        shared_ptr<RawFrameParams> sink_frame = nullptr;
        FD_Index_Pair fd_index_pair = {-1, -1};
        NvBufSurface* ip_surf = nullptr;
        NvBufSurface* dst_surf = nullptr;
        bool is_drc = false;
        bool is_sw_transform = false;
        GstNvVstMeta *meta = nullptr;
        GstNvIpcMeta *ipc_meta = nullptr;
        int64_t pts = 0;
        bool is_sw_mode = GET_CONFIG().use_software_path || g_isGpuPresent == false;
        bool is_error = false;
        int ret = 0;

        {
            std::unique_lock<std::mutex> lk(m_queueLock);
            while ((m_queue.empty() || m_flowData == false) && m_stop == false)
            {
                m_flowData = false;
                auto until = std::chrono::system_clock::now() + chrono::milliseconds(1000);
                m_condVar.wait_until(lk, until, [this]{ return m_flowData.load(); });
            }

            if (m_queue.empty() == false)
            {
                sink_frame = m_queue.front();
                m_queue.pop();
            }
        }

        if (sink_frame)
        {
            // Do not process frames after stop; drop to avoid use-after-free of decoder buffers
            if (m_stop)
            {
                if (sink_frame->m_sample)
                {
                    gst_sample_unref(sink_frame->m_sample);
                    sink_frame->m_sample = nullptr;
                }
                continue;
            }
            /* Get the buffer from sample */
            if (sink_frame->m_sample)
            {
                sink_frame->m_gstBuffer = gst_sample_get_buffer (sink_frame->m_sample);
                if (sink_frame->m_gstBuffer == nullptr)
                {
                    LOG (warning) << "No more buffers available from app sink element" << endl;
                    gst_sample_unref (sink_frame->m_sample);
                    sink_frame->m_sample = nullptr;
                    continue;
                }
                /* Map the gst buffer */
                if (gst_buffer_map (sink_frame->m_gstBuffer, &sink_frame->m_map, GST_MAP_READ) == false)
                {
                    LOG (warning) << "Map the gst buffer Failed" << endl;
                    gst_sample_unref (sink_frame->m_sample);
                    sink_frame->m_sample = nullptr;
                    continue;
                }
                if (sink_frame->m_gstBuffer)
                {
#ifdef JETSON_PLATFORM
                    if (GET_CONFIG().enable_ipc_path == true && m_isIPCMeta)
                    {
                        ipc_meta = GST_NV_IPC_META_GET(sink_frame->m_gstBuffer);
                        pts = GST_BUFFER_PTS (sink_frame->m_gstBuffer);
                    }
                    else
#endif
                    {
                        meta = GST_NV_VST_META_GET (sink_frame->m_gstBuffer);
                        pts = GST_BUFFER_PTS (sink_frame->m_gstBuffer);
                    }
                }
            }

            is_drc = (sink_frame->m_targetWidth != m_width) ||
                    (sink_frame->m_targetHeight != m_height) ||
                    (sink_frame->m_sourceLayout != sink_frame->m_targetLayout) ||
                    (sink_frame->m_sourceColorFormat != sink_frame->m_targetColorFormat);

            // Handle DRC change and update new resolution
            m_width = sink_frame->m_targetWidth;
            m_height = sink_frame->m_targetHeight;

            if (is_sw_mode)
            {
                ip_surf = nullptr;
                m_width = sink_frame->m_sourceWidth;
                m_height = sink_frame->m_sourceHeight;
            }
            else if (!sink_frame->m_sample)
            {
                ret = NvBufWrapper::getInstance()->NvBufSurfaceFromFd (sink_frame->m_fd, (void **)&ip_surf);
                if (ret < 0)
                {
                    LOG(error) << "NvBufSurfaceFromFd failed" << endl;
                    continue;
                }
                if (m_isIPCMeta)
                {
                    ipc_meta = (GstNvIpcMeta*)sink_frame->meta;
                }
                else
                {
                    meta = (GstNvVstMeta*)sink_frame->meta;
                }
                pts = sink_frame->pts;
            }
            else
            {
                ip_surf = (NvBufSurface *)sink_frame->m_map.data;
            }

            // Check for consumer present and update if its HW -> SW transform
            if (m_consumer)
            {
                is_sw_transform = NvHwDetection::getInstance()->m_useNvV4l2Enc == false && (m_consumer->getConsumerType() == ConsumerType::webrtcConsumer);
            }
            if (!is_sw_mode && (!m_surfacePool->m_surfacesAllocated || is_drc))
            {
                if (is_drc)
                {
                    m_surfacePool->freeSurfacesAndDataStructure(false);
                    m_surfacePool->m_surfacesAllocated = false;
                }
                NvBufSurfaceColorFormat color = is_sw_transform ? NVBUF_COLOR_FORMAT_YUV420 : sink_frame->m_targetColorFormat;
                NvBufSurfaceLayout layout =  is_sw_transform ? NVBUF_LAYOUT_PITCH : ip_surf->surfaceList[0].layout;
                if (!is_sw_transform && sink_frame->m_targetLayout != sink_frame->m_sourceLayout)
                {
                    layout = sink_frame->m_targetLayout;
                }
                LOG(info) << "Allocating surfaces of resolution = " << m_width << " x " << m_height << endl;

                m_surfacePool->allocateSurfaces(MAX_BUFFERS,
                                                m_width, m_height, true, color, layout,
                                                NVBUF_MEM_DEFAULT);
            }

            // Get the free FD from surface pool
            fd_index_pair = m_surfacePool->getFreeSurfaceFromQ();
            if (fd_index_pair.first > 0 || is_sw_mode)
            {
                if (!is_sw_mode)
                {
                    ret = NvBufWrapper::getInstance()->NvBufSurfaceFromFd (fd_index_pair.first, (void **)&dst_surf);
                    if (ret < 0)
                    {
                        LOG(error) << "NvBufSurfaceFromFd failed" << endl;
                        is_error = true;
                        goto error;
                    }

                    // Perform transform based on config
                    NvBufSurfTransformConfigParams config_params;
                    config_params.gpu_id       = g_gpuIndex;
                    config_params.compute_mode = NvBufSurfTransformCompute_Default;
                    config_params.cuda_stream  = nullptr;
                    NvBufWrapper::getInstance()->NvBufSurfTransformSetSessionParams (&config_params);

                    NvBufSurfTransformRect *src_rect = nullptr, *dst_rect = nullptr;
                    NvBufSurfTransformParams transform_params = {0};
                    src_rect = (NvBufSurfTransformRect*)calloc (1, sizeof(NvBufSurfTransformRect));
                    dst_rect = (NvBufSurfTransformRect*)calloc (1, sizeof(NvBufSurfTransformRect));
                    src_rect->top                       = 0;
                    src_rect->left                      = 0;
                    src_rect->width                     = sink_frame->m_sourceWidth;
                    src_rect->height                    = sink_frame->m_sourceHeight;
                    dst_rect->top                       = 0;
                    dst_rect->left                      = 0;
                    dst_rect->width                     = sink_frame->m_targetWidth;
                    dst_rect->height                    = sink_frame->m_targetHeight;
                    transform_params.src_rect           = src_rect;
                    transform_params.dst_rect           = dst_rect;
                    transform_params.transform_flag     = NVBUFSURF_TRANSFORM_FILTER;
                    transform_params.transform_flip     = NvBufSurfTransform_None;
                    transform_params.transform_filter   = NvBufSurfTransformInter_Default;

                    NvBufSurfTransform_Error transform_error = NvBufWrapper::getInstance()->NvBufSurfTransform (ip_surf, dst_surf, &transform_params);
                    if (transform_error != NvBufSurfTransformError_Success)
                    {
                        LOG(error) << "Transform failure" << endl;
                        free (dst_rect);
                        free (src_rect);
                        is_error = true;
                        goto error;
                    }
                    free (dst_rect);
                    free (src_rect);
                }

                if (NvHwDetection::getInstance()->m_useNvV4l2Enc == true || (m_consumer && m_consumer->getConsumerType() != ConsumerType::webrtcConsumer))
                {
                    // case 1 hw enc present
                    std::shared_ptr<RawFrameParams> frame_data = std::make_shared<RawFrameParams>();
                    if (fd_index_pair.first > 0 || is_sw_mode)
                    {
                        if (is_sw_mode)
                        {
                            // case 1.1 hw enc - sw dec
                            frame_data->m_fd            = sink_frame->m_fd;
                            frame_data->m_sample        = sink_frame->m_sample;
                            frame_data->m_sourceWidth   = sink_frame->m_sourceWidth;
                            frame_data->m_sourceHeight  = sink_frame->m_sourceHeight;
                        }
                        else
                        {
                            // case 1.2 hw mode full
                            frame_data->m_fd            = fd_index_pair.first;
                            frame_data->m_fdWrapperObj  = new std::shared_ptr<fdWrapper>(std::make_shared<fdWrapper>(m_surfacePool, frame_data->m_fd, fd_index_pair.second));
                            frame_data->m_sourceWidth   = sink_frame->m_targetWidth;
                            frame_data->m_sourceHeight  = sink_frame->m_targetHeight;
                        }
                        frame_data->m_targetWidth   = sink_frame->m_targetWidth;
                        frame_data->m_targetHeight  = sink_frame->m_targetHeight;
                        frame_data->m_isTransformed = frame_data->m_sourceWidth != frame_data->m_targetWidth ||
                                                    frame_data->m_sourceHeight != frame_data->m_targetHeight;
                        frame_data->m_streamId      = sink_frame->m_streamId;
                        frame_data->m_srcStrideY    = sink_frame->m_srcStrideY;
                        frame_data->m_srcStrideU    = sink_frame->m_srcStrideU;
                        frame_data->m_srcStrideV    = sink_frame->m_srcStrideV;
                        if (m_isIPCMeta)
                        {
                            frame_data->meta            = ipc_meta;
                        }
                        else
                        {
                            frame_data->meta            = meta;
                        }
                        frame_data->pts             = pts;
                        if (m_consumer) {
                            try {
                                // End performance tracking after acual processing
                                m_transcodeStats.finishProcessing();
                                m_consumer->onFrame(frame_data);
                            } catch (const std::exception& e) {
                                LOG(error) << "Exception in transform consumer onFrame: " << e.what() << endl;
                                break; // Exit the loop if consumer is broken
                            } catch (...) {
                                LOG(error) << "Unknown exception in transform consumer onFrame" << endl;
                                break; // Exit the loop if consumer is broken
                            }
                        } else {
                            LOG(warning) << "Consumer is null in doTransformTask, skipping frame" << endl;
                        }
                    }
                }
                else
                {
                    // case 2 enc not present
                    rtc::scoped_refptr<webrtc::I420Buffer> yuv_buffer    (new rtc::RefCountedObject<webrtc::I420Buffer>(sink_frame->m_targetWidth, sink_frame->m_targetHeight));

                    if (is_sw_mode) // Input is sw buffer
                    {
                        rtc::scoped_refptr<webrtc::I420Buffer> decoded_buffer(new rtc::RefCountedObject<webrtc::I420Buffer>(m_width, m_height));

                        /* Use src stride from frame_data when set (decoder output), else use width (contiguous) */
                        const int src_stride_y = (sink_frame->m_srcStrideY > 0) ? sink_frame->m_srcStrideY : m_width;
                        const int src_stride_u = (sink_frame->m_srcStrideU > 0) ? sink_frame->m_srcStrideU : (m_width / 2);
                        const int src_stride_v = (sink_frame->m_srcStrideV > 0) ? sink_frame->m_srcStrideV : (m_width / 2);
                        uint8_t *buffer_y = sink_frame->m_map.data;
                        uint8_t *buffer_u = buffer_y + (size_t)src_stride_y * m_height;
                        uint8_t *buffer_v = buffer_u + (size_t)src_stride_u * (m_height / 2);
                        uint8_t *dst_y = decoded_buffer->MutableDataY();
                        uint8_t *dst_u = decoded_buffer->MutableDataU();
                        uint8_t *dst_v = decoded_buffer->MutableDataV();
                        const int dst_stride_y = decoded_buffer->StrideY();
                        const int dst_stride_u = decoded_buffer->StrideU();
                        const int dst_stride_v = decoded_buffer->StrideV();
                        if (src_stride_y == m_width && src_stride_u == (m_width / 2) && src_stride_v == (m_width / 2))
                        {
                            /* Contiguous source: single copy per plane with bounds checking */
                            size_t y_size = (size_t)(m_width * m_height);
                            size_t uv_size = (size_t)((m_width * m_height) / 4);
                            
                            // Bounds checking using WebRTC I420Buffer dimensions
                            const int buffer_width = decoded_buffer->width();
                            const int buffer_height = decoded_buffer->height();
                            const size_t expected_y_size = (size_t)(buffer_width * buffer_height);
                            const size_t expected_uv_size = (size_t)((buffer_width * buffer_height) / 4);
                            
                            // Safe copy with bounds validation using secure memory operations
                            if (dst_y != nullptr && buffer_y != nullptr && y_size > 0 && 
                                y_size <= expected_y_size && m_width <= buffer_width && m_height <= buffer_height)
                            {
                                // Use memmove for safer memory copying (handles overlapping memory)
                                std::memmove(dst_y, buffer_y, y_size);
                            }
                            if (dst_u != nullptr && buffer_u != nullptr && uv_size > 0 && 
                                uv_size <= expected_uv_size)
                            {
                                std::memmove(dst_u, buffer_u, uv_size);
                            }
                            if (dst_v != nullptr && buffer_v != nullptr && uv_size > 0 && 
                                uv_size <= expected_uv_size)
                            {
                                std::memmove(dst_v, buffer_v, uv_size);
                            }
                        }
                        else
                        {
                            /* Strided source: copy per row with bounds checking */
                            const int buffer_width = decoded_buffer->width();
                            const int buffer_height = decoded_buffer->height();
                            
                            if (dst_y != nullptr && buffer_y != nullptr && m_width <= buffer_width && m_height <= buffer_height)
                            {
                                for (int row = 0; row < m_height ; row++)
                                {
                                    size_t y_row_size = (size_t)m_width;
                                    // Bounds check: ensure we don't exceed buffer dimensions
                                    if (y_row_size > 0 && row < buffer_height && 
                                        (row * dst_stride_y + y_row_size <= (size_t)(dst_stride_y * m_height)) &&
                                        (row * src_stride_y + y_row_size <= (size_t)(src_stride_y * buffer_height)))
                                    {
                                        // Use memmove for safer memory copying
                                        std::memmove(dst_y + row * dst_stride_y, buffer_y + row * src_stride_y, y_row_size);
                                    }
                                }
                            }
                            
                            if (dst_u != nullptr && buffer_u != nullptr && dst_v != nullptr && buffer_v != nullptr &&
                                (m_width / 2) <= (buffer_width / 2) && (m_height / 2) <= (buffer_height / 2))
                            {
                                for (int row = 0; row < m_height / 2 ; row++)
                                {
                                    size_t uv_row_size = (size_t)(m_width / 2);
                                    if (uv_row_size > 0 && row < (buffer_height / 2))
                                    {
                                        // Copy U plane with bounds checking using secure memory operations
                                        if ((row * dst_stride_u + uv_row_size <= (size_t)(dst_stride_u * (m_height / 2))) &&
                                            (row * src_stride_u + uv_row_size <= (size_t)(src_stride_u * (buffer_height / 2))))
                                        {
                                            std::memmove(dst_u + row * dst_stride_u, buffer_u + row * src_stride_u, uv_row_size);
                                        }
                                        // Copy V plane with bounds checking using secure memory operations
                                        if ((row * dst_stride_v + uv_row_size <= (size_t)(dst_stride_v * (m_height / 2))) &&
                                            (row * src_stride_v + uv_row_size <= (size_t)(src_stride_v * (buffer_height / 2))))
                                        {
                                            std::memmove(dst_v + row * dst_stride_v, buffer_v + row * src_stride_v, uv_row_size);
                                        }
                                    }
                                }
                            }
                        }
                        yuv_buffer->ScaleFrom(*decoded_buffer->GetI420());
                    }
                    else    // Input is hw buffer, converted to sw buffer
                    {
                        // Create the NvBufSurface for webrtc SW buffer storage
                        if (!m_swSurf)
                        {
                            m_swSurf = (NvBufSurface *) calloc (1, sizeof(NvBufSurface));
                            if (!m_swSurf)
                            {
                                LOG(error) << "Allocate SW surface failed" << endl;
                                is_error = true;
                                goto error;
                            }
                            m_swSurf->surfaceList = (NvBufSurfaceParams *) calloc (1, sizeof(NvBufSurfaceParams));
                            if (!m_swSurf->surfaceList)
                            {
                                LOG(error) << "Allocate SW surfaceList failed" << endl;
                                is_error = true;
                                goto error;
                            }
                        }

                        // For SW transform : Init the NvBufSurface data from the webrtc I420 buffer created
                        ret = NvBufWrapper::getInstance()->getSwNvBufSurface(yuv_buffer, m_swSurf);
                        if (ret < 0)
                        {
                            LOG(error) << "Get SW NvBufSurface failed" << endl;
                            is_error = true;
                            goto error;
                        }

                        // For SW transform : Copy from HW I420 -> SW I420
                        ret = NvBufWrapper::getInstance()->NvBufSurfaceCopy (dst_surf, m_swSurf);
                        if (ret < 0)
                        {
                            LOG(error) << "NvBufSurfaceCopy failed" << endl;
                            is_error = true;
                            goto error;
                        }
                    }

                    // here send yuv_buffer to webrtc_sink_consumer
                    std::shared_ptr<RawFrameParams> frame_data = std::make_shared<RawFrameParams>();
                    frame_data->m_isYuvBuffer = true;
                    frame_data->m_buffer      = static_cast<unsigned char*>(static_cast<void*>(yuv_buffer.get()));
                    if (m_consumer)
                    {
                        m_transcodeStats.finishProcessing();
                        m_consumer->onFrame(frame_data);
                    }
                }
error:
                if ((is_sw_transform || is_error) && fd_index_pair.first > 0)
                {
                    // Add frame back to Queue
                    FdIndexInfo fd_index_info = {{fd_index_pair.first, fd_index_pair.second}, false};
                    m_surfacePool->addFreeSurfaceToQ(fd_index_info);
                }
            }
        }
    }
    LOG(warning) << "Exiting from Transform thread" << endl;
}
