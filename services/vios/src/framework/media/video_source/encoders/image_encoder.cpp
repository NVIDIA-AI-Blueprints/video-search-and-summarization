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

#include "image_encoder.h"
#include "logger.h"
#include <gst/app/gstappsrc.h>
#include <gst/app/gstappsink.h>
#include "nvjpegenc_loader.h"

using namespace std;
using namespace std::chrono_literals;
constexpr const char* DEFAULT_FRAME_WIDTH = "1920";
constexpr const char* DEFAULT_FRAME_HEIGHT = "1080";

static GstFlowReturn
cb_on_new_sample_from_sink (GstElement * appsink, ImageEnc* imageEnc)
{
   if (imageEnc)
    {
        return imageEnc->processJpegImageFromSink(appsink);
    }
   return GST_FLOW_ERROR;
}

ImageEnc::ImageEnc(const std::string& consumer_name) : IMediaDataConsumer(consumer_name)
{
    create(DEFAULT_FRAME_WIDTH, DEFAULT_FRAME_HEIGHT);
    m_stop = false;
}

ImageEnc::ImageEnc(const std::string& consumer_name, const std::map<std::string, std::string, std::less<>> &opts) : IMediaDataConsumer(consumer_name)
{
    LOG(info) << "ImageEnc constructor" << endl;
    string sourceWidth = DEFAULT_FRAME_WIDTH;
    string sourceHeight = DEFAULT_FRAME_HEIGHT;
    string resizeWidth = "", resizeHeight = "";
    
    // Override defaults if valid options are provided
    if(opts.find("source_width") != opts.end() && !opts.at("source_width").empty())
    {
        sourceWidth = opts.at("source_width");
    }
    if(opts.find("source_height") != opts.end() && !opts.at("source_height").empty())
    {
        sourceHeight = opts.at("source_height");
    }
    if(opts.find("resize_width") != opts.end() && opts.find("resize_height") != opts.end() &&
       !opts.at("resize_width").empty() && !opts.at("resize_height").empty())
    {
        resizeWidth = opts.at("resize_width");
        resizeHeight = opts.at("resize_height");
    }
    
    LOG(info) << "ImageEnc creating with sourceWidth=" << sourceWidth << ", sourceHeight=" << sourceHeight << endl;
    create(sourceWidth, sourceHeight, resizeWidth, resizeHeight);
    m_stop = false;
}

ImageEnc::~ImageEnc()
{
    LOG(info) << "Enter ImageEnc destructor" << endl;
    m_stop = true;
    if (m_pipeline)
    {
        gst_element_set_state(m_pipeline, GST_STATE_NULL);
        gst_element_get_state(m_pipeline, nullptr, nullptr, 5 * GST_SECOND);
        gst_object_unref (m_pipeline);
        m_pipeline = nullptr;
    }
    LOG(info) << "Exit ImageEnc destructor" << endl;
}

GstFlowReturn ImageEnc::processJpegImageFromSink(GstElement *appsink)
{
    GstSample *sample;
    GstBuffer *gstBuffer;
    GstMapInfo map;
    GstFlowReturn ret = GST_FLOW_OK;

    /* Get the sample from appsink */
    sample = gst_app_sink_pull_sample (GST_APP_SINK (appsink));

    if (m_stop)
    {
        if (sample)
        {
            gst_sample_unref (sample);
        }
        return GST_FLOW_EOS;
    }

    if (sample == nullptr)
    {
        if (gst_app_sink_is_eos((GstAppSink *)appsink))
        {
            LOG (info) << "EOS Received on app sink element" << endl;
            return GST_FLOW_OK;
        }
        return GST_FLOW_ERROR;
    }

    /* Get the buffer from sample */
    gstBuffer = gst_sample_get_buffer (sample);
    if (gstBuffer == nullptr)
    {
        LOG (warning) << "No more buffers available from app sink element" << endl;
        gst_sample_unref (sample);
        return GST_FLOW_ERROR;
    }

    /* Map the gst buffer */
    if (gst_buffer_map (gstBuffer, &map, GST_MAP_READ) == false)
    {
        LOG (warning) << "Map the gst buffer Failed" << endl;
        gst_sample_unref (sample);
        return GST_FLOW_ERROR;
    }

    /* Send encoded image */
    {
        std::string image_buf(map.size, 1);
        memmove (&image_buf[0], map.data, map.size);
        std::lock_guard<std::mutex> lock(m_imgBufferLock);
        m_imgBuffer = image_buf;
        std::string().swap(image_buf);
        m_stop = true;
        m_imgBufferWait.notify_all();
    }

    /* Unmap the gst buffer */
    gst_buffer_unmap (gstBuffer, &map);

    /* Unref the sample */
    gst_sample_unref (sample);

    return ret;
}

