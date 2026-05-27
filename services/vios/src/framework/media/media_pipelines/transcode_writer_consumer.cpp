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

#include "transcode_writer_consumer.h"
#include "logger.h"
#include "nvhwdetection.h"
#include "overlay_internal.h"
#include "ElasticMetadataStore.h"
#include "utils.h"
#include "database.h"
#include "config.h"

#include <gst/gst.h>
#include <gst/app/gstappsrc.h>
#include <cassert>

/* Macro defined in splitmuxsrc plugin */
#define FIXED_TS_OFFSET (1000*GST_SECOND)

// Probe helpers for debugging and frame dropping
namespace {

static const std::string kEmptyLogPrefix;
static inline const std::string& getLogPrefix(const std::string* prefix)
{
    return prefix ? *prefix : kEmptyLogPrefix;
}

struct WriterProbeCtx {
    const char* tag = nullptr;
    const std::string* log_prefix = nullptr;
};

struct DecodebinAudioCtx {
    GstElement* audioconvert = nullptr;
    const std::string* log_prefix = nullptr;
};

static GstPadProbeReturn writer_buf_probe(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
{
    WriterProbeCtx* ctx = (WriterProbeCtx*)user_data;
    const char *tag = ctx ? ctx->tag : nullptr;
    if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER)
    {
        GstBuffer* b = GST_PAD_PROBE_INFO_BUFFER(info);
        if (b)
        {
            gint64 pts_ms = GST_BUFFER_PTS_IS_VALID(b) ? (GST_BUFFER_PTS(b) / 1000000) : -1;
            gboolean is_key = !GST_BUFFER_FLAG_IS_SET(b, GST_BUFFER_FLAG_DELTA_UNIT);
            GstMapInfo map; gsize sz = 0;
            if (gst_buffer_map(b, &map, GST_MAP_READ)) { sz = map.size; gst_buffer_unmap(b, &map); }
            LOG(info) << getLogPrefix(ctx ? ctx->log_prefix : nullptr)
                      << "Probe[" << (tag ? tag : "?") << "]: pts_ms:" << pts_ms
                        << ", size:" << sz << ", isKey:" << (is_key ? 1 : 0) << endl;
        }
    }
    return GST_PAD_PROBE_OK;
}

struct DecoderProbeCtx {
    int64_t seek_start_ms = 0;
    bool is_overlay_enabled = false;
    int64_t overlay_ts_offset = 0;
    int64_t file_start_time = 0;
    const std::string* log_prefix = nullptr;
};

static GstPadProbeReturn decoder_src_pad_cb(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
{
    DecoderProbeCtx* ctx = (DecoderProbeCtx*)user_data;
    int64_t start_time = 0;
    bool sw_mode = GET_CONFIG().use_software_path || g_isGpuPresent == false;
    if (ctx)
    {
        start_time = ctx->seek_start_ms + FIXED_TS_OFFSET/1000000;
    }

    if (!(GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER))
    {
        return GST_PAD_PROBE_OK;
    }

    GstBuffer* gstBuffer = GST_PAD_PROBE_INFO_BUFFER(info);
    if (gstBuffer == nullptr)
    {
        return GST_PAD_PROBE_OK;
    }

    guint64 gst_pts = GST_BUFFER_PTS(gstBuffer);
    int64_t buf_time = -1;
    if (gst_pts != GST_CLOCK_TIME_NONE)
    {
        buf_time = static_cast<int64_t>(gst_pts / 1000000);
        LOG(verbose) << getLogPrefix(ctx ? ctx->log_prefix : nullptr)
                     << "Decoder(out): buf_time=" << buf_time << ", start_time=" << start_time << endl;
    }
    else
    {
        LOG(verbose) << getLogPrefix(ctx ? ctx->log_prefix : nullptr)
                     << "Decoder(out): buf_time=invalid, start_time=" << start_time << endl;
    }
    if (buf_time >= 0 && ctx && ctx->seek_start_ms > 0 && buf_time < start_time)
    {
        LOG(verbose) << getLogPrefix(ctx ? ctx->log_prefix : nullptr)
                     << "Decoder(src): dropping pre-start buffer, ts=" << buf_time << " < start=" << start_time << endl;
        return GST_PAD_PROBE_DROP;
    }
    if (ctx && ctx->is_overlay_enabled)
    {
        if (sw_mode)
        {
            // CRITICAL: Make buffer writable before adding metadata
            // In pad probes, we can replace the buffer with a writable copy
            if (!gst_buffer_is_writable(gstBuffer))
            {
                gstBuffer = gst_buffer_make_writable(gstBuffer);
                GST_PAD_PROBE_INFO_DATA(info) = gstBuffer;
            }
        }

        GstNvVstMeta *meta = GST_NV_VST_META_ADD (gstBuffer);
        if (meta)
        {
            if (buf_time >= 0)
            {
                int64_t ts_offset = buf_time - FIXED_TS_OFFSET/1000000;
                meta->pts = (ctx->file_start_time + ts_offset) * 1000 * 1000;
            }
            else
            {
                meta->pts = ctx->file_start_time * 1000000;
            }
        }
    }
    return GST_PAD_PROBE_OK;
}

struct AudioDropProbeCtx {
    int64_t seek_start_ms = 0;
    const std::string* log_prefix = nullptr;
};

static GstPadProbeReturn writer_audio_src_drop_cb(GstPad *pad, GstPadProbeInfo *info, gpointer user_data)
{
    AudioDropProbeCtx* ctx = (AudioDropProbeCtx*)user_data;
    int64_t start_time = 0;
    if (ctx)
    {
        start_time = ctx->seek_start_ms + FIXED_TS_OFFSET/1000000;
    }

    if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER)
    {
        GstBuffer* gstBuffer = GST_PAD_PROBE_INFO_BUFFER(info);
        if (gstBuffer != nullptr)
        {
            int64_t buf_time = (GST_BUFFER_PTS (gstBuffer)/1000000);
            if (buf_time < start_time)
            {
                LOG(verbose) << getLogPrefix(ctx ? ctx->log_prefix : nullptr)
                             << "Audio(src): dropping pre-start buffer, ts=" << buf_time << " < start=" << start_time << endl;
                return GST_PAD_PROBE_DROP;
            }
            return GST_PAD_PROBE_OK;
        }
    }
    return GST_PAD_PROBE_OK;
}

} // anonymous namespace

