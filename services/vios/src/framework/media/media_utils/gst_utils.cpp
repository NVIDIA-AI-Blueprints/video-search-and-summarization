/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <cstdio>
#include <cstdlib>
#include <memory>
#include <sstream>
#include <unistd.h>
#include "gst_utils.h"
#include "gstnvvstmeta.h"
#include <gst/app/gstappsink.h>
#include "storage_management.h"
#include "libasync++/async++.h"
#include "mm_utils.h"
#include "nvhwdetection.h"
#include "modules_apis.h"

constexpr int DEFAULT_EACH_FILE_SIZE_MB = 100;
constexpr int MAX_FRAME_COUNT_LIMIT = 500;
constexpr int MAX_TOLERABLE_IDR_FRAME_SIZE = 600000;

using namespace std;

// HW Encoder unsupported profiles
// TODO-MB: Need to verify if these are supported on Orin
std::map<std::string, std::string, std::less<>> videoProfiles{
    {"constrained-baseline", "baseline"},
    {"constrained-high", "high"},
    {"main-12", "main"}
};

// H264 SW Encoder unsupported profiles
std::map<std::string, std::string, std::less<>> videoProfilesx264{
    {"constrained-high", "high"},
    {"main-12", "main"}
};

// H265 SW Encoder unsupported profiles
// std::map<std::string, std::string> videoProfilesx265{
//     {"constrained-baseline", "baseline"},
//     {"constrained-high", "high"}
// };

// Map of pair of resolution, frame rate and level
std::multimap<std::pair<int, int>, string> H264Levels{
    {{HEIGHT_144p, 15}, "1"}, {{HEIGHT_144p, 30}, "1.1"}, {{HEIGHT_144p, 60}, "1.2"},
    {{HEIGHT_240p, 10}, "1.1"}, {{HEIGHT_240p, 20}, "1.2"}, {{HEIGHT_240p, 36}, "1.3"},
    {{HEIGHT_480p, 15}, "2.2"}, {{HEIGHT_480p, 30}, "3"}, {{HEIGHT_480p, 80}, "3.1"},
    {{HEIGHT_720p, 30}, "3.1"}, {{HEIGHT_720p, 60}, "3.2"}, {{HEIGHT_720p, 70}, "4"}, {{HEIGHT_720p, 145}, "4.2"},
    {{HEIGHT_1080p, 30}, "4"}, {{HEIGHT_1080p, 60}, "4.2"}, {{HEIGHT_1080p, 120}, "5.1"},
    {{HEIGHT_1080p, 180}, "5.2"}, {{HEIGHT_2160p, 30}, "5.1"}, {{HEIGHT_2160p, 60}, "5.2"},
    {{HEIGHT_2160p, 128}, "6"}, {{HEIGHT_2160p, 258}, "6.1"}, {{HEIGHT_2160p, 300}, "6.2"}
};

// Map of pair of resolution, frame rate and level
// std::multimap<std::pair<int, int>, string> H265Levels{
//     {{HEIGHT_480p, 30}, "2.1"}, {{HEIGHT_480p, 60}, "3"},
//     {{HEIGHT_1080p, 30}, "4"}, {{HEIGHT_1080p, 60}, "4.1"}, {{HEIGHT_1080p, 128}, "5"},
//     {{HEIGHT_1080p, 256}, "5.1"}, {{HEIGHT_1080p, 300}, "5.2"}
// };

struct MuxPipelineData
{
    GstClockTime timestamp = 0;
    gint fps_num = 1;
    gint fps_den = 0;
};
namespace
{
gboolean udpBusWatchFunc (GstBus *bus, GstMessage *message, gpointer data)
{
    gstElements* elements = (gstElements*)data;
    GError *error = nullptr;
    gchar *name, *debug = nullptr;
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
                if (elements)
                {
                    if (elements->m_playInLoop == true)
                    {
                        LOG(info) << "Re-playing udp pipeline" << endl;
                        gst_element_seek_simple (elements->m_pipeline, GST_FORMAT_TIME,
                                            (GstSeekFlags)(GST_SEEK_FLAG_FLUSH), 0);
                    }
                }
            }
        }
        else
        {
            LOG (info) << "No message on Gstreamer Bus" << endl;
        }
    }
    return TRUE;
}

void cb_udp_pad_added (GstElement *src, GstPad *new_pad, gstElements* elements)
{
    LOG(info) << "Received new pad " << GST_PAD_NAME (new_pad) << " from " << GST_ELEMENT_NAME (src) << endl;

    GstPad *sink_pad = nullptr;
    GstCaps *caps = gst_pad_get_current_caps (new_pad);
    gchar *capsString = gst_caps_to_string (caps);

    if (g_str_has_prefix(capsString, "audio"))
    {
        sink_pad = gst_element_get_static_pad (elements->m_audioQueue, "sink");
    }
    else if (g_str_has_prefix(capsString, "video"))
    {
        sink_pad = gst_element_get_static_pad (elements->m_videoQueue, "sink");
    }
    else
    {
        LOG(error) << "Got un-supported caps: " << capsString << endl;
    }

    if (new_pad && sink_pad)
    {
        if (gst_pad_link (new_pad, sink_pad) != GST_PAD_LINK_OK)
        {
            LOG(error) << "Unable to link new pad" << endl;
        }
        gst_object_unref (sink_pad);
    }
    else
    {
        LOG(error) << "sink pad is NULL" << endl;
    }

    if (caps != nullptr)
    {
        gst_caps_unref (caps);
    }
    g_free(capsString);
}

void cb_on_pad_added (GstElement *src, GstPad *new_pad, GstElement* sink_element)
{
    LOG(verbose) << "Received new pad " << GST_PAD_NAME (new_pad) << " from " << GST_ELEMENT_NAME (src) << endl;

    GstPad *sink_pad = nullptr;
    GstCaps *caps = gst_pad_get_current_caps (new_pad);
    gchar *capsString = gst_caps_to_string (caps);

    if (g_str_has_prefix(capsString, "audio"))
    {
        LOG(verbose) << "Skipping audio" << endl;
        gst_caps_unref (caps);
        return;
    }
    else if (g_str_has_prefix(capsString, "video"))
    {
        gchar *sink_element_name = GST_ELEMENT_NAME (sink_element);
        if (g_str_has_prefix(sink_element_name, "matroska"))
        {
            sink_pad = gst_element_request_pad_simple (sink_element, "video_%u");
        }
        else if (g_str_has_prefix(sink_element_name, "identity"))
        {
            sink_pad = gst_element_get_static_pad (sink_element, "sink");
        }
    }
    else
    {
        LOG(error) << "Got un-supported caps: " << capsString << endl;
    }

    if (sink_pad)
    {
        if (gst_pad_link (new_pad, sink_pad) != GST_PAD_LINK_OK)
        {
            LOG(error) << "Unable to link new pad" << endl;
        }
        gst_object_unref (sink_pad);
    }
    else
    {
        LOG(error) << "sink pad is NULL" << endl;
    }

    if (caps != nullptr)
    {
        gst_caps_unref (caps);
    }
    g_free(capsString);
}

void cb_have_type (GstElement* typefind, guint probab, GstCaps* caps, gstElements* elements)
{
    gchar *type;
    type = gst_caps_to_string (caps);
    LOG(verbose) << "Media type: " << type << endl;

    if (g_strrstr(type, "matroska"))
    {
        elements->m_demuxer  = gst_element_factory_make ("matroskademux", nullptr);
        elements->m_muxer    = gst_element_factory_make ("matroskamux", nullptr);
        if (!elements->m_demuxer || !elements->m_muxer)
        {
            LOG(error) << "Could not create matroska elements" << endl;
            return;
        }
        gst_bin_add_many (GST_BIN (elements->m_pipeline), elements->m_demuxer, elements->m_muxer, NULL);
        g_signal_connect (elements->m_demuxer, "pad-added", G_CALLBACK (cb_on_pad_added), elements->m_muxer);
        if (gst_element_link(elements->m_muxer, elements->m_identity) != TRUE)
        {
            LOG(error) << "Element mux and identity could not be linked." << endl;
            return;
        }
        GstStateChangeReturn ret = gst_element_set_state (elements->m_muxer, GST_STATE_PLAYING);
        if (ret == GST_STATE_CHANGE_FAILURE)
        {
            LOG(error) << "Unable to set the muxer to playing state" << endl;
            return;
        }
    }
    else if (g_strrstr(type, "mpegts"))
    {
        elements->m_demuxer = gst_element_factory_make ("tsdemux", nullptr);
        gst_bin_add (GST_BIN (elements->m_pipeline), elements->m_demuxer);
        g_signal_connect (elements->m_demuxer, "pad-added", G_CALLBACK (cb_on_pad_added), elements->m_identity);
    }
    else
    {
        LOG(error) << "Unknown type" << endl;
        return;
    }
    if (gst_element_link(typefind, elements->m_demuxer) != TRUE)
    {
        LOG(error) << "Element typefind and demux could not be linked." << endl;
        return;
    }
    GstStateChangeReturn ret = gst_element_set_state (elements->m_demuxer, GST_STATE_PLAYING);
    if (ret == GST_STATE_CHANGE_FAILURE)
    {
        LOG(error) << "Unable to set the demuxer to playing state" << endl;
        return;
    }
    g_free (type);
}

void cb_identity_handoff (GstElement *identity, GstBuffer *buffer, gpointer duration)
{
    GstClockTime& file_duration = *(GstClockTime*)duration;
    GstClockTime pts = GST_BUFFER_PTS(buffer);
    GstClockTime frame_duration = GST_BUFFER_DURATION(buffer);

    if (GST_CLOCK_TIME_IS_VALID(pts))
    {
        file_duration = GST_CLOCK_TIME_IS_VALID(frame_duration) ? pts + frame_duration : pts;
    }
}

static GstPadProbeReturn parser_src_pad_cb(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
{
    gstElements* elements = (gstElements*)user_data;
    if (elements)
    {
        elements->m_parserSrcPadOutCount++;
    }
    return GST_PAD_PROBE_OK;
}


GstClockTime getDurationAndRemuxFile (const string& file_path, bool overwrite = false)
{
    GstClockTime duration = 0;
    if(gst_is_initialized() == false)
    {
        gst_init (nullptr, nullptr);
    }
    gstElements elements;

    elements.m_pipeline = gst_pipeline_new ("pipeline");
    elements.m_source   = gst_element_factory_make ("filesrc", nullptr);
    elements.m_typefind = gst_element_factory_make ("typefind", nullptr);
    elements.m_identity = gst_element_factory_make ("identity", nullptr);
    if (overwrite)
    {
        LOG(warning) << "Will overwrite file" << endl;
        elements.m_sink = gst_element_factory_make ("filesink", nullptr);
    }
    else
    {
        elements.m_sink = gst_element_factory_make ("fakesink", nullptr);
        g_object_set (G_OBJECT (elements.m_sink), "sync", false, NULL);
    }

    if (!elements.m_pipeline || !elements.m_source || !elements.m_typefind ||
        !elements.m_identity || !elements.m_sink)
    {
        LOG(error) << "Not all elements could be created" << endl;
        return duration;
    }
    gst_bin_add_many (GST_BIN (elements.m_pipeline), elements.m_source,
        elements.m_typefind, elements.m_identity, elements.m_sink, NULL);

    if (gst_element_link(elements.m_source, elements.m_typefind) != TRUE)
    {
        LOG(error) << "Element source and demux could not be linked." << endl;
        gst_object_unref(elements.m_pipeline);
        return duration;
    }

    if (gst_element_link(elements.m_identity, elements.m_sink) != TRUE)
    {
        LOG(error) << "Element identity and sink could not be linked." << endl;
        gst_object_unref(elements.m_pipeline);
        return duration;
    }

    g_object_set (G_OBJECT (elements.m_source), "location", file_path.c_str(), NULL);
    string temp_file_path = file_path + "_1.mkv";
    if (overwrite)
    {
        g_object_set (G_OBJECT (elements.m_sink), "location", temp_file_path.c_str(), NULL);
    }
    g_signal_connect (elements.m_typefind, "have-type", G_CALLBACK (cb_have_type), &elements);
    g_signal_connect (elements.m_identity, "handoff", G_CALLBACK (cb_identity_handoff), &duration);

    GstStateChangeReturn ret = gst_element_set_state (elements.m_pipeline, GST_STATE_PLAYING);
    if (ret == GST_STATE_CHANGE_FAILURE)
    {
        LOG(error) << "Unable to set the pipeline to the playing state" << endl;
        gst_object_unref (elements.m_pipeline);
        return duration;
    }

    /**
     * TODO: remove do-while wherever applicable
     * */
    gboolean error = false;
    gboolean terminate = false;
    GstBus* bus = gst_element_get_bus (elements.m_pipeline);
    do
    {
        GstMessage* msg = gst_bus_timed_pop_filtered (bus, 10 * GST_SECOND,
                    (GstMessageType)(GST_MESSAGE_ERROR | GST_MESSAGE_EOS));

        /* Parse message */
        if (msg != nullptr)
        {
            GError *err;
            gchar *debug_info;

            switch (GST_MESSAGE_TYPE (msg)) {
            case GST_MESSAGE_ERROR:
                gst_message_parse_error (msg, &err, &debug_info);
                LOG(error) << "Error received from element "
                            << GST_OBJECT_NAME (msg->src) << ": "<< err->message << endl;
                LOG(error) << "Debugging information: " << (debug_info ? debug_info : "none") << endl;
                g_clear_error (&err);
                g_free (debug_info);
                terminate = TRUE;
                error = TRUE;
                break;

            case GST_MESSAGE_EOS:
                LOG(verbose) << "End-Of-Stream reached" << endl;
                terminate = TRUE;
                break;

            default:
                /* We should not reach here */
                LOG(error) << "Unexpected message received" << endl;
                break;
            }
            gst_message_unref (msg);
        }
    } while (!terminate);

    /* Free resources */
    gst_object_unref (bus);
    gst_element_set_state (elements.m_pipeline, GST_STATE_NULL);
    gst_object_unref (elements.m_pipeline);

    if (!error && overwrite)
    {
        LOG(warning) << "Overwriting " << temp_file_path << " over: " << file_path << endl;
        if (rename (temp_file_path.c_str(), file_path.c_str()) != 0)
        {
            LOG(error) << "Could not overwrite file " << file_path.c_str() << endl;
        }
    }

    return duration / (1000*1000);
}
}   // unnamed namespace

