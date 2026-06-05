/*
 * SPDX-FileCopyrightText: Copyright (c) 2022 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include <memory>
#include <string.h>
#include <vector>
#include <mutex>
#include <queue>
#include <atomic>
#include <condition_variable>
#include <thread>
#include <chrono>
#include "media/base/video_broadcaster.h"

#include "media_consumer.h"
#include "logger.h"
#include "stream_monitor.h"
#include "fps_display.h"
#include "webrtcstreamproducer.h"

#include "webrtc_headers/src/common_video/include/video_frame_buffer.h"
#include "webrtc_headers/src/api/video/video_frame_buffer.h"

//#define DUMP_BITSTREAM
using namespace std;

struct VideoSink
{
    VideoSink(): m_broadcaster(nullptr)  {}
    rtc::VideoBroadcaster* m_broadcaster = nullptr;
    string m_state = "NOT_PLAYING";
};

class VideoWebRTCSender : public IMediaDataConsumer
{
    public:
        VideoWebRTCSender (const std::string& consumer_name, const std::string& uri);
        VideoWebRTCSender (const std::string& consumer_name, double frame_rate, bool enable_frame_sync = false);
        VideoWebRTCSender (const std::string& consumer_name);
        ~VideoWebRTCSender ()
        {
            try {
                m_isShuttingDown = true;
                {
                    std::lock_guard<std::mutex> lock(m_videoSinkLock);
                    for (auto& pair : m_videoSinkList) {
                        if (pair.second) {
                            pair.second->m_broadcaster = nullptr;
                            pair.second->m_state = "STOPPED";
                        }
                    }
                    m_videoSinkList.clear();
                }
                m_earlyFrameCv.notify_all();
                LOG(info) << "VideoWebRTCSender instance is deleted " << endl;
            } catch (const std::exception& e) {
                try { LOG(error) << "Exception in ~VideoWebRTCSender: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
            } catch (...) {
                try { LOG(error) << "Unknown exception in ~VideoWebRTCSender" << endl; } catch (...) { (void)std::current_exception(); }
            }
        }

        void unRefDataStructure(void *ptr);
        void getwebRTCFeedback(int* qp, int* bitrate, double* frame_rate);
        int  createPassThroughMode(string& device_id);
        void appendWebrtcBroacaster(const std::string& peerid, rtc::VideoBroadcaster* broadcaster);
        void removeWebrtcBroacaster(const std::string& peerid);
        virtual void onFrame(FrameParams& params);
        void checkEarlyFramesAndSynchronize();

        void resume(const std::string& peerid);
        void pause(const std::string& peerid);
        string getPlaybackState(const std::string& peerid);
        bool isShuttingDown() const { return m_isShuttingDown.load(); }

    private:

#ifdef DUMP_BITSTREAM
        void dump_input_stream(const unsigned char *buffer, ssize_t size);
#endif

#ifdef ENABLE_FRAMEID_SUPPORT_IN_WEBRTC
    private:
        std::vector<uint8_t> getSeiFrame(std::vector<uint8_t>& content, FrameParams& params);
        std::atomic<int64_t>            m_frameId{0};
#endif
    private:
        std::string             m_uri;
        std::mutex              m_videoSinkLock;
        std::mutex              m_encParamsLock;
        int                     m_qp = 0;
        int                     m_targetBps = 0;
        double                  m_fps = 0;
        std::unique_ptr<FPSDisplay> m_fpsDisplay = nullptr;

        std::map<std::string, std::shared_ptr<VideoSink>> m_videoSinkList;
        bool                            m_enableFrameSync = false;
        double                          m_frameRate = 30.0;
        int64_t                         m_idealFrameSendInterval = 0;
        int64_t                         m_prevFrameTimestamp = 0;
        std::mutex                      m_earlyFrameMutex;
        std::condition_variable         m_earlyFrameCv;
        std::atomic<bool>               m_isShuttingDown{false};

#ifdef DUMP_BITSTREAM
        int                     m_frameCount = 0;
        ofstream                m_dumpFile;
#endif
        std::string             m_deviceId;
};
