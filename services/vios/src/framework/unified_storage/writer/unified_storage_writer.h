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

#pragma once

#include "cloud_storage_writer.h"
#include "../unified_storage_types.h"
#include "logger.h"
#include <atomic>
#include <condition_variable>
#include <gst/app/gstappsink.h>
#include <gst/app/gstappsrc.h>
#include <gst/gst.h>
#include <memory>
#include <mutex>
#include <thread>

namespace nv_vms
{

/**
 * @brief Unified storage writer base class that handles GStreamer pipeline for both local and cloud storage
 *
 * Pipeline structure:
 * - Local: appsrc → h264parse → matroskamux → filesink → MKV file
 * - Cloud: appsrc → h264parse → matroskamux → appsink → cloud upload
 */
class UnifiedStorageWriter
{
public:

    UnifiedStorageWriter(StorageType type);
    virtual ~UnifiedStorageWriter();

    // Independent interface
    bool isAvailable() const;
    std::string getStorageMode() const;

    // Write session management
    virtual std::string startWrite(const std::string& remote_path, const std::string& stream_id,
                                   size_t estimated_size = 0);

    // Frame handling
    bool onFrame(const std::string& session_id, const void* data, size_t size, int64_t pts = 0,
                 const std::string& media_type = "video");

    StorageResult stopWrite(const std::string& session_id, const std::string& stream_id);

    // Pause writing session
    bool pauseWrite(const std::string& session_id, const std::string& stream_id);

    // Configuration
    bool configureStorage(const StorageConfig& config);
    StorageConfig getStorageConfiguration() const;

    // Statistics and monitoring
    StorageStats getStorageStats() const;

    // Error handling
    std::string getLastError() const;

    // Pipeline creation (public method to create pipeline)
    bool createPipeline(const std::string& video_codec, bool audio_supported = false,
                        const std::string& stream_id = "");

    // Pipeline destruction (public method for external cleanup)
    bool destroyPipeline();

    // File operations (pure virtual - to be implemented by derived classes)
    virtual StorageResult uploadFile(const std::string& local_path, const std::string& remote_path) = 0;
    virtual bool deleteFile(const std::string& remote_path) = 0;
    virtual bool fileExists(const std::string& remote_path) const = 0;
    virtual size_t getFileSize(const std::string& remote_path) const = 0;

    // Configuration access methods
    virtual std::string getBucketName() const;

protected:
    // Internal pipeline creation (assumes m_pipeline_mutex is already held)
    bool createPipelineInternal(const std::string& video_codec, bool audio_supported,
                                const std::string& stream_id);

    // Session ID generation
    std::string generateSessionId(const std::string& stream_id);

#ifdef UNIFIED_STORAGE_WRITER_UNIT_TEST
    // Simple test method to simulate pushBuffer failure every 30 seconds
    void testPushBufferFailure(int intervalSeconds = 30);
#endif

protected:
    // Pipeline management
    bool setPipelineState(GstState state);
    bool isPipelineReady() const;
    // Internal pipeline destruction (no locking - for internal use)
    bool destroyPipelineInternal();

public:
    bool resetPipeline();

    // Buffer handling
    bool pushBufferToPipeline(const void* data, size_t size, int64_t pts, const std::string& media_type);
    bool sendEOS();
    bool waitForEOSMessage(int timeout_ms = 5000);

    // Session management
    bool startSession(const std::string& session_id, const std::string& remote_path);
    bool stopSession(const std::string& session_id);
    bool reusePipelineForNewFile(const std::string& remote_path);

    // Abstract methods for specific storage implementations
    virtual bool initializeStorage() = 0;
    virtual GstElement* createSinkElement() = 0; // Create filesink or appsink
    virtual bool configureSinkElement(GstElement* sink, const std::string& remote_path,
                                      const std::string& session_id) = 0;
    virtual StorageResult finalizeSession(const std::string& session_id, const std::string& stream_id) = 0;
    virtual bool cleanupSession(const std::string& session_id) = 0;

