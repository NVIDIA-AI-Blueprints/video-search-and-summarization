/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "dash_packager_consumer.h"

#include "logger.h"
#include "video_resolution.h"
#include "config.h"

#include <gst/app/gstappsrc.h>
#include "api/video/i420_buffer.h"
#include <unistd.h>
#include <dirent.h>
#include <climits>
#include <cstdlib>

#include <algorithm>
#include <cmath>
#include <cctype>
#include <cstring>
#include <iterator>
#include <system_error>
#include <vector>

namespace {

constexpr guint64 MAX_APP_SRC_BYTES = 4U * 1024U * 1024U;

GstClockTime toGstTime(const FrameParams& params)
{
    if (params.m_presentationTime.tv_sec < 0 || params.m_presentationTime.tv_usec < 0)
    {
        return GST_CLOCK_TIME_NONE;
    }
    return static_cast<GstClockTime>(params.m_presentationTime.tv_sec) * GST_SECOND
           + static_cast<GstClockTime>(params.m_presentationTime.tv_usec) * GST_USECOND;
}

bool hasProperty(GstElement* element, const char* property)
{
    return element != nullptr
           && g_object_class_find_property(G_OBJECT_GET_CLASS(element), property) != nullptr;
}

bool dashSinkSupportsDashMp4()
{
    GstElement* dashSink = gst_element_factory_make("dashsink", nullptr);
    if (dashSink == nullptr)
    {
        return false;
    }

    const GParamSpec* muxerSpec =
        g_object_class_find_property(G_OBJECT_GET_CLASS(dashSink), "muxer");
    bool supported = false;
    if (muxerSpec != nullptr && G_IS_PARAM_SPEC_ENUM(muxerSpec))
    {
        GEnumClass* enumClass =
            G_ENUM_CLASS(g_type_class_ref(G_PARAM_SPEC_VALUE_TYPE(muxerSpec)));
        supported = enumClass != nullptr
                    && g_enum_get_value_by_nick(enumClass, "dashmp4") != nullptr;
        if (enumClass != nullptr)
        {
            g_type_class_unref(enumClass);
        }
    }
    gst_object_unref(dashSink);
    return supported;
}

bool linkToDashPad(GstElement* source, GstElement* dashSink, const char* padTemplate)
{
    GstPad* sourcePad = gst_element_get_static_pad(source, "src");
    GstPad* sinkPad = gst_element_request_pad_simple(dashSink, padTemplate);
    if (sourcePad == nullptr || sinkPad == nullptr)
    {
        if (sourcePad != nullptr)
        {
            gst_object_unref(sourcePad);
        }
        if (sinkPad != nullptr)
        {
            gst_object_unref(sinkPad);
        }
        return false;
    }
    const bool linked = gst_pad_link(sourcePad, sinkPad) == GST_PAD_LINK_OK;
    gst_object_unref(sourcePad);
    gst_object_unref(sinkPad);
    return linked;
}

GstBuffer* makeAacAudioSpecificConfig(unsigned sampleRate, unsigned channels)
{
    static constexpr unsigned sampleRates[] = {
        96000, 88200, 64000, 48000, 44100, 32000, 24000,
        22050, 16000, 12000, 11025, 8000, 7350
    };
    unsigned frequencyIndex = 3;
    for (unsigned index = 0; index < std::size(sampleRates); ++index)
    {
        if (sampleRates[index] == sampleRate)
        {
            frequencyIndex = index;
            break;
        }
    }
    const unsigned channelConfig = std::clamp(channels, 1U, 7U);
    const uint16_t config = static_cast<uint16_t>((2U << 11U) | (frequencyIndex << 7U)
                                                   | (channelConfig << 3U));
    const uint8_t bytes[] = {
        static_cast<uint8_t>(config >> 8U),
        static_cast<uint8_t>(config & 0xffU)
    };
    GstBuffer* buffer = gst_buffer_new_allocate(nullptr, sizeof(bytes), nullptr);
    if (buffer != nullptr)
    {
        gst_buffer_fill(buffer, 0, bytes, sizeof(bytes));
    }
    return buffer;
}

} // namespace

DashPackagerConsumer::DashPackagerConsumer(DashPackagerConfig config)
    : IMediaDataConsumer("dash_packager_" + config.streamToken)
    , m_config(std::move(config))
{
    setConsumerType(ConsumerType::dashConsumer);
    setConsumerMediaType(m_config.enableAac ? MediaTypeAudioVideo : MediaTypeVideo);
    m_outputDirectory = m_config.outputRoot / m_config.streamToken;
    m_manifestPath = m_outputDirectory / (m_config.streamToken + ".mpd");
}

DashPackagerConsumer::~DashPackagerConsumer()
{
    stop();
}

bool DashPackagerConsumer::isFmp4Available()
{
    GstElementFactory* dashFactory = gst_element_factory_find("dashsink");
    GstElementFactory* mp4Factory = gst_element_factory_find("mp4mux");
    GstElementFactory* dashMp4Factory = gst_element_factory_find("dashmp4mux");
    if (dashFactory != nullptr)
    {
        gst_object_unref(dashFactory);
    }
    if (mp4Factory != nullptr)
    {
        gst_object_unref(mp4Factory);
    }
    if (dashMp4Factory != nullptr)
    {
        gst_object_unref(dashMp4Factory);
    }
    return dashFactory != nullptr && mp4Factory != nullptr && dashMp4Factory != nullptr
           && dashSinkSupportsDashMp4();
}

