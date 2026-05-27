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
#include "logger.h"

using namespace std;

struct LatencyStats
{
     LatencyStats() : m_totalFrames(0)
                    , m_totalLatency(0)
                    , m_minLatency(std::numeric_limits<int64_t>::max())
                    , m_maxLatency(0)
    {
    }
    ~LatencyStats()
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

class CodecStats : public LatencyStats
{
    public:
        CodecStats() {}
        ~CodecStats() {}
        void startProcessing();
        void finishProcessing();
        void clearQueue();
        void printTotalStats();
        void printFrameStats();
        void setLatency(int64_t latency);
        void setElementName(std::string name);
        std::string getElementName();
        void pushStartTime(int64_t startTime);

    private:
        std::queue<int64_t> qTimestamp;
        std::mutex   m_queueLock;
        std::condition_variable  m_qTsCond;
        std::string elementName;
};

class Stats
{
    public:
        struct StreamStats
        {
            Json::Value m_transcodeStats;
            std::time_t m_lastAccessTime;
            double      m_avgFps{0};
            int         m_width{0};
            int         m_height{0};
        };

        static Stats& getInstance()
        {
            static Stats m_instance;
            return m_instance;
        }

        bool isDataStale(std::time_t accessTime)
        {
            std::time_t now = std::time(nullptr);
            return (now - accessTime >=  (10)); // data older than 10 seconds is stale.
        }

        std::map<std::string, StreamStats, std::less<>> getPeerStatsMap ()
        {
            return m_peerStatsMap;
        }

        void setPeerStatsMap (std::pair<std::string, StreamStats> peerStatsPair)
        {
            LOG(verbose) << "Added / Updated entry for peer id = " << peerStatsPair.first << std::endl;
            std::lock_guard<std::mutex> peerStatsLock(m_peerStatsMapMutex);
            m_peerStatsMap[peerStatsPair.first] = peerStatsPair.second;
        }

        void erasePeerStatsMapEntry (std::string peerId)
        {
            LOG(verbose) << "Removing entry for peer id = " << peerId  << std::endl;
            std::lock_guard<std::mutex> peerStatsLock(m_peerStatsMapMutex);
            m_peerStatsMap.erase(peerId);
        }

        void setPeerStatsMapFps (std::string peerId, double fps)
        {
            std::lock_guard<std::mutex> peerStatsLock(m_peerStatsMapMutex);
            std::map<std::string, Stats::StreamStats, std::less<>>::iterator peer_stats_maps_it = m_peerStatsMap.find(peerId);
            if (peer_stats_maps_it != m_peerStatsMap.end())
            {
                peer_stats_maps_it->second.m_avgFps = fps;
                peer_stats_maps_it->second.m_lastAccessTime = std::time(nullptr);
            }
            else
            {
                // Create new entry if peer doesn't exist
                StreamStats newStats;
                newStats.m_avgFps = fps;
                newStats.m_lastAccessTime = std::time(nullptr);
                newStats.m_width = 0;
                newStats.m_height = 0;
                m_peerStatsMap[peerId] = newStats;
                LOG(verbose) << "Created new stats entry for peer: " << peerId << " with FPS: " << fps << std::endl;
            }
        }

        void setPeerStatsMapResolution (std::string peerId, int width, int height)
        {
            std::lock_guard<std::mutex> peerStatsLock(m_peerStatsMapMutex);
            std::map<std::string, Stats::StreamStats, std::less<>>::iterator peer_stats_maps_it = m_peerStatsMap.find(peerId);
            if (peer_stats_maps_it != m_peerStatsMap.end())
            {
                peer_stats_maps_it->second.m_width = width;
                peer_stats_maps_it->second.m_height = height;
            }
        }

        void addQueueEntry (std::pair<uint64_t, uint64_t> timeStampPair)
        {
            LOG(verbose) << "Adding Queue entry" << std::endl;
            std::lock_guard<std::mutex> peerStatsLock(m_timeStampQueueMutex);

            /* limiting the size of queue to last 10 requests only */
            if (m_timeStampRequests.size() == 10)
            {
                m_timeStampRequests.pop();
            }
            m_timeStampRequests.push(timeStampPair);
        }

        std::queue<std::pair <uint64_t, uint64_t>> getQueue ()
        {
            return m_timeStampRequests;
        }

    private:
        /* Here will be the instance stored. */
        std::map<std::string, StreamStats, std::less<>>               m_peerStatsMap;
        std::queue<std::pair< uint64_t, uint64_t> >      m_timeStampRequests;
        std::mutex                                       m_peerStatsMapMutex;
        std::mutex                                       m_timeStampQueueMutex;

        /* Private constructor to prevent instancing. */
        Stats () {}
        ~Stats () {}
};