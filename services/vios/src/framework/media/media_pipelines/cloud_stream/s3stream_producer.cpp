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

#include "s3stream_producer.h"
#include <gst/app/app.h>
#include <cstring>
#include <sys/time.h>
#include <sstream>
#include <iomanip>
#include "mm_utils.h"
#include "utils.h"
#include "storage_management.h"
#include "ReplayPeerConnection.h"

using namespace std;

/* Macro defined in splitmuxsrc plugin - must match for compatibility */
#define FIXED_TS_OFFSET (1000*GST_SECOND)

// Utility function to convert seconds to nanoseconds
static gint64 secondsToNs(double seconds) {
    return static_cast<gint64>(seconds * GST_SECOND);
}

void CloudStreamProducer::setConfig(const std::vector<VideoFileInfo>& fileList,
    const std::string& startTime, const std::string& endTime,
    const std::string& codec, const std::string& container,
    bool syncMode, bool enableAudio)
{
    if (fileList.empty()) {
        LOG(error) << "Configuration is empty" << endl;
        return;
    }

    // Initialize unified storage reader for cloud downloads
    if (!m_unifiedStorageReader && !initUnifiedStorageReader()) {
        LOG(error) << "Failed to initialize unified storage reader" << endl;
        return;
    }

    if (GET_CONFIG().cloud_storage_type == "minio") {
        LOG(info) << "Using local file mode for MinIO storage" << endl;
        parseConfigForLocalFiles(fileList, startTime, endTime, codec, container, syncMode, enableAudio);
        return;
    }

    nv_vms::FileResult urlResult;
    for (const auto& file : fileList) {
        string presignedUrl;
        urlResult = m_unifiedStorageReader->getPresignedUrl(file.m_objectId, presignedUrl);
        if (!urlResult.success || presignedUrl.empty()) {
            LOG(error) << "Failed to get presigned URL for " << file.m_objectId << ": " << urlResult.message << endl;
            continue;
        }
        m_s3Urls.push_back(make_pair(presignedUrl, file.m_startTime));
    }

    if (m_s3Urls.empty()) {
        LOG(error) << "S3 URLs found are empty" << endl;
        return;
    }

    if (m_s3Urls.size() >= 1) {
        m_streamId = fileList[0].m_filePath;
        m_fileStartTime = m_s3Urls[0].second;

        // Use provided container format if valid, otherwise auto-detect from URL
        if (!container.empty()) {
            if (container == "MP4" || container == "mp4" || container == "QUICKTIME" || container == "quicktime") {
                m_container = cloud_stream::ContainerFormat::QUICKTIME;
            } else if (container == "MKV" || container == "mkv" || container == "MATROSKA" || container == "matroska") {
                m_container = cloud_stream::ContainerFormat::MATROSKA;
            }
        } else {
            // Auto-detect from URL
            m_container = cloud_stream::ContainerFormat::QUICKTIME;
            string presigned_url = m_s3Urls[0].first;
            if (presigned_url.find(".mkv") != string::npos || presigned_url.find(".MKV") != string::npos) {
                m_container = cloud_stream::ContainerFormat::MATROSKA;
            } else if (presigned_url.find(".mp4") != string::npos || presigned_url.find(".MP4") != string::npos) {
                m_container = cloud_stream::ContainerFormat::QUICKTIME;
            }
        }

        if (!startTime.empty()) {
            int64_t startTimeEpochMs = getEpocTimeInMS(startTime);
            if (startTimeEpochMs > m_s3Urls[0].second) {
                m_startTime = (startTimeEpochMs - m_s3Urls[0].second) / 1000.0;
            } else {
                m_startTime = 0.0;
            }
        }

        if (!endTime.empty()) {
            int64_t endTimeEpochMs = getEpocTimeInMS(endTime);
            m_endTime = (endTimeEpochMs - m_s3Urls[0].second) / 1000.0;
        }
        else {
            m_endTime = -1.0;
        }
    }

    m_codec = iequals(codec, "h265") ? cloud_stream::VideoCodec::H265 : cloud_stream::VideoCodec::H264;
    m_syncMode = syncMode;
    m_enableAudio = enableAudio;
    m_useMultiSegment = (m_s3Urls.size() > 1);

    // Print all configuration values
    LOG(info) << "CloudStreamProducer configuration:" << endl;
    LOG(info) << "  S3 URLs: " << m_s3Urls.size() << endl;
    for (size_t i = 0; i < m_s3Urls.size(); i++) {
        LOG(info) << "    URL[" << i << "]: " << m_s3Urls[i].first.substr(0, 60) << "..." << ", fileStartTime: " << m_s3Urls[i].second << endl;
    }
    LOG(info) << "  Container: " << (m_container == cloud_stream::ContainerFormat::QUICKTIME ? "MP4/Quicktime" : "Matroska/MKV") << endl;
    LOG(info) << "  User start time: " << m_startTime << endl;
    LOG(info) << "  User end time: " << m_endTime << endl;
    LOG(info) << "  Sync mode: " << (m_syncMode ? "enabled" : "disabled") << endl;
    LOG(info) << "  Audio: " << (m_enableAudio ? "enabled" : "disabled") << endl;
    LOG(info) << "  Codec: " << (m_codec == cloud_stream::VideoCodec::H264 ? "H264" : "H265") << endl;
}

void CloudStreamProducer::parseConfigForLocalFiles(const std::vector<VideoFileInfo>& fileList,
    const std::string& startTime, const std::string& endTime,
    const std::string& codec, const std::string& container,
    bool syncMode, bool enableAudio)
{
    m_fileList = fileList;
    m_useLocalFiles = true;

    if (m_fileList.empty()) {
        LOG(error) << "File list is empty" << endl;
        return;
    }

    if (m_fileList.size() >= 1) {
        m_streamId = m_fileList[0].m_filePath;
        m_fileStartTime = m_fileList[0].m_startTime;

        // Use provided container format if valid, otherwise auto-detect from URL
        if (!container.empty()) {
            if (container == "MP4" || container == "mp4" || container == "QUICKTIME" || container == "quicktime") {
                m_container = cloud_stream::ContainerFormat::QUICKTIME;
            } else if (container == "MKV" || container == "mkv" || container == "MATROSKA" || container == "matroska") {
                m_container = cloud_stream::ContainerFormat::MATROSKA;
            }
        } else {
            // Auto-detect from URL
            m_container = cloud_stream::ContainerFormat::QUICKTIME;
            string filePath = m_fileList[0].m_filePath;
            if (filePath.find(".mkv") != string::npos || filePath.find(".MKV") != string::npos) {
                m_container = cloud_stream::ContainerFormat::MATROSKA;
            } else if (filePath.find(".mp4") != string::npos || filePath.find(".MP4") != string::npos) {
                m_container = cloud_stream::ContainerFormat::QUICKTIME;
            }
        }

        if (!startTime.empty()) {
            uint64_t startTimeEpochMs = getEpocTimeInMS(startTime);
            if (startTimeEpochMs > m_fileList[0].m_startTime) {
                m_startTime = (startTimeEpochMs - m_fileList[0].m_startTime) / 1000.0;
            } else {
                m_startTime = 0.0;
            }
        }

        if (!endTime.empty()) {
            uint64_t endTimeEpochMs = getEpocTimeInMS(endTime);
            m_endTime = (endTimeEpochMs - m_fileList[0].m_startTime) / 1000.0;
        }
        else {
            m_endTime = -1.0;
        }
    }
    m_codec = iequals(codec, "h265") ? cloud_stream::VideoCodec::H265 : cloud_stream::VideoCodec::H264;
    m_syncMode = syncMode;
    m_enableAudio = enableAudio;
    m_useMultiSegment = false;

    // Print all configuration values
    LOG(info) << "CloudStreamProducer configuration:" << endl;
    LOG(info) << "  File list: " << m_fileList.size() << endl;
    for (size_t i = 0; i < m_fileList.size(); i++) {
        LOG(info) << "    File[" << i << "]: " << m_fileList[i].m_filePath << ", fileStartTime: " << m_fileList[i].m_startTime << endl;
    }
    LOG(info) << "  Container: " << (m_container == cloud_stream::ContainerFormat::QUICKTIME ? "MP4/Quicktime" : "Matroska/MKV") << endl;
    LOG(info) << "  User start time: " << m_startTime << endl;
    LOG(info) << "  User end time: " << m_endTime << endl;
    LOG(info) << "  Sync mode: " << (m_syncMode ? "enabled" : "disabled") << endl;
    LOG(info) << "  Audio: " << (m_enableAudio ? "enabled" : "disabled") << endl;
    LOG(info) << "  Codec: " << (m_codec == cloud_stream::VideoCodec::H264 ? "H264" : "H265") << endl;
}

bool CloudStreamProducer::initUnifiedStorageReader()
{
    StorageManagement* storageMngt = GET_STORAGE_MNGT();
    if (storageMngt == nullptr)
    {
        ReplayPeerConnection* replayPeerConnection = GET_PEERCONNECTION_REPLAY_MNGR();
        if (replayPeerConnection != nullptr)
        {
            m_unifiedStorageReader = replayPeerConnection->getUnifiedStorageReader();
            if (!m_unifiedStorageReader)
            {
                LOG(error) << "Failed to get unified storage reader" << endl;
                return false;
            }
            return true;
        }
    }
    else
    {
        m_unifiedStorageReader = storageMngt->getUnifiedStorageReader();
        if (!m_unifiedStorageReader)
        {
            LOG(error) << "Failed to get unified storage reader" << endl;
            return false;
        }
        return true;
    }
    return false;
}

CloudStreamProducer::CloudStreamProducer()
    : m_pipeline(nullptr)
    , m_source(nullptr)
    , m_concat(nullptr)
    , m_demux(nullptr)
    , m_videoParser(nullptr)
    , m_appsink(nullptr)
    , m_audioAppsink(nullptr)
    , m_busWatchId(0)
    , m_enableAudio(false)
    , m_streamInfo(nullptr)
    , m_seekData(nullptr)
{
    LOG(info) << "CloudStreamProducer created (default constructor). Use setConfig() to configure." << endl;
}

