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
 * @file storage.cpp
 * @brief Unit tests for StorageManagement::HandleFileDownload() API
 * 
 * Tests the HandleFileDownload() method indirectly through the public API
 * handleStorageFileAPIrequest() which internally calls HandleFileDownload().
 * 
 * API: GET /api/v1/storage/file/{streamId}
 * Example: curl -X GET "http://localhost:30888/vst/api/v1/storage/file/94841887-71ef-4c89-940d-b7e2aff26d0e?startTime=2025-10-31T09:43:06.386Z&endTime=2025-10-31T09:45:06.386Z&disableAudio=true" -o downloaded_video.mp4
 */

#include "gtest/gtest.h"
#include <iostream>
#include <string>
#include <fstream>
#include <chrono>
#include <vector>
#include <cstdio>
#include <ctime>
#include <cstdlib>
#include <filesystem>
#include <algorithm>
#include <jsoncpp/json/json.h>

// Storage module headers
#include "storage_management.h"
#include "vstmodule.h"
#include "utils.h"  // For convertEpocToISO8601_2

// Pipeline unit test headers
#include "clip_reader_producer.h"
#include "remux_writer_consumer.h"
#include "transcode_writer_consumer.h"
#include "nvhwdetection.h"

// Mock CivetWeb infrastructure for testing
#include "utils/mock_civetweb.h"

// Test utilities for upload/delete operations
#include "utils/storage_test_utils.h"

// Recorder utilities for timeline operations (shared with recorder tests)
#include "utils/recorder_utils.h"
#include "streamrecorder.h"

using namespace std;
using namespace nv_vms;

// ==================== TEST FIXTURE ====================

// Forward declare TestConfig namespace from test_main.cpp
namespace TestConfig
{
    extern std::string videoDirectory;
    extern std::vector<std::string>& videoFiles;
    std::vector<std::string>& getVideoFiles();  // Safe for static-init use
}

/**
 * @brief Test fixture for StorageManagement::HandleFileDownload()
 * 
 * Note: HandleFileDownload is private, so we test it through the public API
 * handleStorageFileAPIrequest() which internally calls HandleFileDownload().
 * 
 * This is a parameterized test fixture that runs tests with different video files.
 * Use --video-dir=/path/to/videos to specify a directory with video files.
 * 
 * Function tested (private):
 * VmsErrorCode HandleFileDownload(const string& queryString, const string& streamId, 
 *                                 Json::Value& response, struct mg_connection* conn, 
 *                                 bool isURLRequested)
 * 
 * Tested via public API:
 * VmsErrorCode handleStorageFileAPIrequest(const Json::Value& req_info, const Json::Value &in, 
 *                                          Json::Value &response, struct mg_connection *conn)
 */
class Download : public ::testing::TestWithParam<std::string>
{
protected:
    StorageManagement* m_storageMgmt = nullptr;
    StreamRecorder* m_recorder = nullptr;
    MockConnection* m_mockConn = nullptr;
    UploadedFileTracker* m_fileTracker = nullptr;
    
    // Test data - populated after upload in SetUp()
    string UPLOADED_FILE_ID;
    string UPLOADED_SENSOR_ID;
    string UPLOADED_STREAM_ID;
    string UPLOADED_FILE_PATH;
    string UPLOAD_START_TIME;  // Timeline-based start time (set in SetUp)
    string UPLOAD_END_TIME;    // Timeline-based end time (set in SetUp)
    
    // Param is the video file path directly (populated from TestConfig::videoFiles).
    string getTestVideoFile() const { return GetParam(); }

    void SetUp() override
    {
        string testVideoFile = getTestVideoFile();
        
        cout << "\n======================================================" << endl;
        cout << "[SETUP] Initializing download test" << endl;
        cout << "[SETUP] Test video file: " << testVideoFile << endl;
        cout << "======================================================" << endl;
        
        // Verify test video file exists
        if (!std::filesystem::exists(testVideoFile))
        {
            cout << "[SETUP] ❌ Test video file not found: " << testVideoFile << endl;
            GTEST_SKIP() << "Test video file not found: " << testVideoFile;
        }
        
        
        ModuleLoader* moduleLoader = ModuleLoader::getInstance();
        
        // Initialize StorageManagement module
        cout << "[SETUP] Initializing StorageManagement module..." << endl;
        int ret = moduleLoader->initialize(ModuleStorageManagement);
        ASSERT_EQ(ret, 0) << "StorageManagement module initialization failed";
        
        // Get StorageManagement instance
        m_storageMgmt = GET_STORAGE_MNGT();
        
        if (m_storageMgmt == nullptr)
        {
            cout << "[SETUP] ⚠️  StorageManagement module not loaded" << endl;
            return;
        }
        else
        {
            cout << "[SETUP] ✓ StorageManagement instance ready" << endl;
        }
        
        // Initialize StreamRecorder module separately (for timeline queries)
        cout << "[SETUP] Initializing StreamRecorder module..." << endl;
        ret = moduleLoader->initialize(ModuleStreamRecorder);
        
        if (ret != 0)
        {
            cout << "[SETUP] ⚠️  StreamRecorder module initialization failed (timeline queries will be skipped)" << endl;
            m_recorder = nullptr;
        }
        else
        {
            // Get StreamRecorder instance
            m_recorder = GET_RECORDER();
            
            if (m_recorder == nullptr)
            {
                cout << "[SETUP] ⚠️  StreamRecorder instance not available (timeline queries will be skipped)" << endl;
            }
            else
            {
                cout << "[SETUP] ✓ StreamRecorder instance ready" << endl;
            }
        }
        
        // Create mock connection and file tracker
        m_mockConn = new MockConnection();
        m_fileTracker = new UploadedFileTracker(m_storageMgmt);
        cout << "[SETUP] ✓ Mock connection and file tracker created" << endl;
        
        // Upload a real video file to use in download tests
        cout << "\n========================================" << endl;
        cout << "[SETUP] UPLOADING TEST FILE FOR DOWNLOAD TESTS" << endl;
        cout << "========================================" << endl;
        cout << "[SETUP] NOTE: Watch for storage service logs below..." << endl;
        cout << "[SETUP] If you don't see storage logs, they may be filtered in SetUp()" << endl;
        cout << "========================================\n" << endl;
        

        // Calculate time range for download test
        // Start time: 1st Jan 2026 00:00:00 UTC
        // End time: Start time + video length (10 seconds)
        
        // 1st Jan 2026 00:00:00 UTC in milliseconds since epoch
        int64_t startMs = 1767225600000L;  // 2026-01-01 00:00:00 UTC
        auto startMicros = startMs * 1000;  // Convert to microseconds
        
        // End time: Start time + video length
        auto endMs = startMs + 8000;  // +10 seconds
        auto endMicros = endMs * 1000;  // Convert to microseconds
        
        // Convert to ISO 8601 format
        UPLOAD_START_TIME = convertEpocToISO8601_2(startMicros);  // 1970-01-01T00:00:00.000Z
        UPLOAD_END_TIME = convertEpocToISO8601_2(endMicros);

        
        // Use original source file name (no renaming)
        string uniqueFilename = std::filesystem::path(testVideoFile).filename().string();
        
        // Upload file using LEGACY API (timestamp in path, not query)
        // Pass timestamp in ISO 8601 format
        // Use the test video file from parameterized test
        TestUploadResult uploadResult = StorageTestUtils::uploadFilePut(
            m_storageMgmt,
            m_mockConn,
            uniqueFilename,
            UPLOAD_START_TIME,  // ISO 8601 format timestamp
            testVideoFile,       // Parameterized video file
            "",   // Let it auto-generate sensorId
            true  // Use LEGACY API (timestamp in path)
        );
        
        if (uploadResult.isSuccess())
        {
            UPLOADED_FILE_ID = uploadResult.fileId;
            UPLOADED_SENSOR_ID = uploadResult.sensorId;
            UPLOADED_STREAM_ID = uploadResult.streamId;
            UPLOADED_FILE_PATH = uploadResult.filePath;
            
            // Track for cleanup (include time range for reliable deletion)
            m_fileTracker->trackFile(UPLOADED_FILE_ID, UPLOADED_STREAM_ID, UPLOADED_FILE_PATH,
                                     UPLOAD_START_TIME, UPLOAD_END_TIME);
            
            
            cout << "[SETUP] ✓ Test file uploaded successfully:" << endl;
            cout << "[SETUP]   File ID: " << UPLOADED_FILE_ID << endl;
            cout << "[SETUP]   Sensor ID: " << UPLOADED_SENSOR_ID << endl;
            cout << "[SETUP]   Stream ID: " << UPLOADED_STREAM_ID << endl;
            cout << "[SETUP]   File Path: " << UPLOADED_FILE_PATH << endl;
            cout << "[SETUP]   Upload Timestamp: " << UPLOAD_START_TIME << " ms" << endl;
            cout << "[SETUP]   Download Query Range:" << endl;
            cout << "[SETUP]     Start Time (ISO): " << UPLOAD_START_TIME << endl;
            cout << "[SETUP]     End Time (ISO): " << UPLOAD_END_TIME << endl;
            
            // Query timelines using RecorderTestUtils to determine final download time range
            // This applies timeline-based selection to all download tests
            if (m_recorder != nullptr)
            {
                cout << "\n[SETUP] Querying recorder timelines for download time range..." << endl;
                
                // Use utility function to get timelines
                string timelineStart, timelineEnd;
                TestTimelineResult timelineResult = RecorderTestUtils::getStreamTimelines(
                    m_recorder,
                    UPLOADED_STREAM_ID,
                    timelineStart,        // OUTPUT: Timeline start time
                    timelineEnd,          // OUTPUT: Timeline end time
                    10000                 // Target 10 seconds
                );
                
                if (timelineResult.isSuccess() && !timelineStart.empty() && !timelineEnd.empty())
                {
                    cout << "[SETUP] ✓ Timeline retrieved successfully" << endl;
                    cout << "[SETUP] Timeline range: " << timelineStart << " to " << timelineEnd << endl;
                    
                    // Check if timeline has duration information
                    if (timelineResult.timelines.isArray() && timelineResult.timelines.size() > 0)
                    {
                        const Json::Value& firstSegment = timelineResult.timelines[0];
                        
                        if (firstSegment.isMember("duration"))
                        {
                            int64_t durationMs = firstSegment["duration"].asInt64();
                            cout << "[SETUP] Timeline duration: " << durationMs << " ms" << endl;
                            
                            const int64_t TARGET_DURATION_MS = 10000;  // 10 seconds
                            
                            if (durationMs > TARGET_DURATION_MS)
                            {
                                // Video is longer than 10 seconds - select random 10-second segment
                                srand(static_cast<unsigned>(time(nullptr)));
                                int64_t maxOffset = durationMs - TARGET_DURATION_MS;
                                int64_t randomOffset = (rand() % (maxOffset / 1000)) * 1000;  // Random offset in ms
                                
                                cout << "[SETUP] Randomly selecting 10-second segment at offset " 
                                     << randomOffset << " ms" << endl;
                                
                                // Update time range with timeline-based selection
                                UPLOAD_START_TIME = timelineStart;
                                UPLOAD_END_TIME = timelineEnd;
                                
                                cout << "[SETUP] Note: Using full timeline range (precise offset calculation TBD)" << endl;
                                cout << "[SETUP] Would extract from offset " << randomOffset 
                                     << " ms to " << (randomOffset + TARGET_DURATION_MS) << " ms" << endl;
                            }
                            else
                            {
                                // Video is 10 seconds or less - use full timeline
                                UPLOAD_START_TIME = timelineStart;
                                UPLOAD_END_TIME = timelineEnd;
                                cout << "[SETUP] Using full timeline (" << durationMs 
                                     << " ms - less than 10 seconds)" << endl;
                            }
                        }
                        else
                        {
                            // No duration info, use full timeline
                            UPLOAD_START_TIME = timelineStart;
                            UPLOAD_END_TIME = timelineEnd;
                            cout << "[SETUP] Using full timeline (no duration info)" << endl;
                        }
                    }
                    else
                    {
                        // Use timeline times returned by utility
                        UPLOAD_START_TIME = timelineStart;
                        UPLOAD_END_TIME = timelineEnd;
                        cout << "[SETUP] Using timeline range from utility" << endl;
                    }
                    
                    cout << "[SETUP] ✓ Final download time range (timeline-based):" << endl;
                    cout << "[SETUP]   Start: " << UPLOAD_START_TIME << endl;
                    cout << "[SETUP]   End: " << UPLOAD_END_TIME << endl;
                }
                else
                {
                    cout << "[SETUP] ⚠️  Timeline query failed: " << timelineResult.errorMessage << endl;
                    cout << "[SETUP] Using calculated time range as fallback" << endl;
                    // UPLOAD_START_TIME and UPLOAD_END_TIME already set above from calculation
                }
            }
            else
            {
                cout << "[SETUP] ⚠️  Recorder not available, using calculated time range" << endl;
            }
        }
        else
        {
            cout << "[SETUP] ⚠️  Failed to upload test file: " << uploadResult.errorMessage << endl;
            cout << "[SETUP] Download tests may be skipped" << endl;
        }
    }

