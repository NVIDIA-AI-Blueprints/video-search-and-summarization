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

#include <glib.h>
#include <gst/gst.h>
#include <string.h>
#include "decoder_base.h"

typedef struct _GstElement GstElement;

class NvDecodeBin
{
    public:
        NvDecodeBin();
        NvDecodeBin(DecoderBase* parent, const string codec);
        ~NvDecodeBin();

        GstElement*       create               (bool is_image_capture = false);
        void              updateDecoderElement (gdouble playback_speed);
        GstPadProbeReturn padProbeCB           (GstPad * pad, GstPadProbeInfo * info);
        int waitForAllPadsCreation();
        void setResolution(int width, int height);

    public:
        int m_decoder_out_probe_count = 0;
        int m_decoder_in_probe_count = 0;
        bool m_imageCapture = false;
        int64_t m_imageEpochTime{0};
        std::atomic<bool> m_monitoFramesInProbe{false};
        bool                    m_padsCreated = false;
        std::condition_variable m_padsCreatedCv;
        std::mutex              m_padsCreatedLock;
        int                     m_decoderSrcWidth = 0;
        int                     m_decoderSrcHeight = 0;
        DecoderBase*            m_parent = nullptr;

    private:
        GstElement*             m_decodeBin       = nullptr;
        GstElement*             m_decoder         = nullptr;
        GstElement*             m_queue           = nullptr;
        std::string             m_codec           = "h264";
        gdouble                 m_playBackSpeed   = 1;
        bool                    m_useNvV4l2Dec    = true;
};