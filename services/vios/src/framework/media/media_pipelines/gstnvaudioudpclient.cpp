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

#include <string.h>
#include <iostream>
#include <gst/gst.h>
#include <string.h>
#include <glib/gstdio.h>
#include <errno.h>
#include <gst/app/gstappsrc.h>
#include <gst/app/gstappsink.h>
#include "gstnvaudioudpclient.h"
#include <limits>
#include <algorithm>
#include <sys/time.h>

using namespace std;
using namespace nv_vms;

namespace nv_vms
{
    gboolean udpAudioBusWatchFunc (GstBus *bus, GstMessage *message, gpointer data)
    {
        GstUDPAudioClient* gstUDPAudioClient = (GstUDPAudioClient*)data;
        GError *error = nullptr;
        gchar *name, *debug = nullptr;
        if (gstUDPAudioClient == nullptr)
        {
            LOG(error) << "gstUDPAudioClient object is NULL" << endl;
            goto exit;
        }
        {
            if (message)
            {
                if (GST_MESSAGE_TYPE (message) == GST_MESSAGE_ERROR)
                {
                    LOG(error) << "GST_MESSAGE_ERROR" << endl;
                    /* get element name from which error was triggered */
                    name = gst_object_get_path_string (message->src);

                    /* get actual error message and debug info */
                    gst_message_parse_error (message, &error, &debug);
                    if(error != nullptr && name != nullptr)
                    {
                        LOG(error) << "ERROR : " <<  name << error->message << endl;
                        g_error_free (error);
                        g_free (name);
                    }
                    if (debug != nullptr)
                    {
                        LOG (error) << "Additional debug info: " << debug;
                        g_free (debug);
                    }
                }
                else if (GST_MESSAGE_TYPE (message) == GST_MESSAGE_EOS)
                {
                    LOG(info) << "GST_MESSAGE_EOS" << endl;
                }
            }
            else
            {
                LOG (info) << "No message on Gstreamer Bus" << endl;
            }
        }
        exit:
        return TRUE;
    }
}

GstFlowReturn GstUDPAudioClient::processNewSampleFromSink (GstElement * appsink)
{
    static int fcount = 0;
    static long int timestamp = 0;
    static long int prev_ts = 0;

    GstSample *sample;
    GstBuffer *gstBuffer;
    GstMapInfo map;

    /* Get the sample from appsink */
    sample = gst_app_sink_pull_sample (GST_APP_SINK (appsink));

    if (sample == nullptr)
    {
        if (gst_app_sink_is_eos((GstAppSink *)appsink))
        {
            LOG(info) << "EOS Received on app sink element" << endl;
            return GST_FLOW_OK;
        }
    }

    /* Get the buffer from sample */
    gstBuffer = gst_sample_get_buffer (sample);
    if (gstBuffer == nullptr)
    {
        LOG(info) << "No more buffers available from app sink element" << endl;
        return GST_FLOW_OK;
    }

    timestamp = GST_BUFFER_PTS (gstBuffer);
    LOG(verbose2) << "frameNumber:" << ++fcount << ", PTS:" << timestamp << ", ts_diff:" << ((timestamp - prev_ts)/1000000) << endl;
    prev_ts = timestamp;

    /* Map the gst buffer */
    if (gst_buffer_map (gstBuffer, &map, GST_MAP_READ) == false)
    {
        LOG(error) << "Map the gst buffer Failed" << endl;
        return GST_FLOW_OK;
    }

    std::shared_ptr<IMediaDataConsumer> consumer = getConsumer(UdpClient::UDP_AUDIO_TYPE);
    if (consumer)
    {
        struct timeval presentationTime = {};
        FrameParams frame_params;
        frame_params.m_media   = "audio";
        frame_params.m_codec   = "PCMU";
        frame_params.m_buffer  = map.data;
        frame_params.m_size    = map.size;
        frame_params.m_presentationTime = presentationTime;
        consumer->onFrame(frame_params);
    }

    /* Unmap the gst buffer */
    gst_buffer_unmap (gstBuffer, &map);

    /* Unref the sample */
    gst_sample_unref (sample);

    return GST_FLOW_OK;
}

/* called when the appsink notifies us that there is a new buffer ready for
 * processing */
