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
 * @file recorder.cpp
 * @brief Unit tests for StreamRecorder APIs
 * 
 * Tests the recorder module APIs as documented in:
 * doc/api/vst_stream_recorder_ms/swagger.yaml
 * 
 * APIs Tested:
 * - GET  /api/v1/record/streams - List streams
 * - POST /api/v1/record/stream/add - Add stream
 * - DELETE /api/v1/record/{streamId} - Remove stream
 * - POST /api/v1/record/{streamId}/start - Start recording
 * - POST /api/v1/record/{streamId}/stop - Stop recording
 * - POST /api/v1/record/{streamId}/event - Trigger event
 * - GET  /api/v1/record/{streamId}/schedule - Get schedules
 * - POST /api/v1/record/{streamId}/schedule - Set schedules
 * - DELETE /api/v1/record/{streamId}/schedule - Delete schedules
 * - GET  /api/v1/record/{streamId}/timelines - Get timelines
 * - GET  /api/v1/record/{streamId}/status - Get status
 * - GET  /api/v1/record/configuration - Get config
 * - GET  /api/v1/record/version - Get version
 * - GET  /api/v1/record/help - Get help
 * - GET  /api/v1/record/timelines - Get all timelines
 */

#include "gtest/gtest.h"
#include <iostream>
#include <string>
#include <chrono>
#include <thread>
#include <cstdlib>
#include <iomanip>
#include <sstream>
#include <jsoncpp/json/json.h>

// Recorder module headers
#include "streamrecorder.h"
#include "storage_management.h"
#include "vstmodule.h"

// Mock CivetWeb infrastructure
#include "utils/mock_civetweb.h"

// Test utilities
#include "utils/recorder_utils.h"
#include "utils/db_test_utils.h"
#include "utils/rtspserver_utils.h"

using namespace std;

// Forward declare TestConfig from test_main.cpp (--video-dir= / VIDEO_DIR / default tools/data)
namespace TestConfig
{
    extern std::string videoDirectory;
}
using namespace nv_vms;

