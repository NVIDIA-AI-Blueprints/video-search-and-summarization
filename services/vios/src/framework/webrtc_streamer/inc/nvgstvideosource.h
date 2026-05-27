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

#pragma once

#include "media/video_source/core/CommonVideoSource.h"
#include "api/video/video_source_interface.h"
#include "media/base/video_broadcaster.h"
#include "error_code.h"
#include "stats.h"

/**
 * @brief WebRTC-specific video source implementation
 * 
 * This class implements the WebRTC VideoSourceInterface and manages WebRTC-specific
 * functionality like video broadcasting to sinks. It delegates pipeline management
 * to CommonVideoSource to maintain proper separation of concerns.
 */
class NvGstVideoSource : public rtc::VideoSourceInterface<webrtc::VideoFrame>
{
public:
    NvGstVideoSource(const std::string &uri, const std::map<std::string, std::string, std::less<>> &opts);
    virtual ~NvGstVideoSource();

    // WebRTC VideoSourceInterface implementation
    void AddOrUpdateSink(rtc::VideoSinkInterface<webrtc::VideoFrame> *sink, const rtc::VideoSinkWants &wants) override;
    void RemoveSink(rtc::VideoSinkInterface<webrtc::VideoFrame> *sink) override;
    void RequestRefreshFrame() override;

    // WebRTC-specific methods
    void OnSinkWantsChanged(const rtc::VideoSinkWants& wants);
    
    // Pipeline management delegation methods
    void getDecodeStats(LatencyStats& stats)
    {
        m_commonVideoSource.getDecodeStats(stats);
    }

    VmsErrorCode controlStreamFileVideoSource(const std::string& action, const std::string& seek_value)
    {
        return m_commonVideoSource.controlStreamFileVideoSource(action, seek_value);
    }

    void startStream()
    {
        m_commonVideoSource.startStream();
    }

    void switchStreamVideoSource(std::string url, const std::map<std::string, std::string, std::less<>> &opts)
    {
        m_commonVideoSource.switchStreamVideoSource(url, opts);
    }

    void streamSettingVideoSource(const std::unordered_map<std::string, std::string> &opts)
    {
        m_commonVideoSource.streamSettingVideoSource(opts);
    }

    gint64 getPositionFileVideoSource()
    {
        return m_commonVideoSource.getPositionFileVideoSource();
    }
    
    uint64_t getLastTS()
    {
        return m_commonVideoSource.getLastTS();
    }

    int64_t getFileStartTime()
    {
        return m_commonVideoSource.getFileStartTime();
    }

    uint32_t getDurationStream()
    {
        return m_commonVideoSource.getDurationStream();
    }

    string getSensorName()
    {
        return m_commonVideoSource.getSensorName();
    }
    
    string getSensorId()
    {
        return m_commonVideoSource.getSensorId();
    }
    
    virtual string getStreamState()
    {
        return m_commonVideoSource.getStreamState();
    }
    
    virtual bool isStreamError()
    {
        return m_commonVideoSource.isStreamError();
    }

    virtual Json::Value getOverlayStatus()
    {
        return m_commonVideoSource.getOverlayStatus();
    }

    std::string getBuffer()
    {
        return m_commonVideoSource.getBuffer();
    }

    // Pipeline component accessors for WebRTC integration
    std::shared_ptr<GstNvVideoDecoder> getDecoder() const { return m_commonVideoSource.getDecoder(); }
    std::shared_ptr<NvEncoderVideoConsumer> getEncoder() const { return m_commonVideoSource.getEncoder(); }
    std::shared_ptr<WebrtcSinkConsumer> getWebrtcConsumer() const { return m_commonVideoSource.getWebrtcConsumer(); }
    std::shared_ptr<NvLLOverlay> getOverlay() const { return m_commonVideoSource.getOverlay(); }
    std::shared_ptr<NvLLTransform> getTransform() const { return m_commonVideoSource.getTransform(); }
    std::shared_ptr<NvLLTransform> getTransformSink() const { return m_commonVideoSource.getTransformSink(); }
    std::shared_ptr<ImageEnc> getImageEncoder() const { return m_commonVideoSource.getImageEncoder(); }
    std::shared_ptr<NvCompositor> getCompositor() const { return m_commonVideoSource.getCompositor(); }
    std::shared_ptr<VideoWebRTCSender> getVideoSender() const { return m_commonVideoSource.getVideoSender(); }
    std::shared_ptr<NativeStreamProducer> getNativeStreamProducer() const { return m_commonVideoSource.getNativeStreamProducer(); }
#ifdef JETSON_PLATFORM
    std::shared_ptr<NvIPCProducer> getIPCProducer() const { return m_commonVideoSource.getIPCProducer(); }
#endif

    // For composite pipelines
    const std::vector<std::shared_ptr<GstNvVideoDecoder>>& getDecoders() const { return m_commonVideoSource.getDecoders(); }
    const std::vector<std::shared_ptr<NvLLOverlay>>& getOverlays() const { return m_commonVideoSource.getOverlays(); }
    bool isCompositePipeline() const { return m_commonVideoSource.isCompositePipeline(); }

private:
    // Setup WebRTC integration with pipeline components
    void setupWebRTCIntegration();
    
    CommonVideoSource m_commonVideoSource;
    rtc::VideoBroadcaster m_broadcaster;
};
