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
#include "gstnvipcproducer.h"
#include "stats.h"
#include "nvhwdetection.h"
#include "gstnvipcmeta.h"

using namespace std;
using namespace nv_vms;

static std::array<FrameSize, 7> g_resolutions = { FrameSize(WIDTH_2160p, HEIGHT_2160p),
                                                  FrameSize(WIDTH_1080p, HEIGHT_1080p),
                                                  FrameSize(WIDTH_720p, HEIGHT_720p),
                                                  FrameSize(WIDTH_480p, HEIGHT_480p),
                                                  FrameSize(WIDTH_360p, HEIGHT_360p),
                                                  FrameSize(WIDTH_240p, HEIGHT_240p),
                                                  FrameSize(WIDTH_144p, HEIGHT_144p)
                                                 };

NvIPCProducer::NvIPCProducer (const string& stream_id) :
    m_pipeline (nullptr),
    m_source (nullptr),
    m_sink (nullptr),
    m_width (1920),
    m_height (1080),
    m_busWatchId(G_MAXUINT),
    m_streamID(stream_id)
{
    LOG(info) << "Created IPC Producer instance for stream_id: " << stream_id << endl;
}

NvIPCProducer::~NvIPCProducer ()
{
    {
        std::lock_guard<std::mutex> lock(m_videoSinkLock);
        m_videoSinkList.clear();
    }
    LOG(info) << "IPC Producer instance deleted" << endl;
}

void NvIPCProducer::setOptions(const std::map<std::string, std::string, std::less<>> &opts)
{
    if ( opts.find("peerid") != opts.end() )
    {
        m_peerid = opts.at("peerid");
    }
}

void NvIPCProducer::setSourceFrameSize(uint32_t w, uint32_t h)
{
    m_sourceWidth = w;
    m_sourceHeight = h;
}

static GstPadProbeReturn pad_cb(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
{
    gboolean res = false;
    GstEvent *event = nullptr;
    event = GST_PAD_PROBE_INFO_EVENT(info);
    if (event)
    {
        if (GST_EVENT_CAPS == GST_EVENT_TYPE(event))
        {
            GstCaps * caps;
            int width, height;
            gst_event_parse_caps(event, &caps);

            GstStructure *gstStruct = gst_caps_get_structure(caps, 0);
            if (gstStruct)
            {
                res = gst_structure_get_int (gstStruct, "width", &width);
                res |= gst_structure_get_int (gstStruct, "height", &height);
                if (!res)
                {
                    LOG (error) << "IPC No resolution information received";
                }
                NvIPCProducer* ipc_producer = (NvIPCProducer*) user_data;
                LOG (info) << "IPC Resolution information received: Frame Size: "<< width << "x" << height << endl;
                ipc_producer->setSourceFrameSize(width, height);
            }
            else
            {
                LOG (error) << "gst_caps_get_structure failed" << endl;
            }
        }
    }
    return GST_PAD_PROBE_OK;
}

/* called when the appsink notifies us that there is a new buffer ready for
 * processing */
static GstFlowReturn
on_new_sample_from_sink (GstElement * appsink, NvIPCProducer* ipc_producer)
{
    if (ipc_producer)
    {
        return ipc_producer->processNewSampleFromSink(appsink);
    }
    return GST_FLOW_ERROR;
}

GstFlowReturn NvIPCProducer::processNewSampleFromSink (GstElement * appsink)
{
    GstSample *sample = nullptr;
    GstBuffer *gstBuffer = nullptr;
    GstMapInfo map;
    int targetWidth  = 0;
    int targetHeight = 0;

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
        LOG(error) << "IPC Producer: received 0 sized buffer";
        /* Unmap the gst buffer */
        gst_buffer_unmap (gstBuffer, &map);

        /* Unref the sample */
        gst_sample_unref (sample);
        return GST_FLOW_OK;
    }

    {
        struct timeval presentationTime;
        gettimeofday(&presentationTime, nullptr);
        /* Check if all sinks are null */
        bool is_sinkPresent = isSinkPresent ();
        if (is_sinkPresent)
        {
            std::lock_guard<std::mutex> lock(m_videoSinkLock);
            std::map<std::string, std::shared_ptr<VideoSinkInfo>>::iterator it;
            for( it = m_videoSinkList.begin(); it != m_videoSinkList.end(); it++)
            {
                shared_ptr<VideoSinkInfo> sink = it->second;
                /* Avoid sending frames to encoder
                ** till PLAY is not received for that peer id*/
                if (!sink->m_isSinkReady)
                {
                    continue;
                }
                /* Create a YUV Buffer */
                targetWidth = sink->m_frameSize.m_width;
                targetHeight = sink->m_frameSize.m_height;
                if(targetWidth == 0 || targetHeight == 0)
                {
                    LOG(error) << "targetWidth/targetHeight of frame is zero" << endl;
                    continue;
                }

                if (sink->m_consumer.get() == nullptr)
                {
                    LOG(error) << "IPC consumer is NULL for " << m_peerid << ", streamId = " << m_streamID << endl;
                    continue;
                }
                std::shared_ptr<RawFrameParams> consumer_frame_data = std::make_shared<RawFrameParams>();
                consumer_frame_data->m_streamId     = m_streamID;
                consumer_frame_data->m_targetWidth  = targetWidth;
                consumer_frame_data->m_targetHeight = targetHeight;
                consumer_frame_data->m_fd           = 0;
                consumer_frame_data->m_index        = 0;
                consumer_frame_data->m_sourceWidth  = m_sourceWidth;
                consumer_frame_data->m_sourceHeight = m_sourceHeight;
                consumer_frame_data->m_sample       = sample;
                sink->m_consumer->onFrame (consumer_frame_data);
            }
        }
    }

    /* Unmap the gst buffer */
    gst_buffer_unmap (gstBuffer, &map);

    /* Unref the sample */
    gst_sample_unref (sample);

    return GST_FLOW_OK;
}

