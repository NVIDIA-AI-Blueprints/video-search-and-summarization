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
#include "gstnvaudiodecoder.h"  

using namespace std;
using namespace nv_vms;

#define CONFIRM_DEC_OUT_FRAMES 5

/* called when the appsink notifies us that there is a new buffer ready for
 * processing */
static GstFlowReturn
on_new_sample_from_sink (GstElement * appsink, GstNvAudioDecoder* nvAudioDecoder)
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
    uint16_t segment_length       = nvAudioDecoder->m_audioData.m_freq / 100;
    uint16_t total_segment_length = nvAudioDecoder->m_audioData.m_channel * segment_length;

    /* Resize the vector to accommodate new data as well existing data */
    unsigned vec_size = map.size / 2;
    nvAudioDecoder->m_audioBuffer.resize(nvAudioDecoder->m_audioBuffer.size() + vec_size);

    /* Copy the map.data into vector while keeping existing data untouched */
    std::memcpy(&nvAudioDecoder->m_audioBuffer[nvAudioDecoder->m_audioBuffer.size() - vec_size], map.data, map.size);

    if (map.size <= 0)
    {
        LOG(error) << "Audio Decoder: received 0 sized buffer";
        /* Unmap the gst buffer */
        gst_buffer_unmap (gstBuffer, &map);

        /* Unref the sample */
        gst_sample_unref (sample);
        return GST_FLOW_ERROR;
    }

    while (nvAudioDecoder->m_audioBuffer.size() > total_segment_length)
    {
        std::lock_guard<std::mutex> lock(nvAudioDecoder->m_sinkLock);
        for (auto *sink : nvAudioDecoder->m_sinks)
        {
            sink->OnData(nvAudioDecoder->m_audioBuffer.data(), nvAudioDecoder->m_audioData.m_bitsPerSample, nvAudioDecoder->m_audioData.m_freq, nvAudioDecoder->m_audioData.m_channel, segment_length);
        }
        /* Erase the processed data from vector
        ** from begin to begin + total_segment_length
        */
        nvAudioDecoder->m_audioBuffer.erase(nvAudioDecoder->m_audioBuffer.begin(), nvAudioDecoder->m_audioBuffer.begin() + total_segment_length);
    }

#ifdef UNIT_TEST
    if (nvAudioDecoder->m_decOutFrames < CONFIRM_DEC_OUT_FRAMES)
    {
        nvAudioDecoder->m_decOutFrames ++;
        if (nvAudioDecoder->m_decOutFrames == CONFIRM_DEC_OUT_FRAMES)
        {
            LOG(info) << "Sending frames to webrtc" << endl;
        }
    }
#endif

    /* Unmap the gst buffer */
    gst_buffer_unmap (gstBuffer, &map);

    /* Unref the sample */
    gst_sample_unref (sample);

    return GST_FLOW_OK;
}

static void on_pad_added (GstElement *element, GstPad *pad, void *data)
{
    GstElement *element2 = (GstElement *)data;
    GstPad *sink_pad = nullptr;
    GstCaps *caps = nullptr;
    gchar *capsString = nullptr;

    caps = gst_pad_get_current_caps (pad);
    capsString = gst_caps_to_string (caps);
    sink_pad = gst_element_get_static_pad (element2, "sink");
    LOG (info) << "Caps = " << capsString << endl;

    /* Try to link pads only if format is audio */
    if (g_strrstr(capsString, "audio"))
    {
        /* Check if sink_pad exists */
        if (!sink_pad)
        {
            LOG(error) << "Failed to get sink pad of element." << endl;
            GST_ELEMENT_ERROR(element2, STREAM, FAILED, ("Failed to get sink pad of element."), (nullptr));
            if (caps != nullptr)
            {
                gst_caps_unref (caps);
            }
            goto _exit;
        }
        /* Check if pads can be linked */
        if (gst_pad_link (pad, sink_pad) != GST_PAD_LINK_OK)
        {
            LOG(error) << "Failed to link elements in pad-added callback. = " << sink_pad << endl;
            GST_ELEMENT_ERROR(element2, STREAM, FAILED, ("Failed to link elements in pad-added callback"), (nullptr));
        }
    }

    /* Unref the data structure */
    if (sink_pad)
    {
        gst_object_unref (sink_pad);
    }
    if (caps != nullptr)
    {
        gst_caps_unref (caps);
    }
_exit:
    g_free(capsString);
}

