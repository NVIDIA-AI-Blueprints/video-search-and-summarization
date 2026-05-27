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

#include <string.h>
#include <iostream>
#include <string.h>
#include <algorithm>
#include <glib/gstdio.h>
#include <chrono>
#include <errno.h>
#include <stdexcept>
#include <gst/app/gstappsrc.h>
#include <gst/app/gstappsink.h>
#include "gstnvaudioencoder.h"
#include "NvMediaSource.hh"

using namespace std;
using namespace nv_vms;

/* called when the appsink notifies us that there is a new buffer ready for
 * processing */
static GstFlowReturn
on_new_sample_from_sink (GstElement * appsink, GstNvAudioEncoder* nvAudioEncoder)
{
    if (nvAudioEncoder)
    {
        return nvAudioEncoder->processNewSampleFromSink(appsink);
    }
    return GST_FLOW_ERROR;
}


GstFlowReturn GstNvAudioEncoder::processNewSampleFromSink (GstElement * appsink)
{
    GstSample *sample = nullptr;
    GstBuffer *gstBuffer = nullptr;
    GstMapInfo map;

    /* Get the sample from appsink */
    sample = gst_app_sink_pull_sample (GST_APP_SINK (appsink));
    if (sample == nullptr)
    {
        if (gst_app_sink_is_eos((GstAppSink *)appsink))
        {
            LOG (info) << "EOS Received on app sink element" << endl;
            return GST_FLOW_EOS;
        }
        else
        {
            LOG (warning) << "Received NULL sample in Audio Decoder Pipeline" << endl;
            return GST_FLOW_ERROR;
        }
    }

    /* Get the buffer from sample */
    gstBuffer = gst_sample_get_buffer (sample);
    if (gstBuffer == nullptr)
    {
        LOG (warning) << "No more buffers available from app sink element" << endl;
        gst_sample_unref (sample);
        return GST_FLOW_ERROR;
    }

    /* Map the gst buffer */
    if (gst_buffer_map (gstBuffer, &map, GST_MAP_READ) == false)
    {
        LOG (warning) << "Map the gst buffer Failed" << endl;
        gst_sample_unref (sample);
        return GST_FLOW_ERROR;
    }

    if (map.size <= 0)
    {
        LOG(error) << "Audio Encoder: received 0 sized buffer";
        /* Unmap the gst buffer */
        gst_buffer_unmap (gstBuffer, &map);

        /* Unref the sample */
        gst_sample_unref (sample);
        return GST_FLOW_OK;
    }

    if (m_consumersList.size() > 0)
    {
        struct timeval presentationTime;
        gettimeofday(&presentationTime, nullptr);

        std::lock_guard<std::mutex> guard(m_consumerLock);
        for (shared_ptr<IMediaDataConsumer> consumer : m_consumersList)
        {
            if (consumer->getConsumerMediaType() == MediaTypeAudio || 
                consumer->getConsumerMediaType() == MediaTypeAudioVideo)
            {
                FrameParams frame_params;
                frame_params.m_media            = "audio";
                frame_params.m_codec            = "aac";
                frame_params.m_buffer           = map.data;
                frame_params.m_size             = map.size;
                frame_params.m_presentationTime = presentationTime;            
                consumer->onFrame(frame_params);
            }
        }
    }
    /* Unmap the gst buffer */
    gst_buffer_unmap (gstBuffer, &map);

    /* Unref the sample */
    gst_sample_unref (sample);

    return GST_FLOW_OK;
}

void GstNvAudioEncoder::onFrame(const string& media, const string& codec, const unsigned char *buffer, ssize_t size,
                                int sample_rate, size_t num_channels)
{
    g_object_set (G_OBJECT (m_parser), "sample-rate",  sample_rate,  nullptr);
    g_object_set (G_OBJECT (m_parser), "num-channels", num_channels, nullptr);
    if (media == "video" || m_pipeline == nullptr)
    {
        return;
    }

    GstBuffer *gstbuffer = nullptr;
    GstMapInfo map;

    /* Allocate a new Gst Buffer */
    gstbuffer = gst_buffer_new_allocate (nullptr, size, nullptr);

    /* Map the Gst Buffer to write the data */
    gst_buffer_map (gstbuffer, &map, GST_MAP_WRITE);

    /* Copy buffer to map, i.e. to gstbuffer */
    memcpy (map.data, buffer, size);

    map.size = size;

    /* Unmap the Gst Buffer */
    gst_buffer_unmap (gstbuffer, &map);
    gst_app_src_push_buffer((GstAppSrc*)m_source, gstbuffer);
    return;
}

void GstNvAudioEncoder::addConsumer (shared_ptr<IMediaDataConsumer> consumer)
{
    std::lock_guard<std::mutex> guard(m_consumerLock);
    if (std::find(m_consumersList.begin(), m_consumersList.end(), consumer) == m_consumersList.end())
    {
        LOG(warning) << "======== Audio Consumer Added ==========" << endl;
        m_consumersList.push_back(consumer);
    }
}

void GstNvAudioEncoder::removeConsumer (shared_ptr<IMediaDataConsumer> consumer)
{
    std::lock_guard<std::mutex> guard(m_consumerLock);
    LOG(warning) << "======== Audio Consumer Removed ==========" << endl;
    m_consumersList.erase(std::remove(m_consumersList.begin(), m_consumersList.end(), consumer), m_consumersList.end());
}