FrameSize NvIPCProducer::qualityToFrameSize(const string& quality)
{
    FrameSize size;
    FrameSize source_frame_size;
    source_frame_size.m_width = m_sourceWidth;
    source_frame_size.m_height = m_sourceHeight;

    if (quality == "high" || quality == "pass_through")
    {
        size.m_width = WIDTH_1080p;
        size.m_height = HEIGHT_1080p;
    }
    else if (quality == "medium")
    {
        size.m_width = WIDTH_720p;
        size.m_height = HEIGHT_720p;
    }
    else if (quality == "low")
    {
        size.m_width = WIDTH_480p;
        size.m_height = HEIGHT_480p;
    }
    else
    {
        size.m_width = m_sourceWidth;
        size.m_height = m_sourceHeight;
    }

    size = size.minimum(source_frame_size);
    return size;
}

void NvIPCProducer::setConsumer(const string& peerid, std::shared_ptr<IMediaDataConsumer> consumer)
{
    /* Add Consumer to map  */
    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    std::map<std::string, std::shared_ptr<VideoSinkInfo>>::iterator it = m_videoSinkList.find(peerid);
    if(it == m_videoSinkList.end())
    {
        shared_ptr<VideoSinkInfo> sink (new VideoSinkInfo);
        sink->m_consumer = consumer;
        sink->m_consumer->setOriginalFrameSize(m_sourceWidth, m_sourceHeight);
        sink->m_consumer->setIPCMeta ();
        m_videoSinkList[peerid] = sink;
    }
}

void NvIPCProducer::setConsumerReady(const string& peerid, bool isReady)
{
    /* search peer in map to set start play flag */
    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    std::map<std::string, std::shared_ptr<VideoSinkInfo>>::iterator it = m_videoSinkList.find(peerid);
    if(it != m_videoSinkList.end())
    {
        std::shared_ptr<VideoSinkInfo> sink = it->second;
        sink->m_isSinkReady = isReady;
    }
}

void NvIPCProducer::setQuality(const std::string& peerid, const std::string& quality)
{
    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    std::map<std::string, std::shared_ptr<VideoSinkInfo>>::iterator it = m_videoSinkList.find(peerid);
    if(it != m_videoSinkList.end())
    {
        std::shared_ptr<VideoSinkInfo> sink = it->second;
        sink->m_quality = quality;
        sink->m_frameSize = qualityToFrameSize(quality);
        sink->m_decoderStats.clear();
    }
    m_stop = false;
    LOG(info) << "IPC Sink list size = " << m_videoSinkList.size() << " for " << m_streamID << endl;
}

