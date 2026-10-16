/*
 * SPDX-FileCopyrightText: Copyright (c) 2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <vector>
#include <queue>
#include <mutex>
#include <jsoncpp/json/json.h>
#include <condition_variable>
#include <chrono>
#include "rtc_base/system/rtc_export.h"

struct WebrtcLatencyStats
{
     WebrtcLatencyStats() : m_totalFrames(0)
                    , m_totalLatency(0)
                    , m_minLatency(std::numeric_limits<int64_t>::max())
                    , m_maxLatency(0)
    {
    }
    ~WebrtcLatencyStats()
    {

    }
    void clear()
    {
        m_totalFrames = 0;
        m_totalLatency = 0;
        m_minLatency = std::numeric_limits<int64_t>::max();
        m_maxLatency = 0;
    }
    int64_t                 m_totalFrames;
    int64_t                 m_totalLatency;
    int64_t                 m_minLatency;
    int64_t                 m_maxLatency;
};

class WebrtcCodecStats : public WebrtcLatencyStats
{
    public:
        WebrtcCodecStats() {}
        ~WebrtcCodecStats() {}
        void startProcessing();
        void finishProcessing();
        RTC_EXPORT void clearQueue();
        void printWebrtcTotalStats();
        void printWebrtcFrameStats();
        void setLatency(int64_t latency);
        void setElementName(std::string name);
        std::string getElementName();
        void pushStartTime(int64_t startTime);
        bool isEmpty();
        int64_t popLastTime();

    private:
        std::queue<int64_t> qTimestamp;
        std::mutex   m_queueLock;
        std::condition_variable  m_qTsCond;
        std::string elementName;
};
