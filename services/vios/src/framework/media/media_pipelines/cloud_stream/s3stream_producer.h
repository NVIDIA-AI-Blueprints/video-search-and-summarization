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

#include "logger.h"
#include <string>
#include <vector>
#include <map>
#include <mutex>
#include <atomic>
#include <memory>
#include <functional>
#include <gst/gst.h>
#include <gst/app/gstappsink.h>
#include <jsoncpp/json/json.h>
#include "media_consumer.h"
#include "media_producer.h"
#include "unified_storage/reader/unified_storage_reader.h"
#include "database_schema.h"

namespace cloud_stream
{

/**
 * @brief Video codec types supported
 */
enum class VideoCodec
{
    H264,
    H265
};

/**
 * @brief Container format types supported
 */
enum class ContainerFormat
{
    QUICKTIME,      // Quicktime/MP4
    MATROSKA        // MKV/WebM
};

} // namespace cloud_stream

/**
 * @brief CloudStreamProducer - Streams video from S3/Cloud sources
 * 
 * This producer creates a GStreamer pipeline to fetch and decode video
 * from S3 presigned URLs or other HTTP sources. It supports seeking,
 * framerate-controlled delivery, and both H.264/H.265 codecs.
 * 
 * Implements IMediaDataProducer interface for integration with the media pipeline.
 */
class CloudStreamProducer : public IMediaDataProducer
{
public:
    /**
     * @brief Construct a new Cloud Stream Producer
     * 
     * Use setConfig() to configure the producer with S3 URLs, codec, container, etc.
     */
    CloudStreamProducer();
    
    /**
     * @brief Construct a new Cloud Stream Producer with configuration
     * 
     * @param streamId The stream identifier
     * @param s3Urls Vector of S3 URLs with their start times
     * @param codec Video codec type
     * @param container Container format type
     * @param startTime Start time in seconds
     * @param endTime End time in seconds (-1 for end of stream)
     * @param syncMode Enable frame rate controlled delivery
     * @param enableAudio Enable audio streaming
     */
    CloudStreamProducer(const std::string& streamId,
                       const std::vector<std::pair<std::string, int64_t>>& s3Urls,
                       cloud_stream::VideoCodec codec,
                       cloud_stream::ContainerFormat container,
                       double startTime,
                       double endTime,
                       bool syncMode,
                       bool enableAudio = false);
    
    ~CloudStreamProducer();

    // ========================================================================
    // IMediaDataProducer Interface Implementation
    // ========================================================================
    
    /**
     * @brief Register a consumer to receive data from this producer
     * @param consumer The consumer to register
     * @param identifier Optional identifier for the consumer
     */
    void registerConsumer(std::shared_ptr<IMediaDataConsumer> consumer, 
                         const std::string& identifier = "") override;

    /**
     * @brief Register a consumer to receive data from this producer with media type
     * @param consumer The consumer to register
     * @param identifier Identifier for the consumer
     * @param media_type Media type for routing (e.g., "video", "audio")
     */
    void registerConsumer(std::shared_ptr<IMediaDataConsumer> consumer,
                         const std::string& identifier,
                         const std::string& media_type) override;

    /**
     * @brief Unregister a consumer from this producer
     * @param consumer The consumer to unregister
     * @param identifier Optional identifier (unused - finds by pointer match)
     * @param doNotRemoveClient Optional flag to skip removal
     */
    void unregisterConsumer(std::shared_ptr<IMediaDataConsumer> consumer,
                           const std::string& identifier = "", 
                           bool doNotRemoveClient = false) override;

    /**
     * @brief Start the producer and begin streaming
     * @return true if started successfully, false otherwise
     */
    bool start() override;

    /**
     * @brief Stop the producer and stop streaming
     */
    void stop() override;

    /**
     * @brief Check if the producer is currently running
     * @return true if running, false otherwise
     */
    bool isRunning() const override;

    /**
     * @brief Get the media type this producer generates
     * @return MediaTypeVideo or MediaTypeAudioVideo if audio is enabled
     */
    eMediaType getProducerMediaType() const override;

    /**
     * @brief Get the source identifier for this producer
     * @return The S3 URL or stream ID
     */
    std::string getSourceIdentifier() const override;

    /**
     * @brief Get the number of registered consumers
     * @return Number of active consumers
     */
    size_t getConsumerCount() const override;

    /**
     * @brief Check if the producer has any registered consumers
     * @return true if has consumers, false otherwise
     */
    bool hasConsumers() const override;

    // ========================================================================
    // CloudStreamProducer Specific Methods
    // ========================================================================

