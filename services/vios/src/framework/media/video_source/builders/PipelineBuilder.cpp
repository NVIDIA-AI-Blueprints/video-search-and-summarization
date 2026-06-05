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

#include "PipelineBuilder.h"
#include "../decoders/gstnvvideodecoder.h"
#include "../encoders/nvvideoencoder.h"
#include "../../overlays/ll_overlay.h"
#include "../processors/transforms/ll_transform.h"
#include "../senders/webrtc_sink_consumer.h"
#include "../encoders/image_encoder.h"
#include "../producers/webrtcstreamproducer.h"
#include "logger.h"
#include "utils.h"
#include "media_producer.h"
#include "stream_monitor.h"
#include "s3stream_producer.h"
#include "clip_reader_producer.h"
#include <thread>
#include <chrono>

// Implementation of PipelineBuilder utility methods

std::shared_ptr<IMediaDataProducer> PipelineBuilder::createSourceProducer(
    const std::string& url, const PipelineConfiguration& config, std::shared_ptr<GstNvVideoDecoder> decoder)
{
    std::shared_ptr<IMediaDataProducer> producer = nullptr;
    
    // Determine the type of source producer based on URL
    if (url.find("webrtc://") == 0 || url.find("webrtc/") != std::string::npos)
    {
        // WebRTC stream producer - use custom deleter to avoid deleting singleton
        producer = std::shared_ptr<IMediaDataProducer>(
            WebrtcStreamProducer::getInstance(),
            [](IMediaDataProducer*) { /* Do nothing - don't delete singleton */ }
        );
        LOG(info) << "Created WebrtcStreamProducer for URL: " << url << endl;
    }
    else if (url.find("rtsp://") == 0 || url.find("rtp://") == 0)
    {
        // RTSP/RTP stream producer - use custom deleter to avoid deleting singleton
        producer = std::shared_ptr<IMediaDataProducer>(
            StreamMonitor::getInstance(),
            [](IMediaDataProducer*) { /* Do nothing - don't delete singleton */ }
        );
        LOG(info) << "Created StreamMonitor producer for URL: " << url << endl;
    }
    else
    {
        // For file based sources, either local or cloud stream
        if (config.isCloudStream())
        {
            // Create a new CloudStreamProducer instance
            auto cloudProducer = std::make_shared<CloudStreamProducer>();
            const auto& activeFileList = decoder->getActiveFileList();
            bool syncMode = config.isImageCapture() ? false : true;
            std::string endTime = config.getEndTime();
            if (config.isImageCapture())
            {
                // Add 3 frames offset for endTime
                double frameRate = stringToDouble(config.getQuality().frameRate, DEFAULT_FRAME_RATE);
                if (frameRate <= 0.0)
                {
                    LOG(warning) << "Invalid frameRate value (" << frameRate << ") for image capture, using default: " << DEFAULT_FRAME_RATE << endl;
                    frameRate = DEFAULT_FRAME_RATE;
                }
                int64_t imageCaptureStartTime = getEpocTimeInMS(config.getStartTime());
                int64_t imageCaptureEndTime = imageCaptureStartTime + ((1000.0 / frameRate) * 30);
                endTime = convertEpocToISO8601_2(imageCaptureEndTime*1000);
            }
            cloudProducer->setConfig(activeFileList, config.getStartTime(),
                            endTime, config.getCodec(),
                            config.getContainer(), syncMode, false);
            producer = cloudProducer;  // Assign to interface pointer after configuration
            LOG(info) << "Created CloudStreamProducer for URL: " << url << endl;
        }
        else if (config.isImageCapture() && url.find("file://") == 0)
        {
            // For local file image capture, use ClipReaderProducer
            // This provides proper seeking support and retry mechanisms
            // which giosrc with is-growing=true does not support
            const auto& activeFileList = decoder->getActiveFileList();
            if (!activeFileList.empty())
            {
                ClipReaderConfig clipConfig;
                clipConfig.is_image_capture = true;
                clipConfig.stream_id = config.getPeerId();
                clipConfig.video_codec = config.getCodec();
                clipConfig.enable_audio = false;  // No audio for image capture
                // B-frame streams: bypass giosrc for image capture to avoid typefind/decode issues with growing files
                clipConfig.bypass_giosrc_for_growing_file = decoder->hasBframes();
                clipConfig.has_bframes = decoder->hasBframes();
                
                // Configure B-frame parameters for image capture
                if (clipConfig.has_bframes)
                {
                    clipConfig.estimated_framerate = 30.0;
                    clipConfig.reorder_depth = MAX_REF_FRAMES;
                }

                // Convert file list to path strings
                for (const auto& fileInfo : activeFileList)
                {
                    clipConfig.file_paths.push_back(fileInfo.m_filePath);
                }

                // Calculate seek times relative to file start
                int64_t fileStartTimeMs = activeFileList[0].m_startTime;
                int64_t fileDurationMs = activeFileList[0].m_duration;
                int64_t epochStartTime = getEpocTimeInMS(config.getStartTime());
                double frameRate = stringToDouble(config.getQuality().frameRate, DEFAULT_FRAME_RATE);
                if (frameRate <= 0.0)
                {
                    LOG(warning) << "Invalid frameRate value (" << frameRate << ") for image capture clip, using default: " << DEFAULT_FRAME_RATE << endl;
                    frameRate = DEFAULT_FRAME_RATE;
                }

                // Store file start epoch time (to convert relative PTS to absolute)
                clipConfig.file_start_epoch_ms = fileStartTimeMs;

                // Seek start: offset from file start to requested timestamp
                clipConfig.seek_start_ms = epochStartTime - fileStartTimeMs;
                if (clipConfig.seek_start_ms < 0) clipConfig.seek_start_ms = 0;

                // Seek end: add a few frames margin for image capture
                int64_t frameDurationMs = static_cast<int64_t>(1000.0 / frameRate);
                clipConfig.seek_end_ms = clipConfig.seek_start_ms + (frameDurationMs * 60);  // 60 frames margin
                if (fileDurationMs != 1 && clipConfig.seek_end_ms >= fileDurationMs)
                {
                    clipConfig.seek_end_ms = std::numeric_limits<int64_t>::max();
                }

                bool is_file_sensor = (config.getSensorType() == SENSOR_TYPE_FILE);

                // Check if last file is completed/finalized, in that case, we don't need to use giosrc
                bool is_growing_file = true;
                if (!is_file_sensor)
                {
                    bool is_completed_file = activeFileList.back().m_fileSize > 0;
                    int64_t last_file_durationMs = activeFileList.back().m_duration;
                    uint64_t last_file_fps = activeFileList.back().m_fileFPS;
                    if (last_file_fps == 0)
                    {
                        last_file_fps = static_cast<uint64_t>(DEFAULT_VIDEO_FRAME_RATE);
                    }
                    int64_t max_gap_between_files_ms = static_cast<int64_t>(1000 / last_file_fps) - 1;
                    if (is_completed_file && clipConfig.seek_end_ms <= (last_file_durationMs + max_gap_between_files_ms))
                    {
                        LOG(info) << "Last file is completed/finalized, not using giosrc, last_file_durationMs: " << last_file_durationMs << endl;
                        is_growing_file = false;
                    }
                }

                auto now = std::chrono::system_clock::now();
                int64_t nowMs = std::chrono::duration_cast<std::chrono::milliseconds>(
                    now.time_since_epoch()).count();
                int64_t diffMs = nowMs - epochStartTime;
                // Within 2 seconds of current time means we're reading near the live edge
                clipConfig.is_growing_file = is_growing_file && !is_file_sensor && (diffMs <= 2000);
                LOG(info) << "ClipReaderProducer: is_growing_file: " << clipConfig.is_growing_file << ", diffMs: " << diffMs << "ms" << endl;

                LOG(info) << "Creating ClipReaderProducer for local file image capture:"
                          << " fileStart=" << fileStartTimeMs << "ms"
                          << ", epochStart=" << epochStartTime << "ms"
                          << ", seekStart=" << clipConfig.seek_start_ms << "ms"
                          << ", seekEnd=" << clipConfig.seek_end_ms << "ms"
                          << ", fileDuration=" << fileDurationMs << "ms"
                          << ", codec=" << clipConfig.video_codec << endl;

                auto clipProducer = std::make_shared<ClipReaderProducer>(clipConfig);
                producer = clipProducer;
                LOG(info) << "Created ClipReaderProducer for local file image capture: " << url << endl;
            }
            else
            {
                LOG(warning) << "No active file list for local file image capture" << endl;
                return nullptr;
            }
        }
        else
        {
            // For local file-based, no separate producer needed
            LOG(info) << "No separate producer needed for URL: " << url << endl;
            return nullptr;
        }
    }

    // Start the producer if created
    if (producer && !producer->start())
    {
        LOG(error) << "Failed to start source producer for URL: " << url << endl;
        throw std::runtime_error("Failed to start source producer");
    }

    return producer;
}

