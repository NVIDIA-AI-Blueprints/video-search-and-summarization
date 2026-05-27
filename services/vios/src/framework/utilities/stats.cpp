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

#include "stats.h"

using namespace std;

void CodecStats::startProcessing()
{
    std::lock_guard<std::mutex> lock(m_queueLock);
    int64_t timeStampInUs = std::chrono::duration_cast<std::chrono::microseconds>
        (std::chrono::system_clock::now().time_since_epoch()).count();
    qTimestamp.push(timeStampInUs);
    m_qTsCond.notify_all();
}

void CodecStats::pushStartTime(int64_t startTime)
{
    std::lock_guard<std::mutex> lock(m_queueLock);
    qTimestamp.push(startTime);
    m_qTsCond.notify_all();
}

void CodecStats::finishProcessing()
{
    std::lock_guard<std::mutex> lock(m_queueLock);

    if (!qTimestamp.empty())
    {
        int64_t timeStampInUs =  qTimestamp.front();
        qTimestamp.pop();
        int64_t timeStampOutUs = std::chrono::duration_cast<std::chrono::microseconds>
            (std::chrono::system_clock::now().time_since_epoch()).count();
        int64_t latency = timeStampOutUs - timeStampInUs;
        setLatency(latency);
        printFrameStats();
    }
}

void CodecStats::clearQueue()
{
    std::lock_guard<std::mutex> lock(m_queueLock);
    while (!qTimestamp.empty())
    {
        qTimestamp.pop();
    }
}

void CodecStats::printTotalStats()
{
    if (m_totalFrames > 0)
    {
        LOG(info) << "\n--------- Element name = " << elementName << "------------------" <<
                    "\nTotal units processed = " << m_totalFrames <<
                    "\nAverage latency(in us) = " << (m_totalLatency / m_totalFrames) <<
                    "\nMin latency(in us) = " << m_minLatency <<
                    "\nMax latency(in us) = " << m_maxLatency <<
                    "\n-------------------------------------------------------\n";
    }
}

void CodecStats::printFrameStats()
{
    if (m_totalFrames > 0)
    {
        LOG(verbose2) << "\nTotal " << elementName << "d frames: "<< m_totalFrames <<
                        " Current average latency(in us): " << (m_totalLatency / m_totalFrames) <<
                        " Current min latency(in us): " << m_minLatency <<
                        " Current max latency(in us): " << m_maxLatency;
    }
}

void CodecStats::setLatency(int64_t latency)
{
    m_totalFrames++;
    m_totalLatency += latency;
    if (latency < m_minLatency)
    {
        m_minLatency = latency;
    }
    if (latency > m_maxLatency)
    {
        m_maxLatency = latency;
    }
}

void CodecStats::setElementName(std::string name)
{
    elementName = name;
}

std::string CodecStats::getElementName()
{
    return elementName;
}
