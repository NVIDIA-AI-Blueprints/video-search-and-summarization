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
 * @file sensor_test_utils.h
 * @brief Common utilities for sensor management unit tests
 *
 * Provides reusable helper functions for invoking sensor management API
 * endpoints from any test file.  Follows the same patterns as
 * storage_test_utils.h so that both modules share a consistent test style.
 */

#ifndef SENSOR_TEST_UTILS_H
#define SENSOR_TEST_UTILS_H

#include <string>
#include <vector>
#include <jsoncpp/json/json.h>
#include "sensor_management.h"
#include "sensor_management_apis.h"
#include "vstmodule.h"
#include "mock_civetweb.h"

using namespace std;
using namespace nv_vms;

/**
 * @brief Result of a sensor API call in tests
 */
struct SensorApiResult
{
    VmsErrorCode errorCode;
    Json::Value  response;
    string       errorMessage;

    bool isSuccess() const { return errorCode == VmsErrorCode::NoError; }
};

/**
 * @brief Sensor test utilities namespace
 *
 * Each function builds the appropriate req_info JSON, invokes the
 * SensorManagementApis handler, and returns a SensorApiResult.
 */
namespace SensorTestUtils
{
    // ---- Request helpers ------------------------------------------------

    /**
     * @brief Build a req_info JSON suitable for sensor API calls.
     */
    Json::Value createSensorRequest(const string& url,
                                     const string& method,
                                     const string& query = "");

    /**
     * @brief Construct a SensorManagementApis instance from ModuleLoader.
     *
     * Returns nullptr if the sensor management module is not initialized.
     * Caller owns the returned pointer.
     */
    SensorManagementApis* createSensorApis();

    // ---- Global (non-sensor-specific) endpoints -------------------------

    /** GET /api/v1/sensor/list */
    SensorApiResult getSensorList();

    /** GET /api/v1/sensor/version */
    SensorApiResult getSensorVersion();

    /** GET /api/v1/sensor/help */
    SensorApiResult getSensorHelp();

    /** GET /api/v1/sensor/configuration */
    SensorApiResult getSensorConfiguration();

    /** POST /api/v1/sensor/configuration */
    SensorApiResult postSensorConfiguration(const Json::Value& config);

    /** GET /api/v1/sensor/qos */
    SensorApiResult getSensorQos();

    /** GET /api/v1/sensor/timelines */
    SensorApiResult getAllTimelines();

    /** POST /api/v1/sensor/scan */
    SensorApiResult postSensorScan();

    // ---- Sensor-add endpoints -------------------------------------------

    /** POST /api/v1/sensor/add  (by IP) */
    SensorApiResult addSensorByIp(const string& ip,
                                   const string& username,
                                   const string& password,
                                   const string& name = "");

    /** POST /api/v1/sensor/add  (by RTSP URL) */
    SensorApiResult addSensorByUrl(const string& rtspUrl,
                                    const string& username,
                                    const string& password,
                                    const string& name = "");

    // ---- Per-sensor endpoints -------------------------------------------

    /** GET /api/v1/sensor/{sensorId}/info */
    SensorApiResult getSensorInfo(const string& sensorId);

    /** POST /api/v1/sensor/{sensorId}/info */
    SensorApiResult postSensorInfo(const string& sensorId,
                                    const Json::Value& body);

    /** GET /api/v1/sensor/{sensorId}/status */
    SensorApiResult getSensorStatus(const string& sensorId);

    /** GET /api/v1/sensor/{sensorId}/streams */
    SensorApiResult getSensorStreams(const string& sensorId);

    /** GET /api/v1/sensor/{sensorId}/timelines */
    SensorApiResult getSensorTimelines(const string& sensorId,
                                        const string& startTime = "",
                                        const string& endTime = "");

    /** GET /api/v1/sensor/{sensorId}/settings */
    SensorApiResult getSensorSettings(const string& sensorId);

    /** POST /api/v1/sensor/{sensorId}/settings */
    SensorApiResult postSensorSettings(const string& sensorId,
                                        const Json::Value& body);

    /** GET /api/v1/sensor/{sensorId}/network */
    SensorApiResult getSensorNetwork(const string& sensorId);

    /** POST /api/v1/sensor/{sensorId}/network */
    SensorApiResult postSensorNetwork(const string& sensorId,
                                       const Json::Value& body);

    /** POST /api/v1/sensor/{sensorId}/credentials */
    SensorApiResult postSensorCredentials(const string& sensorId,
                                           const string& username,
                                           const string& password);

    /** POST /api/v1/sensor/{sensorId}/replace */
    SensorApiResult postSensorReplace(const string& oldSensorId,
                                       const string& newSensorId);

    /** POST /api/v1/sensor/{sensorId}/reboot */
    SensorApiResult postSensorReboot(const string& sensorId);

    /** DELETE /api/v1/sensor/{sensorId} */
    SensorApiResult deleteSensor(const string& sensorId);

    // ---- Generic wildcard caller ----------------------------------------

    /**
     * @brief Call handleSensorAPIrequest for any /api/v1/sensor/{...} route.
     *
     * Use this for ad-hoc calls not covered by the typed helpers above.
     */
    SensorApiResult callSensorApi(const string& url,
                                   const string& method,
                                   const Json::Value& body = Json::nullValue,
                                   const string& query = "");
}

#endif // SENSOR_TEST_UTILS_H
