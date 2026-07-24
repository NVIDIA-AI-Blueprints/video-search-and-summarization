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

/* Surface audio-pipeline errors/warnings that would otherwise be swallowed (the
** audio decode pipeline had no bus watch, so a failing AAC decoder produced
** silence with no visible cause). Sync handler: runs on the posting thread,
** needs no GMainLoop. */
static GstBusSyncReply audio_bus_sync_handler (GstBus* /*bus*/, GstMessage* msg, gpointer /*user_data*/)
{
    if (GST_MESSAGE_TYPE (msg) == GST_MESSAGE_ERROR)
    {
        GError* err = nullptr; gchar* dbg = nullptr;
        gst_message_parse_error (msg, &err, &dbg);
        LOG(error) << "Audio Decoder: pipeline ERROR from " << GST_OBJECT_NAME (msg->src)
                   << ": " << (err ? err->message : "?") << " | " << (dbg ? dbg : "") << endl;
        if (err) g_error_free (err);
        g_free (dbg);
    }
    else if (GST_MESSAGE_TYPE (msg) == GST_MESSAGE_WARNING)
    {
        GError* err = nullptr; gchar* dbg = nullptr;
        gst_message_parse_warning (msg, &err, &dbg);
        LOG(warning) << "Audio Decoder: pipeline WARNING from " << GST_OBJECT_NAME (msg->src)
                     << ": " << (err ? err->message : "?") << " | " << (dbg ? dbg : "") << endl;
        if (err) g_error_free (err);
        g_free (dbg);
    }
    /* Drop (free) every message after logging. Nothing polls this bus -- there is
    ** no gst_bus_add_watch/GMainLoop -- so returning GST_BUS_PASS would queue each
    ** message on the async queue with no consumer, leaking memory for a long-lived
    ** RTSP stream. GST_BUS_DROP frees the message immediately. */
    return GST_BUS_DROP;
}

