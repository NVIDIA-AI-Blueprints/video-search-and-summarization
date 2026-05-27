/*
 * SPDX-FileCopyrightText: Copyright (c) 2022-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "nvvideoencoder.h"
#include "stream_buffer.h"
#include <chrono>

constexpr int MAX_Q_WAIT_SEC = 2;

NvEncoderVideoConsumer::NvEncoderVideoConsumer(const std::string& consumer_name) : IMediaDataConsumer(consumer_name)
{
    LOG(info) << "NvEncoderVideoConsumer::NvEncoderVideoConsumer" << endl;
    m_nvEncoder = std::make_shared<NvVideoEncoder>();
    m_videowebRTCSender = std::make_shared<VideoWebRTCSender>("VideoWebRTCSender");
    m_surfacePool = std::make_shared<NvSurfacePool>();
    m_queue = make_shared<EncoderQueue>(this);
    // Start Queue pull thread
    m_encoderProcessThread = std::thread(&NvEncoderVideoConsumer::encoderProcessThread, this);
    m_transcodeStats.clear();
    // Performance tracking element name is now set in base constructor
}

NvEncoderVideoConsumer::NvEncoderVideoConsumer(const std::string& consumer_name, double frame_rate, string peer_id, bool enable_frame_sync) : IMediaDataConsumer(consumer_name), m_frameRate (frame_rate), m_peerIdStreamId (peer_id)
{
    LOG(info) << "NvEncoderVideoConsumer::NvEncoderVideoConsumer" << endl;
    m_nvEncoder = std::make_shared<NvVideoEncoder>();
    m_videowebRTCSender = std::make_shared<VideoWebRTCSender>("VideoWebRTCSender", frame_rate, enable_frame_sync);
    m_surfacePool = std::make_shared<NvSurfacePool>();
    m_queue = make_shared<EncoderQueue>(this);
    // Start Queue pull thread
    m_encoderProcessThread = std::thread(&NvEncoderVideoConsumer::encoderProcessThread, this);
    m_transcodeStats.clear();
    // Performance tracking element name is now set in base constructor
    setConsumerType (encoder);

    /* Initialize encoder with default resolution */
    if (NvHwDetection::getInstance()->m_useNvV4l2Enc == true)
    {
        if (createEncoder(WIDTH_1080p, HEIGHT_1080p, "h264") == -1)
        {
            LOG(error) << "Encoder creation failed" << endl;
        }
    }
}

