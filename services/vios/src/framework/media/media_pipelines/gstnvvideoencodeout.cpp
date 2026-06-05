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

#include "gstnvvideoencodeout.h"

#include <gst/gst.h>
#include <iostream>
#include <string>

NvVideoEncodeOut::NvVideoEncodeOut() :
                m_tee(nullptr)
                , m_queue_record(nullptr)
                , m_queue_display(nullptr)
                , m_encoder(nullptr)
                , m_parser2(nullptr)
                , m_muxer(nullptr)
                , m_filesink(nullptr)
{
}

NvVideoEncodeOut::~NvVideoEncodeOut()
{
}

GstElement* NvVideoEncodeOut::create(const string& file_name)
{
    LOG (info) << "Creating Gstreamer video encode out pipeline" << endl;
    GstElement*  encout_bin = nullptr;
    GstPad *sink_pad = nullptr, *source_pad = nullptr, *ghost_sourcepad = nullptr, *ghost_sinkpad = nullptr;
    encout_bin = gst_bin_new ("nvvideoencodeout");

    m_tee = gst_element_factory_make("tee", "tee");
    m_queue_display = gst_element_factory_make("queue", nullptr);
    m_queue_record = gst_element_factory_make("queue", nullptr);
    m_encoder = gst_element_factory_make("nvv4l2h264enc", nullptr);
    m_parser2    = gst_element_factory_make ("h264parse", nullptr);
    m_muxer = gst_element_factory_make("matroskamux", nullptr);
    m_filesink = gst_element_factory_make("filesink", nullptr);

    if (!encout_bin || !m_tee || !m_queue_display || !m_queue_record || ! m_encoder || !m_parser2 || !m_muxer || !m_filesink)
    {
        LOG (error) << "Gstreamer element creation failed" << endl;
        return nullptr;
    }
    gst_bin_add_many (GST_BIN (encout_bin), m_tee, m_queue_display, m_queue_record, m_encoder, m_parser2, m_muxer, m_filesink, nullptr);
    if (!gst_element_link_many (m_tee, m_queue_record, m_encoder, m_parser2, m_muxer, m_filesink, nullptr))
    {
        LOG (error) << "Tee element could not be linked" << endl;
        return nullptr;
    }
    if (!gst_element_link_many (m_tee, m_queue_display, nullptr))
    {
        LOG (error) << "Tee element could not be linked" << endl;
        return nullptr;
    }
    string fname(file_name);
    if (fname.empty())
    {
        std::string now = getCurrentUtcTime();
        if (now.empty())
        {
            now = "file";
        }
        fname = now + std::string(".mp4");
    }
    g_object_set(m_filesink, "location", fname.c_str(), nullptr);
    g_object_set(m_encoder, "tune", 4, nullptr);
    g_object_set (G_OBJECT (m_encoder), "gpu-id"   , g_gpuIndex, nullptr);

    source_pad = gst_element_get_static_pad (m_queue_display, "src");
    if (source_pad)
    {
        ghost_sourcepad = gst_ghost_pad_new ("src", source_pad);
        gst_pad_set_active (ghost_sourcepad, TRUE);
        gst_element_add_pad (encout_bin, ghost_sourcepad);
        gst_object_unref (source_pad);
    }
    else
    {
        LOG(error) << "Failed to get src pad from nvvideoconvert" << endl;
        return nullptr;
    }

    sink_pad = gst_element_get_static_pad (m_tee, "sink");
    if (sink_pad)
    {
        ghost_sinkpad = gst_ghost_pad_new ("sink", sink_pad);
        gst_pad_set_active (ghost_sinkpad, TRUE);
        gst_element_add_pad (encout_bin, ghost_sinkpad);
        gst_object_unref (sink_pad);
    }
    else
    {
        LOG(error) << "Failed to get sink pad from nvstreammux" << endl;
        return nullptr;
    }

    return encout_bin;
}
