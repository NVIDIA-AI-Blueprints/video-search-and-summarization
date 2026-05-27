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

/**
 * @file storage_test_utils.cpp
 * @brief Implementation of common storage test utilities
 */

#include "storage_test_utils.h"
#include <iostream>
#include <fstream>
#include <filesystem>

using namespace std;

// ==================== UploadedFileTracker Implementation ====================

UploadedFileTracker::UploadedFileTracker(StorageManagement* storageMgmt)
    : m_storageMgmt(storageMgmt)
{
}

UploadedFileTracker::~UploadedFileTracker()
{
    cleanupAll();
}

void UploadedFileTracker::trackFile(const string& fileId, const string& streamId,
                                    const string& filePath,
                                    const string& startTime, const string& endTime)
{
    TrackedFile entry;
    entry.fileId    = fileId;
    entry.streamId  = streamId;
    entry.filePath  = filePath;
    entry.startTime = startTime;
    entry.endTime   = endTime;
    m_files.push_back(entry);
    
    cout << "[TRACKER] Tracking file for cleanup:"
         << " id=" << (fileId.empty() ? "(none)" : fileId)
         << " stream=" << (streamId.empty() ? "(none)" : streamId)
         << " time=" << (startTime.empty() ? "(none)" : startTime + ".." + endTime)
         << endl;
}

void UploadedFileTracker::cleanupAll()
{
    if (m_storageMgmt == nullptr)
    {
        cout << "[TRACKER] Cannot cleanup - storage management not available" << endl;
        return;
    }
    
    cout << "[TRACKER] Cleaning up " << m_files.size() << " tracked file(s)..." << endl;
    
    for (const auto& entry : m_files)
    {
        TestDeleteResult result;
        result.errorCode = VmsErrorCode::VMSInternalError;
        
        // Prefer time-range deletion when start/end times are available
        if (!entry.streamId.empty() && !entry.startTime.empty() && !entry.endTime.empty())
        {
            cout << "[TRACKER] Deleting by time range: stream=" << entry.streamId
                 << " [" << entry.startTime << " .. " << entry.endTime << "]" << endl;
            result = StorageTestUtils::deleteFilesByTimeRange(
                m_storageMgmt, entry.streamId, entry.startTime, entry.endTime);
        }
        else if (!entry.fileId.empty())
        {
            // Fallback: delete by file ID
            cout << "[TRACKER] Deleting by file ID: " << entry.fileId << endl;
            result = StorageTestUtils::deleteFileById(m_storageMgmt, entry.fileId);
        }
        else if (!entry.streamId.empty())
        {
            // Last resort: delete by stream ID
            cout << "[TRACKER] Deleting by stream ID: " << entry.streamId << endl;
            result = StorageTestUtils::deleteFileByStreamId(m_storageMgmt, entry.streamId);
        }
        else
        {
            cout << "[TRACKER] No identifiers available for cleanup, skipping" << endl;
            continue;
        }
        
        if (result.isSuccess())
        {
            cout << "[TRACKER] Deleted successfully" << endl;
        }
        else
        {
            cout << "[TRACKER] Could not delete: " << result.errorMessage << endl;
        }
    }
    
    m_files.clear();
}

// ==================== StorageTestUtils Implementation ====================