void NvIPCProducer::removeConsumer(const std::string& peerid)
{
    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    std::map<std::string, std::shared_ptr<VideoSinkInfo>>::iterator it = m_videoSinkList.find(peerid);
    if(it != m_videoSinkList.end())
    {
        m_videoSinkList.erase(it);
    }

    if (m_videoSinkList.size() == 0)
    {
        m_stop = true;
    }
}

bool NvIPCProducer::isSinkPresent ()
{
    if (m_stop == false)
    {
        std::lock_guard<std::mutex> lock(m_videoSinkLock);
        std::map<std::string, std::shared_ptr<VideoSinkInfo>>::iterator it;
        for( it = m_videoSinkList.begin(); it != m_videoSinkList.end();)
        {
            shared_ptr<VideoSinkInfo> sink = it->second;
            if (sink->m_consumer.get() == nullptr)
            {
                LOG(error) << "IPC consumer is NULL for " << m_peerid << " uri = " << m_streamID << endl;
                it = m_videoSinkList.erase(it);
            }
            else
            {
                it++;
            }
        }
        return m_videoSinkList.size() == 0 ? false : true;
    }
    else
    {
        return false;
    }
}

bool NvIPCProducer::isDRCAllowed ()
{
    bool ret = true;
    std::time_t now = std::time(nullptr);
    if (m_lastDRCTime != 0) // This is initial condition
    {
        now = std::time(nullptr);
        // ignore drc request less than interval set in config
        ret = (now - m_lastDRCTime >= GET_CONFIG().webrtc_out_min_drc_interval);
    }
    m_lastDRCTime = now;
    return ret;
}

FrameSize NvIPCProducer::handleDRC(const string& peerid, int targetPixels, int targetFPS)
{
    FrameSize frame_size;
    if (!isDRCAllowed ())
    {
        LOG(warning) << "IPC DRC Request too early, in less than " << GET_CONFIG().webrtc_out_min_drc_interval << " secs" << endl;
        return frame_size;
    }

    std::lock_guard<std::mutex> lock(m_videoSinkLock);
    std::map<std::string, std::shared_ptr<VideoSinkInfo>>::iterator it = m_videoSinkList.find(peerid);
    FrameSize prev_size = g_resolutions[m_resolutionIndex];
    if(it != m_videoSinkList.end())
    {
        shared_ptr<VideoSinkInfo> sink = it->second;
        FrameSize source_frame_size;
#ifdef JETSON_PLATFORM
        // For any quality respect the webrtc out default resolution specified in config
        Resolution resolution;
        resolution = GET_CONFIG().webrtc_out_default_resolution;
        if (!resolution.empty() || NvHwDetection::getInstance()->m_useNvV4l2Enc == false)
        {
            // For Orin Nano scale-down the video
            int width = WIDTH_480p, height = HEIGHT_480p;

            if (!resolution.empty())
            {
                width = stringToInt(resolution.width, WIDTH_480p);
                // Replace with nearest multiple of 8 - Bug 4561987
                width = ((width + 7) >> 3) << 3;
                height = stringToInt(resolution.height, HEIGHT_480p);
            }
            source_frame_size.m_width = std::min(width, (int)m_sourceWidth);
            source_frame_size.m_height = std::min(height, (int)m_sourceHeight);

            // DRC not supported in Orin Nano. Bug 4619232
            sink->m_frameSize = source_frame_size;
            if (prev_size.m_width != sink->m_frameSize.m_width || prev_size.m_height != sink->m_frameSize.m_height)
            {
                LOG(warning) << "IPC Peer id = " << peerid << " Changing resolution from : " << prev_size.m_width << "x" << prev_size.m_height << " ==> " << sink->m_frameSize.m_width << "x" << sink->m_frameSize.m_height << endl;
            }
            return sink->m_frameSize;
        }
#endif
        if(sink->m_quality == "auto")
        {
            size_t res_index = 0;
            source_frame_size.m_width = m_sourceWidth;
            source_frame_size.m_height = m_sourceHeight;
            LOG(warning) << "source W = " << m_sourceWidth << endl;
            LOG(warning) << "source H = " << m_sourceHeight << endl;
            while(true)
            {
                res_index = std::min(res_index, g_resolutions.size() - 1 );
                FrameSize size = g_resolutions[res_index];
                if((size.getPixels() <= targetPixels) || (res_index == (g_resolutions.size() - 1)))
                {
                    /* sink's framesize should be less than or equal to source frame size */
                    sink->m_frameSize = size.minimum(source_frame_size);
                    LOG(warning) << "size W = " << size.m_width << endl;
                    LOG(warning) << "size H = " << size.m_height << endl;
                    m_resolutionIndex = res_index;
                    break;
                }
                ++ res_index;
            }
        }
        LOG(warning) << "IPC Peer id = " << peerid << " Changing resolution from : " << prev_size.m_width << "x" << prev_size.m_height << " ==> " << sink->m_frameSize.m_width << "x" << sink->m_frameSize.m_height << endl;
        return sink->m_frameSize;
    }
    return frame_size;
}