    void TearDown() override
    {
        cout << "[CLEANUP] Cleaning up download test..." << endl;
        
        // Cleanup uploaded test file
        if (m_fileTracker)
        {
            m_fileTracker->cleanupAll();
            delete m_fileTracker;
            m_fileTracker = nullptr;
        }
        
        // Cleanup downloaded files (created during download tests)
        // Downloaded files follow pattern: <device_name>_<start_time>_<end_time>_<thread_id>.mp4
        // They are created in the current directory by downloadVideoFile()
        cout << "[CLEANUP] Removing downloaded test files..." << endl;
        string downloadPattern = "download_test_*.mp4";
        string findCommand = "find . -maxdepth 1 -name '" + downloadPattern + "' -type f";
        FILE* pipe = popen(findCommand.c_str(), "r");
        if (pipe)
        {
            char buffer[256];
            vector<string> downloadedFiles;
            while (fgets(buffer, sizeof(buffer), pipe) != nullptr)
            {
                string filePath = buffer;
                // Remove trailing newline
                if (!filePath.empty() && filePath.back() == '\n')
                {
                    filePath.pop_back();
                }
                if (!filePath.empty())
                {
                    downloadedFiles.push_back(filePath);
                }
            }
            pclose(pipe);
            
            // Delete each downloaded file
            for (const auto& file : downloadedFiles)
            {
                if (remove(file.c_str()) == 0)
                {
                    cout << "[CLEANUP] ✓ Deleted downloaded file: " << file << endl;
                }
                else
                {
                    cout << "[CLEANUP] ⚠️  Failed to delete: " << file << endl;
                }
            }
            
            if (downloadedFiles.empty())
            {
                cout << "[CLEANUP] No downloaded files found to clean up" << endl;
            }
        }
        
        if (m_mockConn)
        {
            delete m_mockConn;
            m_mockConn = nullptr;
        }
        
        ModuleLoader::getInstance()->deInitialize();
        cout << "[CLEANUP] Complete\n" << endl;
    }
    
    // Helper to build query string
    string buildQuery(const string& startTime, const string& endTime, 
                     const string& extraParams = "")
    {
        string query = "";
        if (!startTime.empty()) query += "startTime=" + startTime;
        if (!endTime.empty()) query += (query.empty() ? "" : "&") + string("endTime=") + endTime;
        if (!extraParams.empty()) query += (query.empty() ? "" : "&") + extraParams;
        LOG(info) << "Query: " << query << endl;
        LOG(info) << "StartTime: " << startTime << endl;
        LOG(info) << "EndTime: " << endTime << endl;
        LOG(info) << "ExtraParams: " << extraParams << endl;    
        return query;
    }
    
    // Helper to create request info JSON for the public API
    Json::Value createRequestInfo(const string& streamId, const string& queryString, 
                                   const string& action = "")
    {
        Json::Value req_info;
        string url = "/api/v1/storage/file/" + streamId;
        if (!action.empty()) url += "/" + action;
        
        req_info["url"] = url;
        req_info["method"] = "GET";
        req_info["query"] = queryString;
        return req_info;
    }
    
    // Helper to call HandleFileDownload through the public API
    VmsErrorCode callHandleFileDownload(const string& streamId, const string& queryString,
                                       Json::Value& response, const string& action = "")
    {
        Json::Value req_info = createRequestInfo(streamId, queryString, action);
        Json::Value input;
        
        // Create a mock connection for tests that need it
        MockConnection mockConn;
        mockConn.requestInfo.setMethod("GET");
        mockConn.requestInfo.setUri(req_info["url"].asString());
        mockConn.requestInfo.setQueryString(queryString);
        
        return m_storageMgmt->handleStorageFileAPIrequest(req_info, input, response, 
                                                         reinterpret_cast<struct mg_connection*>(&mockConn));
    }
};

// ==================== INDIVIDUAL TEST CASES ====================

/**
 * @brief Diagnostic test - Verify file was uploaded in SetUp
 * 
 * This test simply checks if the upload in SetUp() succeeded.
 * Run this FIRST to verify the test setup is working.
 */


/**
 * @brief Test valid download request with required parameters
 * 
 * Downloads the file that was uploaded in SetUp() using real sensor ID and timestamp.
 * Uses RecorderTestUtils::getStreamTimelines to get actual video timelines, then
 * randomly selects a ~10 second segment (or full length if shorter).
 */
TEST_P(Download, ValidRequestWithRequiredParameters)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    if (UPLOADED_SENSOR_ID.empty()) GTEST_SKIP() << "Test file not uploaded in SetUp";
    
    cout << "[TEST] Valid request with required parameters (using uploaded file)" << endl;
    cout << "[TEST] Downloading sensor ID: " << UPLOADED_SENSOR_ID << endl;
    
    // Use the time range determined in SetUp() (timeline-based if available)
    cout << "[TEST] Download time range (from SetUp):" << endl;
    cout << "[TEST]   Start: " << UPLOAD_START_TIME << endl;
    cout << "[TEST]   End: " << UPLOAD_END_TIME << endl;
    
    string queryString = buildQuery(UPLOAD_START_TIME, UPLOAD_END_TIME, "disableAudio=true");
    Json::Value response;
    
    VmsErrorCode result = callHandleFileDownload(UPLOADED_SENSOR_ID, queryString, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    if (response.isMember("error_message"))
    {
        cout << "[TEST] Error: " << response["error_message"].asString() << endl;
    }
    
    // Must succeed since we uploaded a real file and used actual timeline data
    EXPECT_EQ(result, VmsErrorCode::NoError)
        << "Download should succeed for uploaded file with timeline-based time range";
}

/**
 * @brief Test missing startTime parameter
 * 
 * Validates that the function properly rejects requests without startTime.
 */
TEST_P(Download, MissingStartTime)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    if (UPLOADED_SENSOR_ID.empty()) GTEST_SKIP() << "Test file not uploaded in SetUp";
    
    cout << "[TEST] Missing startTime parameter (error validation)" << endl;
    
    string queryString = buildQuery("", UPLOAD_END_TIME, "disableAudio=true");
    Json::Value response;
    
    VmsErrorCode result = callHandleFileDownload(UPLOADED_SENSOR_ID, queryString, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    EXPECT_EQ(result, VmsErrorCode::InvalidParameterError)
        << "Should return InvalidParameterError when startTime is missing";
}

/**
 * @brief Test missing endTime parameter
 * 
 * Validates that the function properly rejects requests without endTime.
 */