void ImageEnc::onFrame(std::shared_ptr<RawFrameParams> frameData)
{
    // Start performance tracking when frame is received
    m_transcodeStats.startProcessing();
    if (!frameData.get())
    {
        LOG(error) << "empty frame received" << endl;
        return;
    }
    uint64_t fd = 0;
    bool sw_mode = GET_CONFIG().use_software_path || g_isGpuPresent == false;

    if (frameData->m_sample)
    {
        gst_sample_ref ((GstSample *)frameData->m_sample);
        frameData->m_gstBuffer = gst_sample_get_buffer (frameData->m_sample);
        if (frameData->m_gstBuffer == nullptr)
        {
            LOG (warning) << "No more buffers available from app sink element" << endl;
            gst_sample_unref (frameData->m_sample);
            frameData->m_sample = nullptr;
            return;
        }
        /* Map the gst buffer */
        if (gst_buffer_map (frameData->m_gstBuffer, &frameData->m_map, GST_MAP_READ) == false)
        {
            LOG (warning) << "Map the gst buffer Failed" << endl;
            gst_sample_unref (frameData->m_sample);
            frameData->m_sample = nullptr;
            return;
        }
        if (frameData->m_map.size == sizeof(NvBufSurface)) // hardware buffer
        {
            NvBufSurface* ip_surf = nullptr;
            ip_surf = (NvBufSurface *)frameData->m_map.data;
            fd = ip_surf->surfaceList[0].bufferDesc;
        }
        else
        {
            sw_mode = true;
        }
    }
    else if (frameData->m_fd >= 0)
    {
        fd = frameData->m_fd;
    }
    else
    {
        LOG(error) << "Internal error. Sample null or fd < 0" << endl;
        return;
    }

    if (sw_mode)
    {
        return pushBuffer(frameData);
    }
    else
    {
        return hwEncode(fd, frameData);
    }
}

std::string ImageEnc::getImageBuffer()
{
    std::unique_lock<std::mutex> lk(m_imgBufferLock);
    if (m_stop == false)
    {
        if (GET_CONFIG().enable_mega_simulation == false)
        {
            auto until = std::chrono::system_clock::now()
                + std::chrono::seconds(GET_CONFIG().picture_api_timeout_secs);
            if (m_imgBufferWait.wait_until(lk, until, [this]{ return (m_stop.load()); }) == false)
            {
                LOG(error) << "Image Buffer wait timeout occured (configured picture_api_timeout_secs="
                           << GET_CONFIG().picture_api_timeout_secs << "s)" << endl;
                return std::string();
            }
        }
        else
        {
            /* This should wait infinitely, as when I frame will be received is 
            ** not guaranteed */
            m_imgBufferWait.wait(lk, [this]{ return m_stop == true; });
        }
    }
    m_transcodeStats.finishProcessing();
    return m_imgBuffer;
}

void ImageEnc::pushBuffer(std::shared_ptr<RawFrameParams> frameData)
{
    if (frameData->m_sample || frameData->m_fd)
    {
        // Basic input validation
        if (!frameData->m_map.data || frameData->m_map.size == 0)
        {
            LOG(error) << "Invalid frame data: null pointer or zero size" << endl;
            m_stop = true;
            return;
        }
        
        // Reasonable maximum size limit (256MB for video frames)
        const size_t MAX_FRAME_SIZE = 256 * 1024 * 1024;
        if (frameData->m_map.size > MAX_FRAME_SIZE)
        {
            LOG(error) << "Frame size too large: " << frameData->m_map.size << " bytes" << endl;
            m_stop = true;
            return;
        }
        
        GstBuffer* gstbuffer = gst_buffer_new_allocate (nullptr, frameData->m_map.size, nullptr);
        if (gstbuffer == nullptr)
        {
            LOG(error) << "gst_buffer_new_allocate failed" << endl;
            m_stop = true;
            return;
        }
        /* Map the Gst Buffer to write the data */
        GstMapInfo map;
        if (!gst_buffer_map (gstbuffer, &map, GST_MAP_WRITE))
        {
            LOG(error) << "gst_buffer_map failed" << endl;
            gst_buffer_unref(gstbuffer);  // Clean up allocated buffer
            m_stop = true;
            return;
        }

        // Bounds check and safe memory copy (inputs already validated above)
        if (frameData->m_map.size > map.size)
        {
            LOG(error) << "Buffer overflow prevented: frame_size(" << frameData->m_map.size 
                       << ") > buffer_size(" << map.size << ")" << endl;
            gst_buffer_unmap(gstbuffer, &map);
            gst_buffer_unref(gstbuffer);
            m_stop = true;
            return;
        }
        
        // Safe memory copy using memmove
        memmove(map.data, frameData->m_map.data, frameData->m_map.size);
        map.size = frameData->m_map.size;

        /* Unmap the Gst Buffer */
        gst_buffer_unmap (gstbuffer, &map);
        if (frameData->m_gstBuffer)
        {
            GST_BUFFER_PTS (gstbuffer) = GST_BUFFER_PTS (frameData->m_gstBuffer);
            GST_BUFFER_DTS (gstbuffer) = GST_BUFFER_DTS (frameData->m_gstBuffer);
        }
        else
        {
            GST_BUFFER_PTS (gstbuffer) = frameData->pts;
            GST_BUFFER_DTS (gstbuffer) = frameData->pts;
        }

        if (gst_app_src_push_buffer((GstAppSrc*)m_source, gstbuffer) != GST_FLOW_OK)
        {
            LOG(error) << "Failed to push buffer" << endl;
            m_stop = true;
        }
        return;
    }
    else
    {
        LOG(error) << "Internal error" << endl;
        m_stop = true;
    }
}

