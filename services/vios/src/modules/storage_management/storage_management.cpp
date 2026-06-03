/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "storage_management.h"
#include "logger.h"
#include "database.h"
#include "prometheus_client/prometheus_client.h"
#include "storage_management_utils.h"
#include "modules_apis.h"
#include "network_utils.h"
#include "vst_common.h"
#include "fs_utils.h"
#include "VideoGeneratorTaskManager.h"
#include "AsyncVideoStreamHandler.h"
#include <civetweb.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/time.h>
#include <ctime>
#include <chrono>
#include <thread>
#include <array>
#include <assert.h>
#include <fts.h>
#include <string.h>
#include <string_view>
#include <limits>
#include <iomanip>
#include <sstream>
#include <unordered_set>
#include <sys/stat.h>
#include <errno.h>

// OpenSSL includes for AWS Signature V4 authentication
#include <openssl/hmac.h>
#include <openssl/sha.h>

// Cloud Reader Library
#include "cloud_reader_factory.h"
#include "unified_cloud_storage_reader.h"

#include "config.h"
#include "utils.h"
#include "mm_utils.h"
#include "gst_utils.h"
#include <fstream>
#include <filesystem>

using namespace std;
using namespace nv_vms;


constexpr const char* STORAGE_MANAGEMENT_VERSION = "0.0.1";

#define CONVERT_KBPS_TO_GBPERDAY(v) ((v/1024.0) * 60.0 * 60.0 * 24)/(8 * 1024.0);
constexpr int DEFAULT_BITRATE = 5120;

constexpr const char* CONNECTED = "connected";
constexpr const char* DISCONNECTED = "disconnected";
constexpr const char* ONLINE = "online";

constexpr int AGING_POLICY_THREAD_COUNT = 1;
constexpr int AVG_VIDEO_SIZE_BYTES = 10485760;
constexpr double BINARY_CONVERSION_FACTOR = 1024.0;

uint64_t StorageManagement::m_cachedStorageCapacity = 0;
bool StorageManagement::m_isStorageCapacityInitialized = false;

extern "C" void* createStorageManagementObject()
{
    std::shared_ptr<DeviceManager> deviceManager = ModuleLoader::getInstance()->getDeviceManagerObject();
    return new StorageManagement(ModuleLoader::getInstance()->getDeviceType(), ModuleLoader::getInstance()->getDeviceId(), deviceManager);
}

extern "C" void deleteStorageManagementObject(StorageManagement* object)
{
    delete object;
}

StorageManagement::StorageManagement(const string deviceType, const string deviceId, std::shared_ptr<DeviceManager> deviceMngr)
: m_currentUsedStorage(0),
  m_sensorType(deviceType),
  m_deviceId(deviceId),
  m_deviceManager(deviceMngr),
  m_unifiedStorageManager(nullptr),
  m_unifiedStorageReader(nullptr)
{
    storageManagementApis();
    DeviceConfig config = GET_CONFIG();

    //get the initial record size from database
    initialiseUsedStorageSize();
    GET_DB_INSTANCE()->resetProtectedFlagsInDb();

    if (m_deviceManager->getDeviceType() != TYPE_STREAMER && config.enable_aging_policy)
    {
        const uint64_t frequency = config.storage_monitoring_frequency_secs;
        std::chrono::seconds seconds (frequency);
        m_storage = make_unique<Bosma::Scheduler>(AGING_POLICY_THREAD_COUNT);
        m_storage->interval(seconds, [=]() {
            StorageMonitorTask();
        });
        LOG(info) << "Aging policy enabled (enable_aging_policy=" << config.enable_aging_policy << ")" << endl;
    }
    else
    {
        LOG(info) << "Aging policy disabled"
                  << (m_deviceManager->getDeviceType() == TYPE_STREAMER ? " (streamer adaptor)" : " (enable_aging_policy=false)")
                  << endl;
    }

    // Initialize unified storage components
    if (!initUnifiedStorageReader())
    {
        LOG(error) << "Failed to initialize unified storage reader" << endl;
    }

    if (!initUnifiedStorageManager())
    {
        LOG(error) << "Failed to initialize unified storage manager" << endl;
    }

    std::chrono::seconds prometheus_interval (5);
    m_monitoring = make_unique<Bosma::Scheduler>(1);
    m_monitoring->interval(prometheus_interval, [=]() {
        sendCurrentUsedStorageSizeToPrometheus();
    });

    m_videoCleanupScheduler = std::make_unique<TempFileScheduler>(
        nv_vms::TempFilesDBColumns::FILE_TYPE_VIDEO,
        [](const std::string& taskId, const std::string& filePath) {
            StorageManagement::cleanupExpiredAsyncTask(taskId, filePath);
        });
    m_videoCleanupScheduler->initializeFromDatabase();

    m_imageCleanupScheduler = std::make_unique<TempFileScheduler>(
        nv_vms::TempFilesDBColumns::FILE_TYPE_IMAGE,
        [](const std::string& /*taskId*/, const std::string& filePath) {
            StorageManagement::cleanupExpiredFile(filePath);
        });
    m_imageCleanupScheduler->initializeFromDatabase();

    // Send events for existing file-based sensors
    notifyFileSensorEvents();

    // Start cloud storage scanning in background thread if cloud storage is enabled
    if (m_cloudStorageEnabled && config.enable_cloud_storage)
    {
        LOG(info) << "Starting cloud storage scanning thread at bootup" << endl;
        m_cloudScanThread = std::thread(&StorageManagement::scanAndImportCloudFilesBackground, this);
    }
    else
    {
        LOG(info) << "Cloud storage scanning thread is not started as cloud storage is not enabled" << endl;
    }

    LOG(info) << "Sensortype: " << m_sensorType << endl;
    LOG(info) << "DeviceId: " << m_deviceId << endl;
}

int StorageManagement::deleteFilesByTime(const string stream_id, const int64_t startTime, const int64_t endTime, uint32_t &spaceSaved)
{
    int ret = 0;
    VideoRecordDBColumns dbRow;
    auto dbHelper = GET_DB_INSTANCE();

    /* Collect every stream id whose backing recordings should be
     * removed. Always includes the requested id. When that id resolves
     * to a multi-stream FILE sensor (file sensors built via the
     * merge-path use the appended "<sensor>_<file>" id format for
     * sub-streams) we also pull each sub-stream's id so the merge-path
     * files are unlinked alongside the main stream's. Without this,
     * sensor delete only removed the main stream's file and left every
     * merge-path upload orphaned in streamer_videos/.
     *
     * The fan-out is gated on sensor->type == SENSOR_TYPE_FILE because
     * RTSP / ONVIF / Milestone cameras can also carry multiple
     * streams (primary/secondary, audio, etc.) and a stream-scoped
     * delete on those must not cascade into sibling streams.
     * std::unordered_set guards against duplicate ids returned by the
     * device manager for the rare case where a sensor reports the same
     * stream id twice.
     */
    std::vector<std::string> stream_ids = {stream_id};
    if (m_deviceManager)
    {
        shared_ptr<SensorInfo> sensor = m_deviceManager->getSensor(stream_id);
        if (sensor && sensor->type == std::string(SENSOR_TYPE_FILE))
        {
            std::unordered_set<std::string> seen{stream_id};
            for (const auto& sub : sensor->streams)
            {
                if (sub && !sub->id.empty() && seen.insert(sub->id).second)
                {
                    stream_ids.push_back(sub->id);
                }
            }
        }
    }

    /* read rows */
    std::vector<VideoRecordDBColumns> rows;
    for (const auto& sid : stream_ids)
    {
        auto sub_rows = dbHelper->readVideoRecordStreamIdBased(sid, startTime, endTime);
        rows.insert(rows.end(), sub_rows.begin(), sub_rows.end());
    }
    uint64_t deletedFilesSize = 0;
    vector <string> filePaths;
    const string root_camera_dir = GET_CONFIG().recorded_video_root;
    /* iterate, get file size and delete */
    for(uint32_t i = 0; i < rows.size(); i++)
    {
        string file_name = rows[i].filepath_value;
        string object_id = rows[i].object_id_value;
        bool isCloudFile = (rows[i].storage_location_value == StreamStorageTypeCloud);

        // Check if file is protected
        if (rows[i].file_protection_value == "1")
        {
            LOG(info) << "File = " << file_name << " is protected, skipping deletion" << endl;
            continue;
        }

        LOG(info) << "Removing file = " << file_name << " (storage: "
                  << (isCloudFile ? "cloud" : "local") << ")" << endl;

        // For local files, use file path; for cloud files, use object_id
        string pathToDelete = isCloudFile ? object_id : file_name;

        // Remove any symlink in temp_files/ that points at this source
        // recording, so the temp URL stops working in lockstep with the
        // source. MUST run before the source is unlinked, otherwise the
        // symlink would already look broken to the resolver.
        deleteTempLinksForGivenFile(rows[i]);

        // Use unified storage manager utility for deletion with detailed results
        nv_vms::DeleteResult deleteResult =  deleteFileWithStatus(pathToDelete, object_id, isCloudFile);
        if (deleteResult.success)
        {
            filePaths.push_back(file_name);
            // Use the actual deleted size from the result instead of pre-calculated size
            deletedFilesSize += deleteResult.deletedSize;
            LOG(verbose) << "Deleted File using unified storage: " << file_name
                        << " (Size: " << deleteResult.deletedSize << " bytes, Duration: "
                        << deleteResult.duration.count() << "ms)" << endl;
            updateStorageSize(deleteResult.deletedSize, false);

            // Clean up empty directories for local storage
            if (!GET_CONFIG().enable_cloud_storage)
            {
                string parent_folder = getDirPath(file_name);
                deleteEmptyDirectories(parent_folder, root_camera_dir);
            }
        }
        else
        {
            LOG(error) << "Failed to delete file using unified storage: " << file_name
                       << " - " << deleteResult.message << " (Error: " << deleteResult.errorCode << ")" << endl;
        }
    }
    spaceSaved = to_MB(deletedFilesSize);
    /* Remove database entries */
    if(dbHelper->deleteVideoRecordings(filePaths) != 0)
    {
        ret = -1;
    }
    else
    {
        // Process sensor details for all deleted files
        if (!filePaths.empty() && rows.size() > 0)
        {
            for(uint32_t j = 0; j < rows.size(); j++)
            {
                // Check if this row's file was actually deleted
                if (std::find(filePaths.begin(), filePaths.end(), rows[j].filepath_value) != filePaths.end())
                {
                    deleteSensorDetails(rows[j]);
                }
            }
        }
    }
    return ret;
}

uint64_t StorageManagement::getActualStorageCapacity(const std::string& path)
{
    if (!m_isStorageCapacityInitialized)
    {
        m_cachedStorageCapacity = getStorageCapacity(path);
        m_isStorageCapacityInitialized = true;
    }

    return m_cachedStorageCapacity;
}

int StorageManagement::getStreamRecordSize(const string stream_id, size_t& videoSize)
{
    int ret = -1;
    const string videoFilesPath = GET_CONFIG().recorded_video_root;
    const string cameraDirecotry = videoFilesPath + string("/") + stream_id;

    if(isDirExist(cameraDirecotry))
    {
        getDirSize(cameraDirecotry, videoSize);
        ret = 0;
    }
    else
    {
        LOG(error) << "Directory does not exists: " << cameraDirecotry << endl;
    }
    return ret;
}

int StorageManagement::getCurrentUsedStorageSize(unordered_map<string, string> &cameraIdStatusMap, double &gbPerDay, const std::vector<string>& streamList, const string requiredTimelines, Json::Value &response)
{
    int ret = 0;
    const string videoFilesPath = GET_CONFIG().recorded_video_root;
    double days = 0;

    if(!isDirExist(videoFilesPath))
    {
        LOG(error) << "Directory does not exists: " << videoFilesPath << endl;
        return -1;
    }

    std::vector<VideoRecordDBColumns> rows = GET_DB_INSTANCE()->readVideoRecord("", 0, std::numeric_limits<int64_t>::max(), streamList);
    std::map<std::string, Json::Value, std::less<>> recordSegments;
    std::map<std::string, std::vector<VideoRecordDBColumns>, std::less<>> streamRecords;

    for(uint32_t row_cnt = 0; row_cnt < rows.size(); row_cnt++)
    {
        std::string streamId = rows.at(row_cnt).stream_id_value;
        streamRecords[streamId].push_back(rows.at(row_cnt));
    }

    uint64_t totalUsedStorageInBytes = 0;
    uint64_t totalUsedStorageInMB = 0;
    std::map<std::string, uint64_t, std::less<>> streamSizes;
    Json::Value jsonObjTimeline;

    for (const auto& entry : streamRecords)
    {
        const std::string& streamId = entry.first;
        const std::vector<VideoRecordDBColumns>& videoRecords = entry.second;

        uint64_t totalStreamSizeInBytes = 0;
        uint64_t totalSegmentRecordSize = 0;
        jsonObjTimeline.clear();
        recordSegments.clear();

        // Calculate stream size
        for(uint32_t row_cnt = 0; row_cnt < videoRecords.size(); row_cnt++)
        {
            Json::Value streamEntry;

            if (requiredTimelines == "true")
            {
                totalSegmentRecordSize += videoRecords.at(row_cnt).filesize_value;
                if (row_cnt == 0)
                {
                    jsonObjTimeline["startTime"] = convertEpocToISO8601_2(int64_t(videoRecords.at(row_cnt).start_time_value) * 1000);
                }
                if ((row_cnt + 1)  < videoRecords.size())
                {
                    bool isOverlap = (videoRecords.at(row_cnt + 1).start_time_value) < (videoRecords.at(row_cnt).start_time_value + (videoRecords.at(row_cnt).duration_value));
                    if (isOverlap)
                    {
                        continue;
                    }
                    else
                    {
                        bool isSegmentGap = ((videoRecords.at(row_cnt + 1).start_time_value - (videoRecords.at(row_cnt).start_time_value + (videoRecords.at(row_cnt).duration_value)) > MAX_TOLERANCE_SECS * 1000));
                        if (isSegmentGap)
                        {
                            jsonObjTimeline["endTime"] = convertEpocToISO8601_2(int64_t(videoRecords.at(row_cnt).start_time_value + (videoRecords.at(row_cnt).duration_value)) * 1000);
                            jsonObjTimeline["sizeInMegabytes"] = to_MB(totalSegmentRecordSize);
                            totalStreamSizeInBytes += totalSegmentRecordSize;
                            totalSegmentRecordSize = 0;
                            recordSegments[streamId].append(jsonObjTimeline);
                            jsonObjTimeline = Json::nullValue;
                            jsonObjTimeline["startTime"] = convertEpocToISO8601_2(int64_t(videoRecords.at(row_cnt + 1).start_time_value) * 1000);
                        }
                    }
                }
                else
                {
                    jsonObjTimeline["endTime"] = convertEpocToISO8601_2(int64_t(videoRecords.at(row_cnt).start_time_value + (videoRecords.at(row_cnt).duration_value)) * 1000);
                    jsonObjTimeline["sizeInMegabytes"] = to_MB(totalSegmentRecordSize);
                    totalStreamSizeInBytes += totalSegmentRecordSize;
                    totalSegmentRecordSize = 0;
                    recordSegments[streamId].append(jsonObjTimeline);
                }
            }
            else
            {
                totalStreamSizeInBytes += videoRecords.at(row_cnt).filesize_value;
            }
        }

        if (requiredTimelines == "true")
        {
            response[streamId]["timelines"] = recordSegments[streamId];
        }

        // Store raw bytes for accurate totaling
        streamSizes[streamId] = totalStreamSizeInBytes;
        uint64_t totalStreamSizeInMB = to_MB(totalStreamSizeInBytes);
        totalUsedStorageInMB += totalStreamSizeInMB;
        totalUsedStorageInBytes += totalStreamSizeInBytes;

        // Convert to MB for response
        response[streamId]["sizeInMegabytes"] = totalStreamSizeInMB;

        // Set stream state
        std::unordered_map<std::string,string>::const_iterator itr = cameraIdStatusMap.find(streamId);
        if (itr == cameraIdStatusMap.end())
        {
            response[streamId]["state"] = DISCONNECTED;
        }
        else
        {
            response[streamId]["state"] = (itr->second == ONLINE) ? CONNECTED : DISCONNECTED;
        }
    }

    // Calculate and verify totals
    Json::Value totalVideoSize;
    totalVideoSize["sizeInMegabytes"] = totalUsedStorageInMB;

    // Verify total matches sum using raw bytes first
    uint64_t sumOfByteSizes = 0;
    for (const auto& pair : streamSizes)
    {
        sumOfByteSizes += pair.second;
    }

    if (sumOfByteSizes != totalUsedStorageInBytes)
    {
        LOG(warning) << "Storage size mismatch! Raw bytes total=" << totalUsedStorageInBytes
                  << " sum=" << sumOfByteSizes << endl;
    }

    // Verify MB conversion matches
    uint64_t sumOfMBSizes = 0;
    for (const auto& entry : streamRecords)
    {
        const std::string& streamId = entry.first;
        sumOfMBSizes += response[streamId]["sizeInMegabytes"].asUInt64();
    }

    if (totalVideoSize["sizeInMegabytes"].asUInt64() != sumOfMBSizes)
    {
        LOG(warning) << "MB conversion mismatch! Total MB=" << totalVideoSize["sizeInMegabytes"].asUInt64()
                  << " sum MB=" << sumOfMBSizes << endl;
    }

    // Calculate remaining storage space
    size_t availableSpaceMB = to_MB(getAvailableSpace(videoFilesPath));
    size_t configuredMaxStorageMB = GET_CONFIG().total_video_storage_size_MB;
    size_t totalCapacityMB = availableSpaceMB + totalUsedStorageInMB;


    uint32_t remainingSpace = 0;
    // if available space + used space is greater than or equal to configured limit, then remaining space is configured limit - used space
    if (totalCapacityMB >= configuredMaxStorageMB)
    {
        // If storage used is greater than or equal to configured limit, remaining space is 0
        if (totalUsedStorageInMB >= configuredMaxStorageMB)
        {
            remainingSpace = 0; // No space left
        }
        else
        {
            remainingSpace = configuredMaxStorageMB - totalUsedStorageInMB;
        }
        totalVideoSize["totalDiskCapacity"] = configuredMaxStorageMB;
    }
    else
    {
        remainingSpace = availableSpaceMB;
        totalVideoSize["totalDiskCapacity"] = to_MB(getStorageCapacity(videoFilesPath));
    }

    days = (remainingSpace/BINARY_CONVERSION_FACTOR)/gbPerDay;
    totalVideoSize["remainingStorageDays"] = days;
    totalVideoSize["totalAvailableStorageSize"] = remainingSpace;
    response["total"] = totalVideoSize;

    return ret;
}