bool DashPackagerConsumer::createPipeline()
{
    if (!isFmp4Available())
    {
        setFailure("DASH requires dashsink with dashmp4 support, mp4mux, and dashmp4mux");
        return false;
    }

    std::error_code ec;
    std::filesystem::create_directories(m_outputDirectory, ec);
    if (ec)
    {
        setFailure("Failed to create DASH output directory: " + ec.message());
        return false;
    }

    m_pipeline = gst_pipeline_new(("dash_pipeline_" + m_config.streamToken).c_str());
    m_videoAppsrc = gst_element_factory_make("appsrc", "dash_video_src");
    m_videoParser = gst_element_factory_make("h264parse", "dash_video_parse");
    m_dashSink = gst_element_factory_make("dashsink", "dash_sink");
    if (m_pipeline == nullptr || m_videoAppsrc == nullptr || m_videoParser == nullptr || m_dashSink == nullptr)
    {
        setFailure("Failed to construct the DASH video pipeline");
        destroyPipeline();
        return false;
    }

    /* Raw input is encoded here rather than upstream, so the encoder is inserted
     * ahead of the parser and the caps are left unset until the first picture
     * arrives. Announcing "video/x-raw, format=I420" without a width or height
     * is worse than announcing nothing: videoconvert tries to negotiate against
     * a size it does not have and the pipeline fails at startup with
     * "Error calculating the output scaled size - integer overflow", before a
     * single frame is offered. The picture carries its own dimensions, which is
     * also the only description guaranteed to match what is actually sent. */
    if (!m_config.encodeRawInput)
    {
        GstCaps* videoCaps = gst_caps_new_simple("video/x-h264",
                                                 "stream-format", G_TYPE_STRING, "byte-stream",
                                                 "alignment", G_TYPE_STRING, "au", nullptr);
        g_object_set(G_OBJECT(m_videoAppsrc), "caps", videoCaps, nullptr);
        gst_caps_unref(videoCaps);
    }
    g_object_set(G_OBJECT(m_videoAppsrc),
                 "format", GST_FORMAT_TIME,
                 "is-live", TRUE,
                 "block", FALSE,
                 "do-timestamp", FALSE,
                 "max-bytes", MAX_APP_SRC_BYTES,
                 nullptr);
    g_object_set(G_OBJECT(m_videoParser), "config-interval", -1, nullptr);

    const std::string outputDirectory = m_outputDirectory.string() + "/";
    const std::string manifestName = m_manifestPath.filename().string();
    g_object_set(G_OBJECT(m_dashSink),
                 "mpd-root-path", outputDirectory.c_str(),
                 "mpd-filename", manifestName.c_str(),
                 // A live session must publish a dynamic MPD so dash.js uses
                 // the media timeline rather than treating each freshly
                 // generated self-initializing segment as static content.
                 "dynamic", TRUE,
                 "minimum-update-period", static_cast<guint64>(1000),
                 "suggested-presentation-delay", static_cast<guint64>(3000),
                 "target-duration", m_config.targetDurationSeconds,
                 /* Nothing upstream of this sink can answer a keyframe request:
                 ** the appsrc is fed frames that are already encoded.  Leaving it
                 ** on only makes the muxer cut segments it cannot start on a
                 ** keyframe, which is how a one second grid ended up holding
                 ** single frames.
                 */
                 "send-keyframe-requests", FALSE,
                 "muxer", 2,
                 nullptr);
    if (hasProperty(m_dashSink, "playlist-length"))
    {
        g_object_set(G_OBJECT(m_dashSink), "playlist-length", m_config.playlistLength, nullptr);
    }

    if (m_config.encodeRawInput)
    {
        m_videoConvert = gst_element_factory_make("videoconvert", "dash_video_convert");
        m_videoEncoder = gst_element_factory_make("x264enc", "dash_video_enc");
        if (m_videoConvert == nullptr || m_videoEncoder == nullptr)
        {
            setFailure("DASH software encoding requires videoconvert and x264enc");
            destroyPipeline();
            return false;
        }
        /* The same settings the download pipeline uses when it finds no
         * hardware encoder, plus the two a live stream needs: no B-frames so a
         * frame never waits on a later one, and a keyframe every interval so
         * the muxer has somewhere to cut a segment. zerolatency stops x264
         * holding frames back to look ahead, which would otherwise show up as
         * the first segment arriving seconds late. */
        /* Segments can only be cut on a keyframe, so a keyframe every target
         * duration is what makes the segments come out the length that was
         * asked for. Taking it from the encoder's configured IDR interval
         * instead would put 256 frames between keyframes and produce eight
         * second segments no matter what the target said. */
        const double encodeRate = m_config.sourceFrameRate > 0.0 ? m_config.sourceFrameRate : 30.0;
        const unsigned targetSeconds =
            m_config.targetDurationSeconds > 0 ? m_config.targetDurationSeconds : 1;
        const auto keyIntMax = static_cast<guint>(
            std::max(1L, std::lround(encodeRate * static_cast<double>(targetSeconds))));
        g_object_set(G_OBJECT(m_videoEncoder),
                     "bframes", static_cast<guint>(0),
                     "key-int-max", keyIntMax,
                     "tune", 0x00000004 /* zerolatency */,
                     "speed-preset", 1 /* ultrafast */,
                     nullptr);
        LOG(info) << "DASH encoding raw input for " << m_config.streamToken
                  << " with x264enc: bframes=0, key-int-max=" << keyIntMax
                  << endl;
        gst_bin_add_many(GST_BIN(m_pipeline), m_videoAppsrc, m_videoConvert, m_videoEncoder,
                         m_videoParser, m_dashSink, nullptr);
        if (!gst_element_link_many(m_videoAppsrc, m_videoConvert, m_videoEncoder, m_videoParser, nullptr)
            || !linkToDashPad(m_videoParser, m_dashSink, "video_%u"))
        {
            setFailure("Failed to link the DASH software encoding branch");
            destroyPipeline();
            return false;
        }
    }
    else
    {
        gst_bin_add_many(GST_BIN(m_pipeline), m_videoAppsrc, m_videoParser, m_dashSink, nullptr);
        if (!gst_element_link(m_videoAppsrc, m_videoParser)
            || !linkToDashPad(m_videoParser, m_dashSink, "video_%u"))
        {
            setFailure("Failed to link the DASH video branch");
            destroyPipeline();
            return false;
        }
    }

    if (m_config.enableAac)
    {
        m_audioAppsrc = gst_element_factory_make("appsrc", "dash_audio_src");
        m_audioParser = gst_element_factory_make("aacparse", "dash_audio_parse");
        if (m_audioAppsrc == nullptr || m_audioParser == nullptr)
        {
            LOG(warning) << "DASH AAC elements unavailable; continuing video-only for " << m_config.streamToken << endl;
            m_config.enableAac = false;
            setConsumerMediaType(MediaTypeVideo);
            if (m_audioAppsrc != nullptr)
            {
                gst_object_unref(m_audioAppsrc);
                m_audioAppsrc = nullptr;
            }
            if (m_audioParser != nullptr)
            {
                gst_object_unref(m_audioParser);
                m_audioParser = nullptr;
            }
        }
        else
        {
            GstBuffer* codecData = makeAacAudioSpecificConfig(m_config.audioSampleRate,
                                                               m_config.audioChannels);
            GstCaps* audioCaps = gst_caps_new_simple("audio/mpeg",
                                                     "mpegversion", G_TYPE_INT, 4,
                                                     "stream-format", G_TYPE_STRING, "raw",
                                                     "rate", G_TYPE_INT, static_cast<gint>(m_config.audioSampleRate),
                                                     "channels", G_TYPE_INT, static_cast<gint>(m_config.audioChannels),
                                                     "codec_data", GST_TYPE_BUFFER, codecData,
                                                     nullptr);
            if (codecData != nullptr)
            {
                gst_buffer_unref(codecData);
            }
            g_object_set(G_OBJECT(m_audioAppsrc),
                         "caps", audioCaps,
                         "format", GST_FORMAT_TIME,
                         "is-live", TRUE,
                         "block", FALSE,
                         "do-timestamp", FALSE,
                         "max-bytes", MAX_APP_SRC_BYTES,
                         nullptr);
            gst_caps_unref(audioCaps);
            gst_bin_add_many(GST_BIN(m_pipeline), m_audioAppsrc, m_audioParser, nullptr);
            if (!gst_element_link(m_audioAppsrc, m_audioParser)
                || !linkToDashPad(m_audioParser, m_dashSink, "audio_%u"))
            {
                LOG(warning) << "Failed to link DASH AAC branch; continuing video-only for "
                             << m_config.streamToken << endl;
                gst_bin_remove_many(GST_BIN(m_pipeline), m_audioAppsrc, m_audioParser, nullptr);
                m_audioAppsrc = nullptr;
                m_audioParser = nullptr;
                m_config.enableAac = false;
                setConsumerMediaType(MediaTypeVideo);
            }
        }
    }

    GstBus* bus = gst_pipeline_get_bus(GST_PIPELINE(m_pipeline));
    if (bus != nullptr)
    {
        gst_bus_set_sync_handler(bus, busSyncHandler, this, nullptr);
        gst_object_unref(bus);
    }
    return true;
}