    // Pipeline elements (same as GstMux)
    GstElement* m_pipeline = nullptr;
    GstElement* m_sourceVideo = nullptr;
    GstElement* m_sourceAudio = nullptr;
    GstElement* m_parserVideo = nullptr;
    GstElement* m_capsfilterVideo = nullptr;
    GstElement* m_parserAudio = nullptr;
    GstElement* m_mux = nullptr;
    GstElement* m_sink = nullptr; // filesink for local, appsink for cloud

    // Storage mode
    StorageType m_storageMode;

    // Pipeline state
    std::atomic<bool> m_pipeline_ready{false};
    std::atomic<bool> m_session_active{false};
    std::string m_current_session_id;
    std::string m_current_remote_path;
    std::string m_streamId; // Store the current stream ID

    // Configuration
    StorageConfig m_config;
    std::string m_videoCodec;
    bool m_audioSupported = false;
    std::string m_audioCodec;

    // Error handling
    mutable std::mutex m_error_mutex;
    std::string m_last_error;

    // Thread safety
    mutable std::mutex m_pipeline_mutex;

    // Helper methods
    void setLastError(const std::string& error);
    void clearLastError();
    
    // Video source caps configuration for H.264 and H.265
    bool configureVideoSourceCaps();

    // GStreamer callbacks
    static GstFlowReturn onNewSample(GstAppSink* appsink, gpointer user_data);
    static void onEOS(GstAppSink* appsink, gpointer user_data);
    static gboolean onBusMessage(GstBus* bus, GstMessage* message, gpointer user_data);

    // Buffer management
    std::atomic<size_t> m_total_bytes_written{0};
    std::atomic<uint64_t> m_frames_written{0};

    // GStreamer context and loop
    GMainContext* m_mainContext = nullptr;
    GMainLoop* m_mainLoop = nullptr;
    GSource* m_gSource = nullptr;
    GThread* m_gmainLoopThread = nullptr;

    // Pipeline state tracking
    std::atomic<bool> m_eosReceived{false};
    std::mutex m_monitor_eos_mutex;
    std::condition_variable m_monitor_eos_cv;

    // Video metadata
    int m_width = 0;
    int m_height = 0;
    int m_numerator = 0;
    int m_denominator = 0;
    char* m_format = nullptr;
    std::string m_resolution;
    int m_maxAllowedFrameDiff = 0;
    std::string m_parserVideoName; // Store parser element name
    gulong m_probe_id{0};          // Store pad probe ID
#ifdef UNIFIED_STORAGE_WRITER_UNIT_TEST
    // Test mode for error simulation
    std::atomic<bool> m_test_pushbuffer_fail{false};
#endif

    // Friend functions to access protected members
    friend GstPadProbeReturn event_probe(GstPad* pad, GstPadProbeInfo* info, gpointer user_data);
    friend gpointer gmainLoopthread(gpointer data);

public:
    // These methods provide access to protected members for friend functions
    void setResolution(const std::string& resolution);
    void setMaxAllowedFrameDiff(int diff);
    void setVideoMetadata(int width, int height, int numerator, int denominator, const char* format);
    GMainContext* getMainContext() const
    {
        return m_mainContext;
    }
    GMainLoop* getMainLoop() const
    {
        return m_mainLoop;
    }
    GstElement* getPipeline() const
    {
        return m_pipeline;
    }
    int getWidth() const
    {
        return m_width;
    }
    int getHeight() const
    {
        return m_height;
    }
    char* getFormat() const
    {
        return m_format;
    }
    std::string getResolution() const
    {
        return m_resolution;
    }
    int getNumerator() const
    {
        return m_numerator;
    }
    int getDenominator() const
    {
        return m_denominator;
    }
    std::string getParserVideoName() const
    {
        return m_parserVideoName;
    }
    GSource* getGSource() const
    {
        return m_gSource;
    }
    void setGSource(GSource* source)
    {
        m_gSource = source;
    }
    void setMainLoop(GMainLoop* loop)
    {
        m_mainLoop = loop;
    }
    void setMainContext(GMainContext* context)
    {
        m_mainContext = context;
    }

    // Public bus message handler for external use
    static gboolean onBusMessagePublic(GstBus* bus, GstMessage* message, gpointer user_data)
    {
        return onBusMessage(bus, message, user_data);
    }

    /* TODO: 25.07.1: Need to add pipeline health check methods and error recovery methods inside this class only */
};

} // namespace nv_vms