void StorageManagement::StorageMonitorTask()
{
    // Periodic aging policy trigger.
    //
    // Goal: ensure there is enough room for continuous recording by deleting the oldest recordings.
    // This function computes:
    // - A configured "storage capacity" (bytes) = min(user configured cap, actual filesystem cap)
    // - A configured threshold (bytes) = storage capacity * threshold%
    // - Current recordings size (bytes) from DB bookkeeping (m_currentUsedStorage)
    // - Current filesystem available space (bytes) from std::filesystem::space()
    //
    // It triggers deletion in two scenarios:
    // 1) Low disk space: if available bytes drop below a target free-space margin
    // 2) Record-size limit: if total recorded bytes exceed an effective threshold
    DeviceConfig config = GET_CONFIG();
    if (config.recorded_video_root.empty())
    {
        LOG(error) << "Invalid video root path" << endl;
        return;
    }

    // Validate threshold percentage.
    // Interpreted as "recordings may consume up to threshold% of configured storage capacity".
    double storageThresholdPercent = config.storage_threshold_percentage;
    if (storageThresholdPercent <= 0 || storageThresholdPercent >= 100)
    {
        LOG(error) << "Invalid threshold percentage: " << storageThresholdPercent << ", So setting default storage threshold" << endl;
        storageThresholdPercent = 95;
    }

    // Calculate capacities in bytes:
    // - actualStorageCapacity: filesystem total capacity (cached; can be 0 on error)
    // - actualUsedStorage: filesystem used bytes (capacity - available)
    // - userDiskCapacity: configured limit in MB converted to bytes
    const uint64_t actualStorageCapacity = getActualStorageCapacity(config.recorded_video_root);
    const uint64_t actualUsedStorage = getUsedSpace(config.recorded_video_root);
    const uint64_t userDiskCapacity = to_bytes(config.total_video_storage_size_MB);

    // Compute filesystem free/available space (bytes). If capacity/used are inconsistent, fall back
    // to a direct filesystem query to avoid unsigned underflow.
    const uint64_t availableDiskSpace = (actualUsedStorage >= actualStorageCapacity)
                                            ? static_cast<uint64_t>(getAvailableSpace(config.recorded_video_root))
                                            : (actualStorageCapacity - actualUsedStorage);

    // storageCapacity is the effective max we want recordings to consider.
    // If user has configured a smaller cap than disk size, we respect that.
    uint64_t storageCapacity = std::min(userDiskCapacity, actualStorageCapacity);
    if (storageCapacity == 0)
    {
        LOG(error) << "Invalid storage capacity, using available disk space" << endl;
        storageCapacity = availableDiskSpace;
    }

    // Maximum allowed recordings size (bytes) based on configuration threshold%.
    const uint64_t configuredStorageThresholdSize = (storageCapacity * storageThresholdPercent) / 100;

    // Current recordings size derived from DB bookkeeping.
    // Note: initialiseUsedStorageSize() refreshes m_currentUsedStorage (bytes).
    // getCurrentUsedStorageSize() returns MB, hence conversion back to bytes for comparisons here.
    initialiseUsedStorageSize();
    const uint64_t currentRecordSize = to_bytes(getCurrentUsedStorageSize());

    LOG(verbose) << "Storage stats: "
              << "vst_record_size=" << currentRecordSize
              << " actual_used_storage=" << actualUsedStorage
              << " user_capacity=" << userDiskCapacity
              << " actual_capacity=" << actualStorageCapacity
              << " configure_threshold=" << configuredStorageThresholdSize
              << " available_disk_space=" << availableDiskSpace << endl;

    uint64_t bytesToDelete = 0;

    // Effective threshold used by record-size based aging.
    // This takes the minimum of:
    // - configuredStorageThresholdSize: recordings size limit derived from config
    // - availableDiskSpace: current filesystem available space
    //
    // In practice, this makes the record-size trigger more aggressive when the disk is already tight.
    const uint64_t effectiveThresholdSize = std::min(configuredStorageThresholdSize, availableDiskSpace);

    // Target free-space margin (bytes) to keep on disk.
    // Example: threshold=95% => targetFreeSpace is 5% of configured storageCapacity.
    const uint64_t targetFreeSpace = storageCapacity - configuredStorageThresholdSize;

    LOG(verbose) << "Effective threshold: " << effectiveThresholdSize
                 << " (configured=" << configuredStorageThresholdSize
                 << ", available=" << availableDiskSpace << ")" << endl;

    // Trigger aging policy if filesystem free space is below the target margin.
    // Deletion request is bounded to currentRecordSize as a safety guard.
    //
    // NOTE: The following `if / else if` is intentional:
    // - Low disk free-space is given priority (more urgent / prevents ENOSPC for new writes)
    // - Otherwise we enforce the configured record-size threshold.
    // If you ever want both policies to contribute simultaneously, avoid running both branches;
    // instead compute both required deletion sizes and take the maximum (to prevent over-deletion).
    if (availableDiskSpace < targetFreeSpace)
    {
        const uint64_t requiredFreeSpace = targetFreeSpace - availableDiskSpace;
        bytesToDelete = std::min(currentRecordSize, requiredFreeSpace);
        LOG(verbose) << "Low disk space: " << bytesToDelete << " bytes to delete" << endl;
    }
    // Otherwise, trigger aging policy if recorded data exceeds the effective threshold.
    else if (currentRecordSize >= effectiveThresholdSize)
    {
        bytesToDelete = currentRecordSize - effectiveThresholdSize;
        LOG(verbose) << "Record size exceeded threshold: " << bytesToDelete << " bytes to delete" << endl;
    }
    else
    {
        LOG(verbose) << "Aging policy: nothing to do" << endl;
        return;
    }

    // Execute deletion of oldest media files until requested bytes are freed.
    if (bytesToDelete > 0 && deleteOldMediaFiles(bytesToDelete) != 0)
    {
        LOG(error) << "Aging policy: failed to delete " << bytesToDelete << " bytes" << endl;
    }
}

int StorageManagement::deleteSensorDetails(VideoRecordDBColumns& row)
{
    if (!m_deviceManager)
    {
        LOG(error) << "Device manager not initialized during aging policy" << endl;
        return -1;
    }

    // Validate input parameters
    if (row.stream_id_value.empty())
    {
        LOG(warning) << "Stream ID is empty, skipping sensor details deletion" << endl;
        return 0;
    }

    if (row.sensor_id_value.empty())
    {
        LOG(warning) << "Sensor ID is empty, skipping sensor details deletion" << endl;
        return 0;
    }

    shared_ptr<SensorInfo> sensorInfo = m_deviceManager->getSensorInfo(row.sensor_id_value, true);
    if (!sensorInfo || sensorInfo->type != SENSOR_TYPE_FILE)
    {
        return 0; // Early return if not a file sensor
    }

    auto dbHelper = GET_DB_INSTANCE();
    const string& streamId = row.stream_id_value;
    const string& sensorId = row.sensor_id_value;

    // Delete from database first
    if (dbHelper->deleteRowStream(streamId) != 0)
    {
        LOG(error) << "Failed to delete stream details for stream id: " << streamId << endl;
        return -1;
    }

    // Check if this is the last stream for the sensor
    if (sensorInfo->streams.size() == 1)
    {
        // Last stream - send sensor-level offline event
        vst_common::notifySensorStatusEvent(SensorStatusOffline, sensorInfo);

        // Delete entire sensor if it's the last stream
        m_deviceManager->deleteSensor(sensorId);
        dbHelper->deleteSensorDetails(sensorId);

        /* Removed sensor from replay service */
        vst_replaystream::removeSensor(sensorId);
        vst_sensor::deleteSensor(sensorId);
        LOG(info) << "Deleted sensor details for sensor id: " << sensorId << endl;
    }
    else
    {
        // Multiple streams - get the stream and send stream-level offline event
        shared_ptr<StreamInfo> stream = sensorInfo->getStream(streamId);
        if (stream)
        {
            vst_common::notifyStreamStatusEvent(SensorStatusOffline, stream);
        }

        // Remove stream from device manager
        dbHelper->deleteRowStream(streamId);
        m_deviceManager->removeStream(streamId, sensorId);

        vst_replaystream::removeStream(streamId);
        vst_sensor::deleteStream(streamId);
        LOG(info) << "Deleted stream details for stream id: " << streamId << " and sensor id: " << sensorId << endl;
    }

    return 0;
}

int StorageManagement::deleteOldMediaFiles(uint64_t &bytesToDelete)
{
    LOG(verbose) << "Aging policy: task started" << endl;
    int ret = 0;
    VideoRecordDBColumns dbRow;
    auto dbHelper = GET_DB_INSTANCE();
    uint64_t deletedFilesSize = 0;
    uint32_t batchSize = (bytesToDelete/AVG_VIDEO_SIZE_BYTES + 1) * 1.2;
    // Exclude cloud-scanned files from batch (only get files eligible for aging)
    std::vector<VideoRecordDBColumns> rows = dbHelper->readRecordsInBatch(batchSize, true);
    vector <string> filePaths;
    uint32_t attempts = 0;
    bool shouldStop = false;

    // Check if unified storage manager is available
    if (!m_unifiedStorageManager)
    {
        LOG(error) << "Unified storage manager not initialized for aging policy" << endl;
        return -1;
    }

    /* iterate, get file size and delete using unified storage */
    do
    {
        attempts++;
        filePaths.clear(); // Clear filePaths for each batch

        for(uint32_t i = 0; i < rows.size() && !shouldStop; i++)
        {
            string file_name = rows[i].filepath_value;
            string object_id = rows[i].object_id_value;
            bool isCloudFile = (rows[i].storage_location_value == StreamStorageTypeCloud);
            string recordConfig = rows[i].record_config_value;
            const string root_camera_dir = GET_CONFIG().recorded_video_root;
            uint64_t fileSizeInBytes = rows[i].filesize_value;

            // Skip cloud-scanned/imported files from aging policy (record_config = RECORD_CONFIG_CLOUD_SCANNED)
            // Files recorded by VST to cloud storage will still be aged (record_config = "schedule", "user", "alwaysOn", etc.)
            if (recordConfig == RECORD_CONFIG_CLOUD_SCANNED)
            {
                LOG(info) << "Skipping cloud-scanned file from aging policy: " << file_name << " (object_id: " << object_id << ")" << endl;
                continue;
            }

            // Check if we've already deleted enough bytes
            if (deletedFilesSize >= bytesToDelete)
            {
                LOG(verbose) << "Aging Policy: done - deleted " << deletedFilesSize << " bytes, needed " << bytesToDelete << " bytes" << endl;
                shouldStop = true;
                break;
            }

            // Use unified storage manager to delete file (works for both local and cloud)
            bool deleteSuccess = false;

            try
            {
                string parent_folder = getDirPath(file_name);
                // For local files, use file path; for cloud files, use object_id
                string pathToDelete = isCloudFile ? object_id : file_name;

                // Remove symlinks to this source BEFORE the source itself
                // is unlinked, so we can verify the link target.
                deleteTempLinksForGivenFile(rows[i]);

                nv_vms::DeleteResult result =  deleteFileWithStatus(pathToDelete, object_id, isCloudFile);
                deleteSuccess = result.success;

                if (deleteSuccess)
                {
                    filePaths.push_back(file_name);
                    deletedFilesSize += result.deletedSize;
                    updateStorageSize(result.deletedSize, false);
                    deleteEmptyDirectories(parent_folder, root_camera_dir);
                    LOG(verbose) << "Deleted File = " << i << " " << file_name << " size: " << result.deletedSize << " bytes" << endl;
                }
                else
                {
                    LOG(warning) << "Failed to delete file via unified storage: " << file_name
                                << " - " << result.message << endl;
                    // Still add to cleanup list as the file might not exist
                    filePaths.push_back(file_name);
                    deletedFilesSize += fileSizeInBytes;
                    updateStorageSize(fileSizeInBytes, false);
                    LOG(info) << "File marked for cleanup (may not exist): " << file_name << " size: " << fileSizeInBytes << " bytes" << endl;
                }
            }
            catch (const std::exception& e)
            {
                LOG(error) << "Exception during unified storage deletion: " << e.what() << endl;
                ret = -1;
                // Still add to cleanup list
                filePaths.push_back(file_name);
                deletedFilesSize += fileSizeInBytes;
                updateStorageSize(fileSizeInBytes, false);
            }
        }

        /* Remove database entries for all processed files */
        if(!filePaths.empty() && dbHelper->deleteVideoRecordings(filePaths))
        {
            ret = -1;
            LOG(error) << "Can't delete database records" << endl;
        }
        else
        {
            // Process sensor details for all deleted files
            if (!filePaths.empty() && rows.size() > 0)
            {
                for(uint32_t j = 0; j < rows.size(); j++)
                {
                    // Check if this row's file was actually deleted
                    if (std::find(filePaths.begin(), filePaths.end(), rows[j].filepath_value) != filePaths.end())
                    {
                        deleteSensorDetails(rows[j]);
                    }
                }
            }
        }

        // Only fetch more records if we haven't reached the target and haven't stopped
        if(deletedFilesSize < bytesToDelete && !shouldStop)
        {
            uint32_t newBatchSize = ((bytesToDelete - deletedFilesSize)/AVG_VIDEO_SIZE_BYTES + 1) * 1.2;
            LOG(verbose) << "new batch size: " << newBatchSize << endl;
            // Exclude cloud-scanned files from batch (only get files eligible for aging)
            rows = dbHelper->readRecordsInBatch(newBatchSize, true);
        }
        /* Do two attempts */
    } while ((deletedFilesSize < bytesToDelete) && attempts < 2 && !shouldStop);

    // Report aging bytes reclaimed to Prometheus
    if (ret == 0 && deletedFilesSize > 0)
    {
        LOG(verbose) << "Aging policy reclaimed " << deletedFilesSize << " bytes, reporting to Prometheus" << endl;
        GET_PROMETHEUS()->updateAgingBytesReclaimed(deletedFilesSize);
    }

    LOG(verbose) << "Aging policy: task completed using unified storage. Total deleted: " << deletedFilesSize << " bytes" << endl;
    return ret;
}

void StorageManagement::addFileInProtectList(string &filePath, bool remove)
{
    auto dbHelper = GET_DB_INSTANCE();
    LOG(info) << "Added file into the in protect list filePath:" << filePath << " removeOrAdd:" << remove << endl;

    // Check if this is a cloud file by looking in the database
    bool isCloudFile = false;
    std::vector<VideoRecordDBColumns> allRecords = dbHelper->getAllVideoRecordFilePaths();
    for (const auto& record : allRecords)
    {
        // Check if filepath or object_id matches (for cloud files)
        if ((record.filepath_value == filePath || record.object_id_value == filePath) &&
            ((record.storage_location_value == StreamStorageTypeCloud) || (record.record_config_value == RECORD_CONFIG_CLOUD_SCANNED)))
        {
            isCloudFile = true;
            // Use the exact filepath from database for update
            filePath = record.filepath_value;
            LOG(info) << "Detected cloud file, using database filepath: " << filePath << endl;
            break;
        }
    }

    // Only normalize local file paths, not cloud object keys
    if (!isCloudFile)
    {
        /* Extract relative path portion from the full path for database storage. this is for VSS passthrough use case */
        filePath = normalizeRelativePath(filePath, GET_CONFIG().nv_streamer_directory_path);
    }

    int result;
    if(remove)
    {
        result = dbHelper->updateFileProtectionInDb(true, filePath);
    }
    else
    {
        result = dbHelper->updateFileProtectionInDb(false, filePath);
    }

    if (result == 0)
    {
        LOG(info) << "Successfully updated file protection in database for: " << filePath << endl;
    }
    else
    {
        LOG(error) << "Failed to update file protection in database for: " << filePath << endl;
    }
}

void StorageManagement::deleteTempLinksForGivenFile(const VideoRecordDBColumns& row)
{
    // Remove only those temp_files entries that are actual symlinks
    // pointing at the source file about to be deleted. Independently
    // muxed/transcoded clips that happen to share the same time window
    // are intentionally LEFT in place — their data is self-contained.
    //
    // The full-file fast path (generateFullFileUrl) always creates a
    // symlink, so we only need to verify the link target equals sourcePath.
    const int64_t startMs = static_cast<int64_t>(row.start_time_value);
    const int64_t endMs   = startMs + static_cast<int64_t>(row.duration_value);
    const std::string& streamId   = row.stream_id_value;
    const std::string& sourcePath = row.filepath_value;

    if (streamId.empty() || sourcePath.empty() || startMs <= 0 || endMs <= startMs)
    {
        return;
    }

    auto dbHelper = GET_DB_INSTANCE();
    if (!dbHelper)
    {
        return;
    }

    // Derive the source's container from its extension; the full-file fast
    // path stores the same value in TEMP_VIDEO_FILES.container_format, so
    // restricting the lookup to that container avoids accidental matches
    // against unrelated regular-flow rows.
    std::string sourceContainer;
    {
        const auto dotPos = sourcePath.find_last_of('.');
        if (dotPos != std::string::npos && dotPos + 1 < sourcePath.size())
        {
            sourceContainer = sourcePath.substr(dotPos + 1);
        }
    }

    auto candidate = dbHelper->findTempFileByStreamAndTime(
        m_deviceId, streamId, startMs, endMs,
        nv_vms::TempFilesDBColumns::FILE_TYPE_VIDEO, sourceContainer);

    const std::string& candidatePath = candidate.file_path_value;
    if (candidatePath.empty())
    {
        return; // common case: no temp link for this recording
    }

    // Verify identity: candidate must be a symlink whose target equals sourcePath.
    std::error_code ec;
    const auto symStatus = std::filesystem::symlink_status(candidatePath, ec);
    if (ec || !std::filesystem::is_symlink(symStatus))
    {
        LOG(verbose) << "[FULL_FILE] temp candidate " << candidatePath
                     << " is not a symlink — leaving it intact" << endl;
        return;
    }

    const auto target = std::filesystem::read_symlink(candidatePath, ec);
    if (ec || target.string() != sourcePath)
    {
        LOG(verbose) << "[FULL_FILE] temp candidate " << candidatePath
                     << " does not point at source " << sourcePath
                     << " — leaving it intact" << endl;
        return;
    }

    // Safe to remove: it really is a symlink to the source.
    std::filesystem::remove(candidatePath, ec);
    if (ec)
    {
        LOG(warning) << "[FULL_FILE] failed to unlink symlink "
                     << candidatePath << ": " << ec.message() << endl;
    }

    const int rc = dbHelper->deleteTempFileRecord(candidatePath);
    if (rc != 0)
    {
        LOG(warning) << "[FULL_FILE] failed to delete temp DB row for "
                     << candidatePath << endl;
    }
    else
    {
        LOG(info) << "[FULL_FILE] removed symlink " << candidatePath
                  << " -> " << sourcePath
                  << " (source about to be deleted)" << endl;
    }
    // Note: we don't cancel the in-process expiry timer for this taskId.
    // When it eventually fires the cleanup callback finds the file & DB
    // row already gone and exits as a harmless no-op.
}

void StorageManagement::addFilesInProtectList(std::vector<string>& filePaths, bool protect)
{
    if (filePaths.empty())
    {
        return;
    }

    auto dbHelper = GET_DB_INSTANCE();
    LOG(info) << "Batch updating file protection for " << filePaths.size() << " files, protect:" << protect << endl;

    // Get all records once for cloud file detection
    std::vector<VideoRecordDBColumns> allRecords = dbHelper->getAllVideoRecordFilePaths();

    // Create a map for quick lookup of cloud files
    std::unordered_map<string, string> cloudFileMap;
    for (const auto& record : allRecords)
    {
        if ((record.storage_location_value == StreamStorageTypeCloud) ||
            (record.record_config_value == RECORD_CONFIG_CLOUD_SCANNED))
        {
            // Map both filepath and object_id to the actual filepath
            cloudFileMap[record.filepath_value] = record.filepath_value;
            if (!record.object_id_value.empty())
            {
                cloudFileMap[record.object_id_value] = record.filepath_value;
            }
        }
    }

    // Process file paths - normalize local paths, resolve cloud paths
    std::vector<string> normalizedPaths;
    normalizedPaths.reserve(filePaths.size());

    for (auto& filePath : filePaths)
    {
        auto cloudIt = cloudFileMap.find(filePath);
        if (cloudIt != cloudFileMap.end())
        {
            // Cloud file - use the database filepath
            normalizedPaths.push_back(cloudIt->second);
            LOG(verbose) << "Cloud file detected: " << filePath << " -> " << cloudIt->second << endl;
        }
        else
        {
            // Local file - normalize the path
            string normalizedPath = normalizeRelativePath(filePath, GET_CONFIG().nv_streamer_directory_path);
            normalizedPaths.push_back(normalizedPath);
        }
    }

    // Update all files in a single database query
    int result = dbHelper->updateFilesProtectionInDb(protect, normalizedPaths);

    if (result == 0)
    {
        LOG(info) << "Successfully batch updated file protection for " << normalizedPaths.size() << " files" << endl;
    }
    else
    {
        LOG(error) << "Failed to batch update file protection for " << normalizedPaths.size() << " files" << endl;
    }
}

bool StorageManagement::isFileProtected(const string& file_path)
{
    unordered_set<string> fileProtectList = getProtectedFilesList();
    if (fileProtectList.find(file_path) == fileProtectList.end())
    {
        return false;
    }

    return true;
}

void StorageManagement::updateStorageSize(uint64_t frameSize, bool flag)
{
    std::lock_guard<std::mutex> storageLock(m_storageMutex);
    if(flag)
    {
        m_currentUsedStorage += frameSize;
    }
    else
    {
        if(m_currentUsedStorage <= frameSize)
        {
            //handle possible underflow for unsigned int
            m_currentUsedStorage = 0;
        }
        else
        {
            m_currentUsedStorage -= frameSize;
        }
    }
}

void StorageManagement::initialiseUsedStorageSize()
{
    std::lock_guard<std::mutex> storageLock(m_storageMutex);
    //Get the total record size from database and initialise m_currentUsedStorage
    m_currentUsedStorage = GET_DB_INSTANCE()->getTotalCurrentRecordSize();
    LOG(verbose) << "Initial record size: " << m_currentUsedStorage << endl;
}

unordered_set<string> StorageManagement::getProtectedFilesList()
{
    unordered_set<string> protectedFiles;

    auto dbHelper = GET_DB_INSTANCE();
    vector<VideoRecordDBColumns> fileRecords = dbHelper->getProtectedFilesFromDB();

    for(auto& record : fileRecords)
    {
        LOG(verbose) << "Found protected file: " << record.filepath_value << endl;

        // Check if this is a cloud file - if so, don't normalize the path
        bool isCloudFile = (record.storage_location_value == StreamStorageTypeCloud) ||
                          (record.record_config_value == RECORD_CONFIG_CLOUD_SCANNED);

        if (!isCloudFile)
        {
            record.filepath_value = normalizeRelativePath(record.filepath_value, GET_CONFIG().nv_streamer_directory_path);
        }

        protectedFiles.insert(record.filepath_value);
    }

    LOG(info) << "Retrieved " << protectedFiles.size() << " protected files" << endl;
    return protectedFiles;
}

uint64_t StorageManagement::deleteMediaFile(const string file_name)
{
    vector <string> filePaths;
    auto dbHelper = GET_DB_INSTANCE();

    // Get object ID and storage location from database before deleting the record
    string object_id = "";
    bool isCloudFile = false;
    VideoRecordDBColumns matchedRow;
    bool foundMatch = false;

    // Get the object name and storage location from database for this file
    std::vector<VideoRecordDBColumns> rows = dbHelper->getAllVideoRecordFilePaths();
    for (const auto& row : rows)
    {
        if (row.filepath_value == file_name)
        {
            object_id = row.object_id_value;
            isCloudFile = (row.storage_location_value == StreamStorageTypeCloud);
            matchedRow = row;
            foundMatch = true;
            break;
        }
    }

    // For local files, use file path; for cloud files, use object_id
    string pathToDelete = isCloudFile ? object_id : file_name;

    // Remove symlinks to this source BEFORE the source itself is
    // unlinked, so the readlink-target identity check still works.
    if (foundMatch)
    {
        deleteTempLinksForGivenFile(matchedRow);
    }

    // Use unified storage manager utility for deletion with detailed results
    nv_vms::DeleteResult deleteResult = deleteFileWithStatus(pathToDelete, object_id, isCloudFile);
    if (deleteResult.success)
    {
        LOG(verbose) << "Successfully deleted file using unified storage: " << file_name
                  << " (Size: " << deleteResult.deletedSize << " bytes, Duration: "
                  << deleteResult.duration.count() << "ms)" << endl;
    }
    else
    {
        LOG(error) << "Failed to delete file using unified storage: " << file_name
                   << " - " << deleteResult.message << " (Error: " << deleteResult.errorCode << ")" << endl;
    }

    /* Remove database entry */
    filePaths.push_back(file_name);
    if(dbHelper->deleteVideoRecordings(filePaths))
    {
        LOG(error) << "Can't delete database record for File = " << file_name << endl;
    }
    else
    {
        LOG(info) << "DB entry deleted for file = " << file_name << endl;
    }
    return deleteResult.deletedSize;
}