std::string NvIPCProducer::getstate()
{
    string state_str = gst_element_state_get_name(m_state);
    if (state_str != "PLAYING" && state_str != "PAUSED")
    {
        state_str = "NOT_PLAYING";
    }
    return state_str;
}

gboolean busWatchIPC (GstBus *bus, GstMessage *message, gpointer ipc_producer_data)
{
    GError *error = nullptr;
    gchar *name, *debug = nullptr;
    NvIPCProducer* ipc_producer = (NvIPCProducer*)ipc_producer_data;
    if (ipc_producer == nullptr)
    {
        LOG(error) << "IPC Producer object is NULL" << endl;
        goto exit;
    }
    if (message)
    {
        if (GST_MESSAGE_TYPE (message) == GST_MESSAGE_ERROR)
        {
            /* get element name from which error was triggered */
            name = gst_object_get_path_string (message->src);

            /* get actual error message and debug info */
            gst_message_parse_error (message, &error, &debug);
            if(error != nullptr && name != nullptr)
            {
                LOG(error) << "IPC ERROR : " <<  name << error->message << endl;
                g_error_free (error);
                g_free (name);
            }
            if (debug != nullptr)
            {
                LOG (error) << "IPC Additional debug info: " << debug;
                g_free (debug);
            }
            LOG (error) << "IPC Gstreamer error occured: " <<  endl;
            ipc_producer->m_error = true;
            ipc_producer->destroy ();
            goto exit;
        }
        else if (GST_MESSAGE_TYPE (message) == GST_MESSAGE_EOS)
        {
            LOG(warning) << "****** IPC Received EOS: " << " ********"<<endl;
        }
        else if (GST_MESSAGE_TYPE (message) == GST_MESSAGE_ASYNC_DONE ||
                    GST_MESSAGE_TYPE (message) == GST_MESSAGE_STATE_CHANGED)
        {
            if (GST_MESSAGE_TYPE (message) == GST_MESSAGE_ASYNC_DONE)
            {
                LOG(info) << "IPC Received ASYNC_DONE" <<  endl;
                ipc_producer->getGstState ();
            }
        }
    }
    else
    {
        LOG (info) << "No message on Gstreamer Bus" << endl;
    }
exit:
    return TRUE;
}

bool NvIPCProducer::setGstState(State state)
{
    bool ret = true;
    GstState gst_state = static_cast<GstState>(state);
    if (gst_element_set_state (m_pipeline, gst_state) == GST_STATE_CHANGE_FAILURE)
    {
        LOG(error) << "Unable to set the pipeline to " << gst_state << " for streamId:" << m_streamID << endl;
        ret = false;
    }
    else
    {
        string state_str = gst_element_state_get_name(gst_state);
        LOG(info) << "Set State to " << state_str << " success for streamId:" << m_streamID << endl;
    }
    return ret;
}

void NvIPCProducer::getGstState()
{
    GstState  current, pending;
    gst_element_get_state(m_pipeline, &current, &pending, GST_SECOND);
    m_state = current;
    string state_str = gst_element_state_get_name(current);
    LOG(info) << "Current state: " << state_str << "(" << current << ") for streamId:" << m_streamID << endl;
}


