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

#include <string.h>
#include <iostream>
#include <gst/gst.h>
#include <string.h>
#include <glib/gstdio.h>
#include <errno.h>
#include <gst/app/gstappsrc.h>
#include <gst/app/gstappsink.h>
#include "gstnvvideoudpclient.h"
#include "gstnvvideodecoder.h"
#include <limits>
#include <algorithm>
#include <sys/time.h>

using namespace std;
using namespace nv_vms;

#define DECODER_EXTRA_SURFACES 6
#define CUDA_DEC_MEM_TYPE_DEVICE 0

const string UdpClient::UDP_AUDIO_TYPE = "audio";
const string UdpClient::UDP_VIDEO_TYPE = "video";
const string UdpClient::UDP_VIDEO_AUDIO_TYPE = "video_audio";
const string UdpClient::UDP_UNKNOWN_TYPE = "unknown";

#define NV_V4L2_DECODER        "nvv4l2decoder"
#define SW_DECODER             "avdec_h264"
#define DECODER_NODE           "/dev/nvidia0"
#define NV_VID_CONV            "nvvidconv"
#define NV_VID_CONVERT         "nvvideoconvert"
#define VID_CONVERT            "videoconvert"

#define GST_DEBUG_DECODER_PROBE_COUNT 10
#define GST_DEBUG_UDPSRC_PROBE_COUNT 1500
#define VIDEO_DATA_WATCH_DOG_SCHEDULER_INTERVAL  10s

namespace nv_vms
{
    gboolean udpVideoBusWatchFunc (GstBus *bus, GstMessage *message, gpointer data)
    {
        GstUDPVideoClient* gstUDPVideoClient = (GstUDPVideoClient*)data;
        GError *error = nullptr;
        gchar *name, *debug = nullptr;
        if (gstUDPVideoClient == nullptr)
        {
            LOG(error) << "gstUDPVideoClient object is NULL" << endl;
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

GstUDPVideoClient::GstUDPVideoClient (const string&  id, UdpStream& stream)
    : UdpClient(id, stream)
    , m_pipeline(nullptr)
    , m_source(nullptr)
    , m_rtpjitterbuffer(nullptr)
    , m_queue(nullptr)
    , m_identityVideo(nullptr)
    , m_rtpdepay(nullptr)
    , m_parserBeforeMux(nullptr)
    , m_queueVideo(nullptr)
    , m_filter(nullptr)
    , m_queueVideoAfterDeMux (nullptr)
    , m_queueVideoAfterDecode (nullptr)
    , m_videoConverter(nullptr)
    , m_convCapsFilter(nullptr)
    , m_sink(nullptr)
    , m_mux(nullptr)
    , m_queueAfterMux(nullptr)
    , m_demux(nullptr)
    , m_mixerAudio(nullptr)
    , m_sourceAudio(nullptr)
    , m_rtpJitterBufferAudio(nullptr)
    , m_rtpdepayAudio(nullptr)
    , m_filterAudio(nullptr)
    , m_encoderAudio(nullptr)
    , m_convAudio(nullptr)
    , m_queueAudioAfterDeMux(nullptr)
    , m_capsAudioAfterDeMux(nullptr)
    , m_decodebinAudio(nullptr)
    , m_queueAudio(nullptr)
    , m_queueAudioAfterDecode(nullptr)
    , m_identityAudio(nullptr)
    , m_sinkAudio(nullptr)
    , m_bus(nullptr)
    , m_bus_watch_id(0)
    , m_eventLoop("udp_video_event_loop", process_eventloop_message)
    , m_is_error(false)
    , m_udpsrcVideoFrameCount(0)
    , m_udpsrcVideoProbeCount(0)
    , m_udpsrcAudioProbeCount(0)
    , m_videoDecoderProbeCount(0)
    , m_audioDecoderProbeCount(0)
{
    LOG(info) << "GstUDPVideoClient::GstUDPVideoClient port:" << id << endl;
    m_fpsDisplay.reset(new FPSDisplay());
    if (GET_CONFIG().enable_udp_input_dump)
    {
        string file_name = "udp_video_dump_" + id + ".mkv";
        m_dumpFile = fopen(file_name.c_str(), "wb");
    }
}

GstUDPVideoClient::~GstUDPVideoClient ()
{
    LOG(info) << "~GstUDPVideoClient port:" << m_id << endl;
    if (GET_CONFIG().enable_udp_input_dump)
    {
        fclose(m_dumpFile);
    }
}

GstFlowReturn GstUDPVideoClient::processNewSampleFromSink (GstElement * appsink)
{
        struct timeval presentationTime;
        static struct timeval prevTs = {0};

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

        if (GST_BUFFER_PTS (gstBuffer) == GST_CLOCK_TIME_NONE)
        {
            // This could be non-data NALs, use prev timestamps.
            presentationTime = prevTs;
        }
        else
        {
            presentationTime.tv_sec = (GST_BUFFER_PTS (gstBuffer) / 1000000000);
            presentationTime.tv_usec = (GST_BUFFER_PTS (gstBuffer) % 1000000000) / 1000;
            prevTs = presentationTime;
        }

        /* Get vst metadata of the buffer */
        GstNvVstMeta *meta = GST_NV_VST_META_GET (gstBuffer);
        int64_t latencyStartTime = 0;
        if (meta)
        {
            latencyStartTime = meta->ts;
        }

        /* Map the gst buffer */
        if (gst_buffer_map (gstBuffer, &map, GST_MAP_READ) == false)
        {
                cout << "Map the gst buffer Failed" << endl;
                return GST_FLOW_OK;
        }
        LOG(verbose2) << "UDP appsink data size: " << map.size << ", PTS sec:"<< presentationTime.tv_sec << ", usec:" << presentationTime.tv_usec << endl;

        // Deliver the frames to the registered consumers.
        std::shared_ptr<IMediaDataConsumer> consumer = getConsumer(UdpClient::UDP_VIDEO_TYPE);
        if (consumer)
        {
            FrameParams frame_params;
            frame_params.m_media        = "video";
            frame_params.m_codec        = "H264";
            frame_params.m_buffer       = map.data;
            frame_params.m_size         = map.size;
            frame_params.m_width        = m_sourceWidth;
            frame_params.m_height       = m_sourceHeight;
            frame_params.m_needParsing  = false;
            frame_params.m_presentationTime = presentationTime;
            frame_params.m_extdata = sample;
            if (latencyStartTime != 0)
            {
                frame_params.m_latencyStartTime.tv_sec  = latencyStartTime / 1000000;
                frame_params.m_latencyStartTime.tv_usec = latencyStartTime % 1000000;
            }
            else
            {
                frame_params.m_latencyStartTime.tv_sec = std::numeric_limits<time_t>::max();
                frame_params.m_latencyStartTime.tv_usec = std::numeric_limits<time_t>::max();
            }
            consumer->onFrame(frame_params);
            m_videoDataFlowing = true;
            m_videoFirstFrameOut = true;
        }

        /* Unmap the gst buffer */
        gst_buffer_unmap (gstBuffer, &map);

        /* Unref the sample */
        gst_sample_unref (sample);

        return GST_FLOW_OK;
}

void GstUDPVideoClient::setSourceFrameSize(uint32_t w, uint32_t h)
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
                    LOG (error) << "No resolution information received";
                }
                GstUDPVideoClient* client = (GstUDPVideoClient*) user_data;
                LOG (info) << "Resolution information received: Frame Size: "<< width << "x" << height << endl;
                client->setSourceFrameSize(width, height);
            }
            else
            {
                LOG (error) << "gst_caps_get_structure failed" << endl;
            }
        }
    }
    return GST_PAD_PROBE_OK;
}