GstElement* TranscodeWriterConsumer::buildOverlayBinIfNeeded()
{
    if (!mCfg.enable_overlay) return nullptr;
    if (mCfg.overlay_bin) return mCfg.overlay_bin;
    // Build local overlay bin with provided params
    NvLLOverlayInternal::OverlayParams params = {};
    params.m_startTime = mCfg.user_start_time_iso;
    params.m_endTime = mCfg.user_end_time_iso;
    params.m_sensorName = mCfg.sensor_name;
    // Defaults; can be refined later if needed
    params.m_frameRate = 30;
    params.m_frameSize.m_width = 1920;
    params.m_frameSize.m_height = 1080;
    if (mCfg.overlay_params)
    {
        params.m_bboxParams = *mCfg.overlay_params;
    }
    MetadataParams metadataParams;
    metadataParams.m_startTime = mCfg.user_start_time_iso;
    metadataParams.m_endTime = mCfg.user_end_time_iso;
    metadataParams.m_sensorName = mCfg.sensor_name;
    metadataParams.m_isLive = false;
    std::shared_ptr<IMetadataStore> metadataStore = std::make_shared<ElasticMetadataStore>(metadataParams, false);
    // Store overlay instance as member to keep it alive for pipeline duration
    mOverlayInst = std::make_unique<NvLLOverlayInternal>(params, metadataStore, false, true);
    GstElement* overlay = mOverlayInst ? mOverlayInst->create() : nullptr;
    if (!overlay)
    {
        LOG(warning) << mLogPrefix << "TranscodeWriter: failed to create overlay bin" << endl;
        mOverlayInst.reset();  // Clean up if creation failed
    }
    return overlay;
}

TranscodeWriterConsumer::TranscodeWriterConsumer(const TranscodeWriterConfig& cfg)
: IMediaDataConsumer("TranscodeWriterConsumer"), mCfg(cfg)
{
    mLogPrefix = mCfg.log_id.empty() ? "" : ("[" + mCfg.log_id + "] ");
}