bool DashPackagerConsumer::start()
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_state.load() == DashPackagerState::Running)
    {
        return true;
    }
    m_state.store(DashPackagerState::Starting);
    m_hasError.store(false);
    {
        std::lock_guard<std::mutex> errorLock(m_errorMutex);
        m_lastError.clear();
    }
    if (!createPipeline())
    {
        return false;
    }
    const GstStateChangeReturn result = gst_element_set_state(m_pipeline, GST_STATE_PLAYING);
    if (result == GST_STATE_CHANGE_FAILURE)
    {
        setFailure("Failed to set DASH pipeline to PLAYING");
        destroyPipeline();
        return false;
    }
    m_state.store(DashPackagerState::Running);
    m_startedAt = std::chrono::steady_clock::now();
    LOG(info) << "DASH packager started for " << m_config.streamToken
              << ", manifest=" << m_manifestPath << ", audio="
              << (m_config.enableAac ? "aac" : "none") << endl;
    /* One line carrying every setting that decides what this session publishes.
     * Reading them out of the config by hand means guessing which of several
     * files the container actually mounted, and the effective bitrate is not in
     * any of them - it is derived from the picture unless someone set it. */
    LOG(info) << "DASH config for " << m_config.streamToken
              << ": segment_duration=" << m_config.targetDurationSeconds << "s"
              << " playlist_length=" << m_config.playlistLength
              << " idle_timeout=" << GET_CONFIG().dash_idle_timeout_sec << "s"
              << " max_live_sessions=" << GET_CONFIG().max_live_dash_sessions
              << " idr_interval=" << GET_CONFIG().webrtc_out_set_idr_interval
              << " bitrate_kbps=" << GET_CONFIG().dash_bitrate_kbps
              << (GET_CONFIG().dash_bitrate_kbps > 0 ? " (configured)" : " (derived from height)")
              << " frame_rate=" << m_config.sourceFrameRate
              << " encode=" << (m_config.encodeRawInput ? "software" : "passthrough-or-hardware")
              << " timeline=" << (m_config.useArrivalTimestamps ? "arrival"
                                  : (m_config.preferSourceTimestamps ? "source-preferred"
                                     : (m_config.synthesizeTimestamps ? "synthesized" : "source")))
              << endl;
    return true;
}

void DashPackagerConsumer::reportDroppedFrame(const char* kind)
{
    // Backpressure drops every frame it touches, so an unthrottled line here is
    // thirty a second per stream.  Report the first and then every three
    // hundredth, carrying the running total.
    const uint64_t dropped = m_droppedFrames.fetch_add(1) + 1;
    if (dropped == 1 || (dropped % 300) == 0)
    {
        LOG(warning) << "DASH " << kind << " frame dropped for " << m_config.streamToken
                     << " (" << dropped << " dropped so far)" << endl;
    }
}

void DashPackagerConsumer::stop()
{
    // One line that says what the session actually did.  Without it the only
    // record of a degraded session is the rate limited warnings it emitted
    // while running, which say nothing about the totals.
    if (m_state.load() != DashPackagerState::Stopped)
    {
        const int64_t liveMs = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - m_startedAt).count();
        LOG(info) << "DASH packager stopping for " << m_config.streamToken
                  << ": ran " << liveMs << " ms, frames=" << m_videoTimeline.framesPushed
                  << ", gaps closed=" << m_videoTimeline.jumps
                  << ", carried=" << (m_videoTimeline.carried / GST_MSECOND) << " ms"
                  << ", late arrivals=" << m_videoTimeline.lateArrivals
                  << ", dropped=" << m_droppedFrames.load() << endl;
    }

    // End of stream has to travel the length of the pipeline before the sink
    // finishes the fragment it is writing.  Tearing down the moment it is sent
    // gives it no chance to arrive, and the fragment is left open for the life
    // of the process - one descriptor a session, invisible in one session and
    // plain after a few hundred.  Wait for it to come back, outside the lock:
    // the bus handler reports failures through this object, so holding the lock
    // while waiting would deadlock against the very error that makes the wait
    // time out.
    GstElement* pipeline = nullptr;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        /* End of stream asks the muxer to finalize, and finalizing means
         * describing the media - its codec, its resolution - in the manifest.
         * A session torn down before its first frame arrived never negotiated
         * caps, so there is nothing to describe: asking anyway walks the
         * muxer through a finalize whose every field is absent.  Nothing was
         * written, so there is nothing to finish either. */
        const bool wroteMedia = m_videoTimeline.framesPushed > 0 || m_audioTimeline.framesPushed > 0;
        if (wroteMedia)
        {
            if (m_videoAppsrc != nullptr)
            {
                gst_app_src_end_of_stream(GST_APP_SRC(m_videoAppsrc));
            }
            if (m_audioAppsrc != nullptr)
            {
                gst_app_src_end_of_stream(GST_APP_SRC(m_audioAppsrc));
            }
            if (m_pipeline != nullptr)
            {
                pipeline = GST_ELEMENT(gst_object_ref(m_pipeline));
            }
        }
        else if (m_pipeline != nullptr)
        {
            /* Only worth saying when there is still a pipeline to finalize.
             * Tearing down resets the counters, so a second stop looks exactly
             * like a session that never received a frame. */
            LOG(info) << "DASH packager for " << m_config.streamToken
                      << " stopped before any frame arrived; skipping the muxer finalize" << endl;
        }
    }
    if (pipeline != nullptr)
    {
        GstBus* bus = gst_pipeline_get_bus(GST_PIPELINE(pipeline));
        if (bus != nullptr)
        {
            // A pipeline that has already errored will never say end of stream,
            // so accept either answer and give up rather than hang teardown.
            GstMessage* message = gst_bus_timed_pop_filtered(
                bus, 3 * GST_SECOND,
                static_cast<GstMessageType>(GST_MESSAGE_EOS | GST_MESSAGE_ERROR));
            if (message != nullptr)
            {
                gst_message_unref(message);
            }
            else
            {
                LOG(warning) << "DASH pipeline for " << m_config.streamToken
                             << " did not finish before teardown" << endl;
            }
            gst_object_unref(bus);
        }
        gst_object_unref(pipeline);
    }

    std::lock_guard<std::mutex> lock(m_mutex);
    destroyPipeline();
    // Before cleanupOutput: the descriptors are matched by path, and the paths
    // stop existing the moment the directory is removed.
    closeLeakedFragmentDescriptors();
    cleanupOutput();
    if (!m_hasError.load())
    {
        m_state.store(DashPackagerState::Stopped);
    }
}