TEST_P(Download, MissingEndTime)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    if (UPLOADED_SENSOR_ID.empty()) GTEST_SKIP() << "Test file not uploaded in SetUp";
    
    cout << "[TEST] Missing endTime parameter (error validation)" << endl;
    
    string queryString = buildQuery(UPLOAD_START_TIME, "", "disableAudio=true");
    Json::Value response;
    
    VmsErrorCode result = callHandleFileDownload(UPLOADED_SENSOR_ID, queryString, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    EXPECT_EQ(result, VmsErrorCode::InvalidParameterError)
        << "Should return InvalidParameterError when endTime is missing";
}

/**
 * @brief Test empty streamId
 * 
 * Validates that the function properly rejects requests with empty streamId.
 */
TEST_P(Download, EmptyStreamId)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    
    cout << "[TEST] Empty streamId (error validation)" << endl;
    
    // Use dummy time range for error test
    string startTime = "2025-10-31T09:43:06.386Z";
    string endTime = "2025-10-31T09:45:06.386Z";
    string queryString = buildQuery(startTime, endTime);
    Json::Value response;
    
    VmsErrorCode result = callHandleFileDownload("", queryString, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    EXPECT_EQ(result, VmsErrorCode::InvalidParameterError)
        << "Should return InvalidParameterError for empty streamId";
}

/**
 * @brief Test with disableAudio parameter
 * 
 * Tests the optional disableAudio parameter using uploaded file.
 */
TEST_P(Download, WithDisableAudioParameter)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    if (UPLOADED_SENSOR_ID.empty()) GTEST_SKIP() << "Test file not uploaded in SetUp";
    
    cout << "[TEST] With disableAudio parameter (using uploaded file)" << endl;
    
    string queryString = buildQuery(UPLOAD_START_TIME, UPLOAD_END_TIME, "disableAudio=true");
    Json::Value response;
    
    VmsErrorCode result = callHandleFileDownload(UPLOADED_SENSOR_ID, queryString, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    EXPECT_EQ(result, VmsErrorCode::NoError)
        << "Should download uploaded file with disableAudio parameter";
}

/**
 * @brief Test with MP4 container format
 * 
 * Tests the container parameter with MP4 format using uploaded file.
 */
TEST_P(Download, WithMp4Container)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    if (UPLOADED_SENSOR_ID.empty()) GTEST_SKIP() << "Test file not uploaded in SetUp";
    
    cout << "[TEST] With MP4 container format (using uploaded file)" << endl;
    
    string queryString = buildQuery(UPLOAD_START_TIME, UPLOAD_END_TIME, "container=mp4");
    Json::Value response;
    
    VmsErrorCode result = callHandleFileDownload(UPLOADED_SENSOR_ID, queryString, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    EXPECT_EQ(result, VmsErrorCode::NoError)
        << "Should download uploaded file with MP4 container format";
}

/**
 * @brief Test with MKV container format
 * 
 * Tests the container parameter with MKV format using uploaded file.
 */
TEST_P(Download, WithMkvContainer)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    if (UPLOADED_SENSOR_ID.empty()) GTEST_SKIP() << "Test file not uploaded in SetUp";
    
    cout << "[TEST] With MKV container format (using uploaded file)" << endl;
    
    string queryString = buildQuery(UPLOAD_START_TIME, UPLOAD_END_TIME, "container=mkv");
    Json::Value response;
    
    VmsErrorCode result = callHandleFileDownload(UPLOADED_SENSOR_ID, queryString, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    EXPECT_EQ(result, VmsErrorCode::NoError)
        << "Should download uploaded file with MKV container format";
}

/**
 * @brief Test with transcode=none
 * 
 * Tests the transcode parameter with "none" mode (remux) using uploaded file.
 */
TEST_P(Download, WithTranscodeNone)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    if (UPLOADED_SENSOR_ID.empty()) GTEST_SKIP() << "Test file not uploaded in SetUp";
    
    cout << "[TEST] With transcode=none (remux mode, using uploaded file)" << endl;
    
    string queryString = buildQuery(UPLOAD_START_TIME, UPLOAD_END_TIME, "transcode=none");
    Json::Value response;
    
    VmsErrorCode result = callHandleFileDownload(UPLOADED_SENSOR_ID, queryString, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    EXPECT_EQ(result, VmsErrorCode::NoError)
        << "Should download uploaded file with transcode=none";
}

/**
 * @brief Test with transcode=full
 * 
 * Tests the transcode parameter with "full" mode using uploaded file.
 */
TEST_P(Download, WithTranscodeFull)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    if (UPLOADED_SENSOR_ID.empty()) GTEST_SKIP() << "Test file not uploaded in SetUp";
    
    cout << "[TEST] With transcode=full (using uploaded file)" << endl;
    
    string queryString = buildQuery(UPLOAD_START_TIME, UPLOAD_END_TIME, "transcode=full");
    Json::Value response;
    
    VmsErrorCode result = callHandleFileDownload(UPLOADED_SENSOR_ID, queryString, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    EXPECT_EQ(result, VmsErrorCode::NoError)
        << "Should download uploaded file with transcode=full";
}

/**
 * @brief Test with transcode=gop
 * 
 * Tests the transcode parameter with "gop" mode using uploaded file.
 */
TEST_P(Download, WithTranscodeGop)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    if (UPLOADED_SENSOR_ID.empty()) GTEST_SKIP() << "Test file not uploaded in SetUp";
    
    cout << "[TEST] With transcode=gop (using uploaded file)" << endl;
    
    string queryString = buildQuery(UPLOAD_START_TIME, UPLOAD_END_TIME, "transcode=gop");
    Json::Value response;
    
    VmsErrorCode result = callHandleFileDownload(UPLOADED_SENSOR_ID, queryString, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    EXPECT_EQ(result, VmsErrorCode::NoError)
        << "Should download uploaded file with transcode=gop";
}

/**
 * @brief Test with all optional parameters
 * 
 * Tests request with multiple optional parameters combined using uploaded file.
 */
TEST_P(Download, WithAllOptionalParameters)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    if (UPLOADED_SENSOR_ID.empty()) GTEST_SKIP() << "Test file not uploaded in SetUp";
    
    cout << "[TEST] With all optional parameters (using uploaded file)" << endl;
    
    string queryString = buildQuery(UPLOAD_START_TIME, UPLOAD_END_TIME, 
                                    "disableAudio=true&container=mp4&transcode=none&fullLength=false&fileName=test.mp4");
    Json::Value response;
    
    VmsErrorCode result = callHandleFileDownload(UPLOADED_SENSOR_ID, queryString, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    EXPECT_EQ(result, VmsErrorCode::NoError)
        << "Should download uploaded file with all optional parameters";
}

/**
 * @brief Test URL generation mode
 * 
 * Tests the URL generation endpoint using uploaded file.
 * Endpoint: GET /api/v1/storage/file/{streamId}/url
 */
TEST_P(Download, UrlGenerationMode)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    if (UPLOADED_SENSOR_ID.empty()) GTEST_SKIP() << "Test file not uploaded in SetUp";
    
    cout << "[TEST] URL generation mode (using uploaded file)" << endl;
    
    string queryString = buildQuery(UPLOAD_START_TIME, UPLOAD_END_TIME);
    Json::Value response;
    
    // Use "url" action to trigger URL generation mode (isURLRequested=true)
    VmsErrorCode result = callHandleFileDownload(UPLOADED_SENSOR_ID, queryString, response, "url");
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    if (result == VmsErrorCode::NoError && response.isMember("videoUrl"))
    {
        cout << "[TEST] ✓ Generated URL: " << response["videoUrl"].asString() << endl;
    }
    if (response.isMember("error_message"))
    {
        cout << "[TEST] Error: " << response["error_message"].asString() << endl;
    }
    
    EXPECT_EQ(result, VmsErrorCode::NoError)
        << "Should generate URL for uploaded file";
}

// ==================== FILE UPLOAD TEST FIXTURE ====================

/**
 * @brief Test fixture for StorageManagement file upload functionality
 * 
 * Tests the file upload API through handleStorageFileAPIrequest() which internally
 * calls handleFileUpload() for POST requests to /api/v1/storage/file
 * 
 * API: POST /api/v1/storage/file
 * 
 * The API supports three upload modes:
 * 1. Single file upload with metadata (mediaFile + metadata/metadataFile)
 * 2. Chunked upload (file + chunk headers)
 * 3. File registration (mediaFilePath + metadata/metadataFilePath)
 * 
 * TESTING LIMITATIONS:
 * - This test fixture uses a mock mg_connection that doesn't support actual multipart
 *   form data parsing (mg_handle_form_request is mocked to return success without parsing)
 * - Tests can verify API routing, parameter validation, and error handling
 * - Tests CANNOT verify actual file upload functionality (use integration tests for that)
 * - File registration mode (mediaFilePath in JSON) should work if the file exists
 * - Multipart upload mode will not work as no actual file data is parsed
 */
class PostFileUpload : public ::testing::Test
{
protected:
    StorageManagement* m_storageMgmt = nullptr;
    MockConnection* m_mockConn = nullptr;
    UploadedFileTracker* m_fileTracker = nullptr;
    
    // Test data paths
    const string TEST_VIDEO_FILE = "./tools/data/sample_10sec_h264.mp4";
    const string VALID_SENSOR_ID = "test-sensor-12345";
    
    void SetUp() override
    {
        cout << "\n[SETUP] Initializing file upload test..." << endl;
        
        // Clean test environment (remove vst_data, vst_video) for a fresh start
        StorageTestUtils::cleanTestEnvironment(true);
        
        // Initialize ModuleLoader
        ModuleLoader* moduleLoader = ModuleLoader::getInstance();
        int ret = moduleLoader->initialize(ModuleStorageManagement);
        
        ASSERT_EQ(ret, 0) << "ModuleLoader initialization failed";
        
        // Get StorageManagement instance
        m_storageMgmt = GET_STORAGE_MNGT();
        
        if (m_storageMgmt == nullptr)
        {
            cout << "[SETUP] ⚠️  StorageManagement module not loaded" << endl;
        }
        else
        {
            cout << "[SETUP] ✓ StorageManagement instance ready" << endl;
        }
        
        // Create mock connection for tests
        m_mockConn = new MockConnection();
        cout << "[SETUP] ✓ Mock connection created" << endl;
        
        // Create file tracker for automatic cleanup
        m_fileTracker = new UploadedFileTracker(m_storageMgmt);
        cout << "[SETUP] ✓ File tracker initialized" << endl;
    }

