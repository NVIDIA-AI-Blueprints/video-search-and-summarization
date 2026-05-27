/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "CommonVideoSource.h"
#include "PipelineManager.h"
#include "logger.h"
#include "config.h"
#include "nvhwdetection.h"
#include "osd/llosd.h"
#include "utils.h"

#include "media_producer.h"
#include "stream_monitor.h"
#include "../producers/webrtcstreamproducer.h"

using nv_vms::VmsErrorCode;

// Implementation of CommonVideoSource methods
void CommonVideoSource::getDecodeStats(LatencyStats& stats)
{
    auto decoder = m_pipelineManager->getDecoder();
    if (decoder)
    {
        decoder->getStats(m_peerid, stats);
    }
}

// Implementation of CommonVideoSource constructor and destructor
CommonVideoSource::CommonVideoSource(const std::string &uri, const std::map<std::string, std::string, std::less<>> &opts)
    : m_pipelineManager(std::make_shared<PipelineManager>()), m_config(uri, opts)
{
    // Initialize core state from configuration
    m_uri = uri;
    m_recordedPlayback = m_config.isRecordedPlayback();
    m_passThrough = m_config.isPassThrough();
    m_livePlayback = m_config.isLivePlayback();
    m_peerid = m_config.getPeerId();
    m_peerIdStreamId = m_config.getPeerIdStreamId();
    m_quality = m_config.getQuality().quality;
    m_compositePlayback = m_config.getCompositor().enabled;
    m_compositeShowSensorName = m_config.getCompositor().showSensorName;
    m_isNativeStream = m_config.isNativeStream();
    
    // Set composite URLs if needed
    if (m_compositePlayback) {
        m_urlsList = m_config.getCompositor().urls;
    }
    
    LOG(info) << "peerid: " << m_peerid << " uri: " << m_uri 
              << " Video Quality: " << m_config.getQuality().quality << endl;
    
    try {
        // Create and start the pipeline using the manager
        m_pipelineManager->createPipeline(m_config);
        m_pipelineManager->startPipeline();
        
        // Setup legacy members for backward compatibility
        m_gstdecoder = m_pipelineManager->getDecoder();
        m_videowebRTCSender = m_pipelineManager->getVideoSender();
        m_nativeStreamProducer = m_pipelineManager->getNativeStreamProducer();
        
        if (m_pipelineManager->isCompositePipeline()) {
            m_gstdecoderList = m_pipelineManager->getDecoders();
            m_nvLLOverlayList = m_pipelineManager->getOverlays();

            // Ensure urlsList matches actual decoder count to prevent OOB access
            if (m_compositePlayback && m_urlsList.size() > m_gstdecoderList.size()) {
                LOG(warning) << "Clamping URL list from " << m_urlsList.size()
                             << " to " << m_gstdecoderList.size() << " (decoder count)" << endl;
                m_urlsList.resize(m_gstdecoderList.size());
            }
        }
        
        // Ensure decoder is properly initialized
        if (m_gstdecoder) {
            LOG(info) << "Decoder initialized successfully for peer: " << m_peerid << endl;
        } else {
            LOG(info) << "Pipeline created without decoder (pass-through or native stream) for peer: " << m_peerid << endl;
        }
        
        // Handle image capture special case
        if (m_config.isImageCapture() && m_gstdecoder) {
            m_gstdecoder->play();
        }
        
    } catch (const std::exception& e) {
        LOG(error) << "Failed to create pipeline for peer " << m_peerid << ": " << e.what() << endl;
        throw;
    }
}

CommonVideoSource::~CommonVideoSource()
{
    LOG(info) << "Destroying CommonVideoSource for peer: " << m_peerid << endl;
    
    // Stop source producer first to prevent any new data flow
    if (m_sourceProducer) {
        try {
            m_sourceProducer->stop();
            LOG(verbose) << "Stopped source producer for peer: " << m_peerid << endl;
        } catch (const std::exception& e) {
            LOG(error) << "Exception while stopping source producer: " << e.what() << endl;
        }
    }
    
    // Pipeline manager handles all component cleanup in proper order
    try {
        m_pipelineManager->destroyPipeline();
        LOG(verbose) << "Pipeline destroyed for peer: " << m_peerid << endl;
    } catch (const std::exception& e) {
        LOG(error) << "Exception during pipeline destruction for peer " << m_peerid << ": " << e.what() << endl;
    }

    // Explicitly reset decoder reference before destruction completes
    if (m_gstdecoder) {
        m_gstdecoder.reset();
    }

    // Clear decoder lists
    if (!m_gstdecoderList.empty()) {
        m_gstdecoderList.clear();
    }

    // RAII: All shared_ptr members are automatically cleaned up
    LOG(info) << "CommonVideoSource destroyed for peer: " << m_peerid << endl;
}