TranscodeWriterConsumer::~TranscodeWriterConsumer()
{
    try {
        stop();
        teardown();
    } catch (const std::exception& e) {
        try { LOG(error) << "Exception in ~TranscodeWriterConsumer: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
    } catch (...) {
        try { LOG(error) << "Unknown exception in ~TranscodeWriterConsumer" << endl; } catch (...) { (void)std::current_exception(); }
    }
}

bool TranscodeWriterConsumer::buildPipeline()
{
    mPipeline = gst_pipeline_new("writer_pipeline_updated");
    mVideoAppsrc = gst_element_factory_make("appsrc", nullptr);
    if (!mPipeline || !mVideoAppsrc)
    {
        LOG(error) << mLogPrefix << "TranscodeWriter: element creation failed" << endl;
        return false;
    }

    // Set appsrc caps to help with negotiation.
    //
    // Initial caps declare only codec + AU alignment; the exact stream-format
    // (avc/hvc1 vs byte-stream) is overridden by the first sample's caps in
    // onFrame() (see mVideoCapsSet propagation below). Hard-coding
    // "byte-stream" here caused downstream parser/decoder confusion when the
    // producer pushed AVC-formatted (length-prefixed) buffers (e.g.
    // matroska-backed files via giosrc) — which manifested as mp4mux errors
    // ("Buffer has no PTS").
    {
        GstCaps *writer_caps = gst_caps_new_simple(iequals(mCfg.video_codec, "h265") ? "video/x-h265" : "video/x-h264",
                                                   "stream-format", G_TYPE_STRING, "byte-stream",
                                                   "alignment",      G_TYPE_STRING, "au",
                                                   nullptr);
        g_object_set (G_OBJECT (mVideoAppsrc), "caps", writer_caps, "format", GST_FORMAT_TIME,
                      "is-live", FALSE, "block", TRUE, "do-timestamp", FALSE, 
                      "max-bytes", (guint64)2000000,  // 2MB max queue to apply backpressure
                      nullptr);
        gst_caps_unref (writer_caps);
    }

    // Build transcodebin similar to video_download.cpp
    GstElement *videoDecoder=nullptr, *identity=nullptr, *videoEncoder=nullptr, *capssetter=nullptr, *converter=nullptr;
    mTranscodebin = gst_bin_new("transcode_bin_consumer");
    if (!mTranscodebin)
    {
        LOG(error) << mLogPrefix << "TranscodeWriter: failed to create transcodebin" << endl;
        return false;
    }

    capssetter = gst_element_factory_make("capssetter", nullptr);
    identity   = gst_element_factory_make("identity", nullptr);

    if (NvHwDetection::getInstance()->m_useNvV4l2Dec == true)
    {
        videoDecoder = gst_element_factory_make("nvv4l2decoder", nullptr);
        if (videoDecoder)
        {
            // Determine B-frame presence from config or database
            bool hasBframes = mCfg.has_bframes;

            // If not set in config and stream_id is available, read from database
            if (!hasBframes && !mCfg.stream_id.empty())
            {
                auto dbHelper = GET_DB_INSTANCE();
                if (dbHelper)
                {
                    // Get stream info from database using the stream ID
                    SensorStreamsDBColumns stream_row = dbHelper->readSensorStreams(mCfg.stream_id);
                    if (!stream_row.stream_id_value.empty())
                    {
                        hasBframes = (stream_row.isBframesPresent_value == 1);
                        LOG(info) << "TranscodeWriter: B-frame flag from DB for stream " << mCfg.stream_id
                                << ": " << (hasBframes ? "true" : "false")
                                << " (DB value: " << stream_row.isBframesPresent_value << " [0=false, 1=true])" << endl;
                    }
                    else
                    {
                        LOG(warning) << "TranscodeWriter: Stream not found in SENSOR_STREAMS table: " << mCfg.stream_id << endl;
                    }
                }
                else
                {
                    LOG(error) << "TranscodeWriter: Database helper is null, cannot read B-frame flag" << endl;
                }
            }

            // Enable low-latency mode only if B-frames are NOT present
            if (GET_CONFIG().enable_dec_low_latency_mode && !hasBframes)
            {
                g_object_set(G_OBJECT(videoDecoder), "low-latency-mode", TRUE, nullptr);
                LOG(info) << "TranscodeWriter: B-frames NOT present, enabled low-latency decoder mode" << endl;
            }
            else
            {
                LOG(info) << "TranscodeWriter: B-frames present or low-latency disabled, using default decoder settings" << endl;
            }
        }
    }
    else
    {
        videoDecoder = gst_element_factory_make(iequals(mCfg.video_codec, "h265") ? "avdec_h265" : "avdec_h264", nullptr);
    }

    // Save decoder reference for probes
    mVideoDecoder = videoDecoder;

    string output_video_codec = mCfg.video_codec;
    if (mCfg.is_software_encoder) output_video_codec = "h264";

    if (NvHwDetection::getInstance()->m_useNvV4l2Enc == true)
    {
        const char* enc_name = iequals(output_video_codec, "h265") ? "nvv4l2h265enc" : "nvv4l2h264enc";
        videoEncoder = gst_element_factory_make(enc_name, nullptr);
        if (videoEncoder)
        {
            GParamSpec* ps = g_object_class_find_property(G_OBJECT_GET_CLASS(videoEncoder), "num-B-Frames");
            if (ps) g_object_set (G_OBJECT(videoEncoder), "num-B-Frames", 0, nullptr);
            ps = g_object_class_find_property(G_OBJECT_GET_CLASS(videoEncoder), "idrinterval");
            if (ps) g_object_set (G_OBJECT(videoEncoder), "idrinterval", 30, nullptr);
            if (iequals(output_video_codec, "h265"))
            {
                ps = g_object_class_find_property(G_OBJECT_GET_CLASS(videoEncoder), "qp-range");
                if (ps) g_object_set (G_OBJECT(videoEncoder), "qp-range", "25,30:25,30:25,30", nullptr);
            }
            g_object_set (G_OBJECT(videoEncoder), "bitrate", 5000000, nullptr);
            LOG(info) << mLogPrefix << "TranscodeWriter: HW encoder created successfully" << endl;
        }
        else
        {
            LOG(error) << mLogPrefix << "TranscodeWriter: Failed to create HW encoder: " << enc_name << endl;
        }
    }
    else
    {
        const char* enc_name = iequals(output_video_codec, "h265") ? "x265enc" : "x264enc";
        videoEncoder = gst_element_factory_make(enc_name, nullptr);
        if (videoEncoder)
        {
            if (iequals(output_video_codec, "h265"))
            {
                g_object_set (G_OBJECT(videoEncoder), "speed-preset", 1, nullptr);
                g_object_set (G_OBJECT(videoEncoder), "tune", 0x00000004, nullptr);
                GParamSpec* ps = g_object_class_find_property(G_OBJECT_GET_CLASS(videoEncoder), "bframes");
                if (ps) g_object_set (G_OBJECT(videoEncoder), "bframes", 0, nullptr);
                g_object_set (G_OBJECT(videoEncoder), "bitrate", 5000, nullptr);
            }
            else
            {
                g_object_set (G_OBJECT(videoEncoder), "bframes", 0, nullptr);
                GParamSpec* ps = g_object_class_find_property(G_OBJECT_GET_CLASS(videoEncoder), "rc-lookahead");
                if (ps) g_object_set (G_OBJECT(videoEncoder), "rc-lookahead", 0, nullptr);
                ps = g_object_class_find_property(G_OBJECT_GET_CLASS(videoEncoder), "sync-lookahead");
                if (ps) g_object_set (G_OBJECT(videoEncoder), "sync-lookahead", 0, nullptr);
                g_object_set (G_OBJECT(videoEncoder), "speed-preset", 1, nullptr);
                g_object_set (G_OBJECT(videoEncoder), "bitrate", 5000, nullptr);
            }
            LOG(info) << mLogPrefix << "TranscodeWriter: SW encoder created successfully" << endl;
        }
        else
        {
            LOG(error) << mLogPrefix << "TranscodeWriter: Failed to create SW encoder: " << enc_name << endl;
        }
    }

    // Save encoder reference for probes
    mVideoEncoder = videoEncoder;

    if (!videoDecoder || !videoEncoder || !capssetter || !identity)
    {
        LOG(error) << mLogPrefix << "TranscodeWriter: core elements creation failed" << endl;
        return false;
    }

    bool needConverter = (NvHwDetection::getInstance()->m_useNvV4l2Dec ^ NvHwDetection::getInstance()->m_useNvV4l2Enc) ||
                         (!NvHwDetection::getInstance()->m_useNvV4l2Dec && !NvHwDetection::getInstance()->m_useNvV4l2Enc && mCfg.enable_overlay) ||
                         (mCfg.enable_overlay && iequals(mCfg.video_codec, "h265"));  // Always use converter for H.265 with overlay (P010_10LE → NV12)
#ifdef JETSON_PLATFORM
    if (needConverter) converter = gst_element_factory_make ("nvvidconv" , nullptr);
#else
    if (needConverter) converter = gst_element_factory_make ("nvvideoconvert" , nullptr);
    if (needConverter && !converter) converter = gst_element_factory_make("nvvidconv", nullptr);
#endif
    if (needConverter && !converter) converter = gst_element_factory_make("videoconvert", nullptr);

    if (needConverter && mCfg.enable_overlay && iequals(mCfg.video_codec, "h265"))
    {
        LOG(info) << mLogPrefix << "TranscodeWriter: Added videoconvert for H.265 overlay compatibility" << endl;
    }

    gst_bin_add_many (GST_BIN (mTranscodebin), videoDecoder, identity, videoEncoder, capssetter, nullptr);
    if (converter) gst_bin_add (GST_BIN (mTranscodebin), converter);
    GstElement* overlayLocal = buildOverlayBinIfNeeded();
    if (mCfg.enable_overlay && (mCfg.overlay_bin || overlayLocal))
    {
        gst_bin_add (GST_BIN (mTranscodebin), mCfg.overlay_bin ? mCfg.overlay_bin : overlayLocal);
    }

    if (converter)
    {
        if (!gst_element_link_many(videoDecoder, converter, identity, nullptr))
        {
            LOG(error) << mLogPrefix << "TranscodeWriter: failed to link videoDecoder->converter->identity" << endl;
            return false;
        }
    }
    else
    {
        if (!gst_element_link_many(videoDecoder, identity, nullptr))
        {
            LOG(error) << mLogPrefix << "TranscodeWriter: failed to link videoDecoder->identity" << endl;
            return false;
        }
    }

    if (mCfg.enable_overlay && (mCfg.overlay_bin || overlayLocal))
    {
        if (!gst_element_link_many(identity, (mCfg.overlay_bin ? mCfg.overlay_bin : overlayLocal), videoEncoder, capssetter, nullptr))
        {
            LOG(error) << mLogPrefix << "TranscodeWriter: failed to link identity->overlay->videoEncoder->capssetter" << endl;
            return false;
        }
    }
    else
    {
        if (!gst_element_link_many(identity, videoEncoder, capssetter, nullptr))
        {
            LOG(error) << mLogPrefix << "TranscodeWriter: failed to link identity->videoEncoder->capssetter" << endl;
            return false;
        }
    }

    // Ghost pads
    {
        GstPad *src_pad = gst_element_get_static_pad (capssetter, "src");
        GstPad *sink_pad = gst_element_get_static_pad (videoDecoder, "sink");
        if (!src_pad || !sink_pad)
        {
            if (src_pad)
                gst_object_unref(src_pad);
            if (sink_pad)
                gst_object_unref(sink_pad);
            LOG(error) << mLogPrefix << "TranscodeWriter: failed to get src or sink pad from capssetter or videoDecoder" << endl;
            return false;
        }
        GstPad *ghost_src = gst_ghost_pad_new ("src", src_pad);
        gst_pad_set_active (ghost_src, TRUE);
        gst_element_add_pad (mTranscodebin, ghost_src);
        gst_object_unref (src_pad);
        GstPad *ghost_sink = gst_ghost_pad_new ("sink", sink_pad);
        gst_pad_set_active (ghost_sink, TRUE);
        gst_element_add_pad (mTranscodebin, ghost_sink);
        gst_object_unref (sink_pad);
    }

    // Container-specific elements
    if (iequals(mCfg.container, "mp4"))
    {
        mParserAfterEncode = gst_element_factory_make (iequals(output_video_codec, "h265") ? "h265parse" : "h264parse", nullptr);
        mPeCapsfilter = gst_element_factory_make ("capsfilter", nullptr);
        if (!mParserAfterEncode || !mPeCapsfilter)
        {
            LOG(error) << mLogPrefix << "TranscodeWriter: failed to create parserAfterEncode or peCapsfilter" << endl;
            return false;
        }
        // Ensure parser pushes correct format for mp4
        // config-interval=-1 forces SPS/PPS insertion before every IDR (critical after seek)
        g_object_set (G_OBJECT (mParserAfterEncode), "config-interval", -1, "disable-passthrough", TRUE, nullptr);
        // avc/hvc1 caps for MP4
        if (iequals(output_video_codec, "h265"))
        {
            GstCaps *c = gst_caps_new_simple ("video/x-h265", "stream-format", G_TYPE_STRING, "hvc1", "alignment", G_TYPE_STRING, "au", nullptr);
            g_object_set (G_OBJECT (mPeCapsfilter), "caps", c, nullptr); gst_caps_unref(c);
        }
        else
        {
            GstCaps *c = gst_caps_new_simple ("video/x-h264", "stream-format", G_TYPE_STRING, "avc", "alignment", G_TYPE_STRING, "au", nullptr);
            g_object_set (G_OBJECT (mPeCapsfilter), "caps", c, nullptr); gst_caps_unref(c);
        }
    }
    else if (iequals(mCfg.container, "mkv"))
    {
        // Matroska can work with Annex-B/byte-stream; keep parser for header shaping but no capsfilter
        mParserAfterEncode = gst_element_factory_make (iequals(output_video_codec, "h265") ? "h265parse" : "h264parse", nullptr);
        if (!mParserAfterEncode)
        {
            LOG(error) << mLogPrefix << "TranscodeWriter: failed to create parserAfterEncode" << endl;
            return false;
        }
        // config-interval=-1 forces SPS/PPS insertion before every IDR (critical after seek)
        g_object_set (G_OBJECT (mParserAfterEncode), "config-interval", -1, "disable-passthrough", TRUE, nullptr);
    }

    mMux = gst_element_factory_make (iequals(mCfg.container, "mp4") ? "mp4mux" : (iequals(mCfg.container, "mkv") ? "matroskamux" : "mpegtsmux"), nullptr);
    mFilesink = gst_element_factory_make ("filesink", nullptr);
    if (!mMux || !mFilesink)
    {
        LOG(error) << mLogPrefix << "TranscodeWriter: failed to create mux or filesink" << endl;
        return false;
    }
    if (iequals(mCfg.container, "mp4")) g_object_set(mMux, "faststart", TRUE, "fragment-duration", 2000, "fragment-mode", 1, "streamable", TRUE, nullptr);
    if (!mCfg.output_file.empty()) g_object_set (G_OBJECT (mFilesink), "location", mCfg.output_file.c_str(), nullptr);

    // Audio elements (optional) - transcode pipeline: appsrc -> queue -> decodebin -> audioconvert -> audioresample -> avenc_aac -> mux
    if (mCfg.enable_audio)
    {
        mAudioAppsrc = gst_element_factory_make ("appsrc", nullptr);
        mAudioQueue  = gst_element_factory_make ("queue", nullptr);
        mAudioDecodebin = gst_element_factory_make ("decodebin", nullptr);
        mAudioConvert = gst_element_factory_make ("audioconvert", nullptr);
        mAudioResample = gst_element_factory_make ("audioresample", nullptr);
        mAudioEncoder = gst_element_factory_make ("avenc_aac", nullptr);

        if (!mAudioAppsrc || !mAudioQueue || !mAudioDecodebin || !mAudioConvert || !mAudioResample || !mAudioEncoder)
        {
            LOG(error) << mLogPrefix << "TranscodeWriter: failed to create audio elements" << endl;
            // Try fallback encoders if avenc_aac is not available
            if (!mAudioEncoder)
            {
                mAudioEncoder = gst_element_factory_make ("voaacenc", nullptr);
                if (!mAudioEncoder)
                {
                    mAudioEncoder = gst_element_factory_make ("fdkaacenc", nullptr);
                }
            }
            if (!mAudioAppsrc || !mAudioQueue || !mAudioDecodebin || !mAudioConvert || !mAudioResample || !mAudioEncoder)
            {
                return false;
            }
        }

        g_object_set (G_OBJECT (mAudioAppsrc), "format", GST_FORMAT_TIME,
                      "is-live", FALSE, "block", FALSE, "do-timestamp", FALSE,
                      "stream-type", 0, nullptr);  // GST_APP_STREAM_TYPE_STREAM
        g_object_set (G_OBJECT (mAudioQueue), "max-size-buffers", 200,
                      "max-size-time", (guint64)(2 * GST_SECOND), nullptr);

        LOG(info) << mLogPrefix << "TranscodeWriter: Created audio transcode elements (appsrc->queue->decodebin->audioconvert->audioresample->avenc_aac)" << endl;
    }
    else
    {
        mAudioAppsrc = nullptr;
        mAudioQueue  = nullptr;
        mAudioDecodebin = nullptr;
        mAudioConvert = nullptr;
        mAudioResample = nullptr;
        mAudioEncoder = nullptr;
    }

    return true;
}

bool TranscodeWriterConsumer::linkPipeline()
{
    // Add base video elements
    if (mParserAfterEncode && mPeCapsfilter)
    {
        gst_bin_add_many (GST_BIN (mPipeline), mVideoAppsrc, mTranscodebin, mParserAfterEncode, mPeCapsfilter, mMux, mFilesink, nullptr);
        if (!gst_element_link_many(mVideoAppsrc, mTranscodebin, mParserAfterEncode, mPeCapsfilter, mMux, mFilesink, nullptr))
        {
            LOG(error) << mLogPrefix << "TranscodeWriter: failed to link videoAppsrc->transcodebin->parserAfterEncode->peCapsfilter->mux->filesink" << endl;
            return false;
        }
    }
    else if (mParserAfterEncode)
    {
        gst_bin_add_many (GST_BIN (mPipeline), mVideoAppsrc, mTranscodebin, mParserAfterEncode, mMux, mFilesink, nullptr);
        if (!gst_element_link_many(mVideoAppsrc, mTranscodebin, mParserAfterEncode, mMux, mFilesink, nullptr))
        {
            LOG(error) << mLogPrefix << "TranscodeWriter: failed to link videoAppsrc->transcodebin->parserAfterEncode->mux->filesink" << endl;
            return false;
        }
    }
    else
    {
        gst_bin_add_many (GST_BIN (mPipeline), mVideoAppsrc, mTranscodebin, mMux, mFilesink, nullptr);
        if (!gst_element_link_many(mVideoAppsrc, mTranscodebin, mMux, mFilesink, nullptr))
        {
            LOG(error) << mLogPrefix << "TranscodeWriter: failed to link videoAppsrc->transcodebin->mux->filesink" << endl;
            return false;
        }
    }

    // Audio transcode branch: appsrc -> queue -> decodebin -> audioconvert -> audioresample -> avenc_aac -> mux
    if (mAudioAppsrc && mAudioQueue && mAudioDecodebin && mAudioConvert && mAudioResample && mAudioEncoder)
    {
        gst_bin_add_many(GST_BIN(mPipeline), mAudioAppsrc, mAudioQueue, mAudioDecodebin,
                         mAudioConvert, mAudioResample, mAudioEncoder, nullptr);

        // Link static elements: appsrc -> queue -> decodebin
        if (!gst_element_link_many(mAudioAppsrc, mAudioQueue, mAudioDecodebin, nullptr))
        {
            LOG(error) << mLogPrefix << "TranscodeWriter: failed to link appsrc->queue->decodebin" << endl;
            return false;
        }

        // Link post-decode elements: audioconvert -> audioresample -> encoder -> mux
        if (!gst_element_link_many(mAudioConvert, mAudioResample, mAudioEncoder, mMux, nullptr))
        {
            LOG(error) << mLogPrefix << "TranscodeWriter: failed to link audioconvert->audioresample->encoder->mux" << endl;
            return false;
        }

        // Connect pad-added signal for dynamic linking from decodebin to audioconvert
        DecodebinAudioCtx* pad_ctx = (DecodebinAudioCtx*)g_new0(DecodebinAudioCtx, 1);
        pad_ctx->audioconvert = mAudioConvert;
        pad_ctx->log_prefix = &mLogPrefix;
        g_signal_connect_data(mAudioDecodebin, "pad-added", G_CALLBACK(+[](GstElement* decodebin, GstPad* src_pad, gpointer user_data) {
            DecodebinAudioCtx* ctx = static_cast<DecodebinAudioCtx*>(user_data);
            GstElement* audioconvert = ctx ? ctx->audioconvert : nullptr;
            if (!audioconvert) return;

            // Only link audio pads
            GstCaps* caps = gst_pad_get_current_caps(src_pad);
            if (!caps) caps = gst_pad_query_caps(src_pad, nullptr);
            if (caps)
            {
                GstStructure* s = gst_caps_get_structure(caps, 0);
                const gchar* name = gst_structure_get_name(s);
                if (name && g_str_has_prefix(name, "audio/"))
                {
                    GstPad* sink_pad = gst_element_get_static_pad(audioconvert, "sink");
                    if (sink_pad)
                    {
                        if (!gst_pad_is_linked(sink_pad))
                        {
                            GstPadLinkReturn ret = gst_pad_link(src_pad, sink_pad);
                            if (ret == GST_PAD_LINK_OK)
                            {
                                LOG(info) << getLogPrefix(ctx ? ctx->log_prefix : nullptr)
                                          << "TranscodeWriter: decodebin audio pad linked to audioconvert successfully" << endl;
                            }
                            else
                            {
                                LOG(error) << getLogPrefix(ctx ? ctx->log_prefix : nullptr)
                                           << "TranscodeWriter: failed to link decodebin to audioconvert, ret=" << ret << endl;
                            }
                        }
                        gst_object_unref(sink_pad);
                    }
                }
                gst_caps_unref(caps);
            }
        }), pad_ctx, (GClosureNotify)g_free, (GConnectFlags)0);

        LOG(info) << mLogPrefix << "TranscodeWriter: Linked audio transcode pipeline with dynamic pad handling" << endl;
    }
    return true;
}

void TranscodeWriterConsumer::attachProbes()
{
    detachProbes();

    bool enableDecoderInputProbe = false;  // Set to true to debug decoder input
    bool enableEncoderOutputProbe = false; // Set to true to debug encoder output

    // Attach decoder input probe (for debugging)
    if (enableDecoderInputProbe && mVideoDecoder)
    {
        GstPad *p = gst_element_get_static_pad(mVideoDecoder, "sink");
        if (p)
        {
            WriterProbeCtx* pctx = (WriterProbeCtx*)g_new0(WriterProbeCtx, 1);
            pctx->tag = "dec-in";
            pctx->log_prefix = &mLogPrefix;
            mDecoderSinkProbeId = gst_pad_add_probe(p, GST_PAD_PROBE_TYPE_BUFFER, writer_buf_probe, pctx, (GDestroyNotify)g_free);
            if (mDecoderSinkProbeId != 0)
            {
                mDecoderSinkPad = p;
                LOG(info) << mLogPrefix << "Attached decoder input probe" << endl;
            }
            else
            {
                g_free(pctx);
                gst_object_unref(p);
            }
        }
    }

    // Attach decoder output probe to drop pre-start frames
    if (mVideoDecoder)
    {
        GstPad *p = gst_element_get_static_pad(mVideoDecoder, "src");
        if (p)
        {
            DecoderProbeCtx* dctx = (DecoderProbeCtx*) g_new0(DecoderProbeCtx, 1);
            dctx->seek_start_ms = mCfg.seek_start_ms;
            dctx->file_start_time = mCfg.file_start_time;
            dctx->is_overlay_enabled = mCfg.enable_overlay;
            dctx->log_prefix = &mLogPrefix;
            mDecoderSrcProbeId = gst_pad_add_probe(p, GST_PAD_PROBE_TYPE_BUFFER, decoder_src_pad_cb, dctx, (GDestroyNotify)g_free);
            if (mDecoderSrcProbeId != 0)
            {
                mDecoderSrcPad = p;
            }
            else
            {
                g_free(dctx);
                gst_object_unref(p);
            }
        }
    }

    // Attach encoder output probe (for debugging)
    if (enableEncoderOutputProbe && mVideoEncoder)
    {
        GstPad *p = gst_element_get_static_pad(mVideoEncoder, "src");
        if (p)
        {
            WriterProbeCtx* pctx = (WriterProbeCtx*)g_new0(WriterProbeCtx, 1);
            pctx->tag = "enc-out";
            pctx->log_prefix = &mLogPrefix;
            mEncoderSrcProbeId = gst_pad_add_probe(p, GST_PAD_PROBE_TYPE_BUFFER, writer_buf_probe, pctx, (GDestroyNotify)g_free);
            if (mEncoderSrcProbeId != 0)
            {
                mEncoderSrcPad = p;
                LOG(info) << mLogPrefix << "Attached encoder output probe" << endl;
            }
            else
            {
                g_free(pctx);
                gst_object_unref(p);
            }
        }
    }

    // Attach audio drop probe if audio is enabled (on encoder output)
    if (mCfg.seek_start_ms > 0 && mAudioConvert)
    {
        GstPad* audio_src_pad = gst_element_get_static_pad(mAudioConvert, "src");
        if (audio_src_pad)
        {
            AudioDropProbeCtx* actx = (AudioDropProbeCtx*) g_new0(AudioDropProbeCtx, 1);
            actx->seek_start_ms = mCfg.seek_start_ms;
            actx->log_prefix = &mLogPrefix;
            mAudioSrcProbeId = gst_pad_add_probe(audio_src_pad, GST_PAD_PROBE_TYPE_BUFFER, writer_audio_src_drop_cb, actx, (GDestroyNotify)g_free);
            if (mAudioSrcProbeId != 0)
            {
                mAudioSrcPad = audio_src_pad;
            }
            else
            {
                g_free(actx);
                gst_object_unref(audio_src_pad);
            }
        }
    }

    // Attach EOS enforcement on mux sink to stop at end_time_ms
    if (mMux)
    {
        struct MuxProbeCtx {
            int64_t end_ms;
            int64_t seek_start_ms;
            int64_t frameThresholdMs;
            int64_t prevBufMs;
            int64_t firstBufMs;
            bool firstBufSeen;
            bool is_sw_encoder;
            GstElement* mux;
            GstAppSrc* vsrc;
            GstAppSrc* asrc;
            const std::string* log_prefix;
        } *mctx = (MuxProbeCtx*)g_malloc0(sizeof(MuxProbeCtx));

        mctx->end_ms = mCfg.end_time_ms;
        mctx->seek_start_ms = mCfg.seek_start_ms;
        mctx->frameThresholdMs = 0;
        mctx->prevBufMs = 0;
        mctx->firstBufMs = 0;
        mctx->firstBufSeen = false;
        mctx->is_sw_encoder = mCfg.is_software_encoder;
        mctx->mux = mMux;
        mctx->vsrc = mVideoAppsrc ? GST_APP_SRC(mVideoAppsrc) : nullptr;
        mctx->asrc = mAudioAppsrc ? GST_APP_SRC(mAudioAppsrc) : nullptr;
        mctx->log_prefix = &mLogPrefix;
        GstIterator* it = gst_element_iterate_sink_pads(mMux);
        if (it)
        {
            GValue item = G_VALUE_INIT; gboolean done = FALSE;
            while (!done)
            {
                switch (gst_iterator_next(it, &item))
                {
                    case GST_ITERATOR_OK:
                    {
                        GstPad* sink_pad = (GstPad*) g_value_get_object(&item);
                        if (sink_pad)
                        {
                            gulong probe_id = gst_pad_add_probe(sink_pad, GST_PAD_PROBE_TYPE_BUFFER, +[](GstPad *pad, GstPadProbeInfo *info, gpointer user_data){
                                MuxProbeCtx* c = static_cast<MuxProbeCtx*>(user_data);
                                if (!c) return GST_PAD_PROBE_OK;
                                if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER)
                                {
                                    GstBuffer* b = GST_PAD_PROBE_INFO_BUFFER(info);
                                    if (b)
                                    {
                                        int64_t buf_time_raw = GST_BUFFER_PTS_IS_VALID(b) ? (GST_BUFFER_PTS(b)/1000000) : -1;
                                        if (buf_time_raw < 0) return GST_PAD_PROBE_OK;
                                        int64_t buf_time = buf_time_raw;

                                        // Software encoder resets timestamps, so normalize them
                                        // Hardware encoder preserves timestamps, so use them as-is
                                        if (c->is_sw_encoder)
                                        {
                                            // Capture first buffer timestamp to calculate offset
                                            if (!c->firstBufSeen)
                                            {
                                                c->firstBufMs = buf_time_raw;
                                                c->firstBufSeen = true;
                                            }
                                            // Normalize timestamp relative to expected start time
                                            int64_t expected_start = c->seek_start_ms + FIXED_TS_OFFSET/1000000;
                                            int64_t timestamp_offset = c->firstBufMs - expected_start;
                                            buf_time = buf_time_raw - timestamp_offset;
                                        }

                                        if (c->frameThresholdMs == 0 && c->prevBufMs != 0 && buf_time >= 0)
                                        {
                                            c->frameThresholdMs = buf_time - c->prevBufMs;
                                        }
                                        c->prevBufMs = buf_time;
                                    }
                                }
                                return GST_PAD_PROBE_OK;
                            }, mctx, (GDestroyNotify)g_free);
                            if (probe_id != 0)
                            {
                                mMuxSinkProbeId = probe_id;
                                mMuxSinkPad = GST_PAD(gst_object_ref(sink_pad));
                            }
                            else
                            {
                                g_free(mctx);
                            }
                            g_value_reset(&item);
                            done = TRUE; // first video pad is enough
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

void TranscodeWriterConsumer::detachProbes()
{
    if (mDecoderSinkPad && mDecoderSinkProbeId)
    {
        gst_pad_remove_probe(mDecoderSinkPad, mDecoderSinkProbeId);
        mDecoderSinkProbeId = 0;
    }
    if (mDecoderSinkPad)
    {
        gst_object_unref(mDecoderSinkPad);
        mDecoderSinkPad = nullptr;
    }

    if (mDecoderSrcPad && mDecoderSrcProbeId)
    {
        gst_pad_remove_probe(mDecoderSrcPad, mDecoderSrcProbeId);
        mDecoderSrcProbeId = 0;
    }
    if (mDecoderSrcPad)
    {
        gst_object_unref(mDecoderSrcPad);
        mDecoderSrcPad = nullptr;
    }

    if (mEncoderSrcPad && mEncoderSrcProbeId)
    {
        gst_pad_remove_probe(mEncoderSrcPad, mEncoderSrcProbeId);
        mEncoderSrcProbeId = 0;
    }
    if (mEncoderSrcPad)
    {
        gst_object_unref(mEncoderSrcPad);
        mEncoderSrcPad = nullptr;
    }

    if (mAudioSrcPad && mAudioSrcProbeId)
    {
        gst_pad_remove_probe(mAudioSrcPad, mAudioSrcProbeId);
        mAudioSrcProbeId = 0;
    }
    if (mAudioSrcPad)
    {
        gst_object_unref(mAudioSrcPad);
        mAudioSrcPad = nullptr;
    }

    if (mMuxSinkPad && mMuxSinkProbeId)
    {
        gst_pad_remove_probe(mMuxSinkPad, mMuxSinkProbeId);
        mMuxSinkProbeId = 0;
    }
    if (mMuxSinkPad)
    {
        gst_object_unref(mMuxSinkPad);
        mMuxSinkPad = nullptr;
    }
}

bool TranscodeWriterConsumer::start()
{
    if (mRunning.load()) return true;
    if (!gst_is_initialized()) { gst_init(nullptr, nullptr); }
    if (!buildPipeline())
    {
        LOG(error) << mLogPrefix << "TranscodeWriter: failed to build pipeline" << endl;
        return false;
    }
    if (!linkPipeline())
    {
        LOG(error) << mLogPrefix << "TranscodeWriter: failed to link pipeline" << endl;
        teardown();
        return false;
    }
    attachProbes();
    if (gst_element_set_state (mPipeline, GST_STATE_PLAYING) == GST_STATE_CHANGE_FAILURE)
    {
        LOG(error) << mLogPrefix << "TranscodeWriter: failed to set pipeline to playing state" << endl;
        teardown();
        return false;
    }
    mRunning.store(true);
    return true;
}

void TranscodeWriterConsumer::stop()
{
    mRunning.store(false);

    if (!mPipeline) return;
    detachProbes();

    // Set pipeline to NULL state with timeout protection
    LOG(info) << mLogPrefix << "TranscodeWriterConsumer::stop() - Setting pipeline to NULL state" << endl;
    GstStateChangeReturn ret = gst_element_set_state(mPipeline, GST_STATE_NULL);
    if (ret == GST_STATE_CHANGE_ASYNC)
    {
        // Wait up to 5 seconds for state change to complete
        GstState current, pending;
        ret = gst_element_get_state(mPipeline, &current, &pending, 5 * GST_SECOND);
        if (ret == GST_STATE_CHANGE_FAILURE || ret == GST_STATE_CHANGE_ASYNC)
        {
            LOG(error) << mLogPrefix << "TranscodeWriterConsumer::stop() - Pipeline failed to reach NULL state within timeout" << endl;
        }
    }
    LOG(info) << mLogPrefix << "TranscodeWriterConsumer::stop() - Pipeline stopped" << endl;
}

void TranscodeWriterConsumer::teardown()
{
    detachProbes();
    // Explicitly clean up overlay instance before pipeline destruction
    if (mOverlayInst)
    {
        mOverlayInst.reset();
    }

    // Safety net: clear appsrc caps if stop() didn't run or pipeline was
    // partially constructed. Uses the direct API to free priv->last_caps.
    if (mVideoAppsrc)
    {
        gst_app_src_set_caps(GST_APP_SRC(mVideoAppsrc), nullptr);
    }
    if (mAudioAppsrc)
    {
        gst_app_src_set_caps(GST_APP_SRC(mAudioAppsrc), nullptr);
    }

    if (mPipeline) { gst_object_unref (mPipeline); mPipeline = nullptr; }
    mVideoAppsrc = nullptr; mTranscodebin = nullptr;
    mVideoDecoder = nullptr; mVideoEncoder = nullptr;  // references inside transcodebin, no unref needed
    mParserAfterEncode = nullptr; mPeCapsfilter = nullptr; mMux = nullptr; mFilesink = nullptr;
    mAudioAppsrc = nullptr; mAudioQueue = nullptr; mAudioDecodebin = nullptr;
    mAudioConvert = nullptr; mAudioResample = nullptr; mAudioEncoder = nullptr;
}
bool TranscodeWriterConsumer::waitForCompletion(int64_t timeout_secs)
{
    if (!mPipeline) return false;
    GstBus* bus = gst_element_get_bus(mPipeline);
    if (!bus) return false;
    GstMessage* msg = gst_bus_timed_pop_filtered (bus, timeout_secs * GST_SECOND,
        (GstMessageType)(GST_MESSAGE_ERROR | GST_MESSAGE_EOS));
    bool got = false;
    if (msg)
    {
        switch (GST_MESSAGE_TYPE (msg))
        {
            case GST_MESSAGE_ERROR:
            {
                GError* err = nullptr;
                gchar* dbg = nullptr;
                gst_message_parse_error(msg, &err, &dbg);
                gchar* src_name = gst_object_get_path_string(msg->src);
                LOG(error) << mLogPrefix << "TranscodeWriter ERROR from " << (src_name ? src_name : "<unknown>") 
                          << ": " << (err ? err->message : "<null>") << endl;
                if (dbg)
                {
                    LOG(error) << mLogPrefix << "Debug info: " << dbg << endl;
                    g_free(dbg);
                }

                /* Check for fatal resource/library errors before attempting recovery */
                if (err != nullptr && (err->domain == GST_RESOURCE_ERROR || err->domain == GST_LIBRARY_ERROR))
                {
                    LOG(error) << mLogPrefix << "######## Fatal resource/library error, Terminating the service... ###########" << endl;
                    if (NvHwDetection::getInstance()->m_useNvV4l2Dec || NvHwDetection::getInstance()->m_useNvV4l2Enc)
                    {
                        detectGPU();
                        if (!g_isGpuPresent)
                        {
                            LOG(error) << mLogPrefix << "---#--- /dev/nvidia node not present, Non-recoverable error ---#---" << endl;
                            std::exit(EXIT_GPU_NOT_FOUND);
                        }
                    }
                }

                if (err) g_error_free(err);
                if (src_name) g_free(src_name);
                mError.store(true);
                got = true;
                break;
            }
            case GST_MESSAGE_EOS:
                LOG(info) << mLogPrefix << "TranscodeWriterConsumer::waitForCompletion() EOS message received" << endl;
                got = true;
                break;
            default:
                break;
        }
        gst_message_unref(msg);
    }
    gst_object_unref(bus);
    return got;
}

void TranscodeWriterConsumer::onFrame(std::shared_ptr<RawFrameParams> frame_data)
{
    if (!mRunning.load() || !frame_data || !mVideoAppsrc) return;
    if (frame_data->m_gstBuffer && GST_IS_BUFFER(frame_data->m_gstBuffer))
    {
        // Propagate caps from producer to the video appsrc on the first
        // sample. Without this, the appsrc's generic codec-only caps leave
        // the downstream h264parse/decoder unable to determine the actual
        // stream-format (avc/hvc1 vs byte-stream) with certainty — causing
        // buffer reframing and PTS loss ("Buffer has no PTS" at mp4mux).
        if (frame_data->m_caps && !mVideoCapsSet)
        {
            g_object_set(G_OBJECT(mVideoAppsrc), "caps", frame_data->m_caps, nullptr);
            mVideoCapsSet = true;
            gchar* cstr = gst_caps_to_string(frame_data->m_caps);
            LOG(info) << mLogPrefix << "TranscodeWriter(video): Set video appsrc caps: "
                      << (cstr ? cstr : "<null>") << endl;
            if (cstr) g_free(cstr);
        }

        GstFlowReturn ret = gst_app_src_push_buffer(GST_APP_SRC(mVideoAppsrc), gst_buffer_ref(frame_data->m_gstBuffer));
        if (ret != GST_FLOW_OK && ret != GST_FLOW_FLUSHING)
        {
            LOG(warning) << mLogPrefix << "TranscodeWriter: video appsrc push failed, ret=" << ret << endl;
        }
        if (frame_data->m_eos)
        {
            LOG(info) << mLogPrefix << "TranscodeWriterConsumer: EOS received" << endl;
            sendEOS();
        }
    }
}

std::shared_ptr<IMediaDataConsumer> TranscodeWriterConsumer::getAudioConsumer()
{
    if (!mAudioConsumer && mAudioAppsrc)
    {
        mAudioConsumer = std::make_shared<AudioAppSrcConsumer>(this);
    }
    return mAudioConsumer;
}

void TranscodeWriterConsumer::AudioAppSrcConsumer::onFrame(std::shared_ptr<RawFrameParams> frame_data)
{
    if (!mOwner || !frame_data || !mOwner->mAudioAppsrc) return;
    if (!mOwner->mRunning.load()) return;  // Don't push if stopped

    // Propagate caps to audio appsrc on first sample (helps parsebin typefinding)
    if (frame_data->m_caps && !mOwner->mAudioCapsSet)
    {
        g_object_set(G_OBJECT(mOwner->mAudioAppsrc), "caps", frame_data->m_caps, nullptr);
        mOwner->mAudioCapsSet = true;
        gchar* cstr = gst_caps_to_string(frame_data->m_caps);
        LOG(info) << mOwner->mLogPrefix << "Writer(audio): Set audio appsrc caps: " << (cstr ? cstr : "<null>") << endl;
        if (cstr) g_free(cstr);
    }

    if (frame_data->m_gstBuffer && GST_IS_BUFFER(frame_data->m_gstBuffer))
    {
        GstFlowReturn ret = gst_app_src_push_buffer(GST_APP_SRC(mOwner->mAudioAppsrc), gst_buffer_ref(frame_data->m_gstBuffer));
        if (ret != GST_FLOW_OK && ret != GST_FLOW_FLUSHING)
        {
            LOG(warning) << mOwner->mLogPrefix << "TranscodeWriter: audio appsrc push failed, ret=" << ret << endl;
        }
    }
}

void TranscodeWriterConsumer::sendEOS()
{
    LOG(info) << mLogPrefix << "TranscodeWriterConsumer: Sending EOS to appsrc elements" << endl;
    // Stop accepting new frames once EOS is requested.
    mRunning.store(false);
    
    if (mVideoAppsrc)
    {
        GstFlowReturn ret = gst_app_src_end_of_stream(GST_APP_SRC(mVideoAppsrc));
        LOG(info) << mLogPrefix << "TranscodeWriter: Video appsrc EOS sent, result=" << ret << endl;
    }
    
    if (mAudioAppsrc)
    {
        GstFlowReturn ret = gst_app_src_end_of_stream(GST_APP_SRC(mAudioAppsrc));
        LOG(info) << mLogPrefix << "TranscodeWriter: Audio appsrc EOS sent, result=" << ret << endl;
    }
}