static GstFlowReturn
on_new_sample_from_sink (GstElement * appsink, GstUDPAudioClient* nvAudioUDPClient)
{
   if (nvAudioUDPClient)
    {
        return nvAudioUDPClient->processNewSampleFromSink(appsink);
    }
   return GST_FLOW_ERROR;
}

int GstUDPAudioClient::create_internal ()
{
    LOG (info) << "Creating Gstreamer UDP Audio Client Pipeline on port: " << m_id << endl;
    if (gst_is_initialized() == false)
    {
        gst_init (nullptr, nullptr);
    }

    m_pipeline = gst_pipeline_new         ("udp_audio_pipeline");
    m_source   = gst_element_factory_make ("udpsrc"      , nullptr);
    m_rtpJitterBuffer = gst_element_factory_make ("rtpjitterbuffer" , nullptr);
    m_rtpdepay = gst_element_factory_make ("rtpL16depay" , nullptr);
    m_conv     = gst_element_factory_make ("audioconvert", nullptr);
    m_filter   = gst_element_factory_make ("capsfilter"  , nullptr);
    m_encoder  = gst_element_factory_make ("mulawenc"    , nullptr);
    m_queue    = gst_element_factory_make ("queue"       , nullptr);
    m_sink     = gst_element_factory_make ("appsink"     , nullptr);

    /* Check if any of element failed to create */
    if (!m_pipeline || !m_source || !m_rtpJitterBuffer || !m_rtpdepay || !m_conv || !m_filter || !m_encoder || !m_queue || !m_sink)
    {
        LOG (error) << "Gstreamer element creation failed" << endl;
        return -1;
    }

    /* Setting properties of elements */

    g_object_set(GST_BIN(m_pipeline), "message-forward", TRUE, nullptr);
    std::string caps_string = "application/x-rtp,clock-rate=(int)" + to_string(m_freq);
    GstCaps *caps = gst_caps_from_string (caps_string.c_str());
    LOG(info) << "GstUDPAudioClient udpsrc caps = " << caps_string << endl;

    g_object_set(m_source, "port",        getAudioPort(), nullptr);
    g_object_set(m_source, "buffer-size", 2000000,   nullptr);
    g_object_set(m_source, "caps",        caps,      nullptr);

    caps = gst_caps_from_string("audio/x-raw, channels=1");
    g_object_set(m_filter, "caps", caps, nullptr);
    gst_caps_unref (caps);

    m_bus = gst_pipeline_get_bus (GST_PIPELINE (m_pipeline));
    if (!m_bus)
    {
        LOG(error) << "Failed to get BUS of UDP Audio Client pipeline" << endl;
        return -1;
    }
    m_busWatchId = gst_bus_add_watch (m_bus, udpAudioBusWatchFunc, (void*)this);

    /* Add Elements in pipeline */
    gst_bin_add_many (GST_BIN (m_pipeline), m_source, m_rtpJitterBuffer, m_rtpdepay, m_conv, m_filter, m_encoder, m_queue, m_sink, nullptr);

    /* Link Elements in pipeline */
    if (gst_element_link_many(m_source, m_rtpJitterBuffer, m_rtpdepay, m_conv, m_filter, m_encoder, m_queue, m_sink, nullptr) != TRUE)
    {
        LOG (error) << "Many elements could not be linked." << endl;
        gst_object_unref(m_pipeline);
        return -1;
    }

    /* Add signal to get the buffers from app sink element */
    g_object_set (G_OBJECT (m_sink), "emit-signals", TRUE, "sync", TRUE, nullptr);
    if(!g_signal_connect (m_sink, "new-sample", G_CALLBACK (on_new_sample_from_sink), this))
    {
        LOG(error) << "Error in g_signal_connect of new-sample" << endl;
        return -1;
    }

    LOG (info) << "Created Gstreamer UDP Audio Client Pipeline on port: " << m_id << endl;
    return 0;
}

void GstUDPAudioClient::play_internal ()
{
    LOG (info) << "Transitioning to PLAY Gstreamer UDP Audio Client Pipeline on port: " << m_id << endl;
    gst_element_set_state (m_pipeline, GST_STATE_PLAYING);
    LOG (info) << "Setting Completed to PLAY Gstreamer UDP Audio Client Pipeline on port: " << m_id << endl;
}