// Note: IMediaDataProducer integration is now handled by PipelineBuilder classes

// Note: Producer setup is now handled by PipelineBuilder classes

// Legacy methods for backward compatibility
void CommonVideoSource::resetConsumerAndDestroyDecoderIfRequired()
{
    LOG(info) << "Resetting pipeline for peer: " << m_peerid << endl;
    
    // Stop source producer first to prevent any new data flow
    if (m_sourceProducer) {
        try {
            m_sourceProducer->stop();
            LOG(verbose) << "Stopped source producer for peer: " << m_peerid << endl;
        } catch (const std::exception& e) {
            LOG(error) << "Exception while stopping source producer: " << e.what() << endl;
        }
    }
    
    // Pipeline manager handles the proper cleanup sequence
    try {
        m_pipelineManager->stopPipeline();
        m_pipelineManager->destroyPipeline();
        LOG(verbose) << "Pipeline reset completed for peer: " << m_peerid << endl;
    } catch (const std::exception& e) {
        LOG(error) << "Exception during pipeline reset for peer " << m_peerid << ": " << e.what() << endl;
    }
    
    // Clear legacy references - they will be updated when new pipeline is created
    m_gstdecoder.reset();
    m_gstdecoderList.clear();
    m_nvLLOverlayList.clear();
}

// Note: setupDecoder method removed - functionality replaced by PipelineManager and builders

// Original method implementations (preserved for backward compatibility)
VmsErrorCode CommonVideoSource::controlStreamFileVideoSource(const std::string& action, const std::string& seek_value)
{
    if (m_gstdecoder)
    {
        if (action == "pause")
        {
            if (m_livePlayback)
            {
                /* Setting consumer ready to false to pause the particular peer */
                m_gstdecoder->setConsumerReady (m_peerid, false);
            }
            m_gstdecoder->pause();
        }
        else if (action == "resume")
        {
            if (m_livePlayback)
            {
                /* Setting consumer ready to false to resume the particular peer */
                m_gstdecoder->setConsumerReady (m_peerid, true);
            }
            m_gstdecoder->play();
        }
        else
        {
            return m_gstdecoder->controlStream(action, seek_value);
        }
    }
    else if(m_passThrough)
    {
        if (m_videowebRTCSender)
        {
            if (action == "pause")
            {
                if (m_videowebRTCSender) {
                m_videowebRTCSender->pause(m_peerid);
            }
            }
            else if (action == "resume")
            {
                if (m_videowebRTCSender) {
                m_videowebRTCSender->resume(m_peerid);
            }
            }
        }
    }
    else if (m_isNativeStream)
    {
        if (action == "pause")
        {
            if (m_nativeStreamProducer)
            {
                /* Setting consumer ready to false to pause the particular peer */
                m_nativeStreamProducer->setConsumerReady (m_peerid, false);
            }
        }
        else if (action == "resume")
        {
            if (m_nativeStreamProducer)
            {
                /* Setting consumer ready to false to resume the particular peer */
                m_nativeStreamProducer->setConsumerReady (m_peerid, true);
            }
        }
    }
#ifdef JETSON_PLATFORM
    else if(m_nvIPCProducer)
    {
        if (action == "pause")
        {
            /* Setting consumer ready to false to pause the particular peer */
            m_nvIPCProducer->setConsumerReady (m_peerid, false);
        }
        else if (action == "resume")
        {
            /* Setting consumer ready to false to resume the particular peer */
            m_nvIPCProducer->setConsumerReady (m_peerid, true);
        }
    }
#endif
    return VmsErrorCode::NoError;
}

