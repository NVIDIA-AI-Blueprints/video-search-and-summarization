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
#include <gst/gstmessage.h>
#include "config.h"
#include <iomanip>
#include "event_loop.h"
#include "udpclient.h"
#include "fps_display.h"
#include <Scheduler.h>
#include "gstnvdecodebin.h"

typedef struct _GstElement GstElement;
typedef struct _GstBus GstBus;
typedef struct _GstClock GstClock;

namespace nv_vms
{
    class GstUDPVideoClient : public UdpClient, public DecoderBase
    {
        public:
            GstUDPVideoClient (const string&  id, UdpStream& stream);
            ~GstUDPVideoClient ();

            // UdpClient interfaces
            int create ();
            int create_audio();
            void destroy (bool expect_result);
            void start ();

            void pause ();
            void resume ();
            void reset();
            bool isCreated() { return m_pipeline != nullptr; }
            int create_internal();
            int create_audio_pipeline ();
            int create_playbin();
            void play_internal();
            bool pause_internal();
            void resume_internal();
            void destroy_internal();
            void reset_pipeline_internal ();
            static void process_eventloop_message(std::shared_ptr<EventLoopData> data, void* parent);
            friend gboolean busWatchFunc (GstBus *bus, GstMessage *message, gpointer data);
            GstFlowReturn processNewSampleFromSink(GstElement * appsink);
            GstFlowReturn processNewAudioSampleFromSink(GstElement * appsink);
            void setSourceFrameSize(uint32_t w, uint32_t h);
            void fpsDisplay(unsigned long pts);
            void checkVideoDataFlowStatus();
            void displayFPS(unsigned long pts, string peerId_streamId) { m_fpsDisplay->displayFPS(pts, peerId_streamId); }
            EventLoop *getEventLoop() { return &m_eventLoop; }

        public:
            GstElement*             m_pipeline = nullptr;
            GstElement*             m_source = nullptr;
            GstElement*             m_rtpjitterbuffer = nullptr;
            GstElement*             m_queue = nullptr;
            GstElement*             m_identityVideo = nullptr;
            GstElement*             m_rtpdepay = nullptr;
            GstElement*             m_parserBeforeMux = nullptr;
            GstElement*             m_queueVideo = nullptr;
            GstElement*             m_filter = nullptr;
            GstElement*             m_queueVideoAfterDeMux = nullptr;
            GstElement*             m_queueVideoAfterDecode = nullptr;
            GstElement*             m_convVideo = nullptr;
            std::unique_ptr<NvDecodeBin> m_nvDecodeBin = nullptr;
            GstElement*             m_videoConverter = nullptr;
            GstElement*             m_convCapsFilter = nullptr;
            GstElement*             m_sink = nullptr;

            GstElement*             m_mux = nullptr;
            GstElement*             m_queueAfterMux = nullptr;
            GstElement*             m_demux = nullptr;
            GstElement*             m_mixerAudio = nullptr;
            GstElement*             m_sourceAudio = nullptr;
            GstElement*             m_rtpJitterBufferAudio = nullptr;
            GstElement*             m_rtpdepayAudio = nullptr;
            GstElement*             m_filterAudio = nullptr;
            GstElement*             m_encoderAudio = nullptr;
            GstElement*             m_convAudio = nullptr;
            GstElement*             m_queueAudioAfterDeMux = nullptr;
            GstElement*             m_capsAudioAfterDeMux = nullptr;
            GstElement*             m_decodebinAudio = nullptr;
            GstElement*             m_queueAudio = nullptr;
            GstElement*             m_queueAudioAfterDecode = nullptr;
            GstElement*             m_identityAudio = nullptr;
            GstElement*             m_sinkAudio = nullptr;
            GstBus*                 m_bus = nullptr;
            guint                   m_bus_watch_id;

            std::atomic<int>        m_sourceWidth{1800};
            std::atomic<int>        m_sourceHeight{900};
            std::atomic<bool>       m_videoDataReceived{false};
            std::atomic<bool>       m_videoDataFlowing{false};
            std::atomic<bool>       m_videoFirstFrameOut{false};
            struct timeval          m_videoTime {0, 0};
            std::unique_ptr<Bosma::Scheduler>   m_videoDataWatchDog;

        public:
            EventLoop               m_eventLoop;
            bool                    m_is_error;
            unsigned                m_udpsrcVideoFrameCount;
            unsigned                m_udpsrcVideoProbeCount;
            unsigned                m_udpsrcAudioProbeCount;
            unsigned                m_videoDecoderProbeCount;
            unsigned                m_audioDecoderProbeCount;
            FILE*                   m_dumpFile;
        private:
            std::unique_ptr<FPSDisplay> m_fpsDisplay = nullptr;
    };
}