static void on_pad_added (GstElement *element1, GstPad *pad, gpointer data)
{
    GstCaps *caps = nullptr;
    gchar *capsString = nullptr;

    caps = gst_pad_get_current_caps (pad);
    capsString = gst_caps_to_string (caps);
    LOG(info) << "on_pad_added Caps = " << capsString << endl;
    GstUDPVideoClient* udpClient = (GstUDPVideoClient*)(data);
    /* Try to link pads only if format is video */
    if (g_strrstr(capsString, "video"))
	{
		GstPad *sink_pad = nullptr;
		GstElement *element2 = udpClient->m_queueVideoAfterDeMux;
		sink_pad = gst_element_get_static_pad (element2, "sink");
		/* Check if sink_pad exists */
		if (!sink_pad) {
			LOG(error) << "on_pad_added, Failed to get sink pad of element." << endl;
			GST_ELEMENT_ERROR(element2, STREAM, FAILED, ("Failed to get sink pad of element."), (nullptr));
			return;
		}

		/* Check if pads can be linked */
		if (gst_pad_link (pad, sink_pad) != GST_PAD_LINK_OK) {
			LOG(error) << "on_pad_added, Failed to link elements in pad-added callback. = " << sink_pad << endl;
			GST_ELEMENT_ERROR(element2, STREAM, FAILED, ("Failed to link elements in pad-added callback"), (nullptr));
		}

		/* Unref the data structure */
		if (sink_pad) {
			gst_object_unref (sink_pad);
		}
	}
    if (g_strrstr(capsString, "audio"))
	{
		GstPad *sink_pad = nullptr;
		GstElement *element2 = udpClient->m_queueAudioAfterDeMux;
		sink_pad = gst_element_get_static_pad (element2, "sink");
		/* Check if sink_pad exists */
		if (!sink_pad) {
			LOG(error) << "on_pad_added Failed to get sink pad of element." << endl;
			GST_ELEMENT_ERROR(element2, STREAM, FAILED, ("Failed to get sink pad of element."), (nullptr));
			return;
		}

		/* Check if pads can be linked */
		if (gst_pad_link (pad, sink_pad) != GST_PAD_LINK_OK) {
			LOG(error) << "on_pad_added, Failed to link elements in pad-added callback. = " << sink_pad << endl;
			GST_ELEMENT_ERROR(element2, STREAM, FAILED, ("Failed to link elements in pad-added callback"), (nullptr));
		}

		/* Unref the data structure */
		if (sink_pad) {
			gst_object_unref (sink_pad);
		}
	}
}

static GstPadProbeReturn udpsrc_audio_output_pad_cb(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
{
    GstUDPVideoClient* nvVideoClient = (GstUDPVideoClient*)user_data;
    if (nvVideoClient)
    {
        if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER)
        {
            if (nvVideoClient->m_udpsrcAudioProbeCount == 0)
            {
                LOG(info) << " [audio-encoder-output-probe] Received audio data ..." << endl;
            }
            ++(nvVideoClient->m_udpsrcAudioProbeCount);
            if (nvVideoClient->m_udpsrcAudioProbeCount > GST_DEBUG_UDPSRC_PROBE_COUNT)
            {
                // Check and log udp data after every fixed interval.
                nvVideoClient->m_udpsrcAudioProbeCount = 0;
            }
            if (!nvVideoClient->m_videoDataReceived)
            {
                LOG(verbose) << "Dropping audio Frames " << nvVideoClient->m_udpsrcAudioProbeCount << endl;
                return GST_PAD_PROBE_DROP;
            }
            struct timeval timeNow;
            gettimeofday(&timeNow, nullptr);
            int av_elapsed_time = timevaldiff(nvVideoClient->m_videoTime, timeNow) / 1000000;
            if (av_elapsed_time >= 1)
            {
                LOG(verbose) << "Dropping audio Frames " << nvVideoClient->m_udpsrcAudioProbeCount << endl;
                return GST_PAD_PROBE_DROP;
            }
        }
    }
    return GST_PAD_PROBE_OK;
}

static GstPadProbeReturn decodebin_audio_output_pad_cb(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
{
    GstUDPVideoClient* nvVideoClient = (GstUDPVideoClient*)user_data;
    if (nvVideoClient)
    {
        ++(nvVideoClient->m_audioDecoderProbeCount);
        if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER)
        {
            if (nvVideoClient->m_audioDecoderProbeCount <= GST_DEBUG_DECODER_PROBE_COUNT)
            {
                LOG(info) << " [audio-decoder-output-probe] audio decoder buffer: "
                    << nvVideoClient->m_audioDecoderProbeCount  << endl;
            }
            else
            {
                /* Remove probe, as we don't need it anymore */
                return GST_PAD_PROBE_REMOVE;
            }
        }
    }
    return GST_PAD_PROBE_OK;
}