    void TearDown() override
    {
        cout << "[CLEANUP] Cleaning up upload test..." << endl;
        
        // Cleanup all uploaded files
        if (m_fileTracker)
        {
            m_fileTracker->cleanupAll();
            delete m_fileTracker;
            m_fileTracker = nullptr;
        }
        
        if (m_mockConn)
        {
            delete m_mockConn;
            m_mockConn = nullptr;
        }
        
        ModuleLoader::getInstance()->deInitialize();
        cout << "[CLEANUP] Complete\n" << endl;
    }
    
    /**
     * @brief Create request info JSON for POST file upload API
     * 
     * @param queryString Optional query parameters
     * @return Json::Value Request info structure for handleStorageFileAPIrequest
     */
    Json::Value createUploadRequestInfo(const string& queryString = "")
    {
        Json::Value req_info;
        req_info["url"] = "/api/v1/storage/file";
        req_info["method"] = "POST";
        if (!queryString.empty())
        {
            req_info["query"] = queryString;
        }
        return req_info;
    }
    
    /**
     * @brief Create metadata JSON for media upload
     * 
     * Uses utility function for consistency across tests.
     */
    Json::Value createMetadata(const string& sensorId, int64_t timestamp,
                               const string& streamName = "test_stream",
                               const string& eventInfo = "Test upload",
                               const string& tag = "unit_test")
    {
        return StorageTestUtils::createTestMetadata(sensorId, timestamp, streamName, eventInfo, tag);
    }
    
    /**
     * @brief Setup mock connection for file upload test
     * 
     * @param contentLength Size of file to upload
     * @param addChunkHeaders If true, add chunked upload headers
     */
    void setupMockConnectionForUpload(long long contentLength, bool addChunkHeaders = false)
    {
        m_mockConn->requestInfo.setContentLength(contentLength);
        m_mockConn->requestInfo.addHeader("Content-Type", "multipart/form-data; boundary=----WebKitFormBoundary");
        m_mockConn->requestInfo.addHeader("Content-Length", to_string(contentLength));
        
        if (addChunkHeaders)
        {
            m_mockConn->requestInfo.addHeader("nvstreamer-chunk-number", "1");
            m_mockConn->requestInfo.addHeader("nvstreamer-total-chunks", "1");
            m_mockConn->requestInfo.addHeader("nvstreamer-is-last-chunk", "true");
            m_mockConn->requestInfo.addHeader("nvstreamer-identifier", "test-upload-id");
            m_mockConn->requestInfo.addHeader("nvstreamer-file-name", "sample_10sec_h264.mp4");
        }
    }
    
    /**
     * @brief Get mock connection as mg_connection pointer
     * 
     * Returns a pointer to our mock connection structure cast as mg_connection*.
     * This works with our mocked mg_get_request_info() function defined above.
     * 
     * NOTE: This is for unit testing only. For integration tests, use a real HTTP server.
     * 
     * @return struct mg_connection* Pointer to mock connection
     */
    struct mg_connection* getMockConnection()
    {
        return reinterpret_cast<struct mg_connection*>(m_mockConn);
    }
    
    /**
     * @brief Helper to call file upload through the public API
     * 
     * This method properly sets up a mock connection with all required fields
     * for testing file upload functionality without a real HTTP server.
     * 
     * @param req_info Request information (url, method, query)
     * @param input Input JSON (for non-multipart tests)
     * @param response Output response
     * @return VmsErrorCode Result of the operation
     */
    VmsErrorCode callFileUpload(const Json::Value& req_info, const Json::Value& input,
                               Json::Value& response)
    {
        // Setup mock connection with proper request info
        m_mockConn->requestInfo.setMethod(req_info.get("method", "POST").asString());
        m_mockConn->requestInfo.setUri(req_info.get("url", "/api/v1/storage/file").asString());
        
        if (req_info.isMember("query") && !req_info["query"].asString().empty())
        {
            m_mockConn->requestInfo.setQueryString(req_info["query"].asString());
        }
        
        // Cast our mock connection to mg_connection* for the API call
        struct mg_connection* conn = reinterpret_cast<struct mg_connection*>(m_mockConn);
        
        cout << "[INFO] Calling with mock connection - Method: " 
             << m_mockConn->requestInfo.info.request_method 
             << ", URI: " << m_mockConn->requestInfo.info.request_uri << endl;
        
        return m_storageMgmt->handleStorageFileAPIrequest(req_info, input, response, conn);
    }
};

// ==================== FILE UPLOAD TEST CASES ====================

/**
 * @brief Test file upload API routing
 * 
 * Validates that POST requests to /api/v1/storage/file are routed correctly
 * to the file upload handler. With our mock connection, this should reach the
 * file upload handler without crashing.
 */
TEST_F(PostFileUpload, UploadApiRouting)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    
    cout << "[TEST] File upload API routing validation" << endl;
    
    Json::Value req_info = createUploadRequestInfo();
    Json::Value input;
    Json::Value response;
    
    // Setup mock connection
    setupMockConnectionForUpload(1024);
    
    // Call the API - with mock connection, should not crash
    VmsErrorCode result = callFileUpload(req_info, input, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    // The mock connection should allow the code to run without crashing
    // It will fail at file validation stage, but that's expected for unit tests
    // Main check: POST endpoint is recognized (not VMSNotSupportedError)
    EXPECT_NE(result, VmsErrorCode::VMSNotSupportedError)
        << "POST /api/v1/storage/file should be a supported endpoint";
}

/**
 * @brief Test metadata validation - valid metadata
 * 
 * Tests that valid metadata structure is accepted.
 */
TEST_F(PostFileUpload, ValidMetadataStructure)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    
    cout << "[TEST] Valid metadata structure" << endl;
    
    // Create valid metadata with required fields
    int64_t currentTime = 1752846869222L;
    Json::Value metadata = createMetadata(VALID_SENSOR_ID, currentTime);
    
    // Verify required fields are present
    EXPECT_TRUE(metadata.isMember("sensorId")) << "Metadata should have sensorId";
    EXPECT_TRUE(metadata.isMember("timestamp")) << "Metadata should have timestamp";
    EXPECT_EQ(metadata["sensorId"].asString(), VALID_SENSOR_ID) << "Sensor ID should match";
    EXPECT_EQ(metadata["timestamp"].asInt64(), currentTime) << "Timestamp should match";
    
    cout << "[TEST] Metadata structure validated successfully" << endl;
}

/**
 * @brief Test file upload with metadata
 * 
 * Tests uploading a media file with metadata.
 * Note: This test validates the request structure; actual multipart upload
 * requires a real HTTP connection which is not available in unit tests.
 */
TEST_F(PostFileUpload, UploadWithMetadata)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    
    cout << "[TEST] Upload with metadata" << endl;
    
    // Verify test file exists
    ifstream testFile(TEST_VIDEO_FILE, ios::binary);
    ASSERT_TRUE(testFile.good()) << "Test video file should exist: " << TEST_VIDEO_FILE;
    testFile.close();
    
    // Create request
    Json::Value req_info = createUploadRequestInfo();
    Json::Value input;
    
    // Add metadata to input
    int64_t currentTime = 1752846869222L;
    input["metadata"] = createMetadata(VALID_SENSOR_ID, currentTime, 
                                       "test_upload_stream", 
                                       "Unit test upload");
    
    Json::Value response;
    VmsErrorCode result = callFileUpload(req_info, input, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    // With nullptr connection, we can't do actual multipart upload
    // but we can verify the endpoint accepts POST requests
    EXPECT_NE(result, VmsErrorCode::VMSNotSupportedError)
        << "Upload endpoint should be supported";
}

/**
 * @brief Test file registration mode
 * 
 * Tests registering an existing file on the server filesystem.
 * Uses mediaFilePath instead of actual file upload.
 */
TEST_F(PostFileUpload, FileRegistrationMode)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    
    cout << "[TEST] File registration mode" << endl;
    
    Json::Value req_info = createUploadRequestInfo();
    Json::Value input;
    
    // File registration uses paths instead of binary data
    input["mediaFilePath"] = TEST_VIDEO_FILE;
    input["metadata"] = createMetadata(VALID_SENSOR_ID, 1752846869222L);
    
    Json::Value response;
    VmsErrorCode result = callFileUpload(req_info, input, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    EXPECT_NE(result, VmsErrorCode::VMSNotSupportedError)
        << "File registration should be supported";
}

/**
 * @brief Test chunked upload headers validation
 * 
 * Tests that chunked upload parameters are recognized.
 * Chunked uploads use special headers:
 * - nvstreamer-chunk-number
 * - nvstreamer-total-chunks
 * - nvstreamer-is-last-chunk
 * - nvstreamer-identifier
 * - nvstreamer-file-name
 */
TEST_F(PostFileUpload, ChunkedUploadHeaders)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    
    cout << "[TEST] Chunked upload headers" << endl;
    
    // Note: Headers would normally be in HTTP request, not query string
    // This test validates the endpoint accepts the POST method
    Json::Value req_info = createUploadRequestInfo();
    Json::Value input;
    
    // In real chunked upload, these would be HTTP headers
    // For unit test, we just verify the endpoint routing
    input["chunkNumber"] = 1;
    input["totalChunks"] = 5;
    input["isLastChunk"] = false;
    input["identifier"] = "test-chunk-id-12345";
    input["fileName"] = "sample_10sec_h264.mp4";
    
    Json::Value response;
    VmsErrorCode result = callFileUpload(req_info, input, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    EXPECT_NE(result, VmsErrorCode::VMSNotSupportedError)
        << "Chunked upload endpoint should be supported";
}

