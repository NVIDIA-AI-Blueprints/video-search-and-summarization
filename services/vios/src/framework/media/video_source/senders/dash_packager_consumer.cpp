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

#include <gst/app/gstappsrc.h>

#include <algorithm>
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

    GstCaps* videoCaps = gst_caps_new_simple("video/x-h264",
                                             "stream-format", G_TYPE_STRING, "byte-stream",
                                             "alignment", G_TYPE_STRING, "au", nullptr);
    g_object_set(G_OBJECT(m_videoAppsrc),
                 "caps", videoCaps,
                 "format", GST_FORMAT_TIME,
                 "is-live", TRUE,
                 "block", FALSE,
                 "do-timestamp", FALSE,
                 "max-bytes", MAX_APP_SRC_BYTES,
                 nullptr);
    gst_caps_unref(videoCaps);
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
                 "send-keyframe-requests", TRUE,
                 "muxer", 2,
                 nullptr);
    if (hasProperty(m_dashSink, "playlist-length"))
    {
        g_object_set(G_OBJECT(m_dashSink), "playlist-length", m_config.playlistLength, nullptr);
    }

    gst_bin_add_many(GST_BIN(m_pipeline), m_videoAppsrc, m_videoParser, m_dashSink, nullptr);
    if (!gst_element_link(m_videoAppsrc, m_videoParser)
        || !linkToDashPad(m_videoParser, m_dashSink, "video_%u"))
    {
        setFailure("Failed to link the DASH video branch");
        destroyPipeline();
        return false;
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
    LOG(info) << "DASH packager started for " << m_config.streamToken
              << ", manifest=" << m_manifestPath << ", audio="
              << (m_config.enableAac ? "aac" : "none") << endl;
    return true;
}

void DashPackagerConsumer::stop()
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
    destroyPipeline();
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

void DashPackagerConsumer::destroyPipeline()
{
    m_videoBaselineValid = false;
    m_audioBaselineValid = false;
    if (m_pipeline != nullptr)
    {
        GstBus* bus = gst_pipeline_get_bus(GST_PIPELINE(m_pipeline));
        if (bus != nullptr)
        {
            gst_bus_set_sync_handler(bus, nullptr, nullptr, nullptr);
            gst_object_unref(bus);
        }
        gst_element_set_state(m_pipeline, GST_STATE_NULL);
        gst_object_unref(m_pipeline);
    }
    m_pipeline = nullptr;
    m_videoAppsrc = nullptr;
    m_videoParser = nullptr;
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
    std::error_code ec;
    std::filesystem::remove_all(normalizedOutput, ec);
    if (ec)
    {
        LOG(warning) << "Failed to remove DASH output " << normalizedOutput << ": " << ec.message() << endl;
    }
}

bool DashPackagerConsumer::pushFrame(GstElement* appsrc, const uint8_t* data, size_t size,
                                     GstClockTime rawPts, GstClockTime& baseline, bool& baselineValid)
{
    if (appsrc == nullptr || data == nullptr || size == 0)
    {
        return false;
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

    if (GST_CLOCK_TIME_IS_VALID(rawPts))
    {
        if (!baselineValid || rawPts < baseline)
        {
            baseline = rawPts;
            baselineValid = true;
        }
        GST_BUFFER_PTS(buffer) = rawPts - baseline;
        GST_BUFFER_DTS(buffer) = GST_BUFFER_PTS(buffer);
    }
    const GstFlowReturn flow = gst_app_src_push_buffer(GST_APP_SRC(appsrc), buffer);
    return flow == GST_FLOW_OK;
}

void DashPackagerConsumer::onFrame(FrameParams& params)
{
    if (m_state.load() != DashPackagerState::Running)
    {
        return;
    }

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
           && pushFrame(m_audioAppsrc, data, size, toGstTime(params), m_audioBaseline, m_audioBaselineValid))
        : pushFrame(m_videoAppsrc, data, size, toGstTime(params), m_videoBaseline, m_videoBaselineValid);
    if (!pushed && !isAudio)
    {
        LOG(warning) << "DASH video frame dropped for " << m_config.streamToken << endl;
    }
}

void DashPackagerConsumer::onFrame(std::shared_ptr<RawFrameParams> /*frameData*/)
{
    // V1 packages encoded frames delivered by StreamMonitor.
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
        const std::string text = error != nullptr ? error->message : "Unknown GStreamer error";
        self->setFailure(text);
        if (error != nullptr)
        {
            g_error_free(error);
        }
        g_free(debug);
    }
    return GST_BUS_PASS;
}
