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

#include "clip_reader_producer.h"
#include "event_loop.h"
#include "logger.h"
#include "utils.h"
#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstring>
#include <thread>
#include <sys/stat.h>
#include <glib.h>
#include <gst/gst.h>
#include <gst/app/gstappsink.h>
#include <gio/gio.h>

// When 1: giosrc src + demux pad probes, verbose buffer logs, waiting-data correlation (see header).
// Default 0: no pad probes; production still uses waiting-data / done-waiting-data signals and watchdog only.
#ifndef CLIP_READER_GIOSRC_EXTRA_DEBUG
#define CLIP_READER_GIOSRC_EXTRA_DEBUG 0
#endif
#if CLIP_READER_GIOSRC_EXTRA_DEBUG
#include <cstdlib>
#endif

/* Macro defined in splitmuxsrc plugin */
#define FIXED_TS_OFFSET (1000*GST_SECOND)
//#define GIOSRC_FRAME_DEBUG

#if CLIP_READER_GIOSRC_EXTRA_DEBUG
// Optional verbose giosrc probes (see ClipReaderConfig::enable_giosrc_debug_probes / VST_CLIP_READER_GIOSRC_DEBUG).
struct GiosrcDebugState
{
    std::atomic<uint64_t> cumulative_src_bytes{0};
    std::atomic<uint64_t> max_src_offset_end{0};
    std::atomic<uint64_t> demux_video_buffer_count{0};
    std::string growing_file_path;
};

struct GiosrcSrcProbeUserData
{
    GiosrcDebugState* dbg = nullptr;
    gboolean is_growing = FALSE;
    gchar* file_path = nullptr;
};

struct GiosrcDemuxVideoProbeUserData
{
    GiosrcDebugState* dbg = nullptr;
    gchar* pad_name = nullptr;
};

static void giosrc_src_probe_user_data_free(gpointer data)
{
    auto* u = static_cast<GiosrcSrcProbeUserData*>(data);
    if (!u) return;
    g_free(u->file_path);
    g_free(u);
}

static void giosrc_demux_probe_user_data_free(gpointer data)
{
    auto* u = static_cast<GiosrcDemuxVideoProbeUserData*>(data);
    if (!u) return;
    g_free(u->pad_name);
    g_free(u);
}

static GstPadProbeReturn giosrc_src_debug_probe_cb(GstPad* /*pad*/, GstPadProbeInfo* info, gpointer user_data)
{
    auto* u = static_cast<GiosrcSrcProbeUserData*>(user_data);
    if (!u) return GST_PAD_PROBE_OK;

    if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER)
    {
        GstBuffer* buf = GST_PAD_PROBE_INFO_BUFFER(info);
        if (buf)
        {
            const gsize sz = gst_buffer_get_size(buf);
            if (u->dbg)
            {
                u->dbg->cumulative_src_bytes.fetch_add(static_cast<uint64_t>(sz));
                if (GST_BUFFER_OFFSET_IS_VALID(buf))
                {
                    const guint64 end = GST_BUFFER_OFFSET(buf) + static_cast<uint64_t>(sz);
                    uint64_t prev = u->dbg->max_src_offset_end.load();
                    while (end > prev && !u->dbg->max_src_offset_end.compare_exchange_weak(prev, end)) {}
                }
                LOG(verbose) << "ClipReaderProducer (giosrc DEBUG) giosrc src: is_growing=" << (u->is_growing ? "1" : "0")
                             << " file=" << (u->file_path ? u->file_path : "?") << " buf_size=" << sz
                             << " offset_valid=" << (GST_BUFFER_OFFSET_IS_VALID(buf) ? 1 : 0)
                             << " cumulative=" << u->dbg->cumulative_src_bytes.load()
                             << " max_offset_end=" << u->dbg->max_src_offset_end.load() << endl;
            }
        }
    }
    else if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_EVENT_DOWNSTREAM)
    {
        GstEvent* ev = GST_PAD_PROBE_INFO_EVENT(info);
        if (ev && GST_EVENT_TYPE(ev) == GST_EVENT_EOS && u->dbg)
        {
            LOG(info) << "ClipReaderProducer (giosrc DEBUG) giosrc src pad: EOS event is_growing="
                      << (u->is_growing ? "1" : "0") << " file=" << (u->file_path ? u->file_path : "?") << endl;
        }
    }
    return GST_PAD_PROBE_OK;
}

static GstPadProbeReturn giosrc_demux_video_debug_probe_cb(GstPad* /*pad*/, GstPadProbeInfo* info, gpointer user_data)
{
    auto* u = static_cast<GiosrcDemuxVideoProbeUserData*>(user_data);
    if (!u || !u->dbg) return GST_PAD_PROBE_OK;

    if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER)
    {
        GstBuffer* buf = GST_PAD_PROBE_INFO_BUFFER(info);
        if (buf)
        {
            const uint64_t n = u->dbg->demux_video_buffer_count.fetch_add(1) + 1;
            const gsize sz = gst_buffer_get_size(buf);
            const GstClockTime pts = GST_BUFFER_PTS(buf);
            LOG(verbose) << "ClipReaderProducer (giosrc DEBUG) demux video: pad=" << (u->pad_name ? u->pad_name : "?")
                         << " n=" << n << " size=" << sz << " pts_ns=" << pts
                         << (GST_CLOCK_TIME_IS_VALID(pts) ? "" : " (invalid)") << endl;
        }
    }
    else if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_EVENT_DOWNSTREAM)
    {
        GstEvent* ev = GST_PAD_PROBE_INFO_EVENT(info);
        if (ev && GST_EVENT_TYPE(ev) == GST_EVENT_EOS)
        {
            LOG(info) << "ClipReaderProducer (giosrc DEBUG) demux video pad: EOS pad=" << (u->pad_name ? u->pad_name : "?")
                      << endl;
        }
    }
    return GST_PAD_PROBE_OK;
}

#endif // CLIP_READER_GIOSRC_EXTRA_DEBUG

namespace {

static constexpr int kMainLoopStartWaitMs = 5;
static constexpr int kMainLoopStartRetry = 10;
static const std::string kEmptyLogPrefix;

static inline const std::string& getLogPrefix(const ClipReaderProducer* owner)
{
    return owner ? owner->logPrefix() : kEmptyLogPrefix;
}

static void set_all_giosrc_is_growing(GstElement* pipeline, gboolean is_growing)
{
    if (!pipeline) return;
    if (!GST_IS_BIN(pipeline)) return;

    GstIterator* it = gst_bin_iterate_recurse(GST_BIN(pipeline));
    if (!it) return;

    GValue item = G_VALUE_INIT;
    while (gst_iterator_next(it, &item) == GST_ITERATOR_OK)
    {
        GstElement* elem = GST_ELEMENT(g_value_get_object(&item));
        if (elem)
        {
            GstElementFactory* f = gst_element_get_factory(elem);
            const gchar* name = f ? gst_plugin_feature_get_name(GST_PLUGIN_FEATURE(f)) : nullptr;
            if (name && g_strcmp0(name, "giosrc") == 0)
            {
                g_object_set(G_OBJECT(elem), "is-growing", is_growing, nullptr);
            }
        }
        g_value_reset(&item);
    }
    g_value_unset(&item);
    gst_iterator_free(it);
}

static std::string detect_demuxer_for_path(const std::string& filePath)
{
    std::string demuxerName = "qtdemux"; // default for mp4/mov
    size_t dotPos = filePath.rfind('.');
    if (dotPos == std::string::npos) return demuxerName;

    std::string ext = filePath.substr(dotPos + 1);
    std::transform(ext.begin(), ext.end(), ext.begin(), ::tolower);

    if (ext == "mkv" || ext == "webm")
        return "matroskademux";
    if (ext == "ts" || ext == "m2ts" || ext == "mts")
        return "tsdemux";
    if (ext == "flv")
        return "flvdemux";
    if (ext == "avi")
        return "avidemux";

    return demuxerName;
}

// Appsink sink pad probe: store buffers in post-seek queue when seek is done and not yet playing.
// Uses public ClipReaderProducer API so it can stay a free function.
static GstPadProbeReturn appsink_collect_probe_cb(GstPad* pad, GstPadProbeInfo* info, gpointer user_data)
{
    if (!(GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER))
        return GST_PAD_PROBE_OK;
    GstBuffer* buf = GST_PAD_PROBE_INFO_BUFFER(info);
    if (!buf)
        return GST_PAD_PROBE_OK;
    ClipReaderProducer* producer = static_cast<ClipReaderProducer*>(user_data);
    if (!producer)
        return GST_PAD_PROBE_OK;
    // Store only after seek is complete and until pipeline reaches PLAYING
    if (!producer->seekDone() || producer->playingStateReached())
        return GST_PAD_PROBE_OK;

    GstBuffer* ref_buf = gst_buffer_ref(buf);
    GstCaps* caps = gst_pad_get_current_caps(pad);
    if (caps)
        caps = gst_caps_ref(caps);
    int64_t pts_ms = GST_BUFFER_PTS_IS_VALID(buf) ? (int64_t)(GST_BUFFER_PTS(buf) / 1000000) : -1;
    size_t size = (size_t)gst_buffer_get_size(buf);
    producer->pushPostSeekBuffer(ref_buf, caps, pts_ms, size);
    return GST_PAD_PROBE_OK;
}

static bool link_queue_to_concat(GstElement* queue, GstElement* concat, const char* tag, ClipReaderProducer* owner)
{
    if (!queue || !concat) return false;

    GstPad* sinkpad = gst_element_get_request_pad(concat, "sink_%u");
    GstPad* srcpad  = gst_element_get_static_pad(queue, "src");
    if (!sinkpad || !srcpad)
    {
        if (sinkpad) gst_object_unref(sinkpad);
        if (srcpad) gst_object_unref(srcpad);
        LOG(error) << getLogPrefix(owner)
                   << "ClipReaderProducer (giosrc): Failed to get pads for concat link (" << (tag ? tag : "?") << ")" << endl;
        return false;
    }

    GstPadLinkReturn ret = gst_pad_link(srcpad, sinkpad);
    gst_object_unref(srcpad);
    gst_object_unref(sinkpad);

    if (ret != GST_PAD_LINK_OK)
    {
        LOG(error) << getLogPrefix(owner)
                   << "ClipReaderProducer (giosrc): Failed to link queue into concat (" << (tag ? tag : "?")
                   << "), ret=" << ret << endl;
        return false;
    }
    return true;
}

static void link_splitmuxsrc_to_queues(GstElement* src,
                                       GstElement* vq,
                                       GstElement* aq,
                                       bool audio_enabled,
                                       ClipReaderProducer* owner)
{
    struct PadCtx { GstElement* vq; GstElement* aq; bool audio; ClipReaderProducer* owner; } *ctx =
        (PadCtx*)g_malloc0(sizeof(PadCtx));
    ctx->vq = vq; ctx->aq = aq; ctx->audio = audio_enabled; ctx->owner = owner;

    g_signal_connect_data(src, "pad-added", G_CALLBACK(+[] (GstElement* /*src*/, GstPad* new_pad, gpointer user_data) {
        PadCtx* c = (PadCtx*)user_data;
        if (!c) return;
        GstCaps* caps = gst_pad_get_current_caps(new_pad);
        gchar* caps_str = caps ? gst_caps_to_string(caps) : g_strdup("<none>");
        bool is_audio = (caps_str && g_str_has_prefix(caps_str, "audio"));
        // If audio is disabled and this is an audio pad, skip linking to avoid not-negotiated error
        if (is_audio && (!c->audio || !c->aq))
        {
            LOG(info) << getLogPrefix(c->owner)
                      << "ClipReaderProducer: Skipping audio pad link (audio disabled in configuration)" << endl;
            if (caps) gst_caps_unref(caps);
            if (caps_str) g_free(caps_str);
            return;
        }

        GstElement* target_q = is_audio ? c->aq : c->vq;
        if (caps) gst_caps_unref(caps);
        if (caps_str) g_free(caps_str);
        if (!target_q) return;
        GstPad* sink_pad = gst_element_get_static_pad(target_q, "sink");
        if (!sink_pad) return;
        if (!gst_pad_is_linked(sink_pad))
        {
            gst_pad_link(new_pad, sink_pad);
        }
        gst_object_unref(sink_pad);
    }), ctx, (GClosureNotify)g_free, (GConnectFlags)0);
}

static void link_demuxer_to_queues(GstElement* demuxer,
                                   GstElement* vq,
                                   GstElement* aq,
                                   bool audio_enabled,
                                   ClipReaderProducer* owner
#if CLIP_READER_GIOSRC_EXTRA_DEBUG
                                   ,
                                   GiosrcDebugState* giosrc_dbg
#endif
)
{
    struct PadCtx
    {
        GstElement* vq;
        GstElement* aq;
        bool audio;
        ClipReaderProducer* owner;
#if CLIP_READER_GIOSRC_EXTRA_DEBUG
        GiosrcDebugState* giosrc_dbg;
#endif
    } *ctx = (PadCtx*)g_malloc0(sizeof(PadCtx));
    ctx->vq = vq;
    ctx->aq = aq;
    ctx->audio = audio_enabled;
    ctx->owner = owner;
#if CLIP_READER_GIOSRC_EXTRA_DEBUG
    ctx->giosrc_dbg = giosrc_dbg;
#endif

    auto pad_added_cb = +[] (GstElement* /*src*/, GstPad* new_pad, gpointer user_data) {
        PadCtx* c = (PadCtx*)user_data;
        if (!c) return;
        GstCaps* caps = gst_pad_get_current_caps(new_pad);
        if (!caps) caps = gst_pad_query_caps(new_pad, nullptr);
        gchar* caps_str = caps ? gst_caps_to_string(caps) : g_strdup("<none>");
        bool is_video = (caps_str && (g_str_has_prefix(caps_str, "video/x-h264") ||
                                      g_str_has_prefix(caps_str, "video/x-h265")));
        bool is_audio = (caps_str && g_str_has_prefix(caps_str, "audio"));

        LOG(info) << getLogPrefix(c->owner)
                  << "ClipReaderProducer (giosrc): pad-added with caps: " << caps_str << endl;
        // If audio is disabled and this is an audio pad, skip linking
        if (is_audio && (!c->audio || !c->aq))
        {
            LOG(info) << getLogPrefix(c->owner)
                      << "ClipReaderProducer (giosrc): Skipping audio pad link (audio disabled)" << endl;
            if (caps) gst_caps_unref(caps);
            if (caps_str) g_free(caps_str);
            return;
        }

        GstElement* target_q = is_video ? c->vq : (is_audio ? c->aq : nullptr);
        if (caps) gst_caps_unref(caps);
        if (caps_str) g_free(caps_str);
        if (!target_q) return;
        GstPad* sink_pad = gst_element_get_static_pad(target_q, "sink");
        if (!sink_pad) return;
        if (!gst_pad_is_linked(sink_pad))
        {
            GstPadLinkReturn ret = gst_pad_link(new_pad, sink_pad);
            if (ret != GST_PAD_LINK_OK)
            {
                LOG(warning) << getLogPrefix(c->owner)
                             << "ClipReaderProducer (giosrc): Failed to link pad, ret=" << ret << endl;
            }
#if CLIP_READER_GIOSRC_EXTRA_DEBUG
            else if (is_video && c->giosrc_dbg)
            {
                auto* u = static_cast<GiosrcDemuxVideoProbeUserData*>(g_malloc0(sizeof(GiosrcDemuxVideoProbeUserData)));
                u->dbg = c->giosrc_dbg;
                u->pad_name = gst_pad_get_name(new_pad);
                gst_pad_add_probe(new_pad,
                    (GstPadProbeType)(GST_PAD_PROBE_TYPE_BUFFER | GST_PAD_PROBE_TYPE_EVENT_DOWNSTREAM),
                    giosrc_demux_video_debug_probe_cb, u, giosrc_demux_probe_user_data_free);
            }
#endif
        }
        gst_object_unref(sink_pad);
    };

    g_signal_connect_data(demuxer, "pad-added", G_CALLBACK(pad_added_cb), ctx, (GClosureNotify)g_free, (GConnectFlags)0);
}

// VCL (Video Coding Layer) NAL units actually carry encoded picture data.
// In H.264, types 1..5 are VCL (normal slices and IDR slices).
// In H.265, types 0..31 are VCL (see T-REC H.265 table 7-1).
static inline bool isVclNalType(uint8_t nal_type, bool is_h265)
{
    if (is_h265)
    {
        return nal_type <= 31;
    }
    return nal_type >= 1 && nal_type <= 5;
}

// Parse byte-stream NAL start codes (0x000001 or 0x00000001). Returns the
// offset just past the start code, or (size_t)-1 if no start code found.
static inline size_t findByteStreamStartCode(const uint8_t* data, size_t size, size_t from)
{
    if (from + 3 > size) return static_cast<size_t>(-1);
    for (size_t i = from; i + 2 < size; ++i)
    {
        if (data[i] == 0x00 && data[i + 1] == 0x00)
        {
            if (data[i + 2] == 0x01)
            {
                return i + 3;
            }
            if (i + 3 < size && data[i + 2] == 0x00 && data[i + 3] == 0x01)
            {
                return i + 4;
            }
        }
    }
    return static_cast<size_t>(-1);
}

// Returns true if the buffer contains at least one VCL (slice) NAL unit.
// Buffers that are all non-VCL (AUD / SPS / PPS / SEI / Prefix / Filler /
// etc.) carry no picture data and must not be forwarded to mp4mux on their
// own — mp4mux can reframe them with a preceding or following AU and lose
// PTS in the process ("Buffer has no PTS. Could not multiplex stream.").
static bool hasVclNalInBuffer(const uint8_t* data, size_t size, bool is_h265, bool is_avc_format)
{
    if (!data || size < 2) return false;

    auto decodeType = [&](uint8_t header_byte) -> uint8_t {
        return is_h265 ? static_cast<uint8_t>((header_byte >> 1) & 0x3F)
                       : static_cast<uint8_t>(header_byte & 0x1F);
    };

    if (is_avc_format)
    {
        size_t pos = 0;
        // Need at least 4 bytes of length prefix + 1 byte of NAL header (read at data[pos]
        // on line below), hence strict '<' against size.
        while (pos + 4 < size)
        {
            uint32_t nal_len = (static_cast<uint32_t>(data[pos]) << 24)
                             | (static_cast<uint32_t>(data[pos + 1]) << 16)
                             | (static_cast<uint32_t>(data[pos + 2]) << 8)
                             |  static_cast<uint32_t>(data[pos + 3]);
            pos += 4;
            if (nal_len == 0 || pos >= size) break;
            if (pos + nal_len > size) break;
            if (isVclNalType(decodeType(data[pos]), is_h265))
            {
                return true;
            }
            pos += nal_len;
        }
    }
    else
    {
        size_t pos = findByteStreamStartCode(data, size, 0);
        while (pos != static_cast<size_t>(-1) && pos < size)
        {
            if (isVclNalType(decodeType(data[pos]), is_h265))
            {
                return true;
            }
            pos = findByteStreamStartCode(data, size, pos + 1);
        }
    }
    return false;
}

} // namespace