static void on_pad_added_decodebinAudio (GstElement *element1, GstPad *pad, gpointer data)
{
    GstCaps *caps = nullptr;
    gchar *capsString = nullptr;

    caps = gst_pad_get_current_caps (pad);
    capsString = gst_caps_to_string (caps);
    LOG(info) << "on_pad_added_decodebinAudio Caps = " << capsString << endl;
    GstUDPVideoClient* udpClient = (GstUDPVideoClient*)(data);
    /* Try to link pads only if format is video */
    if (g_strrstr(capsString, "audio"))
	{
		GstPad *sink_pad = nullptr;
		GstElement *element2 = udpClient->m_queueAudioAfterDecode;
		sink_pad = gst_element_get_static_pad (element2, "sink");
		/* Check if sink_pad exists */
		if (!sink_pad) {
			LOG(error) << "on_pad_added_decodebinAudio, Failed to get sink pad of element." << endl;
			GST_ELEMENT_ERROR(element2, STREAM, FAILED, ("Failed to get sink pad of element."), (nullptr));
			return;
		}

		/* Check if pads can be linked */
		if (gst_pad_link (pad, sink_pad) != GST_PAD_LINK_OK) {
			LOG(error) << "on_pad_added_decodebinAudio, Failed to link elements in pad-added callback. = " << sink_pad << endl;
			GST_ELEMENT_ERROR(element2, STREAM, FAILED, ("Failed to link elements in pad-added callback"), (nullptr));
		}

		/* Unref the data structure */
		if (sink_pad) {
			gst_object_unref (sink_pad);
		}
	}

    if (GET_CONFIG().enable_gst_debug_probes)
    {
        gst_pad_add_probe(pad, GST_PAD_PROBE_TYPE_BUFFER, decodebin_audio_output_pad_cb, data, nullptr);
    }
}

