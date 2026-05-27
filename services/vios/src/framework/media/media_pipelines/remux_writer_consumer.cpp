/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "remux_writer_consumer.h"
#include "logger.h"
#include "utils.h"

#include <gst/gst.h>
#include <gst/app/gstappsrc.h>

/* Macro defined in splitmuxsrc plugin */
#define FIXED_TS_OFFSET (1000*GST_SECOND)

namespace {

static const std::string kEmptyLogPrefix;
static inline const std::string& getLogPrefix(const std::string* prefix)
{
    return prefix ? *prefix : kEmptyLogPrefix;
}

// Probe context for end-time enforcement on mux sink
struct MuxProbeCtx {
    int64_t end_ms = std::numeric_limits<int64_t>::max();
    int64_t frameThresholdMs = 0;
    int64_t prevBufMs = 0;
    GstElement* mux = nullptr;
    GstAppSrc* videoSrc = nullptr;
    GstAppSrc* audioSrc = nullptr;
    const std::string* log_prefix = nullptr;
};

struct RemuxAudioPadCtx {
    GstPad* mux_sink = nullptr;
    const std::string* log_prefix = nullptr;
};

} // anonymous namespace

RemuxWriterConsumer::RemuxWriterConsumer(const RemuxWriterConfig& cfg)
    : IMediaDataConsumer("RemuxWriterConsumer"), mCfg(cfg)
{
    mLogPrefix = mCfg.log_id.empty() ? "" : ("[" + mCfg.log_id + "] ");
}

