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

#include "media_consumer.h"
#include "videowebRTCsender.h"
#include <atomic>
#include <mutex>
#include <queue>

class 
WebrtcSinkConsumer : public IMediaDataConsumer
{
public:
    WebrtcSinkConsumer(const std::string& consumer_name);
    WebrtcSinkConsumer(const std::string& consumer_name, string peer_id, double frame_rate, 
                    const std::map<std::string, std::string, std::less<>> &opts, bool enable_frame_sync = false);
    ~WebrtcSinkConsumer();

    void onFrame(std::shared_ptr<RawFrameParams> frame_data) override;
    void onFrame(FrameParams& params) override;
    void setWebrtcBroadcaster(void* broadcaster) override;
    void removeWebrtcBroadcaster(const std::string& peerid); // New method to remove broadcaster safely
    void getwebRTCFeedback(int* qp, int* bitrate, double* frame_rate) override;
    
    // Get the peer ID stream ID for broadcaster identification
    std::string getPeerIdStreamId() const { return m_peerIdStreamId; }

    void setBitstreamConsumer(std::shared_ptr<IMediaDataConsumer> bitstreamConsumer);

#ifdef UNIT_TEST
public:
    int getFrameCountForTest() const { return m_frameCountForTest.load(); }
    void resetFrameCountForTest() { m_frameCountForTest.store(0); }
private:
    std::atomic<int> m_frameCountForTest{0};
#endif

private:
    shared_ptr<VideoWebRTCSender>                  m_videowebRTCSender = nullptr;
    std::mutex                                     m_broadcasterMutex;
    void*                                          m_broadcaster = nullptr;
    std::string                                    m_peerIdStreamId{""};
    std::shared_ptr<IMediaDataConsumer>            m_bitstreamConsumer = nullptr;
};