/*
 * SPDX-FileCopyrightText: Copyright (c) 2023-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "sensor_management.h"
#include <jsoncpp/json/json.h>
#include "HttpServerRequestHandler.h"
#include "mm_utils.h"
#include "gst_utils.h"
#include "modules_apis.h"
#include <mutex>
#include "database.h"
#include "unified_storage_types.h"

using namespace std;
using namespace nv_vms;

// Forward declaration
namespace nv_vms {
    class IMediaInterface;
}

// Structure to hold common video file processing parameters
struct VideoFileProcessingParams {
    int64_t epoch_user_start_time = 0;
    int64_t epoch_user_end_time = 0;
    std::vector<VideoFileInfo> fileNameArray;
    size_t max_download_size = 0;
    bool get_accurate = false;
    std::vector<std::pair<std::string, std::string>> remoteLocalPairs;
    // Additional processed parameters
    string input_file_path;
    int64_t file_start_time = 0;
    int64_t relative_start_sec = 0;
    int64_t relative_end_sec = 0;
    int64_t seek_start_pos = 0;
    int64_t seek_end_pos = std::numeric_limits<int64_t>::max();

    // Millisecond precision parameters for LibAV
    int64_t relative_start_ms = 0;
    int64_t relative_end_ms = 0;
    bool use_millisecond_precision = false;
    bool is_cloud_stream = false;
    bool disable_giosrc_for_growing_file = false;
    bool has_bframes = false;
};

/* TODO(storage management): When we remove the dependency of the SensorManagement completely, after that we have to move these below APIs to StorageManagement class  */
/* On the SENSOR_TYPE_FILE merge path, response carries an internal
 * "mergedExisting"=true flag plus "id" / "streamId" of the merged stream so
 * the caller can roll back just the new stream on a later failure. Callers
 * that surface response over a public API should removeMember("mergedExisting"). */
VmsErrorCode addFile(std::shared_ptr<DeviceManager> deviceMngr, const Json::Value &req_info, const Json::Value &data, Json::Value &response);
VmsErrorCode handleFileUpload(std::shared_ptr<DeviceManager> deviceMngr, const struct mg_request_info *req_info, struct mg_connection *conn, Json::Value &out, bool isPutUpload = false, const std::string& filename = "", const std::string& timestamp = "", const std::string& sensorId = "", bool isLegacyUpload = false);
VmsErrorCode deleteFile(std::shared_ptr<DeviceManager> deviceMngr, const Json::Value &req_info, const Json::Value &in, Json::Value &out);
VmsErrorCode checkMaxSensorsLimit(std::shared_ptr<DeviceManager> deviceMngr, Json::Value& response);
int field_stored(const char *path, long long file_size, void *user_data);
int field_get(const char *key, const char *value, size_t valuelen, void *user_data);
int field_found(const char *key, const char *filename, char *path, size_t pathlen, void *user_data);
void addOrRemoveInProtectList(std::vector<VideoFileInfo>& files, bool removeOrAdd);

nv_vms::VmsErrorCode makeVideoFile (std::string start_time, std::string end_time,
                                    std::string sensor_id, std::string id, std::string device_name,
                                    std::string& file_name, std::string& video_codec,
                                    std::string full_length, std::string sensor_type, std::string container = "mp4",
                                    std::string transcode = "gop", std::string disable_audio = "false",
                                    std::string enable_overlay = "false", OverlayBBoxParams *ol_params = nullptr,
                                    std::string frameRate = "",
                                    nv_vms::IMediaInterface* media_interface = nullptr,
                                    std::string uselibav = "false", bool isCloudStream = false,
                                    int64_t* actual_start_epoch_ms = nullptr);

void  cleanupDownloadedFiles(const std::vector<std::pair<std::string, std::string>>& remoteLocalPairs, bool enable_minio);

std::string generateTempVideoFilePath(const std::string& webRootPath, const std::string& baseFileName, const std::string& startTime);
VmsErrorCode recordTempFileForCleanup(const std::string& filePath, const std::string& streamId, 
                                     const std::string& deviceId, int64_t expiryTimestamp);

/**
 * @brief Convert CloudListResult to JSON format for API response
 * @param result The CloudListResult to convert
 * @return Json::Value containing the formatted result
 */
Json::Value convertCloudListResultToJson(const nv_vms::CloudListResult& result);

// Two-pipeline download path (reader+writer)
// actual_start_epoch_ms: optional output for actual start time (from keyframe) in remux mode
nv_vms::VmsErrorCode downloadVideoFile(
    const VideoFileProcessingParams& params,
    std::string& output_file,
    std::string& video_codec,
    const std::string& container,
    const std::string& transcode,
    bool do_seek,
    const std::string& disable_audio,
    const std::string& enable_overlay,
    OverlayBBoxParams *ol_params,
    const std::string& user_start_time,
    const std::string& user_end_time,
    const std::string& device_name,
    const std::string& sensor_id,
    const std::string& sensor_type,
    const std::string& frameRate,
    nv_vms::IMediaInterface* media_interface = nullptr,
    int64_t* actual_start_epoch_ms = nullptr);