void DashPackagerConsumer::sendEOS()
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_videoAppsrc != nullptr)
    {
        gst_app_src_end_of_stream(GST_APP_SRC(m_videoAppsrc));
    }
    if (m_audioAppsrc != nullptr)
    {
        gst_app_src_end_of_stream(GST_APP_SRC(m_audioAppsrc));
    }
}

void DashPackagerConsumer::closeLeakedFragmentDescriptors()
{
    /* Scoped to this session's own directory, which carries a uuid: no other
     * session, and nothing else in the process, can have a descriptor open
     * under it.  Only reached once the pipeline is destroyed, so nothing of
     * ours is still writing.  A descriptor still open here belongs to a stream
     * object the sink leaked, and a leaked object is never finalized - which
     * is what makes closing it safe rather than a race against its owner. */
    const std::filesystem::path normalizedRoot = m_config.outputRoot.lexically_normal();
    const std::filesystem::path normalizedOutput = m_outputDirectory.lexically_normal();
    if (m_config.streamToken.empty() || normalizedOutput.parent_path() != normalizedRoot
        || normalizedOutput.filename() != m_config.streamToken)
    {
        return;
    }
    /* The configured root is relative to the working directory, while the
     * descriptor table answers in absolute paths, so the two only ever match
     * once this one is resolved. */
    std::error_code resolveFailure;
    std::filesystem::path absoluteOutput =
        std::filesystem::weakly_canonical(std::filesystem::absolute(normalizedOutput, resolveFailure),
                                          resolveFailure);
    if (resolveFailure || absoluteOutput.empty())
    {
        return;
    }
    const std::string sessionPrefix = absoluteOutput.string() + "/";

    /* Read the descriptor table with the POSIX calls rather than a directory
     * iterator: the table changes under the walk as other threads open and
     * close, and every C++ iterator answer to that is either an exception or
     * an error that would throw away the entries already found. */
    DIR* descriptorTable = ::opendir("/proc/self/fd");
    if (descriptorTable == nullptr)
    {
        return;
    }
    std::vector<int> leaked;
    /* Collect before closing: closing while walking would invalidate the
     * descriptor the walk itself is using. */
    while (const dirent* entry = ::readdir(descriptorTable))
    {
        char* end = nullptr;
        const long descriptor = std::strtol(entry->d_name, &end, 10);
        if (end == entry->d_name || *end != '\0' || descriptor <= STDERR_FILENO)
        {
            continue;
        }
        const std::string link = std::string("/proc/self/fd/") + entry->d_name;
        char target[PATH_MAX];
        const ssize_t length = ::readlink(link.c_str(), target, sizeof(target) - 1);
        if (length <= 0)
        {
            continue;
        }
        target[length] = '\0';
        if (std::string(target).rfind(sessionPrefix, 0) == 0)
        {
            leaked.push_back(static_cast<int>(descriptor));
        }
    }
    ::closedir(descriptorTable);

    unsigned closed = 0;
    for (const int descriptor : leaked)
    {
        if (::close(descriptor) == 0)
        {
            ++closed;
        }
    }
    if (closed > 0)
    {
        LOG(info) << "DASH closed " << closed << " fragment descriptor(s) the sink left open for "
                  << m_config.streamToken << endl;
    }
}

void DashPackagerConsumer::destroyPipeline()
{
    m_videoTimeline = TimelineState{};
    m_audioTimeline = TimelineState{};
    if (m_pipeline != nullptr)
    {
        GstBus* bus = gst_pipeline_get_bus(GST_PIPELINE(m_pipeline));
        if (bus != nullptr)
        {
            gst_bus_set_sync_handler(bus, nullptr, nullptr, nullptr);
            gst_object_unref(bus);
        }
        gst_element_set_state(m_pipeline, GST_STATE_NULL);
        // Going to NULL can complete asynchronously, and the sink only closes
        // the fragment it is writing as part of that transition.  Dropping the
        // last reference before the transition finishes leaves that file open
        // for the life of the process - one descriptor per session, which is
        // invisible in a single session and obvious after a few hundred.  Wait
        // for the state change, but not indefinitely: teardown must not hang on
        // a pipeline that has already failed.
        GstState reached = GST_STATE_VOID_PENDING;
        const GstStateChangeReturn settled =
            gst_element_get_state(m_pipeline, &reached, nullptr, 2 * GST_SECOND);
        if (settled != GST_STATE_CHANGE_SUCCESS || reached != GST_STATE_NULL)
        {
            LOG(warning) << "DASH pipeline for " << m_config.streamToken
                         << " did not reach NULL before teardown" << endl;
        }
        gst_object_unref(m_pipeline);
    }
    m_pipeline = nullptr;
    m_videoAppsrc = nullptr;
    m_videoParser = nullptr;
    m_videoConvert = nullptr;
    m_videoEncoder = nullptr;
    m_audioAppsrc = nullptr;
    m_audioParser = nullptr;
    m_dashSink = nullptr;
}

