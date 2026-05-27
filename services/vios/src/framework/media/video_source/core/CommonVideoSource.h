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

#pragma once

#include <mutex>
#include <condition_variable>
#include <chrono>
#include <atomic>
#include <vector>
#include <memory>
#include <unordered_map>

// Core video source headers
#include "PipelineConfiguration.h"

// Forward declaration to avoid incomplete type issues with shared_ptr
class PipelineManager;

// Component headers
#include "../decoders/gstnvvideodecoder.h"
#include "../encoders/nvvideoencoder.h"
#include "../../overlays/ll_overlay.h"
#include "../processors/transforms/ll_transform.h"
#include "../processors/compositors/nvcompositor.h"
#include "../senders/webrtc_sink_consumer.h"
#include "../senders/videowebRTCsender.h"
#include "../encoders/image_encoder.h"
#include "../producers/native_stream_monitor.h"
#include "../producers/nativestreamproducer.h"

// Framework headers
#include "logger.h"
#include "libasync++/async++.h"
#include "garbagecollector.h"
#include "nvbufwrapper.h"
#include "media_producer.h"
#include "stream_monitor.h"

#ifdef JETSON_PLATFORM
#include "../producers/gstnvipcproducer.h"
#include "../producers/ipcproducerpool.h"
#endif

// Forward declarations
class LatencyStats;
#include "error_code.h"
#include "stats.h"
using nv_vms::VmsErrorCode;

/**
 * @brief CommonVideoSource - Video pipeline management and control
 * 
 * This class provides a high-level interface for video pipeline management,
 * delegating most operations to the PipelineManager while maintaining 
 * backward compatibility with legacy code.
 * 
 * @note New code should prefer using PipelineManager directly when possible.
 */
class CommonVideoSource
{
public:
    /**
     * @brief Get decoder statistics for the specified peer
     * @param stats Reference to LatencyStats structure to populate
     */
    void getDecodeStats(LatencyStats& stats);

    // Static performance tracking for image capture API
    static CodecStats& getImageCaptureApiStats() {
        static CodecStats s_apiStats;
        static bool initialized = false;
        if (!initialized) {
            s_apiStats.setElementName("getCameraPicture_API");
            initialized = true;
        }
        return s_apiStats;
    }

    CommonVideoSource(const std::string &uri, const std::map<std::string, std::string, std::less<>> &opts);
    virtual ~CommonVideoSource(); // Custom destructor for pimpl-like pattern

    // Public interface methods
    VmsErrorCode controlStreamFileVideoSource(const std::string& action, const std::string& seek_value);
    void switchStreamVideoSource(std::string url, const std::map<std::string, std::string, std::less<>> &opts);
    void streamSettingVideoSource(const std::unordered_map<std::string, std::string> &opts);
    void startStream();
    void setDecoderConsumerPipeline();
    void createConsumerPipeline();
    void setConsumerReady();
    void stopAndRemoveConsumers();
    
    // Status and information methods
    gint64 getPositionFileVideoSource();
    uint64_t getLastTS();
    int64_t getFileStartTime();
    uint32_t getDurationStream();
    int64_t getFirstTs();
    std::string getSensorName();
    std::string getSensorId();
    virtual std::string getStreamState();
    virtual bool isStreamError();
    virtual Json::Value getOverlayStatus();
    std::string getBuffer();
    
    // Configuration and integration methods
    void setBitstreamConsumer(std::shared_ptr<IMediaDataConsumer> bitstreamConsumer);

    // Legacy methods for backward compatibility - prefer PipelineManager methods
    void resetConsumerAndDestroyDecoderIfRequired();

#ifdef UNIT_TEST
    std::shared_ptr<GstNvVideoDecoder> getDecoder();
#endif

    // Public member for backward compatibility
    bool m_recordedPlayback;
    std::shared_ptr<GstNvVideoDecoder> m_gstdecoder = nullptr;

    // Pipeline management accessors for WebRTC integration
    std::shared_ptr<GstNvVideoDecoder> getDecoder() const;
    std::shared_ptr<NvEncoderVideoConsumer> getEncoder() const;
    std::shared_ptr<WebrtcSinkConsumer> getWebrtcConsumer() const;
    std::shared_ptr<NvLLOverlay> getOverlay() const;
    std::shared_ptr<NvLLTransform> getTransform() const;
    std::shared_ptr<NvLLTransform> getTransformSink() const;
    std::shared_ptr<ImageEnc> getImageEncoder() const;
    std::shared_ptr<NvCompositor> getCompositor() const;
    std::shared_ptr<VideoWebRTCSender> getVideoSender() const;
    std::shared_ptr<NativeStreamProducer> getNativeStreamProducer() const;
#ifdef JETSON_PLATFORM
    std::shared_ptr<NvIPCProducer> getIPCProducer() const;
#endif

    // For composite pipelines
    const std::vector<std::shared_ptr<GstNvVideoDecoder>>& getDecoders() const;
    const std::vector<std::shared_ptr<NvLLOverlay>>& getOverlays() const;
    bool isCompositePipeline() const;

    // WebRTC integration accessors
    bool isPassThrough() const { return m_passThrough; }
    const std::string& getPeerId() const { return m_peerid; }
    const std::string& getPeerIdStreamId() const { return m_peerIdStreamId; }

private:
    void resetState();
    // IMediaDataProducer integration (now handled by PipelineManager)
    std::shared_ptr<IMediaDataProducer> m_sourceProducer = nullptr;

    // Pipeline management - using shared_ptr to avoid incomplete type issues
    std::shared_ptr<PipelineManager> m_pipelineManager;
    PipelineConfiguration m_config;
    
    // Minimal legacy members for critical backward compatibility only
    // Note: Most functionality should be accessed through pipeline manager
    std::vector<std::shared_ptr<GstNvVideoDecoder>> m_gstdecoderList; // Required for composite playback legacy methods
    std::vector<std::shared_ptr<NvLLOverlay>> m_nvLLOverlayList; // Required for composite overlay legacy methods
    // Core configuration - kept for legacy compatibility
    std::string m_uri;
    std::string m_peerid;
    std::string m_peerIdStreamId;
    std::vector<std::string> m_urlsList; // Required for composite operations
    std::string m_quality; // Needed for legacy quality operations
    
    // Configuration state derived from pipeline config
    bool m_passThrough = false;
    bool m_livePlayback = false;
    bool m_compositePlayback = false;
    bool m_compositeShowSensorName = false;
    bool m_isNativeStream = false;
    
    // Legacy component references for methods that haven't been fully refactored
    std::shared_ptr<VideoWebRTCSender> m_videowebRTCSender = nullptr; // For pass-through mode
    std::shared_ptr<NativeStreamProducer> m_nativeStreamProducer = nullptr; // For native streams
#ifdef JETSON_PLATFORM
    std::shared_ptr<NvIPCProducer> m_nvIPCProducer = nullptr; // For IPC streams
#endif

    // Note: setupDecoder method removed - use PipelineManager directly
};