GstFlowReturn GstUDPVideoClient::processNewAudioSampleFromSink (GstElement * appsink)
{
    struct timeval presentationTime;
    static struct timeval prevTs = {0};
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

    if (GST_BUFFER_PTS (gstBuffer) == GST_CLOCK_TIME_NONE)
    {
        // This could be non-data NALs, use prev timestamps.
        presentationTime = prevTs;
    }
    else
    {
        presentationTime.tv_sec = (GST_BUFFER_PTS (gstBuffer) / 1000000000);
        presentationTime.tv_usec = (GST_BUFFER_PTS (gstBuffer) % 1000000000) / 1000;
        prevTs = presentationTime;
    }

    /* Map the gst buffer */
    if (gst_buffer_map (gstBuffer, &map, GST_MAP_READ) == false)
    {
        LOG(error) << "Map the gst buffer Failed" << endl;
        return GST_FLOW_OK;
    }

    std::shared_ptr<IMediaDataConsumer> consumer = getConsumer(UdpClient::UDP_AUDIO_TYPE);
    if (consumer)
    {
        FrameParams frame_params;
        frame_params.m_media   = "audio";
        frame_params.m_codec   = "PCMU";
        frame_params.m_buffer  = map.data;
        frame_params.m_size    = map.size;
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
on_new_sample_from_sink (GstElement * appsink, GstUDPVideoClient* nvVideoUDPClient)
{
   if (nvVideoUDPClient)
    {
        return nvVideoUDPClient->processNewSampleFromSink(appsink);
    }
   return GST_FLOW_ERROR;
}

/* called when the appsink notifies us that there is a new buffer ready for
 * processing */
static GstFlowReturn
on_new_audio_sample_from_sink (GstElement * appsink, GstUDPVideoClient* nvVideoUDPClient)
{
   if (nvVideoUDPClient)
    {
        return nvVideoUDPClient->processNewAudioSampleFromSink(appsink);
    }
   return GST_FLOW_ERROR;
}

int GstUDPVideoClient::create_audio_pipeline ()
{
    LOG (info) << "Creating Gstreamer udp-audio" << endl;
    if (m_pipeline && m_sourceAudio)
    {
        LOG (warning) << "Gstreamer udp-audio pipeline is already created, returning" << endl;
        return 0;
    }
    if (m_pipeline)
    {
        gst_element_set_state (m_pipeline, GST_STATE_NULL);
        gst_element_get_state (m_pipeline, nullptr, nullptr, 5 * GST_SECOND);
    }

    /*
    ******  With Mux and Demux  ******
    m_sourceAudio -> m_rtpJitterBufferAudio -> m_rtpdepayAudio -> m_convAudio -> m_filterAudio -> m_mixerAudio -> m_encoderAudio -> m_queueAudio -> A

    A -> m_mux -> m_queueAfterMux -> m_demux -> m_queueAudioAfterDeMux -> m_capsAudioAfterDeMux -> m_decodebinAudio -> m_queueAudioAfterDecode -> m_sinkAudio

=====================================================================================================================================================================

    ******  Without Mux and Demux  ******
    m_sourceAudio -> m_rtpJitterBufferAudio -> m_rtpdepayAudio -> m_convAudio -> m_filterAudio -> m_encoderAudio -> m_queueAudio  -> m_capsAudioAfterDeMux -> B

    B -> m_decodebinAudio -> m_queueAudioAfterDecode -> m_sinkAudio
    */

    m_sourceAudio           = gst_element_factory_make ("udpsrc"          , "audio_udpsrc");
    m_rtpJitterBufferAudio  = gst_element_factory_make ("rtpjitterbuffer" , "audio_rtpjitterbuffer");
    m_rtpdepayAudio         = gst_element_factory_make ("rtpL16depay"     , nullptr);
    m_convAudio             = gst_element_factory_make ("audioconvert"    , nullptr);
    m_filterAudio           = gst_element_factory_make ("capsfilter"      , nullptr);
    m_encoderAudio          = gst_element_factory_make ("mulawenc"        , nullptr);
    m_queueAudio            = gst_element_factory_make ("queue"           , nullptr);
    m_queueAudioAfterDeMux  = gst_element_factory_make ("queue"           , nullptr);
    m_capsAudioAfterDeMux   = gst_element_factory_make ("capsfilter"      , nullptr);
    m_decodebinAudio        = gst_element_factory_make ("decodebin"       , "audio_decodebin");
    m_queueAudioAfterDecode = gst_element_factory_make ("queue"           , nullptr);
    m_identityAudio         = gst_element_factory_make ("identity"        , "audio_identity");
    m_sinkAudio             = gst_element_factory_make ("appsink"         , nullptr);

    if (GET_CONFIG().enable_silent_audio_in_udp_input)
    {
        m_mixerAudio = gst_element_factory_make ("audiomixer", "audio_mixer");
        if (!m_mixerAudio)
        {
            LOG (error) << "Gstreamer m_mixerAudio creation failed" << endl;
            return -1;
        }
    }

    /* Check if any of element failed to create */
    if (!m_sourceAudio || !m_rtpJitterBufferAudio || !m_rtpdepayAudio || !m_convAudio ||
        !m_filterAudio || !m_encoderAudio || !m_queueAudio || !m_queueAudioAfterDeMux ||
        !m_capsAudioAfterDeMux || !m_decodebinAudio || !m_queueAudioAfterDecode || !m_identityAudio || !m_sinkAudio)
    {
        LOG (error) << "Gstreamer audio element creation failed" << endl;
        return -1;
    }

    /* Add Elements in pipeline */
    if (GET_CONFIG().enable_silent_audio_in_udp_input)
    {
        gst_bin_add_many (GST_BIN (m_pipeline), m_sourceAudio, m_rtpJitterBufferAudio, m_rtpdepayAudio, m_convAudio, m_queueAudioAfterDeMux,
                                            m_capsAudioAfterDeMux, m_decodebinAudio,m_queueAudioAfterDecode, m_filterAudio, 
                                            m_encoderAudio, m_queueAudio, m_identityAudio, m_sinkAudio, m_mixerAudio, nullptr);
    }
    else
    {
        gst_bin_add_many (GST_BIN (m_pipeline), m_sourceAudio, m_rtpJitterBufferAudio, m_rtpdepayAudio, m_convAudio, m_queueAudioAfterDeMux,
                                            m_capsAudioAfterDeMux, m_decodebinAudio,m_queueAudioAfterDecode, m_filterAudio,
                                            m_encoderAudio, m_queueAudio, m_identityAudio, m_sinkAudio, nullptr);
    }



    std::string caps_string = "application/x-rtp,clock-rate=(int)" + to_string(getAudioFreq());
    GstCaps *caps = gst_caps_from_string (caps_string.c_str());
    LOG(info) << "GstUDPAudioClient udpsrc caps = " << caps_string << endl;

    g_object_set(m_sourceAudio, "port",        getAudioPort(), nullptr);
    g_object_set(m_sourceAudio, "reuse", TRUE, nullptr);
    g_object_set(m_sourceAudio, "buffer-size", 2000000,   nullptr);
    g_object_set(m_sourceAudio, "caps",        caps,      nullptr);

    g_object_set(m_rtpJitterBufferAudio, "drop-on-latency", GET_CONFIG().udp_drop_on_latency, nullptr);
    int latency = GET_CONFIG().udp_latency_ms;
    if (latency < 20)
    {
        /* Minimum latency has to be 20ms */
        latency = 200;
    }
    LOG(info) << "udp-audio jitter buffer latency: " << latency << ", drop-on-latency: " << GET_CONFIG().udp_drop_on_latency << endl;
    g_object_set(m_rtpjitterbuffer, "latency", latency, nullptr);

    caps = gst_caps_from_string("audio/x-raw, channels=1");
    g_object_set(m_filterAudio, "caps", caps, nullptr);
    gst_caps_unref (caps);

    /* Drop downstream i.e old buffers in case of full */
    g_object_set(m_queueAudio, "leaky", 2, nullptr);
    g_object_set(m_queueAudioAfterDeMux, "leaky", 2, nullptr);
    g_object_set(m_queueAudioAfterDecode, "leaky", 2, nullptr);

    /* Link Elements in pipeline */
    if (gst_element_link_many(m_sourceAudio, m_rtpJitterBufferAudio, m_rtpdepayAudio, m_convAudio, m_filterAudio, nullptr) != TRUE)
    {
        LOG (error) << "Many elements could not be linked." << endl;
        gst_object_unref(m_pipeline);
        return -1;
    }

    /* Link Elements in pipeline */
    if (GET_CONFIG().enable_avsync_udp_input)
    {
        if (GET_CONFIG().enable_silent_audio_in_udp_input)
        {
            if (gst_element_link_many(m_mixerAudio, m_encoderAudio, m_queueAudio, nullptr) != TRUE)
            {
                LOG (error) << "Many elements could not be linked." << endl;
                gst_object_unref(m_pipeline);
                return -1;
            }
        }
        else
        {
            if (gst_element_link_many(m_filterAudio, m_encoderAudio, m_queueAudio, nullptr) != TRUE)
            {
                LOG (error) << "Many elements could not be linked." << endl;
                gst_object_unref(m_pipeline);
                return -1;
            }
        }
    }
    else
    {
        if (gst_element_link_many(m_filterAudio, m_encoderAudio, m_queueAudio, nullptr) != TRUE)
        {
            LOG (error) << "Many elements could not be linked." << endl;
            gst_object_unref(m_pipeline);
            return -1;
        }
    }

    /* Add signal to get the buffers from app sink element */
    g_object_set (G_OBJECT (m_sinkAudio), "emit-signals", TRUE, "sync", TRUE, nullptr);

    if(!g_signal_connect (m_sinkAudio, "new-sample", G_CALLBACK (on_new_audio_sample_from_sink), this))
    {
        LOG(error) << "Error in g_signal_connect of new-sample" << endl;
        return -1;
    }

    GstPad *sink_pad, *src_pad;

    if (GET_CONFIG().enable_avsync_udp_input)
    {
        if (GET_CONFIG().enable_silent_audio_in_udp_input)
        {
            src_pad = gst_element_get_static_pad (m_filterAudio, "src");
            sink_pad = gst_element_request_pad_simple (m_mixerAudio, "sink_%u");
            if (GST_PAD_LINK_OK != gst_pad_link (src_pad, sink_pad))
            {
                LOG(error) << "Could not link Audio Filter & Mixer\n" << endl;
                /* Unref the data structure */
                if (sink_pad)
                {
                    gst_object_unref (sink_pad);
                }
                if (src_pad)
                {
                    gst_object_unref (src_pad);
                }
                return -1;
            }
        }

        src_pad = gst_element_get_static_pad (m_queueAudio, "src");
        sink_pad = gst_element_request_pad_simple (m_mux, "audio_%u");
        if (GST_PAD_LINK_OK != gst_pad_link (src_pad, sink_pad)) 
        {
            LOG(error) << "Could not link Audio Queue & Muxer\n" << endl;
            /* Unref the data structure */
            if (sink_pad)
            {
                gst_object_unref (sink_pad);
            }
            if (src_pad)
            {
                gst_object_unref (src_pad);
            }
            return -1;
        }
        if (!gst_element_link_many (m_queueAudioAfterDeMux, m_capsAudioAfterDeMux, m_decodebinAudio, nullptr))
        {
            LOG (error) << "Elements could not be linked" << endl;
            return -1;
        }
    }
    else
    {
        if (!gst_element_link_many (m_queueAudio, m_capsAudioAfterDeMux, m_decodebinAudio, nullptr))
        {
            LOG (error) << "Elements could not be linked" << endl;
            return -1;
        }      
    }
    caps_string = "audio/x-mulaw, format=(string)S8, rate=(int)" + to_string(getAudioFreq()) + 
                            ", channels=(int)" + to_string(1);
    GstCaps* capsSrc = gst_caps_from_string (caps_string.c_str());
    g_object_set (G_OBJECT (m_capsAudioAfterDeMux), "caps", capsSrc, nullptr);
    gst_caps_unref (capsSrc);

    if (GET_CONFIG().enable_gst_debug_probes)
    {
        GstPad* udpsrc_output_pad = nullptr;
        udpsrc_output_pad = gst_element_get_static_pad (GST_ELEMENT(m_encoderAudio), "src");
        if (!udpsrc_output_pad)
        {
            LOG(error) << "Failed to get src pad of udpsrc" << endl;
        }
        gst_pad_add_probe(udpsrc_output_pad, GST_PAD_PROBE_TYPE_BUFFER, udpsrc_audio_output_pad_cb, this, nullptr);
        gst_object_unref(udpsrc_output_pad);
    }

    if (!g_signal_connect (G_OBJECT (m_decodebinAudio), "pad-added", G_CALLBACK (on_pad_added_decodebinAudio), (void*)this))
    {
        std::cout << "Error in g_signal_connect of pad-added" << endl;
        return -1;
    }
#ifdef DEBUG
    g_object_set (G_OBJECT (m_identityAudio), "silent", false, nullptr);
    if (!gst_element_link_many (m_queueAudioAfterDecode, m_identityAudio, m_sinkAudio, nullptr))
    {
        LOG (error) << "Elements could not be linked" << endl;
        return -1;
    }
#else
    if (!gst_element_link_many (m_queueAudioAfterDecode, m_sinkAudio, nullptr))
    {
        LOG (error) << "Elements could not be linked" << endl;
        return -1;
    }
#endif
    LOG (info) << "Created Gstreamer udp-audio pipeline for port:" << getAudioPort() << endl;
    return 0;
}

long getTimevalDiff(struct timeval& starttime, struct timeval& endtime)
{
    long usec;
    if (starttime.tv_sec > endtime.tv_sec ||
           (starttime.tv_sec == endtime.tv_sec && starttime.tv_usec > endtime.tv_usec))
    {
        return 0;
    }
    usec = ((endtime.tv_sec * 1000000) + (endtime.tv_usec)) -
                ((starttime.tv_sec * 1000000) + (starttime.tv_usec));
	return usec;
}

static GstPadProbeReturn queue_after_mux_output_pad_cb (GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
{
    GstUDPVideoClient* nvVideoClient = (GstUDPVideoClient*)user_data;
    if (nvVideoClient)
    {
        GstBuffer *buffer;
        buffer = GST_PAD_PROBE_INFO_BUFFER (info);
        GstMapInfo map_info;

        // Map the buffer to get access to the data
        if (gst_buffer_map(buffer, &map_info, GST_MAP_READ))
        {
            if (nvVideoClient->m_dumpFile)
            {
                // Write the data to the file
                fwrite(map_info.data, 1, map_info.size, nvVideoClient->m_dumpFile);
            }
            // Unmap the buffer to release resources
            gst_buffer_unmap(buffer, &map_info);
        }
    }
    return GST_PAD_PROBE_OK;
}

static GstPadProbeReturn udpsrc_video_output_pad_cb(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
{
    GstBuffer *gstbuffer;
    GstUDPVideoClient* nvVideoClient = (GstUDPVideoClient*)user_data;
    if (nvVideoClient)
    {
        if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER)
        {
            gettimeofday(&nvVideoClient->m_videoTime, nullptr);
            if (nvVideoClient->m_udpsrcVideoProbeCount == 0)
            {
                LOG(info) << " [udpsrc-video-output-probe]" << endl;
            }
            ++(nvVideoClient->m_udpsrcVideoProbeCount);
            if (nvVideoClient->m_udpsrcVideoProbeCount > GST_DEBUG_UDPSRC_PROBE_COUNT)
            {
                // Check and log udp data every fixed interval video frames.
                nvVideoClient->m_udpsrcVideoProbeCount = 0;
            }
            ++(nvVideoClient->m_udpsrcVideoFrameCount);
            if (nvVideoClient->m_udpsrcVideoFrameCount > 10)
            {
                nvVideoClient->m_videoDataReceived = true;
            }
            gstbuffer = GST_PAD_PROBE_INFO_BUFFER (info);
            if (gstbuffer != nullptr)
            {
                if (GST_BUFFER_PTS (gstbuffer) != GST_CLOCK_TIME_NONE)
                {
                    nvVideoClient->displayFPS(GST_BUFFER_PTS (gstbuffer)/1000000, to_string(nvVideoClient->getVideoPort()));
                }
                /* Add VST metadata to Gst Buffer */
                GstNvVstMeta *meta = GST_NV_VST_META_ADD (gstbuffer);
                if (meta && GET_CONFIG().enable_latency_logging)
                {
                    meta->pts = GST_BUFFER_PTS (gstbuffer);
                    meta->id = nvVideoClient->m_udpsrcVideoProbeCount;
                    meta->ts = std::chrono::duration_cast<std::chrono::microseconds>
                        (std::chrono::system_clock::now().time_since_epoch()).count();
                }
            }
        }
    }
    return GST_PAD_PROBE_OK;
}

static void on_pad_added_decodebin (GstElement *element, GstPad *pad, void *data)
{
    GstElement *element2 = (GstElement *)data;
    GstPad *sink_pad = nullptr;
    GstCaps *caps = nullptr;
    gchar *capsString = nullptr;

    caps = gst_pad_get_current_caps (pad);
    capsString = gst_caps_to_string (caps);
    sink_pad = gst_element_get_static_pad (element2, "sink");
    LOG (info) << "Caps = " << capsString << endl;

    /* Try to link pads only if format is video */
    if (g_strrstr(capsString, "video"))
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
        if (!g_signal_connect (decoder, "pad-added", G_CALLBACK (on_pad_added_decodebin), element))
        {
            return false;
        }
    }
    return true;
}

int GstUDPVideoClient::create_internal ()
{
    LOG (info) << "Creating Gstreamer udp-video on port: " << m_id << endl;
    if (gst_is_initialized() == false)
    {
        gst_init (nullptr, nullptr);
    }
    GstCaps* filtercaps;
    bool use_video_convert = !NvHwDetection::getInstance()->m_useNvV4l2Enc &&
                            NvHwDetection::getInstance()->m_useNvV4l2Dec;
    // TODO: remove below as handled within NvHWDetection and check all usecases
    if (use_video_convert == false)
    {
        /* Video_convert is needed in case user selects software encoder */
        use_video_convert = GET_CONFIG().use_webrtc_inbuilt_encoder.empty() ? false : true;
    }

    /*
    ******  With Mux and Demux  ******
    m_source -> m_rtpjitterbuffer -> m_rtpdepay -> m_parserBeforeMux -> m_queueVideo -> m_filter -> A

    A -> m_mux -> m_queueAfterMux -> m_demux -> m_queueVideoAfterDeMux -> decodeBin -> m_queueVideoAfterDecode -> m_sinkVideo

=====================================================================================================================================================================

    ******  Without Mux and Demux  ******
    m_source -> m_rtpjitterbuffer -> m_rtpdepay -> m_parserBeforeMux -> m_queueVideo -> m_filter -> decodeBin -> m_queueVideoAfterDecode -> m_sinkVideo
    */

    m_pipeline              = gst_pipeline_new         ("GstUDPClient_pipeline");
    m_source                = gst_element_factory_make ("udpsrc", "video_udpsrc");
    m_filter                = gst_element_factory_make ("capsfilter", nullptr);
    m_rtpjitterbuffer       = gst_element_factory_make ("rtpjitterbuffer", "video_rtpjitterbuffer");
    m_rtpdepay              = gst_element_factory_make ("rtph264depay", nullptr);
    m_parserBeforeMux       = gst_element_factory_make ("h264parse", nullptr);
    m_queueVideo            = gst_element_factory_make ("queue"       , nullptr);
    m_mux                   = gst_element_factory_make ("matroskamux", nullptr);
    m_queueAfterMux         = gst_element_factory_make ("queue", nullptr);
    m_demux                 = gst_element_factory_make ("matroskademux", nullptr);
    m_queueVideoAfterDeMux  = gst_element_factory_make ("queue", nullptr);
    m_queueVideoAfterDecode = gst_element_factory_make ("queue", nullptr);
    m_identityVideo         = gst_element_factory_make ("identity", "video_identity");
    m_sink                  = gst_element_factory_make ("appsink", nullptr);

    /* Check if any of element failed to create */
    if (!m_pipeline || !m_source || !m_rtpjitterbuffer || !m_rtpdepay || !m_sink || !m_filter || !m_queueVideoAfterDeMux || 
        !m_queueVideoAfterDecode || !m_identityVideo|| !m_parserBeforeMux || !m_queueVideo)
    {
        LOG (error) << "Gstreamer video element creation failed" << endl;
        return -1;
    }

    m_nvDecodeBin.reset(new NvDecodeBin(this, getVideoCodec()));
    GstElement* decodeBin = m_nvDecodeBin->create(false);
    if (!decodeBin)
    {
        LOG (error) << "Gstreamer element m_decodeBin creation failed" << endl;
        return -1;
    }

    if (use_video_convert)
    {
        m_videoConverter = gst_element_factory_make ("nvvideoconvert", nullptr);
        g_object_set (G_OBJECT (m_videoConverter), "gpu-id", g_gpuIndex, nullptr);
        m_convCapsFilter    = gst_element_factory_make ("capsfilter", nullptr);
        if (!m_videoConverter || !m_convCapsFilter)
        {
            LOG (error) << "Gstreamer element video converter creation failed" << endl;
            return -1;
        }
        filtercaps = gst_caps_new_simple ("video/x-raw", "format", G_TYPE_STRING, "I420", nullptr);
        g_object_set (G_OBJECT (m_convCapsFilter), "caps", filtercaps, nullptr);
        gst_caps_unref (filtercaps);
    }

    /* Check if any of element failed to create */
    if (!m_mux || !m_queueAfterMux || !m_demux)
    {
        LOG (error) << "Gstreamer common element creation failed" << endl;
        return -1;

    }

    /* Setting properties of elements */
    g_object_set(GST_BIN(m_pipeline), "message-forward", TRUE, nullptr);
    g_object_set(m_source, "port", getVideoPort(), nullptr);
    g_object_set(m_source, "reuse", TRUE, nullptr);
    g_object_set(m_source, "buffer-size", UDP_BUFFER_SIZE, nullptr);

    GstCaps *caps = gst_caps_from_string("application/x-rtp");
    g_object_set(m_source, "caps", caps, nullptr);

    g_object_set(m_rtpjitterbuffer, "drop-on-latency", GET_CONFIG().udp_drop_on_latency, nullptr);
    int latency = GET_CONFIG().udp_latency_ms;
    if (latency < 20)
    {
        /* Minimum latency has to be 20ms */
        latency = 200;
    }
    LOG(info) << "udp-video jitter buffer latency: " << latency << ", drop-on-latency: " << GET_CONFIG().udp_drop_on_latency << endl;
    g_object_set(m_rtpjitterbuffer, "latency", latency, nullptr);

    /* stream-format=avc is required for Mux and Demux */
    GstCaps *caps_parser = gst_caps_from_string("video/x-h264, stream-format=avc, alignment=au");
    g_object_set(m_filter, "caps", caps_parser, nullptr);
    gst_caps_unref (caps_parser);

    /* Drop downstream i.e old buffers in case of full */
    g_object_set(m_queueVideo, "leaky", 2, nullptr);
    g_object_set(m_queueVideoAfterDeMux, "leaky", 2, nullptr);
    g_object_set(m_queueVideoAfterDecode, "leaky", 2, nullptr);

    m_bus = gst_pipeline_get_bus (GST_PIPELINE (m_pipeline));
    if (!m_bus)
    {
        LOG(error) << "Failed to get BUS of UDP Video pipeline" << endl;
    }
    m_bus_watch_id = gst_bus_add_watch (m_bus, udpVideoBusWatchFunc, (void*)this);

    if (use_video_convert)
    {
        /* Add Elements in pipeline */
        gst_bin_add_many (GST_BIN (m_pipeline), m_source, m_rtpjitterbuffer, m_rtpdepay, m_parserBeforeMux, m_queueVideo,
                            m_filter, m_identityVideo, m_sink, m_mux, m_demux, m_queueVideoAfterDeMux, decodeBin,
                            m_videoConverter, m_convCapsFilter, m_queueAfterMux, m_queueVideoAfterDecode, nullptr);
    }
    else
    {
        /* Add Elements in pipeline */
        gst_bin_add_many (GST_BIN (m_pipeline), m_source, m_rtpjitterbuffer, m_rtpdepay, m_parserBeforeMux, m_queueVideo,
                            m_filter, m_identityVideo, m_sink, m_mux, m_demux, m_queueVideoAfterDeMux, decodeBin,
                            m_queueAfterMux, m_queueVideoAfterDecode, nullptr);
    }

    /* Link Elements in pipeline */
    if (gst_element_link_many(m_source, m_rtpjitterbuffer, m_rtpdepay, m_parserBeforeMux, m_queueVideo, m_filter, nullptr) != TRUE)
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

    GstPad *sink_pad = nullptr, *src_pad = nullptr;
    if (GET_CONFIG().enable_avsync_udp_input)
    {
        src_pad = gst_element_get_static_pad (m_filter, "src");
        sink_pad = gst_element_request_pad_simple (m_mux, "video_%u");
        if (GST_PAD_LINK_OK != gst_pad_link (src_pad, sink_pad)) 
        {
            LOG(error) << "Could not link Video parser & Muxer\n" << endl;
            /* Unref the data structure */
            if (sink_pad)
            {
                gst_object_unref (sink_pad);
            }
            if (src_pad)
            {
                gst_object_unref (src_pad);
            }
            return -1;
        }
    }

    sink_pad = gst_element_get_static_pad (m_queueVideoAfterDecode, "sink");
    /* Check if sink_pad exists */
    if (!sink_pad)
    {
        LOG(error) << "Failed to get sink pad of m_sink" << endl;
        return -1;
    }
    /* Add probe to query width and height of video stream */
    gst_pad_add_probe(sink_pad, GST_PAD_PROBE_TYPE_EVENT_BOTH, pad_cb, (void*)this, nullptr);

    if (GET_CONFIG().enable_gst_debug_probes)
    {
        GstPad* udpsrc_output_pad = nullptr;
        udpsrc_output_pad = gst_element_get_static_pad (GST_ELEMENT(m_rtpdepay), "src");
        if (!udpsrc_output_pad)
        {
            LOG(error) << "Failed to get src pad of udpsrc" << endl;
        }
        gst_pad_add_probe(udpsrc_output_pad, GST_PAD_PROBE_TYPE_BUFFER, udpsrc_video_output_pad_cb, this, nullptr);
        gst_object_unref(udpsrc_output_pad);
    }

    if (GET_CONFIG().enable_avsync_udp_input)
    {
        if (!gst_element_link_many (m_mux, m_queueAfterMux, nullptr))
        {
            LOG (error) << "Elements could not be linked" << endl;
            return -1;
        }
        if (GET_CONFIG().enable_udp_input_dump)
        {
            GstPad *src_pad = nullptr;
            src_pad = gst_element_get_static_pad (m_queueAfterMux, "src");
            gst_pad_add_probe(src_pad, GST_PAD_PROBE_TYPE_BUFFER, queue_after_mux_output_pad_cb, this, nullptr);
            gst_object_unref (src_pad);
        }
    }

    /* Unref the data structure */
    if (sink_pad)
    {
        gst_object_unref (sink_pad);
    }

    if (src_pad)
    {
        gst_object_unref (src_pad);
    }

    if (GET_CONFIG().enable_avsync_udp_input)
    {
        if (!gst_element_link_many (m_queueAfterMux, m_demux, nullptr))
        {
            LOG (error) << "Elements could not be linked" << endl;
            return -1;
        }
    
        if (!g_signal_connect (G_OBJECT (m_demux), "pad-added", G_CALLBACK (on_pad_added), (void*)this))
        {
            std::cout << "Error in g_signal_connect of pad-added" << endl;
            return -1;
        }
    
        if (!gst_element_link_many (m_queueVideoAfterDeMux, decodeBin, nullptr))
        {
            LOG (error) << "Elements could not be linked" << endl;
            return -1;
        }
    }
    else
    {
        if (!gst_element_link_many (m_filter, decodeBin, nullptr))
        {
            LOG (error) << "Elements could not be linked" << endl;
            return -1;
        }
    }

    if (!link_decoder(decodeBin, m_queueVideoAfterDecode))
    {
        LOG (error) << "decodebin->queue Elements could not be linked" << endl;
        return -1;
    }

#ifdef DEBUG
    g_signal_connect( m_pipeline, "deep-notify", G_CALLBACK( gst_object_default_deep_notify ), nullptr );
    g_object_set (G_OBJECT (m_identityVideo), "silent", false, nullptr);
    if (!gst_element_link_many (m_identityVideo, m_sink, nullptr))
    {
        LOG (error) << "Elements could not be linked" << endl;
        return -1;
    }
#else
    if (use_video_convert)
    {
        if (!gst_element_link_many (m_queueVideoAfterDecode, m_videoConverter, m_convCapsFilter, m_sink, nullptr))
        {
            LOG (error) << "Elements could not be linked" << endl;
            return -1;
        }
    }
    else
    {
        if (!gst_element_link_many (m_queueVideoAfterDecode, m_sink, nullptr))
        {
            LOG (error) << "Elements could not be linked" << endl;
            return -1;
        }
    }
#endif
    LOG (info) << "Created Gstreamer udp-video pipeline for port:" << getVideoPort() << " AV sync = " << GET_CONFIG().enable_avsync_udp_input << endl;
    return 0;
}

void GstUDPVideoClient::checkVideoDataFlowStatus()
{
    if(m_videoDataFlowing.load() == false)
    {
        // Reset only if playback was ever started.
        if (m_videoFirstFrameOut.load())
        {
            LOG(info) << "Video data flow is stalled, attempt rest pipeline " << endl;
            reset();
        }
    }
    else
    {
        m_videoDataFlowing = false;
    }
}

void GstUDPVideoClient::play_internal ()
{
    LOG (info) << "Play Gstreamer GstUDPVideoClient pipeline" << endl;
    gst_element_set_state (m_pipeline, GST_STATE_PLAYING);
    LOG (info) << "Exit - play Gstreamer GstUDPVideoClient pipeline" << endl;   

    m_videoDataWatchDog = make_unique<Bosma::Scheduler>(1);
    m_videoDataWatchDog->interval(VIDEO_DATA_WATCH_DOG_SCHEDULER_INTERVAL, [=]() {
        checkVideoDataFlowStatus();
    });
}

bool GstUDPVideoClient::pause_internal()
{
    bool ret = true;
    if (m_pipeline)
    {
        LOG (info) << "Pausing the pipeline, port:" << m_id << endl;
        GstStateChangeReturn gstStateChangeRet = gst_element_set_state (m_pipeline, GST_STATE_PAUSED);
        if (gstStateChangeRet == GST_STATE_CHANGE_FAILURE)
        {
            LOG (error) << "gst_element_set_state failed. " << endl;
            ret = false;
        }
        else
        {
            gst_element_get_state (m_pipeline, nullptr, nullptr, GST_SECOND);
            LOG (info) << "State change success " << endl;
        }
        m_udpsrcVideoProbeCount = 0;
        m_udpsrcAudioProbeCount = 0;
        m_audioDecoderProbeCount = 0;
        m_videoDecoderProbeCount = 0;
    }
    return ret;
}

void GstUDPVideoClient::resume_internal ()
{
    LOG (info) << "Resume Gstreamer udp-video pipeline" << endl;
    if (m_pipeline)
    {
        gst_element_set_state (m_pipeline, GST_STATE_PLAYING);
    }
}

void GstUDPVideoClient::reset_pipeline_internal ()
{
    if (m_pipeline)
    {
        LOG(info) << "***** reseting the pipeline ******" << endl;
        gst_element_set_state (m_pipeline, GST_STATE_NULL);
        gst_element_get_state (m_pipeline, nullptr, nullptr, 5 * GST_SECOND);
        gst_element_set_state (m_pipeline, GST_STATE_PLAYING);
    }
}

void GstUDPVideoClient::destroy_internal ()
{
    GstStateChangeReturn state_change;
    LOG(info) << "Terminating gstreamer udp-video pipeline port:" << m_id << endl;
    m_videoDataWatchDog.reset();
    if (m_pipeline == nullptr)
    {
        return;
    }

    /* Forcefully close the port in cases of hang */
    if (m_source)
    {
        gst_element_set_state (m_source, GST_STATE_NULL);
        state_change = gst_element_get_state(m_source, nullptr, nullptr, 5 * GST_SECOND);
        if (state_change == GST_STATE_CHANGE_SUCCESS)
        {
            gst_bin_remove(GST_BIN(m_pipeline), m_source);
        }
    }
    if (m_sourceAudio)
    {
        gst_element_set_state (m_sourceAudio, GST_STATE_NULL);
        state_change = gst_element_get_state(m_sourceAudio, nullptr, nullptr, 5 * GST_SECOND);
        if (state_change == GST_STATE_CHANGE_SUCCESS)
        {
            gst_bin_remove(GST_BIN(m_pipeline), m_sourceAudio);
        }
    }
    if (m_pipeline)
    {
        gst_element_set_state (m_pipeline, GST_STATE_NULL);
        gst_element_get_state (m_pipeline, nullptr, nullptr, 5 * GST_SECOND);
        gst_object_unref (m_pipeline);
        m_pipeline = nullptr;
    }
    if (m_bus_watch_id != G_MAXUINT)
    {
        g_source_remove (m_bus_watch_id);
        m_bus_watch_id = G_MAXUINT;
    }
    if (m_bus)
    {
        gst_object_unref (m_bus);
        m_bus = nullptr;
    }
    m_videoDataReceived = false;
    LOG(info) << "Terminated gstreamer udp-video pipeline port:" << m_id << endl;
}

int GstUDPVideoClient::create()
{
    m_eventLoop.setParent(this);
    std::shared_ptr<EventLoopData> data(new EventLoopData);
    data->m_taskName = "create";
    m_eventLoop.postMsg(data);
    return 0;
}

int GstUDPVideoClient::create_audio()
{
    m_eventLoop.setParent(this);
    std::shared_ptr<EventLoopData> data(new EventLoopData);
    data->m_taskName = "create_audio";
    m_eventLoop.postMsg(data);
    return 0;
}

void GstUDPVideoClient::start()
{
    m_eventLoop.setParent(this);
    std::shared_ptr<EventLoopData> data(new EventLoopData);
    data->m_taskName = "play";
    m_eventLoop.postMsg(data);
    return;
}

void GstUDPVideoClient::pause()
{
    std::shared_ptr<EventLoopData> data(new EventLoopData);
    std::shared_ptr<EventLoopOutData> out_data (new EventLoopOutData);
    data->m_taskName = "pause";
    /* At the time of application exit, we are encountering issues
    ** with closing the pipeline, so wait till pipeline transitions into
    ** PAUSE state (caller:~NvGstUDPVideoSource) then destroy the pipeline */
    data->m_outResult = out_data;
    data->m_expectResult = true;
    m_eventLoop.postMsg(data);
    return;
}

void GstUDPVideoClient::resume()
{
    std::shared_ptr<EventLoopData> data(new EventLoopData);
    data->m_taskName = "resume";
    m_eventLoop.postMsg(data);
    return;
}

void GstUDPVideoClient::reset()
{
    std::shared_ptr<EventLoopData> data(new EventLoopData);
    data->m_taskName = "reset";
    m_eventLoop.postMsg(data);
    return;
}

void GstUDPVideoClient::destroy(bool expect_result)
{
    std::shared_ptr<EventLoopData> in_data(new EventLoopData);
    std::shared_ptr<EventLoopOutData> out_data (new EventLoopOutData);
    in_data->m_taskName = "destroy";
    in_data->m_outResult = out_data;
    in_data->m_expectResult = expect_result;
    m_eventLoop.postMsg(in_data);
    return;
}

void GstUDPVideoClient::process_eventloop_message(std::shared_ptr<EventLoopData> data, void* parent)
{
    shared_ptr<EventLoopData> ev_data = std::static_pointer_cast<EventLoopData>(data);
    GstUDPVideoClient* udpClient = static_cast <GstUDPVideoClient*>(parent);
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
    else if (ev_data->m_taskName == "create_audio")
    {
        udpClient->create_audio_pipeline();
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
    else if (ev_data->m_taskName == "reset")
    {
        udpClient->reset_pipeline_internal();
    }
    else
    {
        LOG(warning) << "Invalid action" << endl;
    }
}
