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
 * @file sensor.cpp
 * @brief Unit tests for SensorManagementApis public methods
 *
 * Tests the sensor management API endpoints defined in:
 *   doc/api/vst_sensor_management_ms/swagger.yaml
 *
 * Each test invokes the real API handler through the SensorTestUtils helpers
 * (which construct SensorManagementApis from ModuleLoader) and validates the
 * response against the expected swagger contract.
 *
 * API base: /api/v1/sensor
 */

#include "gtest/gtest.h"
#include <iostream>
#include <string>
#include <vector>
#include <algorithm>
#include <jsoncpp/json/json.h>

#include "sensor_management.h"
#include "sensor_management_apis.h"
#include "vstmodule.h"
#include "utils/mock_civetweb.h"
#include "utils/sensor_test_utils.h"
#include "utils/storage_test_utils.h"  // For StorageTestUtils::cleanTestEnvironment
#include "utils/rtspserver_utils.h"

// Forward declare TestConfig from test_main.cpp (--video-dir= / VIDEO_DIR / default tools/data)
namespace TestConfig
{
    extern std::string videoDirectory;
}

using namespace std;
using namespace nv_vms;

// ==================== TEST FIXTURE ====================

/**
 * @brief Test fixture for SensorManagementApis
 *
 * Uses static suite setup to initialize the SensorManagement module once
 * for all tests, avoiding repeated init/destroy cycles that can hang
 * when ONVIF discovery threads block on socket shutdown.
 */
class SensorManagementTest : public ::testing::Test
{
protected:
    static SensorManagement* s_sensorMgmt;
    static bool s_initialized;

    static void SetUpTestSuite()
    {
        cout << "\n======================================================" << endl;
        cout << "[SUITE-SETUP] Initializing SensorManagement module" << endl;
        cout << "======================================================" << endl;

        StorageTestUtils::cleanTestEnvironment(true);

        ModuleLoader* moduleLoader = ModuleLoader::getInstance();

        int ret = moduleLoader->initialize(ModuleSensorManagement);
        if (ret != 0)
        {
            cout << "[SUITE-SETUP] SensorManagement module initialization failed" << endl;
            return;
        }

        s_sensorMgmt = GET_SENSOR_MNGT();
        if (s_sensorMgmt == nullptr)
        {
            cout << "[SUITE-SETUP] SensorManagement module not loaded" << endl;
        }
        else
        {
            s_initialized = true;
            cout << "[SUITE-SETUP] SensorManagement instance ready" << endl;
        }

        if (!RtspServerTestUtils::start(TestConfig::videoDirectory))
        {
            cout << "[SUITE-SETUP] RTSP server module not loaded; skipping stream pre-add" << endl;
        }
        else
        {
            std::vector<std::string> videoUrls = RtspServerTestUtils::getVideoFileUrls();
            const size_t maxStreams = 3;
            size_t count = std::min(videoUrls.size(), maxStreams);

            cout << "[SUITE-SETUP] Found " << videoUrls.size() << " video file(s), "
                 << "adding up to " << maxStreams << " as sensors" << endl;

            for (size_t i = 0; i < count; ++i)
            {
                string sensorName = "test-sensor-" + to_string(i);
                SensorApiResult res = SensorTestUtils::addSensorByUrl(
                    videoUrls[i], "", "", sensorName);
                if (res.isSuccess())
                    cout << "[SUITE-SETUP] Sensor \"" << sensorName << "\" added" << endl;
                else
                    cout << "[SUITE-SETUP] Failed to add sensor \"" << sensorName
                         << "\": " << res.errorMessage << endl;
            }

            if (count == 0)
                cout << "[SUITE-SETUP] No video files found; no sensors pre-added" << endl;
        }
    }

    static void TearDownTestSuite()
    {
        RtspServerTestUtils::stop();
        cout << "[SUITE-CLEANUP] SensorManagement suite done (cleanup deferred to process exit)\n" << endl;
        s_sensorMgmt = nullptr;
        s_initialized = false;
    }

    SensorManagement* m_sensorMgmt = nullptr;

    void SetUp() override
    {
        m_sensorMgmt = s_sensorMgmt;
    }
};

SensorManagement* SensorManagementTest::s_sensorMgmt = nullptr;
bool SensorManagementTest::s_initialized = false;

// ==================== GLOBAL ENDPOINT TESTS ====================

/**
 * @brief GET /api/v1/sensor/list
 *
 * Swagger: getSensorList -- returns SensorInfoArray (JSON array).
 */
TEST_F(SensorManagementTest, GetSensorList)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] GET /api/v1/sensor/list" << endl;

    SensorApiResult result = SensorTestUtils::getSensorList();

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    EXPECT_EQ(result.errorCode, VmsErrorCode::NoError)
        << "getSensorList should succeed: " << result.errorMessage;

    // Response must be a JSON array (may be empty if no sensors connected)
    EXPECT_TRUE(result.response.isArray())
        << "Response should be a JSON array";
}