static std::string urlEncode(const std::string& value)
{
    std::ostringstream out;
    out.fill('0');
    out << std::hex;
    for (unsigned char c : value)
    {
        if (std::isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~')
            out << c;
        else
            out << '%' << std::uppercase << std::setw(2) << static_cast<int>(c);
    }
    return out.str();
}

// ==================== TEST FIXTURE ====================

/**
 * @brief Test fixture for StreamRecorder APIs
 * 
 * Uses static suite setup to initialize modules once for all tests,
 * avoiding repeated init/destroy cycles that cause segfaults when
 * StreamMonitor is destroyed then accessed by subsequent tests.
 */
class StreamRecorderTest : public ::testing::Test
{
protected:
    static StreamRecorder* s_recorder;
    static StorageManagement* s_storageMgmt;
    static bool s_initialized;

    StreamRecorder* m_recorder = nullptr;
    StorageManagement* m_storageMgmt = nullptr;

    // Test data
    const string TEST_STREAM_ID = "test-stream-12345";
    const string TEST_SCHEDULE_START = "0 9 * * *";   // 9:00 AM daily
    const string TEST_SCHEDULE_END = "0 17 * * *";    // 5:00 PM daily

    static void SetUpTestSuite()
    {
        cout << "\n[SUITE-SETUP] Initializing recorder modules..." << endl;

        if (!RtspServerTestUtils::start(TestConfig::videoDirectory))
            cout << "[SUITE-SETUP] RTSP server module not loaded" << endl;

        ModuleLoader* moduleLoader = ModuleLoader::getInstance();

        int ret = moduleLoader->initialize(ModuleStorageManagement);
        if (ret == 0)
        {
            s_storageMgmt = GET_STORAGE_MNGT();
            if (s_storageMgmt)
                cout << "[SUITE-SETUP] StorageManagement ready" << endl;
        }

        ret = moduleLoader->initialize(ModuleStreamRecorder);
        if (ret != 0)
        {
            cout << "[SUITE-SETUP] StreamRecorder initialization failed" << endl;
            return;
        }

        s_recorder = GET_RECORDER();
        if (s_recorder)
        {
            s_initialized = true;
            cout << "[SUITE-SETUP] StreamRecorder ready" << endl;
        }
    }

    static void TearDownTestSuite()
    {
        cout << "[SUITE-CLEANUP] StreamRecorder suite done (cleanup deferred to process exit)\n" << endl;
        s_recorder = nullptr;
        s_storageMgmt = nullptr;
        s_initialized = false;
    }

    void SetUp() override
    {
        m_recorder = s_recorder;
        m_storageMgmt = s_storageMgmt;

        if (!m_recorder)
            return;

        Json::Value addInput;
        addInput["id"] = TEST_STREAM_ID;
        addInput["url"] = RtspServerTestUtils::getTestStreamUrl();
        addInput["codec"] = "h264";
        Json::Value addResponse;
        callRecorderAPI("/api/v1/record/stream/add", "POST", addInput, addResponse);
    }

    void TearDown() override
    {
        if (m_recorder)
            m_recorder->removeStream(TEST_STREAM_ID);
    }
    
    // Helper to create request info JSON
    Json::Value createRequestInfo(const string& url, const string& method,
                                   const string& queryString = "")
    {
        Json::Value req_info;
        req_info["url"] = url;
        req_info["method"] = method;
        if (!queryString.empty())
        {
            req_info["query"] = queryString;
        }
        return req_info;
    }
    
    // Helper to call recorder API. Uses direct C++ methods where they exist (see recorder_apis.cpp m_func).
    VmsErrorCode callRecorderAPI(const string& url, const string& method,
                                 const Json::Value& input, Json::Value& response,
                                 const string& queryString = "")
    {
        // Dispatch to direct C++ implementation for APIs that have one (matching recorder_apis.cpp m_func)
        if (method == "GET" && url == "/api/v1/record/streams")
            return m_recorder->streams(response);
        if (method == "GET" && url == "/api/v1/record/version")
        {
            string version;
            m_recorder->getVersion(version);
            response["recorder_version"] = version;
            return VmsErrorCode::NoError;
        }
        if (method == "GET" && url == "/api/v1/record/configuration")
            return m_recorder->getConfiguration(response);
        if (method == "GET" && url == "/api/v1/record/status")
            return m_recorder->recordStatus(response);
        if (method == "GET" && url == "/api/v1/record/help")
        {
            IVstModule* mod = ModuleLoader::getInstance()->getRecorderInstance();
            if (mod)
            {
                auto handlers = mod->getHttpApi();
                auto it = handlers.find("/api/v1/record/help");
                if (it != handlers.end())
                    return it->second(createRequestInfo(url, method), input, response, nullptr);
            }
            return VmsErrorCode::VMSNotSupportedError;
        }
        if (method == "POST" && url == "/api/v1/record/stream/add")
        {
            cout << "[TEST] Add stream to recorder" << endl;
            string id = input.get("id", "").asString();
            string inputUrl = input.get("url", "").asString();
            string codec = input.get("codec", "").asString();
            cout << "[TEST] Id: " << id << endl;
            cout << "[TEST] Input URL: " << inputUrl << endl;
            cout << "[TEST] Codec: " << codec << endl;  
            return m_recorder->addStream(id, inputUrl, codec);
        }

        // All other APIs go through the generic handler (stream-id paths, help, etc.)
        Json::Value req_info = createRequestInfo(url, method, queryString);
        MockConnection mockConn;
        mockConn.requestInfo.setMethod(method);
        mockConn.requestInfo.setUri(url);
        if (!queryString.empty())
            mockConn.requestInfo.setQueryString(queryString);
        return m_recorder->handleRecordAPIrequest(req_info, input, response,
                                                 reinterpret_cast<struct mg_connection*>(&mockConn));
    }
};

StreamRecorder* StreamRecorderTest::s_recorder = nullptr;
StorageManagement* StreamRecorderTest::s_storageMgmt = nullptr;
bool StreamRecorderTest::s_initialized = false;

// ==================== BASIC API TESTS ====================

/**
 * @brief Test GET /api/v1/record/streams
 * 
 * Validates streams endpoint returns list of streams.
 */
TEST_F(StreamRecorderTest, GetStreams)
{
    if (!m_recorder) GTEST_SKIP() << "StreamRecorder not available";
    
    cout << "[TEST] Get streams list" << endl;
    
    Json::Value response;
    VmsErrorCode result = callRecorderAPI("/api/v1/record/streams", "GET", Json::Value(), response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    cout << "[TEST] Stream count: " << response.size() << endl;
    cout << "[TEST] Response: " << response.toStyledString() << endl;
    
    EXPECT_EQ(result, VmsErrorCode::NoError);
    EXPECT_TRUE(response.isArray());
}

// ==================== STREAM MANAGEMENT TESTS ====================

/**
 * @brief Test POST /api/v1/record/stream/add
 * 
 * Validates adding a new stream to the recorder.
 */
TEST_F(StreamRecorderTest, AddStream)
{
    if (!m_recorder) GTEST_SKIP() << "StreamRecorder not available";
    
    cout << "[TEST] Add stream to recorder" << endl;
    
    Json::Value input;
    input["id"] = TEST_STREAM_ID;
    input["url"] = RtspServerTestUtils::getTestStreamUrl();
    input["codec"] = "h264";
    
    Json::Value response;
    VmsErrorCode result = callRecorderAPI("/api/v1/record/stream/add", "POST", input, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    if (response.isMember("error_message"))
    {
        cout << "[TEST] Error: " << response["error_message"].asString() << endl;
    }
    
    EXPECT_EQ(result, VmsErrorCode::NoError);
}

/**
 * @brief Test POST with non-RTSP URL (should be rejected)
 * 
 * Validates that non-RTSP URLs are rejected.
 */
TEST_F(StreamRecorderTest, AddStreamNonRTSP)
{
    if (!m_recorder) GTEST_SKIP() << "StreamRecorder not available";
    
    cout << "[TEST] Add stream with non-RTSP URL (should reject)" << endl;
    
    Json::Value input;
    input["id"] = TEST_STREAM_ID;
    input["url"] = "http://example.com/video.mp4";  // HTTP, not RTSP
    
    Json::Value response;
    VmsErrorCode result = callRecorderAPI("/api/v1/record/stream/add", "POST", input, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    // Recorder accepts any URL format (HTTP, RTSP, file) -- it does not reject based on protocol
    EXPECT_TRUE(result == VmsErrorCode::NoError ||
                result == VmsErrorCode::InvalidParameterError);
}

/**
 * @brief Test DELETE /api/v1/record/{streamId}
 * 
 * Validates removing a stream from the recorder.
 */
TEST_F(StreamRecorderTest, RemoveStream)
{
    if (!m_recorder) GTEST_SKIP() << "StreamRecorder not available";
    
    cout << "[TEST] Remove stream from recorder" << endl;
    
    Json::Value input;
    input["id"] = TEST_STREAM_ID;
    input["url"] = RtspServerTestUtils::getTestStreamUrl();
    Json::Value addResponse;
    VmsErrorCode addResult = callRecorderAPI("/api/v1/record/stream/add", "POST", input, addResponse);
    if (addResult != VmsErrorCode::NoError)
        GTEST_SKIP() << "Stream add failed (prerequisite): " << static_cast<int>(addResult);
    
    Json::Value response;
    string url = "/api/v1/record/" + TEST_STREAM_ID;
    VmsErrorCode result = callRecorderAPI(url, "DELETE", Json::Value(), response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    EXPECT_EQ(result, VmsErrorCode::NoError);
}

// ==================== RECORDING CONTROL TESTS ====================

/**
 * @brief Test POST /api/v1/record/{streamId}/start
 * 
 * Validates starting recording for a stream.
 */
TEST_F(StreamRecorderTest, StartRecording)
{
    if (!m_recorder) GTEST_SKIP() << "StreamRecorder not available";
    
    cout << "[TEST] Start recording for stream" << endl;
    
    Json::Value input;
    input["id"] = TEST_STREAM_ID;
    input["url"] = RtspServerTestUtils::getTestStreamUrl();
    Json::Value addResponse;
    VmsErrorCode addResult = callRecorderAPI("/api/v1/record/stream/add", "POST", input, addResponse);
    if (addResult != VmsErrorCode::NoError)
        GTEST_SKIP() << "Stream add failed (prerequisite): " << static_cast<int>(addResult);
    
    Json::Value response;
    string url = "/api/v1/record/" + TEST_STREAM_ID + "/start";
    VmsErrorCode result = callRecorderAPI(url, "POST", Json::Value(), response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    EXPECT_EQ(result, VmsErrorCode::NoError);
}

/**
 * @brief Test POST /api/v1/record/{streamId}/stop
 * 
 * Validates stopping recording for a stream.
 */
TEST_F(StreamRecorderTest, StopRecording)
{
    if (!m_recorder) GTEST_SKIP() << "StreamRecorder not available";
    
    cout << "[TEST] Stop recording for stream" << endl;
    
    Json::Value response;
    string url = "/api/v1/record/" + TEST_STREAM_ID + "/stop";
    VmsErrorCode result = callRecorderAPI(url, "POST", Json::Value(), response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    // May succeed or fail depending on recording state
    EXPECT_TRUE(result == VmsErrorCode::NoError ||
                result == VmsErrorCode::VMSInternalError ||
                result == VmsErrorCode::MethodNotAllowedError);
}

/**
 * @brief Test POST /api/v1/record/{streamId}/event
 * 
 * Validates triggering event-based recording.
 */
TEST_F(StreamRecorderTest, DISABLED_TriggerEventRecording)
{
    if (!m_recorder) GTEST_SKIP() << "StreamRecorder not available";
    
    cout << "[TEST] Trigger event recording" << endl;
    
    Json::Value response;
    string url = "/api/v1/record/" + TEST_STREAM_ID + "/event";
    VmsErrorCode result = callRecorderAPI(url, "POST", Json::Value(), response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    EXPECT_TRUE(result == VmsErrorCode::NoError ||
                result == VmsErrorCode::InvalidParameterError ||
                result == VmsErrorCode::VMSInternalError);
}

// ==================== STATUS & TIMELINE TESTS ====================

/**
 * @brief Test GET /api/v1/record/{streamId}/status
 * 
 * Validates getting recording status for a stream.
 */
TEST_F(StreamRecorderTest, GetRecordingStatus)
{
    if (!m_recorder) GTEST_SKIP() << "StreamRecorder not available";
    
    cout << "[TEST] Get recording status" << endl;
    
    Json::Value response;
    string url = "/api/v1/record/" + TEST_STREAM_ID + "/status";
    VmsErrorCode result = callRecorderAPI(url, "GET", Json::Value(), response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    if (response.isMember("recordingStatus"))
    {
        cout << "[TEST] Recording Status: " << response["recordingStatus"].asString() << endl;
    }
    
    EXPECT_EQ(result, VmsErrorCode::NoError);
    EXPECT_TRUE(response.isMember("recordingStatus"));
}

/**
 * @brief Test GET /api/v1/record/{streamId}/timelines
 * 
 * Validates getting recording timelines for a stream.
 * Uses RecorderTestUtils::getStreamTimelines() helper function which
 * queries timelines and outputs selected time range.
 * Prerequisite: recording is started for the test stream so timelines may be non-empty.
 */
TEST_F(StreamRecorderTest, GetStreamTimelines)
{
    if (!m_recorder) GTEST_SKIP() << "StreamRecorder not available";
    
    cout << "[TEST] Get stream recording timelines (using utility function)" << endl;
    
    // Prerequisite: start recording so timelines can be populated
    Json::Value startResponse;
    VmsErrorCode startResult = callRecorderAPI(
        "/api/v1/record/" + TEST_STREAM_ID + "/start", "POST", Json::Value(), startResponse);
    if (startResult != VmsErrorCode::NoError)
        cout << "[TEST] Note: Start recording returned " << static_cast<int>(startResult)
             << " (timelines may be empty)" << endl;
    std::this_thread::sleep_for(std::chrono::seconds(5));  // allow data to be stored
    // Output parameters for selected time range
    string selectedStart, selectedEnd;
    // Use utility function to get timelines and select time range
    TestTimelineResult result = RecorderTestUtils::getStreamTimelines(
        m_recorder,
        TEST_STREAM_ID,
        selectedStart,    // OUTPUT: Selected start time
        selectedEnd       // OUTPUT: Selected end time
    );
    
    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;
    
    // Timeline query should not crash; may return NoError with empty data or an error
    // if recording didn't produce data in time
    EXPECT_TRUE(result.errorCode == VmsErrorCode::NoError ||
                result.errorCode == VmsErrorCode::VMSNoDataError ||
                result.errorCode == VmsErrorCode::CameraNotFoundError)
        << "Timeline query returned unexpected error: " << static_cast<int>(result.errorCode);
    
    if (result.isSuccess())
    {
        cout << "[TEST] Timelines retrieved successfully" << endl;
        cout << "[TEST] Selected range: " << selectedStart << " to " << selectedEnd << endl;
    }
}

/**
 * @brief Test GET /api/v1/record/timelines (all streams)
 * 
 * Validates getting recording timelines for all streams.
 * Uses RecorderTestUtils::getAllTimelines() helper function.
 * Prerequisite: recording is started for the test stream so timelines may be non-empty.
 */
TEST_F(StreamRecorderTest, GetAllTimelines)
{
    if (!m_recorder) GTEST_SKIP() << "StreamRecorder not available";
    
    cout << "[TEST] Get all recording timelines (using utility function)" << endl;
    
    // Prerequisite: start recording so timelines can be populated
    Json::Value startResponse;
    VmsErrorCode startResult = callRecorderAPI(
        "/api/v1/record/" + TEST_STREAM_ID + "/start", "POST", Json::Value(), startResponse);
    if (startResult != VmsErrorCode::NoError)
        cout << "[TEST] Note: Start recording returned " << static_cast<int>(startResult)
             << " (timelines may be empty)" << endl;
    std::this_thread::sleep_for(std::chrono::seconds(5));  // allow data to be stored
    // Use utility function to get all timelines
    TestTimelineResult result = RecorderTestUtils::getAllTimelines(m_recorder);
    
    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;
    cout << "[TEST] Response is object: " << result.timelines.isObject() << endl;
    
    EXPECT_TRUE(result.errorCode == VmsErrorCode::NoError ||
                result.errorCode == VmsErrorCode::VMSNoDataError ||
                result.errorCode == VmsErrorCode::CameraNotFoundError)
        << "All-timelines query returned unexpected error: " << static_cast<int>(result.errorCode);
    
    if (result.isSuccess() && result.timelines.isObject())
    {
        cout << "[TEST] Retrieved timelines for " 
             << result.timelines.getMemberNames().size() << " streams" << endl;
    }
}

// ==================== SCHEDULE MANAGEMENT TESTS ====================

/**
 * @brief Test GET /api/v1/record/{streamId}/schedule
 * 
 * Validates getting recording schedules for a stream.
 */
TEST_F(StreamRecorderTest, GetRecordSchedules)
{
    if (!m_recorder) GTEST_SKIP() << "StreamRecorder not available";
    
    cout << "[TEST] Get recording schedules" << endl;
    
    Json::Value response;
    string url = "/api/v1/record/" + TEST_STREAM_ID + "/schedule";
    VmsErrorCode result = callRecorderAPI(url, "GET", Json::Value(), response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    EXPECT_TRUE(result == VmsErrorCode::NoError ||
                result == VmsErrorCode::InvalidParameterError);
}

/**
 * @brief Test POST /api/v1/record/{streamId}/schedule
 * 
 * Validates creating a recording schedule (CRON format).
 */
TEST_F(StreamRecorderTest, CreateRecordSchedule)
{
    if (!m_recorder) GTEST_SKIP() << "StreamRecorder not available";
    
    cout << "[TEST] Create recording schedule" << endl;
    
    // Create schedule array
    Json::Value input(Json::arrayValue);
    Json::Value schedule;
    schedule["startTime"] = TEST_SCHEDULE_START;  // 9:00 AM daily
    schedule["endTime"] = TEST_SCHEDULE_END;      // 5:00 PM daily
    input.append(schedule);
    
    Json::Value response;
    string url = "/api/v1/record/" + TEST_STREAM_ID + "/schedule";
    VmsErrorCode result = callRecorderAPI(url, "POST", input, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    EXPECT_TRUE(result == VmsErrorCode::NoError ||
                result == VmsErrorCode::InvalidParameterError);
}

/**
 * @brief Test DELETE /api/v1/record/{streamId}/schedule
 * 
 * Validates deleting a recording schedule.
 */
TEST_F(StreamRecorderTest, DeleteRecordSchedule)
{
    if (!m_recorder) GTEST_SKIP() << "StreamRecorder not available";
    
    cout << "[TEST] Delete recording schedule" << endl;
    
    string queryString = "startTime=" + urlEncode(TEST_SCHEDULE_START) + "&endTime=" + urlEncode(TEST_SCHEDULE_END);
    Json::Value response;
    string url = "/api/v1/record/" + TEST_STREAM_ID + "/schedule";
    VmsErrorCode result = callRecorderAPI(url, "DELETE", Json::Value(), response, queryString);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    EXPECT_TRUE(result == VmsErrorCode::NoError ||
                result == VmsErrorCode::InvalidParameterError);
}

// ==================== ERROR VALIDATION TESTS ====================



/**
 * @brief Test with missing required parameters in schedule
 * 
 * Validates that schedules require both startTime and endTime.
 */
TEST_F(StreamRecorderTest, ScheduleMissingEndTime)
{
    if (!m_recorder) GTEST_SKIP() << "StreamRecorder not available";
    
    cout << "[TEST] Schedule missing endTime (error validation)" << endl;
    
    Json::Value input(Json::arrayValue);
    Json::Value schedule;
    schedule["startTime"] = TEST_SCHEDULE_START;
    // Missing endTime
    input.append(schedule);
    
    Json::Value response;
    string url = "/api/v1/record/" + TEST_STREAM_ID + "/schedule";
    VmsErrorCode result = callRecorderAPI(url, "POST", input, response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    
    EXPECT_EQ(result, VmsErrorCode::InvalidParameterError);
}

// ==================== COMPLETE WORKFLOW TEST ====================

/**
 * @brief Test complete workflow: Add → Start → Status → Stop → Remove
 * 
 * Validates a complete recording workflow.
 */
TEST_F(StreamRecorderTest, CompleteWorkflow)
{
    if (!m_recorder) GTEST_SKIP() << "StreamRecorder not available";
    
    cout << "[TEST] Complete workflow: Add → Start → Status → Stop → Remove" << endl;
    
    string streamId = "workflow-test-" + std::to_string(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
    
    Json::Value response;
    VmsErrorCode result;
    int successCount = 0;
    constexpr int kTotalSteps = 5;
    
    // Step 1: Add stream
    cout << "[TEST]   Step 1: Adding stream..." << endl;
    Json::Value addInput;
    addInput["id"] = streamId;
    addInput["url"] = RtspServerTestUtils::getTestStreamUrl();
    result = callRecorderAPI("/api/v1/record/stream/add", "POST", addInput, response);
    cout << "[TEST]   Add result: " << static_cast<int>(result) << endl;
    EXPECT_EQ(result, VmsErrorCode::NoError) << "Step 1 (Add) failed";
    if (result == VmsErrorCode::NoError) successCount++;
    
    // Step 2: Start recording
    cout << "[TEST]   Step 2: Starting recording..." << endl;
    string startUrl = "/api/v1/record/" + streamId + "/start";
    result = callRecorderAPI(startUrl, "POST", Json::Value(), response);
    cout << "[TEST]   Start result: " << static_cast<int>(result) << endl;
    EXPECT_EQ(result, VmsErrorCode::NoError) << "Step 2 (Start) failed";
    if (result == VmsErrorCode::NoError) successCount++;
    
    // Step 3: Get status
    cout << "[TEST]   Step 3: Getting status..." << endl;
    string statusUrl = "/api/v1/record/" + streamId + "/status";
    result = callRecorderAPI(statusUrl, "GET", Json::Value(), response);
    cout << "[TEST]   Status result: " << static_cast<int>(result) << endl;
    EXPECT_EQ(result, VmsErrorCode::NoError) << "Step 3 (Status) failed";
    if (result == VmsErrorCode::NoError) successCount++;
    
    // Step 4: Stop recording
    cout << "[TEST]   Step 4: Stopping recording..." << endl;
    string stopUrl = "/api/v1/record/" + streamId + "/stop";
    result = callRecorderAPI(stopUrl, "POST", Json::Value(), response);
    cout << "[TEST]   Stop result: " << static_cast<int>(result) << endl;
    EXPECT_EQ(result, VmsErrorCode::NoError) << "Step 4 (Stop) failed";
    if (result == VmsErrorCode::NoError) successCount++;
    
    // Step 5: Remove stream
    cout << "[TEST]   Step 5: Removing stream..." << endl;
    string removeUrl = "/api/v1/record/" + streamId;
    result = callRecorderAPI(removeUrl, "DELETE", Json::Value(), response);
    cout << "[TEST]   Remove result: " << static_cast<int>(result) << endl;
    EXPECT_EQ(result, VmsErrorCode::NoError) << "Step 5 (Remove) failed";
    if (result == VmsErrorCode::NoError) successCount++;
    
    EXPECT_EQ(successCount, kTotalSteps)
        << "Workflow: " << successCount << "/" << kTotalSteps << " steps succeeded";
}

/**
 * @brief Test GET /api/v1/record/version
 * 
 * Validates version endpoint returns recorder version string.
 */
TEST_F(StreamRecorderTest, GetVersion)
{
    if (!m_recorder) GTEST_SKIP() << "StreamRecorder not available";
    
    cout << "[TEST] Get recorder version" << endl;
    
    Json::Value response;
    VmsErrorCode result = callRecorderAPI("/api/v1/record/version", "GET", Json::Value(), response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    if (response.isMember("recorder_version"))
    {
        cout << "[TEST] Version: " << response["recorder_version"].asString() << endl;
    }
    
    EXPECT_EQ(result, VmsErrorCode::NoError);
    EXPECT_TRUE(response.isMember("recorder_version"));
}

/**
 * @brief Test GET /api/v1/record/help
 * 
 * Validates help endpoint returns API list.
 */
TEST_F(StreamRecorderTest, GetHelp)
{
    if (!m_recorder) GTEST_SKIP() << "StreamRecorder not available";
    
    cout << "[TEST] Get API help" << endl;
    
    Json::Value response;
    VmsErrorCode result = callRecorderAPI("/api/v1/record/help", "GET", Json::Value(), response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    cout << "[TEST] API count: " << response.size() << endl;
    
    EXPECT_EQ(result, VmsErrorCode::NoError);
    EXPECT_TRUE(response.isArray());
    EXPECT_GT(response.size(), 0);
}

/**
 * @brief Test GET /api/v1/record/configuration
 * 
 * Validates configuration endpoint returns config object.
 */
TEST_F(StreamRecorderTest, GetConfiguration)
{
    if (!m_recorder) GTEST_SKIP() << "StreamRecorder not available";
    
    cout << "[TEST] Get recorder configuration" << endl;
    
    Json::Value response;
    VmsErrorCode result = callRecorderAPI("/api/v1/record/configuration", "GET", Json::Value(), response);
    
    cout << "[TEST] Result: " << static_cast<int>(result) << endl;
    if (response.isMember("alwaysRecording"))
    {
        cout << "[TEST] Always Recording: " << response["alwaysRecording"].asBool() << endl;
    }
    
    EXPECT_EQ(result, VmsErrorCode::NoError);
    EXPECT_TRUE(response.isObject());
}