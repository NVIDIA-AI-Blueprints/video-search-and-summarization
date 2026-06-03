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

#include "unified_storage_writer.h"
#include "logger.h"
#include "../unified_storage_types.h"
#include "utils.h"
#include <algorithm>
#include <cctype>
#include <errno.h>
#include <filesystem>
#include <fstream>
#include <glib/gstdio.h>
#include <gst/app/gstappsink.h>
#include <gst/app/gstappsrc.h>
#include <gst/gst.h>
#include <iomanip>
#include <iostream>
#include <limits>
#include <numeric>
#include <random>
#include <sstream>
#include <sys/time.h>
#include <thread>

// Constants
constexpr int ONE_SEC_DURATION = 1 * 1000;
constexpr int SEC_TO_MICRO_SEC = 1000 * 1000;
constexpr int AUDIO_CODEC_FORMAT_MULAW = 1;
constexpr int AUDIO_CODEC_FORMAT_ALAW = 2;
constexpr int AUDIO_PCM_FORMAT_S8 = 2;
constexpr int MAX_GMAIN_LOOP_START_WAIT = 5;
constexpr int MAX_GMAIN_LOOP_START_RETRY = 3;
constexpr int DEFAULT_VIDEO_FRAMERATE_NUM = 30;
constexpr int DEFAULT_VIDEO_FRAMERATE_DEN = 1;

using namespace std;
using namespace nv_vms;

// Forward declarations for GStreamer callbacks
GstPadProbeReturn event_probe(GstPad* pad, GstPadProbeInfo* info, gpointer user_data);
gpointer gmainLoopthread(gpointer data);

// UnifiedStorageWriter implementation
UnifiedStorageWriter::UnifiedStorageWriter(StorageType type) : m_storageMode(type)
{
    LOG(info) << "Created unified storage writer for " << (type == StorageType::LOCAL ? StorageConstants::LOCAL_STORAGE : StorageConstants::CLOUD_STORAGE) << " storage"
              << endl;
    m_streamId = "uninitialized";
}