RemuxWriterConsumer::~RemuxWriterConsumer()
{
    try {
        stop();
        teardown();
    } catch (const std::exception& e) {
        try { LOG(error) << "Exception in ~RemuxWriterConsumer: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
    } catch (...) {
        try { LOG(error) << "Unknown exception in ~RemuxWriterConsumer" << endl; } catch (...) { (void)std::current_exception(); }
    }
}

bool RemuxWriterConsumer::buildPipeline()
{
    LOG(info) << mLogPrefix << "RemuxWriter: building pipeline, codec=" << mCfg.video_codec
              << ", container=" << mCfg.container << ", audio=" << (mCfg.enable_audio ? "yes" : "no") << endl;

    mPipeline = gst_pipeline_new("remux_writer_pipeline");
    mVideoAppsrc = gst_element_factory_make("appsrc", "video_appsrc");
    mVideoParser = gst_element_factory_make(iequals(mCfg.video_codec, "h265") ? "h265parse" : "h264parse", "video_parser");
    mFilesink = gst_element_factory_make("filesink", "filesink");

    if (!mPipeline || !mVideoAppsrc || !mVideoParser || !mFilesink)
    {
        LOG(error) << mLogPrefix << "RemuxWriter: failed to create core elements" << endl;
        return false;
    }

    // Configure video appsrc
    // IMPORTANT: block=FALSE to prevent indefinite blocking if downstream errors/stalls
    // This allows the pipeline to handle errors gracefully without hanging
    //
    // Initial caps declare only codec + AU alignment; the exact stream-format
    // (avc/hvc1 vs byte-stream) is overridden by the first sample's caps in
    // onFrame(). Hard-coding "byte-stream" here caused mp4mux failures
    // ("Buffer has no PTS") when the producer actually pushed AVC-formatted
    // (length-prefixed) buffers (e.g. matroska-backed files via giosrc).
    {
        GstCaps* caps = gst_caps_new_simple(iequals(mCfg.video_codec, "h265") ? "video/x-h265" : "video/x-h264",
                                            "alignment", G_TYPE_STRING, "au",
                                            nullptr);
        g_object_set(G_OBJECT(mVideoAppsrc), "caps", caps, "format", GST_FORMAT_TIME,
                     "is-live", FALSE, "block", FALSE, "do-timestamp", FALSE,
                     "max-bytes", (guint64)2000000,  // 2MB max queue for backpressure
                     nullptr);
        gst_caps_unref(caps);
    }

    // Create container-specific elements (mux and optional capsfilter)
    if (!createContainerElements())
    {
        return false;
    }

    // Set output file location
    if (!mCfg.output_file.empty())
    {
        g_object_set(G_OBJECT(mFilesink), "location", mCfg.output_file.c_str(),
                     "sync", FALSE, "buffer-size", 0, nullptr);
        LOG(info) << mLogPrefix << "RemuxWriter: output file set to " << mCfg.output_file << endl;
    }

    // Add video elements to pipeline
    gst_bin_add_many(GST_BIN(mPipeline), mVideoAppsrc, mVideoParser, nullptr);
    if (mVideoCapsfilter)
    {
        gst_bin_add(GST_BIN(mPipeline), mVideoCapsfilter);
    }
    gst_bin_add_many(GST_BIN(mPipeline), mMux, mFilesink, nullptr);

    // Create audio branch if enabled
    // For remux, we preserve the original audio codec using parsebin for auto-detection
    // No transcoding - just repackaging into the new container
    if (mCfg.enable_audio)
    {
        mAudioAppsrc = gst_element_factory_make("appsrc", "audio_appsrc");
        mAudioQueue = gst_element_factory_make("queue", "audio_queue");
        mAudioParser = gst_element_factory_make("parsebin", "audio_parser");

        if (!mAudioAppsrc || !mAudioQueue || !mAudioParser)
        {
            LOG(warning) << mLogPrefix << "RemuxWriter: failed to create audio elements, continuing without audio" << endl;
            // Clean up partial audio elements
            if (mAudioAppsrc) { gst_object_unref(mAudioAppsrc); mAudioAppsrc = nullptr; }
            if (mAudioQueue) { gst_object_unref(mAudioQueue); mAudioQueue = nullptr; }
            if (mAudioParser) { gst_object_unref(mAudioParser); mAudioParser = nullptr; }
        }
        else
        {
            // Configure audio appsrc
            g_object_set(G_OBJECT(mAudioAppsrc), "format", GST_FORMAT_TIME,
                         "is-live", FALSE, "block", FALSE, "do-timestamp", FALSE,
                         "stream-type", 0,  // GST_APP_STREAM_TYPE_STREAM
                         nullptr);

            gst_bin_add_many(GST_BIN(mPipeline), mAudioAppsrc, mAudioQueue, mAudioParser, nullptr);

            LOG(info) << mLogPrefix << "RemuxWriter: audio branch created (appsrc → queue → parsebin)" << endl;
        }
    }

    return true;
}

bool RemuxWriterConsumer::createContainerElements()
{
    if (iequals(mCfg.container, "mp4"))
    {
        mVideoCapsfilter = gst_element_factory_make("capsfilter", "video_capsfilter");
        mMux = gst_element_factory_make("mp4mux", "mux");
        
        if (!mVideoCapsfilter || !mMux)
        {
            LOG(error) << mLogPrefix << "RemuxWriter: failed to create mp4 elements" << endl;
            return false;
        }
        
        // Set caps for MP4 (avc/hvc1 stream format)
        if (iequals(mCfg.video_codec, "h265"))
        {
            GstCaps* c = gst_caps_new_simple("video/x-h265",
                                           "stream-format", G_TYPE_STRING, "hvc1",
                                           "alignment", G_TYPE_STRING, "au",
                                           nullptr);
            g_object_set(G_OBJECT(mVideoCapsfilter), "caps", c, nullptr);
            gst_caps_unref(c);
        }
        else
        {
            GstCaps* c = gst_caps_new_simple("video/x-h264",
                                           "stream-format", G_TYPE_STRING, "avc",
                                           "alignment", G_TYPE_STRING, "au",
                                           nullptr);
            g_object_set(G_OBJECT(mVideoCapsfilter), "caps", c, nullptr);
            gst_caps_unref(c);
        }
        g_object_set(mMux, "faststart", TRUE, nullptr);
        return true;
    }
    else if (iequals(mCfg.container, "mkv"))
    {
        mMux = gst_element_factory_make("matroskamux", "mux");
        if (!mMux)
        {
            LOG(error) << mLogPrefix << "RemuxWriter: failed to create mkv mux" << endl;
            return false;
        }
        return true;
    }
    else // default to mpegts
    {
        mMux = gst_element_factory_make("mpegtsmux", "mux");
        if (!mMux)
        {
            LOG(error) << mLogPrefix << "RemuxWriter: failed to create ts mux" << endl;
            return false;
        }
        return true;
    }
}

void RemuxWriterConsumer::configureParserForMp4()
{
    if (!mVideoParser) return;
    
    // For MP4, ensure parser emits headers at the start
    // config-interval=-1 forces SPS/PPS insertion before every IDR (critical after seek)
    g_object_set(G_OBJECT(mVideoParser), "config-interval", -1, "disable-passthrough", TRUE, nullptr);
}

bool RemuxWriterConsumer::linkPipeline()
{
    // Configure parser for MP4 if needed
    if (iequals(mCfg.container, "mp4"))
    {
        configureParserForMp4();
    }

    // Link video path
    if (mVideoCapsfilter)
    {
        // appsrc → parser → capsfilter → mux
        if (!gst_element_link_many(mVideoAppsrc, mVideoParser, mVideoCapsfilter, nullptr))
        {
            LOG(error) << mLogPrefix << "RemuxWriter: failed to link video appsrc → parser → capsfilter" << endl;
            return false;
        }
        
        // Request pad from mux for video
        GstPad* mux_video_sink = gst_element_request_pad_simple(mMux, "video_%u");
        if (!mux_video_sink)
        {
            LOG(error) << mLogPrefix << "RemuxWriter: failed to request video sink pad from mux" << endl;
            return false;
        }
        GstPad* capsfilter_src = gst_element_get_static_pad(mVideoCapsfilter, "src");
        if (gst_pad_link(capsfilter_src, mux_video_sink) != GST_PAD_LINK_OK)
        {
            LOG(error) << mLogPrefix << "RemuxWriter: failed to link capsfilter to mux" << endl;
            gst_object_unref(capsfilter_src);
            gst_object_unref(mux_video_sink);
            return false;
        }
        gst_object_unref(capsfilter_src);
        gst_object_unref(mux_video_sink);
    }
    else
    {
        // appsrc → parser → mux (for mkv/ts)
        if (!gst_element_link_many(mVideoAppsrc, mVideoParser, mMux, nullptr))
        {
            LOG(error) << mLogPrefix << "RemuxWriter: failed to link video path" << endl;
            return false;
        }
    }

    // Link mux → filesink
    if (!gst_element_link(mMux, mFilesink))
    {
        LOG(error) << mLogPrefix << "RemuxWriter: failed to link mux → filesink" << endl;
        return false;
    }

    // Link audio path if present (using parsebin for auto codec detection)
    // parsebin has dynamic pads, so we connect via pad-added signal
    // IMPORTANT: Must request the mux audio pad BEFORE pipeline starts (qtmux requirement)
    if (mAudioAppsrc && mAudioQueue && mAudioParser)
    {
        // Link: appsrc → queue → parsebin
        if (!gst_element_link(mAudioAppsrc, mAudioQueue))
        {
            LOG(error) << mLogPrefix << "RemuxWriter: failed to link audio appsrc → queue" << endl;
            return false;
        }
        if (!gst_element_link(mAudioQueue, mAudioParser))
        {
            LOG(error) << mLogPrefix << "RemuxWriter: failed to link audio queue → parsebin" << endl;
            return false;
        }

        // Pre-request audio sink pad from mux BEFORE pipeline starts
        // qtmux/mp4mux refuses to provide request pads after stream start
        if (iequals(mCfg.container, "mp4") || iequals(mCfg.container, "mkv"))
        {
            mMuxAudioSinkPad = gst_element_request_pad_simple(mMux, "audio_%u");
        }
        else // ts
        {
            mMuxAudioSinkPad = gst_element_request_pad_simple(mMux, "sink_%d");
        }

        if (!mMuxAudioSinkPad)
        {
            LOG(warning) << mLogPrefix << "RemuxWriter: failed to pre-request audio sink pad from mux" << endl;
        }
        else
        {
            LOG(info) << mLogPrefix << "RemuxWriter: pre-requested audio sink pad from mux" << endl;
        }

        // Connect pad-added signal for dynamic linking from parsebin to the pre-requested mux pad
        RemuxAudioPadCtx* pad_ctx = (RemuxAudioPadCtx*)g_new0(RemuxAudioPadCtx, 1);
        pad_ctx->mux_sink = mMuxAudioSinkPad;
        pad_ctx->log_prefix = &mLogPrefix;
        g_signal_connect_data(mAudioParser, "pad-added", G_CALLBACK(+[](GstElement* parsebin, GstPad* src_pad, gpointer user_data) {
            RemuxAudioPadCtx* ctx = static_cast<RemuxAudioPadCtx*>(user_data);
            GstPad* mux_sink = ctx ? ctx->mux_sink : nullptr;
            if (!mux_sink)
            {
                LOG(error) << getLogPrefix(ctx ? ctx->log_prefix : nullptr)
                           << "RemuxWriter: no pre-requested mux audio sink pad available" << endl;
                return;
            }

            // Only link audio pads
            GstCaps* caps = gst_pad_get_current_caps(src_pad);
            if (!caps) caps = gst_pad_query_caps(src_pad, nullptr);
            if (caps)
            {
                GstStructure* s = gst_caps_get_structure(caps, 0);
                const gchar* name = gst_structure_get_name(s);
                if (name && g_str_has_prefix(name, "audio/"))
                {
                    GstPadLinkReturn ret = gst_pad_link(src_pad, mux_sink);
                    if (ret == GST_PAD_LINK_OK)
                    {
                        LOG(info) << getLogPrefix(ctx ? ctx->log_prefix : nullptr)
                                  << "RemuxWriter: parsebin audio pad linked to mux successfully" << endl;
                    }
                    else
                    {
                        LOG(error) << getLogPrefix(ctx ? ctx->log_prefix : nullptr)
                                   << "RemuxWriter: failed to link parsebin to mux, ret=" << ret << endl;
                    }
                }
                gst_caps_unref(caps);
            }
        }), pad_ctx, (GClosureNotify)g_free, (GConnectFlags)0);

        LOG(info) << mLogPrefix << "RemuxWriter: audio path set up (appsrc → queue → parsebin → mux) with dynamic pad handling" << endl;
    }

    LOG(info) << mLogPrefix << "RemuxWriter: pipeline linked successfully" << endl;
    return true;
}

void RemuxWriterConsumer::attachProbes()
{
    // Attach EOS enforcement probe on mux sink (for end_time_ms)
    if (mMux && mCfg.end_time_ms != std::numeric_limits<int64_t>::max())
    {
        MuxProbeCtx* mctx = new MuxProbeCtx();
        mctx->end_ms = mCfg.end_time_ms;
        mctx->mux = mMux;
        mctx->videoSrc = mVideoAppsrc ? GST_APP_SRC(mVideoAppsrc) : nullptr;
        mctx->audioSrc = mAudioAppsrc ? GST_APP_SRC(mAudioAppsrc) : nullptr;
        mctx->log_prefix = &mLogPrefix;

        GstIterator* it = gst_element_iterate_sink_pads(mMux);
        if (it)
        {
            GValue item = G_VALUE_INIT;
            gboolean done = FALSE;
            while (!done)
            {
                switch (gst_iterator_next(it, &item))
                {
                    case GST_ITERATOR_OK:
                    {
                        GstPad* sink_pad = static_cast<GstPad*>(g_value_get_object(&item));
                        if (sink_pad)
                        {
                            gst_pad_add_probe(sink_pad, GST_PAD_PROBE_TYPE_BUFFER,
                                +[](GstPad* pad, GstPadProbeInfo* info, gpointer user_data) -> GstPadProbeReturn {
                                    MuxProbeCtx* c = static_cast<MuxProbeCtx*>(user_data);
                                    if (!c) return GST_PAD_PROBE_OK;

                                    if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER)
                                    {
                                        GstBuffer* b = GST_PAD_PROBE_INFO_BUFFER(info);
                                        if (b)
                                        {
                                            int64_t buf_time = GST_BUFFER_PTS_IS_VALID(b) ? (GST_BUFFER_PTS(b) / 1000000) : -1;

                                            // Calculate frame threshold
                                            if (c->frameThresholdMs == 0 && c->prevBufMs != 0 && buf_time >= 0)
                                            {
                                                c->frameThresholdMs = buf_time - c->prevBufMs;
                                            }
                                            c->prevBufMs = buf_time;
                                        }
                                    }
                                    return GST_PAD_PROBE_OK;
                                }, mctx, (GDestroyNotify)+[](gpointer data) { delete static_cast<MuxProbeCtx*>(data); });

                            g_value_reset(&item);
                            done = TRUE;  // First video pad is enough
                            continue;
                        }
                        g_value_reset(&item);
                        break;
                    }
                    case GST_ITERATOR_RESYNC: gst_iterator_resync(it); break;
                    case GST_ITERATOR_ERROR:
                    case GST_ITERATOR_DONE: done = TRUE; break;
                }
            }
            g_value_unset(&item);
            gst_iterator_free(it);
        }
    }
}