std::map<string, media_info, std::less<>> GstNvAudioEncoder::getAudioInfo ()
{
    std::map<string, media_info, std::less<>> supported_map;
    media_info audio_info;
    audio_info.codec     = m_codec;
    audio_info.channel   = m_channels;
    audio_info.frequency = m_frequency;
    audio_info.codecData = 1410;
    supported_map["audio"] = audio_info;
    return supported_map;
}

int GstNvAudioEncoder::create (string peerId)
{
    LOG(info) << "Creating GstNvAudioEncoder pipeline" << endl;
    GstCaps* capsSrc = nullptr;

    std::lock_guard<std::mutex> guard(m_pipelineLock);
    m_pipeline     = gst_pipeline_new ("audio_encoder_pipeline");
    m_source       = gst_element_factory_make ("appsrc",        nullptr);
    m_parser       = gst_element_factory_make ("rawaudioparse", nullptr);
    m_convert      = gst_element_factory_make ("audioconvert", nullptr);
    m_resampler    = gst_element_factory_make ("audioresample", nullptr);
    m_filter       = gst_element_factory_make ("capsfilter",    nullptr);
    m_encoder      = gst_element_factory_make ("voaacenc",      nullptr);
    m_filterAfterEnc = gst_element_factory_make ("capsfilter",    nullptr);
    m_aacparse     = gst_element_factory_make ("aacparse",      nullptr);
    m_sink         = gst_element_factory_make ("appsink",       nullptr);

    /* Check if any of element failed to create */
    if (!m_pipeline || !m_source || !m_parser || !m_convert || !m_resampler || !m_filter || !m_encoder || !m_filterAfterEnc || !m_aacparse || !m_sink)
    {
        LOG (error) << "Gstreamer Audio Encoder element creation failed" << endl;
        return -1;
    }

    gst_bin_add_many (GST_BIN (m_pipeline), m_source, m_parser, m_convert, m_resampler, m_filter, m_encoder, m_filterAfterEnc, m_aacparse, m_sink, nullptr);

    if (!gst_element_link_many (m_source, m_parser, m_resampler, m_convert, m_filter, m_encoder, m_filterAfterEnc, m_aacparse, m_sink, nullptr))
    {
        LOG (error) << "Audio Encoder: elements could not be linked" << endl;
        return -1;
    }

#ifdef DEBUG
    g_signal_connect( m_pipeline, "deep-notify", G_CALLBACK( gst_object_default_deep_notify ), nullptr );
#endif
    
    if(!g_signal_connect (m_sink, "new-sample", G_CALLBACK (on_new_sample_from_sink), (void*)this))
    {
        LOG(error) << "Audio Decoder: Error in g_signal_connect of new-sample" << endl;
    }

    std::string caps_string = "audio/x-raw, format=(string)S16LE, layout=(string)interleaved, rate=(int)16000, channels=(int)2";
    capsSrc = gst_caps_from_string (caps_string.c_str());

    g_object_set (G_OBJECT (m_parser), "sample-rate",  16000,   nullptr);
    g_object_set (G_OBJECT (m_parser), "pcm-format",   4,       nullptr); // 4 corresponds to S16LE
    g_object_set (G_OBJECT (m_parser), "num-channels", 2,       nullptr);
    g_object_set (G_OBJECT (m_sink)  , "emit-signals", TRUE, "sync", FALSE, nullptr);
    g_object_set (G_OBJECT (m_filter), "caps",         capsSrc, nullptr);
    g_object_set (G_OBJECT (m_encoder), "bitrate",     192000, nullptr);

    m_codec     = "mpeg4-generic";
    m_channels  = 2;
    m_frequency = 16000;

    caps_string = "audio/mpeg, stream-format=(string)adts, rate=(int)" + to_string(m_frequency) + 
                  ", channels=(int)" + to_string(m_channels);
    capsSrc = gst_caps_from_string (caps_string.c_str());
    g_object_set (G_OBJECT (m_filterAfterEnc), "caps",         capsSrc, nullptr);
    
    gst_caps_unref (capsSrc);

    gst_element_set_state (m_pipeline, GST_STATE_PLAYING);
    LOG(info) << "Created GstNvAudioEncoder pipeline" << endl;
    return 0;
}

void GstNvAudioEncoder::destroy(bool expect_result)
{
    LOG(info) << "Destroying Audio Encoder Pipeline" << endl;

    std::lock_guard<std::mutex> guard(m_pipelineLock);
    if (m_pipeline)
    {
        gst_element_set_state (m_pipeline, GST_STATE_NULL);
        gst_element_get_state (m_pipeline, nullptr, nullptr, 5 * GST_SECOND);
        gst_object_unref (m_pipeline);
        m_pipeline = nullptr;
    }
    /* Notify EOS to media source */
    if (m_consumersList.size() > 0)
    {
        std::lock_guard<std::mutex> guard(m_consumerLock);
        for (shared_ptr<IMediaDataConsumer> consumer : m_consumersList)
        {
            if (consumer->getConsumerMediaType() == MediaTypeAudio)
            {
                LOG(warning) << "Sending EOS for Audio" << endl;
                FrameParams frame_params;
                frame_params.m_buffer  = nullptr;
                frame_params.m_size    = 0;
                consumer->onFrame(frame_params);
            }
        }
    }
    m_consumersList.clear();
    LOG(info) << "Destroyed Audio Encoder Pipeline" << endl;
}