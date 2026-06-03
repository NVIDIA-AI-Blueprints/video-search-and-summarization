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

#include <string>
#include <map>
#include <vector>

inline constexpr int MAX_COMPOSITOR_LIMIT = 12;
inline constexpr int MIN_COMPOSITOR_LIMIT = 2;

/**
 * @brief Configuration class for video pipeline settings
 * 
 * This class encapsulates all configuration parameters needed for pipeline creation,
 * providing a clean interface for pipeline builders.
 */
class PipelineConfiguration
{
public:
    struct CompositorConfig {
        bool enabled = false;
        bool showSensorName = false;
        int overlaySensorPosX = 0;
        int overlaySensorPosY = 0;
        std::vector<std::string> urls;
        std::string layoutJson = "";
    };

    struct QualityConfig {
        std::string quality = "auto";
        std::string frameRate = "30.0";
        bool passThrough = false;
        bool hlsPlayback = false;
    };

    struct OverlayConfig {
        bool enabled = false;
        std::string sensorId;
        std::string tag;
        bool bboxEnabled = false;
    };

    PipelineConfiguration();
    PipelineConfiguration(const std::string& uri, const std::map<std::string, std::string, std::less<>>& opts);

    // Getters
    const std::string& getUri() const { return m_uri; }
    const std::string& getPeerId() const { return m_peerId; }
    const std::string& getPeerIdStreamId() const { return m_peerIdStreamId; }
    const QualityConfig& getQuality() const { return m_quality; }
    const CompositorConfig& getCompositor() const { return m_compositor; }
    const OverlayConfig& getOverlay() const { return m_overlay; }
    
    bool isRecordedPlayback() const { return m_recordedPlayback; }
    bool isLivePlayback() const { return !m_recordedPlayback; }
    bool isNativeStream() const { return m_isNativeStream; }
    bool isCloudStream() const { return m_isCloudStream; }
    bool isImageCapture() const { return m_imageCapture; }
    bool isGodsEyeView() const { return m_godsEyeView; }
    bool isHlsPlayback() const { return m_quality.hlsPlayback; }
    bool isPassThrough() const { return m_quality.passThrough; }
    
    const std::map<std::string, std::string, std::less<>>& getOptions() const { return m_options; }
    std::string getDeviceId() const;
    std::string getCodec() const;
    std::string getSensorType() const;
    std::string getContainer() const;
    std::string getStartTime() const;
    std::string getEndTime() const;

private:
    void parseOptions(const std::map<std::string, std::string, std::less<>>& opts);
    void validateConfiguration() const;
    void setupCompositorConfig(const std::map<std::string, std::string, std::less<>>& opts);
    void setupQualityConfig(const std::map<std::string, std::string, std::less<>>& opts);
    void setupOverlayConfig(const std::map<std::string, std::string, std::less<>>& opts);

    std::string m_uri;
    std::string m_peerId;
    std::string m_peerIdStreamId;
    bool m_recordedPlayback = false;
    bool m_isNativeStream = false;
    bool m_isCloudStream = false;
    bool m_imageCapture = false;
    bool m_godsEyeView = false;
    
    QualityConfig m_quality;
    CompositorConfig m_compositor;
    OverlayConfig m_overlay;
    std::map<std::string, std::string, std::less<>> m_options;
};