static void on_pad_added (GstElement *element, GstPad *pad, void *data)
{
    GstElement *element2 = (GstElement *)data;
    GstPad *sink_pad = nullptr;
    GstCaps *caps = nullptr;
    gchar *capsString = nullptr;

    /* decodebin exposes the decoder src pad before caps are negotiated for some
    ** decoders (e.g. avdec_aac negotiates lazily), so current caps can be NULL
    ** here. Fall back to the pad's queryable caps so we can still identify an
    ** audio pad. */
    caps = gst_pad_get_current_caps (pad);
    if (caps == nullptr)
    {
        caps = gst_pad_query_caps (pad, nullptr);
    }
    capsString = caps ? gst_caps_to_string (caps) : nullptr;
    sink_pad = gst_element_get_static_pad (element2, "sink");
    LOG (info) << "Caps = " << (capsString ? capsString : "NULL") << endl;

    /* This decodebin is fed audio-only caps (audio/x-mulaw or audio/mpeg), so
    ** its only src pad is the decoded audio. Link when caps confirm audio, and
    ** also when caps are still unresolved - refusing to link a not-yet-negotiated
    ** pad would leave the audio path dangling and drop audio entirely. */
    bool isAudio = (capsString != nullptr) && (g_strrstr (capsString, "audio") != nullptr);
    if (isAudio || capsString == nullptr)
    {
        if (!sink_pad)
        {
            LOG(error) << "Failed to get sink pad of element." << endl;
            GST_ELEMENT_ERROR(element2, STREAM, FAILED, ("Failed to get sink pad of element."), (nullptr));
        }
        else if (gst_pad_is_linked (sink_pad))
        {
            LOG(verbose) << "Audio decoder sink pad already linked" << endl;
        }
        else if (gst_pad_link (pad, sink_pad) != GST_PAD_LINK_OK)
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
    if (capsString != nullptr)
    {
        g_free(capsString);
    }
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
            /* The RTSP source codec (e.g. PCMU, MPEG4-GENERIC/AAC) is only known
            ** from the negotiated SDP, so capture it here. It selects the appsrc
            ** caps (and hence the decoder decodebin auto-plugs) in create(). */
            if (!it->second.codec.empty())
            {
                m_audioData.m_audioCodec = it->second.codec;
            }
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
/* Returns true if the RTSP audio codec name denotes MPEG-4 AAC. live555
** reports AAC subsessions as "MPEG4-GENERIC"; other stacks may say "AAC". */
static bool isAacCodec(const std::string& codec)
{
    std::string c(codec);
    std::transform(c.begin(), c.end(), c.begin(), [](unsigned char ch){ return std::tolower(ch); });
    return c.find("aac") != std::string::npos || c.find("mpeg4") != std::string::npos;
}

/* Build the MPEG-4 AudioSpecificConfig (codec_data) for AAC-LC from the
** stream's sample rate and channel count. The RTSP MPEG4-GENERIC depayloader
** delivers raw AAC access units (no ADTS header), so avdec_aac needs this
** config to decode. Layout follows ISO/IEC 14496-3 and matches the recording
** path in gstmux.cpp (CreateUnifiedStorage). Returns a new ref the caller
** must unref, or nullptr on failure. */
static GstBuffer* buildAacCodecData(int sampleRate, int channels)
{
    static const std::map<int, int, std::less<>> kAacFreqIdx = {
        {96000, 0}, {88200, 1}, {64000, 2}, {48000, 3}, {44100, 4},
        {32000, 5}, {24000, 6}, {22050, 7}, {16000, 8}, {12000, 9},
        {11025, 10}, {8000, 11}, {7350, 12}
    };
    auto fit = kAacFreqIdx.find(sampleRate);
    int  freqIdx = (fit != kAacFreqIdx.end()) ? fit->second : 8 /* 16000 */;
    const int aot = 2; // AAC LC

    unsigned int chanCfg;
    if (channels >= 1 && channels <= 6)
    {
        chanCfg = static_cast<unsigned int>(channels);
    }
    else if (channels == 8)
    {
        chanCfg = 7; // 7.1 surround (ISO/IEC 14496-3, Table 1.18)
    }
    else
    {
        LOG(warning) << "Unsupported AAC channel count " << channels
                     << "; using channel_configuration=2 (stereo) for codec_data" << endl;
        chanCfg = 2;
    }

    unsigned int asc = (static_cast<unsigned int>(aot)     << 11)
                     | (static_cast<unsigned int>(freqIdx) << 7)
                     | (chanCfg                            << 3);
    guint8 bytes[2];
    bytes[0] = static_cast<guint8>((asc >> 8) & 0xFF);
    bytes[1] = static_cast<guint8>( asc       & 0xFF);

    GstBuffer* buf = gst_buffer_new_allocate(nullptr, sizeof(bytes), nullptr);
    if (buf == nullptr)
    {
        return nullptr;
    }
    gst_buffer_fill(buf, 0, bytes, sizeof(bytes));
    LOG(info) << "AAC codec_data = " << std::hex << asc << std::dec
              << " (sample_rate=" << sampleRate << ", channels=" << channels
              << ", channel_configuration=" << chanCfg << ", aot=LC)" << endl;
    return buf;
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

    const bool isAac = isAacCodec (m_audioData.m_audioCodec);

    std::lock_guard<std::mutex> guard(m_pipelineLock);
    m_pipeline     = gst_pipeline_new ("audio_decoder_pipeline");
    m_source       = gst_element_factory_make ("appsrc", nullptr);
    m_audioconvert = gst_element_factory_make ("audioconvert", nullptr);
    m_capsfilter   = gst_element_factory_make ("capsfilter", nullptr);
    m_sink         = gst_element_factory_make ("appsink", nullptr);

    /* For AAC insert aacparse ahead of decodebin. The RTSP MPEG4-GENERIC
    ** depayloader delivers raw AAC access units with no ADTS header and no
    ** timestamps; decodebin autoplug over a live appsrc does not reliably frame
    ** those (it exposes a src pad but never emits a decoded buffer). aacparse,
    ** given the codec_data, frames the AUs deterministically so decodebin's
    ** auto-plugged AAC decoder produces output. decodebin (rather than a
    ** hard-coded avdec_aac) keeps us decoder-agnostic. For other codecs (PCMU)
    ** decodebin alone plugs mulawdec directly. */
    m_decoder = gst_element_factory_make ("decodebin", nullptr);
    if (isAac)
    {
        m_parser = gst_element_factory_make ("aacparse", nullptr);
    }

    /* Check if any of element failed to create */
    if (!m_pipeline || !m_source || !m_decoder || !m_audioconvert || !m_capsfilter || !m_sink ||
        (isAac && !m_parser))
    {
        LOG (error) << "Gstreamer element creation failed (isAac=" << isAac
                    << ", aacparse/decodebin available?) for uri = " << m_uri << endl;
        /* None of these are parented to the bin yet (gst_bin_add_many runs
        ** below), so destroy() -- which only unrefs m_pipeline -- would leak
        ** every other created element. Unref the ones that got made here. */
        for (GstElement** e : { &m_pipeline, &m_source, &m_decoder, &m_parser,
                                &m_audioconvert, &m_capsfilter, &m_sink })
        {
            if (*e)
            {
                gst_object_unref (*e);
                *e = nullptr;
            }
        }
        return -1;
    }

    gst_bin_add_many (GST_BIN (m_pipeline), m_source, m_decoder, m_audioconvert, m_capsfilter, m_sink, nullptr);
    if (isAac)
    {
        gst_bin_add (GST_BIN (m_pipeline), m_parser);
    }

    /* Surface otherwise-swallowed audio decode errors on the pipeline bus. */
    {
        GstBus* bus = gst_element_get_bus (m_pipeline);
        if (bus)
        {
            gst_bus_set_sync_handler (bus, audio_bus_sync_handler, this, nullptr);
            gst_object_unref (bus);
        }
    }

    /* Select the appsrc caps from the negotiated RTSP audio codec. The
    ** downstream audioconvert + capsfilter normalise the decoder output to
    ** interleaved S16LE, which is what the WebRTC audio sink expects
    ** (on_new_sample_from_sink treats samples as 16-bit). */
    if (isAac)
    {
        capsSrc = gst_caps_new_simple ("audio/mpeg",
                                       "mpegversion",   G_TYPE_INT,    4,
                                       "stream-format", G_TYPE_STRING, "raw",
                                       "rate",          G_TYPE_INT,    m_audioData.m_freq,
                                       "channels",      G_TYPE_INT,    m_audioData.m_channel,
                                       nullptr);
        GstBuffer* codecData = buildAacCodecData (m_audioData.m_freq, m_audioData.m_channel);
        if (codecData != nullptr)
        {
            gst_caps_set_simple (capsSrc, "codec_data", GST_TYPE_BUFFER, codecData, nullptr);
            gst_buffer_unref (codecData);
        }
        LOG(info) << "Audio Decoder appsrc caps = audio/mpeg (AAC) mpegversion=4 stream-format=raw rate="
                  << m_audioData.m_freq << " channels=" << m_audioData.m_channel
                  << " codec=" << m_audioData.m_audioCodec << endl;
    }
    else
    {
        std::string caps_string = "audio/x-mulaw, format=(string)S8, rate=(int)" + to_string(m_audioData.m_freq) +
                                  ", channels=(int)" + to_string(m_audioData.m_channel);
        capsSrc = gst_caps_from_string (caps_string.c_str());
        LOG(info) << "Audio Decoder appsrc caps = " << caps_string
                  << " codec=" << m_audioData.m_audioCodec << endl;
    }

    /* Force the decoded output format the WebRTC sink consumes. */
    GstCaps* sinkCaps = gst_caps_from_string ("audio/x-raw, format=(string)S16LE, layout=(string)interleaved");

    /* Setting properties of elements. do-timestamp stamps the untimed RTSP AUs
    ** with arrival running-time so aacparse/avdec_aac can frame and decode. */
    g_object_set (G_OBJECT (m_source), "caps", capsSrc, nullptr);
    g_object_set (G_OBJECT (m_source), "is-live", true, nullptr);
    g_object_set (G_OBJECT (m_source), "do-timestamp", true, nullptr);
    g_object_set (G_OBJECT (m_source), "format", 3, nullptr);
    g_object_set (G_OBJECT (m_capsfilter), "caps", sinkCaps, nullptr);
    g_object_set (G_OBJECT (m_sink)  , "emit-signals", TRUE, "sync", FALSE, nullptr);
    gst_caps_unref (capsSrc);
    gst_caps_unref (sinkCaps);

    /* audioconvert -> capsfilter -> appsink is a static link in both paths. */
    if (!gst_element_link_many (m_audioconvert, m_capsfilter, m_sink, nullptr))
    {
        LOG (error) << "Audio Decoder: audioconvert/capsfilter/sink could not be linked for uri = " << m_uri << endl;
        return -1;
    }

#ifdef DEBUG
    g_signal_connect( m_pipeline, "deep-notify", G_CALLBACK( gst_object_default_deep_notify ), nullptr );
#endif

    /* appsrc -> [aacparse ->] decodebin. aacparse (AAC only) frames the raw AUs
    ** before decodebin. decodebin's src pad is dynamic and is linked to
    ** audioconvert in the pad-added callback for both paths. */
    bool sourceLinked = isAac ? gst_element_link_many (m_source, m_parser, m_decoder, nullptr)
                              : gst_element_link_many (m_source, m_decoder, nullptr);
    if (!sourceLinked)
    {
        LOG (error) << "Audio Decoder: source/parser/decoder elements could not be linked for uri = " << m_uri << endl;
        return -1;
    }
    if (!link_decoder(m_decoder, m_audioconvert))
    {
        LOG (error) << "Audio Decoder: decoder and audioconvert elements could not be linked for uri = " << m_uri << endl;
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