NvEncoderVideoConsumer::~NvEncoderVideoConsumer ()
{
    try {
        LOG(info) << "Enter ~NvEncoderVideoConsumer" << endl;
        if (m_videowebRTCSender) {
            m_videowebRTCSender->removeWebrtcBroacaster(m_peerIdStreamId);
        }
        if (m_terminatePullLoop == false)
        {
            m_terminatePullLoop = true;
            std::shared_ptr<RawFrameParams> empty_frame_data = std::make_shared<RawFrameParams>();
            empty_frame_data->m_encoderMsgType = Exit;
            m_queue->push(empty_frame_data);
        }
        if (m_encoderProcessThread.joinable())
        {
            LOG(info) << "Waiting for m_encoderProcessThread thread join" << endl;
            m_encoderProcessThread.join();
        }
        if (GET_CONFIG().enable_perf_logging)
        {
            m_transcodeStats.printTotalStats();
            m_transcodeStats.clearQueue();
        }
        LOG(info) << "Exit ~NvEncoderVideoConsumer" << endl;
    } catch (const std::exception& e) {
        try { LOG(error) << "Exception in ~NvEncoderVideoConsumer: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
    } catch (...) {
        try { LOG(error) << "Unknown exception in ~NvEncoderVideoConsumer" << endl; } catch (...) { (void)std::current_exception(); }
    }
}

FD_Index_Pair NvEncoderVideoConsumer::resetEncIfRequiredAndGetFreeBuffer(int width, int height, bool do_reset)
{
    FD_Index_Pair fd_index_pair;
    bool change_resolution = isEncoderResolutionChanged (width, height);
    bool reset_encoder = change_resolution || do_reset;
    if (reset_encoder)
    {
        LOG(info) << "=== START ENC RESET ===" << endl;
        destroyEncoderAndBuffers();
        if (createEncoder(width, height, "h264") == -1)
        {
            LOG(error) << "Encoder creation failed" << endl;
            return fd_index_pair;
        }
        setEncoderResolution (width, height);
        LOG(info) << "=== END ENC RESET ===" << endl;
    }
    fd_index_pair = m_surfacePool->getFreeFd (reset_encoder, width, height, m_encoderTransformed);

    return fd_index_pair;
}

void NvEncoderVideoConsumer::resetEncoderInternal()
{
    LOG(info) << "=== START ENC RESET ===" << endl;
    destroyEncoderAndBuffers();
    setEncoderResolution (0, 0);
    LOG(info) << "=== END ENC RESET ===" << endl;

    std::unique_lock<std::mutex> lk(m_encoderReleaseMutex);
    m_encoderReleaseNotified = true;
    m_encoderReleaseCv.notify_all();

    return;
}

void NvEncoderVideoConsumer::reset()
{
#if 0 // TBD, fix this in next release
    onLastFrame();
#endif
    LOG(info) << "Reset Encoder" << endl;
    /* To handle closure of encoder in encoderProcessThread */
    std::shared_ptr<RawFrameParams> frame_data = std::make_shared<RawFrameParams>();
    frame_data->m_encoderMsgType = Reset;

    onFrame(frame_data);
    waitForEncoderReleaseInternal ();
    LOG(info) << "Encoder reset done" << endl;
}

void NvEncoderVideoConsumer::stopEncoder()
{
    /* To handle closure of encoder in encoderProcessThread */
    std::shared_ptr<RawFrameParams> frame_data = std::make_shared<RawFrameParams>();
    frame_data->m_encoderMsgType = Exit;
    m_terminatePullLoop = true;

    onFrame(frame_data);
    waitForEncoderReleaseInternal ();
}

void NvEncoderVideoConsumer::destroyBuffers ()
{
    LOG(info) << "Destroying FDs with the encoder" << endl;
    /* As we are removing consumer, need to destroy fds 
    ** corresponding to the consumer
    */
    m_fdGstSampleMap.clear();
    if (m_surfacePool)
    {
        m_surfacePool->freeSurfacesAndDataStructure ();
    }
}

void NvEncoderVideoConsumer::onFrame(std::shared_ptr<RawFrameParams> frame_data)
{
    if (frame_data->m_sample)
    {
        gst_sample_ref ((GstSample *)frame_data->m_sample);
    }
    if (m_firstTS == 0)
    {
        m_firstTS = frame_data->pts / 1000;
    }
    std::shared_ptr<RawFrameParams> enc_frame_data = std::static_pointer_cast<RawFrameParams>(frame_data);
    m_queue->push(enc_frame_data);
}

void NvEncoderVideoConsumer::waitForEncoderReleaseInternal()
{
    std::unique_lock<std::mutex> lk(m_encoderReleaseMutex);
    if (m_encoderReleaseNotified == false)
    {
        auto until = std::chrono::system_clock::now() + 5s;
        m_encoderReleaseCv.wait_until(lk, until, [this]{ return m_encoderReleaseNotified.load(); });
    }
    m_encoderReleaseNotified = false;
}

void NvEncoderVideoConsumer::onLastFrame()
{
#if 0
    LOG(info) << "Sending last buffer to encoder" << endl;
    std::shared_ptr<RawFrameParams> empty_frame_data = std::make_shared<RawFrameParams>();
    empty_frame_data->m_encoderMsgType = Last_Frame;
    m_queue->push(empty_frame_data);
#endif

    LOG(info) << "Encoder sending EOS to consumers" << endl;
    if (m_consumer)
    {
        string eos_msg = STREAM_MSG_EOS;
        FrameParams frame_params;
        frame_params.m_buffer  = (unsigned char *)(eos_msg.data());
        frame_params.m_size    = 0;
        m_consumer->onFrame(frame_params);
    }
}

int NvEncoderVideoConsumer::createEncoder(int width, int height, string codecString)
{
    int ret = -1;
    if (m_nvEncoder)
    {
        m_sentFramesCount = 0;
        ret = m_nvEncoder->InitEncode(width, height, codecString);
        if (ret == -1)
        {
            LOG(error) << "Failed to Initialize Encoder" << endl;
        }
        else
        {
            m_encoderInitDone = true;
            /* These are used to check with what resolution 
            ** encoder has been initialised
            */
            m_width  = width;
            m_height = height;
        }
    }
    return ret;
}

bool NvEncoderVideoConsumer::isEncoderResolutionChanged (int new_width, int new_height)
{
    if (new_width != m_width || new_height != m_height)
    {
        LOG(warning) << "+++++++ Need to execute DRC ++++++++" << endl;
        return true;
    }
    return false;
}

void NvEncoderVideoConsumer::setEncoderResolution (int width, int height)
{
    m_width  = width;
    m_height = height;
}

void NvEncoderVideoConsumer::setRates (unsigned int bitrate)
{
    if (m_nvEncoder)
    {
        m_nvEncoder->SetRates(bitrate);
    }
}

void NvEncoderVideoConsumer::deInitEncoder()
{
    if (m_nvEncoder)
    {
        m_nvEncoder->Release();
    }
}

void NvEncoderVideoConsumer::destroyEncoderAndBuffers ()
{
    LOG(info) << "Do fast reset of encoder and free all buffers" << endl;
    m_lastBufferMonitorCv.notify_all();
    deInitEncoder ();
    destroyBuffers();
    m_encoderInitDone = false;
}

void NvEncoderVideoConsumer::setConsumer(std::shared_ptr<IMediaDataConsumer> consumer)
{
    m_consumer = consumer;
}

void NvEncoderVideoConsumer::encoderProcessThread()
{
    /* To stop this thread:
     * 1) Set m_terminatePullLoop = true
     * 2) Push frame_data with fd = -1 in queue
     * Note: Both of these conditions are required to cover all corner cases
     */
    while (!m_terminatePullLoop)
    {
        bool software_mode = false;

        unsigned char *output_buffer = nullptr;
        ssize_t output_size = 0;
        std::shared_ptr<RawFrameParams> frame_data;
        FD_Index_Pair fd_index_pair (-1, -1);
        InputBufferType input_buf;
        frame_data = m_queue->pull();
        int fd = 0;
        int index = 0;
        if (frame_data->m_encoderMsgType == Exit && m_terminatePullLoop)
        {
            LOG(info) << "Exiting Pull loop" << endl;
            break;
        }
        if (frame_data->m_encoderMsgType == Reset)
        {
            LOG(info) << "Decoder is resetting, need to return buffers" << endl;
            resetEncoderInternal ();
            continue;
        }
        if (frame_data->m_encoderMsgType == Last_Frame) // last buffer handling
        {
            LOG(info) << "Processing last buffer" << endl;
            if (m_nvEncoder->isReleased())
            {
                LOG(warning) << "Encoder is released, not processing last buffer" << endl;
                continue;
            }
            m_nvEncoder->sendLastBuffer();
            bool last_buffer_dq_loop = true;
            while(last_buffer_dq_loop)
            {
                int ret = m_nvEncoder->dqueueLastBuffers(&output_buffer, &output_size);
                if (ret < 0)
                {
                    last_buffer_dq_loop = false;
                    LOG(error) << "Breaking last buffer loop on Error condition" << endl;
                    continue;
                }
                else if (ret == 1)  // LAST BUFFER
                {
                    last_buffer_dq_loop = false;
                    LOG(error) << "Received last buffer from encoder" << endl;
                }
                if (output_size)
                {
                    if (GET_CONFIG().enable_perf_logging)
                    {
                        m_transcodeStats.finishProcessing();
                    }
                    ++m_sentFramesCount;
                    int width = frame_data->m_targetWidth;
                    int height = frame_data->m_targetHeight;
                    LOG(info) << "Last buffer : " << output_buffer << " size: " << output_size << endl;

                    /* Broadcast frames as per framerate, otherwise it might corrupt/lost */
                    {
                        std::unique_lock<std::mutex> lastbuffer_lock(m_lastBufferMonitorMutex);
                        auto until = std::chrono::system_clock::now() + std::chrono::milliseconds(1000/(int)m_frameRate);
                        m_lastBufferMonitorCv.wait_until(lastbuffer_lock, until);
                    }
                    width  = width  == 0 ? m_width  : frame_data->m_targetWidth;
                    height = height == 0 ? m_height : frame_data->m_targetHeight;

                    /* Send the output_buffer and output_size to webRTC Sender class */
                    FrameParams params;
                    params.m_media          = "video";
                    params.m_codec          = "H264";
                    params.m_buffer         = output_buffer;
                    params.m_size           = output_size;
                    params.m_width          = width;
                    params.m_height         = height;
                    params.m_needParsing    = false;
                    if (frame_data->meta && GET_CONFIG().enable_latency_logging)
                    {
                        params.m_latencyStartTime.tv_sec  = ((GstNvVstMeta*)frame_data->meta)->ts / 1000000;
                        params.m_latencyStartTime.tv_usec = ((GstNvVstMeta*)frame_data->meta)->ts % 1000000;
                    }
                    if (m_consumer)
                    {
                        m_consumer->onFrame(params);
                    }
                    output_size = 0;
                }
                if (output_buffer)
                {
                    free (output_buffer);
                    output_buffer = nullptr;
                }
            }
            LOG(info) << "+++++++++++++ Total frame Sent: " << m_sentFramesCount
                      << " +++++++++++++" << endl;
            m_sentFramesCount = 0;
            continue;
        }

        frame_data->m_isTransformed = (frame_data->m_targetWidth * frame_data->m_targetHeight)
                                     < (frame_data->m_sourceWidth * frame_data->m_sourceHeight);
        int encoder_width = frame_data->m_isTransformed ?
                            frame_data->m_targetWidth : frame_data->m_sourceWidth;
        int encoder_height = frame_data->m_isTransformed ?
                             frame_data->m_targetHeight : frame_data->m_sourceHeight;

        /* Get the buffer from sample */
        if (frame_data->m_sample)
        {
            frame_data->m_gstBuffer = gst_sample_get_buffer (frame_data->m_sample);
            if (frame_data->m_gstBuffer == nullptr)
            {
                LOG (warning) << "No more buffers available from app sink element" << endl;
                gst_sample_unref (frame_data->m_sample);
                continue;
            }

            /* Map the gst buffer */
            if (gst_buffer_map (frame_data->m_gstBuffer, &frame_data->m_map, GST_MAP_READ) == false)
            {
                LOG (warning) << "Map the gst buffer Failed" << endl;
                gst_sample_unref (frame_data->m_sample);
                continue;
            }
            if (frame_data->m_map.size != sizeof(NvBufSurface))
            {
                software_mode = true;
            }
        }
#ifndef JETSON_PLATFORM
        frame_data->m_isTransformed = true;
#endif
        m_encoderTransformed = frame_data->m_isTransformed;
        fd_index_pair = resetEncIfRequiredAndGetFreeBuffer (encoder_width, encoder_height, false);
        fd  = fd_index_pair.first;
        index  = fd_index_pair.second;
        if (frame_data->m_sample)
        {
            input_buf.m_inputBuffer = frame_data->m_map.data;
            if (NvBufWrapper::getInstance()->getFDAndDoTransformIfNeeded(input_buf, frame_data->m_sourceWidth,
                                                            frame_data->m_sourceHeight,
                                                            frame_data->m_targetWidth,
                                                            frame_data->m_targetHeight,
                                                            software_mode, &fd,
                                                            &frame_data->m_isTransformed) != 0)
            {
                LOG(error) << "getFDAndDoTransformIfNeeded failed" << endl;
                continue;
            }
        }
        /* Compositor case, transform, where FD is already available from compositor API in nvgstvideosource.h */
        else if (frame_data->m_isTransformed && frame_data->m_fd)
        {
            input_buf.m_inputFD = frame_data->m_fd;
            if (NvBufWrapper::getInstance()->getFDAndDoTransformIfNeeded(input_buf, frame_data->m_sourceWidth,
                                                            frame_data->m_sourceHeight,
                                                            frame_data->m_targetWidth,
                                                            frame_data->m_targetHeight,
                                                            software_mode, &fd,
                                                            &frame_data->m_isTransformed) != 0)
            {
                LOG(error) << "getFDAndDoTransformIfNeeded failed" << endl;
                continue;
            }
        }
        /* Compositor case non transform, where FD is already available from compositor API in nvgstvideosource.h */
        else
        {
            fd = frame_data->m_fd;
            /* Setting index of wrapper object to add this surface to Q in destructor */
            frame_data->m_fdWrapperObj->get()->setIndex(index);
        }
        fd_index_pair = make_pair(fd, index);

        {
            std::lock_guard<std::mutex> queueLock(m_fdGstSampleMapLock);
            /* Keep samples in map as we need to return it to decoder by decreasing
             * ref count once they are DQed from Encoder.
             */
            FD_Index_Sample_Map::iterator it = m_fdGstSampleMap.find(fd);
            if(it == m_fdGstSampleMap.end())
            {
                m_fdGstSampleMap[fd] = std::make_pair(index, frame_data);
                fd_index_pair        = std::make_pair(fd, index);
            }
            else
            {
                auto l_frame_data = it->second.second;
                if (l_frame_data->m_sample != nullptr)
                {
                    LOG(warning) << "unreffing stale gst sample for fd = " << fd << " index = " << index << endl;
                    gst_sample_unref ((GstSample *)l_frame_data->m_sample);
                    it->second.second->m_sample = nullptr;
                }
                m_fdGstSampleMap[fd] = std::make_pair(it->second.first, frame_data);
                fd_index_pair        = std::make_pair(fd, it->second.first);
            }
        }
        if (m_surfacePool)
        {
            FdIndexInfo fd_index_info;
            fd_index_info.m_fdIndexPair = fd_index_pair;
            fd_index_info.m_isTransformed = frame_data->m_isTransformed;
            m_surfacePool->addFreeSurfaceToQ (fd_index_info);
        }
        /* Get QP and Bitrate values feedback from webRTCSender Class */
        int qp = 0, bitrate = 0;
        double frame_rate = 0;
        if (m_consumer && m_consumer->getConsumerType() == ConsumerType::webrtcConsumer)
        {
            m_consumer->getwebRTCFeedback (&qp, &bitrate, &frame_rate);
        }

        int min_bitrate = DEFAULT_WEBRTC_MIN_BITRATE;
        int max_bitrate = DEFAULT_WEBRTC_MAX_BITRATE;
        Json::Value webrtc_video_quality_tunning = VmsConfigManager::getInstance()->getWebrtcVideoQualityValues(encoder_height);
        if (webrtc_video_quality_tunning != Json::nullValue)
        {
            std::vector<int> bitrate_range;
            for (const auto& bitrate : webrtc_video_quality_tunning["bitrate_range"])
            {
                bitrate_range.push_back(bitrate.asInt());
            }
            if (bitrate_range.size() == 2)
            {
                min_bitrate = bitrate_range[0];
                max_bitrate = bitrate_range[1];
            }
        }
        bitrate = std::clamp(bitrate, min_bitrate * 1000,
                            max_bitrate * 1000);
        if (bitrate != 0 && m_prevBitRate != bitrate)
        {
            setRates ((unsigned int)bitrate);
            m_prevBitRate = bitrate;
        }

        FD_Index_Pair free_pair(-1, -1);
        /* 1) IN: Pass the fd and index pair to Encoder class for encoding
        ** 2) IN: Get the output_buffer with encoded data filled in 
        ** 3) IN: Get the output_size with encoded data size
        ** 4) Return: Get free buffer processed FD from Encoder class to destroy (if required)
        */
        free_pair = m_nvEncoder->Encode (fd_index_pair, &output_buffer,
                                                &output_size);
        if (output_size)
        {
            ++m_sentFramesCount;
            if (m_sentFramesCount == 1)
            {
                LOG(error) << "This is first frame received from encoder output_size = " << output_size << endl;
            }
            if (GET_CONFIG().enable_perf_logging)
            {
                m_transcodeStats.finishProcessing();
            }
        }

        if (output_size)
        {
            // TODO: Change m_videowebRTCSender onFrame fn to not require ptr parameter
            int width = frame_data->m_targetWidth;
            int height = frame_data->m_targetHeight;

            width  = width  == 0 ? m_width  : frame_data->m_targetWidth;
            height = height == 0 ? m_height : frame_data->m_targetHeight;

            /* Send the output_buffer and output_size to webRTC Sender class */
            FrameParams params;
            params.m_media          = "video";
            params.m_codec          = "H264";
            params.m_buffer         = output_buffer;
            params.m_size           = output_size;
            params.m_width          = width;
            params.m_height         = height;
            params.m_needParsing    = false;
            int64_t pts_val = frame_data->pts / 1000;
            params.m_presentationTime.tv_sec = pts_val / 1000000;
            params.m_presentationTime.tv_usec = (pts_val % 1000000);
            if (frame_data->meta && GET_CONFIG().enable_latency_logging)
            {
                params.m_latencyStartTime.tv_sec  = ((GstNvVstMeta*)frame_data->meta)->ts / 1000000;
                params.m_latencyStartTime.tv_usec = ((GstNvVstMeta*)frame_data->meta)->ts % 1000000;
            }
            if (m_consumer)
            {
                m_consumer->onFrame(params);
            }
        }
        if (output_buffer)
        {
            free (output_buffer);
            output_buffer = nullptr;
        }
        {
            std::lock_guard<std::mutex> queueLock(m_fdGstSampleMapLock);
            FD_Index_Sample_Map::iterator it = m_fdGstSampleMap.find(free_pair.first);
            if(it != m_fdGstSampleMap.end())
            {
                m_fdGstSampleMap.erase(it);
            }
        }
    }
    LOG(warning) << "Size of Q = " << m_queue->size () << endl;
    m_queue->cleanupQueue ();
    destroyEncoderAndBuffers();
    {
        std::unique_lock<std::mutex> lk(m_encoderReleaseMutex);
        m_encoderReleaseNotified = true;
        m_encoderReleaseCv.notify_all();
    }
    m_nvEncoder.reset();
    m_videowebRTCSender.reset();
    if (m_surfacePool)
    {
        m_surfacePool->freeSurfacesAndDataStructure();
        m_surfacePool.reset();
    }
    m_queue.reset();
    LOG(warning) << "Exiting Encoder Process Thread" << endl;
}

EncoderQueue::EncoderQueue(NvEncoderVideoConsumer* encoder) : m_encoder(encoder)
{}

EncoderQueue::~EncoderQueue()
{
    LOG(info) << "Cleaning encoder queue" << endl;
    clear();
}

void EncoderQueue::push(std::shared_ptr<RawFrameParams> frame_data)
{
    std::lock_guard<std::mutex> queueLock(m_queueLock);
    m_queue.push(frame_data);
    m_flowData = true;
    m_condVar.notify_all();
}

std::shared_ptr<RawFrameParams> EncoderQueue::pull()
{
    /* Funtion blocks until data is pushed in queue.
     * To unblock this fn:
     * 1) Push frame_data with fd = -1 in queue
     * 3) Set m_flowData = true and notify m_condVar
     */
    std::shared_ptr<RawFrameParams> frame_data;
    {
        std::unique_lock<std::mutex> lk(m_queueLock);
        while (m_queue.empty())
        {
            m_flowData = false;
            auto until = std::chrono::system_clock::now() + chrono::milliseconds(10000);
            m_condVar.wait_until(lk, until, [this]{ return m_flowData.load(); });
        }
        frame_data = m_queue.front();
        m_queue.pop();
    }
    return frame_data;
}

void EncoderQueue::clear()
{
    std::unique_lock<std::mutex> lk(m_queueLock);
    std::queue<std::shared_ptr<RawFrameParams>> empty;
    std::swap( m_queue, empty );
}

void EncoderQueue::cleanupQueue()
{
    std::shared_ptr<RawFrameParams> frame_data = std::make_shared<RawFrameParams>();
    std::unique_lock<std::mutex> lk(m_queueLock);
    while (m_queue.empty() == false)
    {
        frame_data = m_queue.front();
        if(frame_data->m_isTransformed)
        {
            if(frame_data->m_fd > 0)
            {
                LOG(warning) << "Destroying FD = " << frame_data->m_fd << endl;
                NvBufWrapper::getInstance()->destroyFd(frame_data->m_fd);
            }
        }
        m_queue.pop();
    }
}