ClipReaderProducer::ClipReaderProducer(const ClipReaderConfig& cfg)
: mCfg(cfg)
, m_recoveryEventLoop(std::make_unique<EventLoop>("clip_reader_retry", &ClipReaderProducer::process_retry_message))
{
#if CLIP_READER_GIOSRC_EXTRA_DEBUG
    const char* env = std::getenv("VST_CLIP_READER_GIOSRC_DEBUG");
    const bool env_on = env && env[0] != '\0' && std::strcmp(env, "0") != 0;
    mGiosrcDebugProbes = mCfg.enable_giosrc_debug_probes || env_on;
#endif
    mLogPrefix = mCfg.log_id.empty() ? "" : ("[" + mCfg.log_id + "] ");
    LOG(info) << mLogPrefix << "ClipReaderProducer constructor" << endl;
#if CLIP_READER_GIOSRC_EXTRA_DEBUG
    if (mGiosrcDebugProbes)
    {
        LOG(info) << mLogPrefix << "ClipReaderProducer: giosrc DEBUG probes enabled (config or VST_CLIP_READER_GIOSRC_DEBUG)"
                  << endl;
    }
#endif
    LOG(info) << mLogPrefix << "cfg.file_paths.size() = " << cfg.file_paths.size() << endl;
    for (const auto& file_path : cfg.file_paths)
    {
        LOG(info) << mLogPrefix << "file_path = " << file_path << endl;
    }
    LOG(info) << mLogPrefix << "cfg.file_start_epoch_ms = " << cfg.file_start_epoch_ms << endl;
    LOG(info) << mLogPrefix << "cfg.seek_start_ms = " << cfg.seek_start_ms << endl;
    LOG(info) << mLogPrefix << "cfg.seek_end_ms = " << cfg.seek_end_ms << endl;
}

