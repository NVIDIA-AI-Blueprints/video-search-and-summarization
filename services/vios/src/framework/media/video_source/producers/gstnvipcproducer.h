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
#include "media_consumer.h"
#include "videosinkinfo.h"

typedef struct _GstElement GstElement;
typedef struct _GstBus GstBus;

typedef enum
{
    INVALID,
    EMPTY,
    READY,
    PAUSED,
    PLAYING
} State;

static std::map<State, GstState> gstStateMap =
{
    { INVALID, GST_STATE_VOID_PENDING },
    { EMPTY  , GST_STATE_NULL         },
    { READY  , GST_STATE_READY        },
    { PAUSED , GST_STATE_PAUSED       },
    { PLAYING, GST_STATE_PLAYING      }
};

class NvIPCProducer
{
    public:
        NvIPCProducer (const string& stream_id);
        ~NvIPCProducer ();

        int create();
        void destroy();
        void setOptions(const std::map<std::string, std::string, std::less<>> &opts);
        bool getError() { return m_error; }
        GstFlowReturn processNewSampleFromSink(GstElement * appsink);
        void setSourceFrameSize(uint32_t w, uint32_t h);
        std::string getstate();
        void getGstState();
        bool setGstState(State state);

        void setQuality(const std::string&, const std::string& quality);
        void removeConsumer(const std::string&);
        std::map<std::string, std::shared_ptr<VideoSinkInfo>> getWebrtcBroacasterList() { return m_videoSinkList; }
        void setConsumer(const string& peerid, std::shared_ptr<IMediaDataConsumer> consumer);
        void setConsumerReady(const string& peerid, bool isReady = true);
        int getVideoSinkListSize() { return m_videoSinkList.size(); }
        FrameSize handleDRC(const string& peerid, int targetPixels, int targetFPS);
        bool isSinkPresent ();

        std::atomic<bool>       m_error{false};
        std::atomic<GstState>   m_state{GST_STATE_NULL};

    private:
        bool isDRCAllowed ();
        FrameSize qualityToFrameSize(const string& quality);

        GstElement*             m_pipeline = nullptr;
        GstElement*             m_source = nullptr;
        GstElement*             m_sink = nullptr;
        std::mutex              m_pipelineLock;
        int                     m_width;
        int                     m_height;
        guint                   m_busWatchId;
        string                  m_streamID;
        string                  m_peerId;
        std::atomic<bool>       m_stop{false};
        std::atomic<int>        m_sourceWidth {960};
        std::atomic<int>        m_sourceHeight{544};
        std::mutex              m_videoSinkLock;
        std::string             m_peerid;
        std::time_t             m_lastDRCTime {0};
        std::size_t             m_resolutionIndex = 0;

        std::shared_ptr<IMediaDataConsumer> m_consumer    = nullptr;
        std::map<std::string, std::shared_ptr<VideoSinkInfo>> m_videoSinkList;
};