void CommonVideoSource::switchStreamVideoSource(std::string url, const std::map<std::string, std::string, std::less<>> &opts)
{
    // Early return if switching to the same URL
    if (m_livePlayback && (url == m_uri))
    {
        LOG(info) << "Same URL requested, no switch needed for peer: " << m_peerid << endl;
        return;
    }
    
    LOG(info) << "Switching stream from " << m_uri << " to " << url << " for peer: " << m_peerid << endl;
    
    // Update state
    m_uri = url;
    m_recordedPlayback = (url.find("file://") == 0);
    
    try {
        // Create new configuration for the switch
        PipelineConfiguration newConfig(url, opts);
        
        // Use pipeline manager to handle the switch cleanly
        m_pipelineManager->switchPipeline(newConfig);
        
        // Update local configuration and state
        m_config = newConfig;
        m_passThrough = m_config.isPassThrough();
        m_livePlayback = m_config.isLivePlayback();
        m_compositePlayback = m_config.getCompositor().enabled;
        
        // Update legacy members for backward compatibility
        m_gstdecoder = m_pipelineManager->getDecoder();
        m_videowebRTCSender = m_pipelineManager->getVideoSender();
        m_nativeStreamProducer = m_pipelineManager->getNativeStreamProducer();
        
        if (m_pipelineManager->isCompositePipeline()) {
            m_gstdecoderList = m_pipelineManager->getDecoders();
            m_nvLLOverlayList = m_pipelineManager->getOverlays();
        }
        
        LOG(info) << "Stream switched successfully for peer: " << m_peerid << endl;
        
    } catch (const std::exception& e) {
        LOG(error) << "Failed to switch stream for peer " << m_peerid << ": " << e.what() << endl;
        throw;
    }
}

void CommonVideoSource::streamSettingVideoSource(const std::unordered_map<std::string, std::string> &opts)
{
    // Get overlay from pipeline manager instead of legacy member
    auto overlay = m_pipelineManager->getOverlay();
    if (overlay)
    {
        bool switchConsumer = overlay->streamSettings(opts);
        if (switchConsumer)
        {
            setDecoderConsumerPipeline();
        }
    }
    else if (GET_OSD_INSTANCE()->isError())
    {
        LOG(error) << "Overlay cuda libs not found, Disabling overlay" << endl;
    }
}

void CommonVideoSource::startStream()
{
    LOG(info) << "startStream: " << m_peerid << endl;
    if (m_compositePlayback)
    {
        for (size_t i = 0; i < m_gstdecoderList.size(); i++)
        {
            std::shared_ptr<GstNvVideoDecoder> dec = m_gstdecoderList[i];
            if(dec)
            {
                dec->setConsumerReady(m_peerid);
                if (m_compositeShowSensorName)
                {
                    dec->setConsumerReady(m_peerid);
                }
                dec->play();
            }
            else
            {
                LOG(warning) << "Decoder Instance is NULL" << endl;
            }
        }
    }
    else
    {
#ifdef JETSON_PLATFORM
        auto overlay = getOverlay();
        bool is_bbox = overlay && !GET_OSD_INSTANCE()->isError() && overlay->isBboxEnabled();
        if (is_bbox == false || GET_CONFIG().enable_ipc_path == false)
#endif
        {
            if(m_gstdecoder)
            {
                m_gstdecoder->setConsumerReady(m_peerid);
                m_gstdecoder->play();
            }
            else if (m_nativeStreamProducer)
            {
                m_nativeStreamProducer->setConsumerReady(m_peerid);
            }
            else
            {
                LOG(warning) << "Decoder Instance is NULL" << endl;
            }
        }
#ifdef JETSON_PLATFORM
        else
        {
            if (m_nvIPCProducer)
            {
                m_nvIPCProducer->setConsumerReady(m_peerid);
            }
        }
#endif
    }
}