void StorageManagement::sendCurrentUsedStorageSizeToPrometheus()
{
    std::lock_guard<std::mutex> storageLock(m_storageMutex);
    GET_PROMETHEUS()->updateStorageSize(to_MB(m_currentUsedStorage));
}

uint64_t StorageManagement::getCurrentUsedStorageSize()
{
    std::lock_guard<std::mutex> storageLock(m_storageMutex);
    return to_MB(m_currentUsedStorage);
}

void StorageManagement::checkandCreateFreeSpaceInStorage(size_t newDataSize)
{
    std::lock_guard<std::mutex> storageLock(m_muxerMutex);
    DeviceConfig config = GET_CONFIG();
    size_t actualDiskCapacity = getStorageCapacity(config.recorded_video_root);
    actualDiskCapacity = to_MB(actualDiskCapacity);
    size_t userDiskCapacity = config.total_video_storage_size_MB;
    size_t diskCapacity = std::min(userDiskCapacity, actualDiskCapacity);
    const double storageThresholdPercent = config.storage_threshold_percentage;
    uint64_t storageThresholdSize = diskCapacity * (storageThresholdPercent/100);
    //get max allowed record size in bytes
    storageThresholdSize = to_bytes(storageThresholdSize);
    uint64_t currentRecordSize = m_currentUsedStorage;
    LOG(verbose2) << "Current record size: " << m_currentUsedStorage << " userDiskCapacity:" << userDiskCapacity << " actualDiskCapacity:" << actualDiskCapacity << " storageThresholdPercent:"<< storageThresholdPercent << " storageThresholdSize:" << storageThresholdSize << endl;
    LOG(verbose2) << "New data size: " << newDataSize << endl;
    if (currentRecordSize + newDataSize > storageThresholdSize)
    {
        LOG(verbose) << "diskCapacity:" << diskCapacity << "  storageThresholdSize:" << storageThresholdSize << endl;
        uint64_t bytesToDelete = currentRecordSize + newDataSize - storageThresholdSize;
        LOG(info) << "Aging policy: deleting more than " << bytesToDelete << " bytes of records" << endl;
        if (deleteOldMediaFiles(bytesToDelete) != 0)
        {
            LOG(error) << "Aging policy: failed to delete data" << endl;
        }
    }
    else
    {
        LOG(verbose) << "Aging policy: nothing todo" << endl;
    }
}

bool StorageManagement::checkStorageCapacity(size_t requiredCapacity)
{
    LOG(info) << "Checking capacity: " << requiredCapacity << endl;
    DeviceConfig config = GET_CONFIG();

    size_t userDiskCapacity = config.total_video_storage_size_MB;
    size_t actualDiskCapacity = getStorageCapacity(config.recorded_video_root);
    actualDiskCapacity = to_MB(actualDiskCapacity);
    size_t diskCapacity = std::min(userDiskCapacity, actualDiskCapacity);

    const double storageThresholdPercent = config.storage_threshold_percentage;
    uint64_t storageThresholdSize = diskCapacity * (storageThresholdPercent/100);
    //get max allowed record size in bytes
    storageThresholdSize = to_bytes(storageThresholdSize);
    return requiredCapacity <= storageThresholdSize;
}

VmsErrorCode StorageManagement::checkStorageCapacity(const Json::Value & req_info, const Json::Value &in, Json::Value &response)
{
    VmsErrorCode ret = VmsErrorCode::NoError;
    string streamId;
    response = Json::nullValue;

    const string request_api = req_info.get("url", EMPTY_STRING).asString();
    const string request_method = req_info.get("method", UNKNOWN_STRING).asString();
    const string query_string = req_info.get("query", EMPTY_STRING).asString();
    if (request_api.empty() || request_method == UNKNOWN_STRING)
    {
        LOG(error) << "Malformed HTTP request" << endl;
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return VmsErrorCode::InvalidParameterError;
    }

    if (!(iequals(request_method, "post")))
    {
        LOG(error) << "Request Method is not supported" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
        return VmsErrorCode::VMSNotSupportedError;
    }

    vector<string> fileList;

    size_t size = in.get("bytesToCheck", 0).asUInt64();
    LOG(verbose) <<"Received bytesToCheck: " << size << std::endl;

    bool status = checkStorageCapacity(size);
    response["status"] = status;

    return ret;
}

VmsErrorCode StorageManagement::getStorageConfiguration(const Json::Value & req_info, Json::Value &response)
{
    VmsErrorCode ret = VmsErrorCode::NoError;
    const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
    DeviceConfig config = GET_CONFIG();
    if (iequals(requestMethod, "get"))
    {
        response["maxVideoDownloadSizeMB"] =  (uint32_t)config.max_video_download_size_MB;
        response["useHttpDigestAuthentication"] = config.use_http_digest_authentication;
        response["useHttps"] = config.use_https;
        response["vstDataPath"] = config.vst_data_path;
        response["webserviceAccessControlList"] = config.webservice_access_control_list;
        response["enableUserCleanup"] = config.enable_user_cleanup;
        response["multiUserExtraOptions"] = vectorToString(config.multi_user_extra_options);
        response["useMultiUser"] = config.use_multi_user;
        response["vstIp"] = g_hostIp;
        response["recordedVideoDirRoot"] = GET_CONFIG().recorded_video_root;
        response["enableAgingPolicy"] = GET_CONFIG().enable_aging_policy;
        response["totalVideoStorageSizeMB"] = (uint32_t)GET_CONFIG().total_video_storage_size_MB;
        response["storageThresholdPercentage"] = GET_CONFIG().storage_threshold_percentage;
        response["storageMonitoringFrequencySecs"] = GET_CONFIG().storage_monitoring_frequency_secs;
        response["enableCloudStorage"] = GET_CONFIG().enable_cloud_storage;
        response["cloudStorageType"] = GET_CONFIG().cloud_storage_type;
        response["cloudStorageEndpoint"] = GET_CONFIG().cloud_storage_endpoint;
        response["cloudStorageBucket"] = GET_CONFIG().cloud_storage_bucket;
        response["cloudStorageRegion"] = GET_CONFIG().cloud_storage_region;
        response["cloudStorageUseSsl"] = GET_CONFIG().cloud_storage_use_ssl;
    }
    else
    {
        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
        ret = VmsErrorCode::MethodNotAllowedError;
    }
    return ret;
}

VmsErrorCode StorageManagement::getVersion(string& version)
{
    version = STORAGE_MANAGEMENT_VERSION;
    return VmsErrorCode::NoError;
}