void ImageEnc::hwEncode(uint64_t fd, std::shared_ptr<RawFrameParams> frameData)
{
    if (fd < 0)
    {
        LOG(error) << "fd error " << fd << endl;
        return;
    }

    unsigned long out_buf_size = 0;
    unsigned char *out_buf = nullptr;

    // Extra 512 Kbytes are required for some case encoded bitstream exceeds input buffer size
    out_buf_size = (frameData->m_targetWidth * frameData->m_targetHeight * 3/2) + (512 << 10) ;
    out_buf = (unsigned char *)malloc(out_buf_size);
    if (NvJpegEncLoader::getInstance()->nvjpegEncodeFromFd(fd, &out_buf, out_buf_size) == 0)
    {
        LOG(info) << "HW jpeg conversion success" << endl;
    }
    else
    {
        LOG(error) << "HW jpeg conversion failed" << endl;
        free(out_buf);
        out_buf = nullptr;
        m_stop = true;
        return;
    }

    std::string image_buf(out_buf_size, 1);
    memmove (&image_buf[0], out_buf, out_buf_size);

    std::lock_guard<std::mutex> lock(m_imgBufferLock);
    m_imgBuffer = image_buf;
    std::string().swap(image_buf);
    m_stop = true;
    m_imgBufferWait.notify_all();

    free(out_buf);
    out_buf = nullptr;
}

int ImageEnc::create(string sourceWidth, string sourceHeight, string resizeWidth, string resizeHeight)
{
    LOG (info) << "Creating Gstreamer Image Encode pipeline" << endl;
    if(gst_is_initialized() == false)
    {
        gst_init (nullptr, nullptr);
    }

    m_pipeline      = gst_pipeline_new ("pipeline");
    m_source        = gst_element_factory_make ("appsrc", nullptr);
    m_parser        = gst_element_factory_make ("videoparse", nullptr);
    m_scaler        = gst_element_factory_make ("videoscale", nullptr);
    m_filter        = gst_element_factory_make ("capsfilter", nullptr);
    m_imageEncoder  = gst_element_factory_make ("jpegenc", nullptr);
    m_sink          = gst_element_factory_make ("appsink", nullptr);
    if (!m_pipeline || !m_source || !m_parser || !m_filter || !m_imageEncoder || !m_sink || !m_scaler)
    {
        LOG (error) << "Gstreamer element creation failed" << endl;
        return -1;
    }

    gst_bin_add_many (GST_BIN (m_pipeline), m_source, m_parser, m_scaler, m_filter, m_imageEncoder, m_sink, nullptr);
    if (!gst_element_link_many (m_source, m_parser, m_scaler, m_filter, m_imageEncoder, m_sink, nullptr))
    {
        LOG (error) << "Elements could not be linked" << endl;
        return -1;
    }

    g_object_set (G_OBJECT (m_sink), "emit-signals", TRUE, "sync", FALSE, nullptr);
    if(!g_signal_connect (m_sink, "new-sample", G_CALLBACK (cb_on_new_sample_from_sink), (void*)this))
    {
        LOG(error) << "Error in g_signal_connect of new-sample" << endl;
        return -1;
    }

    g_object_set (G_OBJECT (m_parser), "width", stringToInt(sourceWidth, 0), nullptr);
    g_object_set (G_OBJECT (m_parser), "height", stringToInt(sourceHeight, 0), nullptr);

    GstCaps *filtercaps  = nullptr;
    std::string caps_string = "video/x-raw, format=I420";
    if (!resizeWidth.empty() && !resizeHeight.empty())
    {
        caps_string = caps_string + ", width=(int)" + resizeWidth +
                                    ", height=(int)" + resizeHeight;
    }
    LOG(info) << "ImageEnc Converter Output Caps = " << caps_string << endl;
    filtercaps = gst_caps_from_string (caps_string.c_str());
    g_object_set (G_OBJECT (m_filter), "caps", filtercaps, nullptr);
    gst_caps_unref (filtercaps);

    m_stop = false;

    GstStateChangeReturn gstStateChangeRet = gst_element_set_state (m_pipeline, GST_STATE_PLAYING);
    if (gstStateChangeRet == GST_STATE_CHANGE_FAILURE)
    {
        LOG (error) << "gst_element_set_state failed. " << endl;
        return -1;
    }
    /* pipeline will be put into PLAYING state from other thread */
    else if (gstStateChangeRet == GST_STATE_CHANGE_ASYNC)
    {
        LOG (info) << "GST_STATE_CHANGE_ASYNC. " << endl;
    }
    /* success case */
    else
    {
        LOG (info) << "State change success " << endl;
    }

    return 0;
}