void CommonVideoSource::setDecoderConsumerPipeline()
{
    LOG(error) << "Setting Decoder Consumer Pipeline" << endl;
    if (m_gstdecoder == nullptr)
    {
        LOG(warning) << "Decoder instance is null" << endl;
        return;
    }
    
    // Get components from pipeline manager
    auto overlay = m_pipelineManager->getOverlay();
    auto transform = m_pipelineManager->getTransform();
    auto transformSink = m_pipelineManager->getTransformSink();
    auto encoder = m_pipelineManager->getEncoder();
    auto webrtcConsumer = m_pipelineManager->getWebrtcConsumer();
    
    if (overlay && !GET_OSD_INSTANCE()->isError() && overlay->isOverlayEnabled())
    {
        // Decoder -> Transform -> overlay -> HW / SW encoder
        if (transform) {
            m_gstdecoder->setConsumer(m_peerid, transform);
            transform->setConsumer(overlay);
        }
        if (NvHwDetection::getInstance()->m_useNvV4l2Enc == true)
        {
            if (encoder && webrtcConsumer) {
                overlay->setConsumer(encoder);
                encoder->setConsumer(webrtcConsumer);
                LOG(info) << "Add consumer gstdecoder->LLTransform->LLOverlay->nvEncoder" << endl;
            }
        }
        else
        {
            if (transformSink && webrtcConsumer) {
                overlay->setConsumer(transformSink);
                transformSink->setConsumer(webrtcConsumer);
                LOG(info) << "Add consumer gstdecoder->LLTransform->LLOverlay->LLTransformSink" << endl;
            }
        }
        m_gstdecoder->setConsumerReady(m_peerid);
    }
    else
    {
        // Decoder -> Transform -> HW / SW encoder
        if (NvHwDetection::getInstance()->m_useNvV4l2Enc == true)
        {
            if (transform && encoder && webrtcConsumer) {
                m_gstdecoder->setConsumer(m_peerid, transform);
                transform->setConsumer(encoder);
                encoder->setConsumer(webrtcConsumer);
                LOG(info) << "Add consumer gstdecoder->LLTransform->nvEncoder" << endl;
            }
        }
        else
        {
            if (transformSink && webrtcConsumer) {
                m_gstdecoder->setConsumer(m_peerid, transformSink);
                transformSink->setConsumer(webrtcConsumer);
                LOG(info) << "Add consumer gstdecoder->LLTransformSink" << endl;
            }
        }
        m_gstdecoder->setConsumerReady(m_peerid);
    }
}