VmsErrorCode StorageManagement::getFileMetadata(const Json::Value& req_info, Json::Value &response)
{
    bool found = false;
    string sensorId, fileId;

    const string query_string = req_info.get("query", EMPTY_STRING).asString();
    if (query_string.empty() == false)
    {
        CivetServer::getParam(query_string, "sensorId", sensorId);
        CivetServer::getParam(query_string, "id", fileId);
        if (sensorId.empty() && fileId.empty())
        {
            LOG(error) << "sensorId and fileId are not provided, atleast one of them is required" << endl;
            SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
            return VmsErrorCode::InvalidParameterError;
        }
        if (!fileId.empty())
        {
            vector<VideoRecordDBColumns> fileRecords = GET_DB_INSTANCE()->getVideoRecordFilePathsIdBased(fileId);
            if (fileRecords.empty())
            {
                LOG(error) << "Invalid file Id" << endl;
                SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
                return VmsErrorCode::InvalidParameterError;
            }

            const VideoRecordDBColumns& record = fileRecords[0];
            string file = record.filepath_value;

            // Check if this is a cloud file
            if (record.storage_location_value == StreamStorageTypeCloud)
            {
                LOG(info) << "File is stored in cloud storage: " << file << endl;

                // Check if cloud storage is enabled
                if (!GET_CONFIG().enable_cloud_storage)
                {
                    LOG(error) << "Cloud storage is not enabled" << endl;
                    SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Cloud storage is not enabled");
                    return VmsErrorCode::MethodNotAllowedError;
                }

                // Check if unified storage reader is available
                if (!m_unifiedStorageReader)
                {
                    LOG(error) << "Unified storage reader not available for cloud file" << endl;
                    SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Cloud storage reader not available");
                    return VmsErrorCode::VMSInternalError;
                }

                // Get presigned URL for the cloud file
                std::string presignedUrl;
                nv_vms::FileResult urlResult = m_unifiedStorageReader->getPresignedUrl(file, presignedUrl);

                if (!urlResult.success || presignedUrl.empty())
                {
                    LOG(error) << "Failed to generate presigned URL for cloud file: " << file
                              << ", Error: " << urlResult.message << endl;
                    SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response,
                                  "Failed to generate presigned URL for cloud file");
                    return VmsErrorCode::VMSInternalError;
                }

                LOG(info) << "Generated presigned URL for cloud file, getting media info..." << endl;

                // Get media information from presigned URL
                if (getMediaInformation(presignedUrl, response) != 0)
                {
                    LOG(error) << "Unable to get media info from cloud file: " << file << endl;
                    SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response,
                                  "Unable to get media info from cloud file");
                    return VmsErrorCode::VMSInternalError;
                }

                response["mediaFilePath"] = file; // Return the object key, not the presigned URL
                response["storageLocation"] = "cloud";
                return VmsErrorCode::NoError;
            }
            else
            {
                // Handle local file (existing logic)
                file = normalizeRelativePath(file, GET_CONFIG().nv_streamer_directory_path);
                if (!isFileExist(file))
                {
                    LOG(error) << "File path = " << file << " not present" << endl;
                    SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "File not present");
                    return VmsErrorCode::InvalidParameterError;
                }
                if (getMediaInformation(file, response) != 0)
                {
                    LOG(error) << "Unnable to get Media info for " << file << endl;
                    SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
                    return VmsErrorCode::VMSInternalError;
                }
                response["mediaFilePath"] = file;
                response["storageLocation"] = "local";
                return VmsErrorCode::NoError;
            }
        }
    }
    else
    {
        LOG(error) << "query string, sensorId / id is empty" << endl;
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return VmsErrorCode::InvalidParameterError;
    }

    const string request_api = req_info.get("url", EMPTY_STRING).asString();
    const string request_method = req_info.get("method", UNKNOWN_STRING).asString();
    if (request_api.empty() || request_method == UNKNOWN_STRING)
    {
        LOG(error) << "Malformed HTTP request" << endl;
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return VmsErrorCode::InvalidParameterError;
    }

    LOG(info) <<"sensorId: " << sensorId << std::endl;
    auto dbHelper = GET_DB_INSTANCE();

    vector<shared_ptr<SensorInfo>> sensors;
    dbHelper->getAllSensors(sensors, m_deviceId);

    for(uint32_t i = 0; i < sensors.size(); i++ )
    {
        shared_ptr<SensorInfo> sensor = sensors[i];
        vector<shared_ptr<StreamInfo>> streams = sensors[i]->streams;
        if (streams.size() > 0 && streams[0])
        {
            shared_ptr<StreamInfo> stream = streams[0];
            if (stream->sensorId == sensorId)
            {
                // Check if this is a cloud stream by looking at the URL scheme or querying the database
                bool isCloudStream = false;
                string objectKey;


                // Query database to check storage location
                // Pass 0 and max value for time range to get all records for this sensor
                vector<VideoRecordDBColumns> fileRecords = dbHelper->getVideoRecordFilePathsSensorIdBased(sensorId, 0, std::numeric_limits<int64_t>::max());
                if (!fileRecords.empty() && fileRecords[0].storage_location_value == StreamStorageTypeCloud)
                {
                    isCloudStream = true;
                    objectKey = fileRecords[0].filepath_value;
                }

                if (isCloudStream)
                {
                    LOG(info) << "Stream is stored in cloud storage, sensor: " << sensorId << endl;

                    // Check if cloud storage is enabled
                    if (!GET_CONFIG().enable_cloud_storage)
                    {
                        LOG(error) << "Cloud storage is not enabled" << endl;
                        SET_VMS_ERROR2(VmsErrorCode::MethodNotAllowedError, response, "Cloud storage is not enabled");
                        return VmsErrorCode::MethodNotAllowedError;
                    }

                    // Check if unified storage reader is available
                    if (!m_unifiedStorageReader)
                    {
                        LOG(error) << "Unified storage reader not available for cloud stream" << endl;
                        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Cloud storage reader not available");
                        return VmsErrorCode::VMSInternalError;
                    }

                    // Get presigned URL for the cloud file
                    std::string presignedUrl;
                    nv_vms::FileResult urlResult = m_unifiedStorageReader->getPresignedUrl(objectKey, presignedUrl);

                    if (!urlResult.success || presignedUrl.empty())
                    {
                        LOG(error) << "Failed to generate presigned URL for cloud stream: " << sensorId
                                  << ", Object: " << objectKey << ", Error: " << urlResult.message << endl;
                        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response,
                                      "Failed to generate presigned URL for cloud stream");
                        return VmsErrorCode::VMSInternalError;
                    }

                    LOG(info) << "Generated presigned URL for cloud stream, getting media info..." << endl;

                    // Get media information from presigned URL
                    if (getMediaInformation(presignedUrl, response) != 0)
                    {
                        LOG(error) << "Unable to get media info from cloud stream: " << sensorId << endl;
                        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response,
                                      "Unable to get media info from cloud stream");
                        return VmsErrorCode::VMSInternalError;
                    }

                    response["mediaFilePath"] = objectKey; // Return the object key
                    response["storageLocation"] = "cloud";
                    found = true;
                    break;
                }
                else
                {
                    // Handle local stream (existing logic)
                    string filepath;
                    if (m_deviceManager->getDeviceType() == TYPE_STREAMER)
                    {
                        filepath = getFilePathFromUrl(stream->live_url, NV_STREAMER);
                    }
                    else
                    {
                        filepath = stream->live_proxy_url;
                        filepath = normalizeRelativePath(filepath, GET_CONFIG().nv_streamer_directory_path);
                    }
                    if (!isFileExist(filepath))
                    {
                        LOG(error) << "File path = " << filepath << " not present" << endl;
                        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "File not present");
                        return VmsErrorCode::InvalidParameterError;
                    }
                    if (getMediaInformation(filepath, response) != 0)
                    {
                        LOG(error) << "Unnable to get Media info for " << filepath << endl;
                        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
                        return VmsErrorCode::VMSInternalError;
                    }
                    response["mediaFilePath"] = filepath;
                    response["storageLocation"] = "local";
                    found = true;
                    break;
                }
            }
        }
    }
    if (!found)
    {
        LOG(error) << "Camera ID is not found" << endl;
        SET_VMS_ERROR(VmsErrorCode::CameraNotFoundError, response)
        return VmsErrorCode::CameraNotFoundError;
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode StorageManagement::getFilePathSensorIdBased(const string &sensorId, const int64_t startTime, const int64_t endTime, Json::Value &response, bool get_metadata)
{
    LOG(info) << "Sensor ID: " << sensorId << endl;
    LOG(info) << "startTime: " << startTime << endl;
    LOG(info) << "endTime: " << endTime << endl;

    vector<VideoRecordDBColumns> fileRecords = GET_DB_INSTANCE()->getVideoRecordFilePathsSensorIdBased(sensorId, startTime, endTime);
    for (const auto& record : fileRecords)
    {
        LOG(info) << "File path: " << record.filepath_value << endl;
        Json::Value Info;
        Info["id"] = record.object_id_value;
        Info["mediaFilePath"] = record.filepath_value;
        Info["metaDataFilePath"] = record.metadata_file_path_value;
        if (get_metadata)
        {
            Info["metadata"] = record.metadata_json_value;
        }
        response.append(Info);
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode StorageManagement::getFilePathIdBased(const string &id, Json::Value &response, bool get_metadata)
{
    vector<VideoRecordDBColumns> fileRecords = GET_DB_INSTANCE()->getVideoRecordFilePathsIdBased(id);
    for (const auto& record : fileRecords)
    {
        LOG(info) << "File path: " << record.filepath_value << endl;
        Json::Value Info;
        Info["id"] = record.object_id_value;
        Info["mediaFilePath"] = record.filepath_value;
        Info["metaDataFilePath"] = record.metadata_file_path_value;
        if (get_metadata)
        {
            Info["metadata"] = record.metadata_json_value;
        }
        response = Info;
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode StorageManagement::getFileListSensorIdBased(const string &sensorId, const int offset, const int limit, Json::Value &response)
{
    LOG(info) << "Sensor ID: " << sensorId << ", offset: " << offset << ", limit: " << limit << endl;

    // Get all files for the sensor (no time constraints)
    int64_t startTime = 0;
    int64_t endTime = std::numeric_limits<int64_t>::max();

    vector<VideoRecordDBColumns> fileRecords = GET_DB_INSTANCE()->getVideoRecordFilePathsSensorIdBased(sensorId, startTime, endTime);

    // Apply offset and limit
    size_t totalCount = fileRecords.size();
    size_t startIndex = static_cast<size_t>(offset);
    size_t endIndex = (limit > 0) ? std::min(startIndex + static_cast<size_t>(limit), totalCount) : totalCount;

    Json::Value files(Json::objectValue);
    if (startIndex < totalCount)
    {
        for (size_t i = startIndex; i < endIndex; i++)
        {
            const auto& record = fileRecords[i];
            Json::Value fileInfo;
            fileInfo["mediaFilePath"] = record.filepath_value;
            fileInfo["metadataFilePath"] = record.metadata_file_path_value;

            // Parse and include metadata if available
            if (!record.metadata_json_value.empty())
            {
                try
                {
                    Json::CharReaderBuilder builder;
                    Json::CharReader* reader = builder.newCharReader();
                    Json::Value metadata;
                    string errors;

                    bool parsingSuccessful = reader->parse(
                        record.metadata_json_value.c_str(),
                        record.metadata_json_value.c_str() + record.metadata_json_value.length(),
                        &metadata,
                        &errors
                    );
                    delete reader;

                    if (parsingSuccessful && !metadata.isNull())
                    {
                        // Ensure the object id is included inside metadata before attaching
                        metadata["id"] = record.object_id_value;
                        fileInfo["metadata"] = metadata;
                    }
                    else
                    {
                        LOG(warning) << "Failed to parse metadata JSON for file: " << record.filepath_value << ", errors: " << errors << endl;
                        Json::Value fallbackMetadata;
                        fallbackMetadata["id"] = record.object_id_value;
                        fileInfo["metadata"] = fallbackMetadata;
                    }
                }
                catch (const std::exception& e)
                {
                    LOG(error) << "Exception parsing metadata JSON for file: " << record.filepath_value << ", error: " << e.what() << endl;
                    Json::Value fallbackMetadata;
                    fallbackMetadata["id"] = record.object_id_value;
                    fileInfo["metadata"] = fallbackMetadata;
                }
            }
            else
            {
                Json::Value fallbackMetadata;
                fallbackMetadata["id"] = record.object_id_value;
                fileInfo["metadata"] = fallbackMetadata;
            }
            //files[record.sensor_id_value] = fileInfo;
            files[record.sensor_id_value].append(fileInfo);

        }
    }

        response = files;

        return VmsErrorCode::NoError;
    }

VmsErrorCode StorageManagement::handleMediaFileDownload(const string &filePath, struct mg_connection *conn)
{
    string fileName = getFileName(filePath);
    string fileExtension = getFileExtension(filePath);
    string fullFileName = fileName + fileExtension;
    string addHeader =  FILE_DOWNLOAD_HEADER(fullFileName)
    mg_send_mime_file2(conn, filePath.c_str(), nullptr, addHeader.c_str());
    return VmsErrorCode::NoError;
}


// Add strict validation for filenames served via /storage/* to prevent traversal
static bool isSafeStorageFilename(const string& name)
{
    if (name.empty()) return false;
    if (name[0] == '/') return false;                 // absolute path
    if (name.find("..") != string::npos) return false; // parent directory traversal
    if (name.find('\\') != string::npos) return false; // backslash not allowed

    // Only allow temp_files/ subdirectory - reject everything else
    const string tempFilesPrefix = string(TEMP_STORAGE_DIR).substr(1) + "/"; // Remove leading "/" and add trailing "/"
    if (name.find(tempFilesPrefix) != 0)
    {
        return false; // Must start with temp_files/
    }

    // Extract the filename part after temp_files/
    string filename = name.substr(tempFilesPrefix.length());
    if (filename.empty()) return false;
    if (filename.find('/') != string::npos) return false; // no further subdirectories
    if (filename.find('\\') != string::npos) return false; // no backslashes
    return true;
}

VmsErrorCode StorageManagement::handleMediaURLRequest(const Json::Value& req_info, Json::Value &response, struct mg_connection *conn)
{
    std::string requestApi = req_info.get("url", EMPTY_STRING).asString();
    std::string queryString = req_info.get("query", EMPTY_STRING).asString();

    // Strip query string from requestApi if present
    size_t queryPos = requestApi.find('?');
    if (queryPos != std::string::npos)
    {
        if (queryString.empty())
        {
            queryString = requestApi.substr(queryPos + 1);
        }
        requestApi = requestApi.substr(0, queryPos);
    }

    if (requestApi.empty())
    {
        LOG(error) << "Malformed HTTP request - empty URL" << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Malformed HTTP request");
        return VmsErrorCode::InvalidParameterError;
    }

    // Parse streamable parameter
    std::string streamableStr;
    CivetServer::getParam(queryString, "streamable", streamableStr);
    bool isStreamable = (streamableStr == "true");

    // Parse and validate URL
    std::string filename;
    VmsErrorCode parseResult = parseStorageUrl(requestApi, filename, response);
    if (parseResult != VmsErrorCode::NoError)
    {
        return parseResult;
    }

    const std::string taskId = extractTaskId(filename);

    VideoGeneratorTaskManager* taskManager = VideoGeneratorTaskManager::getInstance();
    if (taskManager == nullptr)
    {
        LOG(error) << "[ASYNC_MEDIA] Failed to get TaskManager instance" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "TaskManager not available");
        return VmsErrorCode::VMSInternalError;
    }

    VideoTaskStatus taskStatus = taskManager->getTaskStatus(taskId);

    switch (taskStatus)
    {
        case VideoTaskStatus::IN_PROGRESS:
        case VideoTaskStatus::PENDING:
            return handleActiveTaskRequest(taskManager, taskId, filename, isStreamable, conn, response);

        case VideoTaskStatus::COMPLETED:
        case VideoTaskStatus::FAILED:
            return handleCompletedTaskRequest(taskManager, taskId, filename, conn, response);

        default:
            LOG(error) << "[ASYNC_MEDIA] Unknown task status: " << static_cast<int>(taskStatus) << " for: " << taskId << endl;
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Unknown task status");
            return VmsErrorCode::VMSInternalError;
    }
}

VmsErrorCode StorageManagement::handleActiveTaskRequest(VideoGeneratorTaskManager* taskManager,
                                                        const std::string& taskId,
                                                        const std::string& filename,
                                                        bool isStreamable,
                                                        struct mg_connection* conn,
                                                        Json::Value& response)
{
    if (!isStreamable)
    {
        return handleBlockingTaskWait(taskManager, taskId, conn, response);
    }

    return handleStreamingTaskRequest(taskManager, taskId, filename, conn, response);
}

VmsErrorCode StorageManagement::handleBlockingTaskWait(VideoGeneratorTaskManager* taskManager,
                                                       const std::string& taskId,
                                                       struct mg_connection* conn,
                                                       Json::Value& response)
{
    LOG(info) << "[ASYNC_MEDIA] Task in progress, waiting for completion: " << taskId << endl;

    std::string outputFilePath;
    auto waitStartTime = std::chrono::steady_clock::now();
    VmsErrorCode waitResult = taskManager->waitForTask(taskId, outputFilePath);
    auto waitDuration = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - waitStartTime).count();

    LOG(info) << "[ASYNC_MEDIA] waitForTask completed in " << waitDuration << "ms with result: "
              << static_cast<int>(waitResult) << ", outputPath: '" << outputFilePath << "'" << endl;

    if (waitResult != VmsErrorCode::NoError)
    {
        LOG(error) << "[ASYNC_MEDIA] Task failed during blocking wait: " << taskId
                   << " Error: " << static_cast<int>(waitResult) << endl;
        SET_VMS_ERROR2(waitResult, response, "Video generation failed");
        return waitResult;
    }

    if (!outputFilePath.empty() && isFileExist(outputFilePath))
    {
        LOG(info) << "[ASYNC_MEDIA] Task completed, serving file: " << outputFilePath << endl;
        serveMediaFile(conn, outputFilePath);
        return VmsErrorCode::NoError;
    }

    LOG(error) << "[ASYNC_MEDIA] Task completed but file not found: " << outputFilePath << endl;
    SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Generated file not found");
    return VmsErrorCode::InvalidParameterError;
}

VmsErrorCode StorageManagement::handleStreamingTaskRequest(VideoGeneratorTaskManager* taskManager,
                                                           const std::string& taskId,
                                                           const std::string& filename,
                                                           struct mg_connection* conn,
                                                           Json::Value& response)
{
    LOG(info) << "[ASYNC_MEDIA] Task in progress, streaming file for: " << taskId << endl;

    const std::string fileExt = getFileExtension(filename).empty() ? ".mp4" : getFileExtension(filename);
    std::string outputFilePath = VideoGeneratorTaskManager::ensureTempDirAndGenerateFilePath(taskId, fileExt);

    if (outputFilePath.empty())
    {
        LOG(error) << "[ASYNC_MEDIA] Failed to generate expected output file path" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Failed to generate output file path");
        return VmsErrorCode::VMSInternalError;
    }

    // Wait briefly for GStreamer to create the output file (pipeline initialization)
    constexpr int MAX_FILE_CREATE_RETRIES = 20;
    constexpr int RETRY_INTERVAL_MS = 100;
    int retries = 0;

    while (!isFileExist(outputFilePath) && retries < MAX_FILE_CREATE_RETRIES)
    {
        std::this_thread::sleep_for(std::chrono::milliseconds(RETRY_INTERVAL_MS));
        retries++;
    }

    if (!isFileExist(outputFilePath))
    {
        LOG(error) << "[ASYNC_MEDIA] Output file not created after " << (MAX_FILE_CREATE_RETRIES * RETRY_INTERVAL_MS)
                   << "ms: " << outputFilePath << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Media file generation not started");
        return VmsErrorCode::InvalidParameterError;
    }

    AsyncVideoStreamHandler streamHandler(outputFilePath, taskId);
    if (!streamHandler.isValid())
    {
        LOG(error) << "[ASYNC_MEDIA] Failed to create valid stream handler" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Invalid streaming configuration");
        return VmsErrorCode::VMSInternalError;
    }

    int streamResult = streamHandler.handler(conn);
    if (streamResult == 200)
    {
        return VmsErrorCode::NoError;
    }
    if (streamResult == 404)
    {
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Media file not found");
        return VmsErrorCode::InvalidParameterError;
    }

    SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Streaming error");
    return VmsErrorCode::VMSInternalError;
}

VmsErrorCode StorageManagement::handleCompletedTaskRequest(VideoGeneratorTaskManager* taskManager,
                                                           const std::string& taskId,
                                                           const std::string& filename,
                                                           struct mg_connection* conn,
                                                           Json::Value& response)
{
    LOG(verbose) << "[ASYNC_MEDIA] Task completed, serving file for: " << taskId << endl;

    const std::string fileExt = getFileExtension(filename).empty() ? ".mp4" : getFileExtension(filename);
    std::string outputFilePath = VideoGeneratorTaskManager::ensureTempDirAndGenerateFilePath(taskId, fileExt);

    if (!outputFilePath.empty() && isFileExist(outputFilePath))
    {
        LOG(verbose) << "[ASYNC_MEDIA] Serving completed temp file: " << outputFilePath << endl;
        serveMediaFile(conn, outputFilePath);
        return VmsErrorCode::NoError;
    }

    // Fallback to original file path
    const std::string webroot = VmsConfigManager::getInstance()->getWebRootPath();
    const std::string fullFilePath = normalizeRelativePath(filename, webroot);

    if (isFileExist(fullFilePath))
    {
        LOG(verbose) << "[ASYNC_MEDIA] Serving original file: " << fullFilePath << endl;
        serveMediaFile(conn, fullFilePath);
        return VmsErrorCode::NoError;
    }

    LOG(error) << "[ASYNC_MEDIA] File not found: " << taskId
               << " (temp: " << outputFilePath << ", original: " << fullFilePath << ")" << endl;
    SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Media file not found");
    return VmsErrorCode::InvalidParameterError;
}

void StorageManagement::serveMediaFile(struct mg_connection* conn, const std::string& filePath)
{
    const std::string fileExtension = getFileExtension(filePath);
    const std::string contentType = ::getMediaContentType(fileExtension);
    const std::string headers = "Content-Type: " + contentType + "\r\n"
                               "Cache-Control: no-cache\r\n";
    mg_send_mime_file2(conn, filePath.c_str(), contentType.c_str(), headers.c_str());
}

VmsErrorCode StorageManagement::parseStorageUrl(const std::string& requestApi, std::string& filename, Json::Value& response)
{
    constexpr std::string_view videoApiPrefix = "/storage/";
    constexpr size_t prefixLength = videoApiPrefix.length();  // Compile-time constant, avoids strlen

    if (requestApi.size() <= prefixLength || requestApi.substr(0, prefixLength) != videoApiPrefix)
    {
        LOG(error) << "Invalid storage API request: " << requestApi << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Invalid storage API request");
        return VmsErrorCode::InvalidParameterError;
    }

    filename = requestApi.substr(prefixLength);
    if (filename.empty())
    {
        LOG(error) << "No filename specified in video request" << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "No filename specified");
        return VmsErrorCode::InvalidParameterError;
    }

    if (!isSafeStorageFilename(filename))
    {
        LOG(error) << "Invalid filename in video request: " << filename << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Invalid filename");
        return VmsErrorCode::InvalidParameterError;
    }

    return VmsErrorCode::NoError;
}

std::string StorageManagement::extractTaskId(const std::string& filename)
{
    std::string actualFilename = filename;
    const std::string tempFilesPrefix = std::string(TEMP_STORAGE_DIR).substr(1) + "/";

    if (actualFilename.find(tempFilesPrefix) == 0)
    {
        actualFilename = actualFilename.substr(tempFilesPrefix.length());
    }

    const auto extensionPos = actualFilename.find_last_of('.');
    return (extensionPos != std::string::npos) ?
           actualFilename.substr(0, extensionPos) : actualFilename;
}

VmsErrorCode StorageManagement::processUploadMetadata(const Json::Value& metadata, const string& filePath, Json::Value& response)
{
    if (metadata.isNull())
    {
        LOG(info) << "No metadata to process for file: " << filePath << endl;
        return VmsErrorCode::NoError;
    }

    LOG(info) << "Processing upload metadata for file: " << filePath << endl;
    LOG(info) << "Metadata: " << endl << metadata.toStyledString() << endl;

    return VmsErrorCode::NoError;
}

VmsErrorCode StorageManagement::getUsedStorageSize(const Json::Value& req_info, Json::Value &response)
{
    Json::Value resp = Json::nullValue;
    unordered_map<string, string> device_idStatusMap;
    double gbPerDay = 0;
    double totalBitrate = 0;

    const string request_api = req_info.get("url", EMPTY_STRING).asString();
    const string request_method = req_info.get("method", UNKNOWN_STRING).asString();
    const string query_string = req_info.get("query", EMPTY_STRING).asString();
    if (request_api.empty() || request_method == UNKNOWN_STRING)
    {
        LOG(error) << "Malformed HTTP request" << endl;
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return VmsErrorCode::InvalidParameterError;
    }

    if (!(iequals(request_method, "get")))
    {
        LOG(error) << "Request Method is not supported" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
        return VmsErrorCode::VMSNotSupportedError;
    }

    int streamCount = 0;
    size_t pos = 0;
    string target = "streams=";
    while ((pos = query_string.find(target, pos)) != string::npos)
    {
        streamCount++;
        pos += target.length();
    }

    vector<string> streamList;
    size_t occurrence = 0;
    for (; streamCount > 0; streamCount--)
    {
        string streamId;
        if (CivetServer::getParam(query_string, "streams", streamId, occurrence) == false)
        {
            LOG(error) << "Failed to get streamId" << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Failed to get streamId");
            return VmsErrorCode::InvalidParameterError;
        }
        streamList.push_back(streamId);
        occurrence++;
    }

    string timelines;
    CivetServer::getParam(query_string, "timelines", timelines);

    if (timelines == "true")
    {
        LOG(info) << "Timelines needs to provide" << endl;
    }
    else
    {
        LOG(info) << "Timelines don't needs to provide" << endl;
    }

    for (auto it = streamList.begin(); it != streamList.end(); ++it)
    {
        LOG(verbose) << "Receieved stream Id: " << *it << endl;
    }

    /* TODO - Take bitrate over api call or databse */
#if 0
    vector<shared_ptr<SensorInfo>> sensors = m_sensorManagement->getSensorList();
    /* Construct map to search if recorded videos folder belong to some removed camera or not */
    for(uint32_t i = 0; i < sensors.size(); i++)
    {
        shared_ptr<SensorInfo> sensor = sensors[i];
        device_idStatusMap[sensor->id] = sensor->getDeviceStatus() == SensorStatusOffline ? CAMERA_STATE_OFFLINE : CAMERA_STATE_ONLINE;
        /* get aprx data consumed per day using bitrate */
        Json::Value errorCode;
        VmsErrorCode code = translateCameraHttpErrorCodeToVmsErrorCode(sensor->getHttpErrorStatus().first);
        std::pair<string, string> code_pair = getCameraErrorCodeString(code);
        string error_code_str = code_pair.first;

        /* get bitrate of each sensor */
        if (error_code_str == "NoError")
        {
            DeviceSettings settings;
            shared_ptr<StreamInfo> stream = m_sensorManagement->getStreamInfo(sensor->id, sensor->id);
            settings = stream->settings;
            try
            {
                totalBitrate += stringToInt(settings.encoderValues.bitrate, 0);
            }
            catch(const std::exception& e)
            {
                LOG(error) << e.what() << endl;
            }
        }
    }
#endif
    if(totalBitrate == 0)
    {
        totalBitrate = DEFAULT_BITRATE;
    }
    LOG(verbose) << totalBitrate << endl;
    gbPerDay = CONVERT_KBPS_TO_GBPERDAY(totalBitrate)

    if(getCurrentUsedStorageSize(device_idStatusMap, gbPerDay, streamList, timelines, resp) != 0)
    {
        LOG(error) << "Unable to get current used storage size" << endl;
        return VmsErrorCode::VMSInternalError;
    }

    response = resp;

    LOG(verbose) << "Record sizes: " << response.toStyledString() << endl;
    return VmsErrorCode::NoError;
}

VmsErrorCode StorageManagement::deleteFilesByTime(const Json::Value& req_info, Json::Value &response)
{
    const string request_api = req_info.get("url", EMPTY_STRING).asString();
    const string request_method = req_info.get("method", UNKNOWN_STRING).asString();
    const string query_string = req_info.get("query", EMPTY_STRING).asString();

    string device_api(STORAGE_FILE_API);
    string path = request_api.substr(device_api.size() - 1);

    LOG(verbose2) << "Recorder API path: " << path << std::endl;
    vector<string> path_arr = splitString(path, "/");
    string sensor_id = EMPTY_STRING;
    string stream_id = path_arr[0];
    string action = path_arr.size() >= 2 ? path_arr[1] : "";
    if (path_arr[0] == "stream")
    {
        stream_id = path_arr.size() >= 2 ? path_arr[1] : "";
        action = path_arr.size() >= 3 ? path_arr[2] : "";
    }

    string start_time, end_time;
    CivetServer::getParam(query_string, "startTime", start_time);
    CivetServer::getParam(query_string, "endTime", end_time);

    LOG(info) << "deleteFilesByTime id: " << stream_id << " startTime: "<< start_time << " endTime: " << end_time << endl;
    uint32_t spaceSaved = 0;
    int64_t startTime = 0, endTime = 0;
    if(start_time.empty() || end_time.empty())
    {
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return VmsErrorCode::InvalidParameterError;
    }
    try
    {
        if(start_time.compare("*") == 0)
        {
            startTime = -1;
        }
        else
        {
            startTime = isoToEpoch(start_time);
        }
        if(end_time.compare("*") == 0)
        {
            endTime = std::numeric_limits<int64_t>::max();
        }
        else
        {
            endTime = isoToEpoch(end_time);
        }
    }
    catch(...)
    {
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return VmsErrorCode::InvalidParameterError;
    }

    if(deleteFilesByTime(stream_id, startTime, endTime, spaceSaved) != 0)
    {
        SET_VMS_ERROR(VmsErrorCode::VMSInternalError, response)
        LOG(error) << "Unable to delete video files or database entries" << endl;
        return VmsErrorCode::VMSInternalError;
    }
    LOG(info) << "Space saved: " << spaceSaved << " MB" << endl;
    response["spaceSaved"] = spaceSaved;
    return VmsErrorCode::NoError;
}

VmsErrorCode StorageManagement::getSpecificStreamRecordSize(const string& stream_id, Json::Value& stream_record_size)
{
    size_t videoSize = 0;

    if(getStreamRecordSize(stream_id, videoSize) != 0)
    {
        LOG(error) << "Unable to get recorded videos size" << endl;
        return VmsErrorCode::VMSInternalError;
    }
    if(videoSize == 0)
    {
        stream_record_size = Json::nullValue;
    }
    else
    {
        stream_record_size["size_in_mb"] = (uint32_t)videoSize;
    }
    return VmsErrorCode::NoError;
}

VmsErrorCode StorageManagement::updateStorageSize(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
{
    string streamId;
    response = Json::nullValue;

    const string request_api = req_info.get("url", EMPTY_STRING).asString();
    const string request_method = req_info.get("method", UNKNOWN_STRING).asString();
    const string query_string = req_info.get("query", EMPTY_STRING).asString();
    if (request_api.empty() || request_method == UNKNOWN_STRING)
    {
        LOG(error) << "Malformed HTTP request" << endl;
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return VmsErrorCode::InvalidParameterError;
    }

    if (!(iequals(request_method, "post")))
    {
        LOG(error) << "Request Method is not supported" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
        return VmsErrorCode::VMSNotSupportedError;
    }

    vector<string> fileList;

    uint64_t size = in.get("size", 0).asUInt64();

    // Safe boolean extraction with type checking
    Json::Value addOrRemoveValue = in.get("addOrRemove", false);
    bool addOrRemove = false;
    if (addOrRemoveValue.isBool())
    {
        addOrRemove = addOrRemoveValue.asBool();
    }
    else
    {
        LOG(error) << "Invalid addOrRemove field type: expected boolean, got type " << addOrRemoveValue.type() << std::endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "addOrRemove field must be a boolean");
        return VmsErrorCode::InvalidParameterError;
    }

    LOG(verbose) <<"Received size: " << size << " addOrRemove:" << addOrRemove << std::endl;

    if (size)
    {
        /* Update storage size */
        updateStorageSize(size, addOrRemove);
    }

    return VmsErrorCode::NoError;
}

VmsErrorCode StorageManagement::doAging(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
{
    response = Json::nullValue;

    const string request_api = req_info.get("url", EMPTY_STRING).asString();
    const string request_method = req_info.get("method", UNKNOWN_STRING).asString();
    const string query_string = req_info.get("query", EMPTY_STRING).asString();
    if (request_api.empty() || request_method == UNKNOWN_STRING)
    {
        LOG(error) << "Malformed HTTP request" << endl;
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return VmsErrorCode::InvalidParameterError;
    }

    if (!(iequals(request_method, "post")))
    {
        LOG(error) << "Request Method is not supported" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
        return VmsErrorCode::VMSNotSupportedError;
    }

    vector<string> fileList;

    size_t reserveSize = in.get("bytesToReserve", 0).asUInt64();
    LOG(verbose) <<"Received reserveSize: " << reserveSize << std::endl;

    if (reserveSize)
    {
        /* Do aging */
        checkandCreateFreeSpaceInStorage(reserveSize);
    }

    return VmsErrorCode::NoError;
}

VmsErrorCode StorageManagement::addOrRemoveFileInProtectList(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
{
    const string request_api = req_info.get("url", EMPTY_STRING).asString();
    const string request_method = req_info.get("method", UNKNOWN_STRING).asString();
    const string query_string = req_info.get("query", EMPTY_STRING).asString();
    if (request_api.empty() || request_method == UNKNOWN_STRING)
    {
        LOG(error) << "Malformed HTTP request" << endl;
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return VmsErrorCode::InvalidParameterError;
    }

    if (!(iequals(request_method, "post")))
    {
        LOG(error) << "Request Method is not supported" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
        return VmsErrorCode::VMSNotSupportedError;
    }

    vector<string> fileList;

    Json::Value fileListJson = in.get("filePath", EMPTY_STRING);

    // Safe boolean extraction with type checking
    Json::Value protectValue = in.get("protect", false);
    bool protect = false;
    if (protectValue.isBool())
    {
        protect = protectValue.asBool();
    }
    else
    {
        LOG(error) << "Invalid protect field type: expected boolean, got type " << protectValue.type() << std::endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "protect field must be a boolean");
        return VmsErrorCode::InvalidParameterError;
    }

    LOG(info) <<"Received file list: " << fileListJson << " protect:" << protect << std::endl;
    if(!fileListJson.empty())
    {
        for (auto x: fileListJson)
        {
            string filePath = x.asString();
            // Skip empty strings
            if (!filePath.empty())
            {
                fileList.push_back(filePath);
            }
        }
    }

    // Check if we have any valid files after filtering empty strings
    if(fileList.empty())
    {
        LOG(error) << "FilePath is not present or all paths are empty in the input parameters" << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "FilePath is not present or all paths are empty in the input parameters")
        return VmsErrorCode::InvalidParameterError;
    }

    /* Check all the file are present in the DB or not */
    if (false == isReceivedFilesInvalid(fileList))
    {
        LOG(error) << "Invalid filePath given in input parameters" << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Invalid filePath given in input parameters");
        return VmsErrorCode::InvalidParameterError;
    }

    vector<string> invalidFileList;
    getInvalidFilesIfAny(fileList, invalidFileList);

    std::vector<string> filesToUpdate;
    for (auto outerIt = fileList.begin(); outerIt != fileList.end(); ++outerIt)
    {
        auto it = find(invalidFileList.begin(), invalidFileList.end(), *outerIt);
        if ((it == invalidFileList.end()) && ((protect && !isFileProtected(*outerIt)) || (!protect && isFileProtected(*outerIt))))
        {
            filesToUpdate.push_back(*outerIt);
        }
    }

    // Update all files in a single batch operation
    if (!filesToUpdate.empty())
    {
        addFilesInProtectList(filesToUpdate, protect);
    }

    if (invalidFileList.empty())
    {
        response["invalidFiles"] = Json::arrayValue;
    }
    else
    {
        response["invalidFiles"] = vectorToJson(invalidFileList);
    }

    return VmsErrorCode::NoError;
}

VmsErrorCode StorageManagement::deleteFilesByNames(const Json::Value& req_info, Json::Value &response)
{
    const string request_api = req_info.get("url", EMPTY_STRING).asString();
    const string request_method = req_info.get("method", UNKNOWN_STRING).asString();
    const string query_string = req_info.get("query", EMPTY_STRING).asString();
    if (request_api.empty() || request_method == UNKNOWN_STRING)
    {
        LOG(error) << "Malformed HTTP request" << endl;
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return VmsErrorCode::InvalidParameterError;
    }

    if (!(iequals(request_method, "delete")))
    {
        LOG(error) << "Request Method is not supported" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
        return VmsErrorCode::VMSNotSupportedError;
    }

    string id;
    vector<string> fileList;
    CivetServer::getParam(query_string, "id", id);
    if (id.empty() == false)
    {
        vector<VideoRecordDBColumns> fileRecords = GET_DB_INSTANCE()->getVideoRecordFilePathsIdBased(id);
        if (fileRecords.empty())
        {
            LOG(error) << "File not found for Unique ID " << id << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "File not found for Unique ID " + id)
            return VmsErrorCode::InvalidParameterError;
        }
        for (const auto& record : fileRecords)
        {
            LOG(info) << "File path from unique ID: " << record.filepath_value << endl;
            fileList.push_back(record.filepath_value);
        }
    }
    else
    {
        int fileCount = 0;
        size_t pos = 0;
        string target = "filePath=";
        while ((pos = query_string.find(target, pos)) != string::npos)
        {
            fileCount++;
            pos += target.length();
        }

        if (fileCount == 0)
        {
            LOG(error) << "FilePath is not present in the input parameters" << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "FilePath is not present in the input parameters")
            return VmsErrorCode::InvalidParameterError;
        }
        size_t occurrence = 0;
        for (; fileCount > 0; fileCount--)
        {
            string file;
            if (CivetServer::getParam(query_string, "filePath", file, occurrence) == false)
            {
                LOG(error) << "Failed to get file paths" << endl;
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Failed to get file paths");
                return VmsErrorCode::InvalidParameterError;
            }
            // Skip empty strings
            if (!file.empty())
            {
                fileList.push_back(file);
            }
            occurrence++;
        }
    }

    // Check if we have any valid files after filtering empty strings
    if (fileList.empty())
    {
        LOG(error) << "No valid file paths provided (all paths are empty)" << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "No valid file paths provided");
        return VmsErrorCode::InvalidParameterError;
    }

    for (auto it = fileList.begin(); it != fileList.end(); ++it)
    {
        LOG(verbose) << "files: " << *it << endl;
    }

    /* Check all the file are present in the DB or not */
    if (false == isReceivedFilesInvalid(fileList))
    {
        LOG(error) << "Invalid filePath given in input parameters" << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Invalid filePath given in input parameters");
        return VmsErrorCode::InvalidParameterError;
    }

    vector<string> invalidFileList;
    /* Get the invalid files if any */
    getInvalidFilesIfAny(fileList, invalidFileList);

    for (auto it = invalidFileList.begin(); it != invalidFileList.end(); ++it)
    {
        LOG(verbose) << "Invalid files: " << *it << endl;
    }

    unordered_set<string> fileProtectList = getProtectedFilesList();

    vector<string> protectedFileList;
    vector<string> deleteFileList;
    /* Get the protected files if any */
    {
        for (auto outerIt = fileList.begin(); outerIt != fileList.end(); ++outerIt)
        {
            if (fileProtectList.find(*outerIt) == fileProtectList.end())
            {
                auto it = find(invalidFileList.begin(), invalidFileList.end(), *outerIt);
                if (it == invalidFileList.end())
                {
                    deleteFileList.push_back(*outerIt);
                }
            }
            else
            {
                protectedFileList.push_back(*outerIt);
                LOG(info) << "Can't Delete: " << *outerIt << " is protected" << endl;
            }
        }
    }

    for (auto it = protectedFileList.begin(); it != protectedFileList.end(); ++it)
    {
        LOG(verbose) << "protectedFileList files: " << *it << endl;
    }

    for (auto it = deleteFileList.begin(); it != deleteFileList.end(); ++it)
    {
        LOG(verbose) << "deleteFileList files: " << *it << endl;
    }

    const string root_camera_dir = GET_CONFIG().recorded_video_root;
    size_t spaceSaved = 0;
    /* Delete the given files in the VST */
    for (auto it = deleteFileList.begin(); it != deleteFileList.end(); ++it)
    {
        LOG(verbose) << "Removing file: " << *it << endl;

        /* Delete actual file and its DB entry */
        uint64_t actualDeletedBytes = deleteMediaFile(*it);
        string parent_folder = getDirPath(*it);
        deleteEmptyDirectories(parent_folder, root_camera_dir);

        updateStorageSize(actualDeletedBytes, false);
        spaceSaved += actualDeletedBytes;

        LOG(info) << "Deleted File: " << *it << " size: " << actualDeletedBytes << " spaceSaved: " << spaceSaved << endl;
    }

    response = Json::nullValue;

    if (invalidFileList.empty())
    {
        response["invalidFiles"]  = Json::arrayValue;
    }
    else
    {
        response["invalidFiles"] = vectorToJson(invalidFileList);
    }
    if (protectedFileList.empty())
    {
        response["protectedFiles"]  = Json::arrayValue;
    }
    else
    {
        response["protectedFiles"] = vectorToJson(protectedFileList);
    }
    response["spaceSaved"] = to_MB(spaceSaved);

    return VmsErrorCode::NoError;
}

