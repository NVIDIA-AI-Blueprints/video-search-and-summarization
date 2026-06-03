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

#include "logger.h"
#include <gst/gstmessage.h>
#include "config.h"
#include <iomanip>
#include "event_loop.h"
#include "media_consumer.h"
#include "udpclient.h"

typedef struct _GstElement GstElement;
typedef struct _GstBus GstBus;
typedef struct _GstClock GstClock;

namespace nv_vms
{
    class GstUDPAudioClient : public UdpClient
    {
        public:
            GstUDPAudioClient (const string&  id, UdpStream& stream)
                      : UdpClient(id, stream)
                      , m_pipeline(nullptr)
                      , m_source(nullptr)
                      , m_rtpJitterBuffer(nullptr)
                      , m_rtpdepay(nullptr)
                      , m_filter(nullptr)
                      , m_encoder(nullptr)
                      , m_conv(nullptr)
                      , m_queue(nullptr)
                      , m_sink(nullptr)
                      , m_bus(nullptr)
                      , m_busWatchId(0)
                      , m_freq (8000)
                      , m_eventLoop("udp_audio_event_loop", process_eventloop_message)
                      , m_is_error(false)
                      {
                          LOG(info) << "GstUDPAudioClient::GstUDPAudioClient port:" << id << endl;
                      }
            ~GstUDPAudioClient () { LOG(info) << "~GstUDPAudioClient" << endl; }

            // UdpClient interfaces
            int create (int freq);
            void destroy (bool expect_result);
            void start ();

            void pause ();
            void resume ();
            bool isCreated() { return m_pipeline != nullptr; }
            int create_internal();
            int create_playbin();
            void play_internal();
            bool pause_internal();
            void resume_internal();
            void destroy_internal();
            static void process_eventloop_message(std::shared_ptr<EventLoopData> data, void* parent);
            friend gboolean busWatchFunc (GstBus *bus, GstMessage *message, gpointer data);
            GstFlowReturn processNewSampleFromSink(GstElement * appsink);

        private:
            GstElement*             m_pipeline = nullptr;
            GstElement*             m_source = nullptr;
            GstElement*             m_rtpJitterBuffer = nullptr;
            GstElement*             m_rtpdepay = nullptr;
            GstElement*             m_filter = nullptr;
            GstElement*             m_encoder = nullptr;
            GstElement*             m_conv = nullptr;
            GstElement*             m_queue = nullptr;
            GstElement*             m_sink = nullptr;
            GstBus*                 m_bus = nullptr;
            guint                   m_busWatchId;
            int                     m_freq;
        public:
            EventLoop               m_eventLoop;
            bool                    m_is_error;
    };
}