CloudStreamProducer::CloudStreamProducer(const std::string& streamId,
                                       const std::vector<std::pair<std::string, int64_t>>& s3Urls,
                                       cloud_stream::VideoCodec codec,
                                       cloud_stream::ContainerFormat container,
                                       double startTime,
                                       double endTime,
                                       bool syncMode,
                                       bool enableAudio)
    : m_streamId(streamId)
    , m_s3Urls(s3Urls)
    , m_codec(codec)
    , m_container(container)
    , m_startTime(startTime)
    , m_endTime(endTime)
    , m_syncMode(syncMode)
    , m_pipeline(nullptr)
    , m_source(nullptr)
    , m_concat(nullptr)
    , m_demux(nullptr)
    , m_videoParser(nullptr)
    , m_appsink(nullptr)
    , m_audioAppsink(nullptr)
    , m_busWatchId(0)
    , m_enableAudio(enableAudio)
    , m_streamInfo(nullptr)
    , m_seekData(nullptr)
{
    // Determine if this is multi-segment mode
    m_useMultiSegment = (m_s3Urls.size() > 1);
    
    // Initialize unified storage reader
    if (!initUnifiedStorageReader())
    {
        LOG(warning) << "Failed to initialize unified storage reader" << endl;
    }
    
    LOG(info) << "CloudStreamProducer created with configuration:" << endl;
    LOG(info) << "  Stream ID: " << m_streamId << endl;
    LOG(info) << "  S3 URLs: " << m_s3Urls.size() << endl;
    LOG(info) << "  Codec: " << (m_codec == cloud_stream::VideoCodec::H264 ? "H264" : "H265") << endl;
    LOG(info) << "  Container: " << (m_container == cloud_stream::ContainerFormat::QUICKTIME ? "MP4/Quicktime" : "Matroska/MKV") << endl;
    LOG(info) << "  Start time: " << m_startTime << endl;
    LOG(info) << "  End time: " << m_endTime << endl;
    LOG(info) << "  Sync mode: " << (m_syncMode ? "enabled" : "disabled") << endl;
    LOG(info) << "  Audio: " << (m_enableAudio ? "enabled" : "disabled") << endl;
}