bool StorageManagement::isReceivedFilesInvalid(vector<string> fileList)
{
    if (fileList.empty())
    {
        LOG(error) << "There is no file is passed" << endl;
        return false;
    }

    // Check if unified storage manager is available
    if (!m_unifiedStorageManager)
    {
        LOG(warning) << "Unified storage manager is not initialized, cannot validate files" << endl;
        return false;
    }

    std::vector<VideoRecordDBColumns> filePaths = GET_DB_INSTANCE()->getAllVideoRecordFilePaths();

    /* Normalize relative paths only for local files */
    {
        for (auto& filePath : filePaths)
        {
            // Don't normalize cloud object keys - they're not filesystem paths
            if (filePath.storage_location_value != StreamStorageTypeCloud)
            {
                filePath.filepath_value = normalizeRelativePath(filePath.filepath_value, GET_CONFIG().nv_streamer_directory_path);
            }
        }
    }
    bool found = false;
    for (auto outerIt = fileList.begin(); outerIt != fileList.end(); ++outerIt)
    {
        // Keep original path for comparison with cloud object keys
        string originalPath = *outerIt;
        string normalizedPath = normalizeRelativePath(*outerIt, GET_CONFIG().nv_streamer_directory_path);

        LOG(verbose) << "Validating file protection request - originalPath: '" << originalPath
                  << "', normalizedPath: '" << normalizedPath << "'" << endl;

        for (auto innerIt = filePaths.begin(); innerIt != filePaths.end(); ++innerIt)
        {
            // For cloud files, check against both filepath_value and object_id_value
            // Use original path (not normalized) for cloud object key matching
            bool matchesFilepath = false;
            bool matchesObjectId = false;

            if (innerIt->storage_location_value == StreamStorageTypeCloud)
            {
                // For cloud files, compare against original (non-normalized) path
                matchesFilepath = (innerIt->filepath_value == originalPath);
                matchesObjectId = (innerIt->object_id_value == originalPath);
            }
            else
            {
                // For local files, use normalized path
                matchesFilepath = (innerIt->filepath_value == normalizedPath);
            }

            if (matchesFilepath || matchesObjectId)
            {
                // For cloud storage files, skip strict existence validation
                // This allows protecting files during playback without filesystem checks
                if (innerIt->storage_location_value == StreamStorageTypeCloud)
                {
                    LOG(info) << "Cloud file found in database (filepath or objectId match), skipping existence check: " << originalPath << endl;
                    found = true;
                    break;
                }
                // For local files, validate existence
                else if (StorageManagement::isFileExist(normalizedPath, innerIt->object_id_value))
                {
                    found = true;
                    break;
                }
            }
        }
        if (found == true)
        {
            break;
        }
    }

    if (!found)
    {
        LOG(warning) << "File protection validation FAILED for: '" << *fileList.begin()
                     << "' - not found in database or failed existence check" << endl;
    }

    return found;
}

void StorageManagement::getInvalidFilesIfAny(vector<string> fileList, vector<string>& invalidFileList)
{
    if (fileList.empty())
    {
        return;
    }

    // Check if unified storage manager is available
    if (!m_unifiedStorageManager)
    {
        LOG(warning) << "Unified storage manager is not initialized, cannot validate files" << endl;
        // Add all files to invalid list since we can't validate them
        invalidFileList.insert(invalidFileList.end(), fileList.begin(), fileList.end());
        return;
    }

    bool found;
    std::vector<VideoRecordDBColumns> filePaths = GET_DB_INSTANCE()->getAllVideoRecordFilePaths();

    /* Normalize relative paths only for local files */
    {
        for (auto& filePath : filePaths)
        {
            // Don't normalize cloud object keys - they're not filesystem paths
            if (filePath.storage_location_value != StreamStorageTypeCloud)
            {
                filePath.filepath_value = normalizeRelativePath(filePath.filepath_value, GET_CONFIG().nv_streamer_directory_path);
            }
        }
    }
    for (auto outerIt = fileList.begin(); outerIt != fileList.end(); ++outerIt)
    {
        found = false;
        // Keep original path for comparison with cloud object keys
        string originalPath = *outerIt;
        string normalizedPath = normalizeRelativePath(*outerIt, GET_CONFIG().nv_streamer_directory_path);

        for (auto innerIt = filePaths.begin(); innerIt != filePaths.end(); ++innerIt)
        {
            // For cloud files, check against both filepath_value and object_id_value
            // Use original path (not normalized) for cloud object key matching
            bool matchesFilepath = false;
            bool matchesObjectId = false;

            if (innerIt->storage_location_value == StreamStorageTypeCloud)
            {
                // For cloud files, compare against original (non-normalized) path
                matchesFilepath = (innerIt->filepath_value == originalPath);
                matchesObjectId = (innerIt->object_id_value == originalPath);
            }
            else
            {
                // For local files, use normalized path
                matchesFilepath = (innerIt->filepath_value == normalizedPath);
            }

            if (matchesFilepath || matchesObjectId)
            {
                // For cloud storage files, skip strict existence validation
                if (innerIt->storage_location_value == StreamStorageTypeCloud)
                {
                    found = true;
                    break;
                }
                // For local files, validate existence
                else if (StorageManagement::isFileExist(normalizedPath, innerIt->object_id_value))
                {
                    found = true;
                    break;
                }
            }
        }

        if (!found)
        {
            invalidFileList.push_back(*outerIt);
        }
    }
}

VmsErrorCode StorageManagement::getStorageInfo(const Json::Value& req_info, Json::Value &response)
{
    const string request_api = req_info.get("url", EMPTY_STRING).asString();
    const string request_method = req_info.get("method", UNKNOWN_STRING).asString();
    const string query_string = req_info.get("query", EMPTY_STRING).asString();
    if (request_api.empty() || request_method == UNKNOWN_STRING)
    {
        LOG(error) << "Malformed HTTP request" << endl;
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return VmsErrorCode::InvalidParameterError;
    }

    if (!(iequals(request_method, "get")))
    {
        LOG(error) << "Request Method is not supported" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
        return VmsErrorCode::VMSNotSupportedError;
    }

    DeviceConfig config = GET_CONFIG();
    size_t actualDiskCapacity = getStorageCapacity(config.recorded_video_root);
    actualDiskCapacity = to_MB(actualDiskCapacity);
    size_t userDiskCapacity = config.total_video_storage_size_MB;
    size_t diskCapacity = std::min(userDiskCapacity, actualDiskCapacity);

    if (to_bytes(diskCapacity) < m_currentUsedStorage)
    {
        response["available"] = 0;
    }
    else
    {
        response["available"] = (uint32_t)(diskCapacity - ((size_t)to_MB(m_currentUsedStorage)));
    }
    response["used"] = (uint32_t)to_MB(m_currentUsedStorage);
    response["total"] = (uint32_t)diskCapacity;

    LOG(info) << "Storage Info Resp:" << response << endl;

    return VmsErrorCode::NoError;
}

VmsErrorCode StorageManagement::getProtectedFiles(const Json::Value& req_info, Json::Value &response)
{
    const string request_api = req_info.get("url", EMPTY_STRING).asString();
    const string request_method = req_info.get("method", UNKNOWN_STRING).asString();
    const string query_string = req_info.get("query", EMPTY_STRING).asString();
    if (request_api.empty() || request_method == UNKNOWN_STRING)
    {
        LOG(error) << "Malformed HTTP request" << endl;
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return VmsErrorCode::InvalidParameterError;
    }

    if (!(iequals(request_method, "get")))
    {
        LOG(error) << "Request Method is not supported" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
        return VmsErrorCode::VMSNotSupportedError;
    }

    {
        unordered_set<string> fileProtectList = getProtectedFilesList();
        for (auto outerIt = fileProtectList.begin(); outerIt != fileProtectList.end(); ++outerIt)
        {
            if (!outerIt->empty())
            {
                response.append(*outerIt);
            }
        }
    }

    if (response == Json::nullValue)
    {
        response = Json::arrayValue;
    }

    LOG(info) << "Protected File Resp:" << response << endl;

    return VmsErrorCode::NoError;
}

VmsErrorCode StorageManagement::parseS3XMLResponse(const string& xmlContent, const string& bucketName,
                                                   const string& prefix, Json::Value& response)
{
    try
    {
        LOG(verbose) << "Parsing S3 XML response" << endl;

        Json::Value files = Json::arrayValue;
        uint64_t totalSize = 0;
        size_t pos = 0;

        // Simple XML parsing for S3 ListBucket response
        while ((pos = xmlContent.find("<Contents>", pos)) != string::npos)
        {
            size_t endPos = xmlContent.find("</Contents>", pos);
            if (endPos == string::npos) break;

            string contentBlock = xmlContent.substr(pos, endPos - pos);
            Json::Value fileInfo;

            // Extract Key
            size_t keyStart = contentBlock.find("<Key>") + 5;
            size_t keyEnd = contentBlock.find("</Key>");
            if (keyStart != string::npos && keyEnd != string::npos)
            {
                fileInfo["key"] = contentBlock.substr(keyStart, keyEnd - keyStart);
            }

            // Extract Size
            size_t sizeStart = contentBlock.find("<Size>") + 6;
            size_t sizeEnd = contentBlock.find("</Size>");
            if (sizeStart != string::npos && sizeEnd != string::npos)
            {
                string sizeStr = contentBlock.substr(sizeStart, sizeEnd - sizeStart);
                uint64_t fileSize = strtoull(sizeStr.c_str(), nullptr, 10);
                fileInfo["size"] = fileSize;
                totalSize += fileSize;
            }

            // Extract LastModified
            size_t lastModStart = contentBlock.find("<LastModified>") + 14;
            size_t lastModEnd = contentBlock.find("</LastModified>");
            if (lastModStart != string::npos && lastModEnd != string::npos)
            {
                fileInfo["lastModified"] = contentBlock.substr(lastModStart, lastModEnd - lastModStart);
            }

            // Extract ETag
            size_t etagStart = contentBlock.find("<ETag>") + 6;
            size_t etagEnd = contentBlock.find("</ETag>");
            if (etagStart != string::npos && etagEnd != string::npos)
            {
                fileInfo["etag"] = contentBlock.substr(etagStart, etagEnd - etagStart);
            }

            // Extract StorageClass
            size_t classStart = contentBlock.find("<StorageClass>") + 14;
            size_t classEnd = contentBlock.find("</StorageClass>");
            if (classStart != string::npos && classEnd != string::npos)
            {
                fileInfo["storageClass"] = contentBlock.substr(classStart, classEnd - classStart);
            }
            else
            {
                fileInfo["storageClass"] = "STANDARD";
            }

            files.append(fileInfo);
            pos = endPos + 1;
        }

        response["bucket"] = bucketName;
        response["prefix"] = prefix;
        response["files"] = files;
        response["count"] = files.size();
        response["totalSize"] = totalSize;

        LOG(info) << "Parsed " << files.size() << " objects from S3 XML response" << endl;
        return VmsErrorCode::NoError;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception during S3 XML parsing: " << e.what() << endl;
        return VmsErrorCode::VMSInternalError;
    }
}

VmsErrorCode StorageManagement::listCloudObjects(const string& bucketName, const string& prefix,
                                              const string& region, const string& accessKeyId,
                                              const string& secretAccessKey, Json::Value& response)
{
    try
    {
        LOG(info) << "Listing S3 objects using Unified Storage Reader - Bucket: " << bucketName << ", Prefix: " << prefix << endl;

        // Use the unified storage reader API to list cloud objects
        nv_vms::CloudListResult result;

        {
            // Check if unified storage reader is initialized and is cloud type
            if (!m_unifiedStorageReader || !m_cloudStorageEnabled)
            {
                LOG(error) << "Unified cloud storage reader not available" << endl;
                response = Json::nullValue;
                return VmsErrorCode::VMSInternalError;
            }

            LOG(info) << "Using unified storage reader API to list cloud objects" << endl;

            // Call the new API to list cloud objects
            result = m_unifiedStorageReader->listCloudObjects(bucketName, prefix, 1000);
        }

        // Check result and convert to JSON response (using the same format as before)
        if (result.success)
        {
            // Use utility function to convert result to JSON
            response = convertCloudListResultToJson(result);

            LOG(info) << "Successfully listed " << result.count << " objects from S3 via Unified Storage Reader" << endl;
            return VmsErrorCode::NoError;
        }

        LOG(warning) << "Cloud Reader method failed: " << result.message << ", trying Python fallback" << endl;
        string pythonScript = "./s3_list_fallback.py";
        if (!isFileExist(pythonScript))
        {
            pythonScript = "/usr/local/bin/s3_list_fallback.py";
        }

        if (isFileExist(pythonScript))
        {
            string tempOutputFile = "/tmp/s3_list_fallback_" + to_string(time(nullptr)) + ".json";
            string pythonCommand = "python3 " + pythonScript + " " + bucketName;
            if (!prefix.empty())
            {
                pythonCommand += " \"" + prefix + "\"";
            }
            pythonCommand += " \"" + accessKeyId + "\" \"" + secretAccessKey + "\" \"" + region + "\"";
            pythonCommand += " > " + tempOutputFile;

            int pythonResult = system(pythonCommand.c_str());
            if (pythonResult == 0)
            {
                // Read and parse Python output
                ifstream outputFile(tempOutputFile);
                if (outputFile.is_open())
                {
                    string jsonContent((istreambuf_iterator<char>(outputFile)),
                                      istreambuf_iterator<char>());
                    outputFile.close();
                    deleteFile(tempOutputFile);

                    Json::CharReaderBuilder builder;
                    istringstream iss(jsonContent);
                    string errs;

                    if (Json::parseFromStream(builder, iss, &response, &errs))
                    {
                        LOG(info) << "Python fallback succeeded" << endl;
                        return VmsErrorCode::NoError;
                    }
                }
            }
            deleteFile(tempOutputFile);
        }

        LOG(error) << "All S3 listing methods failed" << endl;
        return VmsErrorCode::VMSInternalError;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception during S3 list operation: " << e.what() << endl;
        return VmsErrorCode::VMSInternalError;
    }
}


/**
 * Import file from AWS S3 bucket
 *
 * API Endpoint: POST /api/v1/storage/file/import
 *
 * Request Body JSON:
 * {
 *   "bucketName": "vst-cloud-s3-storage-poc",
 *   "objectKey": "path/to/your/video.mp4",
 *   "fileName": "imported_video.mp4",
 *   "streamName": "S3ImportedStream",
 *   "useReadWriteCreds": false  // optional, defaults to read-only creds
 * }
 *
 * Response JSON (success):
 * {
 *   "message": "File imported successfully from S3",
 *   "fileName": "imported_video.mp4",
 *   "localPath": "/path/to/local/file",
 *   "id": "stream_id"
 * }
 *
 * Response JSON (error):
 * {
 *   "error": "error_code",
 *   "message": "error description"
 * }
 */