void PipelineBuilder::setupDecoderWithProducer(std::shared_ptr<GstNvVideoDecoder> decoder,
    const std::string& url, const PipelineConfiguration& config)
{
    if (!decoder) {
        LOG(error) << "Decoder is null, cannot set up producer" << endl;
        return;
    }

    // Create and inject the producer into the decoder
    std::shared_ptr<IMediaDataProducer> producer = createSourceProducer(url, config, decoder);
    
    if (producer)
    {
        decoder->setProducer(producer);
        LOG(info) << "Injected source producer into decoder for URL: " << url << endl;
    }
    else
    {
        LOG(info) << "No producer injection needed for URL: " << url << endl;
    }
}

// Common component setup methods
void PipelineBuilder::createCommonComponents(const PipelineConfiguration& config)
{
    LOG(info) << "Creating Consumer objects for peer: " << config.getPeerId() << endl;
    
    double frame_rate = stringToDouble(config.getQuality().frameRate, 30.0);
    std::string peerIdStreamId = config.getPeerIdStreamId();
    peerIdStreamId = config.isLivePlayback() ? peerIdStreamId + "_live" : peerIdStreamId + "_recorded";
    LOG(info) << "PeerIdStreamId: " << peerIdStreamId << endl;
    
    // Create encoder (if not image capture)
    if (!m_encoder && !config.isImageCapture())
    {
        string consumer_name = "video_encoder_" + config.getPeerId();
        m_encoder = std::make_shared<NvEncoderVideoConsumer>(consumer_name, frame_rate, peerIdStreamId, config.getCompositor().enabled);
    }
    
    // Create overlay (if OSD is available)
    if (!m_overlay && !GET_OSD_INSTANCE()->isError())
    {
        // Modify sensor name with tag, if provided
        std::map<std::string, std::string, std::less<>> overlayOpts = config.getOptions();
        if (overlayOpts.find("sensorID") != overlayOpts.end() && overlayOpts.find("tag") != overlayOpts.end())
        {
            std::string originalSensorID = overlayOpts.at("sensorID");
            std::string tag = overlayOpts.at("tag");
            overlayOpts.erase("sensorID");
            overlayOpts.insert(std::make_pair("sensorID", originalSensorID + "-" + tag));
        }
        string consumer_name = "overlay_" + config.getPeerId();
        m_overlay = std::make_shared<NvLLOverlay>(consumer_name, config.getUri(), overlayOpts);
    }
    
    // Create transforms
    if (!m_transform)
    {
        string consumer_name = "transform_" + config.getPeerId();
        m_transform = std::make_shared<NvLLTransform>(consumer_name);
    }
    if (!m_transformSink)
    {
        string consumer_name = "transform_sink_" + config.getPeerId();
        m_transformSink = std::make_shared<NvLLTransform>(consumer_name);
    }
    
    // Create WebRTC consumer (if not image capture)
    if (!m_webrtcConsumer && !config.isImageCapture())
    {
        string consumer_name = "webrtc_sink_" + config.getPeerId();
        m_webrtcConsumer = std::make_shared<WebrtcSinkConsumer>(consumer_name, peerIdStreamId, frame_rate, config.getOptions(), config.getCompositor().enabled);
    }
    
    // Create image encoder (if image capture)
    if (!m_imageEncoder && config.isImageCapture())
    {
        string consumer_name = "image_encoder_" + config.getPeerId();
        m_imageEncoder = std::make_shared<ImageEnc>(consumer_name, config.getOptions());
    }
}