namespace StorageTestUtils
{

/**
 * @brief Read data_path and video_path from configs/vst_storage.json.
 *
 * Falls back to "./vst_data/" and "./vst_video/" if the config file
 * is missing or cannot be parsed.
 */
static pair<string, string> getStoragePaths()
{
    string dataPath  = "./vst_data/";
    string videoPath = "./vst_video/";

    const string configFile = "configs/vst_storage.json";
    ifstream ifs(configFile);
    if (ifs.is_open())
    {
        Json::Value root;
        Json::CharReaderBuilder builder;
        string errs;
        if (Json::parseFromStream(builder, ifs, &root, &errs))
        {
            if (root.isMember("data_path"))
                dataPath = root["data_path"].asString();
            if (root.isMember("video_path"))
                videoPath = root["video_path"].asString();
        }
    }
    return {dataPath, videoPath};
}

void cleanTestEnvironment(bool recreate)
{
    cout << "[ENV-CLEANUP] Cleaning test environment..." << endl;

    auto [vstDataPath, vstVideoPath] = getStoragePaths();
    cout << "[ENV-CLEANUP] data_path : " << vstDataPath << endl;
    cout << "[ENV-CLEANUP] video_path: " << vstVideoPath << endl;
    
    // Remove vst_data directory
    if (std::filesystem::exists(vstDataPath))
    {
        try {
            std::filesystem::remove_all(vstDataPath);
            cout << "[ENV-CLEANUP] Removed " << vstDataPath << endl;
        } catch (const std::exception& e) {
            cout << "[ENV-CLEANUP] Could not remove " << vstDataPath << ": " << e.what() << endl;
        }
    }
    
    // Remove vst_video directory
    if (std::filesystem::exists(vstVideoPath))
    {
        try {
            std::filesystem::remove_all(vstVideoPath);
            cout << "[ENV-CLEANUP] Removed " << vstVideoPath << endl;
        } catch (const std::exception& e) {
            cout << "[ENV-CLEANUP] Could not remove " << vstVideoPath << ": " << e.what() << endl;
        }
    }
    
    // Recreate empty directories if requested
    if (recreate)
    {
        try {
            std::filesystem::create_directories(vstDataPath);
            std::filesystem::create_directories(vstVideoPath);
            cout << "[ENV-CLEANUP] Recreated empty directories" << endl;
        } catch (const std::exception& e) {
            cout << "[ENV-CLEANUP] Could not recreate directories: " << e.what() << endl;
        }
    }
    
    cout << "[ENV-CLEANUP] Test environment is clean\n" << endl;
}

TestUploadResult uploadFilePut(
    StorageManagement* storageMgmt,
    MockConnection* mockConn,
    const string& filename,
    const string& timestamp,
    const string& sourceFilePath,
    const string& sensorId,
    bool useLegacyApi)
{
    cout << "[UPLOAD-UTIL] Starting file upload..." << endl;
    cout << "[UPLOAD-UTIL]   Filename: " << filename << endl;
    cout << "[UPLOAD-UTIL]   Timestamp: " << timestamp << endl;
    cout << "[UPLOAD-UTIL]   Source: " << sourceFilePath << endl;
    cout << "[UPLOAD-UTIL]   API: " << (useLegacyApi ? "Legacy" : "New") << endl;
    
    TestUploadResult result;
    result.errorCode = VmsErrorCode::VMSInternalError;
    
    if (storageMgmt == nullptr || mockConn == nullptr)
    {
        result.errorMessage = "Storage management or mock connection is null";
        cout << "[UPLOAD-UTIL] ❌ Error: " << result.errorMessage << endl;
        return result;
    }
    
    // Reset mock connection state for fresh upload
    mockConn->uploadFileData.clear();
    mockConn->uploadBytesRead = 0;
    cout << "[UPLOAD-UTIL] Reset mock connection state" << endl;
    
    // Verify source file exists
    ifstream testFile(sourceFilePath, ios::binary);
    if (!testFile.good())
    {
        result.errorCode = VmsErrorCode::InvalidParameterError;
        result.errorMessage = "Source file not found: " + sourceFilePath;
        cout << "[UPLOAD-UTIL] ❌ Error: " << result.errorMessage << endl;
        return result;
    }
    testFile.close();
    cout << "[UPLOAD-UTIL] ✓ Source file exists" << endl;
    
    // Load real file into mock connection
    if (!mockConn->loadFileForUpload(sourceFilePath))
    {
        result.errorCode = VmsErrorCode::VMSInternalError;
        result.errorMessage = "Failed to load file: " + sourceFilePath;
        cout << "[UPLOAD-UTIL] ❌ Error: " << result.errorMessage << endl;
        return result;
    }
    cout << "[UPLOAD-UTIL] ✓ File loaded into mock connection" << endl;
    
    // Add Content-Type header
    mockConn->requestInfo.addHeader("Content-Type", "application/octet-stream");
    cout << "[UPLOAD-UTIL] ✓ Content-Type header added" << endl;
    
    // Build request info
    Json::Value req_info;
    req_info["method"] = "PUT";
    
    if (useLegacyApi)
    {
        // Legacy: PUT /api/v1/storage/file/{filename}/{timestamp}
        // Timestamp is ISO 8601 format string
        req_info["url"] = "/api/v1/storage/file/" + filename + "/" + timestamp;
        cout << "[UPLOAD-UTIL] Request: PUT " << req_info["url"].asString() << endl;
    }
    else
    {
        // New: PUT /api/v1/storage/file/{filename}?timestamp=X&sensorId=Y
        // Timestamp is ISO 8601 format string
        req_info["url"] = "/api/v1/storage/file/" + filename;
        
        string queryString = "timestamp=" + timestamp;
        if (!sensorId.empty())
        {
            queryString += "&sensorId=" + sensorId;
        }
        req_info["query"] = queryString;
        cout << "[UPLOAD-UTIL] Request: PUT " << req_info["url"].asString() 
             << "?" << queryString << endl;
    }
    
    // Setup mock connection request info
    mockConn->requestInfo.setMethod("PUT");
    mockConn->requestInfo.setUri(req_info["url"].asString());
    if (req_info.isMember("query"))
    {
        mockConn->requestInfo.setQueryString(req_info["query"].asString());
    }
    cout << "[UPLOAD-UTIL] Mock connection configured" << endl;
    
    // For PUT uploads, DO NOT pass metadata in input JSON
    // The timestamp comes from URL path (legacy) or query params (new)
    // Metadata input is only for POST multipart uploads
    Json::Value input;  // Empty for PUT uploads
    
    cout << "[UPLOAD-UTIL] Using PUT upload (timestamp from URL, not metadata)" << endl;
    
    // Call the API
    cout << "\n========================================" << endl;
    cout << "[UPLOAD-UTIL] Calling handleStorageFileAPIrequest..." << endl;
    cout << "[UPLOAD-UTIL] STORAGE SERVICE LOGS SHOULD APPEAR BELOW:" << endl;
    cout << "========================================" << endl;
    cout.flush();  // Force output before API call
    std::cerr.flush();
    
    Json::Value response;
    struct mg_connection* conn = reinterpret_cast<struct mg_connection*>(mockConn);
    
    result.errorCode = storageMgmt->handleStorageFileAPIrequest(req_info, input, response, conn);
    
    cout.flush();  // Force output after API call
    std::cerr.flush();
    cout << "========================================" << endl;
    cout << "[UPLOAD-UTIL] API call completed with result: " << static_cast<int>(result.errorCode) << endl;
    cout << "========================================" << endl;
    
    // Extract result data
    cout << "[UPLOAD-UTIL] Extracting response data..." << endl;
    cout << "[UPLOAD-UTIL] Response JSON:" << endl;
    cout << response.toStyledString() << endl;
    
    if (response.isMember("id"))
    {
        result.fileId = response["id"].asString();
        cout << "[UPLOAD-UTIL] ✓ File ID: " << result.fileId << endl;
    }
    if (response.isMember("sensorId"))
    {
        result.sensorId = response["sensorId"].asString();
        cout << "[UPLOAD-UTIL] ✓ Sensor ID: " << result.sensorId << endl;
    }
    if (response.isMember("streamId"))
    {
        result.streamId = response["streamId"].asString();
        cout << "[UPLOAD-UTIL] ✓ Stream ID: " << result.streamId << endl;
    }
    if (response.isMember("filePath"))
    {
        result.filePath = response["filePath"].asString();
        cout << "[UPLOAD-UTIL] ✓ File Path: " << result.filePath << endl;
    }
    if (response.isMember("bytes"))
    {
        result.bytes = response["bytes"].asInt64();
        cout << "[UPLOAD-UTIL] ✓ File Size: " << result.bytes << " bytes" << endl;
    }
    if (response.isMember("error_message"))
    {
        result.errorMessage = response["error_message"].asString();
        cout << "[UPLOAD-UTIL] ❌ Error: " << result.errorMessage << endl;
    }
    
    if (result.errorCode == VmsErrorCode::NoError)
    {
        cout << "[UPLOAD-UTIL] ✅ Upload succeeded!" << endl;
    }
    else
    {
        cout << "[UPLOAD-UTIL] ❌ Upload failed with error code: " << static_cast<int>(result.errorCode) << endl;
    }
    
    return result;
}

TestDeleteResult deleteFileById(StorageManagement* storageMgmt, const string& fileId)
{
    TestDeleteResult result;
    result.errorCode = VmsErrorCode::VMSInternalError;
    
    if (storageMgmt == nullptr)
    {
        result.errorMessage = "Storage management is null";
        return result;
    }
    
    // Build request: DELETE /api/v1/storage/file?id={fileId}
    Json::Value req_info;
    req_info["url"] = "/api/v1/storage/file";
    req_info["method"] = "DELETE";
    req_info["query"] = "id=" + fileId;
    
    Json::Value response;
    result.errorCode = storageMgmt->handleStorageFileAPIrequest(req_info, Json::Value(), response, nullptr);
    
    // Extract result data
    if (response.isMember("spaceSaved"))
        result.spaceSaved = response["spaceSaved"].asInt();
    if (response.isMember("invalidFiles") && response["invalidFiles"].isArray())
    {
        for (const auto& file : response["invalidFiles"])
            result.invalidFiles.push_back(file.asString());
    }
    if (response.isMember("protectedFiles") && response["protectedFiles"].isArray())
    {
        for (const auto& file : response["protectedFiles"])
            result.protectedFiles.push_back(file.asString());
    }
    if (response.isMember("error_message"))
        result.errorMessage = response["error_message"].asString();
    
    return result;
}

TestDeleteResult deleteFileByStreamId(StorageManagement* storageMgmt, const string& streamId)
{
    TestDeleteResult result;
    result.errorCode = VmsErrorCode::VMSInternalError;
    
    if (storageMgmt == nullptr)
    {
        result.errorMessage = "Storage management is null";
        return result;
    }
    
    // Build request: DELETE /api/v1/storage/file/{streamId}
    Json::Value req_info;
    req_info["url"] = "/api/v1/storage/file/" + streamId;
    req_info["method"] = "DELETE";
    
    Json::Value response;
    result.errorCode = storageMgmt->handleStorageFileAPIrequest(req_info, Json::Value(), response, nullptr);
    
    if (response.isMember("spaceSaved"))
        result.spaceSaved = response["spaceSaved"].asInt();
    if (response.isMember("error_message"))
        result.errorMessage = response["error_message"].asString();
    
    return result;
}

TestDeleteResult deleteFilesByTimeRange(
    StorageManagement* storageMgmt,
    const string& streamId,
    const string& startTime,
    const string& endTime)
{
    TestDeleteResult result;
    result.errorCode = VmsErrorCode::VMSInternalError;
    
    if (storageMgmt == nullptr)
    {
        result.errorMessage = "Storage management is null";
        return result;
    }
    
    // Build request: DELETE /api/v1/storage/file/{streamId}?startTime=X&endTime=Y
    Json::Value req_info;
    req_info["url"] = "/api/v1/storage/file/" + streamId;
    req_info["method"] = "DELETE";
    req_info["query"] = "startTime=" + startTime + "&endTime=" + endTime;
    
    Json::Value response;
    result.errorCode = storageMgmt->handleStorageFileAPIrequest(req_info, Json::Value(), response, nullptr);
    
    if (response.isMember("spaceSaved"))
        result.spaceSaved = response["spaceSaved"].asInt();
    if (response.isMember("error_message"))
        result.errorMessage = response["error_message"].asString();
    
    return result;
}

TestDeleteResult deleteFilesByPaths(
    StorageManagement* storageMgmt,
    const vector<string>& filePaths)
{
    TestDeleteResult result;
    result.errorCode = VmsErrorCode::VMSInternalError;
    
    if (storageMgmt == nullptr)
    {
        result.errorMessage = "Storage management is null";
        return result;
    }
    
    // Build query string: filePath=path1&filePath=path2...
    // Paths are expected to contain only URL-safe characters (alphanumeric,
    // hyphens, underscores, dots, slashes). Storage module paths use UUIDs
    // and controlled directory names so this holds in practice.
    string queryString;
    for (size_t i = 0; i < filePaths.size(); i++)
    {
        if (i > 0) queryString += "&";
        queryString += "filePath=" + filePaths[i];
    }
    
    // Build request: DELETE /api/v1/storage/file?filePath=...
    Json::Value req_info;
    req_info["url"] = "/api/v1/storage/file";
    req_info["method"] = "DELETE";
    req_info["query"] = queryString;
    
    Json::Value response;
    result.errorCode = storageMgmt->handleStorageFileAPIrequest(req_info, Json::Value(), response, nullptr);
    
    if (response.isMember("spaceSaved"))
        result.spaceSaved = response["spaceSaved"].asInt();
    if (response.isMember("invalidFiles") && response["invalidFiles"].isArray())
    {
        for (const auto& file : response["invalidFiles"])
            result.invalidFiles.push_back(file.asString());
    }
    if (response.isMember("protectedFiles") && response["protectedFiles"].isArray())
    {
        for (const auto& file : response["protectedFiles"])
            result.protectedFiles.push_back(file.asString());
    }
    if (response.isMember("error_message"))
        result.errorMessage = response["error_message"].asString();
    
    return result;
}

Json::Value createTestMetadata(
    const string& sensorId,
    int64_t timestamp,
    const string& streamName,
    const string& eventInfo,
    const string& tag)
{
    Json::Value metadata;
    metadata["sensorId"] = sensorId;
    metadata["timestamp"] = timestamp;
    
    if (!streamName.empty())
        metadata["streamName"] = streamName;
    if (!eventInfo.empty())
        metadata["eventInfo"] = eventInfo;
    if (!tag.empty())
        metadata["tag"] = tag;
    
    return metadata;
}

} // namespace StorageTestUtils