// Function removed - using importFileFromCloud instead

// Function removed - using downloadFileFromCloud instead

/**
 * List files from AWS S3 bucket using AWS SDK authentication
 *
 * API Endpoint: GET /api/v1/storage/file/list
 *
 * Features:
 *   - Uses AWS Signature V4 authentication for secure access
 *   - Supports both private and public S3 buckets
 *   - Automatic fallback to Python boto3 if needed
 *
 * Query Parameters:
 *   - bucketName (optional): S3 bucket name (uses config if not provided)
 *   - prefix (optional): Prefix to filter objects
 *   - useReadWriteCreds (optional): Use read-write credentials (default: false)
 *
 * Response JSON (success):
 * {
 *   "bucket": "bucket-name",
 *   "prefix": "prefix-string",
 *   "files": [
 *     {
 *       "key": "path/to/file.mp4",
 *       "size": 1024576,
 *       "lastModified": "2025-01-08T12:00:00.000Z",
 *       "etag": "\"d41d8cd98f00b204e9800998ecf8427e\"",
 *       "storageClass": "STANDARD"
 *     }
 *   ],
 *   "count": 1,
 *   "totalSize": 1024576
 * }
 *
 * Response JSON (error):
 * {
 *   "error": "error_code",
 *   "message": "error description"
 * }
 */
// Function removed - using listCloudFiles instead

VmsErrorCode StorageManagement::importFileFromCloud(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
{
    const string request_method = req_info.get("method", UNKNOWN_STRING).asString();

    if (!(iequals(request_method, "post")))
    {
        LOG(error) << "Request Method is not supported" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
        return VmsErrorCode::VMSNotSupportedError;
    }

    // Check max sensors limit
    VmsErrorCode maxSensorsLimitResult = checkMaxSensorsLimit(m_deviceManager, response);
    if (maxSensorsLimitResult != VmsErrorCode::NoError)
    {
        LOG(error) << "Maximum number of streams limit reached" << endl;
        return maxSensorsLimitResult;
    }

    // Parse input parameters
    string bucketName = in.get("bucketName", "").asString();
    string objectKey = in.get("objectKey", "").asString();
    string fileName = in.get("fileName", "").asString();
    string streamName = in.get("streamName", "").asString();

    if (bucketName.empty() || objectKey.empty() || fileName.empty() || streamName.empty())
    {
        LOG(error) << "Missing required parameters: bucketName, objectKey, fileName, or streamName" << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Missing required parameters");
        return VmsErrorCode::InvalidParameterError;
    }

    // Validate file name
    if (checkWhiteSpace(fileName.c_str()))
    {
        LOG(error) << "Whitespaces not allowed in file name" << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Whitespaces not allowed in file name");
        return VmsErrorCode::InvalidParameterError;
    }

    if (checkFileNameLength(fileName.c_str()))
    {
        LOG(error) << "File name is too long" << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "File name is too long");
        return VmsErrorCode::InvalidParameterError;
    }

    LOG(info) << "Importing file from cloud storage - Bucket: " << bucketName << ", Key: " << objectKey << ", FileName: " << fileName << endl;

    // Cloud storage configuration (currently S3)
    const string s3_region = "us-west-1";
    string access_key_id, secret_access_key;

    // Use configuration values instead of hardcoded credentials
    DeviceConfig config = GET_CONFIG();
    access_key_id = config.cloud_storage_access_key;
    secret_access_key = config.cloud_storage_secret_key;

    // Validate that credentials are provided
    if (access_key_id.empty() || secret_access_key.empty())
    {
        LOG(error) << "Cloud storage credentials not configured" << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Cloud storage credentials not configured");
        return VmsErrorCode::InvalidParameterError;
    }

    try
    {
        // Get local storage path
        DeviceConfig config = GET_CONFIG();
        string fileLocation = config.nv_streamer_directory_path;
        string localFilePath = getUniqueFilePath(fileName, fileLocation);

        // Download file from cloud storage
        VmsErrorCode downloadResult = downloadFileFromCloud(bucketName, objectKey, localFilePath,
                                                        s3_region, access_key_id, secret_access_key);

        if (downloadResult != VmsErrorCode::NoError)
        {
            LOG(error) << "Failed to download file from cloud storage" << endl;
            SET_VMS_ERROR2(downloadResult, response, "Failed to download file from cloud storage");
            return downloadResult;
        }

        // Get media information
        Json::Value mediaInfo;
        string container, codec, frameRate = "30", duration;
        uint32_t width = 0, height = 0;
        //uint bitrate = 0;
        if (!isFileExist(localFilePath))
        {
            LOG(error) << "File path = " << localFilePath << " not present" << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "File not present");
            return VmsErrorCode::InvalidParameterError;
        }
        if (getMediaInformation(localFilePath, mediaInfo) == 0)
        {
            container = mediaInfo.get("Container", EMPTY_STRING).asString();
            codec = mediaInfo.get("Codec", EMPTY_STRING).asString();
            frameRate = mediaInfo.get("Framerate", "30").asString();
            duration = mediaInfo.get("Duration", "0").asString();
            width = mediaInfo.get("Width", 0).asUInt();
            height = mediaInfo.get("Height", 0).asUInt();
            //bitrate = mediaInfo.get("Bitrate", 0).asInt();
            LOG(info) << "Media Information: " << mediaInfo.toStyledString() << endl;
        }

        // Validate container and codec
        VmsConfigManager* configMngr = VmsConfigManager::getInstance();
        if (configMngr->isVideoContainerSupported(container, localFilePath) == false)
        {
            deleteFile(localFilePath);
            LOG(error) << "Format not supported for file: " << localFilePath << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Format not supported");
            return VmsErrorCode::InvalidParameterError;
        }

        if (configMngr->isVideoFormatSupported(codec) == false)
        {
            deleteFile(localFilePath);
            LOG(error) << "Video encode format not supported: " << codec << " for file: " << localFilePath << endl;
            SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Video encode format not supported");
            return VmsErrorCode::VMSNotSupportedError;
        }

        // Create sensor/stream info
        Json::Value streamData;
        streamData["name"] = streamName;
        streamData["file_path"] = localFilePath;
        streamData["url"] = vst_rtsp::rtspUrlPrefix(fileName) + string(NV_STREAMER) + localFilePath;
        streamData["container"] = container;
        streamData["encoding"] = codec;
        streamData["framerate"] = frameRate;
        streamData["duration"] = duration;
        // Pass width and height separately for proper stream table population
        streamData["width"] = std::to_string(width);
        streamData["height"] = std::to_string(height);
        streamData["resolution"] = std::to_string(width) + "x" + std::to_string(height);
        streamData["FrameCount"] = mediaInfo.get("FrameCount", EMPTY_STRING).asString();
        streamData["AudioEncoding"] = mediaInfo.get("AudioCodec", EMPTY_STRING).asString();
        streamData["SampleRate"] = mediaInfo.get("SampleRate", EMPTY_STRING).asString();
        streamData["BitsPerSample"] = mediaInfo.get("Depth", EMPTY_STRING).asString();
        streamData["Channels"] = mediaInfo.get("Channels", EMPTY_STRING).asString();

        // Add the file to the system as cloud stream
        Json::Value req;
        req["storageLocation"] = static_cast<int>(StreamStorageTypeCloud);
        VmsErrorCode addResult = addFile(m_deviceManager, req, streamData, response);
        // Internal flag from addFile's merge path -- not part of the public API.
        response.removeMember("mergedExisting");
        if (addResult != VmsErrorCode::NoError)
        {
            LOG(error) << "Failed to add imported file to system" << endl;
            deleteFile(localFilePath);
            return addResult;
        }

        LOG(info) << "Successfully imported file from cloud storage: " << fileName << endl;
        response["message"] = "File imported successfully from cloud storage";
        response["fileName"] = fileName;
        response["localPath"] = localFilePath;

        return VmsErrorCode::NoError;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception during cloud storage import: " << e.what() << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Internal error during cloud storage import");
        return VmsErrorCode::VMSInternalError;
    }
}

VmsErrorCode StorageManagement::downloadFileFromCloud(const string& bucketName, const string& objectKey,
                                                   const string& localFilePath, const string& region,
                                                   const string& accessKeyId, const string& secretAccessKey)
{
    try
    {
        LOG(info) << "Downloading from cloud storage using Cloud Reader Library - Bucket: " << bucketName << ", Key: " << objectKey << endl;

        // Configure cloud reader
        nv_vms::CloudReaderConfig config;
        config.storageType = nv_vms::CloudStorageType::AWS_S3;
        config.accessKeyId = accessKeyId;
        config.secretAccessKey = secretAccessKey;
        config.region = region;
        config.useSSL = true;
        config.timeoutSeconds = 30;
        config.maxRetries = 3;

        // Create S3 cloud reader
        auto reader = nv_vms::CloudReaderFactory::createReader(config.storageType, config);
        if (!reader || !reader->isAvailable())
        {
            LOG(error) << "Cloud reader is not available" << endl;
            return VmsErrorCode::VMSInternalError;
        }

        // Download object using cloud reader
        nv_vms::CloudResult result = reader->downloadObject(bucketName, objectKey, localFilePath);
        if (result.success)
        {
            LOG(info) << "Successfully downloaded file from cloud storage via Cloud Reader: " << localFilePath << endl;
            return VmsErrorCode::NoError;
        }

        LOG(warning) << "Cloud Reader download failed: " << result.message << ", trying fallback methods" << endl;

        // Skip signed URL fallback method since signedUrl is not implemented
        LOG(info) << "Skipping signed URL method - not implemented" << endl;

        // Try Python fallback for authenticated download
        LOG(info) << "Trying Python fallback for authenticated download using provided credentials" << endl;
        string pythonScript = "./s3_download_fallback.py";
        if (!isFileExist(pythonScript))
        {
            pythonScript = "/usr/local/bin/s3_download_fallback.py";
        }

        if (isFileExist(pythonScript))
        {
            string pythonCommand = "python3 " + pythonScript + " " + bucketName +
                                 " \"" + objectKey + "\" \"" + localFilePath + "\" \"" +
                                 accessKeyId + "\" \"" + secretAccessKey + "\" \"" + region + "\"";

            int result = system(pythonCommand.c_str());
            if (result != 0)
            {
                LOG(error) << "Python fallback download also failed, returned: " << result << endl;
                return VmsErrorCode::VMSInternalError;
            }
            LOG(info) << "Python fallback download succeeded using provided credentials" << endl;
        }
        else
        {
            LOG(error) << "No fallback method available for cloud storage download" << endl;
            return VmsErrorCode::VMSInternalError;
        }

        // Verify file was downloaded
        if (!isFileExist(localFilePath))
        {
            LOG(error) << "Downloaded file does not exist at: " << localFilePath << endl;
            return VmsErrorCode::VMSInternalError;
        }

        // Check if file has content
        size_t fileSize = getFileSizeInBytes(localFilePath);
        if (fileSize == 0)
        {
            LOG(error) << "Downloaded file is empty: " << localFilePath << endl;
            deleteFile(localFilePath);
            return VmsErrorCode::VMSInternalError;
        }

        LOG(info) << "Successfully downloaded file from cloud storage, size: " << fileSize << " bytes" << endl;
        return VmsErrorCode::NoError;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception during cloud storage download: " << e.what() << endl;
        return VmsErrorCode::VMSInternalError;
    }
}

VmsErrorCode StorageManagement::listCloudFiles(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
{
    const string request_method = req_info.get("method", UNKNOWN_STRING).asString();
    const string query_string = req_info.get("query", EMPTY_STRING).asString();

    if (!(iequals(request_method, "get")))
    {
        LOG(error) << "Request Method is not supported" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
        return VmsErrorCode::VMSNotSupportedError;
    }

    // Parse query parameters
    string bucketName;
    string prefix;
    string useReadWriteCredsStr;

    // Get bucket name from query parameter or configuration
    if (!CivetServer::getParam(query_string, "bucketName", bucketName) || bucketName.empty())
    {
        // If not provided in query, try to get from configuration
        DeviceConfig config = GET_CONFIG();
        bucketName = config.cloud_storage_bucket;

        if (bucketName.empty())
        {
            LOG(error) << "Bucket name not provided and not configured in vst_config.json" << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response,
                          "Bucket name not provided. Specify in query parameter or configure in vst_config.json");
            return VmsErrorCode::InvalidParameterError;
        }

        LOG(info) << "Using bucket name from configuration: " << bucketName << endl;
    }
    else
    {
        LOG(info) << "Using bucket name from query parameter: " << bucketName << endl;
    }

    // Optional parameters
    CivetServer::getParam(query_string, "prefix", prefix);
    CivetServer::getParam(query_string, "useReadWriteCreds", useReadWriteCredsStr);

    bool useReadWriteCreds = iequals(useReadWriteCredsStr, "true");

    LOG(info) << "Listing cloud storage files - Bucket: " << bucketName << ", Prefix: " << prefix << ", UseReadWriteCreds: " << useReadWriteCreds << endl;

    // Cloud storage configuration (currently S3)
    const string s3_region = "us-west-1";
    string access_key_id, secret_access_key;

    // Use configuration values instead of hardcoded credentials
    DeviceConfig config = GET_CONFIG();
    access_key_id = config.cloud_storage_access_key;
    secret_access_key = config.cloud_storage_secret_key;

    // Validate that credentials are provided
    if (access_key_id.empty() || secret_access_key.empty())
    {
        LOG(error) << "Cloud storage credentials not configured" << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Cloud storage credentials not configured");
        return VmsErrorCode::InvalidParameterError;
    }

    try
    {
        VmsErrorCode listResult = listCloudObjects(bucketName, prefix, s3_region,
                                               access_key_id, secret_access_key, response);

        if (listResult != VmsErrorCode::NoError)
        {
            LOG(error) << "Failed to list cloud storage objects" << endl;
            SET_VMS_ERROR2(listResult, response, "Failed to list cloud storage objects");
            return listResult;
        }

        // Filter response to only include MP4 and MKV files
        if (response.isMember("files") && response["files"].isArray())
        {
            Json::Value originalFiles = response["files"];
            Json::Value filteredFiles = Json::arrayValue;
            int skippedCount = 0;
            uint64_t filteredTotalSize = 0;

            for (const auto& file : originalFiles)
            {
                string key = file.get("key", "").asString();

                // Skip empty keys
                if (key.empty())
                {
                    skippedCount++;
                    continue;
                }

                // Skip directories (ending with /)
                if (key.back() == '/')
                {
                    LOG(verbose) << "Skipping directory: " << key << endl;
                    skippedCount++;
                    continue;
                }

                // Get file extension
                string fileExtension;
                size_t dotPos = key.find_last_of('.');
                if (dotPos != string::npos)
                {
                    fileExtension = key.substr(dotPos);
                    // Convert to lowercase for case-insensitive comparison
                    for (char& c : fileExtension) {
                        c = std::tolower(c);
                    }
                }

                // Only include MP4 and MKV files
                if (fileExtension == ".mp4" || fileExtension == ".mkv")
                {
                    filteredFiles.append(file);
                    uint64_t fileSize = strtoull(file.get("size", "0").asString().c_str(), nullptr, 10);
                    filteredTotalSize += fileSize;
                }
                else
                {
                    LOG(verbose) << "Skipping non-video file: " << key << " (extension: " << fileExtension << ")" << endl;
                    skippedCount++;
                }
            }

            // Update response with filtered results
            response["files"] = filteredFiles;
            response["count"] = filteredFiles.size();
            response["totalSize"] = filteredTotalSize;
            response["originalCount"] = originalFiles.size();
            response["skippedCount"] = skippedCount;

            LOG(info) << "Filtered cloud files - Total: " << originalFiles.size()
                      << ", Video files (MP4/MKV): " << filteredFiles.size()
                      << ", Skipped: " << skippedCount << endl;
        }

        LOG(info) << "Successfully listed cloud storage files for bucket: " << bucketName << endl;
        return VmsErrorCode::NoError;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception during cloud storage list: " << e.what() << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Internal error during cloud storage list");
        return VmsErrorCode::VMSInternalError;
    }
}

// Unified storage configuration and management methods
bool StorageManagement::initUnifiedStorageReader()
{
    try
    {
        // Get configuration from DeviceConfig
        const nv_vms::DeviceConfig& config = GET_CONFIG();

        // Use UnifiedStorageReaderUtils to create storage reader
        m_unifiedStorageReader = nv_vms::UnifiedStorageReaderUtils::createStorageReader(config);

        if (!m_unifiedStorageReader)
        {
            LOG(error) << "Failed to create unified storage reader: " << nv_vms::UnifiedStorageReaderUtils::getLastError() << endl;
            return false;
        }

        // Set cloud storage enabled flag based on reader type
        m_cloudStorageEnabled = nv_vms::UnifiedStorageReaderUtils::isCloudStorageEnabled(m_unifiedStorageReader);

        LOG(info) << "Unified storage reader initialized successfully" << endl;
        LOG(info) << "Cloud storage enabled: " << (m_cloudStorageEnabled ? "true" : "false") << endl;

        return true;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception during unified storage initialization: " << e.what() << endl;
        return false;
    }
}

bool StorageManagement::initUnifiedStorageManager()
{
    try
    {
        // Get configuration from DeviceConfig
        const nv_vms::DeviceConfig& config = GET_CONFIG();

        // Initialize unified storage manager for file management
        m_unifiedStorageManager = nv_vms::UnifiedStorageManagerUtils::initializeStorageManager(config);

        if (!m_unifiedStorageManager)
        {
            LOG(error) << "Failed to create unified storage manager: " << nv_vms::UnifiedStorageManagerUtils::getLastError() << endl;
            return false;
        }

        LOG(info) << "Unified storage manager initialized successfully" << endl;
        return true;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception during unified storage manager initialization: " << e.what() << endl;
        return false;
    }
}

bool StorageManagement::isCloudStorageEnabled() const
{
    return m_cloudStorageEnabled;
}

DeleteResult StorageManagement::deleteFileWithStatus(const string& filePath, const string& objectId, bool isCloudFile)
{
    if (!m_unifiedStorageManager)
    {
        LOG(error) << "Unified storage manager not initialized" << endl;
        DeleteResult result(false, "Unified storage manager not initialized");
        result.errorCode = "NOT_INITIALIZED";
        return result;
    }

    try
    {
        string pathToDelete;

        // Use storage location from database to determine deletion method
        if (isCloudFile && !objectId.empty())
        {
            // For cloud files, use object ID
            pathToDelete = objectId;
        }
        else
        {
            // For local files, use file path
            pathToDelete = filePath;
            /* If relative path, then prepend the nv_streamer_directory_path for VSS passthrough usecase */
            pathToDelete = normalizeRelativePath(pathToDelete, GET_CONFIG().nv_streamer_directory_path);
        }

        // Use the unified storage manager utils for better error handling and logging
        nv_vms::DeleteResult result = nv_vms::UnifiedStorageManagerUtils::deleteFile(m_unifiedStorageManager, pathToDelete);

        if (result.success)
        {
            LOG(info) << "Successfully deleted file using unified storage: " << pathToDelete
                      << " (" << result.deletedSize << " bytes) in " << result.duration.count() << "ms" << endl;
        }
        else
        {
            LOG(error) << "Failed to delete file using unified storage: " << pathToDelete
                       << " - " << result.message << " (Error: " << result.errorCode << ")" << endl;
        }

        return result;
    }
    catch (const std::exception& e)
    {
        std::string errorMsg = "Exception during unified storage file deletion: " + std::string(e.what());
        LOG(error) << errorMsg << endl;
        DeleteResult result(false, errorMsg);
        result.errorCode = "EXCEPTION";
        return result;
    }
}

bool StorageManagement::isFileExist(const string& filePath, string objectId)
{
    // Check if unified storage manager is available
    if (!m_unifiedStorageManager)
    {
        LOG(warning) << "Unified storage manager is not initialized, cannot check file existence: " << filePath << endl;
        return false;
    }

    try
    {
        if (m_unifiedStorageManager->isFileExist(filePath))
        {
            LOG(info) << "File exists in storage: " << filePath << endl;
            return true;
        }
        else
        {
            LOG(info) << "File does not exist in storage: " << objectId << endl;
            return false;
        }
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception during unified storage file existence check: " << e.what() << endl;
        return false;
    }
}