void DashPackagerConsumer::cleanupOutput()
{
    const std::filesystem::path normalizedRoot = m_config.outputRoot.lexically_normal();
    const std::filesystem::path normalizedOutput = m_outputDirectory.lexically_normal();
    if (normalizedOutput.parent_path() != normalizedRoot || normalizedOutput.filename() != m_config.streamToken
        || m_config.streamToken.empty())
    {
        LOG(error) << "Refusing unsafe DASH output cleanup: " << normalizedOutput << endl;
        return;
    }
    // Diagnostic: the same switch that stops the rolling prune also keeps the
    // directory after the session ends, because a session that misbehaves is
    // usually torn down before anyone can look at what it produced.
    const char* keep = std::getenv("VST_DASH_KEEP_SEGMENTS");
    if (keep != nullptr && keep[0] == '1')
    {
        LOG(warning) << "Keeping DASH output for diagnosis: " << normalizedOutput << endl;
        return;
    }
    std::error_code ec;
    std::filesystem::remove_all(normalizedOutput, ec);
    if (ec)
    {
        LOG(warning) << "Failed to remove DASH output " << normalizedOutput << ": " << ec.message() << endl;
    }
}

constexpr int NAL_TYPE_MASK = 0x1F;
constexpr int NAL_TYPE_IDR = 5;
constexpr int NAL_TYPE_SPS = 7;
constexpr int NAL_TYPE_PPS = 8;