ClipReaderProducer::~ClipReaderProducer()
{
    try {
        stop();
        teardown();
        LOG(info) << mLogPrefix << "~ClipReaderProducer destructor" << endl;
    } catch (const std::exception& e) {
        try { LOG(error) << "Exception in ~ClipReaderProducer: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
    } catch (...) {
        try { LOG(error) << "Unknown exception in ~ClipReaderProducer" << endl; } catch (...) { (void)std::current_exception(); }
    }
}

void ClipReaderProducer::notifyFinishedOnce()
{
    if (!mFinishedCb) return;
    if (!mFinishedNotified.exchange(true))
    {
        LOG(info) << mLogPrefix << "ClipReaderProducer: notifyFinishedOnce" << endl;
        mFinishedCb();
    }
}

void ClipReaderProducer::onGiosrcWaitingData(GstElement* /*src*/, gpointer user_data)
{
    ClipReaderProducer* p = static_cast<ClipReaderProducer*>(user_data);
    if (!p) return;
    if (!p->mRunning.load()) return;
    const std::string prefix = p->logPrefix();
    const int64_t lastSent = p->mLastSentPts.load();
    const int64_t lastAppsink = p->mLastAppsinkPtsMs.load();
    LOG(info) << prefix << "ClipReaderProducer (giosrc): waiting-data - giosrc blocking at EOF, waiting for more bytes"
              << " last_pts_sent_to_consumer_ms=" << lastSent << " last_pts_appsink_ms=" << lastAppsink << endl;
#if CLIP_READER_GIOSRC_EXTRA_DEBUG
    p->logGiosrcDebugWaitingDataCorrelation();
#endif

    std::lock_guard<std::mutex> lk(p->mGiosrcWaitingMtx);
    if (p->mGiosrcWaitingTimeoutSource)
    {
        g_source_destroy(p->mGiosrcWaitingTimeoutSource);
        g_source_unref(p->mGiosrcWaitingTimeoutSource);
        p->mGiosrcWaitingTimeoutSource = nullptr;
    }
    if (!p->mMainContext) return;

    p->mGiosrcWaitingTimeoutSource = g_timeout_source_new_seconds(p->GIOSRC_WAITING_TIMEOUT_SEC);
    g_source_set_callback(p->mGiosrcWaitingTimeoutSource,
        [](gpointer udata) -> gboolean {
            ClipReaderProducer* producer = static_cast<ClipReaderProducer*>(udata);
            producer->onGiosrcWaitingTimeout();
            return G_SOURCE_REMOVE;
        }, p, nullptr);
    g_source_attach(p->mGiosrcWaitingTimeoutSource, p->mMainContext);
}

void ClipReaderProducer::onGiosrcDoneWaitingData(GstElement* /*src*/, gpointer user_data)
{
    ClipReaderProducer* p = static_cast<ClipReaderProducer*>(user_data);
    if (!p) return;
    const std::string prefix = p->logPrefix();
    LOG(info) << prefix << "ClipReaderProducer (giosrc): done-waiting-data - giosrc received new data, resuming" << endl;
#if CLIP_READER_GIOSRC_EXTRA_DEBUG
    p->logGiosrcDebugWaitingDataCorrelation();
#endif

    std::lock_guard<std::mutex> lk(p->mGiosrcWaitingMtx);
    if (p->mGiosrcWaitingTimeoutSource)
    {
        g_source_destroy(p->mGiosrcWaitingTimeoutSource);
        g_source_unref(p->mGiosrcWaitingTimeoutSource);
        p->mGiosrcWaitingTimeoutSource = nullptr;
    }
}

void ClipReaderProducer::onGiosrcWaitingTimeout()
{
    {
        std::lock_guard<std::mutex> lk(mGiosrcWaitingMtx);
        if (mGiosrcWaitingTimeoutSource)
        {
            g_source_unref(mGiosrcWaitingTimeoutSource);
            mGiosrcWaitingTimeoutSource = nullptr;
        }
    }
    if (mEosSignalled.load() || !mRunning.load()) return;
    if (!mPipeline) return;

    LOG(warning) << mLogPrefix << "ClipReaderProducer (giosrc): waiting-data timeout - no new data for "
                 << GIOSRC_WAITING_TIMEOUT_SEC << "s, treating as EOF" << endl;

    mEosSignalled.store(true);
    set_all_giosrc_is_growing(mPipeline, FALSE);
    gst_element_send_event(mPipeline, gst_event_new_flush_start());
    gst_element_send_event(mPipeline, gst_event_new_flush_stop(TRUE));
    gst_element_send_event(mPipeline, gst_event_new_eos());
}

#if CLIP_READER_GIOSRC_EXTRA_DEBUG
void ClipReaderProducer::logGiosrcDebugWaitingDataCorrelation() const
{
    if (!mGiosrcDebugProbes || !mGiosrcDebug) return;

    const uint64_t cum = mGiosrcDebug->cumulative_src_bytes.load();
    const uint64_t maxo = mGiosrcDebug->max_src_offset_end.load();
    const uint64_t nv = mGiosrcDebug->demux_video_buffer_count.load();
    uint64_t disk = 0;
    struct stat st {};
    const std::string& path = mGiosrcDebug->growing_file_path;
    if (!path.empty() && stat(path.c_str(), &st) == 0)
        disk = static_cast<uint64_t>(st.st_size);
    const int64_t gap = (disk > cum) ? static_cast<int64_t>(disk - cum) : int64_t(0);
    LOG(info) << mLogPrefix << "ClipReaderProducer (giosrc DEBUG) waiting-data correlation: growing_file=" << path
              << " disk_size=" << disk << " cumulative_bytes_giosrc_src=" << cum
              << " max_buffer_offset_end=" << maxo << " demux_video_buffers=" << nv
              << " disk_minus_cumulative=" << gap
              << " (instant snapshot; gap>0 is normal if recorder is still writing or before next read — see "
                 "done-waiting-data)" << endl;
}
#endif

void ClipReaderProducer::notifyErrorOnce(const std::string& msg, int code)
{
    if (!mErrorCb) return;
    if (!mErrorNotified.exchange(true))
    {
        mErrorCb(msg, code);
    }
}

void ClipReaderProducer::registerConsumer(std::shared_ptr<IMediaDataConsumer> consumer,
                                          const std::string& identifier)
{
    // Default to "video" media type
    registerConsumer(consumer, identifier, "video");
}

void ClipReaderProducer::registerConsumer(std::shared_ptr<IMediaDataConsumer> consumer,
                                          const std::string& identifier,
                                          const std::string& media_type)
{
    if (!consumer) {
        LOG(warning) << mLogPrefix << "ClipReaderProducer: Attempted to register null consumer" << endl;
        return;
    }

    {
        std::lock_guard<std::mutex> lk(mConsumersMtx);
        mConsumers[media_type] = consumer;
        LOG(info) << mLogPrefix << "ClipReaderProducer: Consumer registered for '" << media_type 
                  << "'. Total consumers: " << mConsumers.size() << endl;
    }

    // If seek is done but pipeline isn't PLAYING yet, start playing now
    // (busWatch waits for consumers before transitioning to PLAYING)
    if (mSeekDone.load() && mPipeline)
    {
        GstState current_state = GST_STATE_NULL;
        gst_element_get_state(mPipeline, &current_state, nullptr, 0);
        if (current_state != GST_STATE_PLAYING)
        {
            LOG(info) << mLogPrefix << "ClipReaderProducer: Consumer registered, starting playback" << endl;
            gst_element_set_state(mPipeline, GST_STATE_PLAYING);
        }
    }
}


void ClipReaderProducer::unregisterConsumer(std::shared_ptr<IMediaDataConsumer> consumer,
                                           const std::string& identifier, bool doNotRemoveClient)
{
    if (!consumer) {
        return;
    }

    std::lock_guard<std::mutex> lk(mConsumersMtx);

    // Find and remove the consumer by matching the pointer
    for (auto it = mConsumers.begin(); it != mConsumers.end(); ++it) {
        if (it->second == consumer) {
            LOG(info) << mLogPrefix << "ClipReaderProducer: Consumer unregistered for '" << it->first 
                      << "'. Remaining consumers: " << (mConsumers.size() - 1) << endl;
            mConsumers.erase(it);
            break;
        }
    }
}

bool ClipReaderProducer::startMainLoop()
{
    if (!mPipeline)
    {
        LOG(error) << mLogPrefix << "ClipReaderProducer: Cannot start main loop without pipeline" << endl;
        return false;
    }

    // Defensive cleanup if a previous loop is still around
    stopMainLoop();
    {
        std::lock_guard<std::mutex> lk(mMainLoopMtx);
        mMainLoopRunning = false;
    }

    mMainContext = g_main_context_new();
    if (!mMainContext)
    {
        LOG(error) << mLogPrefix << "ClipReaderProducer: Failed to create main context" << endl;
        return false;
    }

    mMainLoop = g_main_loop_new(mMainContext, FALSE);
    if (!mMainLoop)
    {
        g_main_context_unref(mMainContext);
        mMainContext = nullptr;
        LOG(error) << mLogPrefix << "ClipReaderProducer: Failed to create main loop" << endl;
        return false;
    }

    std::string threadName = "clip_reader_mainloop";
    if (!mCfg.stream_id.empty())
    {
        threadName += "_" + mCfg.stream_id;
    }

    mGMainLoopThread = g_thread_new(threadName.c_str(), ClipReaderProducer::gmainLoopThread, this);
    if (!mGMainLoopThread)
    {
        g_main_loop_unref(mMainLoop);
        g_main_context_unref(mMainContext);
        mMainLoop = nullptr;
        mMainContext = nullptr;
        LOG(error) << mLogPrefix << "ClipReaderProducer: Failed to create main loop thread" << endl;
        return false;
    }

    {
        std::unique_lock<std::mutex> lk(mMainLoopMtx);
        for (int try_count = 0; !mMainLoopRunning && try_count <= kMainLoopStartRetry; ++try_count)
        {
            if (mMainLoopCv.wait_for(lk, std::chrono::milliseconds(kMainLoopStartWaitMs),
                                     [this]() { return mMainLoopRunning; }))
            {
                break;
            }
            if (try_count == kMainLoopStartRetry)
            {
                LOG(error) << mLogPrefix << "ClipReaderProducer: Failed to start main loop" << endl;
                lk.unlock();
                stopMainLoop();
                return false;
            }
        }
    }

    return true;
}

void ClipReaderProducer::stopMainLoop()
{
    if (mMainLoop && g_main_loop_is_running(mMainLoop))
    {
        g_main_loop_quit(mMainLoop);
    }

    if (mGMainLoopThread)
    {
        g_thread_join(mGMainLoopThread);
        mGMainLoopThread = nullptr;
    }

    if (mMainLoop)
    {
        g_main_loop_unref(mMainLoop);
        mMainLoop = nullptr;
    }

    // Destroy giosrc waiting-data timeout source before unreffing context.
    // The source holds a ref to mMainContext; unreffing it first avoids leaking the GSource
    // and ensures the context refcount can reach zero.
    {
        std::lock_guard<std::mutex> lk(mGiosrcWaitingMtx);
        if (mGiosrcWaitingTimeoutSource)
        {
            g_source_destroy(mGiosrcWaitingTimeoutSource);
            g_source_unref(mGiosrcWaitingTimeoutSource);
            mGiosrcWaitingTimeoutSource = nullptr;
        }
    }

    if (mMainContext)
    {
        g_main_context_unref(mMainContext);
        mMainContext = nullptr;
    }

    if (mGSource)
    {
        g_source_unref(mGSource);
        mGSource = nullptr;
    }

    {
        std::lock_guard<std::mutex> lk(mMainLoopMtx);
        mMainLoopRunning = false;
    }
}

gpointer ClipReaderProducer::gmainLoopThread(gpointer data)
{
    ClipReaderProducer* producer = static_cast<ClipReaderProducer*>(data);
    if (!producer)
    {
        return nullptr;
    }

    g_main_context_push_thread_default(producer->mMainContext);

    GstBus* bus = gst_pipeline_get_bus(GST_PIPELINE(producer->mPipeline));
    if (!bus)
    {
        LOG(error) << producer->mLogPrefix << "ClipReaderProducer: Failed to get pipeline bus" << endl;
        g_main_context_pop_thread_default(producer->mMainContext);
        return nullptr;
    }

    producer->mGSource = gst_bus_create_watch(bus);
    if (!producer->mGSource)
    {
        LOG(error) << producer->mLogPrefix << "ClipReaderProducer: Failed to create bus watch" << endl;
        gst_object_unref(bus);
        g_main_context_pop_thread_default(producer->mMainContext);
        return nullptr;
    }

    g_source_set_callback(producer->mGSource, (GSourceFunc)ClipReaderProducer::busWatch, producer, nullptr);
    if (g_source_attach(producer->mGSource, producer->mMainContext) <= 0)
    {
        LOG(error) << producer->mLogPrefix << "ClipReaderProducer: Failed to attach bus watch to context" << endl;
        gst_bus_remove_watch(bus);
        gst_object_unref(bus);
        g_source_unref(producer->mGSource);
        producer->mGSource = nullptr;
        g_main_context_pop_thread_default(producer->mMainContext);
        return nullptr;
    }

    {
        std::lock_guard<std::mutex> lk(producer->mMainLoopMtx);
        producer->mMainLoopRunning = true;
    }
    producer->mMainLoopCv.notify_all();

    g_main_loop_run(producer->mMainLoop);

    // Cleanup
    gst_bus_remove_watch(bus);
    gst_object_unref(bus);
    if (producer->mGSource)
    {
        g_source_unref(producer->mGSource);
        producer->mGSource = nullptr;
    }

    g_main_context_pop_thread_default(producer->mMainContext);
    {
        std::lock_guard<std::mutex> lk(producer->mMainLoopMtx);
        producer->mMainLoopRunning = false;
    }
    producer->mMainLoopCv.notify_all();

    LOG(info) << producer->mLogPrefix << "ClipReaderProducer: GMainLoop thread exited" << endl;
    return nullptr;
}

bool ClipReaderProducer::start()
{
    if (mRunning.load()) return true;
    if (!gst_is_initialized()) { gst_init(nullptr, nullptr); }

    m_recoveryEventLoop->setParent(this);

    // Save original seek_start_ms before any retries can modify it
    mOriginalSeekStartMs.store(mCfg.seek_start_ms);

    // Reset state tracking for fresh start
    mPausedStateReached.store(false);
    mSeekDone.store(false);
    mPlayingStateReached.store(false);
    mFinishedNotified.store(false);
    mErrorNotified.store(false);
    mGiosrcVideoGopCache.clear();
    mLastSentPts.store(0);
    mFirstVideoSentPts.store(0);
    mResumePts.store(0);
    mPrevBufMs.store(0);
    mFrameThresholdMs.store(0);
    mEosSignalled.store(false);
    mPrematureEosRetryCount.store(0);

    if (!buildPipeline()) return false;
    if (!startMainLoop())
    {
        teardown();
        return false;
    }

    // Set pipeline to PAUSED first (for preroll)
    // Seek will be performed in busWatch() after ASYNC_DONE is received (like CloudStreamProducer)
    GstStateChangeReturn ret = gst_element_set_state(mPipeline, GST_STATE_PAUSED);
    if (ret == GST_STATE_CHANGE_FAILURE)
    {
        LOG(error) << mLogPrefix << "ClipReaderProducer: failed to set PAUSED" << endl;
        stopMainLoop();
        teardown();
        return false;
    }

    LOG(info) << mLogPrefix << "ClipReaderProducer: Pipeline started in PAUSED state, waiting for preroll..." << endl;
    mRunning.store(true);

    return true;
}

void ClipReaderProducer::stop()
{
    LOG(info) << mLogPrefix << "ClipReaderProducer::stop() Enter" << endl;
    if (!mRunning.exchange(false)) return;

    // Wait for retry to complete if one is in progress
    if (mRetryInProgress.load())
    {
        constexpr unsigned int kRetryWaitTimeoutMs = 30000;
        if (!mRetrySync.wait(kRetryWaitTimeoutMs))
        {
            LOG(warning) << mLogPrefix << "ClipReaderProducer::stop() - Retry wait timeout, proceeding" << endl;
        }
    }

    mGiosrcVideoGopCache.clear();

    {
        std::lock_guard<std::mutex> lk(mGiosrcWaitingMtx);
        if (mGiosrcWaitingTimeoutSource)
        {
            g_source_destroy(mGiosrcWaitingTimeoutSource);
            g_source_unref(mGiosrcWaitingTimeoutSource);
            mGiosrcWaitingTimeoutSource = nullptr;
        }
    }

    stopMainLoop();

    if (mPipeline)
    {
        // IMPORTANT: giosrc with is-growing=true can block teardown/state changes.
        // Proactively disable growing and flush to unblock streaming threads before NULL.
        if (mUsingGiosrc)
        {
            LOG(info) << mLogPrefix << "giosrc flush start before NULL transition" << endl;
            set_all_giosrc_is_growing(mPipeline, FALSE);
            gst_element_send_event(mPipeline, gst_event_new_flush_start());
            gst_element_send_event(mPipeline, gst_event_new_flush_stop(TRUE));
        }

        // Original/simple stop logic: set NULL and wait with timeout protection.
        LOG(info) << mLogPrefix << "ClipReaderProducer::stop() - Setting pipeline to NULL state" << endl;
        GstStateChangeReturn ret = gst_element_set_state(mPipeline, GST_STATE_NULL);
        if (ret == GST_STATE_CHANGE_ASYNC)
        {
            GstState current, pending;
            ret = gst_element_get_state(mPipeline, &current, &pending, 5 * GST_SECOND);
            if (ret == GST_STATE_CHANGE_FAILURE || ret == GST_STATE_CHANGE_ASYNC)
            {
                LOG(error) << mLogPrefix << "ClipReaderProducer::stop() - Pipeline failed to reach NULL state within timeout" << endl;
            }
        }
    }
    LOG(info) << mLogPrefix << "ClipReaderProducer::stop() - complete" << endl;
}

bool ClipReaderProducer::isRunning() const { return mRunning.load(); }

eMediaType ClipReaderProducer::getProducerMediaType() const
{
    return MediaTypeVideo;
}

std::string ClipReaderProducer::getSourceIdentifier() const
{
    if (!mCfg.file_paths.empty())
    {
        return mCfg.file_paths.front();
    }
    return std::string();
}

size_t ClipReaderProducer::getConsumerCount() const
{
    std::lock_guard<std::mutex> lk(mConsumersMtx);
    return mConsumers.size();
}

bool ClipReaderProducer::hasConsumers() const { return getConsumerCount() > 0; }

void ClipReaderProducer::onFinished(std::function<void()> cb) { mFinishedCb = std::move(cb); }
void ClipReaderProducer::onError(std::function<void(const std::string&, int)> cb) { mErrorCb = std::move(cb); }

bool ClipReaderProducer::buildPipeline()
{
    // Check if we should use giosrc pipeline for growing files near live edge
    if (shouldUseGiosrcForGrowingFile())
    {
        LOG(info) << mLogPrefix << "ClipReaderProducer: Using giosrc pipeline for growing file near live edge" << endl;
        mUsingGiosrc = true;
        return buildGiosrcPipeline();
    }

    mUsingGiosrc = false;
    mPipeline = gst_pipeline_new("clip_reader_pipeline");
    mReaderSrc = gst_element_factory_make("splitmuxsrc", nullptr);
    mReaderVideoQueue = gst_element_factory_make("queue", nullptr);
    mReaderParser = gst_element_factory_make(iequals(mCfg.video_codec, "h265") ? "h265parse" : "h264parse", nullptr);
    mReaderFilter = gst_element_factory_make("capsfilter", nullptr);
    mReaderVideoSink = gst_element_factory_make("appsink", nullptr);

    if (!mPipeline || !mReaderSrc || !mReaderVideoQueue || !mReaderParser || !mReaderFilter || !mReaderVideoSink)
    {
        LOG(error) << mLogPrefix << "ClipReaderProducer: failed to create core reader elements" << endl;
        return false;
    }

    // Optional audio branch
    if (mCfg.enable_audio)
    {
        mReaderAudioQueue = gst_element_factory_make("queue", nullptr);
        mReaderAudioSink = gst_element_factory_make("appsink", nullptr);
        if (!mReaderAudioQueue || !mReaderAudioSink)
        {
            LOG(error) << mLogPrefix << "ClipReaderProducer: failed to create audio branch" << endl;
            return false;
        }
    }

    gst_bin_add_many(GST_BIN(mPipeline), mReaderSrc, mReaderVideoQueue, mReaderParser, mReaderFilter, mReaderVideoSink, nullptr);
    if (mCfg.enable_audio)
    {
        gst_bin_add_many(GST_BIN(mPipeline), mReaderAudioQueue, mReaderAudioSink, nullptr);
    }

    // Configure parser to output consistent byte-stream and headers, avoid passthrough stalls
    // config-interval=-1 forces SPS/PPS insertion before EVERY IDR (critical after seek)
    g_object_set(G_OBJECT(mReaderParser), "config-interval", -1, "disable-passthrough", TRUE, nullptr);

    // Byte-stream caps with AU alignment to match writer expectations
    {
        GstCaps* caps = gst_caps_new_simple(iequals(mCfg.video_codec, "h265") ? "video/x-h265" : "video/x-h264",
                                            "stream-format", G_TYPE_STRING, "byte-stream",
                                            "alignment", G_TYPE_STRING, "au",
                                            nullptr);
        g_object_set(G_OBJECT(mReaderFilter), "caps", caps, nullptr);
        gst_caps_unref(caps);
    }

    // appsink configuration
    g_object_set(G_OBJECT(mReaderVideoSink), "emit-signals", TRUE, "sync", FALSE, nullptr);
    g_signal_connect(G_OBJECT(mReaderVideoSink), "new-sample", G_CALLBACK(ClipReaderProducer::onVideoNewSample), this);

    if (mCfg.enable_audio && mReaderAudioSink)
    {
        g_object_set(G_OBJECT(mReaderAudioSink), "emit-signals", TRUE, "sync", FALSE, nullptr);
        g_signal_connect(G_OBJECT(mReaderAudioSink), "new-sample", G_CALLBACK(ClipReaderProducer::onAudioNewSample), this);
    }

    if (!linkElements()) return false;

    // Appsink sink pad buffer probe: collect buffers into post-seek queue.
    // Few buffers may be lost when pipeline is paused, so we need to collect them into a queue.
    // Only do this when video contains B-frames, as in other case, it works fine without it.
    if (mReaderVideoSink && mCfg.has_bframes)
    {
        GstPad* appsink_sink = gst_element_get_static_pad(mReaderVideoSink, "sink");
        if (appsink_sink)
        {
            gst_pad_add_probe(appsink_sink, GST_PAD_PROBE_TYPE_BUFFER,
                              appsink_collect_probe_cb, this, NULL);
            gst_object_unref(appsink_sink);
        }
    }

    // Provide file list to splitmuxsrc via format-location
    // Note: splitmuxsrc takes ownership of the returned GStrv, so we allocate a new copy each time
    g_signal_connect(G_OBJECT(mReaderSrc), "format-location",
                    G_CALLBACK(+[] (GstElement* /*element*/, gpointer user_data) -> GStrv {
                        // Create a fresh copy for splitmuxsrc to take ownership of
                        std::vector<std::string>* paths = static_cast<std::vector<std::string>*>(user_data);
                        GStrv locations = (GStrv)g_malloc0_n(paths->size() + 1, sizeof(gchar*));
                        for (size_t i = 0; i < paths->size(); ++i)
                        {
                            locations[i] = g_strdup((*paths)[i].c_str());
                        }
                        return locations; // splitmuxsrc will free this
                    }), &mCfg.file_paths);

    return true;
}

bool ClipReaderProducer::shouldUseGiosrcForGrowingFile() const
{
    // Must have at least one file path
    if (mCfg.file_paths.empty())
    {
        return false;
    }

    if (mCfg.bypass_giosrc_for_growing_file)
    {
        LOG(info) << "ClipReaderProducer: bypass_giosrc_for_growing_file=true, will not use giosrc pipeline" << endl;
        return false;
    }

    // If explicitly marked as growing file, use giosrc pipeline
    if (mCfg.is_growing_file)
    {
        LOG(info) << mLogPrefix << "ClipReaderProducer: is_growing_file=true, will use giosrc pipeline" << endl;
        return true;
    }

    return false;
}

bool ClipReaderProducer::buildGiosrcPipeline()
{
    mPipeline = gst_pipeline_new("clip_reader_giosrc_pipeline");
    // concat avoids manual file switching: each file has its own giosrc+demux+queues feeding concat.
    GstElement* videoConcat = gst_element_factory_make("concat", "clip_reader_video_concat");
    GstElement* audioConcat = mCfg.enable_audio ? gst_element_factory_make("concat", "clip_reader_audio_concat") : nullptr;

    // Post-concat chain
    mReaderVideoQueue = gst_element_factory_make("queue", nullptr); // post-concat queue
    mReaderParser = gst_element_factory_make(iequals(mCfg.video_codec, "h265") ? "h265parse" : "h264parse", nullptr);
    mReaderFilter = gst_element_factory_make("capsfilter", nullptr);
    mReaderVideoSink = gst_element_factory_make("appsink", nullptr);

    if (!mPipeline || !videoConcat || !mReaderVideoQueue || !mReaderParser || !mReaderFilter || !mReaderVideoSink)
    {
        LOG(error) << mLogPrefix << "ClipReaderProducer (giosrc): failed to create core elements (concat/post chain)" << endl;
        return false;
    }

    // Optional audio branch
    if (mCfg.enable_audio)
    {
        if (!audioConcat)
        {
            LOG(error) << mLogPrefix << "ClipReaderProducer (giosrc): failed to create audio concat" << endl;
            return false;
        }
        mReaderAudioQueue = gst_element_factory_make("queue", nullptr); // post-concat queue
        mReaderAudioSink = gst_element_factory_make("appsink", nullptr);
        if (!mReaderAudioQueue || !mReaderAudioSink)
        {
            LOG(error) << mLogPrefix << "ClipReaderProducer (giosrc): failed to create audio branch" << endl;
            return false;
        }
    }

    // Add elements to bin
    gst_bin_add_many(GST_BIN(mPipeline), videoConcat, mReaderVideoQueue, mReaderParser, mReaderFilter, mReaderVideoSink, nullptr);
    if (mCfg.enable_audio)
    {
        gst_bin_add_many(GST_BIN(mPipeline), audioConcat, mReaderAudioQueue, mReaderAudioSink, nullptr);
    }

    // Configure parser to output consistent byte-stream and headers
    // config-interval=-1 forces SPS/PPS insertion before EVERY IDR
    g_object_set(G_OBJECT(mReaderParser), "config-interval", -1, "disable-passthrough", TRUE, nullptr);

    // Byte-stream caps with AU alignment
    {
        GstCaps* caps = gst_caps_new_simple(iequals(mCfg.video_codec, "h265") ? "video/x-h265" : "video/x-h264",
                                            "stream-format", G_TYPE_STRING, "byte-stream",
                                            "alignment", G_TYPE_STRING, "au",
                                            nullptr);
        g_object_set(G_OBJECT(mReaderFilter), "caps", caps, nullptr);
        gst_caps_unref(caps);
    }

    // appsink configuration
    g_object_set(G_OBJECT(mReaderVideoSink), "emit-signals", TRUE, "sync", FALSE, nullptr);
    g_signal_connect(G_OBJECT(mReaderVideoSink), "new-sample", G_CALLBACK(ClipReaderProducer::onVideoNewSample), this);

    if (mCfg.enable_audio && mReaderAudioSink)
    {
        g_object_set(G_OBJECT(mReaderAudioSink), "emit-signals", TRUE, "sync", FALSE, nullptr);
        g_signal_connect(G_OBJECT(mReaderAudioSink), "new-sample", G_CALLBACK(ClipReaderProducer::onAudioNewSample), this);
    }

    // Link post-concat chains
    if (!gst_element_link_many(videoConcat, mReaderVideoQueue, mReaderParser, mReaderFilter, mReaderVideoSink, nullptr))
    {
        LOG(error) << mLogPrefix << "ClipReaderProducer (giosrc): link video post-concat chain failed" << endl;
        return false;
    }
    if (mCfg.enable_audio)
    {
        if (!gst_element_link_many(audioConcat, mReaderAudioQueue, mReaderAudioSink, nullptr))
        {
            LOG(error) << mLogPrefix << "ClipReaderProducer (giosrc): link audio post-concat chain failed" << endl;
            return false;
        }
    }

    // Build per-file branches into concat
    if (mCfg.file_paths.empty())
    {
        LOG(error) << mLogPrefix << "ClipReaderProducer (giosrc): No file paths provided" << endl;
        return false;
    }

    mGiosrcIsGrowingCurrent = mCfg.is_growing_file;
    mReaderSrc = nullptr; // will be set to first giosrc for compatibility

#if CLIP_READER_GIOSRC_EXTRA_DEBUG
    mGiosrcDebug.reset();
    if (mGiosrcDebugProbes)
    {
        mGiosrcDebug = std::make_unique<GiosrcDebugState>();
        LOG(info) << mLogPrefix
                  << "ClipReaderProducer (giosrc DEBUG): installing pad probes on giosrc src and demux video pads" << endl;
    }
#endif

    for (size_t i = 0; i < mCfg.file_paths.size(); ++i)
    {
        const std::string& filePath = mCfg.file_paths[i];
        const std::string demuxerName = detect_demuxer_for_path(filePath);

        GstElement* src = gst_element_factory_make("giosrc", nullptr);
        GstElement* demux = gst_element_factory_make(demuxerName.c_str(), nullptr);
        GstElement* vq = gst_element_factory_make("queue", nullptr);
        GstElement* aq = nullptr;

        if (mCfg.enable_audio)
        {
            aq = gst_element_factory_make("queue", nullptr);
        }

        if (!src || !demux || !vq || (mCfg.enable_audio && !aq))
        {
            LOG(error) << mLogPrefix << "ClipReaderProducer (giosrc): failed to create per-file elements for " << filePath
                       << " (demuxer=" << demuxerName << ")" << endl;
            return false;
        }

        const std::string fileUri = "file://" + filePath;
        g_object_set(G_OBJECT(src), "location", fileUri.c_str(), nullptr);

        // Only the LAST file in list should be marked is-growing when requested.
        const bool isLastFile = (i == mCfg.file_paths.size() - 1);
        const bool isGrowing = isLastFile && mCfg.is_growing_file;
        g_object_set(G_OBJECT(src), "is-growing", isGrowing ? TRUE : FALSE, nullptr);

#if CLIP_READER_GIOSRC_EXTRA_DEBUG
        if (isGrowing && mGiosrcDebug)
            mGiosrcDebug->growing_file_path = filePath;
#endif

        if (isGrowing && g_signal_lookup("waiting-data", G_OBJECT_TYPE(src)) != 0)
        {
            g_signal_connect(G_OBJECT(src), "waiting-data", G_CALLBACK(ClipReaderProducer::onGiosrcWaitingData), this);
            g_signal_connect(G_OBJECT(src), "done-waiting-data", G_CALLBACK(ClipReaderProducer::onGiosrcDoneWaitingData), this);
            LOG(info) << mLogPrefix << "ClipReaderProducer (giosrc): connected waiting-data/done-waiting-data signals" << endl;
        }

        // Allow caller to attach overlay timestamp metadata probes (applicable for giosrc too).
        // We do this per-file so overlays remain present across concat boundaries.
        if (mCfg.attach_overlay_meta_fn)
        {
            mCfg.attach_overlay_meta_fn((void*)src);
        }

        LOG(info) << mLogPrefix << "ClipReaderProducer (giosrc+concat): file [" << (i + 1) << "/" << mCfg.file_paths.size()
                  << "] " << fileUri << " demuxer=" << demuxerName
                  << " is-growing=" << (isGrowing ? "true" : "false") << endl;

        gst_bin_add_many(GST_BIN(mPipeline), src, demux, vq, nullptr);
        if (mCfg.enable_audio)
        {
            gst_bin_add(GST_BIN(mPipeline), aq);
        }

        if (!gst_element_link(src, demux))
        {
            LOG(error) << mLogPrefix << "ClipReaderProducer (giosrc): Failed to link giosrc to demuxer for " << filePath << endl;
            return false;
        }

#if CLIP_READER_GIOSRC_EXTRA_DEBUG
        if (mGiosrcDebug)
        {
            GstPad* srcpad = gst_element_get_static_pad(src, "src");
            if (srcpad)
            {
                auto* ud = static_cast<GiosrcSrcProbeUserData*>(g_malloc0(sizeof(GiosrcSrcProbeUserData)));
                ud->dbg = mGiosrcDebug.get();
                ud->is_growing = isGrowing ? TRUE : FALSE;
                ud->file_path = g_strdup(filePath.c_str());
                gst_pad_add_probe(srcpad,
                    (GstPadProbeType)(GST_PAD_PROBE_TYPE_BUFFER | GST_PAD_PROBE_TYPE_EVENT_DOWNSTREAM),
                    giosrc_src_debug_probe_cb, ud, giosrc_src_probe_user_data_free);
                gst_object_unref(srcpad);
            }
        }
#endif

        // demux pad-added -> queues (dynamic pads)
#if CLIP_READER_GIOSRC_EXTRA_DEBUG
        link_demuxer_to_queues(demux, vq, aq, mCfg.enable_audio, this, mGiosrcDebug.get());
#else
        link_demuxer_to_queues(demux, vq, aq, mCfg.enable_audio, this);
#endif

        if (!link_queue_to_concat(vq, videoConcat, "video", this))
        {
            return false;
        }
        if (mCfg.enable_audio)
        {
            if (!link_queue_to_concat(aq, audioConcat, "audio", this))
            {
                return false;
            }
        }

        if (i == 0)
        {
            mReaderSrc = src; // keep a handle to the first src for debug/probe compatibility
        }
    }

    // Allow caller to attach overlay timestamp metadata probe if needed
    if (mCfg.attach_overlay_meta_fn)
    {
        mCfg.attach_overlay_meta_fn((void*)mReaderSrc);
    }

    return true;
}

bool ClipReaderProducer::linkElements()
{
    // splitmuxsrc pad-added → queues
    link_splitmuxsrc_to_queues(mReaderSrc, mReaderVideoQueue, mReaderAudioQueue, mCfg.enable_audio, this);

    // video chain: queue → parser → capsfilter → appsink
    if (!gst_element_link_many(mReaderVideoQueue, mReaderParser, mReaderFilter, mReaderVideoSink, nullptr))
    {
        LOG(error) << mLogPrefix << "ClipReaderProducer: link video chain failed" << endl;
        return false;
    }
    // audio: queue → appsink
    if (mCfg.enable_audio)
    {
        if (!gst_element_link(mReaderAudioQueue, mReaderAudioSink))
        {
            LOG(error) << mLogPrefix << "ClipReaderProducer: link audio chain failed" << endl;
            return false;
        }
    }
    return true;
}

bool ClipReaderProducer::applySeek()
{
    // Called from run() after ASYNC_DONE when pipeline is prerolled
    // GST_SEEK_FLAG_FLUSH: Flush pipeline before seeking
    // GST_SEEK_FLAG_KEY_UNIT: Seek to nearest keyframe
    // GST_SEEK_FLAG_SNAP_BEFORE: Snap to keyframe BEFORE (or at) the seek position
    GstSeekFlags seekFlags = (GstSeekFlags)(GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_KEY_UNIT | GST_SEEK_FLAG_SNAP_BEFORE);

    gboolean ok = FALSE;
    GstClockTime seek_start_time = mCfg.seek_start_ms * GST_MSECOND;

    // Apply B-frame reorder depth margin if configured
    if (mCfg.has_bframes && mCfg.seek_start_ms > 0)
    {
        GstClockTime margin = calculateSeekMargin();
        if (margin > 0 && seek_start_time > margin)
        {
            seek_start_time -= margin;

            int64_t margin_ms = margin / GST_MSECOND;
            LOG(info) << mLogPrefix << "B-frame seek: applied " << margin_ms
                     << "ms margin (reorder_depth=" << mCfg.reorder_depth
                     << ", fps=" << mCfg.estimated_framerate << ")" << endl;
        }
        else if (margin > 0)
        {
            LOG(info) << mLogPrefix << "B-frame seek: margin is too small, setting seek_start_time to 0" << endl;
            seek_start_time = 0;
        }
    }

    if (mUsingGiosrc)
    {
        // For giosrc pipeline with growing file, seek on the pipeline
        // Seeking directly on giosrc may not work well for growing files
        /*LOG(info) << "ClipReaderProducer (giosrc): Seeking on pipeline to " << (seek_start_time / GST_MSECOND) << "ms" << endl;
        ok = gst_element_seek(mPipeline, 1.0, GST_FORMAT_TIME, seekFlags,
                              GST_SEEK_TYPE_SET, seek_start_time,
                              GST_SEEK_TYPE_NONE, GST_CLOCK_TIME_NONE);
        LOG(info) << "ClipReaderProducer (giosrc): Seek result: " << (ok ? "SUCCESS" : "FAILED") << endl;
        */
       ok = true;
    }
    else
    {
        // Don't set end time in seek - let splitmuxsrc stream all available data.
        // The EOS logic in handleVideoSample() will stop at the desired end time.
        ok = gst_element_seek(mReaderSrc, 1.0, GST_FORMAT_TIME, seekFlags,
                              GST_SEEK_TYPE_SET, seek_start_time,
                              GST_SEEK_TYPE_NONE, GST_CLOCK_TIME_NONE);

        LOG(info) << mLogPrefix << "ClipReaderProducer: Seek on splitmuxsrc to "
                 << (seek_start_time / GST_MSECOND) << "ms result: " << (ok ? "SUCCESS" : "FAILED") << endl;

        // Fallback: try seeking on pipeline if splitmuxsrc seek failed
        if (!ok && mPipeline)
        {
            LOG(warning) << mLogPrefix << "ClipReaderProducer: splitmuxsrc seek failed, trying pipeline seek as fallback" << endl;
            ok = gst_element_seek(mPipeline, 1.0, GST_FORMAT_TIME, seekFlags,
                                  GST_SEEK_TYPE_SET, seek_start_time,
                                  GST_SEEK_TYPE_NONE, GST_CLOCK_TIME_NONE);
            LOG(info) << mLogPrefix << "ClipReaderProducer: Seek on pipeline to "
                     << (seek_start_time / GST_MSECOND) << "ms result: " << (ok ? "SUCCESS" : "FAILED") << endl;
        }
    }

    if (!ok)
    {
        LOG(error) << mLogPrefix << "ClipReaderProducer: seek failed (start=" << mCfg.seek_start_ms << "ms)" << endl;
    }

    return ok;
}

bool ClipReaderProducer::play()
{
    // Note: This is now only called for consumer-initiated play after pipeline is running
    LOG(info) << mLogPrefix << "ClipReaderProducer: play() called" << endl;
    if (mPipeline && gst_element_set_state(mPipeline, GST_STATE_PLAYING) == GST_STATE_CHANGE_FAILURE)
    {
        LOG(error) << mLogPrefix << "ClipReaderProducer: failed to set PLAYING" << endl;
        return false;
    }
    return true;
}

// Static callback: handle bus messages (like CloudStreamProducer)
gboolean ClipReaderProducer::busWatch(GstBus* /*bus*/, GstMessage* msg, gpointer userData)
{
    ClipReaderProducer* producer = static_cast<ClipReaderProducer*>(userData);
    if (!producer || !producer->mPipeline)
    {
        return TRUE;
    }

    switch (GST_MESSAGE_TYPE(msg))
    {
        case GST_MESSAGE_STATE_CHANGED:
        {
            // Only care about pipeline state changes to PAUSED (for preroll tracking)
            if (GST_MESSAGE_SRC(msg) == GST_OBJECT(producer->mPipeline))
            {
                GstState oldState, newState, pendingState;
                gst_message_parse_state_changed(msg, &oldState, &newState, &pendingState);

                if (newState == GST_STATE_PAUSED && !producer->mPausedStateReached.load())
                {
                    LOG(info) << producer->mLogPrefix << "ClipReaderProducer: Pipeline reached PAUSED state" << endl;
                    producer->mPausedStateReached.store(true);
                }
                if (newState == GST_STATE_PLAYING && !producer->mPlayingStateReached.load())
                {
                    LOG(info) << producer->mLogPrefix << "ClipReaderProducer: Pipeline reached PLAYING state" << endl;
                    producer->mPlayingStateReached.store(true);
                }
            }
            break;
        }

        case GST_MESSAGE_ASYNC_DONE:
        {
            // Pipeline is prerolled and ready for seeking (like CloudStreamProducer)
            if (!producer->mSeekDone.load() && producer->mPausedStateReached.load())
            {
                // Perform seek now that pipeline is prerolled
                if (producer->mCfg.seek_start_ms > 0)
                {
                    producer->applySeek();
                }

                producer->mSeekDone.store(true);

                // Check if consumers are registered before transitioning to PLAYING
                if (producer->getConsumerCount() == 0)
                {
                    LOG(info) << producer->mLogPrefix << "ClipReaderProducer: Waiting for consumers before PLAYING" << endl;
                    break;
                }

                // Transition to PLAYING state and ensure it is set properly
                GstStateChangeReturn playRet = gst_element_set_state(producer->mPipeline, GST_STATE_PLAYING);
                if (playRet == GST_STATE_CHANGE_FAILURE)
                {
                    LOG(error) << producer->mLogPrefix << "ClipReaderProducer: Failed to set PLAYING state" << endl;
                    producer->notifyErrorOnce("Failed to set PLAYING state", 1);
                }
                else if (playRet == GST_STATE_CHANGE_ASYNC)
                {
                    LOG(info) << producer->mLogPrefix << "ClipReaderProducer: PLAYING state change async" << endl;
                }
                else if (playRet == GST_STATE_CHANGE_SUCCESS)
                {
                    producer->mPlayingStateReached.store(true);
                    LOG(info) << producer->mLogPrefix << "ClipReaderProducer: PLAYING state set successfully" << endl;
                }
            }
            break;
        }

        case GST_MESSAGE_ERROR:
        {
            GError* err = nullptr;
            gchar* dbg = nullptr;
            gst_message_parse_error(msg, &err, &dbg);
            gchar* src_name = gst_object_get_path_string(msg->src);
            std::string errorMsg = err ? err->message : "Unknown error";
            LOG(error) << producer->mLogPrefix << "ClipReaderProducer ERROR from " << (src_name ? src_name : "<unknown>")
                      << ": " << errorMsg << endl;
            if (dbg)
            {
                LOG(error) << producer->mLogPrefix << "Debug info: " << dbg << endl;
                g_free(dbg);
            }
            if (err) g_error_free(err);
            if (src_name) g_free(src_name);

            // For giosrc with is-growing=true, "Resource not found" can happen when:
            // - The file was finalized/rotated by the recording system
            // - We've already read all the data we need (EOS was signalled)
            // In these cases, treat it as normal completion, not an error
            bool isResourceNotFound = (errorMsg.find("Resource not found") != std::string::npos ||
                                       errorMsg.find("not available anymore") != std::string::npos);

            if (isResourceNotFound && producer->mUsingGiosrc)
            {
                // If EOS was already signalled, we got all data - this is success
                if (producer->mEosSignalled.load())
                {
                    LOG(info) << producer->mLogPrefix
                              << "ClipReaderProducer (giosrc): Resource not found after EOS signalled - file was finalized, treating as success" << endl;
                    break;  // Ignore error, EOS already handled
                }
            }

            // Check if this is a retryable error (empty/incomplete file from typefind). Skip retry for giosrc.
            bool isRetryableError = (errorMsg.find("Stream contains no data") != std::string::npos ||
                                     errorMsg.find("empty stream") != std::string::npos ||
                                     errorMsg.find("Internal data stream error") != std::string::npos);

            if (!producer->mUsingGiosrc && isRetryableError && producer->postRetryPipeline(true, errorMsg))
            {
                break;  // Retry posted to EventLoop, continue watching
            }

            // Check if stop() was called during retry sleep - don't call callback if pipeline destroyed
            if (!producer->mRunning.load())
            {
                LOG(info) << producer->mLogPrefix << "ClipReaderProducer: Stop was called during retry, skipping error callback" << endl;
                break;
            }

            producer->mRunning.store(false);
            producer->notifyErrorOnce(errorMsg, 1);
            break;
        }

        case GST_MESSAGE_EOS:
        {
            LOG(info) << producer->mLogPrefix << "ClipReaderProducer: EOS received" << endl;

            // Check if this is premature EOS from a growing file (skip retry for giosrc - we use waiting-data timeout instead)
            if (!producer->mUsingGiosrc && !producer->mEosSignalled.load() && producer->mCfg.seek_end_ms != std::numeric_limits<int64_t>::max())
            {
                int64_t end_time = producer->mCfg.seek_end_ms + (FIXED_TS_OFFSET / 1000000);
                int64_t lastPts = producer->mPrevBufMs.load();
                int64_t gap = end_time - lastPts;

                if (gap > producer->mFrameThresholdMs.load())
                {
                    int eosRetry = producer->mPrematureEosRetryCount.fetch_add(1);
                    if (eosRetry >= MAX_EMPTY_STREAM_RETRIES)
                    {
                        LOG(error) << producer->mLogPrefix << "ClipReaderProducer: Premature EOS retry limit ("
                                   << MAX_EMPTY_STREAM_RETRIES << ") exceeded; gap=" << gap
                                   << "ms. Treating as completed." << endl;
                        producer->mRunning.store(false);
                        producer->notifyFinishedOnce();
                        break;
                    }

                    producer->mResumePts.store(producer->mLastSentPts.load());
                    LOG(warning) << producer->mLogPrefix << "ClipReaderProducer: Premature EOS from growing file. "
                                 << "File ended at pts=" << lastPts << "ms, requested end=" << end_time
                                 << "ms, gap=" << gap << "ms, mLastSentPts=" << producer->mLastSentPts.load()
                                 << ", mResumePts=" << producer->mResumePts.load()
                                 << ". Retrying " << (eosRetry + 1) << "/" << MAX_EMPTY_STREAM_RETRIES << "..." << endl;

                    if (producer->postRetryPipeline(false, ""))
                    {
                        break;  // Retry posted to EventLoop, continue watching
                    }

                    // Check if stop() was called during retry sleep - don't call callback if pipeline destroyed
                    if (!producer->mRunning.load())
                    {
                        break;
                    }
                    LOG(warning) << producer->mLogPrefix << "ClipReaderProducer: Retry failed, truncated clip" << endl;
                }
            }

            producer->mRunning.store(false);
            producer->notifyFinishedOnce();
            break;
        }

        default:
            break;
    }

    return TRUE;
}

bool ClipReaderProducer::postRetryPipeline(bool isError, const std::string& errorMsg)
{
    if (mRetryInProgress.exchange(true))
    {
        LOG(info) << mLogPrefix << "ClipReaderProducer: Retry already in progress, skipping" << endl;
        return true;  // Treat as "retry initiated" so caller breaks
    }

    std::shared_ptr<EventLoopData> data(new EventLoopData);
    data->m_taskName = "retryPipeline";
    data->m_inData["isError"] = isError;
    data->m_inData["errorMsg"] = errorMsg;
    // Fire-and-forget: don't set m_expectResult so postMsg returns immediately

    m_recoveryEventLoop->postMsg(data);
    return true;
}

void ClipReaderProducer::process_retry_message(std::shared_ptr<EventLoopData> data, void* parent)
{
    ClipReaderProducer* producer = static_cast<ClipReaderProducer*>(parent);
    if (!producer || !data)
    {
        LOG(error) << "ClipReaderProducer::process_retry_message: null producer or data" << endl;
        return;
    }

    // Clear mRetryInProgress and notify BEFORE any pipeline operations so stop() can proceed
    // without racing with retryPipeline() (avoids stop() tearing down a freshly rebuilt pipeline
    // concurrently with the retry callback).
    producer->mRetryInProgress.store(false);
    producer->mRetrySync.signal();

    bool isError = data->m_inData["isError"].asBool();
    std::string errorMsg = data->m_inData["errorMsg"].asString();

    bool ok = producer->retryPipeline();
    if (!ok)
    {
        if (isError)
        {
            if (producer->mRunning.load())
            {
                producer->notifyErrorOnce(errorMsg, 1);
            }
        }
        else
        {
            if (producer->mRunning.load())
            {
                LOG(warning) << producer->mLogPrefix << "ClipReaderProducer: Retry failed, truncated clip" << endl;
                producer->mRunning.store(false);
                producer->notifyFinishedOnce();
            }
        }
    }
}

bool ClipReaderProducer::retryPipeline()
{
    if (!mRunning.load())
    {
        LOG(info) << mLogPrefix << "ClipReaderProducer: Stop requested, skipping retry" << endl;
        return false;
    }

    int currentRetry = mRetryCount.fetch_add(1);
    if (currentRetry >= MAX_EMPTY_STREAM_RETRIES)
    {
        LOG(error) << mLogPrefix << "ClipReaderProducer: Max retries (" << MAX_EMPTY_STREAM_RETRIES
                   << ") exceeded for empty stream error" << endl;
        return false;
    }

    LOG(info) << mLogPrefix << "ClipReaderProducer: Retry " << (currentRetry + 1) << "/" << MAX_EMPTY_STREAM_RETRIES
              << (mUsingGiosrc ? " (giosrc pipeline)" : " (splitmuxsrc pipeline)") << endl;

    // Stop current pipeline
    // Teardown will set pipeline NULL and unref (bounded wait).
    teardown();

    // Wait before retry - allow more data to be written to the in-progress file
    std::this_thread::sleep_for(std::chrono::seconds(RETRY_DELAY_SECONDS));

    // Check if stop was requested during sleep
    if (!mRunning.load())
    {
        LOG(info) << mLogPrefix << "ClipReaderProducer: Stop requested during retry wait, aborting retry" << endl;
        return false;
    }

    // Reset all state for new attempt
    mEosSignalled.store(false);
    mPausedStateReached.store(false);
    mSeekDone.store(false);
    mPlayingStateReached.store(false);
    mPrevBufMs.store(0);
    mFrameThresholdMs.store(0);
    mGiosrcVideoGopCache.clear();
    clearPostSeekBufferQueue();
    mLastSentPts.store(0);
    mFirstVideoSentPts.store(0);

    // If we have a resume point (from premature EOS), update seek_start_ms
    // to start a few seconds before where we left off (to ensure we get a keyframe).
    // But ensure we don't go below the original start time.
    // The skip logic in handleVideoSample will handle any duplicate frames.
    int64_t resumePts = mResumePts.load();
    if (resumePts > 0)
    {
        // Convert PTS back to original time coordinate (remove FIXED_TS_OFFSET)
        // Progressive seek buffer: start at 5000ms, increase by 500ms per retry, max 10000ms
        /*int64_t resumeTime = resumePts - 1000000;
        int64_t seekBuffer = std::min(5000 + (currentRetry * 500), 10000);
        int64_t newSeekStart = resumeTime - seekBuffer; */

        // start from the original seek_start_ms
        mCfg.seek_start_ms = mOriginalSeekStartMs.load();
        LOG(info) << mLogPrefix << "ClipReaderProducer: Retrying from original seek_start=" << mCfg.seek_start_ms
                    << "ms (will skip frames up to pts=" << resumePts << ")" << endl;
    }

    // Rebuild pipeline
    if (!buildPipeline())
    {
        LOG(error) << mLogPrefix << "ClipReaderProducer: Failed to rebuild pipeline on retry" << endl;
        return false;
    }
    if (!startMainLoop())
    {
        teardown();
        return false;
    }

    // Set pipeline to PAUSED first (like in start())
    // The busWatch() will handle ASYNC_DONE to perform seek and transition to PLAYING
    GstStateChangeReturn ret = gst_element_set_state(mPipeline, GST_STATE_PAUSED);
    if (ret == GST_STATE_CHANGE_FAILURE)
    {
        LOG(error) << mLogPrefix << "ClipReaderProducer: Failed to set PAUSED on retry" << endl;
        return false;
    }

    return true;
}

void ClipReaderProducer::clearPostSeekBufferQueue()
{
    for (auto& entry : mPostSeekBufferQueue)
    {
        if (entry.buffer)
            gst_buffer_unref(entry.buffer);
        if (entry.caps)
            gst_caps_unref(entry.caps);
    }
    mPostSeekBufferQueue.clear();
}

GstClockTime ClipReaderProducer::calculateSeekMargin() const
{
    if (!mCfg.has_bframes || mCfg.estimated_framerate <= 0.0)
    {
        return 0;
    }

    // Calculate frame duration in nanoseconds
    GstClockTime frame_duration = GST_SECOND / mCfg.estimated_framerate;

    // Use reorder depth if available, otherwise use conservative default
    int reorder_frames = (mCfg.reorder_depth > 0) ? mCfg.reorder_depth : MAX_REF_FRAMES;

    // Ensure minimum margin of 500ms for problematic streams
    GstClockTime calculated_margin = reorder_frames * frame_duration;
    GstClockTime minimum_margin = 500 * GST_MSECOND;  // 500ms minimum

    return std::max(calculated_margin, minimum_margin);
}

void ClipReaderProducer::pushPostSeekBuffer(GstBuffer* buffer, GstCaps* caps, int64_t pts_ms, size_t size)
{
    PostSeekQueuedBuffer entry;
    entry.buffer = buffer;
    entry.caps = caps;
    entry.pts = pts_ms;
    entry.size = size;
    mPostSeekBufferQueue.push_back(entry);
}

void ClipReaderProducer::teardown()
{
    mGiosrcVideoGopCache.clear();
    clearPostSeekBufferQueue();

    stopMainLoop();

    if (mPipeline)
    {
        if (mUsingGiosrc)
        {
            set_all_giosrc_is_growing(mPipeline, FALSE);
            gst_element_send_event(mPipeline, gst_event_new_flush_start());
            gst_element_send_event(mPipeline, gst_event_new_flush_stop(TRUE));
        }

        // Original/simple teardown: set NULL, wait briefly, then unref.
        gst_element_set_state(mPipeline, GST_STATE_NULL);
        gst_element_get_state(mPipeline, nullptr, nullptr, GST_SECOND);
        gst_object_unref(mPipeline);
        mPipeline = nullptr;
    }
    mReaderSrc = nullptr;
    mReaderVideoQueue = nullptr;
    mReaderAudioQueue = nullptr;
    mReaderParser = nullptr;
    mReaderFilter = nullptr;
    mReaderVideoSink = nullptr;
    mReaderAudioSink = nullptr;
    mUsingGiosrc = false;
    mGiosrcIsGrowingCurrent = false;
#if CLIP_READER_GIOSRC_EXTRA_DEBUG
    mGiosrcDebug.reset();
#endif
    LOG(info) << mLogPrefix << "ClipReaderProducer::teardown() Exit" << endl;
}

void ClipReaderProducer::attachBusHandlers()
{
    // Bus watch is set up in start() via gst_bus_add_watch()
}

GstFlowReturn ClipReaderProducer::onVideoNewSample(GstAppSink* appsink, gpointer user_data)
{
    ClipReaderProducer* self = static_cast<ClipReaderProducer*>(user_data);
    if (!self) return GST_FLOW_OK;
    GstSample* sample = gst_app_sink_pull_sample(appsink);
    if (!sample) return GST_FLOW_OK;
    GstBuffer* current_buffer = gst_sample_get_buffer(sample);

    // If post-seek queue has buffers and current frame is non-IDR, drain queue in order
    // until we reach the current buffer, then clear the rest and process normally.
    if (current_buffer && !self->mPostSeekBufferQueue.empty())
    {
        LOG(info) << "ClipReaderProducer: draining post-seek queue" << endl;
        while (!self->mPostSeekBufferQueue.empty())
        {
            ClipReaderProducer::PostSeekQueuedBuffer& entry = self->mPostSeekBufferQueue.front();
            const bool is_current_buffer = (entry.buffer == current_buffer);

            if (!is_current_buffer)
            {
                GstCaps* caps = entry.caps ? gst_caps_ref(entry.caps) : nullptr;
                GstSample* queued_sample = gst_sample_new(entry.buffer, caps, nullptr, nullptr);
                if (caps) gst_caps_unref(caps);
                if (queued_sample)
                {
                    (void)self->handleVideoSample(queued_sample);
                    gst_sample_unref(queued_sample);
                }
                gst_buffer_unref(entry.buffer);
                if (entry.caps) gst_caps_unref(entry.caps);
            }
            else
            {
                gst_buffer_unref(entry.buffer);
                if (entry.caps) gst_caps_unref(entry.caps);
            }
            self->mPostSeekBufferQueue.pop_front();

            if (is_current_buffer)
            {
                LOG(info) << "ClipReaderProducer: found current buffer in post-seek queue" << endl;
                break;
            }
        }
        LOG(info) << "ClipReaderProducer: clearing post-seek queue" << endl;
        self->clearPostSeekBufferQueue();
    }

    GstFlowReturn ret = self->handleVideoSample(sample);
    gst_sample_unref(sample);
    return ret;
}

GstFlowReturn ClipReaderProducer::onAudioNewSample(GstAppSink* appsink, gpointer user_data)
{
    ClipReaderProducer* self = static_cast<ClipReaderProducer*>(user_data);
    if (!self) return GST_FLOW_OK;
    GstSample* sample = gst_app_sink_pull_sample(appsink);
    if (!sample) return GST_FLOW_OK;
    self->handleAudioSample(sample);
    gst_sample_unref(sample);
    return GST_FLOW_OK;
}

GstFlowReturn ClipReaderProducer::handleVideoSample(GstSample* sample)
{
    // Keep splitmuxsrc behavior isolated from giosrc complexity.
    if (mUsingGiosrc)
    {
        return handleVideoSampleGiosrc(sample);
    }
    return handleVideoSampleSplitmux(sample);
}

GstFlowReturn ClipReaderProducer::handleVideoSampleSplitmux(GstSample* sample)
{
    GstBuffer* buffer = gst_sample_get_buffer(sample);
    if (!buffer) return GST_FLOW_OK;

    // Early exit if EOS already signalled - don't process any more buffers
    if (mEosSignalled.load()) return GST_FLOW_OK;

    auto frame = std::make_shared<RawFrameParams>();
    gst_sample_ref(sample);
    frame->m_sample = sample;
    frame->m_gstBuffer = gst_sample_get_buffer(sample);
    if (frame->m_gstBuffer && gst_buffer_map(frame->m_gstBuffer, &frame->m_map, GST_MAP_READ))
    {
        // map ok; will be unmapped by RawFrameParams dtor
    }
    frame->pts = GST_BUFFER_PTS_IS_VALID(buffer) ? (GST_BUFFER_PTS(buffer)/1000000) : -1;

    // Skip preroll frames until seek completes.
    if (!mSeekDone.load())
    {
        return GST_FLOW_OK;
    }

    // Skip frames we've already sent (when resuming after retry for growing file)
    int64_t resumePts = mResumePts.load();
    if (resumePts > 0 && frame->pts <= resumePts)
    {
        // Still update mPrevBufMs so we know the file has data (for premature EOS detection)
        // This helps distinguish "file has no new data" from "file has data but we're skipping duplicates"
        if (frame->pts >= 0)
        {
            mPrevBufMs.store(frame->pts);
        }
        LOG(verbose) << mLogPrefix << "ClipReaderProducer: Skipping already-sent frame pts=" << frame->pts
                     << " (resume from " << resumePts << ")" << endl;
        return GST_FLOW_OK;
    }

    // Reset retry count only when we process a NEW frame (not skipped)
    mRetryCount = 0;

    // End-time tracking similar to original logic
    if (mCfg.seek_end_ms != std::numeric_limits<int64_t>::max())
    {
        gint64 pts_ms = frame->pts;
        int64_t prevBufMs = mPrevBufMs.load();
        int64_t frameThresholdMs = mFrameThresholdMs.load();
        if (prevBufMs != 0 && pts_ms >= 0)
        {
            const int64_t delta_ms = pts_ms - prevBufMs;
            if (delta_ms > 0 && delta_ms > frameThresholdMs)
            {
                frameThresholdMs = delta_ms;
                mFrameThresholdMs.store(frameThresholdMs);
                LOG(info) << mLogPrefix << "ClipReaderProducer: frameThresholdMs=" << frameThresholdMs << endl;
            }
        }
        mPrevBufMs.store(pts_ms);
        gint64 end_time = mCfg.seek_end_ms + (FIXED_TS_OFFSET/1000000);
        // EOS only when we actually reach or pass the requested end.

        bool eos_detected = (pts_ms >= 0 && pts_ms >= end_time);

        if (eos_detected)
        {
            frame->m_eos = true;
            // Send this last frame to consumer first, then signal EOS
            if (!mEosSignalled.exchange(true))
            {
                // Distribute the last frame to consumer before EOS
                if (mRunning.load())
                {
                    std::lock_guard<std::mutex> lk(mConsumersMtx);
                    auto it = mConsumers.find("video");
                    if (it != mConsumers.end() && it->second)
                    {
                        mLastSentPts.store(frame->pts);
                        // Convert to absolute epoch timestamp if file_start_epoch_ms is set
                        if (mCfg.is_image_capture && mCfg.file_start_epoch_ms > 0 && frame->pts >= 1000000) {
                            frame->pts = mCfg.file_start_epoch_ms + (frame->pts - 1000000);
                        }
                        it->second->onFrame(frame);
                    }
                }
                mFinishedNotified = true;
            }
            // Stop processing any further frames after EOS
            return GST_FLOW_EOS;
        }
    }

    // Only distribute if still running (prevents pushing after stop)
    if (!mRunning.load()) return GST_FLOW_OK;

    // Log first frame only
    if (mLastSentPts.load() == 0)
    {
        LOG(warning) << mLogPrefix << "ClipReaderProducer: First frame pts=" << frame->pts + mCfg.file_start_epoch_ms
                  << "ms, size=" << frame->m_map.size << endl;
    }

    std::lock_guard<std::mutex> lk(mConsumersMtx);
    auto it = mConsumers.find("video");
    if (it != mConsumers.end() && it->second)
    {
        if (mFirstVideoSentPts.load() == 0) mFirstVideoSentPts.store(frame->pts);
        mLastSentPts.store(frame->pts);
        // Convert to absolute epoch timestamp if file_start_epoch_ms is set
        if (mCfg.is_image_capture && mCfg.file_start_epoch_ms > 0 && frame->pts >= 1000000) {
            frame->pts = mCfg.file_start_epoch_ms + (frame->pts - 1000000);
        }
        LOG(verbose) << mLogPrefix << "ClipReaderProducer: sending frame pts=" << frame->pts << ", size=" << frame->m_map.size << endl;
        it->second->onFrame(frame);
    }
    return GST_FLOW_OK;
}

GstFlowReturn ClipReaderProducer::handleVideoSampleGiosrc(GstSample* sample)
{
    GstBuffer* buffer = gst_sample_get_buffer(sample);
    if (!buffer) return GST_FLOW_OK;

    // Early exit if EOS already signalled - don't process any more buffers
    if (mEosSignalled.load()) return GST_FLOW_OK;

    auto frame = std::make_shared<RawFrameParams>();
    gst_sample_ref(sample);
    frame->m_sample = sample;
    frame->m_gstBuffer = gst_sample_get_buffer(sample);
    if (frame->m_gstBuffer && gst_buffer_map(frame->m_gstBuffer, &frame->m_map, GST_MAP_READ))
    {
        // map ok; will be unmapped by RawFrameParams dtor
    }

    // Propagate caps from the sample so downstream consumers (e.g. RemuxWriter's
    // video appsrc) know the actual stream format. Matroska stores H.264/H.265
    // with stream-format=avc/hvc1 (length-prefixed NALs); without this,
    // downstream h264parse may misinterpret buffers as byte-stream and lose
    // PTS, which causes mp4mux to fail with "Buffer has no PTS".
    GstCaps* sample_caps = gst_sample_get_caps(sample);
    if (sample_caps)
    {
        frame->m_caps = gst_caps_ref(sample_caps);
    }

    frame->pts = GST_BUFFER_PTS_IS_VALID(buffer) ? (GST_BUFFER_PTS(buffer)/1000000) : -1;

    // Defensive guard: drop "non-VCL only" buffers — i.e. buffers that
    // contain zero slice NALs. These are standalone config/metadata bundles
    // (AUD + Prefix / AUD + SEI / SPS + PPS / ...) that h264parse sometimes
    // emits as a separate buffer sharing the same PTS as the following slice
    // at concat / IDR / config-insertion boundaries.
    //
    // Without this drop, two same-PTS buffers reach mp4mux, which then
    // reframes the stream and loses PTS on one of them ("Buffer has no PTS.
    // Could not multiplex stream."). The drop is lossless because:
    //   (a) the actual slice buffer arrives next with the same PTS, and
    //   (b) the remux pipeline's h264parse has config-interval=-1 and
    //       re-inserts SPS/PPS from the codec_data caps before every IDR.
    //
    // This is strictly more accurate than a size-based threshold because it
    // correctly keeps any genuinely tiny but valid slice and correctly drops
    // any oversized but slice-less config bundle.
    // Detect codec (h264/h265) and incoming buffer format (avc/hvc1 vs
    // byte-stream) for the VCL check below. When caps don't declare
    // stream-format, we default to byte-stream which matches how the
    // reader pipeline's capsfilter normalises the output (see
    // buildGiosrcPipeline).
    const bool is_h265 = iequals(mCfg.video_codec, "h265");
    bool is_avc_format = false;
    if (sample_caps)
    {
        const GstStructure* s = gst_caps_get_structure(sample_caps, 0);
        const gchar* sf = s ? gst_structure_get_string(s, "stream-format") : nullptr;
        if (sf)
        {
            is_avc_format = (g_strcmp0(sf, "avc") == 0 || g_strcmp0(sf, "hvc1") == 0 ||
                             g_strcmp0(sf, "hev1") == 0);
        }
    }

    if (frame->m_map.data && frame->m_map.size > 0
        && !hasVclNalInBuffer(frame->m_map.data, frame->m_map.size, is_h265, is_avc_format))
    {
        LOG(warning) << mLogPrefix
                     << "ClipReaderProducer (giosrc): dropping non-VCL-only buffer (size="
                     << frame->m_map.size << " bytes, pts_ms=" << frame->pts
                     << "). No slice NAL present; the actual frame is carried by a"
                     << " subsequent buffer (often at the same PTS)." << endl;
        // Note: no manual cleanup is needed here. `frame` is the only
        // std::shared_ptr<RawFrameParams> referencing this data; when it
        // goes out of scope at the return below, ~RawFrameParams()
        // automatically gst_buffer_unmap's m_map, gst_sample_unref's the
        // sample, and gst_caps_unref's m_caps.
        return GST_FLOW_OK;
    }
    // Skip preroll frames until seek completes.
    if (!mSeekDone.load())
    {
        return GST_FLOW_OK;
    }

    bool is_epoch_timestamp = (frame->pts >= 946684800000); // Jan 1, 2000 in epoch ms

    // When using giosrc, the PTS is an absolute epoch timestamp in milliseconds.
    // Convert it to the same format as splitmuxsrc: FIXED_TS_OFFSET (1000000 ms) + relative_position
    if (mUsingGiosrc && is_epoch_timestamp
        && mCfg.file_start_epoch_ms > 0
        && frame->pts >= mCfg.file_start_epoch_ms)
    {
        // Convert absolute epoch timestamp to relative position, then add FIXED_TS_OFFSET
        int64_t relative_ms = frame->pts - mCfg.file_start_epoch_ms;
        frame->pts = (FIXED_TS_OFFSET / 1000000) + relative_ms;  // 1000000 + relative_ms

        // Also update the GstBuffer's PTS so consumers reading from buffer get correct value
        // Create a deep copy to avoid ownership issues with the sample's buffer
        if (frame->m_gstBuffer && frame->m_map.data)
        {
            gst_buffer_unmap(frame->m_gstBuffer, &frame->m_map);

            // Create independent *buffer* copy (shallow). We only adjust timestamps; payload is shared.
            // This avoids huge allocations/fragmentation from deep-copying H264 AU payloads.
            // (sample keeps its original buffer)
            GstBuffer* new_buffer = gst_buffer_copy(frame->m_gstBuffer);
            if (new_buffer)
            {
                // CRITICAL: Preserve original DTS for B-frame streams
                GstClockTime original_dts = GST_BUFFER_DTS(frame->m_gstBuffer);

                GST_BUFFER_PTS(new_buffer) = frame->pts * GST_MSECOND;

                // Only set DTS = PTS for streams without B-frames
                if (mCfg.has_bframes && GST_CLOCK_TIME_IS_VALID(original_dts))
                {
                    // For B-frame streams, preserve the original DTS to maintain decode order
                    GST_BUFFER_DTS(new_buffer) = original_dts;
                }
                else
                {
                    // For I/P-only streams, DTS = PTS is correct
                    GST_BUFFER_DTS(new_buffer) = GST_BUFFER_PTS(new_buffer);
                }

                // Store the new buffer - we own it now
                frame->m_gstBuffer = new_buffer;
                frame->m_owns_gstBuffer = true;
                gst_buffer_map(frame->m_gstBuffer, &frame->m_map, GST_MAP_READ);
            }
            else
            {
                // Fallback: just re-map original if copy failed
                gst_buffer_map(frame->m_gstBuffer, &frame->m_map, GST_MAP_READ);
            }
        }
    }

    if (frame->pts >= 0)
    {
        mLastAppsinkPtsMs.store(frame->pts);
    }

#ifdef GIOSRC_FRAME_DEBUG
    auto timeNow = std::chrono::system_clock::now();
    int64_t timeNowMs = std::chrono::duration_cast<std::chrono::milliseconds>(timeNow.time_since_epoch()).count();
    int64_t frameDiffMs = (mPrevSysTimeMs > 0) ? (timeNowMs - mPrevSysTimeMs) : 0;
    int64_t latencyMs = (frame->pts >= 0 && is_epoch_timestamp) ? (timeNowMs - frame->pts) : -1;
    mPrevSysTimeMs = timeNowMs;
    LOG(warning) << mLogPrefix << "ClipReaderProducer (giosrc): frame->pts=" << convertEpocToISO8601_2((GST_BUFFER_PTS(buffer)/1000))
                 << ", pts_ms=" << frame->pts
                 << ", sysDiffMs=" << frameDiffMs
                 << ", latencyMs=" << latencyMs << endl;
#endif

    // For giosrc, we want to start from the exact keyframe (I/IDR) before the requested start time.
    // Instead of hardcoding a time window (e.g. 5s), we cache the current GOP from the last keyframe
    // and flush it once we reach the requested start PTS. This avoids decoding/sending unnecessary data.
    if (mCfg.seek_start_ms > 0 && mLastSentPts.load() == 0)
    {
        bool should_return = false;
        GstFlowReturn flow = maybeFlushGiosrcGopCacheAndSend(frame, buffer, &should_return);
        if (should_return)
        {
            return flow; // either waiting for start GOP, flushed GOP, or EOS
        }
    }

    // Skip frames we've already sent (when resuming after retry for growing file)
    int64_t resumePts = mResumePts.load();
    if (resumePts > 0 && frame->pts <= resumePts)
    {
        // Still update mPrevBufMs so we know the file has data (for premature EOS detection)
        // This helps distinguish "file has no new data" from "file has data but we're skipping duplicates"
        if (frame->pts >= 0)
        {
            mPrevBufMs.store(frame->pts);
        }
        LOG(verbose) << mLogPrefix << "ClipReaderProducer: Skipping already-sent frame pts=" << frame->pts
                     << " (resume from " << resumePts << ")" << endl;
        return GST_FLOW_OK;
    }

    // Reset retry count only when we process a NEW frame (not skipped)
    mRetryCount = 0;

    // End-time tracking similar to original logic
    if (mCfg.seek_end_ms != std::numeric_limits<int64_t>::max())
    {
        gint64 pts_ms = frame->pts;
        int64_t prevBufMs = mPrevBufMs.load();
        int64_t frameThresholdMs = mFrameThresholdMs.load();
        if (prevBufMs != 0 && pts_ms >= 0)
        {
            const int64_t delta_ms = pts_ms - prevBufMs;
            if (delta_ms > 0 && delta_ms > frameThresholdMs)
            {
                frameThresholdMs = delta_ms;
                mFrameThresholdMs.store(frameThresholdMs);
                LOG(info) << mLogPrefix << "ClipReaderProducer (giosrc): frameThresholdMs=" << frameThresholdMs << endl;
            }
        }
        mPrevBufMs.store(pts_ms);
        gint64 end_time = mCfg.seek_end_ms + (FIXED_TS_OFFSET/1000000);

        bool eos_detected = (pts_ms >= 0 && pts_ms >= end_time);

        if (eos_detected)
        {
            frame->m_eos = true;
            // Send this last frame to consumer first, then signal EOS
            if (!mEosSignalled.exchange(true))
            {
                LOG(warning) << mLogPrefix << "ClipReaderProducer: sending EOS at pts = " << frame->pts << endl << endl;

                // Distribute the last frame to consumer before EOS
                if (mRunning.load())
                {
                    std::lock_guard<std::mutex> lk(mConsumersMtx);
                    auto it = mConsumers.find("video");
                    if (it != mConsumers.end() && it->second)
                    {
                        mLastSentPts.store(frame->pts);
                        // Convert to absolute epoch timestamp if file_start_epoch_ms is set
                        if (mCfg.is_image_capture && mCfg.file_start_epoch_ms > 0 && frame->pts >= 1000000) {
                            frame->pts = mCfg.file_start_epoch_ms + (frame->pts - 1000000);
                        }
                        it->second->onFrame(frame);
                    }
                }
                mFinishedNotified = true;
            }
            // Stop processing any further frames after EOS
            return GST_FLOW_EOS;
        }
    }

    // Only distribute if still running (prevents pushing after stop)
    if (!mRunning.load()) return GST_FLOW_OK;

    // Log first frame only
    if (mLastSentPts.load() == 0)
    {
        LOG(warning) << mLogPrefix << "ClipReaderProducer: First frame pts=" << frame->pts + mCfg.file_start_epoch_ms
                  << "ms, size=" << frame->m_map.size << endl;
    }

    std::lock_guard<std::mutex> lk(mConsumersMtx);
    auto it = mConsumers.find("video");
    if (it != mConsumers.end() && it->second)
    {
        if (mFirstVideoSentPts.load() == 0) mFirstVideoSentPts.store(frame->pts);
        mLastSentPts.store(frame->pts);
        // Convert to absolute epoch timestamp if file_start_epoch_ms is set
        if (mCfg.is_image_capture && mCfg.file_start_epoch_ms > 0 && frame->pts >= 1000000) {
            frame->pts = mCfg.file_start_epoch_ms + (frame->pts - 1000000);
        }
        LOG(verbose) << mLogPrefix << "ClipReaderProducer: sending frame pts=" << frame->pts << ", size=" << frame->m_map.size << endl;
        it->second->onFrame(frame);
    }
    return GST_FLOW_OK;
}

GstFlowReturn ClipReaderProducer::maybeFlushGiosrcGopCacheAndSend(std::shared_ptr<RawFrameParams>& frame,
                                                                  GstBuffer*& buffer,
                                                                  bool* should_return)
{
    if (should_return) *should_return = true;
    const int64_t target_start_pts = (FIXED_TS_OFFSET / 1000000) + mCfg.seek_start_ms;
    const bool is_keyframe = !GST_BUFFER_FLAG_IS_SET(buffer, GST_BUFFER_FLAG_DELTA_UNIT);

    if (is_keyframe)
    {
        // New GOP starts here; drop any previously cached partial GOP.
        mGiosrcVideoGopCache.clear();
    }
    else if (mGiosrcVideoGopCache.empty())
    {
        // We haven't seen a keyframe yet; can't form a decodable GOP start.
        return GST_FLOW_OK; // caller should return (keep waiting)
    }

    mGiosrcVideoGopCache.push_back(frame);

    if (mGiosrcVideoGopCache.size() > MAX_GOP_CACHE_FRAMES)
    {
        // Safety: extremely large GOP (or missing keyframes). Drop and wait for next keyframe.
        LOG(warning) << mLogPrefix << "ClipReaderProducer (giosrc): GOP cache exceeded " << MAX_GOP_CACHE_FRAMES
                     << " frames; dropping cache and waiting for next keyframe" << endl;
        mGiosrcVideoGopCache.clear();
        return GST_FLOW_OK;
    }

    if (frame->pts < target_start_pts)
    {
        // Still before requested start time: keep caching this GOP.
        return GST_FLOW_OK;
    }

    // We've reached the requested start time; flush cached GOP starting at the exact keyframe.
    if (mGiosrcVideoGopCache.empty())
    {
        if (should_return) *should_return = false;
        return GST_FLOW_OK;
    }

    int64_t gop_start_pts = mGiosrcVideoGopCache.front()->pts;
    LOG(info) << mLogPrefix << "ClipReaderProducer (giosrc): Starting from GOP keyframe at pts=" << gop_start_pts
              << "ms (target=" << target_start_pts << "ms, cached=" << mGiosrcVideoGopCache.size() << " frames)" << endl;

    // Flush frames in-order using the same send path below. We rely on the fact that
    // resumePts is 0 on first start; end-time enforcement will still work.
    auto cached = std::move(mGiosrcVideoGopCache);
    mGiosrcVideoGopCache.clear();

    for (auto& f : cached)
    {
        frame = f;
        buffer = frame->m_gstBuffer;

        // Skip frames we've already sent (when resuming after retry for growing file)
        int64_t resumePts = mResumePts.load();
        if (resumePts > 0 && frame->pts <= resumePts)
        {
            if (frame->pts >= 0)
            {
                mPrevBufMs.store(frame->pts);
            }
            continue;
        }

        // Reset retry count only when we process a NEW frame (not skipped)
        mRetryCount = 0;

        // End-time tracking similar to original logic
        if (mCfg.seek_end_ms != std::numeric_limits<int64_t>::max())
        {
            gint64 pts_ms = frame->pts;
            int64_t prevBufMs = mPrevBufMs.load();
            int64_t frameThresholdMs = mFrameThresholdMs.load();
            if (frameThresholdMs == 0 && prevBufMs != 0 && pts_ms >= 0)
            {
                frameThresholdMs = pts_ms - prevBufMs;
                mFrameThresholdMs.store(frameThresholdMs);
            }
            mPrevBufMs.store(pts_ms);
            gint64 end_time = mCfg.seek_end_ms + (FIXED_TS_OFFSET/1000000);

            // For B-frame videos: use precise EOS without margin to avoid cutting duration short
            // For non-B-frame videos: no need for margin in current logic
            bool eos_detected = (pts_ms >= 0 && pts_ms >= end_time);

            if (eos_detected)
            {
                if (!mEosSignalled.exchange(true))
                {
                    if (mRunning.load())
                    {
                        std::lock_guard<std::mutex> lk(mConsumersMtx);
                        auto it = mConsumers.find("video");
                        if (it != mConsumers.end() && it->second)
                        {
                            mLastSentPts.store(frame->pts);
                            if (mCfg.is_image_capture && mCfg.file_start_epoch_ms > 0 && frame->pts >= 1000000) {
                                frame->pts = mCfg.file_start_epoch_ms + (frame->pts - 1000000);
                            }
                            it->second->onFrame(frame);
                        }
                    }
                    LOG(warning) << mLogPrefix << "ClipReaderProducer: sending EOS at pts = " << frame->pts << endl << endl;
                }
                return GST_FLOW_EOS; // stop processing any further frames after EOS
            }
        }

        if (!mRunning.load()) return GST_FLOW_OK;

        if (mLastSentPts.load() == 0)
        {
            LOG(warning) << mLogPrefix << "ClipReaderProducer: First frame pts=" << frame->pts + mCfg.file_start_epoch_ms
                      << "ms, size=" << frame->m_map.size << endl;
        }

        std::lock_guard<std::mutex> lk(mConsumersMtx);
        auto it = mConsumers.find("video");
        if (it != mConsumers.end() && it->second)
        {
            if (mFirstVideoSentPts.load() == 0) mFirstVideoSentPts.store(frame->pts);
            mLastSentPts.store(frame->pts);
            if (mCfg.is_image_capture && mCfg.file_start_epoch_ms > 0 && frame->pts >= 1000000) {
                frame->pts = mCfg.file_start_epoch_ms + (frame->pts - 1000000);
            }
            LOG(verbose) << mLogPrefix << "ClipReaderProducer: sending frame pts=" << frame->pts << ", size=" << frame->m_map.size << endl;
            it->second->onFrame(frame);
        }
    }

    return GST_FLOW_OK; // flushed GOP, caller should return (don't double-send current sample)
}

void ClipReaderProducer::handleAudioSample(GstSample* sample)
{
    // Forward audio frames to a consumer registered as "audio" if present
    GstBuffer* buffer = gst_sample_get_buffer(sample);
    if (!buffer) return;

    // Early exit if EOS already signalled - don't process any more buffers
    if (mEosSignalled.load()) return;

    auto frame = std::make_shared<RawFrameParams>();
    gst_sample_ref(sample);
    frame->m_sample = sample;
    frame->m_gstBuffer = gst_sample_get_buffer(sample);
    if (frame->m_gstBuffer && gst_buffer_map(frame->m_gstBuffer, &frame->m_map, GST_MAP_READ))
    {
    }

    frame->pts = GST_BUFFER_PTS_IS_VALID(buffer) ? (GST_BUFFER_PTS(buffer)/1000000) : -1;

    bool is_epoch_timestamp = (frame->pts >= 946684800000); // Jan 1, 2000 in epoch ms

    // When using giosrc, the PTS is an absolute epoch timestamp in milliseconds.
    // Convert it to the same format as splitmuxsrc: FIXED_TS_OFFSET (1000000 ms) + relative_position
    if (mUsingGiosrc && is_epoch_timestamp
        && mCfg.file_start_epoch_ms > 0
        && frame->pts >= mCfg.file_start_epoch_ms)
    {
        // Convert absolute epoch timestamp to relative position, then add FIXED_TS_OFFSET
        int64_t relative_ms = frame->pts - mCfg.file_start_epoch_ms;
        frame->pts = (FIXED_TS_OFFSET / 1000000) + relative_ms;  // 1000000 + relative_ms

        // Create a deep copy to avoid ownership issues with the sample's buffer
        if (frame->m_gstBuffer && frame->m_map.data)
        {
            gst_buffer_unmap(frame->m_gstBuffer, &frame->m_map);

            // Create independent *buffer* copy (shallow). We only adjust timestamps; payload is shared.
            // (sample keeps its original buffer)
            GstBuffer* new_buffer = gst_buffer_copy(frame->m_gstBuffer);
            if (new_buffer)
            {
                // CRITICAL: Preserve original DTS for B-frame streams
                GstClockTime original_dts = GST_BUFFER_DTS(frame->m_gstBuffer);

                GST_BUFFER_PTS(new_buffer) = frame->pts * GST_MSECOND;

                // Only set DTS = PTS for streams without B-frames
                if (mCfg.has_bframes && GST_CLOCK_TIME_IS_VALID(original_dts))
                {
                    // For B-frame streams, preserve the original DTS to maintain decode order
                    GST_BUFFER_DTS(new_buffer) = original_dts;
                }
                else
                {
                    // For I/P-only streams, DTS = PTS is correct
                    GST_BUFFER_DTS(new_buffer) = GST_BUFFER_PTS(new_buffer);
                }

                // Store the new buffer - we own it now
                frame->m_gstBuffer = new_buffer;
                frame->m_owns_gstBuffer = true;
                gst_buffer_map(frame->m_gstBuffer, &frame->m_map, GST_MAP_READ);
            }
            else
            {
                // Fallback: just re-map original if copy failed
                gst_buffer_map(frame->m_gstBuffer, &frame->m_map, GST_MAP_READ);
            }
        }
    }

    // For giosrc, seeking may not work reliably on growing files.
    // Skip audio frames until video has started (to maintain A/V sync).
    if (mUsingGiosrc && mCfg.seek_start_ms > 0)
    {
        int64_t videoStartPts = mFirstVideoSentPts.load();
        if (videoStartPts == 0)
        {
            // Video hasn't started yet, skip audio
            return;
        }

        // Skip audio frames that are before the video start
        if (frame->pts < videoStartPts)
        {
            return;
        }
    }

    // Skip audio frames we've already sent (when resuming after retry for growing file)
    int64_t resumePtsAudio = mResumePts.load();
    if (resumePtsAudio > 0 && frame->pts <= resumePtsAudio)
    {
        return;  // Skip duplicate audio
    }

    // End-time enforcement for audio (same as video)
    if (mCfg.seek_end_ms != std::numeric_limits<int64_t>::max())
    {
        gint64 pts_ms = frame->pts;
        gint64 end_time = mCfg.seek_end_ms + (FIXED_TS_OFFSET/1000000);
        if (pts_ms >= 0 && pts_ms >= end_time)
        {
            return;  // Drop audio past end time
        }
    }

    // Propagate caps for audio (critical for decodebin typefinding)
    GstCaps* caps = gst_sample_get_caps(sample);
    if (caps)
    {
        frame->m_caps = gst_caps_ref(caps);
    }

    // Only distribute if still running (prevents pushing after stop)
    if (!mRunning.load()) return;

    std::lock_guard<std::mutex> lk(mConsumersMtx);
    auto it = mConsumers.find("audio");
    if (it != mConsumers.end() && it->second)
    {
        // Convert to absolute epoch timestamp if file_start_epoch_ms is set
        if (mCfg.file_start_epoch_ms > 0 && frame->pts >= 1000000) {
            frame->pts = mCfg.file_start_epoch_ms + (frame->pts - 1000000);
        }
        it->second->onFrame(frame);
    }
}

void ClipReaderProducer::distributeToConsumers(std::shared_ptr<RawFrameParams> frameData)
{
    // Not used directly; we route via "video"/"audio" keyed consumers
}

void ClipReaderProducer::distributeToConsumers(FrameParams& frameParams)
{
    // Not used in this producer
}