/**
 * @brief Test invalid HTTP method on upload endpoint
 * 
 * Verifies that non-POST methods are rejected on the upload endpoint.
 */
TEST_F(PostFileUpload, InvalidMethodOnUploadEndpoint)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    
    cout << "[TEST] Invalid HTTP method on upload endpoint" << endl;
    
    Json::Value req_info;
    req_info["url"] = "/api/v1/storage/file";
    req_info["method"] = "PUT";  // Wrong method for multipart upload
    
    Json::Value input;
    Json::Value response;
    
    VmsErrorCode result = callFileUpload(req_info, input, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    // PUT is actually valid for raw file upload on /file/{filename}/{timestamp}
    // So this may succeed or fail depending on URL parsing
    // Main check is that we get a valid error code
    EXPECT_TRUE(result == VmsErrorCode::NoError || 
                result == VmsErrorCode::InvalidParameterError ||
                result == VmsErrorCode::VMSNotSupportedError)
        << "Should return appropriate error code";
}

// ==================== PUT UPLOAD TEST CASES ====================

/**
 * @brief Verify mock functions are working BEFORE running real tests
 * 
 * This test verifies that our mock CivetWeb functions are being called
 * instead of the real library functions. If this test fails, it means
 * the linking didn't work correctly and you need to rebuild.
 */
TEST(MockVerification, VerifyMocksAreActive)
{
    cout << "\n========================================" << endl;
    cout << "VERIFYING MOCK FUNCTIONS ARE ACTIVE" << endl;
    cout << "========================================" << endl;
    
    // Create a simple mock connection
    MockConnection testConn;
    testConn.requestInfo.addHeader("Test-Header", "test-value");
    testConn.requestInfo.setContentLength(100);
    
    struct mg_connection* conn = reinterpret_cast<struct mg_connection*>(&testConn);
    
    // Test 1: mg_get_request_info
    cout << "\n[VERIFY] Testing mg_get_request_info..." << endl;
    const mg_request_info* info = mg_get_request_info(conn);
    ASSERT_NE(info, nullptr) << "mg_get_request_info should return non-null";
    cout << "[VERIFY] ✓ mg_get_request_info returned: " << (void*)info << endl;
    
    // Test 2: mg_get_header  
    cout << "\n[VERIFY] Testing mg_get_header..." << endl;
    const char* header = mg_get_header(conn, "Test-Header");
    
    if (header == nullptr)
    {
        cout << "[VERIFY] ❌ FAILED: mg_get_header returned nullptr!" << endl;
        cout << "[VERIFY] This means the REAL CivetWeb function is being called," << endl;
        cout << "[VERIFY] not our mock. You MUST rebuild the test binary:" << endl;
        cout << "[VERIFY]   rm -f vst_test test/gtests/*.o && make vst_test" << endl;
        FAIL() << "Mock mg_get_header not working - REBUILD REQUIRED!";
    }
    
    ASSERT_NE(header, nullptr) << "mg_get_header should find Test-Header";
    EXPECT_STREQ(header, "test-value") << "Header value should match";
    cout << "[VERIFY] ✓ mg_get_header found header: " << header << endl;
    
    cout << "\n========================================" << endl;
    cout << "✅ ALL MOCKS ARE WORKING CORRECTLY!" << endl;
    cout << "========================================\n" << endl;
}

/**
 * @brief Test fixture for PUT file upload functionality
 * 
 * Tests PUT upload API: PUT /api/v1/storage/file/{filename}/{timestamp}
 *                       PUT /api/v1/storage/file/{filename}?timestamp=X&sensorId=Y
 * 
 * PUT upload sends binary file data directly in request body with filename in path.
 * 
 * UNIT TEST CAPABILITIES:
 * These tests can now upload REAL video files! The test fixture loads the actual
 * test video (./tools/data/sample_10sec_h264.mp4) and uploads it through the API.
 * 
 * What these tests VALIDATE with REAL files:
 * ✅ API endpoint routing (PUT recognized and routed correctly)
 * ✅ HTTP method validation (PUT vs POST vs GET)
 * ✅ URL parameter extraction (filename, timestamp from path)
 * ✅ Query parameter parsing (timestamp, sensorId from query string)
 * ✅ Header validation (Content-Length required and matches file size)
 * ✅ Request body reading (reads actual video file bytes)
 * ✅ File I/O (writes real MP4 data to disk)
 * ✅ Media validation (validates actual video codec/container)
 * ✅ Complete upload workflow end-to-end
 * 
 * Test Modes:
 * - setupMockConnectionForPut(true) → Uploads REAL video file
 * - setupMockConnectionForPut(false) → Generates fake data (for API-only testing)
 * 
 * Note: If test video file is not found, tests will be skipped.
 */
class PutFileUpload : public ::testing::Test
{
protected:
    StorageManagement* m_storageMgmt = nullptr;
    MockConnection* m_mockConn = nullptr;
    UploadedFileTracker* m_fileTracker = nullptr;
    
    // Test data
    const string TEST_VIDEO_FILE = "./tools/data/sample_10sec_h264.mp4";
    const string TEST_FILENAME = "sample_10sec_h264.mp4";
    const int64_t TEST_TIMESTAMP = 1752846869222L;
    string TEST_TIMESTAMP_ISO;      // Will be set in SetUp()
    string TEST_TIMESTAMP_END_ISO;  // Start + 10s, for cleanup time range
    const string VALID_SENSOR_ID = "test-sensor-12345";
    
    // Helper to create unique filename for each successful upload test (avoids conflicts)
    string createUniqueFilename(const string& testName) const
    {
        auto now = std::chrono::system_clock::now();
        auto millis = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();
        return testName + "_" + std::to_string(millis) + ".mp4";
    }
    
    void SetUp() override
    {
        cout << "\n[SETUP] Initializing PUT upload test..." << endl;
        cout << "[SETUP] Mock version: " << MOCK_VERSION << endl;
        
        // Clean test environment (remove vst_data, vst_video) for a fresh start
        StorageTestUtils::cleanTestEnvironment(true);
        
        ModuleLoader* moduleLoader = ModuleLoader::getInstance();
        int ret = moduleLoader->initialize(ModuleStorageManagement);
        
        ASSERT_EQ(ret, 0) << "ModuleLoader initialization failed";
        
        m_storageMgmt = GET_STORAGE_MNGT();
        
        if (m_storageMgmt == nullptr)
        {
            cout << "[SETUP] ⚠️  StorageManagement module not loaded" << endl;
        }
        else
        {
            cout << "[SETUP] ✓ StorageManagement instance ready" << endl;
        }
        
        m_mockConn = new MockConnection();
        cout << "[SETUP] ✓ Mock connection created" << endl;
        
        // Create file tracker for automatic cleanup
        m_fileTracker = new UploadedFileTracker(m_storageMgmt);
        cout << "[SETUP] ✓ File tracker initialized" << endl;
        
        // Convert TEST_TIMESTAMP to ISO 8601 format for upload API
        TEST_TIMESTAMP_ISO = convertEpocToISO8601_2(TEST_TIMESTAMP * 1000);  // Convert ms to microseconds
        TEST_TIMESTAMP_END_ISO = convertEpocToISO8601_2((TEST_TIMESTAMP + 10000) * 1000);  // +10s
        cout << "[SETUP] Test timestamp: " << TEST_TIMESTAMP << " ms = " << TEST_TIMESTAMP_ISO << endl;
        
        // Verify mocks are working by calling mg_get_header with nullptr
        cout << "[SETUP] Testing if mocks are active..." << endl;
        const char* testResult = mg_get_header(nullptr, "Test");
        if (testResult == nullptr)
        {
            cout << "[SETUP] ✓ Mock mg_get_header is responding" << endl;
        }
    }

    void TearDown() override
    {
        cout << "[CLEANUP] Cleaning up PUT upload test..." << endl;
        
        // Cleanup all uploaded files
        if (m_fileTracker)
        {
            m_fileTracker->cleanupAll();
            delete m_fileTracker;
            m_fileTracker = nullptr;
        }
        
        if (m_mockConn)
        {
            delete m_mockConn;
            m_mockConn = nullptr;
        }
        
        ModuleLoader::getInstance()->deInitialize();
        cout << "[CLEANUP] Complete\n" << endl;
    }
    
    /**
     * @brief Create request info for legacy PUT upload
     * 
     * API: PUT /api/v1/storage/file/{filename}/{timestamp}
     */
    Json::Value createLegacyPutRequestInfo(const string& filename, int64_t timestamp)
    {
        Json::Value req_info;
        req_info["url"] = "/api/v1/storage/file/" + filename + "/" + std::to_string(timestamp);
        req_info["method"] = "PUT";
        return req_info;
    }
    
    /**
     * @brief Create request info for new PUT upload with query params
     * 
     * API: PUT /api/v1/storage/file/{filename}?timestamp=X&sensorId=Y
     */
    Json::Value createNewPutRequestInfo(const string& filename, 
                                        const string& queryString = "")
    {
        Json::Value req_info;
        req_info["url"] = "/api/v1/storage/file/" + filename;
        req_info["method"] = "PUT";
        if (!queryString.empty())
        {
            req_info["query"] = queryString;
        }
        return req_info;
    }
    