bool DashPackagerConsumer::pushFrame(GstElement* appsrc, const uint8_t* data, size_t size,
                                     GstClockTime rawPts, TimelineState& timeline)
{
    if (appsrc == nullptr || data == nullptr || size == 0)
    {
        return false;
    }

    // The recorded pipeline stamps every encoded frame with the same value, so
    // the source timeline never advances and mp4mux rejects the stream on the
    // first buffer it cannot place.  Wall-clock arrival is not a substitute:
    // it follows encoder pacing, and a large IDR takes long enough to encode
    // that the gap around it reads as a whole segment, which makes the muxer
    // cut a segment holding nothing but that keyframe.  A recording has a known
    // constant frame rate, so the timeline is built from the frame index.
    if (m_config.synthesizeTimestamps && !m_config.preferSourceTimestamps)
    {
        timeline.synthesize = true;
    }
    if (!timeline.synthesize)
    {
        if (!GST_CLOCK_TIME_IS_VALID(rawPts))
        {
            timeline.synthesize = true;
        }
        else if (timeline.lastRawValid && rawPts <= timeline.lastRaw)
        {
            LOG(warning) << "DASH source timestamps are not advancing; synthesising a timeline for "
                         << m_config.streamToken << endl;
            timeline.synthesize = true;
        }
        else
        {
            timeline.lastRaw = rawPts;
            timeline.lastRawValid = true;
        }
    }

    if (!timeline.modeReported)
    {
        timeline.modeReported = true;
        LOG(info) << "DASH timeline for " << m_config.streamToken << ": "
                  << (m_config.useArrivalTimestamps ? "arrival"
                      : (timeline.synthesize ? "synthesized" : "source-timestamps"))
                  << " (first rawPts=" << (GST_CLOCK_TIME_IS_VALID(rawPts) ? rawPts : 0)
                  << " valid=" << GST_CLOCK_TIME_IS_VALID(rawPts)
                  << " rate=" << m_config.sourceFrameRate << ")" << endl;
    }

    GstClockTime pts = 0;
    GstClockTime duration = GST_CLOCK_TIME_NONE;
    if (m_config.useArrivalTimestamps)
    {
        // A live wall is composed in real time, so when a frame arrives is what
        // it is worth.  Counting frames instead would let anything lost on the
        // way from the compositor slow the published timeline, and a live edge
        // that runs slower than the clock falls behind without ever recovering.
        const auto now = std::chrono::steady_clock::now();
        if (!timeline.arrivalOriginValid)
        {
            timeline.arrivalOrigin = now;
            timeline.arrivalOriginValid = true;
        }
        const auto elapsed =
            std::chrono::duration_cast<std::chrono::nanoseconds>(now - timeline.arrivalOrigin).count();
        pts = static_cast<GstClockTime>(elapsed < 0 ? 0 : elapsed);
        // Never let two frames share a timestamp or go backwards: the muxer
        // rejects the stream on the first buffer it cannot place.
        if (timeline.lastOutValid && pts <= timeline.lastOut)
        {
            pts = timeline.lastOut + 1;
        }
    }
    else if (timeline.synthesize)
    {
        const double frameRate = m_config.sourceFrameRate > 0.0 ? m_config.sourceFrameRate : 30.0;
        const auto frameDuration = static_cast<GstClockTime>(GST_SECOND / frameRate);
        pts = timeline.frameIndex * frameDuration;
        duration = frameDuration;
        ++timeline.frameIndex;
    }
    else
    {
        if (!timeline.baselineValid || rawPts < timeline.baseline)
        {
            timeline.baseline = rawPts;
            timeline.baselineValid = true;
        }
        pts = rawPts - timeline.baseline;

        // A live source can skip forward: the overlay path decodes, draws and
        // re-encodes, and a stall anywhere in that chain means the next frame
        // arrives stamped seconds later.  Publishing that gap verbatim leaves a
        // hole in the media timeline, and a player whose playhead sits before it
        // never becomes contiguous again - it keeps downloading segments and
        // never resumes, which is a freeze that outlasts the stall that caused
        // it.  Close the hole instead and carry the offset forward, so the
        // published timeline is continuous and the viewer loses only the frames
        // that were genuinely missing.
        const double rate = m_config.sourceFrameRate > 0.0 ? m_config.sourceFrameRate : 30.0;
        const auto frameDuration = static_cast<GstClockTime>(GST_SECOND / rate);
        if (timeline.lastOutValid && frameDuration > 0)
        {
            const GstClockTime expected = timeline.lastOut + frameDuration;
            /* Only a stall is worth closing. One frame of slack is not enough
             * slack to tell a stall from a stream that is simply arriving at a
             * lower rate than the source nominally runs at: an overlay that
             * suppresses the frames it drew nothing on delivers every other one,
             * whose neighbours sit two source frames apart, and GST_SECOND/30
             * truncating to 33333333 ns puts that a nanosecond over a two-frame
             * limit. Closing those removed 33 ms per dropped frame - measured at
             * 12.8 seconds carried out of a 20 second replay - and the recording
             * played fast, because the timeline was shorter than the pictures in
             * it. A player copes with a hole of a few frames by showing one for
             * longer; what it cannot cross is the multi-second kind this was
             * written for. */
            const GstClockTime stallLimit =
                std::max<GstClockTime>(4 * frameDuration, 500 * GST_MSECOND);
            if (pts > timeline.lastOut + stallLimit)
            {
                const GstClockTime skipped = pts - expected;
                timeline.baseline += skipped;
                timeline.carried += skipped;
                ++timeline.jumps;
                pts = expected;
                // A source that keeps skipping produces a gap per frame, so
                // report the first and then only every fiftieth; the running
                // totals on each line carry the ones in between.
                if (timeline.jumps == 1 || (timeline.jumps % 50) == 0)
                {
                    LOG(warning) << "DASH timeline gap closed for " << m_config.streamToken
                                 << ": source skipped " << (skipped / GST_MSECOND)
                                 << " ms, total carried " << (timeline.carried / GST_MSECOND)
                                 << " ms across " << timeline.jumps << " gaps" << endl;
                }
            }
        }
    }

    /* Recorded for every path, not just the one that reads the source clock.
     * A session that synthesises its timeline - anything re-encoded, drawn on
     * or composed - publishes just as real a position, and leaving it unset
     * here means the only sessions that could report one are the ones nobody
     * needs to ask about. */
    timeline.lastOut = pts;
    timeline.lastOutValid = true;
    m_lastFrameEpochMs.store(std::chrono::duration_cast<std::chrono::milliseconds>(
                                 std::chrono::system_clock::now().time_since_epoch())
                                 .count());

    // How late the frames themselves arrive.  A viewer near the live edge starves
    // when a segment is written late, and the media timeline gives no sign of
    // that: it stays perfectly continuous while the pipeline behind it stutters.
    // The overlay path decodes, draws and re-encodes, so this is where a hiccup
    // in any of those stages becomes visible.
    {
        const auto now = std::chrono::steady_clock::now();
        if (timeline.lastArrivalValid)
        {
            const auto gapMs = static_cast<uint64_t>(
                std::chrono::duration_cast<std::chrono::milliseconds>(
                    now - timeline.lastArrival).count());
            if (gapMs > timeline.worstGapMs)
            {
                timeline.worstGapMs = gapMs;
            }
            // Late means late for this source, not for a 30 fps one.  A ten
            // frame a second stream delivers every 100 ms quite correctly, and
            // a fixed quarter second threshold would call every frame of a four
            // frame a second stream late.  Allow several frames of slack and
            // never trip below a quarter second.
            const double sourceRate = m_config.sourceFrameRate > 0.0 ? m_config.sourceFrameRate : 30.0;
            const uint64_t lateThresholdMs =
                std::max<uint64_t>(250, static_cast<uint64_t>((3.0 * 1000.0) / sourceRate));
            if (gapMs >= lateThresholdMs)
            {
                ++timeline.lateArrivals;
                // A chronically slow source makes every frame late, so report
                // the first and then every hundredth.
                if (timeline.lateArrivals == 1 || (timeline.lateArrivals % 100) == 0)
                {
                    LOG(warning) << "DASH frame arrived late for " << m_config.streamToken
                                 << ": " << gapMs << " ms since the previous frame ("
                                 << timeline.lateArrivals << " late so far)" << endl;
                }
            }
        }
        timeline.lastArrival = now;
        timeline.lastArrivalValid = true;
    }

    ++timeline.framesPushed;

    if (!m_firstFrameLogged.exchange(true))
    {
        const int64_t readyMs = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - m_startedAt).count();
        LOG(info) << "DASH first frame accepted for " << m_config.streamToken
                  << " after " << readyMs << " ms" << endl;
    }

    // Report on a clock, not a frame count: nine hundred frames is thirty
    // seconds at thirty a second and a minute and a half at ten.
    const double reportRate = m_config.sourceFrameRate > 0.0 ? m_config.sourceFrameRate : 30.0;
    const uint64_t framesPerReport = std::max<uint64_t>(1, static_cast<uint64_t>(reportRate * 30.0));
    if (++timeline.framesSinceReport >= framesPerReport)
    {
        timeline.framesSinceReport = 0;
        LOG(info) << "DASH timeline for " << m_config.streamToken << ": pts="
                  << (pts / GST_MSECOND) << " ms, gaps closed=" << timeline.jumps
                  << ", carried=" << (timeline.carried / GST_MSECOND) << " ms"
                  << ", worst frame arrival gap=" << timeline.worstGapMs << " ms"
                  << ", late arrivals=" << timeline.lateArrivals
                  << (timeline.synthesize ? " (synthesised)" : "") << endl;
        timeline.worstGapMs = 0;
    }

    GstBuffer* buffer = gst_buffer_new_allocate(nullptr, size, nullptr);
    if (buffer == nullptr)
    {
        return false;
    }
    GstMapInfo map{};
    if (!gst_buffer_map(buffer, &map, GST_MAP_WRITE))
    {
        gst_buffer_unref(buffer);
        return false;
    }
    std::memcpy(map.data, data, size);
    gst_buffer_unmap(buffer, &map);

    GST_BUFFER_PTS(buffer) = pts;
    GST_BUFFER_DTS(buffer) = pts;
    /* A freshly allocated buffer carries no flags, so every frame would look
    ** like a keyframe and the muxer would be free to cut a segment anywhere -
    ** which is exactly what it did, once per frame.  The access unit says which
    ** frames are keyframes, so mark the rest as delta units.
    **
    ** The access unit is a sequence of NAL units and it opens with an access
    ** unit delimiter, so reading only the first one finds a delimiter rather
    ** than the IDR behind it, calls every frame a delta unit, and leaves the
    ** muxer no frame it is allowed to start a segment on.  Walk the whole
    ** access unit instead.
    */
    if (size > 5)
    {
        bool keyframe = false;
        size_t offset = 0;
        while (offset + 4 <= size)
        {
            size_t startCode = 0;
            if (data[offset] == 0 && data[offset + 1] == 0 && data[offset + 2] == 0
                && data[offset + 3] == 1)
            {
                startCode = 4;
            }
            else if (data[offset] == 0 && data[offset + 1] == 0 && data[offset + 2] == 1)
            {
                startCode = 3;
            }
            if (startCode == 0)
            {
                ++offset;
                continue;
            }
            const size_t nalPos = offset + startCode;
            if (nalPos >= size)
            {
                break;
            }
            const int nalType = data[nalPos] & NAL_TYPE_MASK;
            if (nalType == NAL_TYPE_IDR || nalType == NAL_TYPE_SPS || nalType == NAL_TYPE_PPS)
            {
                keyframe = true;
                break;
            }
            offset = nalPos + 1;
        }
        if (!keyframe)
        {
            GST_BUFFER_FLAG_SET(buffer, GST_BUFFER_FLAG_DELTA_UNIT);
        }
    }
    if (GST_CLOCK_TIME_IS_VALID(duration))
    {
        GST_BUFFER_DURATION(buffer) = duration;
    }

    const GstFlowReturn flow = gst_app_src_push_buffer(GST_APP_SRC(appsrc), buffer);
    return flow == GST_FLOW_OK;
}