    /**
     * @brief Set the configuration for the cloud stream producer
     * @param config The configuration to set. It should contain the following fields:
     * - s3Urls: Vector of S3 presigned URLs for each segment (in order)
     * - codec: Video codec (H264 or H265)
     * - container: Container format (MP4 or MATROSKA)
     * - startTime: Start time in seconds relative to first segment (default: 0.0)
     * - endTime: End time in seconds relative to first segment (-1 means play to end)
     * - sync: Enable framerate-controlled delivery (true) or fast as possible (false)
     * - enableAudio: Enable audio (true) or disable (false)
     */
    void setConfig(const std::vector<VideoFileInfo>& fileList,
        const std::string& startTime, const std::string& endTime,
        const std::string& codec, const std::string& container,
        bool syncMode, bool enableAudio);

    /**
     * @brief Seek to a specific position in the stream
     * @param positionSeconds Position in seconds to seek to
     * @return true if seek was successful, false otherwise
     *
     * Note: Can be called while pipeline is PLAYING. The pipeline will
     * automatically flush and continue from the new position.
     */
    bool seek(double positionSeconds);

    /**
     * @brief Get current playback position
     * @return Current position in seconds, or -1.0 if query fails
     */
    double getCurrentPosition() const;

    /**
     * @brief Get current pipeline state
     * @return State string
     */
    std::string getState() const;

    /**
     * @brief Get detected framerate
     * @return Framerate in fps
     */
    double getFramerate() const { return m_fps; }

    /**
     * @brief Get detected resolution
     */
    void getResolution(int& width, int& height) const {
        width = m_width;
        height = m_height;
    }

    /**
     * @brief Check if pipeline has encountered an error
     */
    bool hasError() const { return m_error.load(); }

    /**
     * @brief Check if pipeline has been stopped or reached EOS
     */
    bool isStopped() const { return m_stop.load(); }

    /**
     * @brief Set callback to be notified when stream finishes (EOS)
     */
    void onFinished(std::function<void()> callback) override { m_finishedCallback = callback; }

    /**
     * @brief Enable local file mode (uses filesrc instead of souphttpsrc)
     * This mode downloads files from cloud if needed, then uses local filesrc for playback
     * @param enable true to use filesrc, false to use souphttpsrc (default)
     */
    void setUseLocalFiles(bool enable) { m_useLocalFiles = enable; }

    /**
     * @brief Set callback to be notified when pipeline encounters an error
     * @param callback Function to call with error message and error code
     */
    void onError(std::function<void(const std::string& /*errorMsg*/, int /*errorCode*/)> callback) override { m_errorCallback = callback; }

    /**
     * @brief Get stream ID
     */
    std::string getStreamId() const { return m_streamId; }

    /**
     * @brief Get configured codec
     */
    cloud_stream::VideoCodec getCodec() const { return m_codec; }

    /**
     * @brief Get configured container format
     */
    cloud_stream::ContainerFormat getContainer() const { return m_container; }

    /**
     * @brief Get codec as string
     */
    std::string getCodecString() const {
        return (m_codec == cloud_stream::VideoCodec::H264) ? "h264" : "h265";
    }

    /**
     * @brief Set appsink mode for clip download use-case
     *
     * When enabled, frames are delivered as RawFrameParams (with GstSample/GstBuffer).
     * When disabled (default), frames are delivered as FrameParams (raw NAL data).
     *
     * IMPORTANT: Must be called BEFORE startPipeline()
     *
     * @param enable true to enable appsink mode, false for legacy NAL mode
     */
    void setAppsinkMode(bool enable) { m_useAppsinkMode = enable; }

    /**
     * @brief Check if appsink mode is enabled
     */
    bool isAppsinkMode() const { return m_useAppsinkMode; }

    /**
     * @brief Get number of segments
     */
    size_t getSegmentCount() const {
        return m_useMultiSegment ? m_s3Urls.size() : 1;
    }

    /**
     * @brief Get list of consumers (builds vector from map for backward compatibility)
     * @return List of consumers
     */
    std::vector<std::shared_ptr<IMediaDataConsumer>> getConsumerList() const {
        std::lock_guard<std::mutex> lock(m_consumerLock);
        std::vector<std::shared_ptr<IMediaDataConsumer>> result;
        for (const auto& pair : m_consumersMap) {
            if (pair.second) {
                result.push_back(pair.second);
            }
        }
        return result;
    }

private:
    // Pipeline state
    std::atomic<GstState> m_state{GST_STATE_NULL};
    std::atomic<bool> m_error{false};
    // Configuration
    std::string m_streamId;
    std::string m_s3Url;
    int64_t m_fileStartTime = 0;
    std::vector<std::pair<std::string, int64_t>> m_s3Urls;        // Multiple URLs (multi-segment)
    bool m_useMultiSegment{false};            // Flag to indicate multi-segment mode
    cloud_stream::VideoCodec m_codec = cloud_stream::VideoCodec::H264;
    cloud_stream::ContainerFormat m_container = cloud_stream::ContainerFormat::QUICKTIME;
    double m_startTime = 0.0;
    double m_endTime = -1.0;
    bool m_syncMode = false;
    struct timeval m_presentationTime;