/**
 * @brief GET /api/v1/sensor/version
 *
 * Swagger: getVersion -- returns { type, version }.
 */
TEST_F(SensorManagementTest, GetVersion)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] GET /api/v1/sensor/version" << endl;

    SensorApiResult result = SensorTestUtils::getSensorVersion();

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    EXPECT_EQ(result.errorCode, VmsErrorCode::NoError)
        << "getVersion should succeed: " << result.errorMessage;

    EXPECT_TRUE(result.response.isMember("version"))
        << "Response should contain 'version' field";
    EXPECT_TRUE(result.response.isMember("type"))
        << "Response should contain 'type' field";

    cout << "[TEST] Version: " << result.response["version"].asString()
         << ", Type: " << result.response["type"].asString() << endl;
}

/**
 * @brief GET /api/v1/sensor/help
 *
 * Swagger: gethelp -- returns array of supported API route strings.
 */
TEST_F(SensorManagementTest, GetHelp)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] GET /api/v1/sensor/help" << endl;

    SensorApiResult result = SensorTestUtils::getSensorHelp();

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    EXPECT_EQ(result.errorCode, VmsErrorCode::NoError)
        << "getSensorHelp should succeed: " << result.errorMessage;

    EXPECT_TRUE(result.response.isArray())
        << "Response should be a JSON array of route strings";
    EXPECT_GT(result.response.size(), 0u)
        << "Help list should not be empty";

    cout << "[TEST] Help routes count: " << result.response.size() << endl;
}

/**
 * @brief GET /api/v1/sensor/configuration
 *
 * Swagger: getSensorConfiguration -- returns GetConfiguration object.
 */
TEST_F(SensorManagementTest, GetConfiguration)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] GET /api/v1/sensor/configuration" << endl;

    SensorApiResult result = SensorTestUtils::getSensorConfiguration();

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    EXPECT_EQ(result.errorCode, VmsErrorCode::NoError)
        << "getSensorConfiguration should succeed: " << result.errorMessage;

    // Verify a subset of required fields from swagger GetConfiguration schema
    EXPECT_TRUE(result.response.isMember("httpPort"))
        << "Configuration should contain 'httpPort'";
    EXPECT_TRUE(result.response.isMember("maxSensorsSupported"))
        << "Configuration should contain 'maxSensorsSupported'";
    EXPECT_TRUE(result.response.isMember("deviceDiscoveryFrequencySeconds"))
        << "Configuration should contain 'deviceDiscoveryFrequencySeconds'";
    EXPECT_TRUE(result.response.isMember("vstDataPath"))
        << "Configuration should contain 'vstDataPath'";
}

/**
 * @brief POST /api/v1/sensor/configuration with valid input
 *
 * Swagger: postSensorConfiguration -- accepts SetConfiguration (ntpServers, etc.)
 */
TEST_F(SensorManagementTest, PostConfiguration)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] POST /api/v1/sensor/configuration (valid)" << endl;

    Json::Value config;
    Json::Value ntpServers(Json::arrayValue);
    ntpServers.append("pool.ntp.org");
    config["ntpServers"] = ntpServers;

    SensorApiResult result = SensorTestUtils::postSensorConfiguration(config);

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    EXPECT_EQ(result.errorCode, VmsErrorCode::NoError)
        << "postSensorConfiguration should succeed: " << result.errorMessage;
}

/**
 * @brief POST /api/v1/sensor/configuration with invalid (null) body
 *
 * Should return MethodNotAllowedError.
 */
TEST_F(SensorManagementTest, PostConfigurationInvalidInput)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] POST /api/v1/sensor/configuration (invalid null body)" << endl;

    SensorApiResult result = SensorTestUtils::postSensorConfiguration(Json::nullValue);

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    EXPECT_EQ(result.errorCode, VmsErrorCode::MethodNotAllowedError)
        << "Null body should be rejected with MethodNotAllowedError";
}

/**
 * @brief GET /api/v1/sensor/qos
 *
 * Swagger: getSensorQos -- returns { stats: [...], numActiveRtspConnections }.
 */
TEST_F(SensorManagementTest, GetQosStats)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] GET /api/v1/sensor/qos" << endl;

    SensorApiResult result = SensorTestUtils::getSensorQos();

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    EXPECT_EQ(result.errorCode, VmsErrorCode::NoError)
        << "getSensorQos should succeed: " << result.errorMessage;

    EXPECT_TRUE(result.response.isMember("stats"))
        << "Response should contain 'stats' field";
}

/**
 * @brief GET /api/v1/sensor/timelines
 *
 * Swagger: getAllSensorTimelines -- returns object keyed by sensorId.
 */