void CommonVideoSource::createConsumerPipeline()
{
    if (m_compositePlayback)
    {
        auto compositor = m_pipelineManager->getCompositor();

        // Diagnostic logging for debugging
        LOG(info) << "Composite loop: urlsList=" << m_urlsList.size()
                  << " decoders=" << m_gstdecoderList.size()
                  << " overlays=" << m_nvLLOverlayList.size() << endl;

        const size_t loopSize = std::min(m_urlsList.size(), m_gstdecoderList.size());
        for (size_t i = 0; i < loopSize; i++)
        {
            m_gstdecoder = m_gstdecoderList[i];
            if (!m_gstdecoder)
            {
                LOG(warning) << "Null decoder at index " << i << endl;
                continue;
            }
            
            std::shared_ptr<NvLLOverlay> overlay = nullptr;
            if (m_nvLLOverlayList.size() > i)
            {
                overlay = m_nvLLOverlayList[i];
            }
            if (overlay && !GET_OSD_INSTANCE()->isError() && m_compositeShowSensorName)
            {
                m_gstdecoder->setConsumer(m_peerid, overlay);
                m_gstdecoder->setQuality(m_peerid, m_quality);
                if (compositor) {
                    overlay->setConsumer(compositor);
                }
                LOG(info) << "Add consumer gstdecoder->LLOverlay->Compositor" << endl;
            }
            else
            {
                if (compositor) {
                    m_gstdecoder->setConsumer(m_peerid, compositor);
                    LOG(info) << "Add consumer gstdecoder->Compositor" << endl;
                }
                m_gstdecoder->setQuality(m_peerid, m_quality);
            }
        }
    }
    else if (m_gstdecoder)
    {
        // Get components from pipeline manager
        auto overlay = m_pipelineManager->getOverlay();
        auto transform = m_pipelineManager->getTransform();
        auto transformSink = m_pipelineManager->getTransformSink();
        auto encoder = m_pipelineManager->getEncoder();
        auto webrtcConsumer = m_pipelineManager->getWebrtcConsumer();
        
        if (overlay && !GET_OSD_INSTANCE()->isError() && overlay->isOverlayEnabled())
        {
            if (transform) {
                m_gstdecoder->setConsumer(m_peerid, transform);
                transform->setConsumer(overlay);
                LOG(info) << "Add consumer gstdecoder->LLTransform->LLOverlay" << endl;
            } else {
                LOG(error) << "LLTransform not properly initialized for overlay" << endl;
            }
            m_gstdecoder->setQuality(m_peerid, m_quality);
        }
        else
        {
            if (NvHwDetection::getInstance()->m_useNvV4l2Enc == true)
            {
                if (transform && encoder && webrtcConsumer) {
                    m_gstdecoder->setConsumer(m_peerid, transform);
                    transform->setConsumer(encoder);
                    encoder->setConsumer(webrtcConsumer);
                    LOG(info) << "Add consumer gstdecoder->LLTransform->nvEncoder" << endl;
                } else {
                    LOG(error) << "Pipeline components not properly initialized for NV V4L2 encoding" << endl;
                }
            }
            else
            {
                if (transformSink && webrtcConsumer) {
                    m_gstdecoder->setConsumer(m_peerid, transformSink);
                    transformSink->setConsumer(webrtcConsumer);
                    LOG(info) << "Add consumer gstdecoder->LLTransformSink" << endl;
                } else {
                    LOG(error) << "Pipeline components not properly initialized for standard encoding" << endl;
                }
            }
            m_gstdecoder->setQuality(m_peerid, m_quality);
            if (GET_OSD_INSTANCE()->isError() && m_gstdecoder->isOverlay())
            {
                LOG(error) << "Overlay cuda libs not found, Disabling overlay" << endl;
            }
        }
    }
    else if (m_nativeStreamProducer)
    {
        // Get components from pipeline manager
        auto overlay = m_pipelineManager->getOverlay();
        auto transform = m_pipelineManager->getTransform();
        auto transformSink = m_pipelineManager->getTransformSink();
        auto encoder = m_pipelineManager->getEncoder();
        
        if (overlay && !GET_OSD_INSTANCE()->isError() && overlay->isOverlayEnabled())
        {
            if (transform) {
                m_nativeStreamProducer->setConsumer(m_peerid, transform);
                transform->setConsumer(overlay);
                m_nativeStreamProducer->setQuality(m_peerid, m_quality);
            } else {
                LOG(error) << "LLTransform not properly initialized for native stream overlay" << endl;
            }
        }
        else
        {
            if (NvHwDetection::getInstance()->m_useNvV4l2Enc == true)
            {
                if (transform && encoder) {
                    m_nativeStreamProducer->setConsumer(m_peerid, transform);
                    transform->setConsumer(encoder);
                } else {
                    LOG(error) << "Pipeline components not properly initialized for native stream NV V4L2 encoding" << endl;
                }
            }
            else
            {
                if (transformSink) {
                    m_nativeStreamProducer->setConsumer(m_peerid, transformSink);
                } else {
                    LOG(error) << "LLTransformSink not properly initialized for native stream" << endl;
                }
            }
            m_nativeStreamProducer->setQuality(m_peerid, m_quality);
        }
    }

#ifdef JETSON_PLATFORM
    else if (auto ipcProducer = m_pipelineManager->getIPCProducer())
    {
        auto overlay = m_pipelineManager->getOverlay();
        auto transform = m_pipelineManager->getTransform();
        
        if (overlay && !GET_OSD_INSTANCE()->isError() && overlay->isBboxEnabled())
        {
            if (transform) {
                ipcProducer->setConsumer(m_peerid, transform);
                transform->setConsumer(overlay);
                ipcProducer->setQuality(m_peerid, m_quality);
                LOG(info) << "Add consumer m_ipcConsumer->LLTransform->LLOverlay" << endl;
            } else {
                LOG(error) << "LLTransform not properly initialized for IPC overlay" << endl;
            }
        }
    }
#endif
}

void CommonVideoSource::setConsumerReady()
{
    if (m_compositePlayback)
    {
        // Diagnostic logging for debugging
        LOG(info) << "Composite loop: urlsList=" << m_urlsList.size()
                  << " decoders=" << m_gstdecoderList.size()
                  << " overlays=" << m_nvLLOverlayList.size() << endl;

        const size_t loopSize = std::min(m_urlsList.size(), m_gstdecoderList.size());
        for (size_t i = 0; i < loopSize; i++)
        {
            m_gstdecoder = m_gstdecoderList[i];
            if (!m_gstdecoder)
            {
                LOG(warning) << "setConsumerReady: Null decoder at index " << i << endl;
                continue;
            }

            std::shared_ptr<NvLLOverlay> overlay = nullptr;
            if (m_nvLLOverlayList.size() > i)
            {
                overlay = m_nvLLOverlayList[i];
            }
            m_gstdecoder->setConsumerReady(m_peerid);
        }
        auto compositor = m_pipelineManager->getCompositor();
        if (compositor)
        {
            compositor->setOriginalFrameSize();
        }
    }
    else if (m_gstdecoder)
    {
        m_gstdecoder->setConsumerReady(m_peerid);
    }
    else if (m_nativeStreamProducer)
    {
        m_nativeStreamProducer->setConsumerReady(m_peerid);
    }

#ifdef JETSON_PLATFORM
    else if (auto ipcProducer = m_pipelineManager->getIPCProducer())
    {
        auto overlay = m_pipelineManager->getOverlay();
        if (overlay && !GET_OSD_INSTANCE()->isError() && overlay->isBboxEnabled())
        {
            ipcProducer->setConsumerReady(m_peerid);
            LOG(info) << "Add consumer m_ipcConsumer->LLTransform->LLOverlay" << endl;
        }
    }
#endif
    // Set original frame sizes for pipeline components
    auto overlay = m_pipelineManager->getOverlay();
    if (overlay)
    {
        overlay->setOriginalFrameSize();
    }
    auto transform = m_pipelineManager->getTransform();
    if (transform)
    {
        transform->setOriginalFrameSize();
    }
    auto transformSink = m_pipelineManager->getTransformSink();
    if (transformSink)
    {
        transformSink->setOriginalFrameSize();
    }
}

