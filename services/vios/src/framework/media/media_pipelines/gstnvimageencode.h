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
#include <mutex>
#include <condition_variable>
#include "logger.h"
#include <gst/gst.h>
#include <gst/app/gstappsrc.h>
#include <gst/app/gstappsink.h>


typedef struct _GstElement GstElement;
typedef struct _GstBus GstBus;

class NvImageEncode
{
    public:
        NvImageEncode();
        ~NvImageEncode();

        int create(int width, int height);
        void destroy();
        void onFrame(const unsigned char *buffer, ssize_t size);
        std::string getImageBuffer();
        void setCaps(int width, int height);
        void setSourceCaps(int width, int height);
        GstFlowReturn processNewSampleFromSink(GstElement * appsink);

    private:
        GstElement*             m_pipeline = nullptr;
        guint                   m_bus_watch_id = G_MAXUINT;
        GstElement*             m_source = nullptr;
        GstElement*             m_filtersrc = nullptr;
        GstElement*             m_filter = nullptr;
        GstElement*             m_scale = nullptr;
        GstElement*             m_image_encoder = nullptr;
        GstElement*             m_sink = nullptr;
        std::condition_variable m_imgBufferWait;
        std::mutex              m_imgBufferLock;
        std::string             m_imgBuffer;
        int                     m_width = 0;
        int                     m_height = 0;
};