bool RemuxWriterConsumer::start()
{
    if (mRunning.load()) return true;
    if (!gst_is_initialized()) { gst_init(nullptr, nullptr); }

    if (!buildPipeline())
    {
        return false;
    }

    if (!linkPipeline())
    {
        teardown();
        return false;
    }

    attachProbes();

    if (gst_element_set_state(mPipeline, GST_STATE_PLAYING) == GST_STATE_CHANGE_FAILURE)
    {
        LOG(error) << mLogPrefix << "RemuxWriter: failed to set pipeline to PLAYING" << endl;
        teardown();
        return false;
    }

    mRunning.store(true);
    LOG(info) << mLogPrefix << "RemuxWriter: pipeline started" << endl;
    return true;
}

void RemuxWriterConsumer::stop()
{
    mRunning.store(false);
    if (!mPipeline) return;

    LOG(info) << mLogPrefix << "RemuxWriterConsumer::stop() - Setting pipeline to NULL state" << endl;
    GstStateChangeReturn ret = gst_element_set_state(mPipeline, GST_STATE_NULL);
    if (ret == GST_STATE_CHANGE_ASYNC)
    {
        GstState current, pending;
        ret = gst_element_get_state(mPipeline, &current, &pending, 5 * GST_SECOND);
        if (ret == GST_STATE_CHANGE_FAILURE || ret == GST_STATE_CHANGE_ASYNC)
        {
            LOG(error) << mLogPrefix << "RemuxWriterConsumer::stop() - Pipeline failed to reach NULL state within timeout" << endl;
        }
    }
    LOG(info) << mLogPrefix << "RemuxWriterConsumer::stop() - Pipeline stopped" << endl;
}