void DashPackagerConsumer::onFrame(FrameParams& params)
{
    std::vector<uint8_t> parsed;
    const uint8_t* data = params.m_buffer;
    size_t size = params.m_size > 0 ? static_cast<size_t>(params.m_size) : 0;
    const bool isAudio = iequals(params.m_media, "audio");
    if (!isAudio && params.m_needParsing)
    {
        parsed = parseAndCreateFrame(params);
        // SPS/PPS arrive as individual NAL units on this callback path.  The
        // parser retains them and returns an empty payload until it can prepend
        // them to a decodable access unit.  Do not send an empty buffer to
        // appsrc: it becomes a timestamped sample in dashsink and breaks the
        // fMP4 media timeline after the initialization segment.
        if (parsed.empty())
        {
            return;
        }
        data = parsed.data();
        size = parsed.size();
    }

    std::lock_guard<std::mutex> lock(m_mutex);
    const bool isAac = iequals(params.m_codec, "AAC") || iequals(params.m_codec, "MPEG4-GENERIC")
                       || iequals(params.m_codec, "MPEG4GENERIC");
    // Per-NAL arrival times are incorrect because a single access unit arrives
    // as a burst of callbacks.
    const bool pushed = isAudio
        ? (m_config.enableAac && isAac
           && pushFrame(m_audioAppsrc, data, size, toGstTime(params), m_audioTimeline))
        : pushFrame(m_videoAppsrc, data, size, toGstTime(params), m_videoTimeline);
    if (!pushed && !isAudio)
    {
        reportDroppedFrame("video");
    }
}

void DashPackagerConsumer::onFrame(std::shared_ptr<RawFrameParams> frameData)
{
    if (!frameData)
    {
        return;
    }
    /* Where there is no hardware encoder the pipeline ends at the transform and
     * this callback carries a decoded picture, which is ours to encode. */
    if (frameData->m_isYuvBuffer)
    {
        if (m_config.encodeRawInput)
        {
            pushRawPicture(frameData);
        }
        return;
    }
    // Producers either hand over their own pointer or a mapped GstBuffer.
    const uint8_t* data = frameData->m_buffer != nullptr
        ? frameData->m_buffer
        : static_cast<const uint8_t*>(frameData->m_map.data);
    const size_t size = frameData->m_map.size > 0 ? static_cast<size_t>(frameData->m_map.size) : 0;
    if (data == nullptr || size == 0)
    {
        return;
    }
    if (m_state.load() != DashPackagerState::Running)
    {
        return;
    }

    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_videoAppsrc == nullptr)
    {
        return;
    }

    // Recordings store H.264 as length prefixed AVC with the parameter sets in
    // codec_data, not as the byte-stream the live path delivers.  Taking the
    // caps from the sample rather than assuming a format is what lets the same
    // packager serve both.
    if (!m_sourceCapsSet && frameData->m_sample != nullptr)
    {
        if (GstCaps* caps = gst_sample_get_caps(frameData->m_sample))
        {
            gst_app_src_set_caps(GST_APP_SRC(m_videoAppsrc), caps);
            m_sourceCapsSet = true;
            gchar* description = gst_caps_to_string(caps);
            LOG(info) << "DASH source caps for " << m_config.streamToken << ": "
                      << (description != nullptr ? description : "unknown") << endl;
            g_free(description);
        }
    }

    /* The window is expressed as wall clock, so it can only be applied to a
     * source that stamps frames the same way. A recording made from an rtsp
     * sensor does: its timestamps are milliseconds since the epoch and keep
     * rising across file boundaries. An uploaded file does not - it counts from
     * the start of the file - so comparing the two discards every frame ever
     * delivered, and the session runs to completion having written nothing.
     * That is what a seek into an uploaded recording did: the source seeked
     * correctly to 7m28s and offered frames at 448581 ms against a window
     * starting at 1735690039478 ms.
     *
     * Any real epoch timestamp is past the year 2001, so a first frame below
     * that is counting from something else and the window cannot be applied. */
    if (m_windowApplies && !m_windowDecided && frameData->pts > 0)
    {
        /* Decided on the first frame that carries a timestamp at all. Deciding
         * on the first frame unconditionally is wrong: a source whose opening
         * frame is stamped zero would settle the question on no evidence and
         * never revisit it, and every later frame would be discarded. */
        m_windowDecided = true;
        constexpr int64_t kYear2001Ms = 1000000000000;
        if (frameData->pts < kYear2001Ms && m_config.startEpochMs >= kYear2001Ms)
        {
            m_windowApplies = false;
            LOG(info) << "DASH source for " << m_config.streamToken
                      << " timestamps from the start of the recording, not the epoch"
                      << " (first frame " << frameData->pts << " ms); the requested window is"
                      << " already applied by the source, so it is not filtered again" << endl;
        }
    }
    /* Judged per frame rather than latched with the window decision: the
     * window is decided once from the first frame that carries a timestamp,
     * but this has to follow the source as it advances. */
    {
        constexpr int64_t kYear2001Ms = 1000000000000;
        if (frameData->pts >= kYear2001Ms)
        {
            m_lastSourceEpochMs.store(frameData->pts);
        }
    }
    if (m_windowApplies && m_config.startEpochMs > 0 && frameData->pts > 0
        && frameData->pts < m_config.startEpochMs)
    {
        /* Dropping every frame here is indistinguishable from a stream that
         * simply never started: the session runs, the decoder plays, and not a
         * byte is ever written. Say so, rate limited, with the two numbers that
         * explain it. */
        const uint64_t skipped = m_beforeWindow.fetch_add(1) + 1;
        if (skipped == 1 || (skipped % 300) == 0)
        {
            LOG(warning) << "DASH dropping frames before the requested window for "
                         << m_config.streamToken << ": frame at " << frameData->pts
                         << " ms, window starts at " << m_config.startEpochMs
                         << " ms (" << skipped << " dropped so far)" << endl;
        }
        return;
    }
    // pts is milliseconds since the epoch and keeps rising across file
    // boundaries, which is what makes a window spanning several recordings one
    // continuous timeline.
    const GstClockTime rawPts = frameData->pts > 0
        ? static_cast<GstClockTime>(frameData->pts) * GST_MSECOND
        : GST_CLOCK_TIME_NONE;
    const bool pushed = pushFrame(m_videoAppsrc, data, size, rawPts, m_videoTimeline);
    if (!pushed)
    {
        reportDroppedFrame("replay");
    }
}

bool DashPackagerConsumer::hasError() const
{
    return m_hasError.load();
}

DashPackagerState DashPackagerConsumer::state() const
{
    return m_state.load();
}

bool DashPackagerConsumer::audioEnabled() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_config.enableAac;
}

