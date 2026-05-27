/*
 * SPDX-FileCopyrightText: Copyright (c) 2022-2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include "logger.h"
#include <list>
#include "videowebRTCsender.h"
#include "nvbufwrapper.h"
#include "nvsurfacepool.h"
#include "media_consumer.h"
#include <gst/gstsample.h>



typedef std::map  <int, int> FD_Index_Map;
typedef std::map  <int, std::pair <int, std::shared_ptr<RawFrameParams>>> FD_Index_Sample_Map;

class EncoderQueue;
class NvEncoderVideoConsumer : public IMediaDataConsumer
{
    public:
        NvEncoderVideoConsumer (const std::string& consumer_name);
        NvEncoderVideoConsumer (const std::string& consumer_name, double frame_rate, std::string peerId_streamId, bool enable_frame_sync = false);
        ~NvEncoderVideoConsumer ();
        void stopEncoder();
        void onFrame(std::shared_ptr<RawFrameParams> frame_data) override;
        void onLastFrame()override;
        void reset() override;
        void setConsumer(std::shared_ptr<IMediaDataConsumer> consumer);
        int64_t getFirstTs() { return m_firstTS; }

    private:
        FD_Index_Pair resetEncIfRequiredAndGetFreeBuffer(int width, int height, bool do_reset = false);
        void destroyBuffers ();
        int createEncoder(int width, int height, string codecString);
        void setRates (unsigned int bitrate);
        void deInitEncoder();
        void destroyEncoderAndBuffers ();
        void encoderProcessThread();
        bool isEncoderResolutionChanged (int new_width, int new_height);
        void setEncoderResolution (int new_width, int new_height);
        void waitForEncoderReleaseInternal ();
        void resetEncoderInternal();

        std::atomic<bool>                    m_encoderInitDone {false};
        int                                  m_width {0};
        int                                  m_height {0};
        std::map<int, std::pair<int, std::shared_ptr<RawFrameParams>>> m_fdGstSampleMap; // <fd, <index, sample>>
        std::mutex                           m_fdGstSampleMapLock;
        shared_ptr<EncoderQueue>             m_queue = nullptr;
        shared_ptr<NvSurfacePool>            m_surfacePool = nullptr;

    private:
        std::mutex                           m_videoSinkLock;
        shared_ptr<NvVideoEncoder>           m_nvEncoder = nullptr;
        shared_ptr<VideoWebRTCSender>        m_videowebRTCSender = nullptr;
        std::thread                          m_encoderProcessThread;
        std::atomic<bool>                    m_terminatePullLoop{false};
        uint64_t                             m_sentFramesCount = 0;
        double                               m_frameRate = 30;
        std::mutex                           m_lastBufferMonitorMutex;
        std::condition_variable              m_lastBufferMonitorCv;
        std::mutex                           m_encoderReleaseMutex;
        std::condition_variable              m_encoderReleaseCv;
        std::atomic<bool>                    m_encoderReleaseNotified{false};
        std::atomic<bool>                    m_encoderTransformed{false};
        std::string                          m_peerIdStreamId{""};
        int                                  m_prevBitRate {0};
        std::shared_ptr<IMediaDataConsumer>  m_consumer = nullptr;
        std::atomic<int64_t>                 m_firstTS {0};
};

class EncoderQueue
{
public:
    EncoderQueue(NvEncoderVideoConsumer* consumer);
    ~EncoderQueue();

    void push(std::shared_ptr<RawFrameParams> frame_data);
    std::shared_ptr<RawFrameParams> pull();
    void clear();
    bool empty();
    void cleanupQueue();
    int size() { return m_queue.size(); };

private:
    NvEncoderVideoConsumer*         m_encoder;
    std::queue<std::shared_ptr<RawFrameParams>>    m_queue;
    std::mutex                      m_queueLock;
    std::condition_variable         m_condVar;
    atomic<bool>                    m_flowData {false};
};