int NvIPCProducer::create ()
{
    LOG(info) << "Creating IPC Producer pipeline for " << m_streamID << endl;
    GstBus* bus = nullptr;


    std::lock_guard<std::mutex> guard(m_pipelineLock);
    m_pipeline           = gst_pipeline_new         ("video_ipc_pipeline");
    m_source             = gst_element_factory_make ("nvunixfdsrc"       , nullptr);
    GstElement* caps     = gst_element_factory_make ("capsfilter"       , nullptr);
    GstElement* queue    = gst_element_factory_make ("queue"       , nullptr);
    GstElement* identity = gst_element_factory_make ("identity"       , nullptr);
    m_sink               = gst_element_factory_make ("appsink"      , nullptr);

    /* Check if any of element failed to create */
    if (!m_pipeline || !m_source || !caps || !queue || !identity || !m_sink)
    {
        LOG (error) << "Gstreamer IPC Producer element creation failed" << endl;
        return -1;
    }

    gst_bin_add_many (GST_BIN (m_pipeline), m_source, caps, queue, identity, m_sink, NULL);

    if (!gst_element_link_many (m_source, caps, queue, identity, m_sink, NULL))
    {
        LOG (error) << "IPC Producer: elements could not be linked" << endl;
        return -1;
    }

    std::string caps_string = "video/x-raw(memory:NVMM), format=(string)NV12";

    LOG(info) << "IPC Input Caps = " << caps_string << endl;
    GstCaps* capsSrc = gst_caps_from_string (caps_string.c_str());
    g_object_set (G_OBJECT (caps),   "caps"        , capsSrc, NULL);


#ifdef DEBUG
    g_signal_connect( m_pipeline, "deep-notify", G_CALLBACK( gst_object_default_deep_notify ), nullptr );
    g_object_set (G_OBJECT (identity),"silent"        , false, NULL);
#endif

    GstPad* sinkpad = gst_element_get_static_pad (m_sink, "sink");
    /* Check if sink_pad exists */
    if (!sinkpad)
    {
        LOG(error) << "Failed to get sink pad of appsink." << endl;
        return -1;
    }
    /* Add probe to query width and height of video stream */
    gst_pad_add_probe(sinkpad, GST_PAD_PROBE_TYPE_EVENT_BOTH, pad_cb, (void*)this, nullptr);
    gst_object_unref(sinkpad);

    if(!g_signal_connect (m_sink, "new-sample", G_CALLBACK (on_new_sample_from_sink), (void*)this))
    {
        LOG(error) << "IPC Producer: Error in g_signal_connect of new-sample" << endl;
        return -1;
    }

    /* Set properties for source element */
    string socket_name = GET_CONFIG().ipc_socket_path + m_streamID + "_ds";
    g_object_set (m_source, "socket-path"             , socket_name.c_str()                        , NULL);
    g_object_set (m_source, "meta-deserialization-lib", "prebuilts/aarch64/libnvdsgst_ipcmeta.so"  , NULL);
    g_object_set (m_source, "buffer-timestamp-copy"   , GET_CONFIG().ipc_src_buffer_timestamp_copy , NULL);
    g_object_set (m_source, "connection-attempts"     , GET_CONFIG().ipc_src_connection_attempts   , NULL);
    g_object_set (m_source, "connection-interval"     , GET_CONFIG().ipc_src_connection_interval_us, NULL);

    g_object_set (m_sink, "emit-signals", true, "sync", false, NULL);

    bus = gst_pipeline_get_bus (GST_PIPELINE (m_pipeline));
    if (!bus)
    {
        LOG(error) << "Failed to get BUS of IPC Producer pipeline" << endl;
        return -1;
    }
    m_busWatchId = gst_bus_add_watch (bus, busWatchIPC, (void*)this);
    gst_object_unref(bus);

    if (setGstState (PLAYING) == false)
    {
        return -1;
    }

    LOG(info) << "Created IPC Producer pipeline for " << m_streamID << endl;
    return 0;
}

void NvIPCProducer::destroy()
{
    LOG(info) << "Destroying IPC Producer Pipeline for " << m_streamID << endl;

    std::lock_guard<std::mutex> guard(m_pipelineLock);

    if (m_busWatchId != G_MAXUINT)
    {
        g_source_remove (m_busWatchId);
        m_busWatchId = G_MAXUINT;
    }

    if (m_pipeline)
    {
        setGstState (EMPTY);
        getGstState();
        gst_object_unref (m_pipeline);
        m_pipeline = nullptr;
        m_state = GST_STATE_NULL;
    }

    LOG(info) << "Destroyed IPC Producer Pipeline for " << m_streamID << endl;
}