int64_t DashPackagerConsumer::publishedPositionMs() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!m_videoTimeline.lastOutValid)
    {
        return -1;
    }
    return static_cast<int64_t>(m_videoTimeline.lastOut / GST_MSECOND);
}

/* The transform hands over the picture as the buffer libwebrtc wants, because
 * the WebRTC sink is the consumer it was written for: a reference counted
 * I420 buffer whose planes are strided and not necessarily contiguous. appsrc
 * needs one contiguous allocation, so copy row by row, honouring each plane's
 * own stride rather than assuming width. */
void DashPackagerConsumer::pushRawPicture(const std::shared_ptr<RawFrameParams>& frameData)
{
    if (frameData->m_buffer == nullptr)
    {
        return;
    }
    if (m_state.load() != DashPackagerState::Running)
    {
        return;
    }
    webrtc::scoped_refptr<webrtc::I420Buffer> picture(
        static_cast<webrtc::I420Buffer*>(static_cast<void*>(frameData->m_buffer)));
    if (picture.get() == nullptr)
    {
        return;
    }
    const int width = picture->width();
    const int height = picture->height();
    if (width <= 0 || height <= 0)
    {
        return;
    }

    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_videoAppsrc == nullptr)
    {
        return;
    }
    /* The size is known from the first picture, not from configuration, so a
     * source that turns out to differ from what was requested still plays. */
    if (!m_videoTimeline.rawCapsSet)
    {
        GstCaps* caps = gst_caps_new_simple(
            "video/x-raw",
            "format", G_TYPE_STRING, "I420",
            "width", G_TYPE_INT, width,
            "height", G_TYPE_INT, height,
            "framerate", GST_TYPE_FRACTION,
            static_cast<int>(m_config.sourceFrameRate > 0.0 ? m_config.sourceFrameRate : 30.0), 1,
            nullptr);
        gst_app_src_set_caps(GST_APP_SRC(m_videoAppsrc), caps);
        gst_caps_unref(caps);
        m_videoTimeline.rawCapsSet = true;
        /* The encoder is built before any picture arrives, so the rate is set
         * here instead - this is the first point the height is known, and
         * x264enc takes a bitrate change in any state. Left unset it encodes at
         * its own 2048 kbit/s default whatever the resolution, which is thin
         * for 1080p and far too thin for 2160p. */
        if (m_videoEncoder != nullptr)
        {
            const int kbps = dashBitrateKbpsForHeight(GET_CONFIG().dash_bitrate_kbps, height);
            g_object_set(G_OBJECT(m_videoEncoder), "bitrate", static_cast<guint>(kbps), nullptr);
            /* Same shape as the hardware line so one search finds either. */
            LOG(info) << "DASH software encoder bitrate: " << kbps
                      << " kbit/s for height " << height
                      << " (configured=" << GET_CONFIG().dash_bitrate_kbps << ")"
                      << " stream=" << m_config.streamToken << endl;
        }
        LOG(info) << "DASH raw input caps for " << m_config.streamToken << ": I420 "
                  << width << "x" << height << " at " << m_config.sourceFrameRate << " fps" << endl;
    }

    const size_t ySize = static_cast<size_t>(width) * static_cast<size_t>(height);
    const int chromaWidth = (width + 1) / 2;
    const int chromaHeight = (height + 1) / 2;
    const size_t chromaSize = static_cast<size_t>(chromaWidth) * static_cast<size_t>(chromaHeight);
    const size_t total = ySize + 2U * chromaSize;

    m_rawScratch.resize(total);
    uint8_t* out = m_rawScratch.data();
    for (int row = 0; row < height; ++row)
    {
        std::memcpy(out + static_cast<size_t>(row) * static_cast<size_t>(width),
                    picture->DataY() + static_cast<size_t>(row) * static_cast<size_t>(picture->StrideY()),
                    static_cast<size_t>(width));
    }
    out += ySize;
    for (int row = 0; row < chromaHeight; ++row)
    {
        std::memcpy(out + static_cast<size_t>(row) * static_cast<size_t>(chromaWidth),
                    picture->DataU() + static_cast<size_t>(row) * static_cast<size_t>(picture->StrideU()),
                    static_cast<size_t>(chromaWidth));
    }
    out += chromaSize;
    for (int row = 0; row < chromaHeight; ++row)
    {
        std::memcpy(out + static_cast<size_t>(row) * static_cast<size_t>(chromaWidth),
                    picture->DataV() + static_cast<size_t>(row) * static_cast<size_t>(picture->StrideV()),
                    static_cast<size_t>(chromaWidth));
    }

    const GstClockTime rawPts = frameData->pts > 0
        ? static_cast<GstClockTime>(frameData->pts) * GST_MSECOND
        : GST_CLOCK_TIME_NONE;
    if (!pushFrame(m_videoAppsrc, m_rawScratch.data(), m_rawScratch.size(), rawPts, m_videoTimeline))
    {
        reportDroppedFrame("raw");
    }
}

uint64_t DashPackagerConsumer::framesPublished() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_videoTimeline.framesPushed;
}

int64_t DashPackagerConsumer::lastFrameEpochMs() const
{
    return m_lastFrameEpochMs.load();
}

int64_t DashPackagerConsumer::lastSourceEpochMs() const
{
    return m_lastSourceEpochMs.load();
}

std::filesystem::path DashPackagerConsumer::manifestPath() const
{
    return m_manifestPath;
}

std::string DashPackagerConsumer::lastError() const
{
    std::lock_guard<std::mutex> lock(m_errorMutex);
    return m_lastError;
}

void DashPackagerConsumer::setFailure(const std::string& message)
{
    {
        std::lock_guard<std::mutex> lock(m_errorMutex);
        m_lastError = message;
    }
    m_hasError.store(true);
    m_state.store(DashPackagerState::Failed);
    LOG(error) << "DASH packager " << m_config.streamToken << ": " << message << endl;
}

GstBusSyncReply DashPackagerConsumer::busSyncHandler(GstBus* /*bus*/, GstMessage* message, gpointer userData)
{
    auto* self = static_cast<DashPackagerConsumer*>(userData);
    if (self != nullptr && GST_MESSAGE_TYPE(message) == GST_MESSAGE_ERROR)
    {
        GError* error = nullptr;
        gchar* debug = nullptr;
        gst_message_parse_error(message, &error, &debug);
        std::string text = error != nullptr ? error->message : "Unknown GStreamer error";
        // The debug string names the element and the reason; without it a mux
        // failure is indistinguishable from any other pipeline error.
        if (debug != nullptr)
        {
            text += " [";
            text += debug;
            text += "]";
        }
        self->setFailure(text);
        if (error != nullptr)
        {
            g_error_free(error);
        }
        g_free(debug);
    }
    return GST_BUS_PASS;
}