    // Stream properties (detected from caps)
    std::atomic<int> m_width{1920};
    std::atomic<int> m_height{1080};
    std::atomic<double> m_fps{0.0};
    int m_framerateNum{0};
    int m_framerateDen{0};

    // GStreamer elements (managed by GStreamer reference counting, cleaned up in destroyPipeline())
    // Child elements are owned by m_pipeline and automatically freed when pipeline is unref'd
    GstElement* m_pipeline = nullptr;
    GstElement* m_source = nullptr;       // souphttpsrc (single segment)
    GstElement* m_concat = nullptr;       // concat element (multi-segment)
    std::vector<GstElement*> m_sources;  // Multiple souphttpsrc (multi-segment)
    std::vector<GstElement*> m_demuxers; // Multiple demuxers (multi-segment)
    GstElement* m_demux = nullptr;        // qtdemux or matroskademux (single segment)
    GstElement* m_videoParser = nullptr;  // h264parse or h265parse
    GstElement* m_appsink = nullptr;      // video appsink
    GstElement* m_audioAppsink = nullptr; // audio appsink (optional)
    guint m_busWatchId = 0;

    // Audio support flag
    bool m_enableAudio;

    // Multi-segment pad tracking
    std::vector<bool> m_segmentPadsLinked;  // Track which segments linked to concat

    // Consumer management - uses map for identifier-based routing (like ClipReaderProducer)
    // This allows video frames to go only to "video" consumer and audio to "audio" consumer
    mutable std::mutex m_consumerLock;
    std::map<std::string, std::shared_ptr<IMediaDataConsumer>, std::less<>> m_consumersMap;

    // Pipeline control
    std::atomic<bool> m_stop{false};
    bool m_seekDone{false};
    bool m_pausedStateReached{false};
    std::mutex m_pipelineLock;

    // Frame tracking
    std::atomic<int> m_frameNum{0};

    // Appsink mode flag (for clip download use-case)
    bool m_useAppsinkMode{false};

    // Local file mode flag (uses filesrc instead of souphttpsrc)
    bool m_useLocalFiles{false};

    // Store file list for cloud download
    std::vector<VideoFileInfo> m_fileList;

    // Track first frame PTS for relative time calculation with epoch timestamps
    std::atomic<GstClockTime> m_firstFramePtsNs{GST_CLOCK_TIME_NONE};

    // Track previous frame PTS to detect out-of-order frames (B-frames)
    std::atomic<GstClockTime> m_prevFramePtsNs{0};

    // Track if end-time EOS has been signaled (to avoid duplicate logs)
    std::atomic<bool> m_endTimeEosSent{false};

    // Helper structures for callbacks
    struct StreamInfo {
        int framerateNum;
        int framerateDen;
        double fps;
        CloudStreamProducer* producer;
    };

    struct SeekData {
        GstElement* pipeline;
        double startTime;
        double endTime;
        bool seekDone;
        bool pausedStateReached;
    };

    StreamInfo* m_streamInfo;
    SeekData* m_seekData;
    
    // Completion and error callbacks
    std::function<void()> m_finishedCallback;
    std::function<void(const std::string&, int)> m_errorCallback;

    // Unified storage reader for cloud storage access
    std::shared_ptr<nv_vms::UnifiedStorageReader> m_unifiedStorageReader = nullptr;

    // Static callbacks
    static GstFlowReturn onNewSample(GstElement* sink, gpointer userData);
    static GstFlowReturn onAudioNewSample(GstElement* sink, gpointer userData);
    static gboolean busWatch(GstBus* bus, GstMessage* msg, gpointer userData);
    static void onPadAdded(GstElement* src, GstPad* newPad, gpointer userData);

    // Helper methods
    bool createPipeline();
    bool createSingleSegmentPipeline();
    bool createMultiSegmentPipeline();
    bool createLocalFilePipeline();  // New: filesrc-based pipeline with cloud download
    void parseConfigForLocalFiles(const std::vector<VideoFileInfo>& fileList,
                                   const std::string& startTime, const std::string& endTime,
                                   const std::string& codec, const std::string& container,
                                   bool syncMode, bool enableAudio);
    bool setupSeek();
    void destroyPipeline();
    bool initUnifiedStorageReader();

protected:
    // IMediaDataProducer interface - protected distribution methods
    void distributeToConsumers(std::shared_ptr<RawFrameParams> frameData) override;
    void distributeToConsumers(FrameParams& frameParams) override;

    // Identifier-based distribution (for routing video/audio separately)
    void distributeToConsumer(std::shared_ptr<RawFrameParams> frameData, const std::string& identifier);

private:
    // Legacy distribution method (for internal use)
    void distributeFrameToConsumers(FrameParams& frameParams);
};

 