CloudStreamProducer::~CloudStreamProducer()
{
    try {
        stop();

        if (m_streamInfo) {
            delete m_streamInfo;
            m_streamInfo = nullptr;
        }

        if (m_seekData) {
            delete m_seekData;
            m_seekData = nullptr;
        }

        LOG(info) << "CloudStreamProducer destroyed for stream: " << m_streamId << endl;
    } catch (const std::exception& e) {
        try { LOG(error) << "Exception in ~CloudStreamProducer: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
    } catch (...) {
        try { LOG(error) << "Unknown exception in ~CloudStreamProducer" << endl; } catch (...) { (void)std::current_exception(); }
    }
}

// ============================================================================
// IMediaDataProducer Interface Implementation
// ============================================================================

void CloudStreamProducer::registerConsumer(std::shared_ptr<IMediaDataConsumer> consumer, 
                                          const std::string& identifier)
{
    // Default to "video" media type (same as ClipReaderProducer)
    registerConsumer(consumer, identifier, "video");
}

void CloudStreamProducer::registerConsumer(std::shared_ptr<IMediaDataConsumer> consumer,
                                          const std::string& identifier,
                                          const std::string& media_type)
{   
    if (!consumer) {
        LOG(warning) << "CloudStreamProducer: Attempted to register null consumer" << endl;
        return;
    }

    {
        std::lock_guard<std::mutex> lock(m_consumerLock);
        m_consumersMap[media_type] = consumer;
        LOG(info) << "CloudStreamProducer: Consumer registered for '" << media_type
                  << "'. Total consumers: " << m_consumersMap.size() << endl;
    }

    if(m_syncMode && m_state != GST_STATE_PLAYING) {
        gst_element_set_state(m_pipeline, GST_STATE_PLAYING);
        m_state = GST_STATE_PLAYING;
    }
}

void CloudStreamProducer::unregisterConsumer(std::shared_ptr<IMediaDataConsumer> consumer,
                                            const std::string& identifier, 
                                            bool doNotRemoveClient)
{   
    if (doNotRemoveClient) return;

    if (!consumer) {
        return;
    }

    std::lock_guard<std::mutex> lock(m_consumerLock);

    // Find and remove the consumer by matching the pointer (same as ClipReaderProducer)
    for (auto it = m_consumersMap.begin(); it != m_consumersMap.end(); ++it) {
        if (it->second == consumer) {
            LOG(info) << "CloudStreamProducer: Consumer unregistered for '" << it->first
                      << "'. Remaining consumers: " << (m_consumersMap.size() - 1) << endl;
            m_consumersMap.erase(it);
            break;
        }
    }
}

bool CloudStreamProducer::start()
{
    if (m_stop.load()) {
        LOG(warning) << "Cannot start pipeline - stop flag is set" << endl;
        return false;
    }

    if (!createPipeline()) {
        return false;
    }

    // Initialize seek data
    m_seekData = new SeekData{m_pipeline, m_startTime, m_endTime, false, false};

    // Set pipeline to PAUSED first (for preroll and seeking and then play in asyncDone callback)
    GstStateChangeReturn ret = gst_element_set_state(m_pipeline, GST_STATE_PAUSED);
    if (ret == GST_STATE_CHANGE_FAILURE) {
        LOG(error) << "Failed to set pipeline to PAUSED state" << endl;
        destroyPipeline();
        return false;
    }

    m_state = GST_STATE_PAUSED;
    LOG(info) << "Pipeline started in PAUSED state, waiting for preroll..." << endl;
    
    return true;
}

void CloudStreamProducer::stop()
{
    m_stop.store(true);

    {
        std::lock_guard<std::mutex> lock(m_consumerLock);
        m_consumersMap.clear();
    }

    destroyPipeline();

    // Clean up downloaded files if using local file mode
    if (m_useLocalFiles && !m_fileList.empty())
    {
        LOG(info) << "Local file mode: Cleaning up downloaded files..." << endl;

        int deletedCount = 0;
        int failedCount = 0;

        for (const auto& fileInfo : m_fileList)
        {
            // Only delete files that were downloaded from cloud (have objectId)
            if (!fileInfo.m_objectId.empty() && isFileExist(fileInfo.m_filePath))
            {
                if (remove(fileInfo.m_filePath.c_str()) == 0)
                {
                    LOG(info) << "Deleted downloaded file: " << fileInfo.m_filePath << endl;
                    deletedCount++;
                }
                else
                {
                    LOG(warning) << "Failed to delete file: " << fileInfo.m_filePath
                                << " (error: " << strerror(errno) << ")" << endl;
                    failedCount++;
                }
            }
        }

        if (deletedCount > 0)
        {
            LOG(info) << "Cleanup complete: deleted " << deletedCount << " file(s)"
                      << (failedCount > 0 ? ", failed to delete " + std::to_string(failedCount) : "") << endl;
        }
    }

    m_state = GST_STATE_NULL;
    LOG(info) << "Pipeline stopped for stream: " << m_streamId << endl;
}

bool CloudStreamProducer::isRunning() const
{
    GstState currentState = m_state.load();
    return (currentState == GST_STATE_PLAYING || currentState == GST_STATE_PAUSED) && 
           !m_stop.load() && 
           !m_error.load();
}

eMediaType CloudStreamProducer::getProducerMediaType() const
{
    return m_enableAudio ? MediaTypeAudioVideo : MediaTypeVideo;
}

std::string CloudStreamProducer::getSourceIdentifier() const
{
    // Return the S3 URL for single segment, or stream ID for multi-segment
    if (!m_useMultiSegment && !m_s3Url.empty()) {
        return m_s3Url;
    }
    return m_streamId;
}

size_t CloudStreamProducer::getConsumerCount() const
{
    std::lock_guard<std::mutex> lock(m_consumerLock);
    return m_consumersMap.size();
}

bool CloudStreamProducer::hasConsumers() const
{
    return getConsumerCount() > 0;
}

void CloudStreamProducer::distributeToConsumers(std::shared_ptr<RawFrameParams> frameData)
{
    if (!frameData) {
        return;
    }
    
    // Broadcast to all consumers (for backward compatibility with legacy code)
    // For proper routing, use distributeToConsumer(frameData, identifier) instead
    std::lock_guard<std::mutex> lock(m_consumerLock);
    for (auto& pair : m_consumersMap) {
        if (pair.second) {
            pair.second->onFrame(frameData);
        }
    }
}

void CloudStreamProducer::distributeToConsumer(std::shared_ptr<RawFrameParams> frameData,
                                               const std::string& identifier)
{
    if (!frameData) {
        return;
    }

    // Route to specific consumer by identifier (like ClipReaderProducer)
    std::lock_guard<std::mutex> lock(m_consumerLock);
    auto it = m_consumersMap.find(identifier);
    if (it != m_consumersMap.end() && it->second) {
        it->second->onFrame(frameData);
    }
}

void CloudStreamProducer::distributeToConsumers(FrameParams& frameParams)
{
    distributeFrameToConsumers(frameParams);
}

// ============================================================================
// CloudStreamProducer Specific Methods
// ============================================================================

bool CloudStreamProducer::createPipeline()
{
    // Determine which pipeline to create based on mode
    if (m_useLocalFiles) {
        return createLocalFilePipeline();
    } else if (m_useMultiSegment) {
        return createMultiSegmentPipeline();
    } else {
        return createSingleSegmentPipeline();
    }
}

bool CloudStreamProducer::createSingleSegmentPipeline()
{
    std::lock_guard<std::mutex> lock(m_pipelineLock);

    if (m_pipeline) {
        LOG(warning) << "Pipeline already exists for stream: " << m_streamId << endl;
        return false;
    }

    if (m_s3Urls.empty()) {
        LOG(error) << "S3 URLs are empty, not creating pipeline" << endl;
        return false;
    }

    // Determine element names based on configuration
    const char* demuxName = (m_container == cloud_stream::ContainerFormat::QUICKTIME) ? "qtdemux" : "matroskademux";
    const char* parserName = (m_codec == cloud_stream::VideoCodec::H264) ? "h264parse" : "h265parse";
    const char* codecMimeType = (m_codec == cloud_stream::VideoCodec::H264) ? "video/x-h264" : "video/x-h265";

    // Create pipeline elements
    m_pipeline = gst_pipeline_new(("cloudstream-pipeline-" + m_streamId).c_str());
    m_source = gst_element_factory_make("souphttpsrc", "source");
    m_demux = gst_element_factory_make(demuxName, "demux");
    m_videoParser = gst_element_factory_make(parserName, "parser");
    m_appsink = gst_element_factory_make("appsink", "appsink");

    // Create audio appsink if audio is enabled
    if (m_enableAudio) {
        m_audioAppsink = gst_element_factory_make("appsink", "audio-appsink");
    }

    if (!m_pipeline || !m_source || !m_demux || !m_videoParser || !m_appsink) {
        LOG(error) << "Failed to create GStreamer elements for stream: " << m_streamId << endl;
        LOG(error) << "  Pipeline: " << (m_pipeline ? "OK" : "FAIL") << endl;
        LOG(error) << "  Source: " << (m_source ? "OK" : "FAIL") << endl;
        LOG(error) << "  Demux (" << demuxName << "): " << (m_demux ? "OK" : "FAIL") << endl;
        LOG(error) << "  Parser (" << parserName << "): " << (m_videoParser ? "OK" : "FAIL") << endl;
        LOG(error) << "  Appsink: " << (m_appsink ? "OK" : "FAIL") << endl;
        if (m_enableAudio) {
            LOG(error) << "  Audio Appsink: " << (m_audioAppsink ? "OK" : "FAIL") << endl;
        }
        destroyPipeline();
        return false;
    }

    if (m_enableAudio && !m_audioAppsink) {
        LOG(error) << "Failed to create audio appsink for stream: " << m_streamId << endl;
        destroyPipeline();
        return false;
    }

    LOG(info) << "Using demuxer: " << demuxName << endl;
    LOG(info) << "Using parser: " << parserName << endl;

    // Configure souphttpsrc for HTTP range requests
    g_object_set(G_OBJECT(m_source),
                 "location", m_s3Urls[0].first.c_str(),
                 "is-live", FALSE,
                 "automatic-redirect", TRUE,
                 "keep-alive", TRUE,
                 "blocksize", 256 * 1024,   // 256KB block size
                 "timeout", 10,              // Reduce timeout (default is 15s)
                 "retries", 3,               // Add retry logic
                 nullptr);

    // Configure appsink with appropriate codec caps
    // IMPORTANT: Use "au" (Access Unit) alignment so each buffer contains a complete frame
    // This matches the expectation of TranscodeWriter and ClipReaderProducer consumers
    string alignment = "au";
    if (!m_useAppsinkMode) {
        alignment = "nal";
    }
    GstCaps* caps = gst_caps_new_simple(codecMimeType,
                                        "stream-format", G_TYPE_STRING, "byte-stream",
                                        "alignment", G_TYPE_STRING, alignment.c_str(),
                                        nullptr);
    g_object_set(G_OBJECT(m_appsink),
                 "emit-signals", TRUE,
                 "sync", m_syncMode,
                 "max-buffers", 30,
                 "drop", FALSE,
                 "caps", caps,
                 nullptr);
    gst_caps_unref(caps);

    LOG(info) << "Appsink configured: sync=" << (m_syncMode ? "TRUE" : "FALSE") 
              << ", codec=" << codecMimeType << ", alignment=" << alignment << endl;

    // Configure audio appsink if enabled
    if (m_enableAudio && m_audioAppsink) {
        g_object_set(G_OBJECT(m_audioAppsink),
                     "emit-signals", TRUE,
                     "sync", m_syncMode,
                     "max-buffers", 30,
                     "drop", FALSE,
                     "async", FALSE,
                     nullptr);
    }

    // Add elements to pipeline
    if (m_enableAudio && m_audioAppsink) {
        gst_bin_add_many(GST_BIN(m_pipeline), m_source, m_demux, m_videoParser, m_appsink, m_audioAppsink, nullptr);
    } else {
        gst_bin_add_many(GST_BIN(m_pipeline), m_source, m_demux, m_videoParser, m_appsink, nullptr);
    }

    // Link source -> demux
    if (!gst_element_link(m_source, m_demux)) {
        LOG(error) << "Failed to link source to demux" << endl;
        destroyPipeline();
        return false;
    }

    // Link parser -> appsink
    if (!gst_element_link(m_videoParser, m_appsink)) {
        LOG(error) << "Failed to link parser to appsink" << endl;
        destroyPipeline();
        return false;
    }

    // Setup stream info for pad-added callback
    m_streamInfo = new StreamInfo{0, 0, 0.0, this};

    // Connect demux pad-added signal for dynamic linking
    g_signal_connect(m_demux, "pad-added", G_CALLBACK(onPadAdded), m_streamInfo);

    // Connect appsink new-sample signal
    g_signal_connect(m_appsink, "new-sample", G_CALLBACK(onNewSample), this);

    // Connect audio appsink new-sample signal if enabled
    if (m_enableAudio && m_audioAppsink) {
        g_signal_connect(m_audioAppsink, "new-sample", G_CALLBACK(onAudioNewSample), this);
    }

    // Setup bus watch
    GstBus* bus = gst_element_get_bus(m_pipeline);
    m_busWatchId = gst_bus_add_watch(bus, busWatch, this);
    gst_object_unref(bus);

    LOG(info) << "Single-segment pipeline created successfully for stream: " << m_streamId << endl;
    return true;
}

bool CloudStreamProducer::createMultiSegmentPipeline()
{
    std::lock_guard<std::mutex> lock(m_pipelineLock);

    if (m_pipeline) {
        LOG(warning) << "Pipeline already exists for stream: " << m_streamId << endl;
        return false;
    }

    if (m_s3Urls.empty()) {
        LOG(error) << "No segment URLs provided for multi-segment pipeline" << endl;
        return false;
    }

    // Determine element names based on configuration
    const char* demuxName = (m_container == cloud_stream::ContainerFormat::QUICKTIME) ? "qtdemux" : "matroskademux";
    const char* parserName = (m_codec == cloud_stream::VideoCodec::H264) ? "h264parse" : "h265parse";
    const char* codecMimeType = (m_codec == cloud_stream::VideoCodec::H264) ? "video/x-h264" : "video/x-h265";

    // Create pipeline and shared elements
    m_pipeline = gst_pipeline_new(("cloudstream-multi-pipeline-" + m_streamId).c_str());
    m_concat = gst_element_factory_make("concat", "concat");
    m_videoParser = gst_element_factory_make(parserName, "parser");
    m_appsink = gst_element_factory_make("appsink", "appsink");

    // Create audio appsink if audio is enabled
    if (m_enableAudio) {
        m_audioAppsink = gst_element_factory_make("appsink", "audio-appsink");
    }

    if (!m_pipeline || !m_concat || !m_videoParser || !m_appsink) {
        LOG(error) << "Failed to create GStreamer elements for multi-segment stream: " << m_streamId << endl;
        LOG(error) << "  Pipeline: " << (m_pipeline ? "OK" : "FAIL") << endl;
        LOG(error) << "  Concat: " << (m_concat ? "OK" : "FAIL") << endl;
        LOG(error) << "  Parser (" << parserName << "): " << (m_videoParser ? "OK" : "FAIL") << endl;
        LOG(error) << "  Appsink: " << (m_appsink ? "OK" : "FAIL") << endl;
        if (m_enableAudio) {
            LOG(error) << "  Audio Appsink: " << (m_audioAppsink ? "OK" : "FAIL") << endl;
        }
        destroyPipeline();
        return false;
    }

    if (m_enableAudio && !m_audioAppsink) {
        LOG(error) << "Failed to create audio appsink for multi-segment stream: " << m_streamId << endl;
        destroyPipeline();
        return false;
    }

    LOG(info) << "Creating multi-segment pipeline with " << m_s3Urls.size() << " segments" << endl;
    LOG(info) << "Using concat, demuxer: " << demuxName << " (per segment), parser: " << parserName << endl;

    // Add shared elements to pipeline
    if (m_enableAudio && m_audioAppsink) {
        gst_bin_add_many(GST_BIN(m_pipeline), m_concat, m_videoParser, m_appsink, m_audioAppsink, nullptr);
    } else {
        gst_bin_add_many(GST_BIN(m_pipeline), m_concat, m_videoParser, m_appsink, nullptr);
    }

    // Initialize pad tracking
    m_segmentPadsLinked.resize(m_s3Urls.size(), false);

    // Create souphttpsrc + demuxer for each segment
    // Pipeline: souphttpsrc → demux → (pad-added) → concat
    for (size_t i = 0; i < m_s3Urls.size(); ++i) {
        std::string sourceName = "source_" + std::to_string(i);
        std::string demuxName_i = std::string("demux_") + std::to_string(i);

        GstElement* source = gst_element_factory_make("souphttpsrc", sourceName.c_str());
        GstElement* demuxer = gst_element_factory_make(demuxName, demuxName_i.c_str());

        if (!source || !demuxer) {
            LOG(error) << "Failed to create elements for segment " << i << endl;
            destroyPipeline();
            return false;
        }

        // Configure souphttpsrc
        g_object_set(G_OBJECT(source),
                    "location", m_s3Urls[i].first.c_str(),
                    "is-live", FALSE,
                    "automatic-redirect", TRUE,
                    "keep-alive", TRUE,
                    "blocksize", 256 * 1024,   // 256KB block size
                    "timeout", 10,              // Reduce timeout (default is 15s)
                    "retries", 3,               // Add retry logic
                    nullptr);

        // Add elements to pipeline
        gst_bin_add_many(GST_BIN(m_pipeline), source, demuxer, nullptr);
        m_sources.push_back(source);
        m_demuxers.push_back(demuxer);

        // Link source → demuxer
        if (!gst_element_link(source, demuxer)) {
            LOG(error) << "Failed to link source " << i << " to demuxer" << endl;
            destroyPipeline();
            return false;
        }

        // Connect demuxer pad-added to link video pad → concat
        // Need to capture segment index for the callback
        struct SegmentLinkData {
            CloudStreamProducer* producer;
            size_t segmentIndex;
        };

        SegmentLinkData* linkData = new SegmentLinkData{this, i};

        g_signal_connect_data(demuxer, "pad-added",
            G_CALLBACK(+[](GstElement* demux, GstPad* newPad, gpointer userData) {
                SegmentLinkData* data = static_cast<SegmentLinkData*>(userData);
                if (!data || !data->producer) return;

                CloudStreamProducer* producer = data->producer;
                size_t segIdx = data->segmentIndex;

                // Check if this is a video pad
                GstCaps* caps = gst_pad_query_caps(newPad, nullptr);
                if (caps) {
                    gchar* capsStr = gst_caps_to_string(caps);
                    const char* expectedVideo = (producer->m_codec == cloud_stream::VideoCodec::H264) ?
                                               "video/x-h264" : "video/x-h265";

                    if (g_strrstr(capsStr, expectedVideo)) {
                        // This is the video pad - link to concat
                        GstPad* concatSink = gst_element_request_pad_simple(producer->m_concat, "sink_%u");

                        if (concatSink && segIdx < producer->m_segmentPadsLinked.size() && !producer->m_segmentPadsLinked[segIdx]) {
                            if (gst_pad_link(newPad, concatSink) == GST_PAD_LINK_OK) {
                                producer->m_segmentPadsLinked[segIdx] = true;
                                LOG(info) << "Segment " << segIdx << " video pad linked to concat" << endl;
                            } else {
                                LOG(error) << "Failed to link segment " << segIdx << " video pad to concat" << endl;
                            }
                            gst_object_unref(concatSink);
                        }
                    }

                    g_free(capsStr);
                    gst_caps_unref(caps);
                }
            }),
            linkData,
            +[](gpointer data, GClosure* /*closure*/) {
                delete static_cast<SegmentLinkData*>(data);
            },
            (GConnectFlags)0
        );

        LOG(verbose) << "Segment " << i << " (source → demuxer) created: "
                  << m_s3Urls[i].first.substr(0, 60) << "..." << endl;
    }

    // Link concat → parser → appsink
    if (!gst_element_link_many(m_concat, m_videoParser, m_appsink, nullptr)) {
        LOG(error) << "Failed to link concat → parser → appsink" << endl;
        destroyPipeline();
        return false;
    }

    // Configure appsink with appropriate codec caps
    // IMPORTANT: Use "au" (Access Unit) alignment so each buffer contains a complete frame
    // This matches the expectation of TranscodeWriter and ClipReaderProducer consumers
    string alignment = "au";
    if (!m_useAppsinkMode) {
        alignment = "nal";
    }
    GstCaps* caps = gst_caps_new_simple(codecMimeType,
                                        "stream-format", G_TYPE_STRING, "byte-stream",
                                        "alignment", G_TYPE_STRING, alignment.c_str(),
                                        nullptr);
    g_object_set(G_OBJECT(m_appsink),
                 "emit-signals", TRUE,
                 "sync", m_syncMode,
                 "max-buffers", 30,
                 "drop", FALSE,
                 "caps", caps,
                 nullptr);
    gst_caps_unref(caps);

    LOG(info) << "Appsink configured (multi-segment): sync=" << (m_syncMode ? "TRUE" : "FALSE")
              << ", codec=" << codecMimeType << ", alignment=" << alignment << endl;

    // Configure audio appsink if enabled
    if (m_enableAudio && m_audioAppsink) {
        g_object_set(G_OBJECT(m_audioAppsink),
                     "emit-signals", TRUE,
                     "sync", m_syncMode,
                     "max-buffers", 30,
                     "drop", FALSE,
                     "async", FALSE,
                     nullptr);
    }

    // Setup stream info for first demuxer (to extract framerate/resolution)
    m_streamInfo = new StreamInfo{0, 0, 0.0, this};
    if (!m_demuxers.empty()) {
        g_signal_connect(m_demuxers[0], "notify::n-video-streams",
            G_CALLBACK(+[](GObject* /*object*/, GParamSpec* /*pspec*/, gpointer userData) {
                StreamInfo* info = static_cast<StreamInfo*>(userData);
                if (info && info->producer) {
                    LOG(info) << "Demuxer detected video streams" << endl;
                }
            }), m_streamInfo);
    }

    // Connect appsink new-sample signal
    g_signal_connect(m_appsink, "new-sample", G_CALLBACK(onNewSample), this);

    // Connect audio appsink new-sample signal if enabled
    if (m_enableAudio && m_audioAppsink) {
        g_signal_connect(m_audioAppsink, "new-sample", G_CALLBACK(onAudioNewSample), this);
    }

    // Setup bus watch
    GstBus* bus = gst_element_get_bus(m_pipeline);
    m_busWatchId = gst_bus_add_watch(bus, busWatch, this);
    gst_object_unref(bus);

    LOG(info) << "Multi-segment pipeline created successfully for stream: " << m_streamId
              << " with " << m_s3Urls.size() << " segments" << endl;
    LOG(info) << "Pipeline structure: [souphttpsrc × " << m_s3Urls.size()
              << "] → [demux × " << m_s3Urls.size() << "] → concat → parser → appsink" << endl;
    return true;
}

void CloudStreamProducer::destroyPipeline()
{
    GstElement* pipeline = nullptr;

    {
        std::lock_guard<std::mutex> lock(m_pipelineLock);

        // Remove bus watch first to prevent callbacks during teardown
        if (m_busWatchId != 0) {
            g_source_remove(m_busWatchId);
            m_busWatchId = 0;
        }

        // Save pipeline pointer and clear member to prevent callbacks from using it
        pipeline = m_pipeline;
        m_pipeline = nullptr;

        // Clear element references (they're owned by pipeline, so just nullify)
        m_source = nullptr;
        m_concat = nullptr;
        m_sources.clear();     // Vector of element pointers (owned by pipeline)
        m_demuxers.clear();    // Vector of demuxer pointers (owned by pipeline)
        m_demux = nullptr;
        m_videoParser = nullptr;
        m_appsink = nullptr;
        m_audioAppsink = nullptr;

        // Clear pad tracking
        m_segmentPadsLinked.clear();
    }

    // Destroy pipeline outside the lock to avoid deadlock with callbacks
    if (pipeline) {
        // Set to NULL state and wait for completion (safer teardown)
        GstStateChangeReturn ret = gst_element_set_state(pipeline, GST_STATE_NULL);
        if (ret == GST_STATE_CHANGE_ASYNC) {
            // Wait for state change to complete (with timeout)
            gst_element_get_state(pipeline, nullptr, nullptr, GST_SECOND);
        }
        gst_object_unref(pipeline);
    }

    LOG(info) << "Pipeline destroyed for stream: " << m_streamId << endl;
}

bool CloudStreamProducer::createLocalFilePipeline()
{
    std::lock_guard<std::mutex> lock(m_pipelineLock);

    if (m_pipeline) {
        LOG(warning) << "Pipeline already exists for stream: " << m_streamId << endl;
        return false;
    }

    if (m_fileList.empty()) {
        LOG(error) << "No files provided for local file pipeline" << endl;
        return false;
    }

    LOG(info) << "Creating local file pipeline with " << m_fileList.size() << " file(s)" << endl;

    // Step 1: Download ALL files from cloud if needed
    // Use parallel download for speed, but wait for all before creating pipeline
    // (GStreamer's filesrc requires files to exist before pipeline goes to PAUSED)
    if (!m_fileList.empty())
    {
        LOG(info) << "Cloud storage enabled, checking files..." << endl;

        // Collect all files that need downloading
        std::vector<std::pair<std::string, std::string>> filesToDownload;
        for (const auto& fileInfo : m_fileList)
        {
            if (!fileInfo.m_objectId.empty() && !isFileExist(fileInfo.m_filePath))
            {
                filesToDownload.emplace_back(fileInfo.m_objectId, fileInfo.m_filePath);
                LOG(info) << "File needs download: " << fileInfo.m_objectId
                          << " -> " << fileInfo.m_filePath << endl;
            }
            else if (!isFileExist(fileInfo.m_filePath))
            {
                LOG(error) << "File does not exist and no cloud object: " << fileInfo.m_filePath << endl;
                return false;
            }
            else
            {
                LOG(info) << "File already exists locally: " << fileInfo.m_filePath << endl;
            }
        }

        // Download ALL files before creating pipeline
        // (filesrc requires files to exist when pipeline goes to PAUSED state)
        if (!filesToDownload.empty())
        {
            LOG(info) << "Downloading " << filesToDownload.size() << " file(s) from cloud..." << endl;
            LOG(info) << "NOTE: All files must be downloaded before playback can start" << endl;
            LOG(info) << "      (filesrc requires files to exist, unlike souphttpsrc)" << endl;

            auto downloadCallback = [](const std::string& remote_path, const nv_vms::DownloadResult& result) {
                if (result.overall_success) {
                    LOG(info) << "Downloaded: " << remote_path << endl;
                } else {
                    LOG(error) << "Download FAILED: " << remote_path
                              << " - " << result.error_message << endl;
                }
            };

            // Download all files in PARALLEL using multiple threads
            // This is much faster than sequential downloads
            LOG(info) << "Starting parallel download of " << filesToDownload.size() << " files..." << endl;

            std::vector<std::thread> downloadThreads;
            std::atomic<int> successCount{0};
            std::atomic<int> failCount{0};

            for (const auto& filePair : filesToDownload) {
                downloadThreads.emplace_back([this, filePair, &successCount, &failCount, downloadCallback]() {
                    // downloadFile returns FileResult (StorageResult), not DownloadResult
                    nv_vms::FileResult fileResult = m_unifiedStorageReader->downloadFile(
                        filePair.first, filePair.second);

                    // Convert FileResult to DownloadResult for callback
                    nv_vms::DownloadResult result;
                    result.overall_success = fileResult.success;
                    result.error_message = fileResult.message;
                    result.total_bytes_downloaded = 0;  // FileResult doesn't have size field

                    if (fileResult.success) {
                        successCount++;
                    } else {
                        failCount++;
                    }

                    if (downloadCallback) {
                        downloadCallback(filePair.first, result);
                    }
                });
            }
            // Wait for all downloads to complete
            for (auto& thread : downloadThreads) {
                thread.join();
            }

            LOG(info) << "Parallel downloads complete: " << successCount << " succeeded, "
                      << failCount << " failed" << endl;

            bool downloadSuccess = (successCount > 0);  // At least one file succeeded

            if (!downloadSuccess) {
                LOG(warning) << "Some files failed to download - will use only available files" << endl;
            }
            else
            {
                LOG(info) << "All " << filesToDownload.size() << " file(s) downloaded successfully" << endl;
            }

            // Verify which files actually exist after download attempt
            // Remove non-existent files from m_fileList
            auto it = m_fileList.begin();
            while (it != m_fileList.end())
            {
                if (!isFileExist(it->m_filePath))
                {
                    LOG(warning) << "Removing unavailable file from playlist: " << it->m_filePath << endl;
                    it = m_fileList.erase(it);
                }
                else
                {
                    ++it;
                }
            }

            // Check if we have at least one file
            if (m_fileList.empty())
            {
                LOG(error) << "No files available after download - cannot create pipeline" << endl;
                return false;
            }

            LOG(info) << "Pipeline will use " << m_fileList.size() << " available file(s)" << endl;
        }
        else
        {
            LOG(info) << "All files already exist locally, no downloads needed" << endl;
        }
    }

    // Step 2: Create GStreamer pipeline with filesrc
    const char* demuxName = (m_container == cloud_stream::ContainerFormat::QUICKTIME) ? "qtdemux" : "matroskademux";
    const char* parserName = (m_codec == cloud_stream::VideoCodec::H264) ? "h264parse" : "h265parse";
    const char* codecMimeType = (m_codec == cloud_stream::VideoCodec::H264) ? "video/x-h264" : "video/x-h265";

    m_pipeline = gst_pipeline_new(("localfile-pipeline-" + m_streamId).c_str());

    if (m_fileList.size() > 1) {
        // Multi-file: use concat
        m_concat = gst_element_factory_make("concat", "concat");
        if (!m_concat) {
            LOG(error) << "Failed to create concat element" << endl;
            return false;
        }
    }

    m_videoParser = gst_element_factory_make(parserName, "parser");
    m_appsink = gst_element_factory_make("appsink", "appsink");

    if (!m_pipeline || !m_videoParser || !m_appsink) {
        LOG(error) << "Failed to create pipeline elements" << endl;
        destroyPipeline();
        return false;
    }

    // Create audio appsink if needed
    if (m_enableAudio) {
        m_audioAppsink = gst_element_factory_make("appsink", "audio-appsink");
        if (!m_audioAppsink) {
            LOG(error) << "Failed to create audio appsink" << endl;
            destroyPipeline();
            return false;
        }
    }

    LOG(info) << "Creating pipeline: filesrc(s) → " << demuxName << " → concat → " << parserName << " → appsink" << endl;

    // Add shared elements to pipeline
    if (m_enableAudio && m_audioAppsink) {
        if (m_concat) {
            gst_bin_add_many(GST_BIN(m_pipeline), m_concat, m_videoParser, m_appsink, m_audioAppsink, nullptr);
        } else {
            gst_bin_add_many(GST_BIN(m_pipeline), m_videoParser, m_appsink, m_audioAppsink, nullptr);
        }
    } else {
        if (m_concat) {
            gst_bin_add_many(GST_BIN(m_pipeline), m_concat, m_videoParser, m_appsink, nullptr);
        } else {
            gst_bin_add_many(GST_BIN(m_pipeline), m_videoParser, m_appsink, nullptr);
        }
    }

    // Initialize pad tracking for multi-file
    std::vector<GstPad*> concatSinkPads;
    if (m_concat) {
        m_segmentPadsLinked.resize(m_fileList.size(), false);

        // PRE-REQUEST all concat sink pads in order
        // This ensures sink_0, sink_1, sink_2, ... are assigned in file order
        // Otherwise, pad-added signals arrive in random order and files play out of sequence!
        for (size_t i = 0; i < m_fileList.size(); ++i) {
            GstPad* sinkPad = gst_element_request_pad_simple(m_concat, "sink_%u");
            concatSinkPads.push_back(sinkPad);
            gchar* padName = gst_pad_get_name(sinkPad);
            LOG(info) << "Pre-requested concat pad " << padName << " for file " << i << endl;
            g_free(padName);
        }
    }

    // Create filesrc + demuxer for each file
    for (size_t i = 0; i < m_fileList.size(); ++i) {
        std::string sourceName = "filesrc_" + std::to_string(i);
        std::string demuxName_i = std::string("demux_") + std::to_string(i);

        GstElement* source = gst_element_factory_make("filesrc", sourceName.c_str());
        GstElement* demuxer = gst_element_factory_make(demuxName, demuxName_i.c_str());

        if (!source || !demuxer) {
            LOG(error) << "Failed to create filesrc/demuxer for file " << i << endl;
            destroyPipeline();
            return false;
        }

        // Configure filesrc with local file path
        g_object_set(G_OBJECT(source),
                    "location", m_fileList[i].m_filePath.c_str(),
                    nullptr);

        LOG(info) << "File " << i << ": " << m_fileList[i].m_filePath
                  << " (start: " << m_fileList[i].m_startTime << "ms)" << endl;

        // Add elements to pipeline
        gst_bin_add_many(GST_BIN(m_pipeline), source, demuxer, nullptr);
        m_sources.push_back(source);
        m_demuxers.push_back(demuxer);

        // Link source → demuxer
        if (!gst_element_link(source, demuxer)) {
            LOG(error) << "Failed to link filesrc " << i << " to demuxer" << endl;
            destroyPipeline();
            return false;
        }

        // Connect demuxer pad-added to link video pad → concat (or parser if single file)
        if (m_concat) {
            // Multi-file: connect to concat
            struct SegmentLinkData {
                CloudStreamProducer* producer;
                size_t segmentIndex;
                GstPad* concatSinkPad;  // Pre-requested pad
            };

            SegmentLinkData* linkData = new SegmentLinkData{this, i, concatSinkPads[i]};

            g_signal_connect_data(demuxer, "pad-added",
                G_CALLBACK(+[](GstElement* src, GstPad* newPad, gpointer userData) {
                    SegmentLinkData* data = static_cast<SegmentLinkData*>(userData);
                    CloudStreamProducer* producer = data->producer;
                    size_t idx = data->segmentIndex;

                    gchar* padName = gst_pad_get_name(newPad);
                    LOG(info) << "Pad added on demuxer " << idx << ": " << padName << endl;

                    if (g_str_has_prefix(padName, "video")) {
                        // Link to pre-requested concat sink pad
                        // This ensures files play in order (not in pad-added signal order)
                        GstPad* concatSinkPad = data->concatSinkPad;  // Already requested in file order

                        if (concatSinkPad && idx < producer->m_segmentPadsLinked.size()) {
                            gchar* concatPadName = gst_pad_get_name(concatSinkPad);
                            if (GST_PAD_LINK_OK == gst_pad_link(newPad, concatSinkPad)) {
                                LOG(info) << "Linked demuxer " << idx << " video pad to concat pad "
                                          << concatPadName << " (file order preserved)" << endl;
                                producer->m_segmentPadsLinked[idx] = true;
                            } else {
                                LOG(error) << "Failed to link demuxer " << idx << " to concat" << endl;
                            }
                            g_free(concatPadName);
                        }
                    } else if (g_str_has_prefix(padName, "audio") && producer->m_audioAppsink) {
                        // Link audio to audio appsink (TODO: implement if needed)
                        LOG(info) << "Audio pad detected on demuxer " << idx << " (not yet implemented)" << endl;
                    }

                    g_free(padName);
                }),
                linkData,
                +[](gpointer data, GClosure* /*closure*/) { delete static_cast<SegmentLinkData*>(data); },
                (GConnectFlags)0);
        } else {
            // Single file: connect directly to parser
            g_signal_connect(demuxer, "pad-added", G_CALLBACK(+[](GstElement* src, GstPad* newPad, gpointer userData) {
                CloudStreamProducer* producer = static_cast<CloudStreamProducer*>(userData);

                gchar* padName = gst_pad_get_name(newPad);
                LOG(info) << "Pad added: " << padName << endl;

                if (g_str_has_prefix(padName, "video")) {
                    GstPad* parserSinkPad = gst_element_get_static_pad(producer->m_videoParser, "sink");
                    if (parserSinkPad) {
                        if (GST_PAD_LINK_OK == gst_pad_link(newPad, parserSinkPad)) {
                            LOG(info) << "Linked demuxer video pad to parser" << endl;
                        }
                        gst_object_unref(parserSinkPad);
                    }
                }

                g_free(padName);
            }), this);
        }
    }

    // Link concat → parser → appsink (for multi-file) or just parser → appsink (for single file)
    if (m_concat) {
        if (!gst_element_link(m_concat, m_videoParser)) {
            LOG(error) << "Failed to link concat to parser" << endl;
            destroyPipeline();
            return false;
        }
    }

    if (!gst_element_link(m_videoParser, m_appsink)) {
        LOG(error) << "Failed to link parser to appsink" << endl;
        destroyPipeline();
        return false;
    }

    // Configure appsink
    string alignment = m_useAppsinkMode ? "au" : "nal";
    GstCaps* caps = gst_caps_new_simple(codecMimeType,
                                        "stream-format", G_TYPE_STRING, "byte-stream",
                                        "alignment", G_TYPE_STRING, alignment.c_str(),
                                        nullptr);
    g_object_set(G_OBJECT(m_appsink),
                 "emit-signals", TRUE,
                 "sync", m_syncMode,
                 "max-buffers", 30,
                 "drop", FALSE,
                 "caps", caps,
                 nullptr);
    gst_caps_unref(caps);

    LOG(info) << "Appsink configured: sync=" << (m_syncMode ? "TRUE" : "FALSE")
              << ", codec=" << codecMimeType << ", alignment=" << alignment << endl;

    // Configure audio appsink if enabled
    if (m_enableAudio && m_audioAppsink) {
        g_object_set(G_OBJECT(m_audioAppsink),
                     "emit-signals", TRUE,
                     "sync", m_syncMode,
                     "max-buffers", 30,
                     "drop", FALSE,
                     "async", FALSE,
                     nullptr);
    }

    // Setup stream info
    m_streamInfo = new StreamInfo{0, 0, 0.0, this};

    // Monitor concat's active-pad to track which file is currently playing
    if (m_concat) {
        g_signal_connect(m_concat, "notify::active-pad",
            G_CALLBACK(+[](GObject* object, GParamSpec* /*pspec*/, gpointer userData) {
                CloudStreamProducer* producer = static_cast<CloudStreamProducer*>(userData);
                GstElement* concat = GST_ELEMENT(object);

                // Get current active pad
                GstPad* activePad = nullptr;
                g_object_get(concat, "active-pad", &activePad, nullptr);

                if (activePad) {
                    gchar* padName = gst_pad_get_name(activePad);

                    // Extract sink pad number (sink_0, sink_1, etc.)
                    int fileIndex = -1;
                    std::string padNameStr(padName);
                    constexpr std::string_view sinkPrefix = "sink_";
                    if (padNameStr.compare(0, sinkPrefix.length(), sinkPrefix) == 0)
                    {
                        try
                        {
                            fileIndex = std::stoi(padNameStr.substr(sinkPrefix.length()));
                        } 
                        catch (const std::exception& e)
                        {
                            LOG(warning) << "Failed to parse pad index from: " << padName << endl;
                        }
                    }
                    if (fileIndex >= 0 && fileIndex < static_cast<int>(producer->m_fileList.size()))
                    {
                        // Update file start time for current file
                        //producer->m_fileStartTime = producer->m_fileList[fileIndex].m_startTime;
                        int64_t fileStartTime = producer->m_fileList[fileIndex].m_startTime;
                        LOG(warning) << "#### concat switched to " << padName
                                  << " (File " << fileIndex << ")"
                                  << ", fileStartTime: " << fileStartTime
                                  << "ms ####" << endl;
                    }

                    g_free(padName);
                    gst_object_unref(activePad);
                }
            }), this);

        LOG(info) << "Monitoring concat active-pad to track file transitions" << endl;
    }

    // Connect appsink callbacks
    g_signal_connect(m_appsink, "new-sample", G_CALLBACK(onNewSample), this);
    if (m_enableAudio && m_audioAppsink) {
        g_signal_connect(m_audioAppsink, "new-sample", G_CALLBACK(onAudioNewSample), this);
    }

    // Setup bus watch
    GstBus* bus = gst_element_get_bus(m_pipeline);
    m_busWatchId = gst_bus_add_watch(bus, busWatch, this);
    gst_object_unref(bus);

    LOG(info) << "Local file pipeline created successfully for " << m_fileList.size()
              << " file(s), stream: " << m_streamId << endl;
    return true;
}

bool CloudStreamProducer::seek(double positionSeconds)
{
    // This function works for ALL pipeline types:
    // 1. HTTP single-segment:  souphttpsrc → demux → parse → appsink
    // 2. HTTP multi-segment:   souphttpsrc(s) → demux(s) → concat → parse → appsink
    // 3. Local file single:    filesrc → demux → parse → appsink
    // 4. Local file multi:     filesrc(s) → demux(s) → concat → parse → appsink
    //
    // All use the same GStreamer seek mechanism (gst_element_seek on pipeline)
    // Each source type (souphttpsrc, filesrc) handles seeking appropriately

    if (!m_pipeline) {
        LOG(error) << "Cannot seek - pipeline not created" << endl;
        return false;
    }

    if (m_stop.load()) {
        LOG(error) << "Cannot seek - pipeline is stopped" << endl;
        return false;
    }

    // Check if pipeline is in seekable state (PAUSED or PLAYING)
    GstState currentState = m_state.load();
    if (currentState != GST_STATE_PAUSED && currentState != GST_STATE_PLAYING) {
        LOG(error) << "Cannot seek - pipeline not in PAUSED or PLAYING state (current: "
                  << gst_element_state_get_name(currentState) << ")" << endl;
        return false;
    }
    // For multi-file/multi-segment pipelines (concat), log which file/segment
    bool isMultiFile = (m_useLocalFiles && m_fileList.size() > 1) ||
                       (m_useMultiSegment && m_s3Urls.size() > 1);

    if (isMultiFile) {
        if (m_useLocalFiles && !m_fileList.empty()) {
            // Local files: Calculate which file based on m_fileList
            int64_t seekEpochMs = m_fileList[0].m_startTime + static_cast<int64_t>(positionSeconds * 1000);

            size_t targetFile = 0;
            for (size_t i = 0; i < m_fileList.size(); ++i) {
                int64_t fileStart = m_fileList[i].m_startTime;
                int64_t fileEnd = (i + 1 < m_fileList.size()) ?
                                  m_fileList[i + 1].m_startTime :
                                  INT64_MAX;

                if (seekEpochMs >= fileStart && seekEpochMs < fileEnd) {
                    targetFile = i;
                    break;
                }
            }

            LOG(info) << "Local file seek: target File " << targetFile
                      << " (concat will switch to sink_" << targetFile << ")" << endl;
        }
        else if (m_useMultiSegment && !m_s3Urls.empty()) {
            // HTTP segments: Calculate which segment
            int64_t firstSegmentStart = m_s3Urls[0].second;
            int64_t seekEpochMs = firstSegmentStart + static_cast<int64_t>(positionSeconds * 1000);

            size_t targetSegment = 0;
            for (size_t i = 0; i < m_s3Urls.size(); ++i) {
                int64_t segmentStart = m_s3Urls[i].second;
                int64_t segmentEnd = (i + 1 < m_s3Urls.size()) ?
                                     m_s3Urls[i + 1].second :
                                     INT64_MAX;

                if (seekEpochMs >= segmentStart && seekEpochMs < segmentEnd) {
                    targetSegment = i;
                    break;
                }
            }

            LOG(info) << "HTTP segment seek: target Segment " << targetSegment
                      << " (concat will switch to sink_" << targetSegment << ")" << endl;
        }

        // Note: concat element automatically switches to the correct sink pad based on timestamp
    }

    // Log pipeline type for debugging
    std::string pipelineType = m_useLocalFiles ? "local file (filesrc + concat)" :
                                m_useMultiSegment ? "multi-segment HTTP (souphttpsrc + concat)" :
                                "single-segment HTTP (souphttpsrc)";

    LOG(info) << "Seeking " << pipelineType << " pipeline to position: " << positionSeconds << "s" << endl;

    // Reset end-time tracking for new seek position
    m_endTimeEosSent.store(false);
    m_firstFramePtsNs.store(GST_CLOCK_TIME_NONE);
    m_prevFramePtsNs.store(0);

    // Perform seek with flush to clear buffered data
    gboolean seekResult = gst_element_seek(m_pipeline,
        1.0,                                    // playback rate
        GST_FORMAT_TIME,
        (GstSeekFlags)(GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_KEY_UNIT),
        GST_SEEK_TYPE_SET, secondsToNs(positionSeconds),
        GST_SEEK_TYPE_NONE, GST_CLOCK_TIME_NONE  // Don't change end position
    );

    if (seekResult) {
        LOG(info) << "Seek to " << positionSeconds << "s successful" << endl;
    } else {
        LOG(error) << "Seek to " << positionSeconds << "s failed" << endl;

        // Try fallback seek on demuxer/concat element
        GstElement* seekTarget = m_useMultiSegment ? m_concat : m_demux;
        if (seekTarget) {
            LOG(warning) << "Retrying seek on " << (m_useMultiSegment ? "concat" : "demux") << " element" << endl;
            seekResult = gst_element_seek(seekTarget,
                1.0,
                GST_FORMAT_TIME,
                (GstSeekFlags)(GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_KEY_UNIT),
                GST_SEEK_TYPE_SET, secondsToNs(positionSeconds),
                GST_SEEK_TYPE_NONE, GST_CLOCK_TIME_NONE
            );
            LOG(info) << "Fallback seek result: " << (seekResult ? "SUCCESS" : "FAILED") << endl;
        }
    }

    return seekResult;
}

double CloudStreamProducer::getCurrentPosition() const
{
    if (!m_pipeline) {
        return -1.0;
    }

    gint64 position = 0;
    if (!gst_element_query_position(m_pipeline, GST_FORMAT_TIME, &position)) {
        LOG(verbose) << "Failed to query pipeline position" << endl;
        return -1.0;
    }
    return (double)position / GST_SECOND;
}

void CloudStreamProducer::distributeFrameToConsumers(FrameParams& frameParams)
{
    std::lock_guard<std::mutex> lock(m_consumerLock);
    
    // Legacy mode: broadcast to all consumers (for live playback)
    for (auto& pair : m_consumersMap) {
        if (pair.second) {
            pair.second->onFrame(frameParams);
        }
    }
}

std::string CloudStreamProducer::getState() const
{
    switch (m_state.load()) {
        case GST_STATE_NULL: return "NULL";
        case GST_STATE_READY: return "READY";
        case GST_STATE_PAUSED: return "PAUSED";
        case GST_STATE_PLAYING: return "PLAYING";
        default: return "UNKNOWN";
    }
}

// Static callback: handle new sample from appsink
GstFlowReturn CloudStreamProducer::onNewSample(GstElement* sink, gpointer userData)
{
    CloudStreamProducer* producer = static_cast<CloudStreamProducer*>(userData);
    if (!producer) {
        return GST_FLOW_ERROR;
    }

    GstSample* sample;
    g_signal_emit_by_name(sink, "pull-sample", &sample);
    if (!sample) {
        return GST_FLOW_ERROR;
    }

    // Check if pipeline is being destroyed or stopped
    if (producer->m_stop.load() || !producer->m_pipeline) {
        gst_sample_unref(sample);
        return GST_FLOW_OK;
    }

    GstBuffer* buffer = gst_sample_get_buffer(sample);
    if (!buffer) {
        gst_sample_unref(sample);
        return GST_FLOW_ERROR;
    }

    GstClockTime pts = GST_BUFFER_PTS(buffer);
    if (GST_BUFFER_PTS_IS_VALID(buffer)) {
        producer->m_prevFramePtsNs.store(GST_BUFFER_PTS(buffer));
    } else {
        pts = producer->m_prevFramePtsNs.load();
    }
    bool isEpochTimestamp = ((pts / 1000000) > 946684800);  // Jan 1, 2000 in epoch ms

    // COMMON: Map buffer once and use for both NAL filtering and processing
    GstMapInfo map;
    if (!gst_buffer_map(buffer, &map, GST_MAP_READ)) {
        gst_sample_unref(sample);
        return GST_FLOW_ERROR;
    }

    // Track first frame PTS for relative time calculation
    if (producer->m_firstFramePtsNs.load() == GST_CLOCK_TIME_NONE)
    {
        producer->m_firstFramePtsNs.store(pts);
        LOG(info) << "CloudStream: First frame PTS = " << (pts/1000000) << ", fileStartTime = " << producer->m_fileStartTime << endl;
    }
 
    // Check if we're in appsink mode (for clip download use-case)
    if (producer->m_useAppsinkMode) {
        // Appsink mode: forward GstSample/GstBuffer to consumers as RawFrameParams
        // This is used for clip download where consumers need GstBuffer for transcoding

        // Detect if timestamp is relative (< 1 hour) or absolute epoch (> year 2000)
        // Relative timestamps need FIXED_TS_OFFSET added to match splitmuxsrc behavior
        // Epoch timestamps should be kept as-is
        
        // Unmap the original buffer before processing
        gst_buffer_unmap(buffer, &map);

        GstBuffer* finalBuffer = nullptr;
        GstSample* finalSample = nullptr;

        // Create a copy of buffer with adjusted timestamps
        GstBuffer* offsetBuffer = gst_buffer_copy(buffer);
        if (!isEpochTimestamp)
        {
            if (GST_BUFFER_PTS_IS_VALID(offsetBuffer))
            {
                GST_BUFFER_PTS(offsetBuffer) = GST_BUFFER_PTS(offsetBuffer) + FIXED_TS_OFFSET;
            }
            if (GST_BUFFER_DTS_IS_VALID(offsetBuffer))
            {
                GST_BUFFER_DTS(offsetBuffer) = GST_BUFFER_DTS(offsetBuffer) + FIXED_TS_OFFSET;
            }
        }
        else
        {
            GstClockTime firstPtsNs = producer->m_firstFramePtsNs.load();
            if (GST_BUFFER_PTS_IS_VALID(buffer) && firstPtsNs != GST_CLOCK_TIME_NONE)
            {
                // Skip out-of-order frames (B-frames with PTS earlier than first frame)
                if (pts < firstPtsNs)
                {
                    gst_buffer_unref(offsetBuffer);
                    gst_sample_unref(sample);
                    return GST_FLOW_OK;
                }

                // Convert to offset format: relative time from first frame in nanoseconds
                GstClockTime offsetPtsNs = (pts - producer->m_fileStartTime*1000*1000);
                GST_BUFFER_PTS(offsetBuffer) = offsetPtsNs + FIXED_TS_OFFSET;
                if (GST_BUFFER_DTS_IS_VALID(buffer))
                {
                    GST_BUFFER_DTS(offsetBuffer) = offsetPtsNs + FIXED_TS_OFFSET;
                }
            }
        }

        // Create a new sample with the offset buffer
        GstCaps* sampleCaps = gst_sample_get_caps(sample);
        finalSample = gst_sample_new(offsetBuffer, sampleCaps, nullptr, nullptr);
        gst_buffer_unref(offsetBuffer);  // Sample now owns the buffer
        finalBuffer = gst_sample_get_buffer(finalSample);

        auto frameData = std::make_shared<RawFrameParams>();
        frameData->m_sample = finalSample;  // Will be unref'd in RawFrameParams destructor
        frameData->m_gstBuffer = finalBuffer;

        // Map the final buffer
        if (gst_buffer_map(frameData->m_gstBuffer, &frameData->m_map, GST_MAP_READ))
        {
            // Map succeeded; will be unmapped in RawFrameParams destructor
        }

        frameData->pts = GST_BUFFER_PTS_IS_VALID(frameData->m_gstBuffer) ? (GST_BUFFER_PTS(frameData->m_gstBuffer)/1000000) : -1;
        frameData->m_sourceWidth = producer->m_width.load();
        frameData->m_sourceHeight = producer->m_height.load();
        frameData->m_streamId = producer->m_streamId;

        LOG(verbose) << "CloudStream video: PTS=" << frameData->pts << ", GstBufPts=" << pts/1000000
                  << ", size=" << frameData->m_map.size << " bytes" << endl;

        // Route video frames specifically to the "video" consumer (like ClipReaderProducer)
        producer->distributeToConsumer(frameData, "video");

        // Check if we've reached the end time (calculate relative time from first frame)
        if (producer->m_endTime > 0 && pts != GST_CLOCK_TIME_NONE)
        {
            double duration_ms = 0.0, relative_time_ms = 0.0;
            if (isEpochTimestamp) {
                int64_t pts_ms = pts / 1000000;
                relative_time_ms = pts_ms - producer->m_fileStartTime;  // Relative time from first frame in ms
                duration_ms = producer->m_endTime * 1000.0;
            } else {
                gint64 firstPts = producer->m_firstFramePtsNs.load();
                relative_time_ms = (pts - firstPts) / 1000000.0;  // Relative time from first frame in ms

                double firstFrameTimeSec = firstPts / 1000000000.0;
                duration_ms = (producer->m_endTime - firstFrameTimeSec) * 1000.0;
            }

            if (relative_time_ms >= duration_ms)
            {
                // Log and send EOS event to pipeline (only once)
                if (!producer->m_endTimeEosSent.exchange(true))
                {
                    LOG(info) << "CloudStream: Reached end time (relative: " << relative_time_ms 
                             << "ms >= duration: " << duration_ms << "ms), sending EOS to pipeline" << endl;

                    // Send EOS event to pipeline to stop it (matches ClipReaderProducer behavior)
                    if (producer->m_pipeline)
                    {
                        gst_element_send_event(producer->m_pipeline, gst_event_new_eos());
                    }
                    producer->m_stop.store(true);
                }
                // Drop this frame and any subsequent frames  
                // Note: frameData shared_ptr will be destroyed when we return, 
                // and its destructor will properly unmap buffer and unref finalSample
                // We only need to unref the original sample if we created a new one
                gst_sample_unref(sample);  // Original sample (not used in frameData)
                // If !needsOffset, finalSample IS sample (ref'd), so don't double-unref
                return GST_FLOW_EOS;
            }
        }

        // Cleanup: finalSample is owned by frameData and will be unref'd in destructor
        // If we didn't use the original sample (i.e., we created a new one with offset), unref it here
        gst_sample_unref(sample);  // Original sample not used, unref it
        // else: original sample was ref'd and stored in frameData, will be unref'd by destructor
        return GST_FLOW_OK;
    }

    // Legacy mode: forward as FrameParams (for live playback)
    // Note: Buffer already mapped above
    LOG(verbose) << "CloudStream legacy mode PTS: " << convertEpocToISO8601_2(pts/1000)
        << ", GST time: " << pts/1000000 << ", size: " << map.size << " bytes" << endl;

    // Prepare frame params for consumers
    FrameParams frameParams;
    frameParams.m_media = "video";
    frameParams.m_codec = producer->getCodecString();
    frameParams.m_buffer = map.data;
    frameParams.m_size = map.size;
    frameParams.m_width = producer->m_width.load();
    frameParams.m_height = producer->m_height.load();
    frameParams.m_frameNum = producer->m_frameNum.load();

    // Set presentation time
    if (pts != GST_CLOCK_TIME_NONE)
    {
        producer->m_presentationTime.tv_sec = pts/1000000;
        producer->m_presentationTime.tv_usec = pts%1000000;
    }
    frameParams.m_presentationTime = producer->m_presentationTime;

    // Distribute to consumers (buffer stays mapped during this call)
    producer->distributeFrameToConsumers(frameParams);

    // Check if we've reached the end time (calculate relative time from first frame)
    if (producer->m_endTime > 0 && pts != GST_CLOCK_TIME_NONE)
    {
        double duration_ms = 0.0, relative_time_ms = 0.0;
        if (isEpochTimestamp) {
            int64_t pts_ms = pts / 1000000;
            relative_time_ms = pts_ms - producer->m_fileStartTime;  // Relative time from first frame in ms
            duration_ms = producer->m_endTime * 1000.0;
        } else {
            gint64 firstPts = producer->m_firstFramePtsNs.load();
            relative_time_ms = (pts - firstPts) / 1000000.0;  // Relative time from first frame in ms

            double firstFrameTimeSec = firstPts / 1000000000.0;
            duration_ms = (producer->m_endTime - firstFrameTimeSec) * 1000.0;
        }

        if (relative_time_ms >= duration_ms)
        {
            // Log and send EOS event to pipeline (only once)
            if (!producer->m_endTimeEosSent.exchange(true))
            {
                LOG(info) << "CloudStream: Reached end time (relative: " << relative_time_ms 
                         << "ms >= duration: " << duration_ms << "ms), sending EOS to pipeline" << endl;

                // Send EOS event to pipeline to stop it (matches ClipReaderProducer behavior)
                if (producer->m_pipeline)
                {
                    gst_element_send_event(producer->m_pipeline, gst_event_new_eos());
                }
                producer->m_stop.store(true);
            }
            // Drop this frame and any subsequent frames
            gst_buffer_unmap(buffer, &map);
            gst_sample_unref(sample);
            return GST_FLOW_EOS;
        }
    }

    // Unmap buffer and unref sample after consumers are done
    gst_buffer_unmap(buffer, &map);
    gst_sample_unref(sample);

    return GST_FLOW_OK;
}

// Static callback: handle new audio sample from appsink
GstFlowReturn CloudStreamProducer::onAudioNewSample(GstElement* sink, gpointer userData)
{
    CloudStreamProducer* producer = static_cast<CloudStreamProducer*>(userData);
    if (!producer) {
        return GST_FLOW_ERROR;
    }

    GstSample* sample;
    g_signal_emit_by_name(sink, "pull-sample", &sample);
    if (!sample) {
        return GST_FLOW_ERROR;
    }

    // Check if pipeline is being destroyed or stopped
    if (producer->m_stop.load() || !producer->m_pipeline) {
        gst_sample_unref(sample);
        return GST_FLOW_OK;
    }

    GstBuffer* buffer = gst_sample_get_buffer(sample);
    if (!buffer) {
        gst_sample_unref(sample);
        return GST_FLOW_ERROR;
    }

    GstClockTime pts = GST_BUFFER_PTS(buffer);
    gint64 pts_ms = GST_BUFFER_PTS_IS_VALID(buffer) ? (GST_BUFFER_PTS(buffer) / 1000000) : 0;
    bool isEpochTimestamp = (pts_ms > 946684800000);  // Jan 1, 2000 in epoch ms

    // For appsink mode, forward audio as RawFrameParams
    if (producer->m_useAppsinkMode) {
        // Apply same offset logic as video frames to keep timestamps synchronized
        GstBuffer* finalBuffer = nullptr;
        GstSample* finalSample = nullptr;

        // Create a copy of buffer with adjusted timestamps (same as video)
        GstBuffer* offsetBuffer = gst_buffer_copy(buffer);
        if (!isEpochTimestamp)
        {
            // Relative timestamps: add FIXED_TS_OFFSET
            if (GST_BUFFER_PTS_IS_VALID(offsetBuffer))
            {
                GST_BUFFER_PTS(offsetBuffer) = GST_BUFFER_PTS(offsetBuffer) + FIXED_TS_OFFSET;
            }
            if (GST_BUFFER_DTS_IS_VALID(offsetBuffer))
            {
                GST_BUFFER_DTS(offsetBuffer) = GST_BUFFER_DTS(offsetBuffer) + FIXED_TS_OFFSET;
            }
        }
        else
        {
            // Epoch timestamps: convert to relative from first frame
            GstClockTime firstPtsNs = producer->m_firstFramePtsNs.load();
            if (GST_BUFFER_PTS_IS_VALID(buffer) && firstPtsNs != GST_CLOCK_TIME_NONE)
            {
                // Skip out-of-order audio frames (PTS earlier than first video frame)
                if (pts < firstPtsNs)
                {
                    gst_buffer_unref(offsetBuffer);
                    gst_sample_unref(sample);
                    return GST_FLOW_OK;
                }

                // Convert to offset format: relative time from first frame in nanoseconds
                GstClockTime offsetPtsNs = (pts - producer->m_fileStartTime*1000*1000);
                GST_BUFFER_PTS(offsetBuffer) = offsetPtsNs + FIXED_TS_OFFSET;
                // Only set DTS if original buffer had valid DTS
                if (GST_BUFFER_DTS_IS_VALID(buffer))
                {
                    GST_BUFFER_DTS(offsetBuffer) = offsetPtsNs + FIXED_TS_OFFSET;
                }
            }
        }

        // Create a new sample with the offset buffer
        GstCaps* sampleCaps = gst_sample_get_caps(sample);
        finalSample = gst_sample_new(offsetBuffer, sampleCaps, nullptr, nullptr);
        gst_buffer_unref(offsetBuffer);  // Sample now owns the buffer
        finalBuffer = gst_sample_get_buffer(finalSample);

        auto frameData = std::make_shared<RawFrameParams>();
        frameData->m_sample = finalSample;  // Will be unref'd in RawFrameParams destructor
        frameData->m_gstBuffer = finalBuffer;

        if (frameData->m_gstBuffer && gst_buffer_map(frameData->m_gstBuffer, &frameData->m_map, GST_MAP_READ)) {
            // Map succeeded; will be unmapped in RawFrameParams destructor
        }

        frameData->pts = GST_BUFFER_PTS_IS_VALID(frameData->m_gstBuffer) ? (GST_BUFFER_PTS(frameData->m_gstBuffer)/1000000) : -1;
        frameData->m_streamId = producer->m_streamId;

        // Propagate caps for audio (critical for decoding/typefinding)
        GstCaps* caps = gst_sample_get_caps(finalSample);
        if (caps) {
            frameData->m_caps = gst_caps_ref(caps);
        }
        LOG(verbose) << "CloudStream audio: PTS=" << frameData->pts << "ms, size="
                     << frameData->m_map.size << " bytes" << endl;

        // Route audio frames specifically to the "audio" consumer (like ClipReaderProducer)
        producer->distributeToConsumer(frameData, "audio");

        // Cleanup: always unref original sample (we created a new offsetBuffer)
        gst_sample_unref(sample);
        return GST_FLOW_OK;
    }

    // Legacy mode (not typically used for audio, but provided for completeness)
    LOG(verbose) << "CloudStream audio (legacy mode): PTS=" << (GST_BUFFER_PTS(buffer)/1000000) << "ms" << endl;
    gst_sample_unref(sample);
    return GST_FLOW_OK;
}

// Static callback: handle dynamic pad addition from demuxer
void CloudStreamProducer::onPadAdded(GstElement* src, GstPad* newPad, gpointer userData)
{
    StreamInfo* streamInfo = static_cast<StreamInfo*>(userData);
    if (!streamInfo || !streamInfo->producer) {
        return;
    }

    CloudStreamProducer* producer = streamInfo->producer;

    // Determine expected video type based on codec
    const char* expectedVideoType = (producer->m_codec == cloud_stream::VideoCodec::H264) ? "video/x-h264" : "video/x-h265";

    // Check pad caps to determine if it's video or audio
    GstCaps* newPadCaps = gst_pad_query_caps(newPad, nullptr);
    if (!newPadCaps) {
        return;
    }

    gchar* capsStr = gst_caps_to_string(newPadCaps);
    LOG(info) << "Demux pad added: " << capsStr << endl;

    bool isVideo = false;
    bool isAudio = false;
    if (capsStr)
    {
        isVideo = (g_strrstr(capsStr, expectedVideoType) != nullptr);
        isAudio = g_str_has_prefix(capsStr, "audio/");
    }

    if (isVideo) {
        // Handle video pad
        GstPad* videoSinkPad = gst_element_get_static_pad(producer->m_videoParser, "sink");
        if (videoSinkPad && !gst_pad_is_linked(videoSinkPad)) {
            // Extract framerate and resolution from caps
            GstStructure* structure = gst_caps_get_structure(newPadCaps, 0);
            
            if (gst_structure_get_fraction(structure, "framerate",
                                          &streamInfo->framerateNum,
                                          &streamInfo->framerateDen)) {
                streamInfo->fps = (double)streamInfo->framerateNum / streamInfo->framerateDen;
                producer->m_fps.store(streamInfo->fps);
                producer->m_framerateNum = streamInfo->framerateNum;
                producer->m_framerateDen = streamInfo->framerateDen;
                LOG(info) << "Detected framerate: " << streamInfo->framerateNum << "/"
                         << streamInfo->framerateDen << " = " << streamInfo->fps << " fps" << endl;
            }

            int width, height;
            if (gst_structure_get_int(structure, "width", &width) &&
                gst_structure_get_int(structure, "height", &height)) {
                producer->m_width.store(width);
                producer->m_height.store(height);
                LOG(info) << "Detected resolution: " << width << "x" << height << endl;
            }

            // Link demux to video parser
            GstPadLinkReturn ret = gst_pad_link(newPad, videoSinkPad);
            if (GST_PAD_LINK_FAILED(ret)) {
                LOG(error) << "Failed to link demux to video parser, error: " << ret << endl;
            } else {
                LOG(info) << "Successfully linked " << expectedVideoType << " stream to parser" << endl;
            }
        }
        if (videoSinkPad) {
            gst_object_unref(videoSinkPad);
        }
    }
    else if (isAudio && producer->m_enableAudio && producer->m_audioAppsink) {
        // Handle audio pad
        GstPad* audioSinkPad = gst_element_get_static_pad(producer->m_audioAppsink, "sink");
        if (audioSinkPad && !gst_pad_is_linked(audioSinkPad)) {
            // Link demux audio pad directly to audio appsink
            GstPadLinkReturn ret = gst_pad_link(newPad, audioSinkPad);
            if (GST_PAD_LINK_FAILED(ret)) {
                LOG(error) << "Failed to link demux to audio appsink, error: " << ret << endl;
            } else {
                LOG(info) << "Successfully linked audio stream to audio appsink" << endl;
            }
        }
        if (audioSinkPad) {
            gst_object_unref(audioSinkPad);
        }
    }
    else if (isAudio && !producer->m_enableAudio) {
        LOG(info) << "Skipping audio pad (audio disabled)" << endl;
    }

    g_free(capsStr);
    gst_caps_unref(newPadCaps);
}

// Static callback: handle bus messages
gboolean CloudStreamProducer::busWatch(GstBus* bus, GstMessage* msg, gpointer userData)
{
    CloudStreamProducer* producer = static_cast<CloudStreamProducer*>(userData);
    if (!producer) {
        return TRUE;
    }

    // Check if pipeline is being destroyed (prevent race conditions)
    if (!producer->m_pipeline || !producer->m_seekData) {
        return TRUE;
    }

    switch (GST_MESSAGE_TYPE(msg)) {
        case GST_MESSAGE_EOS:
            LOG(info) << "End of stream reached for: " << producer->m_streamId << endl;
            producer->m_stop.store(true);
            // Notify completion callback if set
            if (producer->m_finishedCallback)
            {
                producer->m_finishedCallback();
            }
            break;

        case GST_MESSAGE_ERROR: {
            gchar* debug;
            GError* error;
            gst_message_parse_error(msg, &error, &debug);
            std::string errorMsg = error->message;
            LOG(error) << "Pipeline error for " << producer->m_streamId 
                      << ": " << errorMsg << endl;
            if (debug) {
                LOG(error) << "Debug: " << debug << endl;
            }
            g_free(debug);
            g_error_free(error);
            producer->m_error.store(true);
            producer->m_stop.store(true);

            // Invoke error callback if set
            if (producer->m_errorCallback) {
                producer->m_errorCallback(errorMsg, 1);  // errorCode = 1 for generic pipeline error
            }
            break;
        }

        case GST_MESSAGE_ASYNC_DONE: {
            // Pipeline is prerolled and ready for seeking
            SeekData* seekData = producer->m_seekData;
            if (!seekData->seekDone && seekData->pausedStateReached) {
                LOG(info) << "Pipeline ready for seeking" << endl;

                // Wait for pipeline to fully reach PAUSED state before seeking
                GstState cur = GST_STATE_NULL, pending = GST_STATE_NULL;
                GstStateChangeReturn ret = gst_element_get_state(producer->m_pipeline, &cur, &pending, 5 * GST_SECOND);
                if (ret == GST_STATE_CHANGE_FAILURE) {
                    LOG(error) << "Failed to get pipeline state before seeking" << endl;
                    producer->m_error.store(true);
                    break;
                }
                LOG(info) << "Pipeline state before seek: current=" << gst_element_state_get_name(cur)
                         << ", pending=" << gst_element_state_get_name(pending) << endl;

                // Query seekability and duration
                GstQuery* query = gst_query_new_seeking(GST_FORMAT_TIME);
                gboolean seekable = FALSE;

                if (gst_element_query(producer->m_pipeline, query)) {
                    gint64 start, end;
                    gst_query_parse_seeking(query, nullptr, &seekable, &start, &end);

                    double durationSec = (double)end / GST_SECOND;
                    LOG(info) << "Pipeline seekable: " << (seekable ? "YES" : "NO") << endl;
                    LOG(info) << "Duration from query: " << durationSec << " seconds" << endl;

                    // For HTTP sources (MinIO/S3), duration query is often unreliable
                    // MKV files store duration at the end, which HTTP sources can't easily read
                    // If duration is suspiciously small (< 1s), it's likely just the first cluster
                    if (durationSec < 1.0)
                    {
                        LOG(warning) << "Duration (" << durationSec << "s) suspiciously small for HTTP source - "
                                    << "likely incomplete metadata. Will attempt seek anyway." << endl;
                    }
                }
                gst_query_unref(query);

                // Perform seek (skip if starting from position 0 as pipeline is already there)
                gboolean seekResult = TRUE;
                
                if (seekData->startTime > 0) {  // Only seek if start time is meaningful (> 100ms)
                    // For MKV over HTTP, only seek to start position (let end-time enforcement handle stopping)
                    // Range seeks often fail with HTTP sources
                    LOG(info) << "Seeking to start position: " << seekData->startTime << "s" << endl;

                    // Try seeking on pipeline first (works for most cases)
                    seekResult = gst_element_seek(producer->m_pipeline,
                        1.0,                                    // playback rate
                        GST_FORMAT_TIME,
                        (GstSeekFlags)(GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_KEY_UNIT | GST_SEEK_FLAG_SNAP_BEFORE),
                        GST_SEEK_TYPE_SET, secondsToNs(seekData->startTime),
                        GST_SEEK_TYPE_NONE, GST_CLOCK_TIME_NONE  // Don't specify end - let app-level logic handle it
                    );
                    
                    LOG(info) << "Seek on pipeline result: " << (seekResult ? "SUCCESS" : "FAILED") << endl;
                    
                    // Fallback: try seeking on demuxer if pipeline seek failed (for some HTTP sources)
                    if (!seekResult)
                    {
                        LOG(warning) << "Pipeline seek failed, trying demuxer seek as fallback" << endl;
                        GstElement* seekTarget = producer->m_useMultiSegment ? producer->m_concat : producer->m_demux;
                        if (seekTarget)
                        {
                            seekResult = gst_element_seek(seekTarget,
                                1.0,
                                GST_FORMAT_TIME,
                                (GstSeekFlags)(GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_KEY_UNIT | GST_SEEK_FLAG_SNAP_BEFORE),
                                GST_SEEK_TYPE_SET, secondsToNs(seekData->startTime),
                                GST_SEEK_TYPE_NONE, GST_CLOCK_TIME_NONE
                            );
                            LOG(info) << "Seek on demuxer result: " << (seekResult ? "SUCCESS" : "FAILED") << endl;
                        }
                    }
                } else {
                    LOG(info) << "Start time is " << seekData->startTime 
                             << "s (near beginning), skipping seek and playing from start" << endl;
                }

                if (producer->m_syncMode && producer->getConsumerList().size() == 0) {
                    // In case of sync=true, Set pipeline playing state when atleast one consumer is ready
                    // This is to avoid initial video frame loss
                    LOG(info) << "No consumers registered sync=true, Waiting for consumers to register" << endl;
                    seekData->seekDone = true;
                    break;
                }

                if (seekResult) {
                    LOG(info) << "Seek successful (or skipped), transitioning to PLAYING state" << endl;
                    seekData->seekDone = true;
                    gst_element_set_state(producer->m_pipeline, GST_STATE_PLAYING);
                    producer->m_state = GST_STATE_PLAYING;
                } else {
                    // Seek failed - for HTTP sources with unreliable duration, this is expected
                    // Fall back to playing from the beginning and rely on app-level time filtering
                    LOG(warning) << "Seek failed - falling back to playing from start position" << endl;

                    seekData->seekDone = true;
                    gst_element_set_state(producer->m_pipeline, GST_STATE_PLAYING);
                    producer->m_state = GST_STATE_PLAYING;
                }
            }
            break;
        }

        case GST_MESSAGE_STATE_CHANGED: {
            // Only care about pipeline state changes (check again to avoid race)
            if (producer->m_pipeline && producer->m_seekData &&
                GST_MESSAGE_SRC(msg) == GST_OBJECT(producer->m_pipeline)) {
                GstState oldState, newState, pendingState;
                gst_message_parse_state_changed(msg, &oldState, &newState, &pendingState);

                producer->m_state = newState;

                if (newState == GST_STATE_PAUSED && !producer->m_seekData->pausedStateReached) {
                    LOG(info) << "Pipeline reached PAUSED state" << endl;
                    producer->m_seekData->pausedStateReached = true;
                }
            }
            break;
        }

        default:
            break;
    }

    return TRUE;
}