VmsErrorCode StorageManagement::listLocalFiles(const Json::Value& req_info, const Json::Value &in, Json::Value &response)
{
    const string request_method = req_info.get("method", UNKNOWN_STRING).asString();
    const string query_string = req_info.get("query", EMPTY_STRING).asString();

    if (!(iequals(request_method, "get")))
    {
        LOG(error) << "Request Method is not supported" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSNotSupportedError, response, "Request Method is not supported");
        return VmsErrorCode::VMSNotSupportedError;
    }

    // Parse query parameters (both are optional)
    string offsetStr;
    string limitStr;

    CivetServer::getParam(query_string, "offset", offsetStr);
    CivetServer::getParam(query_string, "limit", limitStr);

    int offset = 0;  // Default offset
    int limit = 0;   // Default: no limit (return all)

    // Parse offset if provided
    if (!offsetStr.empty())
    {
        try
        {
            offset = std::stoi(offsetStr);
        }
        catch (const std::exception& e)
        {
            LOG(error) << "Invalid offset parameter: " << offsetStr << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Invalid offset parameter");
            return VmsErrorCode::InvalidParameterError;
        }
    }

    // Parse limit if provided
    if (!limitStr.empty())
    {
        try
        {
            limit = std::stoi(limitStr);
        }
        catch (const std::exception& e)
        {
            LOG(error) << "Invalid limit parameter: " << limitStr << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Invalid limit parameter");
            return VmsErrorCode::InvalidParameterError;
        }
    }

    // Validate parameters
    if (offset < 0)
    {
        LOG(error) << "Invalid offset parameter: " << offset << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Offset must be non-negative");
        return VmsErrorCode::InvalidParameterError;
    }

    if (limit < 0)
    {
        LOG(error) << "Invalid limit parameter: " << limit << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Limit must be non-negative");
        return VmsErrorCode::InvalidParameterError;
    }

    LOG(info) << "Listing local storage files - offset: " << offset << ", limit: " << limit << endl;

    try
    {
        // Get all files from database
        vector<VideoRecordDBColumns> fileRecords = GET_DB_INSTANCE()->getAllVideoRecordFilePaths();

        // First, build the complete response with all sensors and streams
        Json::Value allFiles(Json::objectValue);

        for (const auto& record : fileRecords)
        {
            Json::Value fileInfo;
            fileInfo["mediaFilePath"] = record.filepath_value;
            fileInfo["metadataFilePath"] = record.metadata_file_path_value;

            // Parse and include metadata if available
            if (!record.metadata_json_value.empty())
            {
                try
                {
                    Json::CharReaderBuilder builder;
                    Json::CharReader* reader = builder.newCharReader();
                    Json::Value metadata;
                    string errors;

                    bool parsingSuccessful = reader->parse(
                        record.metadata_json_value.c_str(),
                        record.metadata_json_value.c_str() + record.metadata_json_value.length(),
                        &metadata,
                        &errors
                    );
                    delete reader;

                    if (parsingSuccessful && !metadata.isNull())
                    {
                        metadata["id"] = record.object_id_value;
                        fileInfo["metadata"] = metadata;
                    }
                    else
                    {
                        LOG(warning) << "Failed to parse metadata JSON for file: " << record.filepath_value << ", errors: " << errors << endl;
                        Json::Value fallbackMetadata;
                        fallbackMetadata["id"] = record.object_id_value;
                        fileInfo["metadata"] = fallbackMetadata;
                    }
                }
                catch (const std::exception& e)
                {
                    LOG(error) << "Exception parsing metadata JSON for file: " << record.filepath_value << ", error: " << e.what() << endl;
                    Json::Value fallbackMetadata;
                    fallbackMetadata["id"] = record.object_id_value;
                    fileInfo["metadata"] = fallbackMetadata;
                }
            }
            else
            {
                Json::Value fallbackMetadata;
                fallbackMetadata["id"] = record.object_id_value;
                fileInfo["metadata"] = fallbackMetadata;
            }

            // If sensor ID doesn't exist in files object, create an empty array
            if (!allFiles.isMember(record.sensor_id_value))
            {
                allFiles[record.sensor_id_value] = Json::arrayValue;
            }

            // Append this file to the sensor's array
            allFiles[record.sensor_id_value].append(fileInfo);
        }

        // Now apply post-processing for pagination
        if (offset > 0 || limit > 0)
        {
            // Create a flat list of all streams with their sensor IDs for pagination
            vector<pair<string, Json::Value>> allStreams;

            // Extract all streams from all sensors
            for (auto it = allFiles.begin(); it != allFiles.end(); ++it)
            {
                const string& sensorId = it.key().asString();
                const Json::Value& streams = *it;

                for (Json::ArrayIndex i = 0; i < streams.size(); i++)
                {
                    allStreams.push_back(make_pair(sensorId, streams[i]));
                }
            }

            // Keep streams in their original database order

            // Apply offset and limit to the sorted streams
            size_t totalStreams = allStreams.size();
            size_t startIndex = static_cast<size_t>(offset);
            size_t endIndex = (limit > 0) ? std::min(startIndex + static_cast<size_t>(limit), totalStreams) : totalStreams;

            // Build paginated response
            Json::Value paginatedFiles(Json::objectValue);

            if (startIndex < totalStreams)
            {
                for (size_t i = startIndex; i < endIndex; i++)
                {
                    const string& sensorId = allStreams[i].first;
                    const Json::Value& stream = allStreams[i].second;

                    // Initialize array for this sensor if needed
                    if (!paginatedFiles.isMember(sensorId))
                    {
                        paginatedFiles[sensorId] = Json::arrayValue;
                    }

                    // Add stream to sensor's array
                    paginatedFiles[sensorId].append(stream);
                }
            }

            response = paginatedFiles;
            LOG(info) << "Successfully listed " << paginatedFiles.size() << " sensors and " << (endIndex - startIndex) << " streams (from total " << totalStreams << " streams)" << endl;
        }
        else
        {
            // No pagination requested, return all data
            response = allFiles;

            // Count total streams for logging
            size_t totalStreams = 0;
            for (auto it = allFiles.begin(); it != allFiles.end(); ++it)
            {
                totalStreams += it->size();
            }

            LOG(info) << "Successfully listed " << allFiles.size() << " sensors and " << totalStreams << " streams" << endl;
        }

        return VmsErrorCode::NoError;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception during local storage list: " << e.what() << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Internal error during local storage list");
        return VmsErrorCode::VMSInternalError;
    }
}

VmsErrorCode StorageManagement::getRecordTimelines(const string streamId, const string startTime,
                                                const string endTime, Json::Value &response)
{
    return vst_common::getRecordTimelines(streamId, startTime, endTime, response);
}

VmsErrorCode StorageManagement::GetAllRecordTimelines(const Json::Value& req_info, Json::Value &out)
{
    return vst_common::GetAllRecordTimelines(req_info, out);
}

// ---------------------------------------------------------------------------
// Full-file fast path
// ---------------------------------------------------------------------------
// When a user requests a time range that exactly covers a stored recording
// (or passes fullFile=true explicitly), we can avoid the expensive makeVideoFile
// pipeline (demux / remux / mux / transcode) and instead:
//   - download API  : stream the raw file directly via mg_send_mime_file2
//   - url API       : expose the file via a symlink under temp_files/ and
//                     register it in the TEMP_VIDEO_FILES DB like any other
//                     generated temp file.
// Guardrails ensure this only engages when the output would have been byte
// identical to the raw file (no transcode, no overlay, no audio stripping,
// same container).

StorageManagement::FullFileMatch
StorageManagement::tryFindFullFileMatch(const std::string& sensorId,
                                        const std::string& id,
                                        int64_t startMs,
                                        int64_t endMs,
                                        const std::string& requestedContainer,
                                        const std::string& transcode,
                                        const std::string& enableOverlay,
                                        const std::string& uselibav,
                                        const std::string& disableAudio,
                                        const std::string& frameRate,
                                        bool  fullFileFlag)
{
    FullFileMatch m;

    // Any form of processing disqualifies the fast path.
    if (transcode == "full" || enableOverlay == "true")
    {
        return m;
    }

    auto dbHelper = GET_DB_INSTANCE();
    if (!dbHelper)
    {
        LOG(warning) << "[FULL_FILE] DB helper unavailable" << endl;
        return m;
    }

    // Resolve matching record(s):
    //   - id lookup is unambiguous,
    //   - sensorId + time range must yield exactly one recording,
    //   - sensorId + fullFile=true with no time bounds: list all recordings
    //     for the sensor and require exactly one (typical for file-based
    //     sensors, which have a single uploaded recording per sensor).
    std::vector<VideoRecordDBColumns> records;
    if (!id.empty())
    {
        records = dbHelper->getVideoRecordFilePathsIdBased(id);
    }
    else if (!sensorId.empty() && startMs > 0 && endMs > 0)
    {
        records = dbHelper->getVideoRecordFilePathsSensorIdBased(sensorId, startMs, endMs);
    }
    else if (!sensorId.empty() && fullFileFlag)
    {
        // fullFile=true without a time range: enumerate everything for this
        // sensor. The records.size() == 1 check below ensures we only engage
        // when there is no ambiguity about which file to return.
        records = dbHelper->getVideoRecordFilePathsSensorIdBased(
            sensorId, 0, std::numeric_limits<int64_t>::max());
    }
    else
    {
        return m;
    }

    if (records.size() != 1)
    {
        return m;
    }
    const auto& r = records[0];

    // Cloud-stored recordings already have their own presigned-URL fast path;
    // do not interfere.
    if (r.storage_location_value == StreamStorageTypeCloud)
    {
        return m;
    }

    if (r.filepath_value.empty() || !::isFileExist(r.filepath_value))
    {
        LOG(warning) << "[FULL_FILE] Candidate file missing on disk: " << r.filepath_value << endl;
        return m;
    }

    // Still-open recordings have duration set to FILE_INIT_DURATION; exclude
    // those to avoid racing with the recorder writing the file.
    if (r.duration_value <= FILE_INIT_DURATION)
    {
        return m;
    }

    // Container must align with the stored file's extension, otherwise the
    // user is asking for a format conversion (cannot be served raw).
    std::string storedExt = getFileExtension(r.filepath_value); // e.g. ".mp4"
    std::string storedContainer = storedExt.empty()
        ? std::string("mp4")
        : storedExt.substr(1);
    if (!requestedContainer.empty() && !iequals(requestedContainer, storedContainer))
    {
        return m;
    }

    // Eligibility: either the caller set fullFile=true explicitly, or the
    // requested [startMs, endMs] covers the recording's [start, start+duration]
    // within one frame interval. Deriving the tolerance from the stream's own
    // frame rate keeps it tight enough to reject explicit sub-range requests
    // (e.g. [.400, end-100ms]) while still absorbing sub-frame rounding noise
    // between client clocks and stored timestamps. When fps is unknown /
    // unparsable we fall back to the cache-lookup tolerance so behaviour
    // stays consistent with other temp-file matching.
    int64_t fpsValue = 0;
    if (!frameRate.empty())
    {
        try { fpsValue = static_cast<int64_t>(std::stod(frameRate)); }
        catch (...) { fpsValue = 0; }
    }
    const int64_t kRangeToleranceMs = (fpsValue > 0)
        ? (1000 / fpsValue) // one frame interval, e.g. 30 fps -> 33 ms
        : nv_vms::TempFilesDBColumns::CACHE_TIME_TOLERANCE_MS;
    bool rangeCoversFile = false;
    if (!id.empty())
    {
        // id-based requests point at a single file and imply "give me this file".
        rangeCoversFile = true;
    }
    else if (startMs <= 0 || endMs <= 0)
    {
        // No time bounds at all => caller must have passed fullFile=true to
        // identify "the file" for this sensor; treat as whole-file request.
        rangeCoversFile = fullFileFlag;
    }
    else
    {
        const int64_t recStart = static_cast<int64_t>(r.start_time_value);
        const int64_t recEnd   = recStart + static_cast<int64_t>(r.duration_value);
        rangeCoversFile = (startMs <= recStart + kRangeToleranceMs) &&
                          (endMs   >= recEnd   - kRangeToleranceMs);
    }

    if (!fullFileFlag && !rangeCoversFile)
    {
        return m;
    }

    m.eligible    = true;
    m.filePath    = r.filepath_value;
    m.objectId    = r.object_id_value;
    m.startTimeMs = r.start_time_value;
    m.durationMs  = r.duration_value;
    m.container   = storedContainer;
    LOG(info) << "[FULL_FILE] Match: path=" << m.filePath
              << " start=" << m.startTimeMs
              << " duration=" << m.durationMs
              << " container=" << m.container << endl;
    return m;
}

VmsErrorCode
StorageManagement::serveFullFileDownload(const FullFileMatch& match,
                                         const std::string& downloadFileName,
                                         struct mg_connection* conn,
                                         Json::Value& response)
{
    if (!match.eligible || match.filePath.empty())
    {
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Full file match is not eligible");
        return VmsErrorCode::VMSInternalError;
    }
    if (conn == nullptr)
    {
        LOG(error) << "[FULL_FILE] Null connection, cannot serve download" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Invalid connection");
        return VmsErrorCode::VMSInternalError;
    }

    // Protect the raw recording from the aging job while it is being served.
    // Note: addFileInProtectList takes non-const string&; copy to a local.
    std::string protectPath = match.filePath;
    addFileInProtectList(protectPath, true);

    std::string outName = downloadFileName;
    if (outName.empty())
    {
        outName = getFileName(match.filePath) + getFileExtension(match.filePath);
    }
    std::string headers = FILE_DOWNLOAD_HEADER(outName);

    LOG(info) << "[FULL_FILE] Serving raw file directly: " << match.filePath
              << " as " << outName << endl;
    mg_send_mime_file2(conn, match.filePath.c_str(), nullptr, headers.c_str());

    // Release protection.
    addFileInProtectList(protectPath, false);

    return VmsErrorCode::NoError;
}

VmsErrorCode
StorageManagement::generateFullFileUrl(const FullFileMatch& match,
                                       const VideoGenerationParam& params,
                                       Json::Value& response)
{
    if (!match.eligible || match.filePath.empty())
    {
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Full file match is not eligible");
        return VmsErrorCode::VMSInternalError;
    }

    // Parse expiry from the original query string (same contract as other
    // URL-generating paths).
    std::string expiryMinutesStr;
    CivetServer::getParam(params.queryString, "expiryMinutes", expiryMinutesStr);
    int expiryMinutesInt = stringToInt(expiryMinutesStr, GET_CONFIG().default_file_expiry_minutes);
    if (expiryMinutesInt < 1)
    {
        LOG(error) << "[FULL_FILE] Invalid expiry minutes: " << expiryMinutesInt << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, "Invalid expiry minutes value");
        return VmsErrorCode::InvalidParameterError;
    }

    auto dbHelper = GET_DB_INSTANCE();
    if (!dbHelper)
    {
        LOG(error) << "[FULL_FILE] DB helper unavailable for URL generation" << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Database unavailable");
        return VmsErrorCode::VMSInternalError;
    }

    const std::string webRoot = VmsConfigManager::getInstance()->getWebRootPath();
    const std::string baseUrl = getIngressBaseUrl();
    const std::string extension = "." + match.container;

    // -----------------------------------------------------------------
    // Cache reuse: if a previous /url call for the same recording already
    // produced a link in temp_files/ that is still on disk, reuse it
    // instead of creating a new symlink + DB row each time.
    // We look up by the FILE's actual [start, start+duration] (matching
    // what we wrote in the DB) so callers passing slightly different
    // requested ranges still hit the same cached entry.
    // -----------------------------------------------------------------
    {
        const int64_t fileStartMs = static_cast<int64_t>(match.startTimeMs);
        const int64_t fileEndMs   = fileStartMs + static_cast<int64_t>(match.durationMs);
        auto existing = dbHelper->findTempFileByStreamAndTime(
            m_deviceId, params.streamId, fileStartMs, fileEndMs,
            nv_vms::TempFilesDBColumns::FILE_TYPE_VIDEO, match.container);

        if (!existing.file_path_value.empty() && ::isFileExist(existing.file_path_value))
        {
            const std::string cachedFilename =
                std::filesystem::path(existing.file_path_value).filename().string();

            // Refresh expiry to "now + expiryMinutes" so frequent clients
            // keep the cached link alive instead of letting it age out.
            auto now = std::chrono::system_clock::now();
            const int64_t expiryTsMs = std::chrono::duration_cast<std::chrono::milliseconds>(
                (now + std::chrono::minutes(expiryMinutesInt)).time_since_epoch()).count();
            dbHelper->updateTempFileExpiry(existing.file_path_value, expiryTsMs);

            if (m_videoCleanupScheduler)
            {
                const std::string cachedTaskId = extractTaskId(cachedFilename);
                const int64_t durationMs =
                    static_cast<int64_t>(expiryMinutesInt) * 60 * 1000;
                m_videoCleanupScheduler->schedule(
                    cachedTaskId, durationMs, existing.file_path_value);
            }

            const std::string videoUrlCached =
                baseUrl + TEMP_STORAGE_PATH + "/" + cachedFilename;
            const std::string expiryISO = convertEpocToISO8601_2(expiryTsMs * 1000);
            response["absolutePath"]     = getAbsolutePath(webRoot) + TEMP_STORAGE_DIR + "/" + cachedFilename;
            response["videoUrl"]         = videoUrlCached;
            response["expiryISO"]        = expiryISO;
            response["expiryMinutes"]    = expiryMinutesInt;
            response["streamId"]         = params.streamId;
            response["type"]             = "replay";
            response["fullFile"]         = true;
            response["startTime"]        = convertEpocToISO8601_2(fileStartMs * 1000);
            response["startTimeEpochMs"] = static_cast<Json::Int64>(fileStartMs);

            LOG(info) << "[FULL_FILE] Reusing cached temp link: " << existing.file_path_value
                      << " -> " << match.filePath
                      << " (refreshed expiry to " << expiryMinutesInt << "min)" << endl;
            return VmsErrorCode::NoError;
        }
    }

    // Build the task id the same way setupVideoUrlGenerationContext does, so
    // the public URL format stays consistent.
    std::shared_ptr<SensorInfo> sensor =
        dbHelper->searchSensorAndGetSensorInfo(params.streamId, m_deviceManager->getDeviceId());
    std::string sensorPrefix;
    if (sensor && !sensor->name.empty() && sensor->name != "disconnected_device")
    {
        const std::string candidate = sanitizePrefix(sensor->name);
        if (!candidate.empty())
        {
            sensorPrefix = candidate + "_";
        }
    }
    const std::string taskId =
        (sensorPrefix.empty() ? VideoGeneratorTaskManager::VIDEO_TASK_PREFIX : "") +
        sensorPrefix +
        getUniqueIdFromUTCTime(params.startTime, "");

    const std::string linkPath   = webRoot + TEMP_STORAGE_DIR + "/" + taskId + extension;
    const std::string publicPath = TEMP_STORAGE_PATH + std::string("/") + taskId + extension;
    const std::string videoUrl   = baseUrl + publicPath;

    // If something already exists at the link path (stale, same taskId
    // collision), remove it first so the link creation does not fail.
    std::error_code rmEc;
    std::filesystem::remove(linkPath, rmEc);
    (void)rmEc;

    // Expose the source recording via a symlink in temp_files/. The
    // temp URL is intended to live and die with the source recording:
    // when the source is explicitly deleted (DELETE API) or aged out,
    // deleteTempLinksForGivenFile() removes this symlink as part of
    // the same operation, so the URL stops working in lockstep with
    // the source.
    std::error_code linkEc;
    std::filesystem::create_symlink(match.filePath, linkPath, linkEc);
    if (linkEc)
    {
        LOG(error) << "[FULL_FILE] Unable to expose raw file at " << linkPath
                   << ": " << linkEc.message() << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Unable to expose full file");
        return VmsErrorCode::VMSInternalError;
    }

    // Register the exposed link in TEMP_VIDEO_FILES so it is tracked,
    // cache-reusable, and eligible for scheduled cleanup. We store the link
    // path (not the source), so expiry cleanup removes only the link
    // (via deleteTempFileRecord + unlink on the link inode).
    int64_t fileSize = 0;
    try
    {
        fileSize = static_cast<int64_t>(getFileSizeInBytes(match.filePath));
    }
    catch (...)
    {
        fileSize = 0;
    }

    auto now = std::chrono::system_clock::now();
    const int64_t createdTsMs = std::chrono::duration_cast<std::chrono::milliseconds>(
                                    now.time_since_epoch()).count();
    const int64_t expiryTsMs = std::chrono::duration_cast<std::chrono::milliseconds>(
                                   (now + std::chrono::minutes(expiryMinutesInt)).time_since_epoch()).count();

    try
    {
        nv_vms::TempFilesDBColumns rec;
        rec.device_id_value         = m_deviceId;
        rec.file_path_value         = linkPath;
        rec.created_timestamp_value = createdTsMs;
        rec.expiry_timestamp_value  = expiryTsMs;
        rec.stream_id_value         = params.streamId;
        rec.file_size_value         = fileSize;
        rec.start_time_ms_value     = static_cast<int64_t>(match.startTimeMs);
        rec.end_time_ms_value       = static_cast<int64_t>(match.startTimeMs) +
                                      static_cast<int64_t>(match.durationMs);
        rec.file_type_value         = nv_vms::TempFilesDBColumns::FILE_TYPE_VIDEO;
        rec.container_format_value  = match.container;

        int ins = dbHelper->insertTempFileRecord(rec);
        if (ins != 0)
        {
            LOG(warning) << "[FULL_FILE] Failed to record temp file in DB: " << linkPath << endl;
        }
    }
    catch (const std::exception& e)
    {
        LOG(warning) << "[FULL_FILE] Exception recording temp file in DB: " << e.what() << endl;
    }

    // Schedule expiry cleanup via the existing video scheduler.
    if (m_videoCleanupScheduler)
    {
        const int64_t durationMs = static_cast<int64_t>(expiryMinutesInt) * 60 * 1000;
        m_videoCleanupScheduler->schedule(taskId, durationMs, linkPath);
    }
    else
    {
        LOG(warning) << "[FULL_FILE] video cleanup scheduler not initialized; link will not auto-expire" << endl;
    }

    // Build response with the same shape as buildVideoUrlResponse(), plus a
    // hint flag so clients can tell this was the fast path.
    const std::string expiryISO = convertEpocToISO8601_2(expiryTsMs * 1000);
    response["absolutePath"]     = getAbsolutePath(webRoot) + TEMP_STORAGE_DIR + "/" + taskId + extension;
    response["videoUrl"]         = videoUrl;
    response["expiryISO"]        = expiryISO;
    response["expiryMinutes"]    = expiryMinutesInt;
    response["streamId"]         = params.streamId;
    response["type"]             = "replay";
    response["fullFile"]         = true;
    response["startTime"]        = convertEpocToISO8601_2(static_cast<int64_t>(match.startTimeMs) * 1000);
    response["startTimeEpochMs"] = static_cast<Json::Int64>(match.startTimeMs);

    LOG(info) << "[FULL_FILE] URL generated: " << videoUrl
              << " -> " << match.filePath
              << " expiry=" << expiryMinutesInt << "min" << endl;
    return VmsErrorCode::NoError;
}