void RemuxWriterConsumer::teardown()
{
    // Release the pre-requested audio sink pad before destroying the pipeline
    if (mMuxAudioSinkPad)
    {
        if (mMux)
        {
            gst_element_release_request_pad(mMux, mMuxAudioSinkPad);
        }
        gst_object_unref(mMuxAudioSinkPad);
        mMuxAudioSinkPad = nullptr;
    }

    if (mPipeline)
    {
        gst_object_unref(mPipeline);
        mPipeline = nullptr;
    }
    // Elements are owned by pipeline, just null out references
    mVideoAppsrc = nullptr;
    mVideoParser = nullptr;
    mVideoCapsfilter = nullptr;
    mMux = nullptr;
    mFilesink = nullptr;
    mAudioAppsrc = nullptr;
    mAudioQueue = nullptr;
    mAudioParser = nullptr;
}

bool RemuxWriterConsumer::waitForCompletion(int64_t timeout_secs)
{
    if (!mPipeline) return false;

    GstBus* bus = gst_element_get_bus(mPipeline);
    if (!bus) return false;

    LOG(info) << mLogPrefix << "RemuxWriter: waiting for completion (timeout=" << timeout_secs << "s)" << endl;

    GstMessage* msg = gst_bus_timed_pop_filtered(bus, timeout_secs * GST_SECOND,
        (GstMessageType)(GST_MESSAGE_ERROR | GST_MESSAGE_EOS));

    bool got = false;
    if (msg)
    {
        switch (GST_MESSAGE_TYPE(msg))
        {
            case GST_MESSAGE_ERROR:
            {
                GError* err = nullptr;
                gchar* dbg = nullptr;
                gst_message_parse_error(msg, &err, &dbg);
                gchar* src_name = gst_object_get_path_string(msg->src);
                LOG(error) << mLogPrefix << "RemuxWriter ERROR from " << (src_name ? src_name : "<unknown>")
                          << ": " << (err ? err->message : "<null>") << endl;
                if (dbg)
                {
                    LOG(error) << mLogPrefix << "Debug info: " << dbg << endl;
                    g_free(dbg);
                }
                if (err) g_error_free(err);
                if (src_name) g_free(src_name);
                mError.store(true);
                got = true;
                break;
            }
            case GST_MESSAGE_EOS:
                LOG(info) << mLogPrefix << "RemuxWriter: EOS received" << endl;
                got = true;
                break;
            default:
                break;
        }
        gst_message_unref(msg);
    }
    else
    {
        LOG(warning) << mLogPrefix << "RemuxWriter: bus timeout after " << timeout_secs << " seconds" << endl;
    }

    gst_object_unref(bus);
    return got;
}