void CommonVideoSource::stopAndRemoveConsumers()
{
    if (m_compositePlayback)
    {
        auto transform = m_pipelineManager->getTransform();
        if (transform) {
            transform->stopTransform();
        }

        // Diagnostic logging for debugging
        LOG(info) << "Composite loop: urlsList=" << m_urlsList.size()
                  << " decoders=" << m_gstdecoderList.size() 
                  << " overlays=" << m_nvLLOverlayList.size() << endl;

        const size_t loopSize = std::min(m_urlsList.size(), m_gstdecoderList.size());
        for (size_t i = 0; i < loopSize; i++)
        {
            if (m_nvLLOverlayList.size() > i)
            {
                auto overlay = m_nvLLOverlayList[i];
                if (overlay) {
                    overlay->stopOverlay();
                }
            }

            m_gstdecoder = m_gstdecoderList[i];
            if (!m_gstdecoder)
            {
                LOG(warning) << "stopAndRemoveConsumers: Null decoder at index " << i << endl;
                continue;
            }

            m_gstdecoder->removeConsumer(m_peerid);
            if (m_compositeShowSensorName)
            {
                m_gstdecoder->removeConsumer(m_peerid);
            }
        }
    }
    else if (m_gstdecoder)
    {
        m_gstdecoder->removeConsumer(m_peerid);
    }
    else if (m_nativeStreamProducer)
    {
        m_nativeStreamProducer->removeConsumer(m_peerid);
    }
#ifdef JETSON_PLATFORM
    else if (auto ipcProducer = m_pipelineManager->getIPCProducer())
    {
        ipcProducer->removeConsumer(m_peerid);
    }
#endif
}

// WebRTC sink management methods have been moved to NvGstVideoSource
// These methods are no longer needed in CommonVideoSource as it should
// only handle pipeline management, not WebRTC-specific functionality

gint64 CommonVideoSource::getPositionFileVideoSource()
{
    if (m_gstdecoder)
    {
        return m_gstdecoder->getAbsPosition();
    }
    else
    {
        return 0;
    }
}

uint64_t CommonVideoSource::getLastTS()
{
    if (m_gstdecoder)
    {
        return m_gstdecoder->getLastTS();
    }
    return 0;
}

int64_t CommonVideoSource::getFileStartTime()
{
    if (m_gstdecoder)
    {
        return m_gstdecoder->getFileStartTime();
    }
    return 0;
}

uint32_t CommonVideoSource::getDurationStream()
{
    if (m_gstdecoder)
    {
        return m_gstdecoder->getDurationStream();
    }
    return 0;
}

int64_t CommonVideoSource::getFirstTs()
{
    auto encoder = m_pipelineManager->getEncoder();
    return encoder ? encoder->getFirstTs() : 0;
}

string CommonVideoSource::getSensorName()
{
    string name;
    if (m_gstdecoder)
    {
        name = m_gstdecoder->getSensorName();
    }
    return name;
}

string CommonVideoSource::getSensorId()
{
    string sensorId;
    if (m_gstdecoder)
    {
        sensorId = m_gstdecoder->getSensorId();
    }
    return sensorId;
}

