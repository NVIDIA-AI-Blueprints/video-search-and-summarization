/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "nvgstvideosource.h"
#include "logger.h"

NvGstVideoSource::NvGstVideoSource(const std::string &uri, const std::map<std::string, std::string, std::less<>> &opts)
    : m_commonVideoSource(uri, opts)
{
    LOG(info) << "Creating " << __METHOD_NAME__ << endl;
    setupWebRTCIntegration();
}

NvGstVideoSource::~NvGstVideoSource()
{
    LOG(info) << "Destroying " << __METHOD_NAME__ << endl;
}

// WebRTC VideoSourceInterface implementation
void NvGstVideoSource::AddOrUpdateSink(rtc::VideoSinkInterface<webrtc::VideoFrame> *sink, const rtc::VideoSinkWants &wants)
{
    LOG(info) << __METHOD_NAME__ << endl;
    
    // Get pipeline components
    auto decoder = getDecoder();
    auto webrtcConsumer = getWebrtcConsumer();
    auto videoSender = getVideoSender();
    
    // Handle pass-through mode
    if (m_commonVideoSource.isPassThrough()) {
        if (videoSender) {
            videoSender->appendWebrtcBroacaster(m_commonVideoSource.getPeerIdStreamId(), &m_broadcaster);
        }
    } else {
        // Create consumer pipeline if needed
        m_commonVideoSource.createConsumerPipeline();
        
        // Connect WebRTC sink consumer to broadcaster
        if (webrtcConsumer) {
            webrtcConsumer->setWebrtcBroadcaster(&m_broadcaster);
        }
    }
    
    // Add sink to broadcaster
    m_broadcaster.AddOrUpdateSink(sink, wants);
    
    // Handle sink wants changes
    OnSinkWantsChanged(wants);
}

void NvGstVideoSource::RemoveSink(rtc::VideoSinkInterface<webrtc::VideoFrame> *sink)
{
    LOG(info) << __METHOD_NAME__ << endl;
    
    // Handle pass-through mode
    if (m_commonVideoSource.isPassThrough()) {
        auto videoSender = getVideoSender();
        if (videoSender) {
            LOG(info) << "Pass-through mode: removing broadcaster for peer: " << m_commonVideoSource.getPeerIdStreamId() << endl;
            videoSender->removeWebrtcBroacaster(m_commonVideoSource.getPeerIdStreamId());
        } else {
            LOG(error) << "CRITICAL: Video sender is null in pass-through mode!" << endl;
        }
    } else {
        LOG(info) << "Regular mode: stopping and removing consumers" << endl;
        auto webrtcConsumer = getWebrtcConsumer();
        if (webrtcConsumer) {
            // Use the consumer's own peer ID for consistent identification
            std::string consumerPeerId = webrtcConsumer->getPeerIdStreamId();
            LOG(info) << "Regular mode: removing broadcaster using consumer peer ID: " << consumerPeerId << endl;
            webrtcConsumer->removeWebrtcBroadcaster(consumerPeerId);
        } else {
            LOG(error) << "CRITICAL: WebRTC consumer is null in regular mode!" << endl;
        }
        // Stop and remove consumers
        m_commonVideoSource.stopAndRemoveConsumers();
    }
    
    // Remove sink from broadcaster
    m_broadcaster.RemoveSink(sink);
    LOG(info) << __METHOD_NAME__ << " completed" << endl;
}

void NvGstVideoSource::RequestRefreshFrame()
{
    // Request a refresh frame from the pipeline
    // This can be implemented by triggering a key frame request
    auto webrtcConsumer = getWebrtcConsumer();
    if (webrtcConsumer) {
        // Trigger a refresh frame if the consumer supports it
        // This is implementation-specific and may need to be adapted
    }
}

void NvGstVideoSource::OnSinkWantsChanged(const rtc::VideoSinkWants& wants)
{
    // Handle sink wants changes for adaptive streaming
    if (!m_commonVideoSource.isPassThrough()) {
        unsigned int targetPixelCount = wants.target_pixel_count.value_or(wants.max_pixel_count);
        LOG(info) << "WebRTC asked targetPixel = " << targetPixelCount 
                  << " maxPixel = " << wants.max_pixel_count
                  << " maxFps = " << wants.max_framerate_fps 
                  << " resAlignment = " << wants.resolution_alignment << endl;

        // Handle Dynamic Rate Control (DRC) for decoder
        auto decoder = getDecoder();
        if (decoder) {
            decoder->handleDRC(m_commonVideoSource.getPeerId(), wants.max_pixel_count, wants.max_framerate_fps);
        }
        
        // Handle DRC for native stream producer
        auto nativeStreamProducer = getNativeStreamProducer();
        if (nativeStreamProducer) {
            nativeStreamProducer->handleDRC(m_commonVideoSource.getPeerId(), wants.max_pixel_count, wants.max_framerate_fps);
        }
        
#ifdef JETSON_PLATFORM
        // Handle DRC for IPC producer
        auto ipcProducer = getIPCProducer();
        if (ipcProducer) {
            ipcProducer->handleDRC(m_commonVideoSource.getPeerId(), wants.max_pixel_count, wants.max_framerate_fps);
        }
#endif
    }
}

void NvGstVideoSource::setupWebRTCIntegration()
{
    // WebRTC integration is now handled in AddOrUpdateSink method
    // This ensures proper timing of pipeline creation and broadcaster connection
    LOG(info) << "WebRTC integration setup completed" << endl;
}