void RemuxWriterConsumer::onFrame(std::shared_ptr<RawFrameParams> frame_data)
{
    if (!mRunning.load() || !frame_data || !mVideoAppsrc) return;

    if (frame_data->m_gstBuffer && GST_IS_BUFFER(frame_data->m_gstBuffer))
    {
        // Propagate caps from producer to video appsrc on the first sample.
        // Without this, the appsrc's generic codec-only caps leave h264parse
        // unable to determine stream-format (avc vs byte-stream) with
        // certainty, which can cause buffer reframing and PTS loss
        // (resulting in "Buffer has no PTS" errors from mp4mux).
        if (frame_data->m_caps && !mVideoCapsSet)
        {
            g_object_set(G_OBJECT(mVideoAppsrc), "caps", frame_data->m_caps, nullptr);
            mVideoCapsSet = true;
            gchar* cstr = gst_caps_to_string(frame_data->m_caps);
            LOG(info) << mLogPrefix << "RemuxWriter(video): Set video appsrc caps: "
                      << (cstr ? cstr : "<null>") << endl;
            if (cstr) g_free(cstr);
        }

        {
            GstClockTime pts = GST_BUFFER_PTS(frame_data->m_gstBuffer);
            if (GST_CLOCK_TIME_IS_VALID(pts))
            {
                int64_t pts_ms = pts / GST_MSECOND;
                // Subtract FIXED_TS_OFFSET (1000 seconds = 1000000 ms) to get offset from file start
                int64_t offset_from_file_start_ms = (pts_ms >= 1000000) ? (pts_ms - 1000000) : pts_ms;
                mActualEndPtsMs.store(offset_from_file_start_ms);
                if (!mFirstFrameReceived.load())
                {
                    mActualStartPtsMs.store(offset_from_file_start_ms);
                    mFirstFrameReceived.store(true);
                    LOG(info) << mLogPrefix << "RemuxWriter: First frame offset from file start = " << offset_from_file_start_ms << " ms" << endl;
                }
            }
        }

        GstFlowReturn ret = gst_app_src_push_buffer(GST_APP_SRC(mVideoAppsrc), gst_buffer_ref(frame_data->m_gstBuffer));
        if (ret != GST_FLOW_OK && ret != GST_FLOW_FLUSHING)
        {
            LOG(warning) << mLogPrefix << "RemuxWriter: video appsrc push failed, ret=" << ret << endl;
        }

        if (frame_data->m_eos)
        {
            LOG(info) << mLogPrefix << "RemuxWriterConsumer: EOS received" << endl;
            sendEOS();
        }
    }
}

