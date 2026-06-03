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

#pragma once

#include <iostream>
#include <queue>
#include <mutex>
#include <string>
#include <vector>
#include <condition_variable>
#include "media_consumer.h"
#include "nvsurfacepool.h"
#include "nvbufsurface.h"
#include "video_resolution.h"

using namespace std;

class NvLLTransform : public IMediaDataConsumer
{
public:
    NvLLTransform(const std::string& consumer_name);
    ~NvLLTransform();

    void doTransformTask();
    void onFrame(std::shared_ptr<RawFrameParams> frame_data) override;
    void setConsumer(std::shared_ptr<IMediaDataConsumer> consumer);
    void stopTransform();
    std::shared_ptr<IMediaDataConsumer> getConsumer() { return m_consumer; }
    void setOriginalFrameSize(int w, int h) override;
    void setOriginalFrameSize() override;
    void setIPCMeta () override;
    /* Update start time for overlay */
    void updateStartTime(string start_time) override;
    void reset() override;
    void onLastFrame() override;

private:
    std::shared_ptr<IMediaDataConsumer>            m_consumer    = nullptr;

    std::thread                                    m_transformThread;
    std::atomic<bool>                              m_stop {false};
    shared_ptr<NvSurfacePool>                      m_surfacePool = nullptr;

    /* Data structure related to Queue */
    std::queue<std::shared_ptr<RawFrameParams>> m_queue;
    std::mutex                                     m_queueLock;
    std::condition_variable                        m_condVar;
    atomic<bool>                                   m_flowData {false};
    int                                            m_width = WIDTH_1080p;
    int                                            m_height = HEIGHT_1080p;
    int                                            m_sourceWidth = WIDTH_1080p;
    int                                            m_sourceHeight = HEIGHT_1080p;
    NvBufSurface                                   *m_swSurf = nullptr;
    bool                                           m_isIPCMeta = false;
};