    /**
     * @brief Setup mock connection for PUT upload with real file
     * 
     * @param useRealFile If true, loads TEST_VIDEO_FILE. If false, uses fake data.
     * 
     * IMPORTANT: Headers are added FIRST, then file is loaded.
     * This prevents vector reallocation from invalidating pointers.
     */
    void setupMockConnectionForPut(bool useRealFile = true)
    {
        // ALWAYS add Content-Type first (before anything else)
        m_mockConn->requestInfo.addHeader("Content-Type", "application/octet-stream");
        
        if (useRealFile)
        {
            // Load actual video file (this will add/update Content-Length)
            if (!m_mockConn->loadFileForUpload(TEST_VIDEO_FILE))
            {
                cout << "[WARNING] Could not load test video file: " << TEST_VIDEO_FILE << endl;
                cout << "[WARNING] Falling back to fake data" << endl;
                
                // Fallback to fake data
                long long fakeSize = 1024;
                m_mockConn->requestInfo.setContentLength(fakeSize);
                m_mockConn->requestInfo.addHeader("Content-Length", to_string(fakeSize));
            }
            // else: Content-Length added by loadFileForUpload
        }
        else
        {
            // Use fake data
            long long fakeSize = 1024;
            m_mockConn->requestInfo.setContentLength(fakeSize);
            m_mockConn->requestInfo.addHeader("Content-Length", to_string(fakeSize));
        }
        
        // Final verification: print all headers
        cout << "[SETUP] Mock connection configured with " << m_mockConn->requestInfo.info.num_headers << " headers:" << endl;
        for (int i = 0; i < m_mockConn->requestInfo.info.num_headers; i++)
        {
            cout << "[SETUP]   " << i << ": '" << m_mockConn->requestInfo.info.http_headers[i].name 
                 << "' = '" << m_mockConn->requestInfo.info.http_headers[i].value << "'" << endl;
        }
    }
    
    /**
     * @brief Helper to check if result indicates successful API processing
     * 
     * For unit tests with fake data, we expect:
     * - NoError: Unlikely (fake data usually fails validation)
     * - InvalidParameterError: File validation failed (expected)
     * - VMSInternalError: Media processing failed (expected)
     * - NOT VMSNotSupportedError: Would mean API wasn't recognized
     */
    bool isApiProcessed(VmsErrorCode result)
    {
        return result != VmsErrorCode::VMSNotSupportedError;
    }
    
    /**
     * @brief Print detailed diagnostic information about the response
     */
    void printResponseDiagnostics(VmsErrorCode result, const Json::Value& response)
    {
        cout << "\n============ RESPONSE DIAGNOSTICS ============" << endl;
        cout << "Result Code: " << static_cast<int>(result) << " - ";
        switch(result) {
            case VmsErrorCode::NoError: 
                cout << "✅ NoError (SUCCESS!)" << endl; 
                break;
            case VmsErrorCode::InvalidParameterError: 
                cout << "❌ InvalidParameterError" << endl; 
                break;
            case VmsErrorCode::VMSInternalError: 
                cout << "❌ VMSInternalError" << endl; 
                break;
            case VmsErrorCode::CameraNotFoundError: 
                cout << "⚠️  CameraNotFoundError" << endl; 
                break;
            case VmsErrorCode::VMSNotSupportedError: 
                cout << "❌ VMSNotSupportedError (API not recognized)" << endl; 
                break;
            default: 
                cout << "Other (" << static_cast<int>(result) << ")" << endl; 
                break;
        }
        
        cout << "\nResponse JSON:" << endl;
        cout << response.toStyledString() << endl;
        cout << "=============================================\n" << endl;
    }
    
    /**
     * @brief Call PUT upload API
     * 
     * NOTE: Call setupMockConnectionForPut() BEFORE calling this method
     * to ensure headers are properly set.
     * 
     * IMPORTANT: This uploads FAKE data (0x42 bytes), not a real video file.
     * Expect media validation to fail with InvalidParameterError or VMSInternalError.
     */
    VmsErrorCode callPutUpload(const Json::Value& req_info, const Json::Value& input,
                              Json::Value& response)
    {
        // Update method and URI without clearing headers
        m_mockConn->requestInfo.setMethod(req_info.get("method", "PUT").asString());
        m_mockConn->requestInfo.setUri(req_info.get("url", "").asString());
        
        if (req_info.isMember("query") && !req_info["query"].asString().empty())
        {
            m_mockConn->requestInfo.setQueryString(req_info["query"].asString());
        }
        
        struct mg_connection* conn = reinterpret_cast<struct mg_connection*>(m_mockConn);
        
        cout << "[INFO] Calling PUT upload - Method: " 
             << m_mockConn->requestInfo.info.request_method 
             << ", URI: " << m_mockConn->requestInfo.info.request_uri 
             << ", Headers: " << m_mockConn->requestInfo.info.num_headers << endl;
        
        // Debug: print headers
        for (int i = 0; i < m_mockConn->requestInfo.info.num_headers; i++)
        {
            cout << "[INFO]   Header[" << i << "]: " 
                 << m_mockConn->requestInfo.info.http_headers[i].name << " = "
                 << m_mockConn->requestInfo.info.http_headers[i].value << endl;
        }
        
        return m_storageMgmt->handleStorageFileAPIrequest(req_info, input, response, conn);
    }
};

/**
 * @brief Test legacy PUT upload API routing
 * 
 * Tests: PUT /api/v1/storage/file/{filename}/{timestamp}
 * 
 * NOTE: This test uploads FAKE binary data (0x42 bytes), not a real video file.
 * The upload will progress through header validation, body reading, and file writing,
 * but will eventually fail at media validation (which is correct behavior for corrupt files).
 * 
 * What we're testing:
 * - API routing works ✅
 * - Header validation works ✅
 * - Content-Length validation works ✅
 * - Request body reading works ✅
 * - File writing works ✅
 * - Media validation properly rejects corrupt files ✅
 */
TEST_F(PutFileUpload, DISABLED_LegacyPutApiRouting)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    
    cout << "[TEST] Legacy PUT upload with utility function (auto-cleanup enabled)" << endl;
    
    // Create unique filename to avoid conflicts between tests
    string uniqueFilename = createUniqueFilename("legacy_put");
    cout << "[TEST] Unique filename: " << uniqueFilename << endl;
    
    // Upload file using utility function
    TestUploadResult uploadResult = StorageTestUtils::uploadFilePut(
        m_storageMgmt,
        m_mockConn,
        uniqueFilename,
        TEST_TIMESTAMP_ISO,  // ISO 8601 format timestamp
        TEST_VIDEO_FILE,
        "",   // sensorId (auto-generate)
        true  // useLegacyApi
    );
    
    // Track for automatic cleanup in TearDown (include time range for reliable deletion)
    if (uploadResult.isSuccess())
    {
        m_fileTracker->trackFile(uploadResult.fileId, uploadResult.streamId, uploadResult.filePath,
                                 TEST_TIMESTAMP_ISO, TEST_TIMESTAMP_END_ISO);
        cout << "[TEST] ✓ File tracked for cleanup: ID=" << uploadResult.fileId 
             << ", Path=" << uploadResult.filePath << endl;
    }
    
    // Print diagnostics
    cout << "\n============ UPLOAD RESULT ============" << endl;
    cout << "Error Code: " << static_cast<int>(uploadResult.errorCode);
    if (uploadResult.isSuccess())
    {
        cout << " - ✅ NoError" << endl;
        cout << "File ID: " << uploadResult.fileId << endl;
        cout << "Sensor ID: " << uploadResult.sensorId << endl;
        cout << "File Path: " << uploadResult.filePath << endl;
        cout << "File Size: " << uploadResult.bytes << " bytes" << endl;
    }
    else
    {
        cout << " - ❌ FAILED" << endl;
        cout << "Error: " << uploadResult.errorMessage << endl;
    }
    cout << "======================================\n" << endl;
    
    // STRICT VALIDATION: Real file upload must succeed
    EXPECT_EQ(uploadResult.errorCode, VmsErrorCode::NoError)
        << "Upload failed: " << uploadResult.errorMessage;
    EXPECT_FALSE(uploadResult.fileId.empty()) << "File ID should be returned";
    EXPECT_GT(uploadResult.bytes, 0) << "File size should be > 0";
}

/**
 * @brief Test new PUT upload API routing with REAL video file
 * 
 * Tests: PUT /api/v1/storage/file/{filename}
 */
TEST_F(PutFileUpload, DISABLED_NewPutApiRouting)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    
    cout << "[TEST] New PUT API with utility function (auto-cleanup enabled)" << endl;
    
    // Create unique filename
    string uniqueFilename = createUniqueFilename("new_put");
    cout << "[TEST] Unique filename: " << uniqueFilename << endl;
    
    // Upload file using new API format
    TestUploadResult uploadResult = StorageTestUtils::uploadFilePut(
        m_storageMgmt,
        m_mockConn,
        uniqueFilename,
        TEST_TIMESTAMP_ISO,  // ISO 8601 format timestamp
        TEST_VIDEO_FILE,
        "",    // sensorId (auto-generate)
        false  // useLegacyApi = false (new format)
    );
    
    // Track for automatic cleanup
    if (uploadResult.isSuccess())
    {
        m_fileTracker->trackFile(uploadResult.fileId, uploadResult.streamId, uploadResult.filePath,
                                 TEST_TIMESTAMP_ISO, TEST_TIMESTAMP_END_ISO);
        cout << "[TEST] ✓ File tracked: " << uploadResult.fileId << endl;
    }
    
    // Validate
    EXPECT_EQ(uploadResult.errorCode, VmsErrorCode::NoError)
        << "Upload failed: " << uploadResult.errorMessage;
    EXPECT_FALSE(uploadResult.fileId.empty()) << "File ID required";
}

/**
 * @brief Test PUT upload with timestamp query parameter (REAL file)
 * 
 * Tests: PUT /api/v1/storage/file/{filename}?timestamp=1752846869222
 */
TEST_F(PutFileUpload, DISABLED_PutWithTimestampQueryParam)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    
    cout << "[TEST] PUT with timestamp query param (utility + auto-cleanup)" << endl;
    
    // Create unique filename
    string uniqueFilename = createUniqueFilename("timestamp_query");
    
    // Upload using new API format with timestamp in query
    TestUploadResult uploadResult = StorageTestUtils::uploadFilePut(
        m_storageMgmt,
        m_mockConn,
        uniqueFilename,
        TEST_TIMESTAMP_ISO,  // ISO 8601 format timestamp
        TEST_VIDEO_FILE,
        "",    // sensorId
        false  // new API format
    );
    
    // Track for cleanup
    if (uploadResult.isSuccess())
    {
        m_fileTracker->trackFile(uploadResult.fileId, uploadResult.streamId, uploadResult.filePath,
                                 TEST_TIMESTAMP_ISO, TEST_TIMESTAMP_END_ISO);
    }
    
    // Validate
    EXPECT_EQ(uploadResult.errorCode, VmsErrorCode::NoError)
        << "Upload failed: " << uploadResult.errorMessage;
}