static bool link_decoder(GstElement* decoder, GstElement* element)
{
    if (!gst_element_link_many (decoder, element, nullptr))
    {
        if (!g_signal_connect (decoder, "pad-added", G_CALLBACK (on_pad_added), element))
        {
            return false;
        }
    }
    return true;
}

bool GstNvAudioDecoder::isPlaying()
{
    bool result = false;
    if (m_decOutFrames == CONFIRM_DEC_OUT_FRAMES)
    {
        result = true;
    }
    return result;
}

std::string GstNvAudioDecoder::getstate()
{
    GstState state = GST_STATE_NULL;
    if (m_pipeline)
    {
        std::lock_guard<std::mutex> guard(m_pipelineLock);
        GstStateChangeReturn state_change;
        GstState  current, pending;
        state_change = gst_element_get_state(m_pipeline, &current, &pending, GST_SECOND);
        state = current;
        if (state_change == GST_STATE_CHANGE_FAILURE)
        {
            state = GST_STATE_NULL;
        }
    }

    string state_str = gst_element_state_get_name(state);
    if (state_str != "PLAYING" && state_str != "PAUSED")
    {
        state_str = "NOT_PLAYING";
    }
    return state_str;
}

bool GstNvAudioDecoder::pause()
{
    bool ret = true;
    GstState desired_state = GST_STATE_PAUSED;
    if (m_pipeline)
    {
        std::lock_guard<std::mutex> guard(m_pipelineLock);
        GstStateChangeReturn gstStateChangeRet = gst_element_set_state (m_pipeline, desired_state);
        if (gstStateChangeRet == GST_STATE_CHANGE_FAILURE)
        {
            LOG (error) << "gst_element_set_state failed. " << endl;
            ret = false;
        }
        else if (gstStateChangeRet == GST_STATE_CHANGE_ASYNC)
        {
            LOG (info) << "GST_STATE_CHANGE_ASYNC. " << endl;
        }
        else
        {
            gst_element_get_state (m_pipeline, nullptr, nullptr, GST_SECOND);
            LOG (info) << "State change success " << endl;
        }
    }
    return ret;
}

void GstNvAudioDecoder::appendWebrtcSink(webrtc::AudioTrackSinkInterface* broadcaster)
{
    LOG(info) << "Add webRTC Sink for uri = " << m_uri << endl;
    std::lock_guard<std::mutex> lock(m_sinkLock);
    m_sinks.push_back(broadcaster);
}

void GstNvAudioDecoder::removeWebrtcSink(webrtc::AudioTrackSinkInterface* broadcaster)
{
    std::lock_guard<std::mutex> lock(m_sinkLock);
    std::list<webrtc::AudioTrackSinkInterface*>::iterator it = std::find(m_sinks.begin(), m_sinks.end(), broadcaster);
    if(it != m_sinks.end())
    {
        LOG(info) << "Remove webRTC Audio Sink for uri = " << m_uri << endl;
        m_sinks.erase(it);
    }
}

void GstNvAudioDecoder::updateAudioDataIfRequired()
{
    if (!m_audioData.m_isAudioDataUpdated)
    {
        LOG(verbose) << "Audio Details updating now for uri = " << m_uri << endl;
        std::map<string, media_info, std::less<>> media_details = StreamMonitor::getInstance()->getSupportedSubSessions(m_uri);
        std::map<string, media_info, std::less<>>::iterator it;
        it = media_details.find("audio");
        if (it != media_details.end())
        {
            m_audioData.m_freq               = it->second.frequency;
            m_audioData.m_channel            = it->second.channel;
            m_audioData.m_isAudioDataUpdated = true;
        }
    }
}

