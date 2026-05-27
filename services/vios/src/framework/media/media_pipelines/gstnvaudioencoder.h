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
#include "logger.h"
#include "stream_monitor.h"
#include "gstnvdecoder.h"
#include "stats.h"
#include "mm_utils.h"

#include <string.h>
#include <vector>
#include <mutex>
#include <queue>
#include <condition_variable>
#include <atomic>
#include <glib.h>
#include <gst/gst.h>
#include <condition_variable>


typedef struct _GstElement GstElement;
typedef struct _GstBus GstBus;

class GstNvAudioEncoder
{
    public:
        GstNvAudioEncoder () :
        m_pipeline (nullptr),
        m_source (nullptr),
        m_parser (nullptr),
        m_convert (nullptr),
        m_resampler (nullptr),
        m_filter (nullptr),
        m_encoder (nullptr),
        m_filterAfterEnc (nullptr),
        m_aacparse (nullptr),
        m_sink (nullptr)
        {

        }
        ~GstNvAudioEncoder ()
        {
            LOG(info) << "Audio Decoder instance deleted" << endl;
        }

        /* GstNvDecoder Interfaces */
        int create(string peerId);
        void destroy(bool expect_result = false);

        void addConsumer    (shared_ptr<IMediaDataConsumer> consumer);
        void removeConsumer (shared_ptr<IMediaDataConsumer> consumer);
        std::map<string, media_info, std::less<>> getAudioInfo ();
        void onFrame(const string& media, const string& codec, const unsigned char *buffer, ssize_t size,
                     int sample_rate, size_t num_channels);

        GstFlowReturn processNewSampleFromSink(GstElement * appsink);
        std::vector<shared_ptr<IMediaDataConsumer>> m_consumersList;

    private:
        GstElement*             m_pipeline = nullptr;
        GstElement*             m_source = nullptr;
        GstElement*             m_parser = nullptr;
        GstElement*             m_convert = nullptr;
        GstElement*             m_resampler = nullptr;
        GstElement*             m_filter = nullptr;
        GstElement*             m_encoder = nullptr;
        GstElement*             m_filterAfterEnc = nullptr;
        GstElement*             m_aacparse = nullptr;
        GstElement*             m_sink = nullptr;
        std::mutex              m_pipelineLock;
        std::mutex              m_consumerLock;
        std::string             m_codec {""};
        int                     m_channels = 2;
        int                     m_frequency = 16000;
};