string CommonVideoSource::getStreamState()
{
    // Check decoder first (most common case)
    auto decoder = m_pipelineManager->getDecoder();
    if (decoder)
    {
        return decoder->getstate(m_peerid);
    }
    
    // Check pass-through mode
    if (m_passThrough)
    {
        auto videoSender = m_pipelineManager->getVideoSender();
        if (videoSender)
        {
            return videoSender->getPlaybackState(m_peerIdStreamId);
        }
        return "STOPPED";
    }
    
    // Check native stream producer
    auto nativeProducer = m_pipelineManager->getNativeStreamProducer();
    if (nativeProducer)
    {
        return nativeProducer->getstate();
    }
    
#ifdef JETSON_PLATFORM
    // Check IPC producer
    auto ipcProducer = m_pipelineManager->getIPCProducer();
    if (ipcProducer)
    {
        return ipcProducer->getstate();
    }
#endif

    return "NOT_PLAYING";
}

bool CommonVideoSource::isStreamError()
{
    // Check decoder first
    auto decoder = m_pipelineManager->getDecoder();
    if (decoder)
    {
        return decoder->getError();
    }
    
#ifdef JETSON_PLATFORM
    // Check IPC producer on Jetson platform
    auto ipcProducer = m_pipelineManager->getIPCProducer();
    if (ipcProducer)
    {
        return ipcProducer->getError();
    }
#endif
    
    return false;
}

Json::Value CommonVideoSource::getOverlayStatus()
{
    Json::Value ret;
    auto overlay = m_pipelineManager->getOverlay();
    if (overlay)
    {
        ret = overlay->getOverlayStatus();
    }
    return ret;
}

std::string CommonVideoSource::getBuffer()
{
    auto imageEncoder = m_pipelineManager->getImageEncoder();
    return imageEncoder ? imageEncoder->getImageBuffer() : std::string{};
}

void CommonVideoSource::setBitstreamConsumer(std::shared_ptr<IMediaDataConsumer> bitstreamConsumer)
{
    auto webrtcConsumer = m_pipelineManager->getWebrtcConsumer();
    if (webrtcConsumer)
    {
        webrtcConsumer->setBitstreamConsumer(bitstreamConsumer);
    }
}

#ifdef UNIT_TEST
shared_ptr<GstNvVideoDecoder> CommonVideoSource::getDecoder()
{
    return m_gstdecoder;
}
#endif

// Pipeline management accessors for WebRTC integration
std::shared_ptr<GstNvVideoDecoder> CommonVideoSource::getDecoder() const
{
    return m_pipelineManager->getDecoder();
}

std::shared_ptr<NvEncoderVideoConsumer> CommonVideoSource::getEncoder() const
{
    return m_pipelineManager->getEncoder();
}

std::shared_ptr<WebrtcSinkConsumer> CommonVideoSource::getWebrtcConsumer() const
{
    return m_pipelineManager->getWebrtcConsumer();
}

std::shared_ptr<NvLLOverlay> CommonVideoSource::getOverlay() const
{
    return m_pipelineManager->getOverlay();
}

std::shared_ptr<NvLLTransform> CommonVideoSource::getTransform() const
{
    return m_pipelineManager->getTransform();
}

std::shared_ptr<NvLLTransform> CommonVideoSource::getTransformSink() const
{
    return m_pipelineManager->getTransformSink();
}

std::shared_ptr<ImageEnc> CommonVideoSource::getImageEncoder() const
{
    return m_pipelineManager->getImageEncoder();
}

std::shared_ptr<NvCompositor> CommonVideoSource::getCompositor() const
{
    return m_pipelineManager->getCompositor();
}

std::shared_ptr<VideoWebRTCSender> CommonVideoSource::getVideoSender() const
{
    return m_pipelineManager->getVideoSender();
}

std::shared_ptr<NativeStreamProducer> CommonVideoSource::getNativeStreamProducer() const
{
    return m_pipelineManager->getNativeStreamProducer();
}

#ifdef JETSON_PLATFORM
std::shared_ptr<NvIPCProducer> CommonVideoSource::getIPCProducer() const
{
    return m_pipelineManager->getIPCProducer();
}
#endif

// For composite pipelines
const std::vector<std::shared_ptr<GstNvVideoDecoder>>& CommonVideoSource::getDecoders() const
{
    return m_pipelineManager->getDecoders();
}

const std::vector<std::shared_ptr<NvLLOverlay>>& CommonVideoSource::getOverlays() const
{
    return m_pipelineManager->getOverlays();
}

bool CommonVideoSource::isCompositePipeline() const
{
    return m_pipelineManager->isCompositePipeline();
}