void GstNvAudioDecoder::onFrame(FrameParams& params)
{
    LOG(verbose) << "GstNvAudioDecoder::onFrame media:"<< params.m_media << ", codec:" << params.m_codec
                 << ", size:" << params.m_size << endl;

    if (params.m_media == "video" || m_pipeline == nullptr)
    {
        return;
    }

    updateAudioDataIfRequired();

    GstBuffer *gstbuffer = nullptr;
    GstMapInfo map;

    /* Allocate a new Gst Buffer */
    gstbuffer = gst_buffer_new_allocate (nullptr, params.m_size, nullptr);

    /* Map the Gst Buffer to write the data */
    gst_buffer_map (gstbuffer, &map, GST_MAP_WRITE);

    /* Copy buffer to map, i.e. to gstbuffer */
    memcpy (map.data, params.m_buffer, params.m_size);

    map.size = params.m_size;

    /* Unmap the Gst Buffer */
    gst_buffer_unmap (gstbuffer, &map);
    gst_app_src_push_buffer((GstAppSrc*)m_source, gstbuffer);
    return;
}
int GstNvAudioDecoder::create (bool blocking)
{
    LOG(info) << "Creating GstNvAudioDecoder pipeline for uri = " << m_uri << endl;
    updateAudioDataIfRequired();
    GstCaps* capsSrc = nullptr;
    m_audioBuffer.clear();
    if (!m_udpSource)
    {
        StreamMonitor::getInstance()->registerDataCallback(m_uri, getself());
    }

    std::lock_guard<std::mutex> guard(m_pipelineLock);
    m_pipeline     = gst_pipeline_new ("audio_decoder_pipeline");
    m_source       = gst_element_factory_make ("appsrc", nullptr);
    m_decoder      = gst_element_factory_make ("decodebin", nullptr);
    m_sink         = gst_element_factory_make ("appsink", nullptr);

    /* Check if any of element failed to create */
    if (!m_pipeline || !m_source || !m_decoder || !m_sink)
    {
        LOG (error) << "Gstreamer element creation failed" << endl;
        return -1;
    }

    gst_bin_add_many (GST_BIN (m_pipeline), m_source, m_decoder, m_sink, nullptr);
    std::string caps_string = "audio/x-mulaw, format=(string)S8, rate=(int)" + to_string(m_audioData.m_freq) + 
                              ", channels=(int)" + to_string(m_audioData.m_channel);
    capsSrc = gst_caps_from_string (caps_string.c_str());

    LOG(info) << "Audio Decoder appsrc caps = " << caps_string << endl;

    /* Setting properties of elements */
    g_object_set (G_OBJECT (m_source), "caps", capsSrc, nullptr);
    g_object_set (G_OBJECT (m_source), "is-live", true, nullptr);
    g_object_set (G_OBJECT (m_source), "format", 3, nullptr);
    g_object_set (G_OBJECT (m_sink)  , "emit-signals", TRUE, "sync", FALSE, nullptr);
    gst_caps_unref (capsSrc);

    if (!gst_element_link_many (m_source, m_decoder, nullptr))
    {
        LOG (error) << "Audio Decoder: source and decoder elements could not be linked for uri = " << m_uri << endl;
        return -1;
    }

#ifdef DEBUG
    g_signal_connect( m_pipeline, "deep-notify", G_CALLBACK( gst_object_default_deep_notify ), nullptr );
#endif

    if (!link_decoder(m_decoder, m_sink))
    {
        LOG (error) << "Audio Decoder: decoder and sink elements could not be linked for uri = " << m_uri << endl;
        return -1;
    }
    
    if(!g_signal_connect (m_sink, "new-sample", G_CALLBACK (on_new_sample_from_sink), (void*)this))
    {
        LOG(error) << "Audio Decoder: Error in g_signal_connect of new-sample for uri = " << m_uri << endl;
    }

    gst_element_set_state (m_pipeline, GST_STATE_PLAYING);
    StreamMonitor::getInstance()->registerDataCallback(m_uri, getself());
    LOG(info) << "Created GstNvAudioDecoder pipeline for uri = " << m_uri << endl;
    return 0;
}

void GstNvAudioDecoder::destroy(bool expect_result)
{
    LOG(info) << "Destroying Audio Pipeline for uri = " << m_uri << endl;
    if (!m_udpSource)
    {
        StreamMonitor::getInstance()->deregisterDataCallback(getself(), m_uri);
    }
    {
        std::lock_guard<std::mutex> lock(m_sinkLock);
        m_sinks.clear();
    }
    std::lock_guard<std::mutex> guard(m_pipelineLock);
    if (m_pipeline)
    {
        gst_element_set_state (m_pipeline, GST_STATE_NULL);
        gst_element_get_state (m_pipeline, nullptr, nullptr, 5 * GST_SECOND);
        gst_object_unref (m_pipeline);
        m_pipeline = nullptr;
    }
    m_audioBuffer.clear();
    m_decOutFrames = 0;
    LOG(info) << "Destroyed Audio Pipeline for uri = " << m_uri << endl;
}