bool GstUDPAudioClient::pause_internal()
{
    bool ret = true;
    if (m_pipeline)
    {
        LOG (info) << "Pausing the pipeline, port:" << m_id << endl;
        GstStateChangeReturn gstStateChangeRet = gst_element_set_state (m_pipeline, GST_STATE_PAUSED);
        if (gstStateChangeRet == GST_STATE_CHANGE_FAILURE)
        {
            LOG (error) << "gst_element_set_state failed UDP Audio Client Pipeline on port: " << m_id << endl;
            ret = false;
        }
        else
        {
            gst_element_get_state (m_pipeline, nullptr, nullptr, GST_SECOND);
            LOG (info) << "State change success UDP Audio Client Pipeline on port: " << m_id << endl;
        }
    }
    return ret;
}

void GstUDPAudioClient::resume_internal ()
{
    LOG (info) << "Resume the pipeline, port:" << m_id << endl;
    if (m_pipeline)
    {
        gst_element_set_state (m_pipeline, GST_STATE_PLAYING);
    }
}

void GstUDPAudioClient::destroy_internal ()
{
    LOG(info) << "Terminating gstreamer UDP Audio Client Pipeline on port: " << m_id << endl;
    if (m_pipeline == nullptr)
    {
        return;
    }
    if (m_pipeline)
    {
        gst_element_set_state (m_pipeline, GST_STATE_NULL);
        gst_element_get_state (m_pipeline, nullptr, nullptr, 5 * GST_SECOND);
        gst_object_unref (m_pipeline);
        m_pipeline = nullptr;
    }
    if (m_busWatchId != G_MAXUINT)
    {
        g_source_remove (m_busWatchId);
        m_busWatchId = G_MAXUINT;
    }
    if (m_bus)
    {
        gst_object_unref (m_bus);
        m_bus = nullptr;
    }
    LOG(info) << "Terminated gstreamer UDP Audio Client Pipeline on port: " << m_id << endl;
}

int GstUDPAudioClient::create(int freq)
{
    m_eventLoop.setParent(this);
    std::shared_ptr<EventLoopData> data(new EventLoopData);
    m_freq = freq;
    data->m_taskName = "create";
    m_eventLoop.postMsg(data);
    return 0;
}

void GstUDPAudioClient::start()
{
    m_eventLoop.setParent(this);
    std::shared_ptr<EventLoopData> data(new EventLoopData);
    data->m_taskName = "play";
    m_eventLoop.postMsg(data);
    return;
}

void GstUDPAudioClient::pause()
{
    std::shared_ptr<EventLoopData> data(new EventLoopData);
    data->m_taskName = "pause";
    m_eventLoop.postMsg(data);
    return;
}

void GstUDPAudioClient::resume()
{
    std::shared_ptr<EventLoopData> data(new EventLoopData);
    data->m_taskName = "resume";
    m_eventLoop.postMsg(data);
    return;
}

void GstUDPAudioClient::destroy(bool expect_result)
{
    std::shared_ptr<EventLoopData> data(new EventLoopData);
    data->m_taskName = "destroy";
    m_eventLoop.postMsg(data);
    return;
}

void GstUDPAudioClient::process_eventloop_message(std::shared_ptr<EventLoopData> data, void* parent)
{
    shared_ptr<EventLoopData> ev_data = std::static_pointer_cast<EventLoopData>(data);
    GstUDPAudioClient* udpClient = static_cast <GstUDPAudioClient*>(parent);
    if (udpClient == nullptr || ev_data == nullptr)
    {
        LOG(error) << "Received null data" << endl;
        return;
    }
    LOG(verbose) << ev_data->m_taskName << endl;
    if (ev_data->m_taskName == "create")
    {
        udpClient->create_internal();
    }
    else if (ev_data->m_taskName == "play")
    {
        udpClient->play_internal();
    }
    else if (ev_data->m_taskName == "pause")
    {
        udpClient->pause_internal();
    }
    else if (ev_data->m_taskName == "resume")
    {
        udpClient->resume_internal();
    }
    else if (ev_data->m_taskName == "destroy")
    {
        udpClient->destroy_internal();
    }
    else
    {
        LOG(warning) << "Invalid action" << endl;
    }
}