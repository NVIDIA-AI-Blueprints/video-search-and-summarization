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
 * @file storage_test_utils.h
 * @brief Common utilities for storage management unit tests
 * 
 * This file provides reusable helper functions for:
 * - File upload (PUT and POST)
 * - File deletion (cleanup)
 * - File tracking for automatic cleanup
 * - Common test data creation
 */

#ifndef STORAGE_TEST_UTILS_H
#define STORAGE_TEST_UTILS_H

#include <string>
#include <vector>
#include <jsoncpp/json/json.h>
#include "storage_management.h"
#include "mock_civetweb.h"

using namespace std;
using namespace nv_vms;

/**
 * @brief Result of a file upload operation in tests
 * 
 * NOTE: Named TestUploadResult to clearly indicate this is for testing
 */
struct TestUploadResult
{
    VmsErrorCode errorCode = VmsErrorCode::VMSInternalError;
    string fileId;
    string sensorId;
    string streamId;
    string filePath;
    int64_t bytes = 0;
    string errorMessage;
    
    bool isSuccess() const { return errorCode == VmsErrorCode::NoError; }
};

/**
 * @brief Result of a file deletion operation in tests
 * 
 * NOTE: Named TestDeleteResult to avoid conflict with nv_vms::DeleteResult
 */
struct TestDeleteResult
{
    VmsErrorCode errorCode = VmsErrorCode::VMSInternalError;
    int spaceSaved = 0;
    vector<string> invalidFiles;
    vector<string> protectedFiles;
    string errorMessage;
    
    bool isSuccess() const { return errorCode == VmsErrorCode::NoError; }
};

/**
 * @brief Tracks uploaded files for automatic cleanup
 * 
 * This class maintains a list of uploaded file IDs and ensures
 * they are deleted when tests complete, preventing test pollution.
 */
class UploadedFileTracker
{
private:
    struct TrackedFile
    {
        string fileId;
        string streamId;
        string filePath;
        string startTime;   // ISO 8601 format (for time-range deletion)
        string endTime;     // ISO 8601 format (for time-range deletion)
    };
    
    vector<TrackedFile> m_files;
    StorageManagement* m_storageMgmt;

public:
    UploadedFileTracker(StorageManagement* storageMgmt);
    ~UploadedFileTracker();
    
    // Track a file for cleanup.
    // Provide startTime/endTime (ISO 8601) for reliable time-range based deletion.
    void trackFile(const string& fileId, const string& streamId = "",
                   const string& filePath = "",
                   const string& startTime = "", const string& endTime = "");
    
    // Clean up all tracked files (uses deleteFilesByTimeRange when times are available)
    void cleanupAll();
    
    // Get tracked file count
    size_t getTrackedCount() const { return m_files.size(); }
};

/**
 * @brief Storage test utilities namespace
 */
namespace StorageTestUtils
{
    /**
     * @brief Clean test environment by removing storage directories
     * 
     * Removes vst_data and vst_video directories to ensure clean state.
     * This should be called at the start of each test to prevent pollution.
     * 
     * @param recreate If true, recreates empty directories after deletion (default: true)
     */
    void cleanTestEnvironment(bool recreate = true);
    
    /**
     * @brief Upload a file using PUT API
     * 
     * @param storageMgmt Storage management instance
     * @param mockConn Mock connection to use
     * @param filename Filename to upload as
     * @param timestamp Timestamp in ISO 8601 format (e.g., "2026-01-01T00:00:00.000Z")
     * @param sourceFilePath Local file to upload (e.g., "./tools/data/sample_10sec_h264.mp4")
     * @param sensorId Optional sensor ID (empty = auto-generate)
     * @param useLegacyApi true = PUT /file/{name}/{ts}, false = PUT /file/{name}?timestamp=ts
     * @return TestUploadResult with file ID, paths, and status
     */
    TestUploadResult uploadFilePut(
        StorageManagement* storageMgmt,
        MockConnection* mockConn,
        const string& filename,
        const string& timestamp,
        const string& sourceFilePath,
        const string& sensorId = "",
        bool useLegacyApi = true
    );
    
    /**
     * @brief Register an existing file using POST API with file path
     * 
     * This is better for test setup as it allows proper timestamp control.
     * Uses POST with mediaFilePath and metadata (no binary upload needed).
     * 
     * @param storageMgmt Storage management instance
     * @param mockConn Mock connection to use
     * @param sourceFilePath File to register (must exist on disk)
     * @param timestamp Timestamp in milliseconds
     * @param sensorId Optional sensor ID (empty = auto-generate)
     * @return TestUploadResult with file ID, paths, and status
     */
    TestUploadResult registerFile(
        StorageManagement* storageMgmt,
        MockConnection* mockConn,
        const string& sourceFilePath,
        int64_t timestamp,
        const string& sensorId = ""
    );
    
    /**
     * @brief Delete a file by unique ID
     * 
     * API: DELETE /api/v1/storage/file?id={fileId}
     * 
     * @param storageMgmt Storage management instance
     * @param fileId Unique file identifier to delete
     * @return TestDeleteResult with status
     */
    TestDeleteResult deleteFileById(
        StorageManagement* storageMgmt,
        const string& fileId
    );
    
    /**
     * @brief Delete a file by streamId (for nvstreamer)
     * 
     * API: DELETE /api/v1/storage/file/{streamId}
     * 
     * @param storageMgmt Storage management instance
     * @param streamId Stream identifier to delete
     * @return TestDeleteResult with status
     */
    TestDeleteResult deleteFileByStreamId(
        StorageManagement* storageMgmt,
        const string& streamId
    );
    
    /**
     * @brief Delete files by time range
     * 
     * API: DELETE /api/v1/storage/file/{streamId}?startTime=X&endTime=Y
     * 
     * @param storageMgmt Storage management instance
     * @param streamId Stream identifier
     * @param startTime Start time (ISO 8601 format)
     * @param endTime End time (ISO 8601 format)
     * @return TestDeleteResult with status
     */
    TestDeleteResult deleteFilesByTimeRange(
        StorageManagement* storageMgmt,
        const string& streamId,
        const string& startTime,
        const string& endTime
    );
    
    /**
     * @brief Delete multiple files by file paths
     * 
     * API: DELETE /api/v1/storage/file?filePath={path1}&filePath={path2}...
     * 
     * @param storageMgmt Storage management instance
     * @param filePaths Array of file paths to delete
     * @return TestDeleteResult with status
     */
    TestDeleteResult deleteFilesByPaths(
        StorageManagement* storageMgmt,
        const vector<string>& filePaths
    );
    
    /**
     * @brief Create standard test metadata
     * 
     * @param sensorId Sensor ID
     * @param timestamp Unix timestamp in milliseconds
     * @param streamName Stream name (optional)
     * @param eventInfo Event description (optional)
     * @param tag Category tag (optional)
     * @return JSON metadata object
     */
    Json::Value createTestMetadata(
        const string& sensorId,
        int64_t timestamp,
        const string& streamName = "test_stream",
        const string& eventInfo = "Test upload",
        const string& tag = "unit_test"
    );
}

#endif // STORAGE_TEST_UTILS_H
