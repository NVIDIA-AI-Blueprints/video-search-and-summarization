/*
 * SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <iostream>
#include <queue>
#include <mutex>
#include <string>
#include <vector>
#include <condition_variable>
#include "media_consumer.h"
#include "nvvideoencoder.h"
#include "overlay_internal.h"
#include "nvcompositor.h"
#include "MetadataStore.h"

using namespace std;

inline constexpr double DEFAULT_FRAME_RATE = 30.0;
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
inline constexpr int OUTPUT_PLANE_NUM_BUFFERS = 19;
#else
inline constexpr int OUTPUT_PLANE_NUM_BUFFERS = 20;
#endif

/*
 * Overlay is a consumer of Decoder and inherits MediaConsumer class
 * Overlay uses RedisSubscriber that depends on IMediaDataConsumer class
 */
class NvLLOverlay : public IMediaDataConsumer
{
public:
    NvLLOverlay (const std::string& consumer_name, const std::string& uri, const std::map<std::string, std::string, std::less<>> &opts);
    ~NvLLOverlay();

    void doDrawTask();
    void onFrame(std::shared_ptr<RawFrameParams> frame_data) override;
    void setConsumer(std::shared_ptr<IMediaDataConsumer> consumer);
    std::string getUri() { return m_uri; }
    void setUri(std::string uri) { m_uri = uri; }
    void stopOverlay();
    void setOptions(const std::map<std::string, std::string, std::less<>> &opts);
    bool streamSettings(const std::unordered_map<std::string, std::string> &opts);
    bool isOverlayEnabled() { return m_overlay->isOverlayEnabled(); }
    bool isBboxEnabled() { return m_overlay->isBboxEnabled(); }
    void setOverlayResolution(int w, int h) { m_overlay->m_width = w; m_overlay->m_height = h; }
    std::shared_ptr<IMediaDataConsumer> getConsumer() { return m_consumer; }
    void setOriginalFrameSize(int w, int h) override;
    void setOriginalFrameSize() override;
    void setIPCMeta () override;
    Json::Value getOverlayStatus();
    /* Update start time for overlay */
    void updateStartTime(string start_time) override;
    void reset() override;
    void onLastFrame() override;

private:
    std::shared_ptr<IMediaDataConsumer>            m_consumer    = nullptr;
    std::shared_ptr<NvLLOverlayInternal>           m_overlay = nullptr;

    std::thread                                    m_drawThread;
    std::atomic<bool>                              m_stop {false};
    shared_ptr<NvSurfacePool>                      m_surfacePool = nullptr;

    /* Data structure related to Queue */
    std::queue<std::shared_ptr<RawFrameParams>> m_queue;
    std::mutex                                     m_queueLock;
    std::condition_variable                        m_condVar;
    atomic<bool>                                   m_flowData {false};
    std::string                                    m_uri;
    std::map<std::string, std::string, std::less<>>             m_opts;
    bool                                           m_compositePlayback = false;
    bool                                           m_compositeShowSensorName = false;
    int                                            m_compositeOverlaySensorPosX = 0;
    int                                            m_compositeOverlaySensorPosY = 0;
    int                                            m_width = WIDTH_1080p;
    int                                            m_height = HEIGHT_1080p;
    int                                            m_prevWidth = WIDTH_1080p;
    int                                            m_prevHeight = HEIGHT_1080p;
    int                                            m_streamWidth = WIDTH_1080p;
    int                                            m_streamHeight = HEIGHT_1080p;
    OverlayBBoxParams                              m_overlayParams;
    std::string                                    m_sensorName;
    std::string                                    m_sensorName3d {""};
    std::string                                    m_sensorType;
    std::string                                    m_newStartTime;
    std::string                                    m_startTime;
    std::string                                    m_endTime;
    double                                         m_frameRate = DEFAULT_FRAME_RATE;
    uint64_t                                       m_firstFrameTS = 0;
    std::mutex                                     m_debugData;
    bool                                           m_isOverlay = false;
    bool                                           m_recordedPlayback = false;
    std::string                                    m_peerid;
    std::string                                    m_isoStartTime;
    std::string                                    m_isoEndTime;
    void*                                          m_broadcaster = nullptr;
    uint8_t*                                       m_cpuPtr[OUTPUT_PLANE_NUM_BUFFERS];
    bool                                           m_imageCapture = false;
    bool                                           m_isIPCMeta = false;
    std::shared_ptr<IMetadataStore>                m_metadataStore = nullptr;
};