UnifiedStorageWriter::~UnifiedStorageWriter()
{
    try {
        if (m_format)
        {
            free(m_format);
            m_format = nullptr;
        }
        destroyPipeline();
    } catch (const std::exception& e) {
        try { LOG(error) << "Exception in ~UnifiedStorageWriter: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
    } catch (...) {
        try { LOG(error) << "Unknown exception in ~UnifiedStorageWriter" << endl; } catch (...) { (void)std::current_exception(); }
    }
}

// StorageWriter interface
bool UnifiedStorageWriter::isAvailable() const
{
    return m_pipeline_ready.load();
}

std::string UnifiedStorageWriter::getStorageMode() const
{
    return (m_storageMode == StorageType::LOCAL) ? StorageConstants::LOCAL_STORAGE : StorageConstants::CLOUD_STORAGE;
}

std::string UnifiedStorageWriter::getBucketName() const
{
    // Get bucket name from storage configuration parameters
    return m_config.getParameter(StorageConstants::BUCKET_NAME_KEY, "");
}

std::string UnifiedStorageWriter::startWrite(const std::string& remote_path, const std::string& stream_id,
                                             size_t estimated_size)
{
    std::lock_guard<std::mutex> lock(m_pipeline_mutex);

    LOG(info) << "Starting recording session for stream: " << stream_id << " path: " << remote_path << endl;

    // If there's an active session, properly finalize the current file before starting new one
    if (m_session_active.load())
    {
        // Generate new session ID for the new file first
        std::string session_id = generateSessionId(stream_id);
        
        // Update session state before reusing pipeline
        m_current_session_id = session_id;
        m_current_remote_path = remote_path;
        m_streamId = stream_id;

        if (!reusePipelineForNewFile(remote_path))
        {
            setLastError("Failed to reuse pipeline for new file");
            LOG(error) << "Failed to reuse pipeline for new file: " << remote_path << endl;
            return "";
        }

        LOG(info) << "Recording session continued with new file: " << session_id << " for stream: " << stream_id
                  << endl;
        return session_id;
    }

    // Initialize storage if not already done
    if (!initializeStorage())
    {
        setLastError("Failed to initialize storage");
        LOG(error) << "Failed to initialize storage for stream: " << stream_id << endl;
        return "";
    }

    // Create pipeline only if not already created
    if (!m_pipeline_ready.load())
    {
        if (!createPipeline(m_videoCodec, m_audioSupported, stream_id))
        {
            setLastError("Failed to create pipeline");
            LOG(error) << "Failed to create pipeline for stream: " << stream_id << endl;
            return "";
        }
        m_pipeline_ready = true;
    }

    // Generate session ID using base class method
    std::string session_id = generateSessionId(stream_id);

    // Start session
    if (!startSession(session_id, remote_path))
    {
        setLastError("Failed to start session");
        LOG(error) << "Failed to start session for stream: " << stream_id << endl;
        return "";
    }

    m_session_active = true;
    m_current_session_id = session_id;
    m_current_remote_path = remote_path;
    m_streamId = stream_id; // Store the stream ID

    LOG(info) << "Recording session started: " << session_id << " for stream: " << stream_id << endl;
    return session_id;
}

bool UnifiedStorageWriter::onFrame(const std::string& session_id, const void* data, size_t size, int64_t pts,
                                   const std::string& media_type)
{
    if (!m_session_active.load() || session_id != m_current_session_id)
    {
        setLastError("Invalid session");
        LOG(error) << "Invalid session: " << session_id << " current session ID: " << m_current_session_id
                   << " for stream ID: " << (m_streamId.empty() ? "unknown" : m_streamId) << endl;
        return false;
    }

    if (getLastError().empty() == false)
    {
        LOG(error) << "Error in pushBufferToPipeline: " << getLastError() << endl;
        return false;
    }

    // Push buffer to pipeline with PTS and media type
    return pushBufferToPipeline(data, size, pts, media_type);
}

StorageResult UnifiedStorageWriter::stopWrite(const std::string& session_id, const std::string& stream_id)
{
    StorageResult result;
    result.success = false;

    if (!m_session_active.load() || session_id != m_current_session_id)
    {
        result.message = "Invalid session";
        setLastError(result.message);
        return result;
    }

    // Send EOS to pipeline (reused from GstMux sendEOS logic)
    if (!sendEOS())
    {
        result.message = "Failed to send EOS to pipeline";
        setLastError(result.message);
        return result;
    }

    // Set to NULL and wait for pipeline to finish
    if (!setPipelineState(GST_STATE_NULL))
    {
        result.message = "Failed to wait for pipeline to finish";
        setLastError(result.message);
        return result;
    }

    // Finalize session (implemented by derived classes)
    result = finalizeSession(session_id, stream_id);

    // Clean up session
    cleanupSession(session_id);

    m_session_active = false;
    m_current_session_id.clear();
    m_current_remote_path.clear();
    m_streamId.clear(); // Clear the stream ID

    return result;
}

bool UnifiedStorageWriter::pauseWrite(const std::string& session_id, const std::string& stream_id)
{
    std::string current_stream_id = m_streamId; // Store before potential clearing

    if (!m_session_active.load() || session_id != m_current_session_id)
    {
        LOG(warning) << "Invalid session for cancellation: " << session_id
                     << " for stream ID: " << (current_stream_id.empty() ? "unknown" : current_stream_id) << endl;
        return false;
    }

    // Check if pipeline is still valid before attempting operations
    if (bool isPipelineValid = m_pipeline ? GST_IS_ELEMENT(m_pipeline) : false; !isPipelineValid)
    {
        LOG(warning) << "Pipeline is not valid during pause, cleaning up session: " << session_id
                     << " for stream ID: " << (current_stream_id.empty() ? "unknown" : current_stream_id) << endl;
        // Clean up session even if pipeline is gone
        cleanupSession(session_id);
        m_session_active = false;
        m_current_session_id.clear();
        m_current_remote_path.clear();
        m_streamId.clear();
        return true;
    }

    // Stop session
    stopSession(session_id);

    // Clean up session
    cleanupSession(session_id);

    m_session_active = false;
    m_current_session_id.clear();
    m_current_remote_path.clear();
    m_streamId.clear(); // Clear the stream ID

    LOG(info) << "Recording session paused: " << session_id
              << " for stream ID: " << (current_stream_id.empty() ? "unknown" : current_stream_id) << endl;
    return true;
}

bool UnifiedStorageWriter::configureStorage(const StorageConfig& config)
{
    m_config = config;

    // Extract video codec from config
    m_videoCodec = config.getParameter(StorageConstants::VIDEO_CODEC_KEY, "h264");
    m_audioSupported = (config.getParameter(StorageConstants::AUDIO_SUPPORTED_KEY, "false") == "true");
    m_audioCodec = config.getParameter(StorageConstants::AUDIO_CODEC_KEY, "pcmu");

    if (m_audioSupported)
    {
        LOG(info) << "Configured unified storage writer with codec: " << m_videoCodec
                  << " audio: " << (m_audioSupported ? "enabled" : "disabled") << " audio_codec: " << m_audioCodec << endl;
    }
    else
    {
        LOG(info) << "Configured unified storage writer with codec: " << m_videoCodec << " audio: " << (m_audioSupported ? "enabled" : "disabled") << endl;
    }
    return true;
}

StorageConfig UnifiedStorageWriter::getStorageConfiguration() const
{
    return m_config;
}

StorageStats UnifiedStorageWriter::getStorageStats() const
{
    StorageStats stats;
    stats.bytesWritten = m_total_bytes_written.load();
    stats.framesWritten = m_frames_written.load();
    return stats;
}

std::string UnifiedStorageWriter::getLastError() const
{
    std::lock_guard<std::mutex> lock(m_error_mutex);
    return m_last_error;
}

std::string UnifiedStorageWriter::generateSessionId(const std::string& stream_id)
{
    // Generate unique session ID with timestamp, random number, and stream ID
    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<> dis(100000, 999999);

    // Get current timestamp
    auto now = std::chrono::system_clock::now();
    auto timestamp = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();

    // Create session ID with format: type_timestamp_random_streamid
    std::string prefix = (m_storageMode == StorageType::LOCAL ? StorageConstants::LOCAL_STORAGE : StorageConstants::CLOUD_STORAGE);
    std::string session_id =
        prefix + "_" + std::to_string(timestamp) + "_" + std::to_string(dis(gen)) + "_" + stream_id;

    return session_id;
}

// Pipeline management
bool UnifiedStorageWriter::createPipeline(const std::string& video_codec, bool audio_supported,
                                          const std::string& stream_id)
{
    // Acquire pipeline mutex if not already held by caller
    std::lock_guard<std::mutex> lock(m_pipeline_mutex);
    return createPipelineInternal(video_codec, audio_supported, stream_id);
}

bool UnifiedStorageWriter::createPipelineInternal(const std::string& video_codec, bool audio_supported,
                                                  const std::string& stream_id)
{
    // Store the codec information and stream ID
    m_videoCodec = video_codec;
    m_audioSupported = audio_supported;
    m_streamId = stream_id;

    LOG(info) << "Creating Storage Writer pipeline for " << (m_storageMode == StorageType::LOCAL ? StorageConstants::LOCAL_STORAGE : StorageConstants::CLOUD_STORAGE)
              << " storage" << endl;

    if (gst_is_initialized() == false)
    {
        gst_init(nullptr, nullptr);
    }

    m_pipeline = gst_pipeline_new("pipeline");
    m_sourceVideo = gst_element_factory_make("appsrc", nullptr);

    if (iequals(video_codec, "h265"))
    {
        m_parserVideo = gst_element_factory_make("h265parse", nullptr);
    }
    else
    {
        m_parserVideo = gst_element_factory_make("h264parse", nullptr);
    }

    // Force the parser's source pad to emit AU-aligned
    // output. We only constrain `alignment=au` — `stream-format` is left for
    // h26xparse and matroskamux to negotiate (matroskamux requires hvc1/avc1
    // for HEVC/H264 respectively, so pinning `byte-stream` here would break
    // the link).
    {
        std::string vcodec_lc = video_codec;
        std::transform(vcodec_lc.begin(), vcodec_lc.end(), vcodec_lc.begin(), ::tolower);
        std::string filter_caps_str = "video/x-" + vcodec_lc + ", alignment=(string)au";
        m_capsfilterVideo = gst_element_factory_make("capsfilter", NULL);
        if (m_capsfilterVideo)
        {
            GstCaps* filter_caps = gst_caps_from_string(filter_caps_str.c_str());
            if (filter_caps)
            {
                g_object_set(G_OBJECT(m_capsfilterVideo), "caps", filter_caps, NULL);
                gst_caps_unref(filter_caps);
                LOG(info) << "Configured parser-output capsfilter with: "
                          << filter_caps_str << endl;
            }
        }
    }

    // Get and store parser element name
    gchar* element_name = gst_element_get_name(m_parserVideo);
    if (element_name)
    {
        m_parserVideoName = element_name; // std::string will handle the memory
        g_free(element_name);
    }
    else
    {
        m_parserVideoName.clear();
    }

    if (audio_supported)
    {
        m_sourceAudio = gst_element_factory_make("appsrc", nullptr);
        if (!m_sourceAudio)
        {
            LOG(error) << "Gstreamer Audio Source element creation failed" << endl;
            destroyPipelineInternal();
            return false;
        }

        // Audio codec handling
        std::string audio_codec = m_config.getParameter(StorageConstants::AUDIO_CODEC_KEY, "pcmu");
        bool audio_enable = m_config.getParameter(StorageConstants::AUDIO_SUPPORTED_KEY, "true") == "true";
        std::string audio_container = m_config.getParameter(StorageConstants::AUDIO_CONTAINER_KEY, "");
        int codec_data = std::stoi(m_config.getParameter(StorageConstants::CODEC_DATA_KEY, "1410"));

        if (!audio_enable)
        {
            audio_supported = false;
        }
        else if (iequals(audio_codec, "pcmu") || iequals(audio_codec, "pcma"))
        {
            m_parserAudio = gst_element_factory_make("rawaudioparse", nullptr);
        }
        else if (iequals(audio_codec, "mpeg4-generic"))
        {
            m_parserAudio = gst_element_factory_make("aacparse", nullptr);
            std::string caps_string = string(
                                          "audio/mpeg, mpegversion=(int)4, \
                                    stream-format=(string)raw, \
                                    codec_data=(buffer)") +
                                      to_string(codec_data);
            GstCaps* caps_before_dec = gst_caps_from_string(caps_string.c_str());
            if (caps_before_dec)
            {
                g_object_set(G_OBJECT(m_sourceAudio), "caps", caps_before_dec, nullptr);
                gst_caps_unref(caps_before_dec);
            }
        }
        else
        {
            audio_codec = "pcmu";
            m_parserAudio = gst_element_factory_make("rawaudioparse", nullptr);
        }

        if (!m_parserAudio)
        {
            LOG(error) << "Gstreamer Audio Parser element creation failed" << endl;
            destroyPipelineInternal();
            return false;
        }
    }

    m_mux = gst_element_factory_make("matroskamux", nullptr);
    m_sink = createSinkElement(); // Create filesink or appsink

    // Check if any element failed to create
    if (!m_pipeline || !m_sourceVideo || !m_parserVideo || !m_capsfilterVideo || !m_mux || !m_sink)
    {
        LOG(error) << "Gstreamer element creation failed" << endl;
        destroyPipelineInternal();
        return false;
    }

    // Configure matroskamux for better compatibility with growing file reading
    // min-index-interval: Write Cue (index) entries more frequently for better seek granularity
    if (m_mux)
    {
        g_object_set(G_OBJECT(m_mux),
                     "min-index-interval", (guint64)(500 * GST_MSECOND),  // Cue entry every 500ms
                     nullptr);
        LOG(info) << "Configured matroskamux with min-index-interval=500ms" << endl;
    }

    // Set format property of appsrc element
    g_object_set(m_sourceVideo, "format", GST_FORMAT_TIME, nullptr);
    g_object_set(m_sourceVideo, "is-live", true, nullptr);
    g_object_set(m_sourceVideo, "do-timestamp", true, nullptr);

    // Configure video source caps if needed (H.264/H.265)
    if (!configureVideoSourceCaps())
    {
        LOG(error) << "Failed to configure video source caps for codec " << video_codec << " stream: " << stream_id << endl;
        destroyPipelineInternal();
        return false;
    }
    
    // Add probe to remove framerate field from caps
    GstPad* srcpad = gst_element_get_static_pad(GST_ELEMENT(G_OBJECT(m_parserVideo)), "src");
    m_probe_id = gst_pad_add_probe(srcpad, GST_PAD_PROBE_TYPE_EVENT_BOTH, event_probe, (void*)this, nullptr);
    gst_object_unref(srcpad);

    if (audio_supported)
    {
        gst_bin_add_many(GST_BIN(m_pipeline), m_sourceVideo, m_sourceAudio, m_parserVideo, m_parserAudio, m_mux, m_sink,
                         nullptr);

        g_object_set(m_sourceAudio, "format", GST_FORMAT_TIME, nullptr);
        g_object_set(m_sourceAudio, "is-live", true, nullptr);
        g_object_set(m_sourceAudio, "do-timestamp", true, nullptr);

        // Audio codec specific configuration
        std::string audio_codec = m_config.getParameter("audio_codec", "pcmu");
        if (iequals(audio_codec, "pcmu"))
        {
            g_object_set(m_parserAudio, "format", AUDIO_CODEC_FORMAT_MULAW, nullptr);
            g_object_set(m_parserAudio, "pcm-format", AUDIO_PCM_FORMAT_S8, nullptr);
        }
        else if (iequals(audio_codec, "pcma"))
        {
            g_object_set(m_parserAudio, "format", AUDIO_CODEC_FORMAT_ALAW, nullptr);
            g_object_set(m_parserAudio, "pcm-format", AUDIO_PCM_FORMAT_S8, nullptr);
        }

        // Channel & sample rate properties
        if (iequals(audio_codec, "pcmu") || iequals(audio_codec, "pcma"))
        {
            // Get frequency and channel from config, with enhanced audio parameters
            int frequency = std::stoi(m_config.getParameter("audio_sample_rate", "8000"));
            int channel = std::stoi(m_config.getParameter("audio_channels", "1"));

            g_object_set(m_parserAudio, "sample-rate", frequency, nullptr);
            g_object_set(m_parserAudio, "num-channels", channel, nullptr);
        }

        GstPad *sink_pad, *src_pad;

        src_pad = gst_element_get_static_pad(m_parserAudio, "src");
        sink_pad = gst_element_request_pad_simple(m_mux, "audio_%u");
        if (src_pad && sink_pad)
        {
            if (GST_PAD_LINK_OK != gst_pad_link(src_pad, sink_pad))
            {
                LOG(error) << "Could not link Audio parser & Muxer" << endl;
                gst_object_unref(sink_pad);
                gst_object_unref(src_pad);
                destroyPipelineInternal();
                return false;
            }
        }

        src_pad = gst_element_get_static_pad(m_parserVideo, "src");
        sink_pad = gst_element_request_pad_simple(m_mux, "video_%u");
        if (src_pad && sink_pad)
        {
            if (GST_PAD_LINK_OK != gst_pad_link(src_pad, sink_pad))
            {
                LOG(error) << "Could not link Video parser & Muxer" << endl;
                gst_object_unref(sink_pad);
                gst_object_unref(src_pad);
                destroyPipelineInternal();
                return false;
            }
        }

        // Check if elements are linked to each other
        if (!gst_element_link_many(m_sourceVideo, m_parserVideo, nullptr))
        {
            LOG(error) << "Elements could not be linked" << endl;
            destroyPipelineInternal();
            return false;
        }

        if (!gst_element_link_many(m_sourceAudio, m_parserAudio, nullptr))
        {
            LOG(error) << "Elements could not be linked" << endl;
            destroyPipelineInternal();
            return false;
        }

        if (!gst_element_link_many(m_mux, m_sink, nullptr))
        {
            LOG(error) << "Elements could not be linked" << endl;
            destroyPipelineInternal();
            return false;
        }

        // Unref the data structure
        if (sink_pad)
        {
            gst_object_unref(sink_pad);
        }
        if (src_pad)
        {
            gst_object_unref(src_pad);
        }
    }
    else
    {
        gst_bin_add_many(GST_BIN(m_pipeline), m_sourceVideo, m_parserVideo, m_capsfilterVideo, m_mux, m_sink, nullptr);

        // Check if elements are linked to each other
        if (!gst_element_link_many(m_sourceVideo, m_parserVideo, m_capsfilterVideo, m_mux, m_sink, nullptr))
        {
            LOG(error) << "Elements could not be linked" << endl;
            destroyPipelineInternal();
            return false;
        }
    }

    // Initialize GMainLoop
    m_mainContext = g_main_context_new();
    if (!m_mainContext)
    {
        LOG(error) << "Failed to create main context" << endl;
        destroyPipelineInternal();
        return false;
    }

    m_mainLoop = g_main_loop_new(m_mainContext, FALSE);
    if (!m_mainLoop)
    {
        g_main_context_unref(m_mainContext);
        m_mainContext = nullptr;
        LOG(error) << "Failed to create main loop" << endl;
        destroyPipelineInternal();
        return false;
    }

    // Start GMainLoop thread
    string gthread_name = "gmainloop_" + stream_id;
    m_gmainLoopThread = g_thread_new(gthread_name.c_str(), gmainLoopthread, this);
    if (!m_gmainLoopThread)
    {
        g_main_loop_unref(m_mainLoop);
        g_main_context_unref(m_mainContext);
        m_mainLoop = nullptr;
        m_mainContext = nullptr;
        LOG(error) << "Failed to create main loop thread" << endl;
        destroyPipelineInternal();
        return false;
    }

    // Wait for main loop to start
    int try_count = 0;
    while (m_mainLoop && (g_main_loop_is_running(m_mainLoop) == false) && try_count <= MAX_GMAIN_LOOP_START_RETRY)
    {
        if (try_count == MAX_GMAIN_LOOP_START_RETRY)
        {
            LOG(error) << "Failed to start main loop" << endl;
            destroyPipelineInternal();
            return false;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(MAX_GMAIN_LOOP_START_WAIT));
        try_count++;
    }

    m_pipeline_ready = true;

    if (m_audioSupported)
    {
        LOG(info) << "StorageWriter Pipeline created successfully for stream: " << stream_id
                  << " video_codec: " << video_codec << " audio_supported: " << m_audioSupported << " audio_codec: " << m_audioCodec << endl;
    }
    else
    {
        LOG(info) << "StorageWriter Pipeline created successfully for stream: " << stream_id
                  << " video_codec: " << video_codec << " audio_supported: " << m_audioSupported << endl;
    }
    return true;
}

bool UnifiedStorageWriter::destroyPipeline()
{
    std::lock_guard<std::mutex> lock(m_pipeline_mutex);
    return destroyPipelineInternal();
}

bool UnifiedStorageWriter::destroyPipelineInternal()
{
    // Store stream ID before clearing it for logging purposes
    std::string stream_id = m_streamId;

    // Check if pipeline is already destroyed
    if (!m_pipeline)
    {
        LOG(info) << "Pipeline already destroyed for stream ID: " << stream_id << endl;
        return true;
    }

    // Mark session as inactive to prevent further operations
    m_session_active = false;

    // Clean up session state without calling stopWrite (which could cause recursion)
    if (!m_current_session_id.empty())
    {
        cleanupSession(m_current_session_id);
        m_current_session_id.clear();
        m_current_remote_path.clear();
    }

    // First remove pad probe if it exists
    if (m_parserVideo && m_probe_id > 0)
    {
        GstPad* srcpad = gst_element_get_static_pad(m_parserVideo, "src");
        if (srcpad)
        {
            gst_pad_remove_probe(srcpad, m_probe_id);
            gst_object_unref(srcpad);
            m_probe_id = 0;
        }
    }

    // Stop the pipeline first and wait for state change
    if (m_pipeline)
    {
        GstStateChangeReturn ret = gst_element_set_state(m_pipeline, GST_STATE_NULL);
        if (ret == GST_STATE_CHANGE_ASYNC)
        {
            // Wait for state change to complete
            ret = gst_element_get_state(m_pipeline, nullptr, nullptr, 5 * GST_SECOND);
            if (ret != GST_STATE_CHANGE_SUCCESS)
            {
                LOG(error) << "Failed to change pipeline state to NULL for stream ID: " << stream_id << endl;
            }
        }
    }

    // Unlink elements before unref
    if (m_pipeline)
    {
        if (m_sourceVideo && m_parserVideo)
        {
            gst_element_unlink(m_sourceVideo, m_parserVideo);
        }
        if (m_parserVideo && m_capsfilterVideo)
        {
            gst_element_unlink(m_parserVideo, m_capsfilterVideo);
        }
        if (m_capsfilterVideo && m_mux)
        {
            gst_element_unlink(m_capsfilterVideo, m_mux);
        }
        if (m_parserVideo && m_mux)
        {
            gst_element_unlink(m_parserVideo, m_mux);
        }
        if (m_sourceAudio && m_parserAudio)
        {
            gst_element_unlink(m_sourceAudio, m_parserAudio);
        }
        if (m_parserAudio && m_mux)
        {
            gst_element_unlink(m_parserAudio, m_mux);
        }
        if (m_mux && m_sink)
        {
            gst_element_unlink(m_mux, m_sink);
        }
    }

    // Remove and unref elements from pipeline with proper error handling
    if (m_pipeline)
    {
        // Remove elements from pipeline before unreffing
        if (m_sourceVideo)
        {
            gchar* name = gst_object_get_name(GST_OBJECT(m_sourceVideo));
            if (name && gst_bin_get_by_name(GST_BIN(m_pipeline), name))
            {
                gst_bin_remove(GST_BIN(m_pipeline), m_sourceVideo);
            }
            g_free(name);
            gst_object_unref(m_sourceVideo);
            m_sourceVideo = nullptr;
        }

        if (m_parserVideo)
        {
            gchar* name = gst_object_get_name(GST_OBJECT(m_parserVideo));
            if (name && gst_bin_get_by_name(GST_BIN(m_pipeline), name))
            {
                gst_bin_remove(GST_BIN(m_pipeline), m_parserVideo);
            }
            g_free(name);
            gst_object_unref(m_parserVideo);
            m_parserVideo = nullptr;
        }

        if (m_capsfilterVideo)
        {
            gchar* name = gst_object_get_name(GST_OBJECT(m_capsfilterVideo));
            if (name && gst_bin_get_by_name(GST_BIN(m_pipeline), name))
            {
                gst_bin_remove(GST_BIN(m_pipeline), m_capsfilterVideo);
            }
            g_free(name);
            gst_object_unref(m_capsfilterVideo);
            m_capsfilterVideo = nullptr;
        }

        if (m_sourceAudio)
        {
            gchar* name = gst_object_get_name(GST_OBJECT(m_sourceAudio));
            if (name && gst_bin_get_by_name(GST_BIN(m_pipeline), name))
            {
                gst_bin_remove(GST_BIN(m_pipeline), m_sourceAudio);
            }
            g_free(name);
            gst_object_unref(m_sourceAudio);
            m_sourceAudio = nullptr;
        }

        if (m_parserAudio)
        {
            gchar* name = gst_object_get_name(GST_OBJECT(m_parserAudio));
            if (name && gst_bin_get_by_name(GST_BIN(m_pipeline), name))
            {
                gst_bin_remove(GST_BIN(m_pipeline), m_parserAudio);
            }
            g_free(name);
            gst_object_unref(m_parserAudio);
            m_parserAudio = nullptr;
        }

        if (m_mux)
        {
            gchar* name = gst_object_get_name(GST_OBJECT(m_mux));
            if (name && gst_bin_get_by_name(GST_BIN(m_pipeline), name))
            {
                gst_bin_remove(GST_BIN(m_pipeline), m_mux);
            }
            g_free(name);
            gst_object_unref(m_mux);
            m_mux = nullptr;
        }

        if (m_sink)
        {
            gchar* name = gst_object_get_name(GST_OBJECT(m_sink));
            if (name && gst_bin_get_by_name(GST_BIN(m_pipeline), name))
            {
                gst_bin_remove(GST_BIN(m_pipeline), m_sink);
            }
            g_free(name);
            gst_object_unref(m_sink);
            m_sink = nullptr;
        }
    }

    // Unref pipeline last since it owns all elements
    if (m_pipeline)
    {
        gst_object_unref(m_pipeline);
        m_pipeline = nullptr;
    }

    // Stop and cleanup main loop
    if (m_mainLoop && g_main_loop_is_running(m_mainLoop))
    {
        g_main_loop_quit(m_mainLoop);
    }

    if (m_gmainLoopThread)
    {
        LOG(info) << "Joining main loop thread for stream ID: " << stream_id << " thread ID: " << m_gmainLoopThread
                  << endl;
        g_thread_join(m_gmainLoopThread);
        m_gmainLoopThread = nullptr;
    }

    // Cleanup main loop resources
    if (m_mainLoop)
    {
        g_main_loop_unref(m_mainLoop);
        m_mainLoop = nullptr;
    }
    if (m_mainContext)
    {
        g_main_context_unref(m_mainContext);
        m_mainContext = nullptr;
    }

    // Cleanup bus watch source
    if (m_gSource)
    {
        g_source_unref(m_gSource);
        m_gSource = nullptr;
    }

    // Reset all parameters
    m_width = 0;
    m_height = 0;
    m_numerator = 0;
    m_denominator = 0;
    if (m_format)
    {
        free(m_format);
        m_format = nullptr;
    }

    // Reset session state
    m_session_active = false;
    m_current_session_id.clear();
    m_current_remote_path.clear();
    m_streamId.clear();

    // Reset pipeline state
    m_pipeline_ready = false;
    m_eosReceived = false;

    // Reset statistics
    m_total_bytes_written = 0;
    m_frames_written = 0;

    // Clear any pending errors
    clearLastError();

    LOG(warning) << "Terminated storage writer pipeline for stream ID: " << stream_id << endl;
    return true;
}

bool UnifiedStorageWriter::setPipelineState(GstState state)
{
    if (!m_pipeline)
    {
        LOG(error) << "Pipeline is null, cannot set state for stream ID: "
                   << (m_streamId.empty() ? "unknown" : m_streamId) << endl;
        return false;
    }

    // Check if pipeline is still valid
    if (!GST_IS_ELEMENT(m_pipeline))
    {
        LOG(error) << "Pipeline is not a valid GStreamer element for stream ID: "
                   << (m_streamId.empty() ? "unknown" : m_streamId) << endl;
        return false;
    }

    GstStateChangeReturn gstStateChangeRet = GST_STATE_CHANGE_FAILURE;
    bool ret = true;

    /* Check if pipeline is already in target state */
    GstState current_state, pending_state;
    gst_element_get_state(m_pipeline, &current_state, &pending_state, 0);
    if (current_state == state)
    {
        LOG(verbose) << "Pipeline already in target state: " << gst_element_state_get_name(state)
                  << " for stream ID = " << m_streamId << endl;
        return true;
    }

    /* Setting Pipeline to target state */
    gstStateChangeRet = gst_element_set_state(m_pipeline, state);

    /* handling returns of above API */
    if (gstStateChangeRet == GST_STATE_CHANGE_FAILURE)
    {
        LOG(error) << "gst_element_set_state (for " << gst_element_state_get_name(state)
                   << " state) failed for stream ID = " << m_streamId << endl;
        setLastError("Pipeline state change failed");
        ret = false;
    }
    /* pipeline will be put into target state from other thread */
    else if (gstStateChangeRet == GST_STATE_CHANGE_ASYNC)
    {
        LOG(info) << "GST_STATE_CHANGE_ASYNC (for " << gst_element_state_get_name(state)
                  << " state) for stream ID = " << m_streamId << endl;
    }
    /* success case */
    else
    {
        LOG(info) << "State change success in " << gst_element_state_get_name(state)
                  << " state for stream ID = " << m_streamId << endl;
    }

    return ret;
}

bool UnifiedStorageWriter::isPipelineReady() const
{
    return m_pipeline_ready.load();
}

bool UnifiedStorageWriter::resetPipeline()
{
    std::string current_stream_id = m_streamId; // Store before potential clearing
    LOG(info) << "Resetting pipeline for stream ID = " << (current_stream_id.empty() ? "unknown" : current_stream_id)
              << endl;

    // Use destroyPipeline to handle all cleanup and reset operations
    if (m_pipeline)
    {
        LOG(info) << "Destroying current pipeline for reset for stream ID = "
                  << (current_stream_id.empty() ? "unknown" : current_stream_id) << endl;
        destroyPipeline();
    }

    // // Clear session buffers for cloud storage (not handled by destroyPipeline)
    // if (m_storageMode == StorageType::CLOUD) {
    //     UnifiedCloudStorageWriter* cloudWriter = dynamic_cast<UnifiedCloudStorageWriter*>(this);
    //     if (cloudWriter) {
    //         cloudWriter->clearSessionBuffer(m_current_session_id);
    //     }
    // }

    return createPipeline(m_videoCodec, m_audioSupported, current_stream_id);
}

bool UnifiedStorageWriter::pushBufferToPipeline(const void* data, size_t size, int64_t pts,
                                                const std::string& media_type)
{
    // Validate input parameters to prevent crashes
    if (!data || size == 0)
    {
        LOG(error) << "Invalid buffer data or size for stream ID: " << (m_streamId.empty() ? "unknown" : m_streamId)
                   << endl;
        return false;
    }

    // Check for unreasonably large buffer size to prevent string allocation errors
    const size_t MAX_BUFFER_SIZE = 100ULL * 1024ULL * 1024ULL; // 100MB limit
    if (size > MAX_BUFFER_SIZE)
    {
        LOG(error) << "Buffer size too large: " << size
                   << " bytes for stream ID: " << (m_streamId.empty() ? "unknown" : m_streamId) << endl;
        return false;
    }

#ifdef UNIFIED_STORAGE_WRITER_UNIT_TEST
    // TEST: Check if pushBuffer failure is simulated
    if (m_test_pushbuffer_fail.load())
    {
        LOG(error) << "TEST: pushBuffer FAILING due to test flag for stream ID: "
                   << (m_streamId.empty() ? "unknown" : m_streamId) << endl;
        return false;
    }
#endif

    if (!m_pipeline_ready.load())
    {
        LOG(warning) << "Pipeline not ready for buffer push, stream ID: "
                     << (m_streamId.empty() ? "unknown" : m_streamId) << endl;
        return false;
    }

    GstState current, pending;
    GstBuffer* gstbuffer = nullptr;
    GstMapInfo map;
    gboolean map_ret = false;
    int64_t bufferPTS = 0;
    GstFlowReturn ret = GST_FLOW_OK;
    bool buffer_owned_by_pipeline = false; // Track buffer ownership

    auto startTime = std::chrono::steady_clock::now();
    while (std::chrono::steady_clock::now() - startTime < std::chrono::seconds(1))
    {
        gst_element_get_state(m_pipeline, &current, &pending, 10 * GST_MSECOND);
        if (current != GST_STATE_NULL)
        {
            break;
        }
    }

    if (current != GST_STATE_NULL)
    {
        try
        {
            gstbuffer = gst_buffer_new_allocate(nullptr, size, nullptr);
            if (gstbuffer == nullptr)
            {
                LOG(error) << "gst_buffer_new_allocate failed" << endl;
                return false;
            }

            map_ret = gst_buffer_map(gstbuffer, &map, GST_MAP_WRITE);
            if (!map_ret)
            {
                LOG(error) << "gst_buffer_map failed" << endl;
                gst_buffer_unref(gstbuffer);
                return false;
            }

            // Bounds check and safe memory copy (inputs already validated above)
            if (size > map.size)
            {
                LOG(error) << "Buffer overflow prevented: frame_size(" << size 
                           << ") > buffer_size(" << map.size << ")" << endl;
                gst_buffer_unmap(gstbuffer, &map);
                gst_buffer_unref(gstbuffer);
                return false;
            }
            
            // Safe memory copy using memmove (overlap-safe)
            memmove(map.data, data, size);
            map.size = size;
            gst_buffer_unmap(gstbuffer, &map);

            // Set PTS and DTS for the buffer
            bufferPTS = pts;
            GST_BUFFER_PTS(gstbuffer) = bufferPTS * 1000 * 1000;
            GST_BUFFER_DTS(gstbuffer) = GST_BUFFER_PTS(gstbuffer);

            if (media_type == "video")
            {
                ret = gst_app_src_push_buffer(GST_APP_SRC(m_sourceVideo), gstbuffer);
                if (ret != GST_FLOW_OK)
                {
                    LOG(error) << "Failed to push buffer in video appsrc queue" << endl;
                    gst_buffer_unref(gstbuffer);
                    return false;
                }
                buffer_owned_by_pipeline = true; // Pipeline now owns the buffer
            }
            else if (media_type == "audio" && m_audioSupported)
            {
                ret = gst_app_src_push_buffer(GST_APP_SRC(m_sourceAudio), gstbuffer);
                if (ret != GST_FLOW_OK)
                {
                    LOG(error) << "Failed to push buffer in audio appsrc queue" << endl;
                    gst_buffer_unref(gstbuffer);
                    return false;
                }
                buffer_owned_by_pipeline = true; // Pipeline now owns the buffer
            }

            m_total_bytes_written.fetch_add(size, std::memory_order_relaxed);
            m_frames_written.fetch_add(1, std::memory_order_relaxed);

            return true;
        }
        catch (...)
        {
            // Exception safety: clean up buffer if not transferred to pipeline
            if (gstbuffer && !buffer_owned_by_pipeline)
            {
                gst_buffer_unref(gstbuffer);
                gstbuffer = nullptr;
            }
            throw;
        }
    }

    return false;
}

bool UnifiedStorageWriter::sendEOS()
{
    std::string current_stream_id = m_streamId; // Store before potential clearing
    LOG(info) << "Sending EOS in UnifiedStorageWriter pipeline for stream id = "
              << (current_stream_id.empty() ? "unknown" : current_stream_id) << endl;
    /* Need to reset the flag to make sure EOS is received */
    m_eosReceived = false;
    bool ret_eos = true;
    GstFlowReturn ret = gst_app_src_end_of_stream(GST_APP_SRC(m_sourceVideo));
    if (ret != GST_FLOW_OK)
    {
        LOG(error) << "Failed to send EOS event in Video Pipeline for stream id = "
                   << (current_stream_id.empty() ? "unknown" : current_stream_id) << endl;
        ret_eos = false;
        goto exit;
    }
    if (m_audioSupported && m_sourceAudio)
    {
        ret = gst_app_src_end_of_stream(GST_APP_SRC(m_sourceAudio));
        if (ret != GST_FLOW_OK)
        {
            LOG(error) << "Failed to send EOS event in Audio Pipeline for stream id = "
                       << (current_stream_id.empty() ? "unknown" : current_stream_id) << endl;
            ret_eos = false;
            goto exit;
        }
    }
    {
        auto until = std::chrono::system_clock::now() + std::chrono::seconds(3);
        std::unique_lock<std::mutex> lock(m_monitor_eos_mutex);
        if (m_monitor_eos_cv.wait_until(lock, until, [this] { return (m_eosReceived.load()); }) == false)
        {
            LOG(error) << "Timed out while waiting for EOS signal for stream id = "
                       << (current_stream_id.empty() ? "unknown" : current_stream_id) << endl;
            /* No need to process return of below function as EOS is not received, return of this function is false */
            ret_eos = false;
            goto exit;
        }
        m_eosReceived = false;
    }
exit:
    return ret_eos;
}

bool UnifiedStorageWriter::waitForEOSMessage(int timeout_ms)
{
    auto until = std::chrono::system_clock::now() + std::chrono::milliseconds(timeout_ms);
    std::unique_lock<std::mutex> lock(m_monitor_eos_mutex);
    return m_monitor_eos_cv.wait_until(lock, until, [this] { return m_eosReceived.load(); });
}

bool UnifiedStorageWriter::startSession(const std::string& session_id, const std::string& remote_path)
{
    std::string current_stream_id = m_streamId; // Store before potential clearing
    LOG(info) << "Starting session " << session_id << " with path: " << remote_path
              << " for stream ID: " << (current_stream_id.empty() ? "unknown" : current_stream_id) << endl;

    // Configure sink element for this session
    if (!configureSinkElement(m_sink, remote_path, session_id))
    {
        setLastError("Failed to configure sink element");
        LOG(error) << "Failed to configure sink element for session: " << session_id
                   << " stream ID: " << (current_stream_id.empty() ? "unknown" : current_stream_id) << endl;
        destroyPipeline();
        return false;
    }

    // Set pipeline to PLAYING state
    if (!setPipelineState(GST_STATE_PLAYING))
    {
        setLastError("Failed to set pipeline to playing state");
        LOG(error) << "Failed to set pipeline to playing state for session: " << session_id
                   << " stream ID: " << (current_stream_id.empty() ? "unknown" : current_stream_id) << endl;
        destroyPipeline();
        return false;
    }

    LOG(info) << "Session started successfully: " << session_id << endl;
    return true;
}

bool UnifiedStorageWriter::reusePipelineForNewFile(const std::string& remote_path)
{
    std::string current_stream_id = m_streamId; // Store before potential clearing

    // Check if pipeline exists
    if (!m_pipeline)
    {
        setLastError("Pipeline is null, cannot reuse");
        LOG(error) << "Pipeline is null, cannot reuse for new file: " << remote_path
                   << " stream ID: " << (current_stream_id.empty() ? "unknown" : current_stream_id) << endl;
        return false;
    }

    // Check if sink element exists
    if (!m_sink)
    {
        setLastError("Sink element is null, cannot reuse");
        LOG(error) << "Sink element is null, cannot reuse for new file: " << remote_path
                   << " stream ID: " << (current_stream_id.empty() ? "unknown" : current_stream_id) << endl;
        return false;
    }

    // For filesink, we need to stop the pipeline to change the location
    // This is because filesink doesn't support dynamic location changes
    if (!setPipelineState(GST_STATE_NULL))
    {
        setLastError("Failed to set pipeline to null state for reuse");
        LOG(error) << "Failed to set pipeline to null state for reuse, stream ID: "
                   << (current_stream_id.empty() ? "unknown" : current_stream_id) << endl;
        destroyPipeline();
        return false;
    }

    // Reset video caps on appsrc for proper negotiation during pipeline reuse
    if (!configureVideoSourceCaps())
    {
        setLastError("Failed to configure video source caps for pipeline reuse");
        LOG(error) << "Failed to configure video source caps for codec " << m_videoCodec 
                   << " during pipeline reuse for stream ID: "
                   << (current_stream_id.empty() ? "unknown" : current_stream_id) << endl;
        destroyPipeline();
        return false;
    }

    // Configure sink element for new file
    if (!configureSinkElement(m_sink, remote_path, m_current_session_id))
    {
        setLastError("Failed to configure sink element for new file");
        LOG(error) << "Failed to configure sink element for new file: " << remote_path
                   << " stream ID: " << (current_stream_id.empty() ? "unknown" : current_stream_id) << endl;
        destroyPipeline();
        return false;
    }

    // Set pipeline back to PLAYING state
    if (!setPipelineState(GST_STATE_PLAYING))
    {
        setLastError("Failed to set pipeline to playing state for new file");
        LOG(error) << "Failed to set pipeline to playing state for new file: " << remote_path
                   << " stream ID: " << (current_stream_id.empty() ? "unknown" : current_stream_id) << endl;
        destroyPipeline();
        return false;
    }

    LOG(verbose) << "Pipeline reused successfully for new file: " << remote_path
              << " stream ID: " << (current_stream_id.empty() ? "unknown" : current_stream_id) << endl;
    return true;
}

bool UnifiedStorageWriter::stopSession(const std::string& session_id)
{
    std::string current_stream_id = m_streamId; // Store before potential clearing
    LOG(info) << "Stopping session: " << session_id
              << " for stream ID: " << (current_stream_id.empty() ? "unknown" : current_stream_id) << endl;

    // Check if pipeline is still valid before attempting state change
    if (bool isPipelineValidForStop = m_pipeline ? GST_IS_ELEMENT(m_pipeline) : false; !isPipelineValidForStop)
    {
        LOG(warning) << "Pipeline is not valid, skipping state change for session: " << session_id
                     << " stream ID: " << (current_stream_id.empty() ? "unknown" : current_stream_id) << endl;
        return true; // Consider this a successful stop since pipeline is already gone
    }

    // Set pipeline to NULL state
    if (!setPipelineState(GST_STATE_NULL))
    {
        setLastError("Failed to set pipeline to null state");
        destroyPipeline();
        return false;
    }

    return true;
}

// Helper methods
void UnifiedStorageWriter::setLastError(const std::string& error)
{
    // Validate error message to prevent string allocation issues
    if (error.empty())
    {
        LOG(warning) << "Attempted to set empty error message" << endl;
        return;
    }

    // Check for unreasonably long error messages
    const size_t MAX_ERROR_LENGTH = 8192; // 8KB limit
    if (error.length() > MAX_ERROR_LENGTH)
    {
        LOG(warning) << "Error message too long, truncating: " << error.length() << " chars" << endl;
        std::lock_guard<std::mutex> lock(m_error_mutex);
        m_last_error = error.substr(0, MAX_ERROR_LENGTH) + " [TRUNCATED]";
        return;
    }

    std::lock_guard<std::mutex> lock(m_error_mutex);
    m_last_error = error;
}

void UnifiedStorageWriter::setVideoMetadata(int width, int height, int numerator, int denominator, const char* format)
{
    m_width = width;
    m_height = height;
    m_numerator = numerator;
    m_denominator = denominator;
    if (m_format)
    {
        free(m_format);
    }
    m_format = format ? strdup(format) : nullptr;
}

void UnifiedStorageWriter::setResolution(const std::string& resolution)
{
    m_resolution = resolution;
}

void UnifiedStorageWriter::setMaxAllowedFrameDiff(int diff)
{
    m_maxAllowedFrameDiff = diff;
}

void UnifiedStorageWriter::clearLastError()
{
    std::lock_guard<std::mutex> lock(m_error_mutex);
    m_last_error.clear();
}

bool UnifiedStorageWriter::configureVideoSourceCaps()
{
    // Check if codec requires caps configuration
    if (!iequals(m_videoCodec, "h264") && !iequals(m_videoCodec, "h265"))
    {
        return true; // No caps configuration needed for other codecs
    }
    
    if (!m_sourceVideo)
    {
        LOG(error) << "Video source element is null, cannot configure " << m_videoCodec 
                   << " caps for stream ID: " << (m_streamId.empty() ? "unknown" : m_streamId) << endl;
        return false;
    }
    
    // Set default framerate if not already configured
    if (m_numerator == 0 || m_denominator == 0)
    {
        m_numerator = DEFAULT_VIDEO_FRAMERATE_NUM;
        m_denominator = DEFAULT_VIDEO_FRAMERATE_DEN;
    }
    
    // Create caps based on codec type (ensure lowercase for GStreamer)
    std::string codec_lowercase = m_videoCodec;
    std::transform(codec_lowercase.begin(), codec_lowercase.end(), codec_lowercase.begin(), ::tolower);
    std::string caps_name = "video/x-" + codec_lowercase;
    // Declare the appsrc input as single-NAL Annex-B
    // byte-stream so h26xparse takes ownership of access-unit framing and
    // aggregates multi-slice pictures into one output buffer per AU. Without
    // these fields h26xparse autodetects alignment=au from the first buffer
    // and then treats every subsequent input buffer as a complete AU.
    GstCaps* video_caps = gst_caps_new_simple(caps_name.c_str(),
                                            "stream-format", G_TYPE_STRING, "byte-stream",
                                            "alignment",     G_TYPE_STRING, "nal",
                                            "framerate", GST_TYPE_FRACTION, m_numerator, m_denominator,
                                            NULL);
    if (!video_caps)
    {
        LOG(error) << "Failed to create " << m_videoCodec << " video caps (" << caps_name 
                   << ") for stream ID: " << (m_streamId.empty() ? "unknown" : m_streamId) << endl;
        return false;
    }
    
    g_object_set(m_sourceVideo, "caps", video_caps, nullptr);
    gst_caps_unref(video_caps);
    
    LOG(verbose) << "Configured " << m_videoCodec << " video caps (" << caps_name 
                 << ") with framerate " << m_numerator << "/" << m_denominator 
                 << " for stream ID: " << (m_streamId.empty() ? "unknown" : m_streamId) << endl;
    
    return true;
}

GstFlowReturn UnifiedStorageWriter::onNewSample(GstAppSink* appsink, gpointer user_data)
{
    // Not used in base class - implemented by derived classes
    return GST_FLOW_OK;
}

void UnifiedStorageWriter::onEOS(GstAppSink* appsink, gpointer user_data)
{
    UnifiedStorageWriter* writer = static_cast<UnifiedStorageWriter*>(user_data);

    LOG(info) << "Received EOS from appsink" << endl;

    // Set EOS received flag
    {
        std::lock_guard<std::mutex> lock(writer->m_monitor_eos_mutex);
        writer->m_eosReceived = true;
        writer->m_monitor_eos_cv.notify_all();
    }
}

gboolean UnifiedStorageWriter::onBusMessage(GstBus* bus, GstMessage* message, gpointer user_data)
{
    UnifiedStorageWriter* writer = static_cast<UnifiedStorageWriter*>(user_data);

    if (!message)
    {
        return TRUE;
    }

    switch (GST_MESSAGE_TYPE(message))
    {
        case GST_MESSAGE_ERROR:
        {
            GError* error = nullptr;
            gchar* debug = nullptr;
            gst_message_parse_error(message, &error, &debug);

            LOG(error) << "GStreamer error: " << (error ? error->message : "Unknown error") << endl;
            if (debug)
            {
                LOG(error) << "Debug info: " << debug << endl;
                g_free(debug);
            }
            if (error)
            {
                g_error_free(error);
            }

            writer->setLastError("GStreamer pipeline error");
            break;
        }
        case GST_MESSAGE_WARNING:
        {
            GError* error = nullptr;
            gchar* debug = nullptr;
            gst_message_parse_warning(message, &error, &debug);

            LOG(warning) << "GStreamer warning: " << (error ? error->message : "Unknown warning") << endl;
            if (debug)
            {
                g_free(debug);
            }
            if (error)
            {
                g_error_free(error);
            }
            break;
        }
        case GST_MESSAGE_INFO:
        {
            GError* error = nullptr;
            gchar* debug = nullptr;
            gst_message_parse_info(message, &error, &debug);

            if (debug)
            {
                g_free(debug);
            }
            if (error)
            {
                g_error_free(error);
            }
            break;
        }
        case GST_MESSAGE_STATE_CHANGED:
        {
            GstState old_state, new_state, pending_state;
            gst_message_parse_state_changed(message, &old_state, &new_state, &pending_state);

            // Only log state changes for the pipeline itself, not individual elements
            if (GST_MESSAGE_SRC(message) == GST_OBJECT(writer->getPipeline()))
            {
                // Handle specific state transitions
                if (new_state == GST_STATE_NULL)
                {
                    LOG(info) << "Pipeline stopped for stream ID: "
                              << (writer->m_streamId.empty() ? "unknown" : writer->m_streamId) << endl;
                }
                else if (new_state == GST_STATE_PLAYING)
                {
                    LOG(info) << "Pipeline started for stream ID: "
                              << (writer->m_streamId.empty() ? "unknown" : writer->m_streamId) << endl;
                }
            }
            break;
        }
        case GST_MESSAGE_ASYNC_DONE:
            LOG(info) << "GStreamer async done for stream ID: "
                      << (writer->m_streamId.empty() ? "unknown" : writer->m_streamId) << endl;
            break;
        case GST_MESSAGE_EOS:
            LOG(info) << "GStreamer EOS for stream ID: "
                      << (writer->m_streamId.empty() ? "unknown" : writer->m_streamId) << endl;
            // Set EOS received flag for both local and cloud storage
            {
                std::lock_guard<std::mutex> lock(writer->m_monitor_eos_mutex);
                writer->m_eosReceived = true;
                writer->m_monitor_eos_cv.notify_all();
            }
            break;
        default:
            break;
    }

    return TRUE;
}

// GStreamer callback functions
GstPadProbeReturn event_probe(GstPad* pad, GstPadProbeInfo* info, gpointer user_data)
{
    UnifiedStorageWriter* writer = static_cast<UnifiedStorageWriter*>(user_data);
    GstEvent* event = GST_PAD_PROBE_INFO_EVENT(info);

    if (event && GST_EVENT_CAPS == GST_EVENT_TYPE(event))
    {
        GstCaps* caps;
        gst_event_parse_caps(event, &caps);
        GstStructure* gstStruct = gst_caps_get_structure(caps, 0);

        if (!gstStruct)
        {
            LOG(error) << "Failed to get structure from caps" << endl;
            return GST_PAD_PROBE_DROP;
        }

        int width = 0, height = 0, numerator = 0, denominator = 0;
        const gchar* format = nullptr;

        gst_structure_get_int(gstStruct, "width", &width);
        gst_structure_get_int(gstStruct, "height", &height);
        gst_structure_get_fraction(gstStruct, "framerate", &numerator, &denominator);
        format = gst_structure_get_string(gstStruct, "stream-format");

        // Store video metadata
        if (writer->getWidth() == 0 && writer->getHeight() == 0 && writer->getFormat() == nullptr)
        {
            writer->setVideoMetadata(width, height, numerator, denominator, format);

            if (denominator > 0)
            {
                double frame_rate = numerator / denominator;
                if (frame_rate > 0)
                {
                    writer->setMaxAllowedFrameDiff((int)((1000 / frame_rate) * 1.5));
                }
            }

            if (writer->getResolution().empty())
            {
                writer->setResolution(std::to_string(width) + "x" + std::to_string(height));
            }
        }

        // Drop caps events if only framerate changed
        if (writer->getWidth() == width && writer->getHeight() == height && writer->getFormat() && format &&
            !strcmp(writer->getFormat(), format) &&
            (writer->getNumerator() != numerator || writer->getDenominator() != denominator))
        {
            LOG(verbose) << "Dropping framerate-only caps change for stream ID: "
                         << (writer->m_streamId.empty() ? "unknown" : writer->m_streamId) << endl;
            return GST_PAD_PROBE_DROP;
        }
    }

    return GST_PAD_PROBE_OK;
}

gpointer gmainLoopthread(gpointer data)
{
    UnifiedStorageWriter* writer = static_cast<UnifiedStorageWriter*>(data);

    g_main_context_push_thread_default(writer->getMainContext());

    GstBus* bus = gst_pipeline_get_bus(GST_PIPELINE(writer->getPipeline()));
    if (!bus)
    {
        LOG(error) << "Failed to get pipeline bus" << endl;
        g_main_context_pop_thread_default(writer->getMainContext());
        return nullptr;
    }

    writer->setGSource(gst_bus_create_watch(bus));
    if (!writer->getGSource())
    {
        LOG(error) << "Failed to create bus watch" << endl;
        gst_object_unref(bus);
        g_main_context_pop_thread_default(writer->getMainContext());
        return nullptr;
    }

    g_source_set_callback(writer->getGSource(), (GSourceFunc)UnifiedStorageWriter::onBusMessagePublic, data, nullptr);
    if (g_source_attach(writer->getGSource(), writer->getMainContext()) <= 0)
    {
        LOG(error) << "Failed to attach source to context" << endl;
        gst_bus_remove_watch(bus);
        gst_object_unref(bus);
        g_source_unref(writer->getGSource());
        writer->setGSource(nullptr);
        g_main_context_pop_thread_default(writer->getMainContext());
        return nullptr;
    }

    g_main_loop_run(writer->getMainLoop());

    // Cleanup
    gst_bus_remove_watch(bus);
    gst_object_unref(bus);
    g_source_unref(writer->getGSource());
    writer->setGSource(nullptr);
    g_main_loop_unref(writer->getMainLoop());
    writer->setMainLoop(nullptr);
    g_main_context_pop_thread_default(writer->getMainContext());
    g_main_context_unref(writer->getMainContext());
    writer->setMainContext(nullptr);

    LOG(info) << "GMainLoop thread exited" << endl;
    return nullptr;
}

#ifdef UNIFIED_STORAGE_WRITER_UNIT_TEST
void UnifiedStorageWriter::testPushBufferFailure(int intervalSeconds)
{
    // Only enable test mode if explicitly requested (intervalSeconds > 0)
    if (intervalSeconds <= 0) {
        LOG(info) << "Test mode disabled - intervalSeconds = " << intervalSeconds << endl;
        m_test_pushbuffer_fail = false;
        return;
    }
    
    LOG(info) << "Setting up test pushBuffer failure simulation - will fail once after " << intervalSeconds << " seconds" << endl;
    
    // Enable test mode
    m_test_pushbuffer_fail = true;
    
    // Start a background thread to simulate a single failure
    std::thread([this, intervalSeconds]() {
        // Wait for the specified interval
        std::this_thread::sleep_for(std::chrono::seconds(intervalSeconds));
        
        if (m_test_pushbuffer_fail.load()) {
            LOG(warning) << "Test mode: Simulating ONE-TIME pushBuffer failure for stream ID: " 
                        << (m_streamId.empty() ? "unknown" : m_streamId) << endl;
            
            // Set error to simulate failure (this will cause next push to fail)
            setLastError("Test mode: Simulated pushBuffer failure");
            
            // Clear the error after a short delay to allow recovery
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
            clearLastError();
            
            // Disable test mode after the single failure
            m_test_pushbuffer_fail = false;
            LOG(info) << "Test mode: Single failure completed, test mode disabled for stream ID: " 
                     << (m_streamId.empty() ? "unknown" : m_streamId) << endl;
        }
    }).detach();
}
#endif