void PipelineBuilder::setupOverlay(const PipelineConfiguration& config)
{
    // This would be implemented based on overlay setup logic
    // For now, this is a placeholder
}

void PipelineBuilder::setupTransform(const PipelineConfiguration& config)
{
    // This would be implemented based on transform setup logic
    // For now, this is a placeholder
}

void PipelineBuilder::setupEncoder(const PipelineConfiguration& config)
{
    // This would be implemented based on encoder setup logic
    // For now, this is a placeholder
}

void PipelineBuilder::setupWebrtcConsumer(const PipelineConfiguration& config)
{
    // This would be implemented based on WebRTC consumer setup logic
    // For now, this is a placeholder
}

void PipelineBuilder::setupImageEncoder(const PipelineConfiguration& config)
{
    // This would be implemented based on image encoder setup logic
    // For now, this is a placeholder
}

void PipelineBuilder::clearDecoderProducer(std::shared_ptr<GstNvVideoDecoder> decoder, const PipelineConfiguration& config)
{
    if (decoder) {
        std::shared_ptr<IMediaDataProducer> producer = decoder->getProducer();
        bool isReplayPicture = config.isImageCapture() && config.getUri().find("file://") == 0;
        if ((config.isCloudStream() || isReplayPicture) && producer)
        {
            producer->stop();
        }
        decoder->setProducer(nullptr);
        LOG(info) << "Cleared producer from decoder" << endl;
    }
}