GstClockTime getMediaFileDuration (const std::string& file_path)
{
    return getDurationAndRemuxFile (file_path);
}

GstClockTime fixMediaFileAndGetDuration (const std::string& file_path)
{
    return getDurationAndRemuxFile (file_path, true);
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

void on_pad_added_internal (GstElement *demux, GstPad *pad, GstNvElements* elements)
{
    GstPad *sink_pad = nullptr;
    LOG(info) << "Received new pad " << GST_PAD_NAME (pad) << " from " << GST_ELEMENT_NAME (demux) << endl;

    GstCaps *caps = gst_pad_get_current_caps (pad);
    gchar *capsString = gst_caps_to_string (caps);
    LOG(info) << "Caps = " << capsString << endl;
    if (g_str_has_prefix(capsString, "video"))
    {
        if (elements->m_videoQueue )
        {
            sink_pad = gst_element_get_static_pad (elements->m_videoQueue, "sink");
            if (sink_pad == nullptr)
            {
                LOG(error) << "sink pad is NULL" << endl;
                elements->m_isError =  true;
            }
        }
        else
        {
            LOG(error) << "m_videoQueue is null" << endl;
            elements->m_isError =  true;
        }

        if (pad && sink_pad)
        {
            if (gst_pad_link (pad, sink_pad) != GST_PAD_LINK_OK)
            {
                LOG(error) << "gst_element_link failed" << endl;
                elements->m_isError =  true;
            }
            else
            {
                LOG(info) << "gst_element_link Done" << endl;
            }
            gst_object_unref (sink_pad);
        }
        else
        {
            LOG(error) << "sink pad or demux pad is NULL" << endl;
        }
    }
    if (caps != nullptr)
    {
        gst_caps_unref (caps);
    }
    g_free(capsString);
}

static void trans_on_pad_added (GstElement *demux, GstPad *pad, GstNvElements* elements)
{
    on_pad_added_internal(demux, pad, elements);
}

double getAvgFPSForFile (string& file_path, const string& codec)
{
    int frame_count   = getFrameCountForFile (file_path, codec);
    int64_t duration = 0;
    int attempts = 2;
    do
    {
        duration  = getMediaFileDuration (file_path); // this returns duration in ms
        if(duration == 0) // Duration 0 result in case where file is just started recording.
        {
            usleep(500000); // Wait for 500ms to allow accumulation of few frames.
            --attempts;
        }
    } while (duration == 0 && attempts != 0 );
    if(duration == 0)
    {
        return 30.0; // use Default value
    }
    return (frame_count / (duration / 1000));
}

int getFrameCountForFile (string& file_path, const string& codec)
{
    GstClockTime frame_count = 0;
    if(gst_is_initialized() == false)
    {
        gst_init (nullptr, nullptr);
    }
    gstElements elements;

    /*
       filesrc -> matroskademux -> h264parse -> fakesink
                                             ^
                                             |
                                           Probe
    */

    elements.m_pipeline    = gst_pipeline_new ("pipeline");
    elements.m_source      = gst_element_factory_make ("filesrc", nullptr);
    elements.m_demuxer     = gst_element_factory_make ("matroskademux", nullptr);
    if (iequals(codec, "h265"))
    {
        elements.m_videoParser = gst_element_factory_make ("h265parse", nullptr);
    }
    else
    {
        elements.m_videoParser = gst_element_factory_make ("h264parse", nullptr);
    }
    elements.m_sink        = gst_element_factory_make ("fakesink", nullptr);

    g_object_set (G_OBJECT (elements.m_sink), "sync", false, NULL);

    if (!elements.m_pipeline || !elements.m_source || !elements.m_videoParser ||
        !elements.m_sink     || !elements.m_demuxer)
    {
        LOG(error) << "Not all elements could be created" << endl;
        return frame_count;
    }
    gst_bin_add_many (GST_BIN (elements.m_pipeline), elements.m_source, elements.m_demuxer,
                      elements.m_videoParser, elements.m_sink, NULL);

    if (gst_element_link(elements.m_source, elements.m_demuxer) != TRUE)
    {
        LOG(error) << "Element source and demux could not be linked." << endl;
        gst_object_unref(elements.m_pipeline);
        return frame_count;
    }

    if (!g_signal_connect (G_OBJECT (elements.m_demuxer), "pad-added", G_CALLBACK (on_pad_added), elements.m_videoParser))
    {
        LOG(error) << "Error in g_signal_connect of pad-added" << endl;
    }

    if (gst_element_link_many(elements.m_videoParser, elements.m_sink, NULL) != TRUE)
    {
        LOG(error) << "Element identity and sink could not be linked." << endl;
        gst_object_unref(elements.m_pipeline);
        return frame_count;
    }

    GstPad* parsersrc_src_pad = nullptr;
    parsersrc_src_pad = gst_element_get_static_pad (GST_ELEMENT(elements.m_videoParser), "src");
    if (!parsersrc_src_pad)
    {
        LOG(error) << "Failed to get sink pad of appsrc_src_pad" << endl;
    }
    gst_pad_add_probe(parsersrc_src_pad, GST_PAD_PROBE_TYPE_BUFFER, parser_src_pad_cb,  &elements, nullptr);
    gst_object_unref(parsersrc_src_pad);

    g_object_set (G_OBJECT (elements.m_source), "location", file_path.c_str(), NULL);

    GstStateChangeReturn ret = gst_element_set_state (elements.m_pipeline, GST_STATE_PLAYING);
    if (ret == GST_STATE_CHANGE_FAILURE)
    {
        LOG(error) << "Unable to set the pipeline to the playing state" << endl;
        gst_object_unref (elements.m_pipeline);
        return frame_count;
    }
#ifdef DEBUG
    g_signal_connect( elements.m_pipeline, "deep-notify", G_CALLBACK( gst_object_default_deep_notify ), NULL );
#endif
    /**
     * TODO: remove do-while wherever applicable
     * */
    gboolean terminate = false;
    GstBus* bus = gst_element_get_bus (elements.m_pipeline);
    do
    {
        GstMessage* msg = gst_bus_timed_pop_filtered (bus, 10 * GST_SECOND,
                    (GstMessageType)(GST_MESSAGE_ERROR | GST_MESSAGE_EOS));

        /* Parse message */
        if (msg != nullptr)
        {
            GError *err;
            gchar *debug_info;

            switch (GST_MESSAGE_TYPE (msg)) {
            case GST_MESSAGE_ERROR:
                gst_message_parse_error (msg, &err, &debug_info);
                LOG(error) << "Error received from element "
                            << GST_OBJECT_NAME (msg->src) << ": "<< err->message << endl;
                LOG(error) << "Debugging information: " << (debug_info ? debug_info : "none") << endl;
                g_clear_error (&err);
                g_free (debug_info);
                terminate = TRUE;
                break;

            case GST_MESSAGE_EOS:
                LOG(verbose) << "End-Of-Stream reached" << endl;
                terminate = TRUE;
                break;

            default:
                /* We should not reach here */
                LOG(error) << "Unexpected message received" << endl;
                break;
            }
            gst_message_unref (msg);
        }
    } while (!terminate);

    /* Free resources */
    gst_object_unref (bus);
    gst_element_set_state (elements.m_pipeline, GST_STATE_NULL);
    gst_object_unref (elements.m_pipeline);

    return elements.m_parserSrcPadOutCount;
}

void GstNvElements::pollBusMessages()
{
    // Maximum timeout to prevent permanent hangs (e.g., caps negotiation failures)
    const int MAX_TIMEOUT_SECONDS = GET_CONFIG().download_files_timeout_secs;
    const int POLL_INTERVAL_SECONDS = 2;
    int elapsed_seconds = 0;

    while(!m_isError)
    {
        GstBus *bus = gst_pipeline_get_bus (GST_PIPELINE (m_pipeline));
        LOG(verbose) << "Waiting to get message from Bus...." << endl;
        GstMessage *message = gst_bus_timed_pop_filtered (bus, POLL_INTERVAL_SECONDS * GST_SECOND,
                (GstMessageType) (GST_MESSAGE_ERROR | GST_MESSAGE_EOS | GST_MESSAGE_DURATION));
        gst_object_unref (bus);
        if (message != nullptr)
        {
            switch (GST_MESSAGE_TYPE(message))
            {
                case GST_MESSAGE_EOS:
                case GST_MESSAGE_ERROR:
                {
                    if (GST_MESSAGE_TYPE(message) == GST_MESSAGE_ERROR)
                    {
                        GError *err = nullptr;
                        gchar *dbg_info = nullptr;
                        gst_message_parse_error(message, &err, &dbg_info);
                        LOG(error) << "Decoder Pipeline ERR from "
                            << std::string(GST_OBJECT_NAME(message->src)) + ": "
                            <<  std::string(err->message)  << endl;
                        if (dbg_info)
                        {
                            LOG(info) << "Decoder Pipeline ERR DEBUG "
                                << std::string(dbg_info)  << endl;
                        }
                        g_error_free(err);
                        g_free(dbg_info);
                        m_isError = true;
                    }
                    else if (GST_MESSAGE_TYPE(message) == GST_MESSAGE_EOS)
                    {
                        LOG(info) << "Decoder Pipeline -> received EOS" << endl;
                    }
                    gst_message_unref (message);
                    return;
                }
                default:
                {
                    gchar *path = gst_object_get_path_string (GST_MESSAGE_SRC (message));
                    LOG(info) << "Decoder Pipeline playing="
                        << " -> received OTHER (" << std::string(GST_MESSAGE_TYPE_NAME(message))
                        << ") from " + std::string(path) << endl;
                    g_free (path);
                    // Reset timeout on activity - pipeline is making progress
                    elapsed_seconds = 0;
                    break;
                }
            }
            gst_message_unref (message);
        }
        else
        {
            // No message received within poll interval - check for timeout
            elapsed_seconds += POLL_INTERVAL_SECONDS;
            if (elapsed_seconds >= MAX_TIMEOUT_SECONDS)
            {
                LOG(error) << "Pipeline timeout after " << MAX_TIMEOUT_SECONDS
                          << " seconds without EOS/progress. Pipeline may be stuck." << endl;
                m_isError = true;
                return;
            }
            LOG(verbose) << "No message received, elapsed: " << elapsed_seconds
                        << "s, max: " << MAX_TIMEOUT_SECONDS << "s" << endl;
        }
    }
    LOG(verbose) << "Exiting from bus message task...." << endl;
}

// Callback to control decoder selection in decodebin (for HW/SW control)
// Return values: 0=TRY, 1=EXPOSE, 2=SKIP
static gint transcode_autoplug_select(GstElement *decodebin, GstPad *pad,
                                       GstCaps *caps, GstElementFactory *factory,
                                       GstTranscode *data)
{
    const gchar *factory_name = gst_plugin_feature_get_name(GST_PLUGIN_FEATURE(factory));

    // If forcing SW decode, skip all HW decoders
    if (!data->m_useHwDecoder)
    {
        bool isNvElement = g_str_has_prefix(factory_name, "nv");
        bool isOmxElement = g_str_has_prefix(factory_name, "omx");
        if (isNvElement || isOmxElement)
        {
            LOG(info) << "autoplug-select: Skipping HW element: " << factory_name << endl;
            return 2;  // GST_AUTOPLUG_SELECT_SKIP
        }
    }

    return 0;  // GST_AUTOPLUG_SELECT_TRY
}

// Callback for audio decoder (decodebin) pad-added in manual pipeline
static void transcode_audiodec_pad_added(GstElement *decodebin, GstPad *pad, GstTranscode *data)
{
    GstCaps *caps = gst_pad_get_current_caps(pad);
    if (!caps)
    {
        caps = gst_pad_query_caps(pad, nullptr);
    }

    GstStructure *str = gst_caps_get_structure(caps, 0);
    const gchar *name = gst_structure_get_name(str);
    gchar *caps_str = gst_caps_to_string(caps);
    LOG(info) << "Audio decoder pad: " << caps_str << endl;

    // Only handle raw audio pads
    if (g_str_has_prefix(name, "audio/x-raw"))
    {
        // Link to audioconvert
        GstPad *sinkpad = gst_element_get_static_pad(data->m_audioconvert, "sink");
        if (sinkpad && !gst_pad_is_linked(sinkpad))
        {
            GstPadLinkReturn ret = gst_pad_link(pad, sinkpad);
            if (GST_PAD_LINK_FAILED(ret))
            {
                LOG(warning) << "Failed to link audio decoder to audioconvert: " << ret << endl;
            }
            else
            {
                LOG(info) << "Linked audio decoder -> audioconvert" << endl;
            }
        }
        if (sinkpad) gst_object_unref(sinkpad);
    }

    g_free(caps_str);
    gst_caps_unref(caps);
}

// Callback for manual pipeline demuxer pad-added (HW+HW path)
static void transcode_demux_pad_added(GstElement *demux, GstPad *pad, GstTranscode *data)
{
    GstCaps *caps = gst_pad_get_current_caps(pad);
    gchar *caps_str = gst_caps_to_string(caps);
    LOG(info) << "Demuxer pad: " << caps_str << endl;

    if (g_strrstr(caps_str, "video"))
    {
        // Link demuxer -> videoqueue
        GstPad *sinkpad = gst_element_get_static_pad(data->m_videoQueue, "sink");
        if (sinkpad && !gst_pad_is_linked(sinkpad))
        {
            if (gst_pad_link(pad, sinkpad) == GST_PAD_LINK_OK)
            {
                LOG(info) << "Linked demuxer -> videoqueue" << endl;
                data->m_hasVideo = true;
            }
            else
            {
                LOG(error) << "Failed to link demuxer -> videoqueue" << endl;
                data->m_isError = true;
            }
        }
        if (sinkpad) gst_object_unref(sinkpad);
    }
    else if (g_strrstr(caps_str, "audio"))
    {
        // Handle audio for manual pipeline - dynamically add and link audio elements
        // This ensures video-only files don't hang waiting for audio
        if (data->m_audioQueue && data->m_audiodecoder && data->m_audioconvert && 
            data->m_audioresample && data->m_audioencoder && data->m_pipeline)
        {
            // Dynamically add audio elements to the pipeline
            gst_bin_add_many(GST_BIN(data->m_pipeline),
                data->m_audioQueue, data->m_audiodecoder, 
                data->m_audioconvert, data->m_audioresample, data->m_audioencoder, NULL);

            // Link audioqueue -> audiodecoder
            if (!gst_element_link(data->m_audioQueue, data->m_audiodecoder))
            {
                LOG(warning) << "Failed to link audioqueue -> audiodecoder" << endl;
                g_free(caps_str);
                gst_caps_unref(caps);
                return;
            }

            // Link audioconvert -> audioresample -> audioencoder
            if (!gst_element_link_many(data->m_audioconvert, data->m_audioresample, data->m_audioencoder, NULL))
            {
                LOG(warning) << "Failed to link audio encoding chain" << endl;
                g_free(caps_str);
                gst_caps_unref(caps);
                return;
            }

            // Connect audiodecoder (decodebin) pad-added signal
            g_signal_connect(data->m_audiodecoder, "pad-added", G_CALLBACK(transcode_audiodec_pad_added), data);

            // Set audio elements to PLAYING state
            gst_element_sync_state_with_parent(data->m_audioQueue);
            gst_element_sync_state_with_parent(data->m_audiodecoder);
            gst_element_sync_state_with_parent(data->m_audioconvert);
            gst_element_sync_state_with_parent(data->m_audioresample);
            gst_element_sync_state_with_parent(data->m_audioencoder);

            // Link demuxer -> audioqueue
            GstPad *sinkpad = gst_element_get_static_pad(data->m_audioQueue, "sink");
            if (sinkpad && !gst_pad_is_linked(sinkpad))
            {
                GstPadLinkReturn ret = gst_pad_link(pad, sinkpad);
                if (ret == GST_PAD_LINK_OK)
                {
                    // Link audioencoder to muxer
                    if (gst_element_link(data->m_audioencoder, data->m_muxer))
                    {
                        LOG(info) << "Dynamically added and linked audio: demuxer -> audioqueue -> audiodecoder -> audioconvert -> audioresample -> audioencoder -> muxer" << endl;
                        data->m_hasAudio = true;
                    }
                    else
                    {
                        LOG(warning) << "Failed to link audioencoder to muxer for manual pipeline" << endl;
                    }
                }
                else
                {
                    LOG(warning) << "Failed to link demuxer audio pad to audio queue: " << ret << endl;
                }
            }
            if (sinkpad) gst_object_unref(sinkpad);
        }
        else
        {
            LOG(info) << "Audio elements not available, skipping audio for manual pipeline" << endl;
        }
    }

    g_free(caps_str);
    gst_caps_unref(caps);
}

// Callback when decodebin creates a new pad (video or audio)
static void transcode_decodebin_pad_added(GstElement *decodebin, GstPad *pad, GstTranscode *data)
{
    GstCaps *caps;
    GstStructure *str;
    const gchar *name;

    // Get pad caps
    caps = gst_pad_get_current_caps(pad);
    if (!caps)
    {
        caps = gst_pad_query_caps(pad, nullptr);
    }

    str = gst_caps_get_structure(caps, 0);
    name = gst_structure_get_name(str);

    gchar *caps_str = gst_caps_to_string(caps);
    LOG(info) << "Decodebin pad: " << caps_str << endl;

    // Handle video pads (this callback is only used for non-manual pipeline)
    if (g_str_has_prefix(name, "video/x-raw"))
    {
        GstPad *sinkpad = gst_element_get_static_pad(data->m_videoConverter, "sink");
        if (sinkpad && !gst_pad_is_linked(sinkpad))
        {
            GstPadLinkReturn ret = gst_pad_link(pad, sinkpad);
            if (GST_PAD_LINK_FAILED(ret))
            {
                LOG(error) << "Failed to link video: " << ret << endl;
                data->m_isError = true;
            }
            else
            {
                LOG(info) << "Linked video to videoconvert" << endl;
                data->m_hasVideo = true;
            }
        }
        if (sinkpad) gst_object_unref(sinkpad);
    }
    // Handle audio pads - re-encode to AAC for muxer
    else if (g_str_has_prefix(name, "audio/"))
    {
        if (data->m_audioconvert && data->m_audioencoder && data->m_muxer)
        {
            GstPad *sinkpad = gst_element_get_static_pad(data->m_audioconvert, "sink");
            if (sinkpad && !gst_pad_is_linked(sinkpad))
            {
                GstPadLinkReturn ret = gst_pad_link(pad, sinkpad);
                if (GST_PAD_LINK_FAILED(ret))
                {
                    LOG(warning) << "Failed to link audio: " << ret << endl;
                }
                else
                {
                    // Now link audioencoder to muxer since we have valid audio
                    if (gst_element_link(data->m_audioencoder, data->m_muxer))
                    {
                        LOG(info) << "Linked audio: decodebin -> audioconvert -> ... -> audioencoder -> muxer" << endl;
                        data->m_hasAudio = true;
                    }
                    else
                    {
                        LOG(warning) << "Failed to link audioencoder to muxer" << endl;
                    }
                }
            }
            if (sinkpad) gst_object_unref(sinkpad);
        }
    }

    g_free(caps_str);
    gst_caps_unref(caps);
}

bool GstTranscode::transcode (TranscodeParam params)
{
    bool ret = true;
    bool elements_created = true;

    LOG(info) << "Transcode input file: " << params.m_inFilePath << ", container: " << params.m_inContainer << ", codec: " << params.m_inCodec << endl;

    if(gst_is_initialized() == false)
    {
        gst_init (nullptr, nullptr);
    }

    // Determine HW/SW usage from NvHwDetection
    m_useHwEncoder = NvHwDetection::getInstance()->m_useNvV4l2Enc;
    m_useHwDecoder = NvHwDetection::getInstance()->m_useNvV4l2Dec;

    // Use manual pipeline for HW+HW to avoid nvvideoconvert deadlock
    // When both decoder and encoder are HW, nvvideoconvert can cause buffer pool contention
    m_useManualPipeline = m_useHwDecoder && m_useHwEncoder;

    LOG(info) << "HW encoder: " << (m_useHwEncoder ? "available" : "not available") << endl;
    LOG(info) << "HW decoder: " << (m_useHwDecoder ? "available" : "not available") << endl;
    LOG(info) << "Using: " << (m_useHwDecoder ? "HW" : "SW") << " decoder + "
              << (m_useHwEncoder ? "HW" : "SW") << " encoder"
              << (m_useManualPipeline ? " [MANUAL PIPELINE]" : "") << endl;

    // Create pipeline elements
    m_pipeline = gst_pipeline_new("transcode-pipeline");
    m_source = gst_element_factory_make("filesrc", "source");

    if (m_useManualPipeline)
    {
        // Manual pipeline for HW+HW: filesrc -> demuxer -> h264parse -> nvv4l2decoder -> nvv4l2h264enc -> h264parse -> muxer -> filesink
        // This avoids nvvideoconvert which can cause blocking issues with NVMM passthrough
        m_demuxer = createDemuxerForFile(params.m_inFilePath, params.m_inContainer);

        if (iequals(params.m_inCodec, "h265"))
        {
            m_inputParser = gst_element_factory_make("h265parse", "inputparser");
            m_dec = gst_element_factory_make("nvv4l2decoder", "decoder");
            m_enc = gst_element_factory_make("nvv4l2h265enc", "encoder");
        }
        else
        {
            m_inputParser = gst_element_factory_make("h264parse", "inputparser");
            m_dec = gst_element_factory_make("nvv4l2decoder", "decoder");
            m_enc = gst_element_factory_make("nvv4l2h264enc", "encoder");
        }
        m_videoConverter = nullptr;  // Not needed for direct HW path
        m_decodebin = nullptr;
    }
    else
    {
        // Use decodebin for other paths (HW+SW, SW+HW, SW+SW)
        m_decodebin = gst_element_factory_make("decodebin", "decodebin");

        // Video converter: nvvideoconvert if ANY HW element is used (handles NVMM memory)
        // - HW dec + SW enc: NVMM -> regular (needs nvvideoconvert!)
        // - SW dec + HW enc: regular -> NVMM (needs nvvideoconvert!)
        // - SW dec + SW enc: regular -> regular (videoconvert is fine)
        if (m_useHwDecoder || m_useHwEncoder)
        {
#ifdef JETSON_PLATFORM
            m_videoConverter = gst_element_factory_make("nvvidconv", "convert");
#else
            m_videoConverter = gst_element_factory_make("nvvideoconvert", "convert");
            if (!m_videoConverter)
            {
                m_videoConverter = gst_element_factory_make("nvvidconv", "convert");
            }
#endif
            if (!m_videoConverter)
            {
                LOG(warning) << "nvvideoconvert not available, using videoconvert" << endl;
            }
        }
        else
        {
            m_videoConverter = gst_element_factory_make("videoconvert", "convert");
        }

        // Encoder based on HW availability and codec
        if (m_useHwEncoder)
        {
            if (iequals(params.m_inCodec, "h265"))
            {
                m_enc = gst_element_factory_make("nvv4l2h265enc", "encoder");
            }
            else
            {
                m_enc = gst_element_factory_make("nvv4l2h264enc", "encoder");
            }
        }
        else
        {
            // Use appropriate software encoder based on input codec
            if (iequals(params.m_inCodec, "h265"))
            {
                m_enc = gst_element_factory_make("x265enc", "encoder");
            }
            else
            {
                m_enc = gst_element_factory_make("x264enc", "encoder");
            }
        }
    }

    // Output parser based on encoder and input codec
    if (iequals(params.m_inCodec, "h265"))
    {
        m_videoParser2 = gst_element_factory_make("h265parse", "parser");
    }
    else
    {
        m_videoParser2 = gst_element_factory_make("h264parse", "parser");
    }

    // Muxer based on output container
    m_muxer = createMuxerForFile(params.m_outFilePath, params.m_inContainer);
    m_sink = gst_element_factory_make("filesink", "sink");

    // Audio re-encoding chain (decodebin outputs raw audio, muxer needs AAC)
    // Create audio elements for both paths
    m_audioconvert = gst_element_factory_make("audioconvert", "audioconvert");
    m_audioresample = gst_element_factory_make("audioresample", "audioresample");
    // Try different AAC encoders in order of preference
    m_audioencoder = gst_element_factory_make("avenc_aac", "audioencoder");
    if (!m_audioencoder)
    {
        m_audioencoder = gst_element_factory_make("voaacenc", "audioencoder");
    }
    if (!m_audioencoder)
    {
        m_audioencoder = gst_element_factory_make("fdkaacenc", "audioencoder");
    }
    if (!m_audioencoder)
    {
        LOG(warning) << "No AAC encoder found, audio will be skipped" << endl;
    }

    // Additional elements for manual pipeline
    if (m_useManualPipeline)
    {
        // Video queue after demuxer to prevent blocking
        m_videoQueue = gst_element_factory_make("queue", "videoqueue");
        if (!m_videoQueue)
        {
            LOG(warning) << "Failed to create video queue for manual pipeline" << endl;
        }

        // Audio elements (needs decoder since demuxer outputs encoded audio)
        m_audioQueue = gst_element_factory_make("queue", "audioqueue");
        // Use decodebin for audio decoding - it auto-selects the right decoder
        m_audiodecoder = gst_element_factory_make("decodebin", "audiodecoder");
        if (!m_audioQueue)
        {
            LOG(warning) << "Failed to create audio queue for manual pipeline" << endl;
        }
        if (!m_audiodecoder)
        {
            LOG(warning) << "Failed to create audio decoder for manual pipeline" << endl;
        }
    }

    // Videorate + capsfilter for framerate control (decodebin pipeline only)
    if (!m_useManualPipeline && params.m_outframeRate > 0)
    {
        m_videoRate = gst_element_factory_make("videorate", nullptr);
        m_rateCapsFilter = gst_element_factory_make("capsfilter", nullptr);
        if (!m_videoRate || !m_rateCapsFilter)
        {
            LOG(error) << "Failed to create videorate or capsfilter elements" << endl;
            elements_created = false;
        }
    }

    // Validate elements
    if (!m_pipeline) { LOG(error) << "Failed to create pipeline" << endl; elements_created = false; }
    if (!m_source) { LOG(error) << "Failed to create filesrc" << endl; elements_created = false; }

    if (m_useManualPipeline)
    {
        // Validate manual pipeline elements
        if (!m_demuxer) { LOG(error) << "Failed to create demuxer" << endl; elements_created = false; }
        if (!m_videoQueue) { LOG(error) << "Failed to create video queue" << endl; elements_created = false; }
        if (!m_inputParser) { LOG(error) << "Failed to create input parser" << endl; elements_created = false; }
        if (!m_dec) { LOG(error) << "Failed to create nvv4l2decoder" << endl; elements_created = false; }
        if (!m_enc) { LOG(error) << "Failed to create nvv4l2h264enc/h265enc" << endl; elements_created = false; }
    }
    else
    {
        // Validate decodebin pipeline elements
        if (!m_decodebin) { LOG(error) << "Failed to create decodebin" << endl; elements_created = false; }
        if (!m_videoConverter) { LOG(error) << "Failed to create videoconvert" << endl; elements_created = false; }
        if (!m_enc) {
            LOG(error) << "Failed to create " << (m_useHwEncoder ? "nvv4l2h264enc/h265enc" : "x264enc") << endl;
            elements_created = false;
        }
    }

    if (!m_videoParser2) { LOG(error) << "Failed to create h264parse" << endl; elements_created = false; }
    if (!m_muxer) { LOG(error) << "Failed to create muxer" << endl; elements_created = false; }
    if (!m_sink) { LOG(error) << "Failed to create filesink" << endl; elements_created = false; }
    // Warn about audio elements for both paths
    if (!m_audioconvert) { LOG(warning) << "Failed to create audioconvert" << endl; }
    if (!m_audioresample) { LOG(warning) << "Failed to create audioresample" << endl; }
    // Additional audio element warnings for manual pipeline
    if (m_useManualPipeline)
    {
        if (!m_audioQueue) { LOG(warning) << "Failed to create audio queue" << endl; }
        if (!m_audiodecoder) { LOG(warning) << "Failed to create audio decoder" << endl; }
    }

    int keyFrameInterval = (params.m_outKeyFrameInterval > 0)
        ? params.m_outKeyFrameInterval
        : DEFAUL_KEY_FRAME_INTERVAL;

    if (!elements_created)
    {
        LOG(error) << "Failed to create pipeline elements" << endl;
        ret = false;
        goto error;
    }

    LOG(info) << "Encoder: " << GST_ELEMENT_NAME(m_enc) << endl;
    if (m_videoConverter)
    {
        LOG(info) << "Converter: " << GST_ELEMENT_NAME(m_videoConverter) << endl;
    }

    // Set properties
    g_object_set(G_OBJECT(m_source), "location", params.m_inFilePath.c_str(), NULL);
    g_object_set(G_OBJECT(m_sink), "location", params.m_outFilePath.c_str(), NULL);
    g_object_set(G_OBJECT(m_videoParser2), "config-interval", -1, NULL);

    // Configure encoder

    if (m_useHwEncoder)
    {
        // nvv4l2 encoder settings
        g_object_set(G_OBJECT(m_enc), "gpu-id", g_gpuIndex, NULL);

        if (params.m_allIframes)
        {
            LOG(info) << "Encoding with all I Frames" << endl;
            g_object_set(G_OBJECT(m_enc), "iframeinterval", 1, NULL);
        }
        else
        {
            g_object_set(G_OBJECT(m_enc), "iframeinterval", keyFrameInterval, NULL);
            g_object_set(G_OBJECT(m_enc), "idrinterval", keyFrameInterval, NULL);
        }

        // Set bitrate from params if specified
        if (params.m_outBitrate > 0)
        {
            LOG(info) << "nvv4l2enc: bitrate=" << (guint)params.m_outBitrate << endl;
            g_object_set(G_OBJECT(m_enc), "bitrate", (guint)params.m_outBitrate, NULL);

            if (params.m_outframeRate > 0)
            {
                int vbv_buf_size = (params.m_outBitrate * 2) / params.m_outframeRate;
                LOG(verbose) << "vbv_buf size: " << vbv_buf_size << endl;
                g_object_set(G_OBJECT(m_enc), "vbvbufsize", vbv_buf_size, NULL);
            }
        }

#ifdef JETSON_PLATFORM
        g_object_set(G_OBJECT(m_enc), "copy-timestamp", true, NULL);
#endif
        LOG(info) << "nvv4l2enc configured (no B-frames by default), iframeinterval=" << keyFrameInterval << endl;
    }
    else
    {
        // Software encoder settings (x264enc or x265enc)
        if (iequals(params.m_inCodec, "h265"))
        {
            // x265enc settings
            if (params.m_allIframes)
            {
                LOG(info) << "Encoding with all I Frames" << endl;
                g_object_set(G_OBJECT(m_enc), "key-int-max", (guint)1, NULL);
            }
            else
            {
                g_object_set(G_OBJECT(m_enc),
                    "option-string", "bframes=0:b-adapt=0",  // No B-frames, disable adaptive
                    "key-int-max", (guint)keyFrameInterval,
                    NULL);
                LOG(info) << "x265enc: option-string=bframes=0:b-adapt=0,key-int-max=" << keyFrameInterval << endl;
            }
            // Set bitrate from params if specified (convert bps to kbps for x265enc)
            if (params.m_outBitrate > 0)
            {
                guint bitrate_kbps = (guint)(params.m_outBitrate / 1000);
                LOG(info) << "x265enc: bitrate=" << bitrate_kbps << "kbps" << endl;
                g_object_set(G_OBJECT(m_enc), "bitrate", bitrate_kbps, NULL);
            }
        }
        else
        {
            // x264enc settings
            if (params.m_allIframes)
            {
                LOG(info) << "Encoding with all I Frames" << endl;
                g_object_set(G_OBJECT(m_enc), "key-int-max", (guint)1, NULL);
            }
            else
            {
                g_object_set(G_OBJECT(m_enc),
                    "bframes", (guint)0,                      // No B-frames
                    "key-int-max", (guint)keyFrameInterval,
                    NULL);
                LOG(info) << "x264enc: bframes=0, key-int-max=" << keyFrameInterval << endl;
            }
            // Set bitrate from params if specified (convert bps to kbps for x264enc)
            if (params.m_outBitrate > 0)
            {
                guint bitrate_kbps = (guint)(params.m_outBitrate / 1000);
                LOG(info) << "x264enc: bitrate=" << bitrate_kbps << "kbps" << endl;
                g_object_set(G_OBJECT(m_enc), "bitrate", bitrate_kbps, NULL);
            }
        }
    }

    // Configure videorate capsfilter with target framerate
    if (m_videoRate && m_rateCapsFilter && params.m_outframeRate > 0)
    {
        GstCaps* rateCaps = gst_caps_new_simple("video/x-raw",
            "framerate", GST_TYPE_FRACTION, params.m_outframeRate, 1,
            NULL);
        g_object_set(G_OBJECT(m_rateCapsFilter), "caps", rateCaps, NULL);
        gst_caps_unref(rateCaps);
        LOG(info) << "videorate + capsfilter configured with framerate=" << params.m_outframeRate << "/1" << endl;
    }

    if (m_useManualPipeline)
    {
        // MANUAL PIPELINE for HW+HW: 
        // Video: filesrc -> demuxer -> videoqueue -> inputparser -> decoder -> encoder -> parser -> muxer -> filesink
        // Audio (added dynamically if present): demuxer -> audioqueue -> audiodecoder -> audioconvert -> audioresample -> audioencoder -> muxer
        gst_bin_add_many(GST_BIN(m_pipeline),
            m_source, m_demuxer, m_videoQueue, m_inputParser, m_dec,
            m_enc, m_videoParser2, m_muxer, m_sink, NULL);

        // NOTE: Audio elements are NOT added here - they will be added dynamically
        // when an audio pad is received from the demuxer. This prevents the pipeline
        // from hanging when processing video-only files.

        // Link source -> demuxer
        if (!gst_element_link(m_source, m_demuxer))
        {
            LOG(error) << "Failed to link source -> demuxer" << endl;
            ret = false;
            goto error;
        }

        // Link videoqueue -> inputparser -> decoder -> encoder -> parser -> muxer -> sink
        if (!gst_element_link_many(m_videoQueue, m_inputParser, m_dec, m_enc, 
                                    m_videoParser2, m_muxer, m_sink, NULL))
        {
            LOG(error) << "Failed to link HW encoding chain" << endl;
            ret = false;
            goto error;
        }
        LOG(info) << "Linked: videoqueue -> inputparser -> decoder -> encoder -> parser -> muxer -> sink" << endl;

        // Connect demuxer pad-added signal - audio elements will be added/linked dynamically
        g_signal_connect(m_demuxer, "pad-added", G_CALLBACK(transcode_demux_pad_added), this);
        LOG(info) << "Manual pipeline ready, audio will be added dynamically if present" << endl;
    }
    else
    {
        // DECODEBIN PIPELINE for other paths (HW+SW, SW+HW, SW+SW)
        gst_bin_add_many(GST_BIN(m_pipeline),
            m_source, m_decodebin, m_videoConverter,
            m_enc, m_videoParser2, m_muxer, m_sink, NULL);

        if (m_videoRate && m_rateCapsFilter)
        {
            gst_bin_add_many(GST_BIN(m_pipeline), m_videoRate, m_rateCapsFilter, NULL);
        }

        // Add audio elements if available
        if (m_audioconvert && m_audioresample && m_audioencoder)
        {
            gst_bin_add_many(GST_BIN(m_pipeline),
                m_audioconvert, m_audioresample, m_audioencoder, NULL);
        }

        // Link source -> decodebin
        if (!gst_element_link(m_source, m_decodebin))
        {
            LOG(error) << "Failed to link source -> decodebin" << endl;
            ret = false;
            goto error;
        }

        if (m_videoRate && m_rateCapsFilter)
        {
            if (!gst_element_link_many(m_videoConverter, m_videoRate, m_rateCapsFilter,
                                        m_enc, m_videoParser2, m_muxer, m_sink, NULL))
            {
                LOG(error) << "Failed to link video encoding chain (with videorate)" << endl;
                ret = false;
                goto error;
            }
            LOG(info) << "Linked: convert -> videorate -> capsfilter(fps=" << params.m_outframeRate << ") -> encoder -> parser -> muxer -> sink" << endl;
        }
        else
        {
            if (!gst_element_link_many(m_videoConverter, m_enc, m_videoParser2,
                                        m_muxer, m_sink, NULL))
            {
                LOG(error) << "Failed to link video encoding chain" << endl;
                ret = false;
                goto error;
            }
            LOG(info) << "Linked: convert -> encoder -> parser -> muxer -> sink" << endl;
        }

        // Link audio chain EXCEPT muxer - we'll link to muxer dynamically when audio pad arrives
        // This prevents muxer from waiting for audio that might never come (e.g., non-interleaved audio)
        if (m_audioconvert && m_audioresample && m_audioencoder)
        {
            if (!gst_element_link_many(m_audioconvert, m_audioresample,
                                        m_audioencoder, NULL))
            {
                LOG(warning) << "Failed to link audio encoding chain" << endl;
            }
            else
            {
                LOG(info) << "Linked: audioconvert -> audioresample -> audioencoder (muxer link deferred)" << endl;
            }
        }
        else
        {
            LOG(info) << "Audio encoding disabled (no AAC encoder available)" << endl;
        }

        // Connect decodebin signals
        g_signal_connect(m_decodebin, "pad-added", G_CALLBACK(transcode_decodebin_pad_added), this);
        g_signal_connect(m_decodebin, "autoplug-select", G_CALLBACK(transcode_autoplug_select), this);
    }

#ifdef DEBUG
    g_signal_connect(m_pipeline, "deep-notify", G_CALLBACK(gst_object_default_deep_notify), NULL);
#endif

    LOG(info) << "Creating output file: " << params.m_outFilePath << endl;

    // Start pipeline
    if (gst_element_set_state(m_pipeline, GST_STATE_PLAYING) == GST_STATE_CHANGE_FAILURE)
    {
        LOG(error) << "Unable to set the transcode pipeline to playing state" << endl;
        ret = false;
        goto error;
    }

    LOG(info) << "Pipeline started, transcoding..." << endl;
    pollBusMessages();

    if (m_isError)
    {
        LOG(error) << "Error occurred in pipeline" << endl;
        ret = false;
    }

error:
    if (m_pipeline)
    {
        gst_element_set_state(m_pipeline, GST_STATE_NULL);
        gst_element_get_state(m_pipeline, nullptr, nullptr, GST_SECOND);

        // Drop any element that was never added to the bin (e.g. audio chain
        // on a video-only input, or any element on the early-failure path) so
        // its floating ref doesn't leak. Bin-owned elements (parent != null)
        // are freed by gst_object_unref(m_pipeline) below.
        auto unrefOrphan = [](GstElement*& elem) {
            if (elem && GST_OBJECT_PARENT(elem) == nullptr)
            {
                gst_object_unref(elem);
            }
            elem = nullptr;
        };
        unrefOrphan(m_source);
        unrefOrphan(m_demuxer);
        unrefOrphan(m_decodebin);
        unrefOrphan(m_inputParser);
        unrefOrphan(m_videoQueue);
        unrefOrphan(m_dec);
        unrefOrphan(m_enc);
        unrefOrphan(m_videoConverter);
        unrefOrphan(m_videoParser2);
        unrefOrphan(m_videoRate);
        unrefOrphan(m_rateCapsFilter);
        unrefOrphan(m_audioconvert);
        unrefOrphan(m_audioresample);
        unrefOrphan(m_audioencoder);
        unrefOrphan(m_audioQueue);
        unrefOrphan(m_audiodecoder);
        unrefOrphan(m_muxer);
        unrefOrphan(m_sink);

        gst_object_unref(m_pipeline);
        m_pipeline = nullptr;
    }

    // Reset state for next use
    m_hasVideo = false;
    m_hasAudio = false;
    m_useManualPipeline = false;

    LOG(info) << "Exiting Transcode " << (ret ? "SUCCESS" : "FAILED") << " for: " << params.m_inFilePath << endl;
    return ret;
}

bool TranscodeTaskManager::addTask(GstTranscode::TranscodeParam params)
{
    bool ret = true;
    m_transcodeTaskList.push_back(async::spawn([=] () -> bool
    {
        GstTranscode t;
        return t.transcode(params);
    }));
    LOG(verbose) << "Started the Task...." << endl;
    for(uint32_t i = 0; i < m_transcodeTaskList.size(); i++ )
    {
        auto t = move(m_transcodeTaskList[i]);
        LOG(verbose) << "Waiting for finishing Task...." << endl;
        if(t.valid())
        {
            ret =  t.get();
            if (ret == false)
            {
                break;
            }
        }
    }
    LOG(verbose) << "Finished Task...." << endl;
    return ret;
}

static GstFlowReturn
on_new_sample_from_sink (GstElement * appsink, GstkeyframeParser *gstkeyframeParser)
{
    if (gstkeyframeParser)
    {
        return gstkeyframeParser->processNewSampleFromSink(appsink);
    }
    return GST_FLOW_ERROR;
}

GstFlowReturn GstkeyframeParser::processNewSampleFromSink (GstElement * appsink)
{
    GstSample *sample;
    GstBuffer *gstBuffer;
    GstMapInfo map;
    /* Get the sample from appsink */
    sample = gst_app_sink_pull_sample (GST_APP_SINK (appsink));
    if (sample == nullptr)
    {
        if (gst_app_sink_is_eos((GstAppSink *)appsink))
        {
            LOG (info) << "EOS Received on app sink element" << endl;
            // Handle case where no keyframe interval was assigned but we have frames
            if (m_videoEncodeParams.m_keyFrameInterval == 0 && m_videoEncodeParams.m_FrameCount > 0) {
                // Single keyframe or incomplete GOP scenario - set keyframe interval to total frame count
                m_videoEncodeParams.m_keyFrameInterval = m_videoEncodeParams.m_FrameCount;
                LOG(info) << "No GOP intervals detected, setting keyframe interval to total frame count: "
                         << m_videoEncodeParams.m_keyFrameInterval << endl;
            }
            return GST_FLOW_EOS;
        }
        return GST_FLOW_ERROR;
    }

    /* Get the buffer from sample */
    gstBuffer = gst_sample_get_buffer (sample);
    if (gstBuffer == nullptr)
    {
        LOG (info) << "No more buffers available from app sink element" << endl;
        return GST_FLOW_ERROR;
    }

    /* Map the gst buffer */
    if (gst_buffer_map (gstBuffer, &map, GST_MAP_READ) == false)
    {
        LOG (warning) << "Map the gst buffer Failed" << endl;
        gst_sample_unref (sample);
        return GST_FLOW_ERROR;
    }

    bool is_idr_frame = false;
    uint8_t nal_type = NaluType::kNalUnknown;

    if (iequals(m_videoEncodeParams.m_codec, "h264"))
    {
        nal_type = parseH264NaluType(map.data, map.size);
        is_idr_frame = isIDRFrame(nal_type, "H264");

        if (nal_type == NaluType::kSlice)
        {
            SliceType slice_type = parseH264SliceType(map.data, map.size);
            if (m_videoEncodeParams.m_isBframesPresent == false)
            {
                if (slice_type == SLICE_TYPE_B || slice_type == SLICE_TYPE_EXT_B)
                {
                    m_videoEncodeParams.m_isBframesPresent = true;
                    m_videoEncodeParams.m_bframeStartFrame = m_videoEncodeParams.m_FrameCount + 1;
                    LOG(info) << "H264 B-frames first detected at frame "
                             << m_videoEncodeParams.m_bframeStartFrame << endl;
                }
            }
            if (slice_type == SLICE_TYPE_I || slice_type == SLICE_TYPE_EXT_I)
            {
                is_idr_frame = true;
            }
        }
        if (isValidDataNAL(nal_type, "H264"))
        {
            ++m_videoEncodeParams.m_FrameCount;
        }
    }
    else if (iequals(m_videoEncodeParams.m_codec, "h265"))
    {
        H265NaluType h265_nal_type = parseH265NaluType(map.data, map.size);
        nal_type = static_cast<uint8_t>(h265_nal_type);
        is_idr_frame = isIDRFrame(nal_type, "H265");

        // Check for B-frames in H.265 VCL NAL units
        // We parse the slice_type from the slice header for accurate detection
        // NAL types 0-9 are VCL (Video Coding Layer) NAL units that contain slice data
        if (m_videoEncodeParams.m_isBframesPresent == false && h265_nal_type <= H265NaluType::RASL_R)
        {
            // Parse slice header to get the actual slice_type
            // This is the most reliable method as it reads the slice_type directly
            SliceType slice_type = parseH265SliceType(map.data, map.size, h265_nal_type);
            if (slice_type == SLICE_TYPE_B || slice_type == SLICE_TYPE_EXT_B)
            {
                m_videoEncodeParams.m_isBframesPresent = true;
                m_videoEncodeParams.m_bframeStartFrame = m_videoEncodeParams.m_FrameCount + 1;
                LOG(info) << "H265 B-frame detected, NAL type=" << (int)h265_nal_type
                          << ", slice_type=" << slice_type << " at frame " << m_videoEncodeParams.m_bframeStartFrame << endl;
            }
        }

        if (isValidDataNAL(nal_type, "H265"))
        {
            ++m_videoEncodeParams.m_FrameCount;
        }
    }

    if (is_idr_frame)
    {
        if (map.size >= MAX_TOLERABLE_IDR_FRAME_SIZE)
        {
            m_videoEncodeParams.m_isLargeIdrPresent = true;
        }

        // Calculate GOP interval (only after first keyframe)
        if (m_videoEncodeParams.m_prevIdrIndex > 0)
        {
            int gopInterval = m_videoEncodeParams.m_FrameCount - m_videoEncodeParams.m_prevIdrIndex;
            m_videoEncodeParams.m_keyFrameInterval = gopInterval;
            m_videoEncodeParams.m_gopIntervals.push_back(gopInterval);

            LOG(info) << "GOP interval detected: " << gopInterval
                     << " (frames " << m_videoEncodeParams.m_prevIdrIndex
                     << " to " << m_videoEncodeParams.m_FrameCount << ")" << endl;
        }

        m_videoEncodeParams.m_prevIdrIndex = m_videoEncodeParams.m_FrameCount;
        m_videoEncodeParams.analyzeGopPattern();
    }

    /* Unref the sample */
    gst_sample_unref (sample);

    /* Unmap the gst buffer */
    gst_buffer_unmap (gstBuffer, &map);

    return GST_FLOW_OK;
}

Json::Value GstkeyframeParser::parseKeyframeInterval (StreamParam params)
{
    int ret = 0;
    Json::Value result;
    string caps_string;
    GstCaps* filtercaps;
    string bitstream_type;

    LOG(info) << "parseKeyframeInterval file: container: " << params.m_inContainer << ", params.m_inFilePath: " << params.m_inFilePath << endl;
    if(gst_is_initialized() == false)
    {
        gst_init (nullptr, nullptr);
    }
    m_pipeline      = gst_pipeline_new ("pipeline");
    m_source        = gst_element_factory_make ("filesrc", nullptr);
    // Use the common utility API for container format detection and demuxer creation
    m_demuxer = createDemuxerForFile(params.m_inFilePath, params.m_inContainer);
    if (m_demuxer == nullptr)
    {
        LOG(error) << "File extension not supported or could not create demuxer for file: " << params.m_inFilePath << endl;
        ret = -1;
        goto error;
    }
    if (iequals(params.m_inCodec, "h264"))
    {
        m_videoParser   = gst_element_factory_make ("h264parse", nullptr);
        bitstream_type = "video/x-h264";
    }
    else if (iequals(params.m_inCodec, "h265"))
    {
        m_videoParser   = gst_element_factory_make ("h265parse", nullptr);
        bitstream_type = "video/x-h265";
    }
    else
    {
        LOG(error) << "Codec format not supported" << endl;
        ret = -1;
        goto error;
    }
    // Reset analysis state for clean analysis
    m_videoEncodeParams.reset();
    m_videoEncodeParams.m_codec = params.m_inCodec;

    m_videoQueue    = gst_element_factory_make ("queue", nullptr);
    m_capsFilter   = gst_element_factory_make ("capsfilter", nullptr);
    m_sink         = gst_element_factory_make ("appsink", nullptr);

    if (!m_pipeline || !m_source || !m_demuxer || !m_videoQueue || !m_videoParser || !m_sink)
    {
        LOG(error) << "Not all elements could be created, returning" << endl;
        ret = -1;
        goto error;
    }

#ifdef DEBUG
	g_signal_connect( m_pipeline, "deep-notify", G_CALLBACK( gst_object_default_deep_notify ), NULL );
#endif

    filtercaps = gst_caps_new_simple (bitstream_type.c_str(),
                "stream-format", G_TYPE_STRING, "byte-stream",
                "alignment", G_TYPE_STRING, "nal",
                NULL);
    g_object_set (G_OBJECT (m_capsFilter), "caps", filtercaps, NULL);
    gst_caps_unref (filtercaps);

    gst_bin_add_many (GST_BIN (m_pipeline), m_source, m_demuxer, m_videoQueue,
                    m_capsFilter, m_videoParser, m_sink, NULL);

    g_object_set (G_OBJECT (m_source), "location", params.m_inFilePath.c_str(), NULL);

    /* Link Video Elements */
    if (gst_element_link_many(m_source, m_demuxer, NULL) != TRUE)
    {
        LOG(error) << "Elements could not be linked, source and demuxer" << endl;
        ret = -1;
        goto error;
    }

    if (gst_element_link_many(m_videoQueue, m_videoParser, m_capsFilter, m_sink, NULL) != TRUE)
    {
        LOG(error) << "Multiple Elements could not be linked before decoder." << endl;
        ret = -1;
        goto error;
    }

    /* Add signal to link demuxer with audio/video queue */
    g_signal_connect (m_demuxer, "pad-added", G_CALLBACK (trans_on_pad_added),  this);
    g_object_set (G_OBJECT (m_sink), "emit-signals", TRUE, "sync", FALSE, NULL);

    g_signal_connect (m_sink, "new-sample", G_CALLBACK (on_new_sample_from_sink), (void*)this);

    if (gst_element_set_state (m_pipeline, GST_STATE_PLAYING) == GST_STATE_CHANGE_FAILURE)
    {
        LOG(error) << "Unable to set the transcode to playing state" << endl;
        ret = -1;
        goto error;
    }

    pollBusMessages();
    if (m_isError)
    {
        LOG(error) << "Error occured in pipeline, exiting" << endl;
        ret = -1;
        goto error;
    }
    else
    {
        int effective_keyint = m_videoEncodeParams.m_keyFrameInterval;
        if (effective_keyint == 0 && m_videoEncodeParams.m_FrameCount > 0) {
            effective_keyint = m_videoEncodeParams.m_FrameCount;
            LOG(info) << "No keyframe intervals detected, using frame count as fallback: " << effective_keyint << endl;
        }

        result["keyInt"] = effective_keyint;
        result["bFramesPresent"] = m_videoEncodeParams.m_isBframesPresent;
        result["largeIdrFramesPresent"] = m_videoEncodeParams.m_isLargeIdrPresent;
    }
error:
    if (m_pipeline)
    {
        gst_element_set_state (m_pipeline, GST_STATE_NULL);
        gst_element_get_state (m_pipeline, nullptr, nullptr, GST_SECOND);
        gst_object_unref (m_pipeline);
        m_pipeline = nullptr;
    }

    LOG(info) << "Exiting parseKeyframeInterval for " << params.m_inFilePath
             << ", keyFrameInterval:" << m_videoEncodeParams.m_keyFrameInterval
             << ", variableGOP:" << (m_videoEncodeParams.m_hasVariableGop ? "Yes" : "No")
             << ", B-frames start:" << m_videoEncodeParams.m_bframeStartFrame
             << ", GOP range:" << m_videoEncodeParams.m_minGopInterval
             << "-" << m_videoEncodeParams.m_maxGopInterval << endl;

    return (ret == 0) ? result : Json::nullValue;
}

GstDummyUdpPipeline* GstDummyUdpPipeline::m_instance = nullptr;
GstDummyUdpPipeline* GstDummyUdpPipeline::getInstance()
{
    if (m_instance == nullptr)
    {
        m_instance = new GstDummyUdpPipeline();
    }
    return m_instance;
}

void GstDummyUdpPipeline::deleteInstance()
{
    delete m_instance;
    m_instance = nullptr;
}

GstDummyUdpPipeline::~GstDummyUdpPipeline()
{
    LOG(warning) << "destroying GstDummyUdpPipeline" << endl;
}

int GstDummyUdpPipeline::startUdpPipeline(string id, int32_t audio_port,
                                    int32_t video_port, bool loop /*false*/)
{
    LOG(info) << __METHOD_NAME__ << "id: " << id << endl;

    // Check if pipeline already exists for given id.
    shared_ptr<gstElements> pipeline = getPipeline(id);
    if (pipeline.get())
    {
        LOG(warning) << "Udp pipeline already running for " << id << endl;
        return 0;
    }

    // Create pipeline and run
    pipeline = std::make_shared<gstElements>();
    int ret = createAndRunUdpPipeline(pipeline, audio_port, video_port, loop);

    // Insert pipeline in map
    if (ret == 0)
    {
        insertPipeline(id, pipeline);
    }

    return ret;
}

int GstDummyUdpPipeline::stopUdpPipeline(string id)
{
    LOG(info) << __METHOD_NAME__ << "id: " << id << endl;
    // Get pipeline from map
    shared_ptr<gstElements> pipeline = getPipeline(id);
    if (!pipeline.get())
    {
        LOG(info) << "Udp pipeline not present for " << id << endl;
        return -1;
    }

    // Stop pipeline
    if (destroyUdpPipeline(pipeline))
    {
        LOG(error) << "destroy UDP pieline failed for : " << id << endl;
    }
    erasePipeline(id);
    return 0;
}

void GstDummyUdpPipeline::stopAllUdpPipelines()
{
    LOG(info) << __METHOD_NAME__ << endl;
    std::lock_guard<std::mutex> peerlock(m_pipelineMapMutex);
    for (auto id_pipeline_pair : m_udpPipelines)
    {
        shared_ptr<gstElements> pipeline = id_pipeline_pair.second;
        destroyUdpPipeline(pipeline);
    }
    m_udpPipelines.clear();
}

std::shared_ptr<gstElements> GstDummyUdpPipeline::getPipeline(string id)
{
    std::lock_guard<std::mutex> peerlock(m_pipelineMapMutex);
    shared_ptr<gstElements> elements;
    std::unordered_map<std::string, shared_ptr<gstElements> >::iterator it = m_udpPipelines.find(id);
    if (it != m_udpPipelines.end())
    {
        elements = it->second;
    }
    return elements;
}

void GstDummyUdpPipeline::insertPipeline(string id, std::shared_ptr<gstElements> elements)
{
    LOG(info) << __METHOD_NAME__ << "id: " << id << endl;
    std::lock_guard<std::mutex> peerlock(m_pipelineMapMutex);
    m_udpPipelines.insert(std::pair<std::string, shared_ptr<gstElements> >(id, elements));
}

void GstDummyUdpPipeline::erasePipeline(string id)
{
    LOG(info) << __METHOD_NAME__ << "id: " << id << endl;
    std::lock_guard<std::mutex> peerlock(m_pipelineMapMutex);
    std::unordered_map<std::string, shared_ptr<gstElements> >::iterator it = m_udpPipelines.find(id);
    if (it != m_udpPipelines.end())
    {
        m_udpPipelines.erase(it);
    }
}

int createAndRunUdpPipeline(shared_ptr<gstElements> elements, int32_t audio_port,
                            int32_t video_port, bool loop /*false*/)
{
    LOG(info) << __METHOD_NAME__ << endl;
    if(gst_is_initialized() == false)
    {
        gst_init (nullptr, nullptr);
    }

    elements->m_playInLoop = loop;
    string file_path = "tools/data/sample_10sec_h264.mp4";
    GstCaps *filtercaps_audio;
    GstStateChangeReturn state_ret;

    elements->m_pipeline = gst_pipeline_new ("pipeline");
    GstElement* source   = gst_element_factory_make ("filesrc", nullptr);
    GstElement* demux    = gst_element_factory_make ("qtdemux", nullptr);

    // video elements
    elements->m_videoQueue      = gst_element_factory_make ("queue", nullptr);
    GstElement* video_payloader = gst_element_factory_make ("rtph264pay", nullptr);
    GstElement* video_sink      = gst_element_factory_make ("udpsink", nullptr);

    // audio elements
    elements->m_audioQueue      = gst_element_factory_make ("queue", nullptr);
    GstElement* audio_parse     = gst_element_factory_make ("aacparse", nullptr);
    GstElement* audio_dec       = gst_element_factory_make ("faad", nullptr);
    GstElement* audio_conv      = gst_element_factory_make ("audioconvert", nullptr);
    GstElement* audio_resample  = gst_element_factory_make ("audioresample", nullptr);
    GstElement* filter          = gst_element_factory_make ("capsfilter", nullptr);
    GstElement* audio_payloader = gst_element_factory_make ("rtpL16pay", nullptr);
    GstElement* audio_sink      = gst_element_factory_make ("udpsink", nullptr);

    if (!source || !demux)
    {
        LOG(error) << "source or demux elements not created" << endl;
        return -1;
    }
    if (!elements->m_videoQueue || !video_payloader || !video_sink)
    {
        LOG(error) << "Video elements not created" << endl;
        return -1;
    }
    if (!elements->m_audioQueue || !audio_parse || !audio_dec || !audio_conv ||
        !audio_resample || !filter || !audio_payloader || !audio_sink)
    {
        LOG(error) << "Audio elements not created" << endl;
        return -1;
    }

    gst_bin_add_many (
        GST_BIN (elements->m_pipeline), source, demux,
        elements->m_videoQueue, video_payloader, video_sink,
        elements->m_audioQueue, audio_parse, audio_dec, audio_conv,
            audio_resample, filter, audio_payloader, audio_sink, NULL);

    if (gst_element_link(source, demux) != TRUE)
    {
        LOG(error) << "Element source and demux could not be linked." << endl;
        goto error;
    }

    if (gst_element_link_many(elements->m_videoQueue, video_payloader, video_sink, NULL) != TRUE)
    {
        LOG(error) << "Video elements could not be linked." << endl;
        goto error;
    }

    if (gst_element_link_many(elements->m_audioQueue, audio_parse, audio_dec, audio_conv,
                audio_resample, filter, audio_payloader, audio_sink, NULL) != TRUE)
    {
        LOG(error) << "Audio elements could not be linked." << endl;
        goto error;
    }

    g_object_set (G_OBJECT (source), "location", file_path.c_str(), NULL);
    /* pad-added CB for demux element */
    if (!g_signal_connect(G_OBJECT(demux), "pad-added", G_CALLBACK(cb_udp_pad_added), elements.get()))
    {
        LOG(error) << "Error in g_signal_connect of pad-added" << endl;
        goto error;
    }

    g_object_set (G_OBJECT (video_payloader), "pt", 96, "config-interval", -1, NULL);
    g_object_set (G_OBJECT (video_sink), "host", "0.0.0.0", "port", video_port, NULL);
    g_object_set(G_OBJECT (video_sink), "buffer-size", UDP_BUFFER_SIZE, NULL);

    filtercaps_audio = gst_caps_new_simple ("audio/x-raw",
                        "channels", G_TYPE_INT, 1,
                        "rate", G_TYPE_INT, 16000,
                        NULL);
    g_object_set (G_OBJECT (filter), "caps", filtercaps_audio, NULL);
    gst_caps_unref (filtercaps_audio);
    g_object_set (G_OBJECT (audio_sink), "host", "0.0.0.0", "port", audio_port, NULL);

    if (loop)
    {
        elements->m_bus = gst_pipeline_get_bus (GST_PIPELINE (elements->m_pipeline));
        if (!elements->m_bus)
        {
            LOG(error) << "Failed to get BUS of De-Muxer playbin pipeline, will not play in loop" << endl;
        }
        elements->m_busWatchId = gst_bus_add_watch (elements->m_bus, udpBusWatchFunc, (void*)elements.get());
    }

    state_ret = gst_element_set_state (elements->m_pipeline, GST_STATE_PLAYING);
    if (state_ret == GST_STATE_CHANGE_FAILURE)
    {
        LOG(error) << "Unable to set UDP pipeline to playing state" << endl;
        goto error;
    }
    LOG(info) << "Started udp pipeline" << endl;
    return 0;
error:
    gst_object_unref(elements->m_pipeline);
    return -1;
}

int destroyUdpPipeline(shared_ptr<gstElements> elements)
{
    LOG(info) << __METHOD_NAME__ << endl;
    if (elements->m_busWatchId != G_MAXUINT)
    {
        g_source_remove (elements->m_busWatchId);
        elements->m_busWatchId = G_MAXUINT;
    }
    if (elements->m_bus)
    {
        gst_object_unref (elements->m_bus);
        elements->m_bus = nullptr;
    }
    if (elements->m_pipeline)
    {
        GstStateChangeReturn state_ret = gst_element_set_state (elements->m_pipeline, GST_STATE_NULL);
        if (state_ret == GST_STATE_CHANGE_FAILURE)
        {
            LOG(error) << "Unable to set UDP pipeline to NULL state" << endl;
            return -1;
        }
        gst_object_unref (elements->m_pipeline);
        elements->m_pipeline = nullptr;
        LOG(info) << "Stopped udp pipeline" << endl;
    }
    return 0;
}

static GstPadProbeReturn pad_cb(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
{
    gboolean res = false;
    gboolean fps_res = false;
    GstEvent *event = nullptr;
    GstCaps * caps = nullptr;
    event = GST_PAD_PROBE_INFO_EVENT(info);
    if (event)
    {
        if (GST_EVENT_CAPS == GST_EVENT_TYPE(event))
        {
            int width = 0, height = 0, numerator = 0, denominator = 0;
            gst_event_parse_caps(event, &caps);

            GstStructure *gstStruct = gst_caps_get_structure(caps, 0);
            if (gstStruct)
            {
                res = gst_structure_get_int (gstStruct, "width", &width);
                res |= gst_structure_get_int (gstStruct, "height", &height);
                fps_res = gst_structure_get_fraction (gstStruct, "framerate", &numerator, &denominator);
                if (!res && !fps_res)
                {
                    LOG (error) << "No resolution information received";
                }
                else
                {
                    gstElements* elements = (gstElements*)user_data;
                    elements->m_width  = width;
                    elements->m_height = height;
                    elements->m_fpsNumerator = numerator;
                    elements->m_fpsDenominator = denominator;
                    LOG (info) << "Resolution information received: Frame Size: "<< width << "x" << height << " fps = " << numerator << "/" << denominator << endl;
                    gst_element_send_event(elements->m_source, gst_event_new_eos());
                    return GST_PAD_PROBE_REMOVE;
                }
            }
            else
            {
                LOG (error) << "gst_caps_get_structure failed" << endl;
            }
        }
    }
    return GST_PAD_PROBE_OK;
}

Json::Value getRTSPStreamDetails (const string &url, std::string& codec, std::vector<std::vector<uint8_t>> sps_pps_idr_frames)
{
    LOG(info) << "Entry getRTSPStreamDetails for url = " << secureUrlForLogging(url) << endl;

    if(gst_is_initialized() == false)
    {
        gst_init (nullptr, nullptr);
    }

    GstBus* bus = nullptr;
    GstMessage* msg = nullptr;
    GstPad *srcpad = nullptr;
    GstStateChangeReturn state_ret;
    gstElements elements;
    Json::Value response;
    GstMapInfo    map;
    GstBuffer*    gstbuffer = nullptr;
    gboolean      map_ret   = false;
    GstFlowReturn ret       = GST_FLOW_OK;
    std::vector<uint8_t> merge_sps_pps_idr;

    elements.m_pipeline = gst_pipeline_new ("pipeline");
    elements.m_source   = gst_element_factory_make ("appsrc", nullptr);
    if (iequals(codec, "h265"))
    {
        elements.m_videoParser = gst_element_factory_make ("h265parse", nullptr);
    }
    else if (iequals(codec, "h264"))
    {
        elements.m_videoParser = gst_element_factory_make ("h264parse", nullptr);

    }
    else
    {
        LOG(error) << "Unsupported codec received " << codec << endl;
        goto error;
    }
    elements.m_sink = gst_element_factory_make ("fakesink", nullptr);

    if (!elements.m_pipeline || !elements.m_source || !elements.m_videoParser || !elements.m_sink)
    {
        LOG(error) << "Elements not created" << endl;
        goto error;
    }

    gst_bin_add_many (GST_BIN (elements.m_pipeline), elements.m_source, elements.m_videoParser, elements.m_sink, NULL);

    g_object_set (elements.m_source, "format", GST_FORMAT_TIME, NULL);
    g_object_set (elements.m_source, "is-live", true, NULL);
    g_object_set (elements.m_source, "do-timestamp", true, NULL);

    if (gst_element_link_many(elements.m_source, elements.m_videoParser, elements.m_sink, NULL) != TRUE)
    {
        LOG(error) << "Element m_sourcse, m_videoParser and m_sink could not be linked." << endl;
        goto error;
    }

    state_ret = gst_element_set_state (elements.m_pipeline, GST_STATE_PLAYING);

    if (state_ret == GST_STATE_CHANGE_FAILURE)
    {
        LOG(error) << "Unable to set pipeline to playing state" << endl;
        goto error;
    }

    srcpad = gst_element_get_static_pad (elements.m_videoParser, "src");
    /* Check if srcpad exists */
    if (!srcpad)
    {
        LOG(error) << "Failed to get src pad of m_videoParser." << endl;
        goto error;
    }

    /* Add probe to query width and height of video stream */
    gst_pad_add_probe(srcpad, GST_PAD_PROBE_TYPE_EVENT_BOTH, pad_cb, (void*)&elements, nullptr);
    gst_object_unref(srcpad);

    for (auto frame : sps_pps_idr_frames)
    {
        merge_sps_pps_idr.insert(merge_sps_pps_idr.end(), frame.begin(), frame.end());
    }

    gstbuffer = gst_buffer_new_allocate (nullptr, merge_sps_pps_idr.size(), nullptr);
    if (gstbuffer == nullptr)
    {
        LOG(error) << "gst_buffer_new_allocate failed" << endl;
        goto error;
    }
    /* Map the Gst Buffer to write the data */
    map_ret = gst_buffer_map (gstbuffer, &map, GST_MAP_WRITE);
    if (!map_ret)
    {
        LOG(error) << "gst_buffer_map failed" << endl;
        goto error;
    }

    memcpy (map.data, (uint8_t*)merge_sps_pps_idr.data(), merge_sps_pps_idr.size());
    map.size = merge_sps_pps_idr.size();

    /* Unmap the Gst Buffer */
    gst_buffer_unmap (gstbuffer, &map);
    ret = gst_app_src_push_buffer((GstAppSrc*)elements.m_source, gstbuffer);
    if (ret != GST_FLOW_OK)
    {
        LOG(error) << "Failed to push buffer in video appsrc queue, exiting muxer process thread" << endl;
        goto error;
    }

    ret = gst_app_src_end_of_stream (GST_APP_SRC ((GstAppSrc*)elements.m_source));
    if (ret != GST_FLOW_OK)
    {
        LOG(error) << "Failed to send EOS event in Video Pipeline" << endl;
        goto error;
    }

    /* Listen to the bus */
    bus = gst_element_get_bus (elements.m_pipeline);
    msg = gst_bus_timed_pop_filtered (bus, 10 * GST_SECOND,
        (GstMessageType)(GST_MESSAGE_ERROR | GST_MESSAGE_EOS));

    /* Parse message */
    if (msg != nullptr)
    {
        GError *err;
        gchar *debug_info;

        switch (GST_MESSAGE_TYPE (msg))
        {
            case GST_MESSAGE_ERROR:
                gst_message_parse_error (msg, &err, &debug_info);
                LOG(error) << "Error received from element " << GST_OBJECT_NAME (msg->src)
                            << " : " << err->message << endl;
                LOG(error) << "Debugging information: " << (debug_info ? debug_info : "none") << endl;;
                g_clear_error (&err);
                g_free (debug_info);
                elements.m_isError = true;
                break;

            case GST_MESSAGE_EOS:
                LOG(info) << "EOS Message received" << endl;
                break;

            default:
                /* We should not reach here */
                LOG(error) << "Unexpected message received" << endl;
                break;
        }
        gst_message_unref (msg);
    }
    else
    {
        LOG(warning) << "Breaking gst bus message loop with timeout of 10 " << endl;
    }

    if (elements.m_width)
    {
        response["width"] = to_string(elements.m_width);
    }
    if (elements.m_height)
    {
        response["height"] = to_string(elements.m_height);
    }
    if (elements.m_fpsNumerator && elements.m_fpsDenominator)
    {
        double fps = ceil((double)elements.m_fpsNumerator / (double)elements.m_fpsDenominator);
        response["frame_rate"] = to_string(fps);
    }
error:
    if (bus)
    {
        gst_object_unref (bus);
    }
    if (elements.m_pipeline)
    {
        gst_element_set_state (elements.m_pipeline, GST_STATE_NULL);
        gst_object_unref (elements.m_pipeline);
    }
    LOG(info) << "Exiting getRTSPStreamDetails url = " << secureUrlForLogging(url) << " width = " << response["width"]
              << " height = " << response["height"] << " fps = " << response["frame_rate"] << endl;
    return response;
}

bool isRecordedFileExist(const string& sensorId, const int64_t& epochStartTime, const int64_t& epochEndTime)
{
    /* Get list of files alongwith its associated timestamps */
    auto dbHelper = GET_DB_INSTANCE();
    std::vector<VideoFileInfo> fileNameArray = dbHelper->getFileList(sensorId, epochStartTime, epochEndTime);
    if (fileNameArray.size() == 0)
    {
        VideoFileInfo receivedFile = dbHelper->getInProgressRecordFile(sensorId, epochStartTime);
        if (!receivedFile.m_filePath.empty())
        {
            return true;
        }
        else
        {
            LOG(error) << "No recorded video files found" << endl;
            return false;
        }
    }
    return true;
}

// Container format detection and demuxer selection utilities
string detectContainerFormatFromExtension(const string& filePath)
{
    string extension = getFileExtension(filePath);
    if (extension.empty())
    {
        return "";
    }
    
    // Convert to lowercase for case-insensitive comparison
    std::transform(extension.begin(), extension.end(), extension.begin(), ::tolower);
    
    // Remove leading dot if present
    if (extension[0] == '.')
    {
        extension = extension.substr(1);
    }
    
    if (extension == "mp4" || extension == "mov" || extension == "m4v")
    {
        return CONTAINER_FORMAT_QUICKTIME;
    }
    else if (extension == "mkv" || extension == "webm")
    {
        return CONTAINER_FORMAT_MATROSKA;
    }
    
    return "";
}

string detectContainerFormatFromFile(const string& filePath)
{
    return detectContainerFormatFromExtension(filePath);
}

GstElement* createDemuxerForContainer(const string& containerFormat)
{
    if (isSubstringCaseInsensitive(containerFormat, CONTAINER_FORMAT_QUICKTIME))
    {
        return gst_element_factory_make("qtdemux", nullptr);
    }
    else if (isSubstringCaseInsensitive(containerFormat, CONTAINER_FORMAT_MATROSKA))
    {
        return gst_element_factory_make("matroskademux", nullptr);
    }
    
    return nullptr;
}

GstElement* createDemuxerForFile(const string& filePath, const string& containerFormat)
{
    string detectedFormat = containerFormat;
    
    // If no container format provided, detect from file extension
    if (containerFormat.empty() || (!isSubstringCaseInsensitive(containerFormat, CONTAINER_FORMAT_QUICKTIME) &&
    !isSubstringCaseInsensitive(containerFormat, CONTAINER_FORMAT_MATROSKA)))
    {
        detectedFormat = detectContainerFormatFromFile(filePath);
    }
    
    // If still no format detected, return nullptr
    if (detectedFormat.empty())
    {
        LOG(warning) << "Could not detect container format for file: " << filePath << endl;
        return nullptr;
    }
    
    return createDemuxerForContainer(detectedFormat);
}

GstElement* createMuxerForContainer(const string& containerFormat)
{
    if (isSubstringCaseInsensitive(containerFormat, CONTAINER_FORMAT_QUICKTIME))
    {
        return gst_element_factory_make("qtmux", nullptr);
    }
    else if (isSubstringCaseInsensitive(containerFormat, CONTAINER_FORMAT_MATROSKA))
    {
        return gst_element_factory_make("matroskamux", nullptr);
    }
    
    return nullptr;
}

GstElement* createMuxerForFile(const string& filePath, const string& containerFormat)
{
    string detectedFormat = containerFormat;
    
    // If no container format provided, detect from file extension
    if (containerFormat.empty() || (!isSubstringCaseInsensitive(containerFormat, CONTAINER_FORMAT_QUICKTIME) &&
    !isSubstringCaseInsensitive(containerFormat, CONTAINER_FORMAT_MATROSKA)))
    {
        detectedFormat = detectContainerFormatFromFile(filePath);
    }
    
    // If still no format detected, return nullptr
    if (detectedFormat.empty())
    {
        LOG(warning) << "Could not detect container format for muxer creation for file: " << filePath << endl;
        return nullptr;
    }
    
    return createMuxerForContainer(detectedFormat);
}

// Pad probe callback to add timestamps to buffers
static GstPadProbeReturn add_timestamps_probe(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
{
    MuxPipelineData* data = (MuxPipelineData*)user_data;
    GstBuffer *buffer = GST_PAD_PROBE_INFO_BUFFER(info);
    if (buffer)
    {
        // Calculate duration based on provided FPS
        GstClockTime duration = gst_util_uint64_scale_int(GST_SECOND, data->fps_den, data->fps_num);

        GST_BUFFER_PTS(buffer) = data->timestamp;
        GST_BUFFER_DTS(buffer) = data->timestamp;
        GST_BUFFER_DURATION(buffer) = duration;
        data->timestamp += duration;
    }
    return GST_PAD_PROBE_OK;
}


bool muxElementaryStream(const std::string& elementaryFilePath, const std::string& codec,
                        const std::string& containerFormat, std::string& outputFilePath, int32_t frameRate)
{
    LOG(info) << "muxElementaryStream: input=" << elementaryFilePath
              << ", codec=" << codec
              << ", container=" << containerFormat
              << ", frameRate=" << frameRate << endl;

    if(gst_is_initialized() == false)
    {
        gst_init (nullptr, nullptr);
    }

    // Determine output file extension based on container format
    std::string extension;
    GstElement* muxer = nullptr;
    if (iequals(containerFormat, "mp4"))
    {
        extension = ".mp4";
        muxer = gst_element_factory_make("qtmux", nullptr);
        if (muxer)
        {
            g_object_set(G_OBJECT(muxer), "faststart", TRUE, NULL);
        }
    }
    else if (iequals(containerFormat, "mkv"))
    {
        extension = ".mkv";
        muxer = gst_element_factory_make("matroskamux", nullptr);
        if (muxer)
        {
            // Configure matroskamux for streaming mode
            g_object_set(G_OBJECT(muxer), "streamable", TRUE, NULL);
        }
    }
    else
    {
        LOG(error) << "Unsupported container format: " << containerFormat << endl;
        return false;
    }

    // Generate output file path by replacing extension
    outputFilePath = elementaryFilePath;
    size_t lastDot = outputFilePath.find_last_of('.');
    if (lastDot != std::string::npos)
    {
        outputFilePath = outputFilePath.substr(0, lastDot);
    }
    outputFilePath += extension;

    // Create GStreamer elements
    GstElement* pipeline = gst_pipeline_new("mux-pipeline");
    GstElement* source = gst_element_factory_make("filesrc", nullptr);
    GstElement* parser = nullptr;
    GstElement* identity = gst_element_factory_make("identity", nullptr);
    GstElement* queue = gst_element_factory_make("queue", nullptr);
    GstElement* sink = gst_element_factory_make("filesink", nullptr);

    if (!pipeline || !source || !identity || !queue || !sink)
    {
        LOG(error) << "Failed to create basic pipeline elements" << endl;
        if (pipeline) gst_object_unref(pipeline);
        if (source) gst_object_unref(source);
        if (identity) gst_object_unref(identity);
        if (queue) gst_object_unref(queue);
        if (sink) gst_object_unref(sink);
        return false;
    }

    // Configure source for streaming mode
    g_object_set(G_OBJECT(source), "blocksize", 4096, NULL);

    // Configure identity to handle missing timestamps
    g_object_set(G_OBJECT(identity), "sync", FALSE, NULL);

    // Create parser based on codec
    if (iequals(codec, "h264"))
    {
        parser = gst_element_factory_make("h264parse", nullptr);
    }
    else if (iequals(codec, "h265"))
    {
        parser = gst_element_factory_make("h265parse", nullptr);
    }
    else
    {
        LOG(error) << "Unsupported codec: " << codec << endl;
        gst_object_unref(pipeline);
        return false;
    }

    if (!parser)
    {
        LOG(error) << "Failed to create parser for codec: " << codec << endl;
        gst_object_unref(pipeline);
        return false;
    }

    // Configure parser to handle timing and generate timestamps
    g_object_set(G_OBJECT(parser), "config-interval", -1, NULL);
    g_object_set(G_OBJECT(parser), "disable-passthrough", TRUE, NULL);

    // Configure queue to be more lenient with timestamps
    g_object_set(G_OBJECT(queue), "max-size-buffers", 0, NULL);
    g_object_set(G_OBJECT(queue), "max-size-time", 0, NULL);
    g_object_set(G_OBJECT(queue), "max-size-bytes", 0, NULL);

    if (!muxer)
    {
        LOG(error) << "Failed to create muxer for container: " << containerFormat << endl;
        gst_object_unref(pipeline);
        if (parser) gst_object_unref(parser);
        return false;
    }

    // Configure elements
    g_object_set(G_OBJECT(source), "location", elementaryFilePath.c_str(), NULL);
    g_object_set(G_OBJECT(sink), "location", outputFilePath.c_str(), NULL);

    // Add elements to pipeline
    gst_bin_add_many(GST_BIN(pipeline), source, parser, identity, queue, muxer, sink, NULL);

    // Link elements with identity and queue to handle buffering
    if (!gst_element_link_many(source, parser, identity, queue, muxer, sink, NULL))
    {
        LOG(error) << "Failed to link pipeline elements" << endl;
        gst_object_unref(pipeline);
        return false;
    }

    // Create data structure for probes and initialize with provided frameRate
    auto probe_data = std::make_unique<MuxPipelineData>();
    probe_data->timestamp = 0;
    probe_data->fps_num = frameRate;
    probe_data->fps_den = 1;

    LOG(info) << "Using provided frameRate: " << frameRate << " fps" << endl;

    // Add probe to identity src pad to generate timestamps
    GstPad* identity_src_pad = gst_element_get_static_pad(identity, "src");
    if (identity_src_pad)
    {
        gst_pad_add_probe(identity_src_pad, GST_PAD_PROBE_TYPE_BUFFER,
                         (GstPadProbeCallback)add_timestamps_probe, probe_data.get(), nullptr);
        gst_object_unref(identity_src_pad);
    }

    // Start pipeline
    GstStateChangeReturn ret = gst_element_set_state(pipeline, GST_STATE_PLAYING);
    if (ret == GST_STATE_CHANGE_FAILURE)
    {
        LOG(error) << "Failed to set pipeline to playing state" << endl;
        gst_object_unref(pipeline);
        return false;
    }

    // Wait for completion or error
    GstBus* bus = gst_element_get_bus(pipeline);
    GstMessage* msg = gst_bus_timed_pop_filtered(bus, GST_CLOCK_TIME_NONE,
                                                (GstMessageType)(GST_MESSAGE_ERROR | GST_MESSAGE_EOS));

    bool success = false;
    if (msg != nullptr)
    {
        if (GST_MESSAGE_TYPE(msg) == GST_MESSAGE_ERROR)
        {
            GError *err;
            gchar *debug_info;
            gst_message_parse_error(msg, &err, &debug_info);
            LOG(error) << "Error received from element " << GST_OBJECT_NAME(msg->src)
                      << ": " << err->message << endl;
            if (debug_info)
            {
                LOG(error) << "Debug info: " << debug_info << endl;
                g_free(debug_info);
            }
            g_clear_error(&err);
        }
        else if (GST_MESSAGE_TYPE(msg) == GST_MESSAGE_EOS)
        {
            LOG(info) << "Muxing completed successfully" << endl;
            success = true;
        }
        gst_message_unref(msg);
    }

    // Cleanup
    gst_object_unref(bus);
    gst_element_set_state(pipeline, GST_STATE_NULL);
    gst_object_unref(pipeline);

    // Delete source file if muxing was successful
    if (success)
    {
        if (unlink(elementaryFilePath.c_str()) != 0)
        {
            LOG(warning) << "Failed to delete elementary stream file: " << elementaryFilePath << endl;
        }
        else
        {
            LOG(info) << "Deleted elementary stream file: " << elementaryFilePath << endl;
        }
    }

    LOG(info) << "muxElementaryStream completed. Success=" << success
              << ", output=" << outputFilePath << endl;

    return success;
}