std::shared_ptr<IMediaDataConsumer> RemuxWriterConsumer::getAudioConsumer()
{
    if (!mAudioConsumer && mAudioAppsrc)
    {
        mAudioConsumer = std::make_shared<AudioAppSrcConsumer>(this);
    }
    return mAudioConsumer;
}

void RemuxWriterConsumer::AudioAppSrcConsumer::onFrame(std::shared_ptr<RawFrameParams> frame_data)
{
    if (!mOwner || !frame_data || !mOwner->mAudioAppsrc) return;
    if (!mOwner->mRunning.load()) return;

    // Propagate caps to audio appsrc on first sample
    if (frame_data->m_caps && !mOwner->mAudioCapsSet)
    {
        g_object_set(G_OBJECT(mOwner->mAudioAppsrc), "caps", frame_data->m_caps, nullptr);
        mOwner->mAudioCapsSet = true;
        gchar* cstr = gst_caps_to_string(frame_data->m_caps);
        LOG(info) << mOwner->mLogPrefix << "RemuxWriter(audio): Set audio appsrc caps: " << (cstr ? cstr : "<null>") << endl;
        if (cstr) g_free(cstr);
    }

    if (frame_data->m_gstBuffer && GST_IS_BUFFER(frame_data->m_gstBuffer))
    {
        GstFlowReturn ret = gst_app_src_push_buffer(GST_APP_SRC(mOwner->mAudioAppsrc), gst_buffer_ref(frame_data->m_gstBuffer));
        if (ret != GST_FLOW_OK && ret != GST_FLOW_FLUSHING)
        {
            LOG(warning) << mOwner->mLogPrefix << "RemuxWriter: audio appsrc push failed, ret=" << ret << endl;
        }
    }
}

void RemuxWriterConsumer::sendEOS()
{
    LOG(info) << mLogPrefix << "RemuxWriterConsumer: Sending EOS to appsrc elements" << endl;
    // Stop accepting new frames once EOS is requested.
    mRunning.store(false);

    if (mVideoAppsrc)
    {
        GstFlowReturn ret = gst_app_src_end_of_stream(GST_APP_SRC(mVideoAppsrc));
        LOG(info) << mLogPrefix << "RemuxWriter: Video appsrc EOS sent, result=" << ret << endl;
    }

    if (mAudioAppsrc)
    {
        GstFlowReturn ret = gst_app_src_end_of_stream(GST_APP_SRC(mAudioAppsrc));
        LOG(info) << mLogPrefix << "RemuxWriter: Audio appsrc EOS sent, result=" << ret << endl;
    }
}