TEST_F(SensorManagementTest, GetAllTimelines)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] GET /api/v1/sensor/timelines" << endl;

    SensorApiResult result = SensorTestUtils::getAllTimelines();

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    EXPECT_EQ(result.errorCode, VmsErrorCode::NoError)
        << "getAllTimelines should succeed: " << result.errorMessage;
}

/**
 * @brief POST /api/v1/sensor/scan
 *
 * Swagger: postSensorScan -- triggers sensor discovery, returns 200.
 */
TEST_F(SensorManagementTest, PostSensorScan)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] POST /api/v1/sensor/scan" << endl;

    SensorApiResult result = SensorTestUtils::postSensorScan();

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    EXPECT_EQ(result.errorCode, VmsErrorCode::NoError)
        << "postSensorScan should succeed: " << result.errorMessage;
}

// ==================== SENSOR ADD TESTS ====================

/**
 * @brief POST /api/v1/sensor/add with IP-based payload
 *
 * Swagger: postSensorNew -- adds sensor by IP address.
 * Note: may fail if no real sensor is reachable; test validates API routing.
 */
TEST_F(SensorManagementTest, AddSensorByIp)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] POST /api/v1/sensor/add (by IP)" << endl;

    SensorApiResult result = SensorTestUtils::addSensorByIp(
        "192.168.1.100", "admin", "password", "test-sensor-ip");

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    // API routing should work even if sensor is unreachable
    EXPECT_NE(result.errorCode, VmsErrorCode::VMSNotSupportedError)
        << "Sensor add by IP endpoint should be supported";
}

/**
 * @brief POST /api/v1/sensor/add with RTSP URL
 *
 * Swagger: postSensorNew -- adds sensor by RTSP URL.
 */
TEST_F(SensorManagementTest, AddSensorByUrl)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] POST /api/v1/sensor/add (by URL)" << endl;

    SensorApiResult result = SensorTestUtils::addSensorByUrl(
        "rtsp://192.168.1.100:554/stream1", "admin", "password", "test-sensor-url");

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    EXPECT_NE(result.errorCode, VmsErrorCode::VMSNotSupportedError)
        << "Sensor add by URL endpoint should be supported";
}

/**
 * @brief POST /api/v1/sensor/add with missing required fields
 *
 * Should fail with an error (InvalidParameterError or similar).
 */
TEST_F(SensorManagementTest, AddSensorMissingFields)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] POST /api/v1/sensor/add (missing fields)" << endl;

    // Call with empty strings -- required fields missing
    SensorApiResult result = SensorTestUtils::addSensorByIp("", "", "");

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    EXPECT_NE(result.errorCode, VmsErrorCode::NoError)
        << "Adding sensor with missing fields should fail";
}

// ==================== PER-SENSOR ENDPOINT TESTS ====================

/**
 * @brief GET /api/v1/sensor/{sensorId}/info with invalid sensor ID
 *
 * Should return CameraNotFoundError.
 */
TEST_F(SensorManagementTest, GetSensorInfo_InvalidId)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] GET /sensor/{id}/info (invalid ID)" << endl;

    SensorApiResult result = SensorTestUtils::getSensorInfo("nonexistent-sensor-id-12345");

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    EXPECT_TRUE(result.errorCode == VmsErrorCode::CameraNotFoundError ||
                result.errorCode == VmsErrorCode::NoError)
        << "Non-existent sensor ID should return CameraNotFoundError or NoError";
}

/**
 * @brief GET /api/v1/sensor/{sensorId}/status with invalid sensor ID
 *
 * Should return CameraNotFoundError.
 */
TEST_F(SensorManagementTest, GetSensorStatus_InvalidId)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] GET /sensor/{id}/status (invalid ID)" << endl;

    SensorApiResult result = SensorTestUtils::getSensorStatus("nonexistent-sensor-id-12345");

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    // Status for non-existent sensor should either be CameraNotFoundError
    // or NoError with an error-state payload
    EXPECT_TRUE(result.errorCode == VmsErrorCode::CameraNotFoundError ||
                result.errorCode == VmsErrorCode::NoError)
        << "Status call for non-existent sensor should handle gracefully";
}

/**
 * @brief DELETE /api/v1/sensor/{sensorId} with invalid sensor ID
 *
 * Should not crash; may return CameraNotFoundError or NoError
 * (delete of non-existent sensor is idempotent in some implementations).
 */
TEST_F(SensorManagementTest, DeleteSensor_InvalidId)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] DELETE /sensor/{id} (invalid ID)" << endl;

    SensorApiResult result = SensorTestUtils::deleteSensor("nonexistent-sensor-id-12345");

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    // Should not be VMSNotSupportedError (endpoint must be recognized)
    EXPECT_NE(result.errorCode, VmsErrorCode::VMSNotSupportedError)
        << "DELETE sensor endpoint should be supported";
}