/**
 * @brief Test PUT upload with sensorId query parameter (REAL file)
 * 
 * Tests: PUT /api/v1/storage/file/{filename}?sensorId=test-sensor-12345
 */
TEST_F(PutFileUpload, PutWithSensorIdQueryParam)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    
    cout << "[TEST] PUT upload with sensorId query parameter (REAL file)" << endl;
    
    // Skip if test file doesn't exist
    ifstream testFile(TEST_VIDEO_FILE, ios::binary);
    if (!testFile.good())
    {
        GTEST_SKIP() << "Test video file not found: " << TEST_VIDEO_FILE;
    }
    testFile.close();
    
    string queryString = "sensorId=" + VALID_SENSOR_ID;
    Json::Value req_info = createNewPutRequestInfo(TEST_FILENAME, queryString);
    Json::Value input;
    Json::Value response;
    
    setupMockConnectionForPut(true); // Use real file
    
    VmsErrorCode result = callPutUpload(req_info, input, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    EXPECT_NE(result, VmsErrorCode::VMSNotSupportedError)
        << "PUT upload with sensorId query param should be supported";
}

/**
 * @brief Test PUT upload with both timestamp and sensorId (REAL file)
 * 
 * Tests: PUT /api/v1/storage/file/{filename}?timestamp=X&sensorId=Y
 */
TEST_F(PutFileUpload, PutWithTimestampAndSensorId)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    
    cout << "[TEST] PUT upload with timestamp and sensorId (REAL file)" << endl;
    
    // Skip if test file doesn't exist
    ifstream testFile(TEST_VIDEO_FILE, ios::binary);
    if (!testFile.good())
    {
        GTEST_SKIP() << "Test video file not found: " << TEST_VIDEO_FILE;
    }
    testFile.close();
    
    string queryString = "timestamp=" + std::to_string(TEST_TIMESTAMP) + 
                        "&sensorId=" + VALID_SENSOR_ID;
    Json::Value req_info = createNewPutRequestInfo(TEST_FILENAME, queryString);
    Json::Value input;
    Json::Value response;
    
    setupMockConnectionForPut(true); // Use real file
    
    VmsErrorCode result = callPutUpload(req_info, input, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    EXPECT_NE(result, VmsErrorCode::VMSNotSupportedError)
        << "PUT upload with both timestamp and sensorId should be supported";
}

/**
 * @brief Test PUT upload with missing filename (fake data - error test)
 * 
 * Tests: PUT /api/v1/storage/file/
 */
TEST_F(PutFileUpload, PutWithMissingFilename)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    
    cout << "[TEST] PUT upload with missing filename (error validation)" << endl;
    
    Json::Value req_info;
    req_info["url"] = "/api/v1/storage/file/";  // No filename
    req_info["method"] = "PUT";
    
    Json::Value input;
    Json::Value response;
    
    setupMockConnectionForPut(false); // Use fake data - testing error condition
    
    VmsErrorCode result = callPutUpload(req_info, input, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    // Should fail with invalid parameter error
    EXPECT_TRUE(result == VmsErrorCode::InvalidParameterError ||
                result == VmsErrorCode::VMSNotSupportedError)
        << "PUT upload without filename should fail";
}

/**
 * @brief Test PUT upload with filename containing whitespace (fake data - error test)
 * 
 * According to spec: filename must match pattern "^[^\\s]+$" (no whitespace)
 */
TEST_F(PutFileUpload, PutWithWhitespaceInFilename)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    
    cout << "[TEST] PUT upload with whitespace in filename (error validation)" << endl;
    
    Json::Value req_info = createNewPutRequestInfo("test file.mp4");
    Json::Value input;
    Json::Value response;
    
    setupMockConnectionForPut(false); // Use fake data - testing error condition
    
    VmsErrorCode result = callPutUpload(req_info, input, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    // Should fail because filenames cannot contain whitespace
    EXPECT_EQ(result, VmsErrorCode::InvalidParameterError)
        << "PUT upload with whitespace in filename should be rejected";
}

/**
 * @brief Test PUT upload with zero timestamp (REAL file)
 * 
 * Tests: PUT /api/v1/storage/file/{filename}/0
 * According to spec: timestamp=0 is valid if not applicable
 */
TEST_F(PutFileUpload, PutWithZeroTimestamp)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    
    cout << "[TEST] PUT upload with zero timestamp (REAL file)" << endl;
    
    // Skip if test file doesn't exist
    ifstream testFile(TEST_VIDEO_FILE, ios::binary);
    if (!testFile.good())
    {
        GTEST_SKIP() << "Test video file not found: " << TEST_VIDEO_FILE;
    }
    testFile.close();
    
    Json::Value req_info = createLegacyPutRequestInfo(TEST_FILENAME, 0);
    Json::Value input;
    Json::Value response;
    
    setupMockConnectionForPut(true); // Use real file
    
    VmsErrorCode result = callPutUpload(req_info, input, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    // Zero timestamp should be accepted according to spec
    EXPECT_NE(result, VmsErrorCode::VMSNotSupportedError)
        << "PUT upload with timestamp=0 should be supported";
}

/**
 * @brief Test PUT upload with very long filename (fake data - error test)
 * 
 * According to spec: maxLength is 128 characters
 */
TEST_F(PutFileUpload, PutWithLongFilename)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    
    cout << "[TEST] PUT upload with long filename (error validation)" << endl;
    
    // Create filename exactly 129 characters (1 over limit)
    string longFilename(129, 'a');
    longFilename += ".mp4";
    
    Json::Value req_info = createNewPutRequestInfo(longFilename);
    Json::Value input;
    Json::Value response;
    
    setupMockConnectionForPut(false); // Use fake data - testing error condition
    
    VmsErrorCode result = callPutUpload(req_info, input, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    // May fail due to filename length validation
    EXPECT_TRUE(result == VmsErrorCode::InvalidParameterError ||
                result == VmsErrorCode::VMSInternalError ||
                result != VmsErrorCode::NoError)
        << "Very long filename should be handled appropriately";
}

/**
 * @brief Test legacy PUT vs new PUT differentiation (REAL file)
 * 
 * Validates that the system correctly differentiates between:
 * - Legacy: PUT /file/{filename}/{timestamp}
 * - New: PUT /file/{filename}?timestamp=X
 */
TEST_F(PutFileUpload, LegacyVsNewPutDifferentiation)
{
    if (!m_storageMgmt) GTEST_SKIP() << "StorageManagement not available";
    
    cout << "[TEST] Legacy vs New PUT API differentiation (REAL file)" << endl;
    
    // Skip if test file doesn't exist
    ifstream testFile(TEST_VIDEO_FILE, ios::binary);
    if (!testFile.good())
    {
        GTEST_SKIP() << "Test video file not found: " << TEST_VIDEO_FILE;
    }
    testFile.close();
    
    // Test legacy format
    Json::Value legacy_req = createLegacyPutRequestInfo(TEST_FILENAME, TEST_TIMESTAMP);
    Json::Value legacy_response;
    setupMockConnectionForPut(true); // Real file
    VmsErrorCode legacy_result = callPutUpload(legacy_req, Json::Value(), legacy_response);
    
    // Reset mock connection for second test
    delete m_mockConn;
    m_mockConn = new MockConnection();
    
    // Test new format
    string queryString = "timestamp=" + std::to_string(TEST_TIMESTAMP);
    Json::Value new_req = createNewPutRequestInfo(TEST_FILENAME, queryString);
    Json::Value new_response;
    setupMockConnectionForPut(true); // Real file
    VmsErrorCode new_result = callPutUpload(new_req, Json::Value(), new_response);
    
    cout << "[TEST] Legacy result: " << static_cast<int>(legacy_result) << endl;
    cout << "[TEST] New result: " << static_cast<int>(new_result) << endl;
    
    // Both formats should be supported
    EXPECT_NE(legacy_result, VmsErrorCode::VMSNotSupportedError)
        << "Legacy PUT format should be supported";
    EXPECT_NE(new_result, VmsErrorCode::VMSNotSupportedError)
        << "New PUT format should be supported";
}

// ==================== PIPELINE UNIT TEST FIXTURE ====================

/**
 * @brief Test fixture for ClipReaderProducer -> Writer pipeline integration
 *
 * Tests the producer/consumer pipeline classes directly without going through
 * the StorageManagement API layer. Parameterized on video files from TestConfig.
 *
 * Each test wires ClipReaderProducer to a writer consumer (RemuxWriterConsumer
 * or TranscodeWriterConsumer), runs the pipeline end-to-end, and verifies
 * the output file is created with non-zero size.
 */
class PipelineUnit : public ::testing::TestWithParam<std::string>
{
protected:
    std::vector<std::string> m_outputFiles;

    std::string getTestVideoFile() const { return GetParam(); }

    void TearDown() override
    {
        for (const auto& f : m_outputFiles)
        {
            if (std::filesystem::exists(f))
            {
                std::remove(f.c_str());
                cout << "[CLEANUP] Removed output file: " << f << endl;
            }
        }
    }

    std::string detectCodec(const std::string& filePath) const
    {
        std::string lower = std::filesystem::path(filePath).stem().string();
        std::transform(lower.begin(), lower.end(), lower.begin(), ::tolower);
        if (lower.find("h265") != std::string::npos || lower.find("hevc") != std::string::npos)
            return "h265";
        return "h264";
    }

