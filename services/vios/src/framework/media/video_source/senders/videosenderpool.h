/*
 * SPDX-FileCopyrightText: Copyright (c) 2021 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include <atomic>
#include <mutex>

#include "libasync++/async++.h"
#include "logger.h"
#include "videowebRTCsender.h"

typedef std::map<string, shared_ptr<VideoWebRTCSender>, std::less<>> video_sender_map;

class VideoSenderPool
{
    public:
        static VideoSenderPool* getInstance()
        {
            static VideoSenderPool instance;
            return &instance;
        }
        ~VideoSenderPool() {}

        video_sender_map getVideoSenderPool()
        {
            std::lock_guard<std::mutex> guard(m_poolLock);
            return m_videoSenderPool;
        }

        void addStream(const string& url)
        {
            std::lock_guard<std::mutex> guard(m_poolLock);
            LOG(info) << "Adding stream: " << url << endl;
            video_sender_map::iterator it = m_videoSenderPool.find(url);
            if (it == m_videoSenderPool.end())
            {
                shared_ptr<VideoWebRTCSender> dec(new VideoWebRTCSender(url, url));
                m_videoSenderPool[url] = dec;
            }
        }

        void removeStreams()
        {
            std::lock_guard<std::mutex> guard(m_poolLock);
            for(const auto &it : m_videoSenderPool)
            {
                shared_ptr<VideoWebRTCSender> sender = it.second;
                if (sender)
                {
                    LOG(info) << "Deleting Video Sender instance: " << it.first << endl;
                    sender.reset();
                }
            }
            m_videoSenderPool.clear();
	    }

        shared_ptr<VideoWebRTCSender> getVideoSender(const string& url)
        {
            std::lock_guard<std::mutex> guard(m_poolLock);
            video_sender_map::iterator it = m_videoSenderPool.find(url);
            if (it != m_videoSenderPool.end())
            {
                return m_videoSenderPool[url];
            }
            else
            {
                return nullptr;
            }
        }

        void setVideoSender(shared_ptr<VideoWebRTCSender>& dec, const string& url)
        {
            std::lock_guard<std::mutex> guard(m_poolLock);
            m_videoSenderPool[url] = dec;
        }

    private:
        video_sender_map m_videoSenderPool;
        std::mutex m_poolLock;
};