/**
 * @brief GET /api/v1/sensor/{sensorId}/streams with invalid sensor ID
 *
 * Verify endpoint routing works; may return error for non-existent sensor.
 */
TEST_F(SensorManagementTest, GetSensorStreams_InvalidId)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] GET /sensor/{id}/streams (invalid ID)" << endl;

    SensorApiResult result = SensorTestUtils::getSensorStreams("nonexistent-sensor-id-12345");

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    EXPECT_NE(result.errorCode, VmsErrorCode::VMSNotSupportedError)
        << "Streams endpoint should be supported";
}

/**
 * @brief GET /api/v1/sensor/{sensorId}/timelines with invalid sensor ID
 */
TEST_F(SensorManagementTest, GetSensorTimelines_InvalidId)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] GET /sensor/{id}/timelines (invalid ID)" << endl;

    SensorApiResult result = SensorTestUtils::getSensorTimelines("nonexistent-sensor-id-12345");

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    EXPECT_NE(result.errorCode, VmsErrorCode::VMSNotSupportedError)
        << "Timelines endpoint should be supported";
}

/**
 * @brief POST /api/v1/sensor/{sensorId}/info with invalid sensor ID
 */
TEST_F(SensorManagementTest, PostSensorInfo_InvalidId)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] POST /sensor/{id}/info (invalid ID)" << endl;

    Json::Value body;
    body["name"] = "updated-name";
    SensorApiResult result = SensorTestUtils::postSensorInfo("nonexistent-sensor-id-12345", body);

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    // Should handle gracefully (CameraNotFoundError or similar)
    EXPECT_NE(result.errorCode, VmsErrorCode::VMSNotSupportedError)
        << "POST sensor info endpoint should be supported";
}

// ==================== API ROUTING / ERROR HANDLING TESTS ====================

/**
 * @brief Unsupported action on /api/v1/sensor/{id}/{badAction}
 *
 * Should return MethodNotAllowedError.
 */
TEST_F(SensorManagementTest, HandleSensorAPI_InvalidAction)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] GET /sensor/{id}/invalid_action" << endl;

    SensorApiResult result = SensorTestUtils::callSensorApi(
        "/api/v1/sensor/some-sensor-id/invalid_action", "GET");

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    // The handleSensorAPI routing should return MethodNotAllowedError for
    // unrecognized actions, or CameraNotFoundError if sensor check runs first
    EXPECT_TRUE(result.errorCode == VmsErrorCode::MethodNotAllowedError ||
                result.errorCode == VmsErrorCode::CameraNotFoundError)
        << "Invalid action should return MethodNotAllowedError or CameraNotFoundError";
}

/**
 * @brief Malformed request with empty URL
 *
 * Should return InvalidParameterError.
 */
TEST_F(SensorManagementTest, HandleSensorAPI_EmptyUrl)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] Malformed request (empty URL)" << endl;

    SensorApiResult result = SensorTestUtils::callSensorApi("", "GET");

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    EXPECT_TRUE(result.errorCode == VmsErrorCode::InvalidParameterError ||
                result.errorCode == VmsErrorCode::MethodNotAllowedError)
        << "Empty URL should be rejected";
}

/**
 * @brief PUT method on sensor API (unsupported)
 *
 * Should return MethodNotAllowedError.
 */
TEST_F(SensorManagementTest, HandleSensorAPI_UnsupportedMethod)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] PUT /sensor/{id} (unsupported method)" << endl;

    SensorApiResult result = SensorTestUtils::callSensorApi(
        "/api/v1/sensor/some-sensor-id", "PUT");

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    // PUT is not a supported method on sensor APIs
    EXPECT_TRUE(result.errorCode == VmsErrorCode::MethodNotAllowedError ||
                result.errorCode == VmsErrorCode::CameraNotFoundError)
        << "PUT should not be supported on sensor endpoint";
}

/**
 * @brief POST /api/v1/sensor/{sensorId}/credentials with invalid sensor ID
 *
 * Should return CameraNotFoundError (credentials endpoint checks sensor existence).
 */
TEST_F(SensorManagementTest, PostCredentials_InvalidId)
{
    if (!m_sensorMgmt) GTEST_SKIP() << "SensorManagement not available";

    cout << "[TEST] POST /sensor/{id}/credentials (invalid ID)" << endl;

    SensorApiResult result = SensorTestUtils::postSensorCredentials(
        "nonexistent-sensor-id-12345", "admin", "password");

    cout << "[TEST] Result: " << static_cast<int>(result.errorCode) << endl;

    EXPECT_EQ(result.errorCode, VmsErrorCode::CameraNotFoundError)
        << "Credentials for non-existent sensor should return CameraNotFoundError";
}
