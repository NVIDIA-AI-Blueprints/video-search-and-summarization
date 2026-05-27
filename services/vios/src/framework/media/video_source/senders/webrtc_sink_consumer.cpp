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

#include "webrtc_sink_consumer.h"
#include "logger.h"
// For HW -> SW I420 copy considering strides
#include "third_party/libyuv/include/libyuv/convert.h"
#include "nvhwdetection.h"
#include "api/video/i420_buffer.h"

WebrtcSinkConsumer::WebrtcSinkConsumer(const std::string& consumer_name) : IMediaDataConsumer(consumer_name)
{
    m_videowebRTCSender = std::make_shared<VideoWebRTCSender>("VideoWebRTCSender");
    setConsumerType (ConsumerType::webrtcConsumer);
}

WebrtcSinkConsumer::WebrtcSinkConsumer(const std::string& consumer_name, string peer_id, double frame_rate,
                    const std::map<std::string, std::string, std::less<>> &opts, bool enable_frame_sync /*false*/)
        : IMediaDataConsumer(consumer_name), m_peerIdStreamId (peer_id)
{
    m_videowebRTCSender = std::make_shared<VideoWebRTCSender>("VideoWebRTCSender", frame_rate, enable_frame_sync);
    setConsumerType (ConsumerType::webrtcConsumer);
}

WebrtcSinkConsumer::~WebrtcSinkConsumer()
{
    {
        std::lock_guard<std::mutex> lock(m_broadcasterMutex);
        m_broadcaster = nullptr;
    }
    LOG(info) << "WebrtcSinkConsumer instance deleted for " << m_peerIdStreamId << endl;
}

void WebrtcSinkConsumer::setWebrtcBroadcaster(void* broadcaster)
{
    {
        std::lock_guard<std::mutex> lock(m_broadcasterMutex);
        m_broadcaster = broadcaster;
    }
    if (m_videowebRTCSender)
    {
        m_videowebRTCSender->appendWebrtcBroacaster(m_peerIdStreamId, (rtc::VideoBroadcaster*)broadcaster);
    }
}

void WebrtcSinkConsumer::getwebRTCFeedback(int* qp, int* bitrate, double* frame_rate)
{
    if (m_videowebRTCSender) {
        m_videowebRTCSender->getwebRTCFeedback (qp, bitrate, frame_rate);
    }
}

void WebrtcSinkConsumer::setBitstreamConsumer(std::shared_ptr<IMediaDataConsumer> bitstreamConsumer)
{
    m_bitstreamConsumer = bitstreamConsumer;
    setConsumerType (ConsumerType::encoder);
}

void WebrtcSinkConsumer::onFrame(std::shared_ptr<RawFrameParams> frame_data)
{
    // Start performance tracking for WebRTC sink processing
    m_transcodeStats.startProcessing();

    if (frame_data->m_isYuvBuffer && frame_data->m_buffer != nullptr)
    {
        rtc::scoped_refptr<webrtc::I420Buffer> yuv_buffer = rtc::scoped_refptr<webrtc::I420Buffer>(static_cast<webrtc::I420Buffer*>(static_cast<void*>(frame_data->m_buffer)));

        /* Create a VideoFrame to pass it further in webRTC framework */
        if (yuv_buffer.get() != nullptr)
        {
            webrtc::VideoFrame webRTC_input_video_frame  = webrtc::VideoFrame::Builder()
                                        .set_video_frame_buffer(yuv_buffer)
                                        .build();
            std::lock_guard<std::mutex> lock(m_broadcasterMutex);
            if (m_broadcaster != nullptr)
            {
                ((rtc::VideoBroadcaster *)m_broadcaster)->OnFrame(webRTC_input_video_frame);
            }
        }
    }

#ifdef UNIT_TEST
    m_frameCountForTest++;
#endif

    // End performance tracking after WebRTC sink processing
    m_transcodeStats.finishProcessing();
}

void WebrtcSinkConsumer::onFrame(FrameParams& params)
{
    // Start performance tracking for WebRTC frame processing
    m_transcodeStats.startProcessing();

    if (m_bitstreamConsumer)
    {
        return m_bitstreamConsumer->onFrame(params);
    }
    else if (params.m_codec == "H264")
    {
        if (m_videowebRTCSender) {
            m_videowebRTCSender->onFrame(params);
        }
    }

#ifdef UNIT_TEST
    m_frameCountForTest++;
#endif

    // End performance tracking after WebRTC frame processing
    m_transcodeStats.finishProcessing();
}

void WebrtcSinkConsumer::removeWebrtcBroadcaster(const std::string& peerid)
{
    LOG(info) << "WebrtcSinkConsumer::removeWebrtcBroadcaster for peer: " << peerid << endl;
    LOG(info) << "Using internal peerIdStreamId: " << m_peerIdStreamId << endl;
    
    if (m_videowebRTCSender) {
        LOG(info) << "Calling VideoWebRTCSender::removeWebrtcBroacaster with: " << m_peerIdStreamId << endl;
        m_videowebRTCSender->removeWebrtcBroacaster(m_peerIdStreamId); // Use internal peer ID
    } else {
        LOG(warning) << "VideoWebRTCSender is null in WebrtcSinkConsumer" << endl;
    }
    {
        std::lock_guard<std::mutex> lock(m_broadcasterMutex);
        m_broadcaster = nullptr;
    }
}
