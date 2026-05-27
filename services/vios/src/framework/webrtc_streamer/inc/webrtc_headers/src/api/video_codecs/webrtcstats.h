/*
 * SPDX-FileCopyrightText: Copyright (c) 2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice, this
 * list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 * this list of conditions and the following disclaimer in the documentation
 * and/or other materials provided with the distribution.
 *
 * 3. Neither the name of the copyright holder nor the names of its
 * contributors may be used to endorse or promote products derived from
 * this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
 * AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 * DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
 * FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
 * SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
 * CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
 * OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

#pragma once

#include <vector>
#include <queue>
#include <mutex>
#include <jsoncpp/json/json.h>
#include <condition_variable>
#include <chrono>

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
        void clearQueue();
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