void PipelineBuilder::destroyCommonComponents()
{
    LOG(info) << "Destroying common pipeline components" << endl;
    
    // Stop overlay before destroying it to prevent thread issues
    if (m_overlay)
    {
        m_overlay->stopOverlay();
        LOG(info) << "Stopped overlay before destruction" << endl;
              
        m_overlay.reset();
        LOG(info) << "Reset overlay" << endl;
    }
    
    // Stop transforms before destroying them to prevent thread issues
    if (m_transform)
    {
        m_transform->stopTransform();
        LOG(info) << "Stopped transform before destruction" << endl;
        
        
        m_transform.reset();
        LOG(info) << "Reset transform" << endl;
    }
    
    if (m_transformSink)
    {
        m_transformSink->stopTransform();
        LOG(info) << "Stopped transformSink before destruction" << endl;
        
        m_transformSink.reset();
        LOG(info) << "Reset transformSink" << endl;
    }
    
    // Phase 1: Stop encoder and wait for complete shutdown using proper synchronization
    if (m_encoder)
    {
        LOG(info) << "Stopping encoder and waiting for thread to complete" << endl;
        
        // stopEncoder() internally calls waitForEncoderReleaseInternal() which uses condition variables
        // This ensures the encoder thread is completely finished before returning
        m_encoder->stopEncoder();
        
        LOG(info) << "Encoder thread confirmed stopped - safe to destroy consumers" << endl;
    }
    
    // Phase 2: Now safe to reset WebRTC consumer (VideoWebRTCSender)
    // The encoder thread is guaranteed to be stopped, so no more onFrame() calls
    if (m_webrtcConsumer)
    {
        LOG(info) << "Destroying WebRTC consumer - no more encoder callbacks possible" << endl;
        m_webrtcConsumer.reset();
        LOG(info) << "WebRTC consumer destroyed safely" << endl;
    }
    
    // Phase 3: Reset encoder after consumers are safely destroyed
    if (m_encoder)
    {
        LOG(info) << "Resetting encoder after safe consumer cleanup" << endl;
        m_encoder.reset();
        LOG(info) << "Encoder reset completed" << endl;
    }
    
    // Phase 4: Handle image encoder
    if (m_imageEncoder)
    {
        LOG(info) << "Resetting image encoder" << endl;
        m_imageEncoder.reset();
        LOG(info) << "Image encoder reset" << endl;
    }
    
    LOG(info) << "Common pipeline components destroyed" << endl;
}
