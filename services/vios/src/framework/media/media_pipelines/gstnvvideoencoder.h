/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include "stream_monitor.h"
#include "gstnvdecoder.h"

#include <string.h>
#include <vector>
#include <mutex>
#include <queue>
#include <condition_variable>
#include <atomic>
#include <glib.h>
#include <gst/gst.h>
#include <condition_variable>
#include "modules/video_coding/codecs/nvidia/NvVideoFrameBuffer.h"

typedef struct _GstElement GstElement;
typedef struct _GstBus GstBus;

class GstNvVideoEncoder
{
    public:
        GstNvVideoEncoder (const string& device_name, const string& peer_id);
        ~GstNvVideoEncoder ();

        /* GstNvDecoder Interfaces */
        int create();
        void destroy(bool expect_result = false);
        void reset_pipeline();

        void addConsumer    (shared_ptr<IMediaDataConsumer> consumer);
        void removeConsumer (shared_ptr<IMediaDataConsumer> consumer);
        GstFlowReturn processNewSampleFromSink(GstElement * appsink);
        int onFrame(FrameParams& frame_params, const string& codec, int fps);
        std::queue<vector<uint8_t>> getSpsPpsHeaders() { return m_spsPpsFrames; }
        bool checkIfSpsPpsHeadersAvailable() { return !m_spsPpsFrames.empty(); }
        void setPassThrough(bool pass_through)  { m_passThrough = pass_through; }
        void intraRefreshEncoder();
        void freeVideoFrameData(int fd);

        std::vector<shared_ptr<IMediaDataConsumer>> m_consumersList;

    private:
        void sendToConsumer(FrameParams& frame_params, string codec);
        void saveSpsPps(const unsigned char *buffer, ssize_t size);

    private:
        GstElement*             m_pipeline = nullptr;
        GstElement*             m_source = nullptr;
        GstElement*             m_conv = nullptr;
        GstElement*             m_scale = nullptr;
        GstElement*             m_videoParseOrCapsFilter = nullptr;
        GstElement*             m_capsfilterA = nullptr;
        GstElement*             m_encoder = nullptr;
        GstElement*             m_parser = nullptr;
        GstElement*             m_sink = nullptr;
        std::mutex              m_pipelineLock;
        std::mutex              m_consumerLock;
        int                     m_width;
        int                     m_height;
        guint                   m_busWatchId;
        std::queue<vector<uint8_t>>    m_spsPpsFrames;
        bool                    m_passThrough = false;
        string                  m_deviceName;
        string                  m_peerId;
#ifdef ENABLE_FRAMEID_SUPPORT_IN_WEBRTC
        int64_t                 m_currentFrameId = -1;
        int64_t                 m_prevFrameId = -1;
        int64_t                 m_ptsFromServer = 0;
        std::ofstream           m_csvFile;
        std::mutex              m_csvfileMutex;
        struct timeval          m_prevDumpTime {0, 0};
        std::vector<int>        m_latencyVector;
        std::mutex                                      m_videoFrameDataMapLock;
        std::map<int, rtc::scoped_refptr<NvVideoFrameBuffer>> m_videoFrameDataMap;

public:
        std::atomic<bool>       m_resetInProgress{false};
        std::atomic<bool>       m_isFatalError{false};
#endif
};