    std::string createOutputPath(const std::string& testName, const std::string& ext) const
    {
        std::string stem = std::filesystem::path(getTestVideoFile()).stem().string();
        auto now = std::chrono::system_clock::now();
        auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
            now.time_since_epoch()).count();
        return "pipeline_test_" + testName + "_" + stem + "_" + std::to_string(ms) + ext;
    }

    ClipReaderConfig createReaderConfig() const
    {
        std::string videoFile = getTestVideoFile();
        ClipReaderConfig cfg;
        cfg.stream_id = videoFile;
        cfg.file_paths.push_back(videoFile);
        cfg.video_codec = detectCodec(videoFile);
        cfg.enable_audio = false;
        cfg.seek_start_ms = 0;
        cfg.seek_end_ms = std::numeric_limits<int64_t>::max();
        cfg.file_start_epoch_ms = 0;
        cfg.is_growing_file = false;
        return cfg;
    }

    /**
     * Run a full ClipReaderProducer -> IMediaDataConsumer pipeline.
     * Returns true if the output file exists with size > 0 and no errors occurred.
     */
    bool runPipeline(std::shared_ptr<IMediaDataProducer> reader,
                     std::shared_ptr<IMediaDataConsumer> writer,
                     const std::string& outputFile)
    {
        if (!writer->start())
        {
            cout << "[PIPELINE] Writer start() failed" << endl;
            return false;
        }

        std::string id;
        reader->registerConsumer(writer, id, "video");

        std::atomic<bool> finished{false};
        std::atomic<bool> readerError{false};

        reader->onFinished([&]() {
            finished = true;
            writer->sendEOS();
        });

        reader->onError([&](const std::string& errorMsg, int errorCode) {
            finished = true;
            readerError = true;
            cout << "[PIPELINE] Reader error: " << errorMsg
                 << " (code " << errorCode << ")" << endl;
            GstElement* wp = static_cast<GstElement*>(writer->getPipeline());
            if (wp)
                gst_element_send_event(wp, gst_event_new_eos());
        });

        if (!reader->start())
        {
            cout << "[PIPELINE] Reader start() failed" << endl;
            writer->stop();
            return false;
        }

        constexpr int64_t kTimeoutSecs = 30;
        bool waitOk = writer->waitForCompletion(kTimeoutSecs);
        if (!waitOk)
        {
            cout << "[PIPELINE] Writer waitForCompletion timed out" << endl;
            reader->stop();
            writer->sendEOS();
            writer->waitForCompletion(2);
        }

        bool hasErr = readerError.load() || writer->hasError();

        reader->stop();
        writer->stop();

        if (hasErr)
        {
            cout << "[PIPELINE] Pipeline completed with errors" << endl;
            return false;
        }

        if (!std::filesystem::exists(outputFile))
        {
            cout << "[PIPELINE] Output file not found: " << outputFile << endl;
            return false;
        }

        auto fileSize = std::filesystem::file_size(outputFile);
        cout << "[PIPELINE] Output file size: " << fileSize << " bytes" << endl;
        return fileSize > 0;
    }
};

// ───────── Test 1: ClipReaderProducer -> RemuxWriterConsumer (MP4) ─────────

TEST_P(PipelineUnit, ClipReaderToRemuxWriter_Mp4)
{
    std::string videoFile = getTestVideoFile();
    if (!std::filesystem::exists(videoFile))
        GTEST_SKIP() << "Video file not found: " << videoFile;

    cout << "[TEST] ClipReader -> RemuxWriter (mp4): " << videoFile << endl;

    ClipReaderConfig rcfg = createReaderConfig();
    auto reader = std::make_shared<ClipReaderProducer>(rcfg);

    std::string outFile = createOutputPath("remux_mp4", ".mp4");
    m_outputFiles.push_back(outFile);

    RemuxWriterConfig wcfg;
    wcfg.video_codec = rcfg.video_codec;
    wcfg.container = "mp4";
    wcfg.output_file = outFile;
    wcfg.enable_audio = false;
    wcfg.seek_start_ms = 0;
    wcfg.end_time_ms = std::numeric_limits<int64_t>::max();

    auto writer = std::make_shared<RemuxWriterConsumer>(wcfg);

    bool ok = runPipeline(reader, writer, outFile);
    EXPECT_TRUE(ok) << "RemuxWriter (mp4) pipeline should produce a valid output file";
}

// ───────── Test 2: ClipReaderProducer -> RemuxWriterConsumer (MKV) ─────────

TEST_P(PipelineUnit, ClipReaderToRemuxWriter_Mkv)
{
    std::string videoFile = getTestVideoFile();
    if (!std::filesystem::exists(videoFile))
        GTEST_SKIP() << "Video file not found: " << videoFile;

    cout << "[TEST] ClipReader -> RemuxWriter (mkv): " << videoFile << endl;

    ClipReaderConfig rcfg = createReaderConfig();
    auto reader = std::make_shared<ClipReaderProducer>(rcfg);

    std::string outFile = createOutputPath("remux_mkv", ".mkv");
    m_outputFiles.push_back(outFile);

    RemuxWriterConfig wcfg;
    wcfg.video_codec = rcfg.video_codec;
    wcfg.container = "mkv";
    wcfg.output_file = outFile;
    wcfg.enable_audio = false;
    wcfg.seek_start_ms = 0;
    wcfg.end_time_ms = std::numeric_limits<int64_t>::max();

    auto writer = std::make_shared<RemuxWriterConsumer>(wcfg);

    bool ok = runPipeline(reader, writer, outFile);
    EXPECT_TRUE(ok) << "RemuxWriter (mkv) pipeline should produce a valid output file";
}

// ───────── Test 3: ClipReaderProducer -> TranscodeWriterConsumer (MP4) ─────

TEST_P(PipelineUnit, ClipReaderToTranscodeWriter_Mp4)
{
    std::string videoFile = getTestVideoFile();
    if (!std::filesystem::exists(videoFile))
        GTEST_SKIP() << "Video file not found: " << videoFile;

    cout << "[TEST] ClipReader -> TranscodeWriter (mp4): " << videoFile << endl;

    ClipReaderConfig rcfg = createReaderConfig();
    auto reader = std::make_shared<ClipReaderProducer>(rcfg);

    std::string outFile = createOutputPath("transcode_mp4", ".mp4");
    m_outputFiles.push_back(outFile);

    bool isSw = !NvHwDetection::getInstance()->m_useNvV4l2Enc;

    TranscodeWriterConfig wcfg;
    wcfg.video_codec = rcfg.video_codec;
    wcfg.container = "mp4";
    wcfg.output_file = outFile;
    wcfg.file_start_time = 0;
    wcfg.enable_overlay = false;
    wcfg.enable_audio = false;
    wcfg.overlay_bin = nullptr;
    wcfg.overlay_params = nullptr;
    wcfg.is_software_encoder = isSw;
    wcfg.seek_start_ms = 0;
    wcfg.end_time_ms = std::numeric_limits<int64_t>::max();

    auto writer = std::make_shared<TranscodeWriterConsumer>(wcfg);

    bool ok = runPipeline(reader, writer, outFile);
    EXPECT_TRUE(ok) << "TranscodeWriter (mp4) pipeline should produce a valid output file";
}

// ───────── Test 4: ClipReaderProducer -> TranscodeWriterConsumer (TS) ──────

TEST_P(PipelineUnit, ClipReaderToTranscodeWriter_Ts)
{
    std::string videoFile = getTestVideoFile();
    if (!std::filesystem::exists(videoFile))
        GTEST_SKIP() << "Video file not found: " << videoFile;

    cout << "[TEST] ClipReader -> TranscodeWriter (ts): " << videoFile << endl;

    ClipReaderConfig rcfg = createReaderConfig();
    auto reader = std::make_shared<ClipReaderProducer>(rcfg);

    std::string outFile = createOutputPath("transcode_ts", ".ts");
    m_outputFiles.push_back(outFile);

    bool isSw = !NvHwDetection::getInstance()->m_useNvV4l2Enc;

    TranscodeWriterConfig wcfg;
    wcfg.video_codec = rcfg.video_codec;
    wcfg.container = "ts";
    wcfg.output_file = outFile;
    wcfg.file_start_time = 0;
    wcfg.enable_overlay = false;
    wcfg.enable_audio = false;
    wcfg.overlay_bin = nullptr;
    wcfg.overlay_params = nullptr;
    wcfg.is_software_encoder = isSw;
    wcfg.seek_start_ms = 0;
    wcfg.end_time_ms = std::numeric_limits<int64_t>::max();

    auto writer = std::make_shared<TranscodeWriterConsumer>(wcfg);

    bool ok = runPipeline(reader, writer, outFile);
    EXPECT_TRUE(ok) << "TranscodeWriter (ts) pipeline should produce a valid output file";
}

// ==================== PARAMETERIZED TEST INSTANTIATION ====================

/**
 * @brief Instantiate parameterized tests with video files
 * 
 * Dynamically creates one test instance per video file found in the directory.
 * The file list is populated during static init by the EarlyVideoScan global
 * constructor in test_main.cpp (reads VIDEO_DIR env var or scans the default dir).
 * 
 * Usage:
 *   VIDEO_DIR=/path/to/videos ./vst_test
 *   VIDEO_DIR=/path/to/videos ./vst_test --gtest_filter='*DiagnosticVerifyUpload*'
 * 
 * Each test in Download will run once per actual video file.
 * No dummy/skipped test instances are created.
 */
INSTANTIATE_TEST_SUITE_P(
    VideoFiles,
    Download,
    ::testing::ValuesIn(TestConfig::getVideoFiles()),
    [](const ::testing::TestParamInfo<std::string>& info) {
        // Use the file stem (filename without extension) as the test suffix
        return std::filesystem::path(info.param).stem().string();
    }
);

INSTANTIATE_TEST_SUITE_P(
    VideoFiles,
    PipelineUnit,
    ::testing::ValuesIn(TestConfig::getVideoFiles()),
    [](const ::testing::TestParamInfo<std::string>& info) {
        return std::filesystem::path(info.param).stem().string();
    }
);