void StorageManagement::cleanupExpiredFile(const std::string& filePath)
{
    // Try to delete the file
    if (deleteFile(filePath))
    {
        LOG(info) << "Successfully deleted expired temp file: " << filePath << endl;

        // Delete from database
        auto dbHelper = GET_DB_INSTANCE();
        if (dbHelper)
        {
            dbHelper->deleteTempFileRecord(filePath);
        }
    }
    else
    {
        // Check if file doesn't exist
        if (!::isFileExist(filePath))
        {
            LOG(info) << "Temp file already deleted, removing from database: " << filePath << endl;
            auto dbHelper = GET_DB_INSTANCE();
            if (dbHelper)
            {
                dbHelper->deleteTempFileRecord(filePath);
            }
        }
        else
        {
            LOG(error) << "Failed to delete temp file: " << filePath << endl;
        }
    }
}

void StorageManagement::cleanupExpiredAsyncTask(const std::string& taskId, const std::string& filePath)
{
    auto* taskManager = VideoGeneratorTaskManager::getInstance();
    if (taskManager)
    {
        // First check if task is still running - if so, wait for it to complete
        VideoTaskStatus status = taskManager->getTaskStatus(taskId);
        if (status == VideoTaskStatus::IN_PROGRESS || status == VideoTaskStatus::PENDING)
        {
            LOG(info) << "Task " << taskId << " is still running during cleanup, waiting for completion..." << endl;

            // Wait for the task to complete so we can clean up the actual generated file
            string outputFilePath;
            VmsErrorCode result = taskManager->waitForTask(taskId, outputFilePath);

            if (result == VmsErrorCode::NoError)
            {
                LOG(info) << "Task " << taskId << " completed during cleanup, will clean up file: " << outputFilePath << endl;

                // Delete the actually generated file
                if (::isFileExist(outputFilePath))
                {
                    if (deleteFile(outputFilePath))
                    {
                        LOG(info) << "Deleted expired video file for completed task " << taskId << ": " << outputFilePath << endl;

                        // Remove the temp file DB record
                        auto dbHelper = GET_DB_INSTANCE();
                        if (dbHelper)
                        {
                            dbHelper->deleteTempFileRecord(outputFilePath);
                        }
                    }
                    else
                    {
                        LOG(error) << "Failed to delete expired video file: " << outputFilePath << endl;
                    }
                }
                else
                {
                    LOG(warning) << "Generated file not found after task completion: " << outputFilePath << endl;
                    // Still remove DB record to keep it clean
                    auto dbHelper = GET_DB_INSTANCE();
                    if (dbHelper)
                    {
                        dbHelper->deleteTempFileRecord(outputFilePath);
                    }
                }
            }
            else
            {
                LOG(error) << "Task " << taskId << " failed during cleanup, removing DB record for: " << filePath << endl;
                // Task failed, just clean up DB record
                auto dbHelper = GET_DB_INSTANCE();
                if (dbHelper)
                {
                    dbHelper->deleteTempFileRecord(filePath);
                }
            }
        }
        else
        {
            // Task is not running (completed, failed, or not found), proceed with normal cleanup
            LOG(info) << "Task " << taskId << " is not running, proceeding with normal file cleanup" << endl;

            if (!filePath.empty())
            {
                if (::isFileExist(filePath))
                {
                    if (deleteFile(filePath))
                    {
                        LOG(info) << "Deleted expired video file for task " << taskId << ": " << filePath << endl;

                        // Remove the temp file DB record
                        auto dbHelper = GET_DB_INSTANCE();
                        if (dbHelper)
                        {
                            dbHelper->deleteTempFileRecord(filePath);
                        }
                    }
                    else
                    {
                        LOG(error) << "Failed to delete expired video file: " << filePath << endl;
                    }
                }
                else
                {
                    LOG(warning) << "File not found on disk for task " << taskId << ": " << filePath << endl;
                    // File doesn't exist, but remove DB record to keep it clean
                    auto dbHelper = GET_DB_INSTANCE();
                    if (dbHelper)
                    {
                        dbHelper->deleteTempFileRecord(filePath);
                    }
                }
            }
        }
    }
    else
    {
        LOG(error) << "VideoGeneratorTaskManager instance not available" << endl;
    }
}

void StorageManagement::notifyFileSensorEvents()
{
    if (!m_deviceManager)
    {
        LOG(warning) << "Device manager not available for sending events" << endl;
        return;
    }

    try
    {
        // Get all sensors from device manager with DB fetch
        vector<shared_ptr<SensorInfo>> sensors = m_deviceManager->getSensorList(true);

        for (const auto& sensor : sensors)
        {
            if (!sensor || sensor->type != SENSOR_TYPE_FILE)
            {
                continue; // Skip non-file sensors
            }

            // Check if sensor has streams and is online
            if (sensor->streams.empty() || sensor->getSensorStatus() != SensorStatusOnline)
            {
                LOG(info) << "Sensor has no streams or is not online: " << sensor->name << " (ID: " << sensor->id << ")" << endl;
                continue;
            }

            // Get the first stream's URL (file path)
            shared_ptr<StreamInfo> stream = sensor->streams[0];
            if (!stream || stream->live_proxy_url.empty())
            {
                LOG(info) << "Sensor has no streams or is not streaming: " << sensor->name << " (ID: " << sensor->id << ")" << endl;
                continue;
            }

            // Send camera_add event
            SensorStatus status;
            status.timeStamp = getCurrentTime();
            status.sensorId = sensor->id;
            status.sensorName = sensor->name;
            status.serverId = m_deviceManager->getDeviceId();
            status.event = SensorStatusOnline;
            status.type = TYPE_VST;

            if (sensor->m_notify == true)
            {
                LOG(info) << "Sending camera_add event for file sensor: " << sensor->name
                         << " (ID: " << sensor->id << ")" << endl;
                vst_common::notifyEvent(status, "");
            }

            // Send camera_proxy event so SDR can assign the stream to a replica
            LOG(info) << "Sending camera_proxy event for file sensor: " << sensor->name
                     << " (ID: " << sensor->id << ")" << endl;
            vst_common::notifySensorStatusEvent(SensorStatusProxy, sensor);

            // Send camera_streaming event for file-based sensors
            LOG(info) << "Sending camera_streaming event for file sensor: " << sensor->name
                     << " (ID: " << sensor->id << ")" << endl;
            vst_common::notifySensorStatusEvent(SensorStatusStreaming, sensor);
        }

        LOG(info) << "Sent events for file-based sensors" << endl;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception while sending events for file sensors: " << e.what() << endl;
    }
}

void StorageManagement::scanAndImportCloudFilesBackground()
{
    try
    {
        scanAndImportCloudFiles();
        LOG(info) << "Cloud storage scanning thread completed" << endl;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception in cloud storage scanning thread: " << e.what() << endl;
    }
}

void StorageManagement::scanAndImportCloudFiles()
{
    if (m_cloudScanInProgress.load())
    {
        LOG(warning) << "Cloud storage scan already in progress, skipping" << endl;
        return;
    }

    // Check if we should stop before starting
    if (m_cloudScanShouldStop.load())
    {
        LOG(info) << "Cloud storage scan cancelled before start" << endl;
        return;
    }

    m_cloudScanInProgress.store(true);

    try
    {
        DeviceConfig config = GET_CONFIG();

        // Validate configuration
        if (!config.enable_cloud_storage)
        {
            LOG(info) << "Cloud storage not enabled in configuration, skipping scan" << endl;
            m_cloudScanInProgress.store(false);
            return;
        }

        string bucketName = config.cloud_storage_bucket;
        string region = config.cloud_storage_region;
        string accessKeyId = config.cloud_storage_access_key;
        string secretAccessKey = config.cloud_storage_secret_key;

        if (bucketName.empty() || accessKeyId.empty() || secretAccessKey.empty())
        {
            LOG(error) << "Cloud storage configuration incomplete, skipping scan" << endl;
            m_cloudScanInProgress.store(false);
            return;
        }

        LOG(info) << "======================================" << endl;
        LOG(info) << "Starting Cloud Storage Auto-Import" << endl;
        LOG(info) << "Bucket: " << bucketName << endl;
        LOG(info) << "Region: " << region << endl;
        LOG(info) << "======================================" << endl;

        // Step 1: List all cloud files using listAllCloudObjects for complete scan
        Json::Value listResponse;
        nv_vms::CloudListResult result;

        {
            if (!m_unifiedStorageReader || !m_cloudStorageEnabled)
            {
                LOG(error) << "Unified cloud storage reader not available for scan" << endl;
                m_cloudScanInProgress.store(false);
                return;
            }

            LOG(info) << "Using listAllCloudObjects to fetch all files from bucket (no pagination limit)" << endl;
            result = m_unifiedStorageReader->listAllCloudObjects(bucketName, "");
        }

        if (!result.success)
        {
            LOG(error) << "Failed to list all cloud objects during scan: " << result.message << endl;
            m_cloudScanInProgress.store(false);
            return;
        }

        // Convert result to JSON response format
        listResponse = convertCloudListResultToJson(result);
        LOG(info) << "Successfully retrieved " << result.count << " objects from cloud storage in "
                  << result.duration.count() << "ms" << endl;

        // Check if we should stop after listing
        if (m_cloudScanShouldStop.load())
        {
            LOG(info) << "Cloud storage scan cancelled after listing files" << endl;
            m_cloudScanInProgress.store(false);
            return;
        }

        // Check if we have files
        if (!listResponse.isMember("files") || !listResponse["files"].isArray())
        {
            LOG(warning) << "No files found in cloud storage" << endl;
            m_cloudScanInProgress.store(false);
            return;
        }

        Json::Value filesArray = listResponse["files"];
        int totalFiles = filesArray.size();

        // Filter to only MP4 and MKV files
        Json::Value filteredFiles = Json::arrayValue;
        for (const auto& file : filesArray)
        {
            string key = file.get("key", "").asString();

            if (key.empty() || key.back() == '/')
            {
                continue; // Skip directories
            }

            // Get file extension
            string fileExtension;
            size_t dotPos = key.find_last_of('.');
            if (dotPos != string::npos)
            {
                fileExtension = key.substr(dotPos);
                for (char& c : fileExtension) {
                    c = std::tolower(c);
                }
            }

            // Only include MP4 and MKV files
            if (fileExtension == ".mp4" || fileExtension == ".mkv")
            {
                filteredFiles.append(file);
            }
        }

        int videoFileCount = filteredFiles.size();
        LOG(info) << "Found " << totalFiles << " total objects, " << videoFileCount << " video files (MP4/MKV)" << endl;

        if (videoFileCount == 0)
        {
            LOG(info) << "No video files to import from cloud storage" << endl;
            m_cloudScanInProgress.store(false);
            return;
        }

        // Step 2: Process each video file
        int importedCount = 0;
        int skippedCount = 0;
        int failedCount = 0;
        VmsConfigManager* configMngr = VmsConfigManager::getInstance();

        for (int i = 0; i < videoFileCount; i++)
        {
            // Check if we should stop processing
            if (m_cloudScanShouldStop.load())
            {
                LOG(info) << "Cloud storage scan interrupted by shutdown signal after processing "
                          << (i) << "/" << videoFileCount << " files" << endl;
                break;
            }

            const Json::Value& file = filteredFiles[i];
            string objectKey = file.get("key", "").asString();

            if (objectKey.empty())
            {
                skippedCount++;
                continue;
            }

            LOG(info) << "[" << (i+1) << "/" << videoFileCount << "] Processing: " << objectKey << endl;

            // Step 2a: Generate presigned URL
            std::string presignedUrl;
            nv_vms::FileResult urlResult;

            {
                if (!m_unifiedStorageReader)
                {
                    LOG(error) << "Unified storage reader not available" << endl;
                    failedCount++;
                    continue;
                }

                // Get presigned URL (uses caching internally)
                urlResult = m_unifiedStorageReader->getPresignedUrl(objectKey, presignedUrl);
            }

            if (!urlResult.success || presignedUrl.empty())
            {
                LOG(error) << "Failed to generate presigned URL for " << objectKey << ": " << urlResult.message << endl;
                failedCount++;
                continue;
            }

            // Step 2b: Get media information
            Json::Value mediaInfo;
            int mediaInfoResult = getMediaInformation(presignedUrl, mediaInfo, false);

            if (mediaInfoResult != 0)
            {
                LOG(error) << "Failed to get media information for " << objectKey << endl;
                failedCount++;
                continue;
            }

            // Extract media properties
            string container = mediaInfo.get("Container", EMPTY_STRING).asString();
            string codec = mediaInfo.get("Codec", EMPTY_STRING).asString();
            string frameRate = mediaInfo.get("Framerate", "30").asString();
            string duration = mediaInfo.get("Duration", "0").asString();
            uint32_t width = mediaInfo.get("Width", 0).asUInt();
            uint32_t height = mediaInfo.get("Height", 0).asUInt();

            // Check for audio metadata
            string audioCodec = mediaInfo.get("AudioCodec", EMPTY_STRING).asString();
            string sampleRate = mediaInfo.get("SampleRate", "0").asString();
            string channels = mediaInfo.get("Channels", "0").asString();
            bool hasAudio = !audioCodec.empty() && audioCodec != "unknown";

            LOG(info) << "  Media Info - Container: " << container << ", Codec: " << codec
                      << ", Resolution: " << width << "x" << height
                      << ", Duration: " << duration << "s, FPS: " << frameRate
                      << ", Audio: " << (hasAudio ? ("Yes (" + audioCodec + ", " + sampleRate + "Hz, " + channels + " channels)") : "No") << endl;

            // Step 2c: Validate container and codec support
            if (!configMngr->isVideoContainerSupported(container, objectKey))
            {
                LOG(warning) << "Container format not supported: " << container << ", skipping " << objectKey << endl;
                skippedCount++;
                continue;
            }

            if (!configMngr->isVideoFormatSupported(codec))
            {
                LOG(warning) << "Video codec not supported: " << codec << ", skipping " << objectKey << endl;
                skippedCount++;
                continue;
            }

            // Step 2d: Check if file already exists in database by objectKey
            auto dbHelper = GET_DB_INSTANCE();
            std::vector<VideoRecordDBColumns> existingRecords = dbHelper->getAllVideoRecordFilePaths();
            bool alreadyExists = false;

            for (const auto& record : existingRecords)
            {
                if (record.filepath_value == objectKey || record.object_id_value == objectKey)
                {
                    LOG(info) << "File already exists in database, skipping: " << objectKey << endl;
                    alreadyExists = true;
                    skippedCount++;
                    break;
                }
            }

            if (alreadyExists)
            {
                continue;
            }

            // Step 2e: Create sensor/stream for this cloud file
            // Extract filename from object key
            string fileName = objectKey;
            size_t lastSlash = objectKey.find_last_of('/');
            if (lastSlash != string::npos)
            {
                fileName = objectKey.substr(lastSlash + 1);
            }

            // Remove extension for sensor name
            string sensorName = fileName;
            size_t lastDot = fileName.find_last_of('.');
            if (lastDot != string::npos)
            {
                sensorName = fileName.substr(0, lastDot);
            }

            // Generate a UUID for the sensor ID to avoid numeric-only IDs causing DB type issues
            // The sensor name will still be the original filename for display purposes
            string sensorId = generate_uuid();

            // Construct permanent S3 URL using s3:// URI scheme
            string s3Url = "s3://" + bucketName + "/" + objectKey;
            LOG(info) << "  S3 URL: " << s3Url << " (storage type: " << config.cloud_storage_type << ")" << endl;

            Json::Value streamData;
            streamData["name"] = sensorName;
            streamData["sensorId"] = sensorId;  // Explicitly set UUID as sensor ID
            streamData["file_path"] = objectKey; // Store object key as file path
            streamData["url"] = s3Url; // Store permanent S3 URL using s3:// scheme
            streamData["container"] = container;
            streamData["encoding"] = codec;
            streamData["framerate"] = frameRate;
            streamData["duration"] = duration;
            // Pass width and height separately for proper stream table population
            streamData["width"] = std::to_string(width);
            streamData["height"] = std::to_string(height);
            streamData["resolution"] = std::to_string(width) + "x" + std::to_string(height);
            streamData["FrameCount"] = mediaInfo.get("FrameCount", "0").asString();
            streamData["AudioEncoding"] = mediaInfo.get("AudioCodec", EMPTY_STRING).asString();
            streamData["SampleRate"] = mediaInfo.get("SampleRate", EMPTY_STRING).asString();
            streamData["BitsPerSample"] = mediaInfo.get("Depth", EMPTY_STRING).asString();
            streamData["Channels"] = mediaInfo.get("Channels", EMPTY_STRING).asString();

            // Add the cloud file as a sensor/stream
            Json::Value req;
            req["storageLocation"] = static_cast<int>(StreamStorageTypeCloud);
            Json::Value addResponse;
            VmsErrorCode addResult = addFile(m_deviceManager, req, streamData, addResponse);
            // Internal flag from addFile's merge path -- not part of the public API.
            addResponse.removeMember("mergedExisting");

            if (addResult != VmsErrorCode::NoError)
            {
                LOG(error) << "Failed to add cloud file as sensor: " << objectKey
                          << " Error: " << getCameraErrorCodeString(addResult).second << endl;
                failedCount++;
                continue;
            }

            // Extract sensor and stream IDs from addResponse
            string addedSensorId = addResponse.get("id", "").asString();
            string streamId = addResponse.get("streamId", "").asString();

            if (addedSensorId.empty())
            {
                LOG(error) << "Failed to get sensor ID from addFile response for: " << objectKey << endl;
                failedCount++;
                continue;
            }

            LOG(info) << "  Added sensor - ID: " << addedSensorId << ", Stream ID: " << streamId << endl;

            // Step 2f: Add database entry for this cloud file
            VideoRecordDBColumns row;
            row.sensor_id_value = addedSensorId; // Use the sensor ID returned from addFile
            row.sensor_name_value = sensorName;
            row.stream_id_value = streamId;
            // Use the lastModified timestamp from cloud storage as the start time
            string lastModified = file.get("lastModified", "").asString();
            row.start_time_value = !lastModified.empty() ? isoToEpoch(lastModified, false) : getCurrentUnixTimestampInMs();
            row.filepath_value = objectKey; // Store object key as filepath
            row.resolution_value = std::to_string(width) + "x" + std::to_string(height);
            row.duration_value = static_cast<uint64_t>(std::stod(duration) * 1000); // Convert to milliseconds, handle decimal
            row.filefps_value = static_cast<uint32_t>(std::stof(frameRate)); // Convert float string to int
            row.codec_value = codec;
            row.object_id_value = objectKey; // Use object key as object ID
            row.filesize_value = strtoull(file.get("size", "0").asString().c_str(), nullptr, 10);
            row.record_config_value = RECORD_CONFIG_CLOUD_SCANNED; // Mark as cloud-sourced
            row.file_protection_value = "0";
            row.metadata_file_path_value = "";
            row.storage_location_value = StreamStorageTypeCloud; // Set storage location to Cloud
            row.bucket_name_value = bucketName; // Store bucket name

            // Create metadata JSON for cloud file (same format as local files)
            Json::Value metadata;
            metadata["mediaFilePath"] = objectKey;
            metadata["sensorId"] = addedSensorId;
            metadata["timestamp"] = static_cast<Json::Int64>(row.start_time_value);
            row.metadata_json_value = jsonToString(metadata);

            // Insert into database
            if (dbHelper->insertRowVideoRecord(row) == 0)
            {
                LOG(info) << "  ✓ Successfully imported cloud file: " << objectKey << endl;
                LOG(info) << "    Sensor ID: " << addedSensorId << ", Stream ID: " << streamId
                          << ", Resolution: " << row.resolution_value
                          << ", Duration: " << row.duration_value << "ms, FPS: " << row.filefps_value << endl;
                importedCount++;

                // Update storage size
                updateStorageSize(row.filesize_value, true);
            }
            else
            {
                LOG(error) << "Failed to insert database record for: " << objectKey << endl;
                // Try to remove the sensor we just added
                m_deviceManager->deleteSensor(addedSensorId);
                failedCount++;
            }
        }

        // Summary
        LOG(info) << "======================================" << endl;
        if (m_cloudScanShouldStop.load())
        {
            LOG(info) << "Cloud Storage Auto-Import Interrupted" << endl;
        }
        else
        {
            LOG(info) << "Cloud Storage Auto-Import Complete" << endl;
        }
        LOG(info) << "Total Video Files: " << videoFileCount << endl;
        LOG(info) << "Successfully Imported: " << importedCount << endl;
        LOG(info) << "Skipped (already exists/unsupported): " << skippedCount << endl;
        LOG(info) << "Failed: " << failedCount << endl;
        LOG(info) << "======================================" << endl;

        m_cloudScanInProgress.store(false);
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception during cloud storage scan: " << e.what() << endl;
        m_cloudScanInProgress.store(false);
    }
}