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
 * @file sensor_test_utils.cpp
 * @brief Implementation of common sensor test utilities
 */

#include "sensor_test_utils.h"
#include <iostream>

using namespace std;

// ==================== Internal helpers ====================

/**
 * @brief Extract error_message from a response JSON if present.
 */
static string extractError(const Json::Value& response)
{
    if (response.isObject() && response.isMember("error_message"))
        return response["error_message"].asString();
    return {};
}

/**
 * @brief Invoke a SensorManagementApis method via the HTTP-function map.
 *
 * The API class registers all routes as lambdas keyed by URL pattern.  For
 * fixed routes (/api/v1/sensor/list, etc.) we look up the route directly.
 * For wildcard /api/v1/sensor/{id}/{action} routes we use the "*" entry.
 */
static SensorApiResult invokeRoute(SensorManagementApis* apis,
                                    const string& routeKey,
                                    const Json::Value& reqInfo,
                                    const Json::Value& body,
                                    struct mg_connection* conn)
{
    SensorApiResult result;
    result.errorCode = VmsErrorCode::VMSInternalError;

    if (apis == nullptr)
    {
        result.errorMessage = "SensorManagementApis is null";
        return result;
    }

    auto funcMap = apis->getHttpApi();
    auto it = funcMap.find(routeKey);
    if (it != funcMap.end())
    {
        result.errorCode = it->second(reqInfo, body, result.response, conn);
    }
    else
    {
        // Fall back to the wildcard handler
        auto wild = funcMap.find("/api/v1/sensor/*");
        if (wild != funcMap.end())
        {
            result.errorCode = wild->second(reqInfo, body, result.response, conn);
        }
        else
        {
            result.errorMessage = "No matching route for: " + routeKey;
        }
    }

    std::string extracted = extractError(result.response);
    if (!extracted.empty())
        result.errorMessage = extracted;
    return result;
}

// ==================== SensorTestUtils Implementation ====================

namespace SensorTestUtils
{

// ---- Request helpers ----------------------------------------------------

Json::Value createSensorRequest(const string& url,
                                 const string& method,
                                 const string& query)
{
    Json::Value req;
    req["url"]    = url;
    req["method"] = method;
    if (!query.empty())
        req["query"] = query;
    return req;
}

SensorManagementApis* createSensorApis()
{
    SensorManagement* sensorMgmt = GET_SENSOR_MNGT();
    if (sensorMgmt == nullptr)
    {
        cout << "[SENSOR-UTIL] SensorManagement module not available" << endl;
        return nullptr;
    }

    shared_ptr<DeviceManager> deviceMgr =
        ModuleLoader::getInstance()->getDeviceManagerObject();
    if (deviceMgr == nullptr)
    {
        cout << "[SENSOR-UTIL] DeviceManager not available" << endl;
        return nullptr;
    }

    // SensorManagementApis needs shared_ptr<SensorManagement>.
    // SensorManagement is managed by ModuleLoader; wrap with a no-op deleter.
    shared_ptr<SensorManagement> sensorPtr(sensorMgmt, SensorManagementObjDeleter{});

    return new SensorManagementApis(sensorPtr, deviceMgr);
}

// ---- Global endpoints ---------------------------------------------------

SensorApiResult getSensorList()
{
    unique_ptr<SensorManagementApis> apis(createSensorApis());
    Json::Value req = createSensorRequest("/api/v1/sensor/list", "GET");
    return invokeRoute(apis.get(), "/api/v1/sensor/list", req, Json::nullValue, nullptr);
}

SensorApiResult getSensorVersion()
{
    unique_ptr<SensorManagementApis> apis(createSensorApis());
    Json::Value req = createSensorRequest("/api/v1/sensor/version", "GET");
    return invokeRoute(apis.get(), "/api/v1/sensor/version", req, Json::nullValue, nullptr);
}

SensorApiResult getSensorHelp()
{
    unique_ptr<SensorManagementApis> apis(createSensorApis());
    Json::Value req = createSensorRequest("/api/v1/sensor/help", "GET");
    return invokeRoute(apis.get(), "/api/v1/sensor/help", req, Json::nullValue, nullptr);
}

SensorApiResult getSensorConfiguration()
{
    unique_ptr<SensorManagementApis> apis(createSensorApis());
    Json::Value req = createSensorRequest("/api/v1/sensor/configuration", "GET");
    return invokeRoute(apis.get(), "/api/v1/sensor/configuration", req, Json::nullValue, nullptr);
}

SensorApiResult postSensorConfiguration(const Json::Value& config)
{
    unique_ptr<SensorManagementApis> apis(createSensorApis());
    Json::Value req = createSensorRequest("/api/v1/sensor/configuration", "POST");
    return invokeRoute(apis.get(), "/api/v1/sensor/configuration", req, config, nullptr);
}

SensorApiResult getSensorQos()
{
    unique_ptr<SensorManagementApis> apis(createSensorApis());
    Json::Value req = createSensorRequest("/api/v1/sensor/qos", "GET");
    return invokeRoute(apis.get(), "/api/v1/sensor/qos", req, Json::nullValue, nullptr);
}

SensorApiResult getAllTimelines()
{
    unique_ptr<SensorManagementApis> apis(createSensorApis());
    Json::Value req = createSensorRequest("/api/v1/sensor/timelines", "GET");
    return invokeRoute(apis.get(), "/api/v1/sensor/timelines", req, Json::nullValue, nullptr);
}

SensorApiResult postSensorScan()
{
    unique_ptr<SensorManagementApis> apis(createSensorApis());
    Json::Value req = createSensorRequest("/api/v1/sensor/scan", "POST");
    return invokeRoute(apis.get(), "/api/v1/sensor/scan", req, Json::nullValue, nullptr);
}

// ---- Sensor-add endpoints -----------------------------------------------

SensorApiResult addSensorByIp(const string& ip,
                               const string& username,
                               const string& password,
                               const string& name)
{
    unique_ptr<SensorManagementApis> apis(createSensorApis());
    Json::Value req = createSensorRequest("/api/v1/sensor/add", "POST");
    Json::Value body;
    body["sensorIp"] = ip;
    body["username"]  = username;
    body["password"]  = password;
    if (!name.empty())
        body["name"] = name;
    return invokeRoute(apis.get(), "/api/v1/sensor/add", req, body, nullptr);
}

SensorApiResult addSensorByUrl(const string& rtspUrl,
                                const string& username,
                                const string& password,
                                const string& name)
{
    unique_ptr<SensorManagementApis> apis(createSensorApis());
    Json::Value req = createSensorRequest("/api/v1/sensor/add", "POST");
    Json::Value body;
    body["sensorUrl"] = rtspUrl;
    body["username"]   = username;
    body["password"]   = password;
    if (!name.empty())
        body["name"] = name;
    return invokeRoute(apis.get(), "/api/v1/sensor/add", req, body, nullptr);
}

// ---- Per-sensor endpoints -----------------------------------------------

SensorApiResult getSensorInfo(const string& sensorId)
{
    return callSensorApi("/api/v1/sensor/" + sensorId + "/info", "GET");
}

SensorApiResult postSensorInfo(const string& sensorId, const Json::Value& body)
{
    return callSensorApi("/api/v1/sensor/" + sensorId + "/info", "POST", body);
}

SensorApiResult getSensorStatus(const string& sensorId)
{
    return callSensorApi("/api/v1/sensor/" + sensorId + "/status", "GET");
}

SensorApiResult getSensorStreams(const string& sensorId)
{
    return callSensorApi("/api/v1/sensor/" + sensorId + "/streams", "GET");
}

SensorApiResult getSensorTimelines(const string& sensorId,
                                    const string& startTime,
                                    const string& endTime)
{
    string query;
    if (!startTime.empty())
        query += "startTime=" + startTime;
    if (!endTime.empty())
    {
        if (!query.empty()) query += "&";
        query += "endTime=" + endTime;
    }
    return callSensorApi("/api/v1/sensor/" + sensorId + "/timelines", "GET",
                          Json::nullValue, query);
}

SensorApiResult getSensorSettings(const string& sensorId)
{
    return callSensorApi("/api/v1/sensor/" + sensorId + "/settings", "GET");
}

SensorApiResult postSensorSettings(const string& sensorId, const Json::Value& body)
{
    return callSensorApi("/api/v1/sensor/" + sensorId + "/settings", "POST", body);
}

SensorApiResult getSensorNetwork(const string& sensorId)
{
    return callSensorApi("/api/v1/sensor/" + sensorId + "/network", "GET");
}

SensorApiResult postSensorNetwork(const string& sensorId, const Json::Value& body)
{
    return callSensorApi("/api/v1/sensor/" + sensorId + "/network", "POST", body);
}

SensorApiResult postSensorCredentials(const string& sensorId,
                                       const string& username,
                                       const string& password)
{
    Json::Value body;
    body["username"] = username;
    body["password"] = password;
    return callSensorApi("/api/v1/sensor/" + sensorId + "/credentials", "POST", body);
}

SensorApiResult postSensorReplace(const string& oldSensorId,
                                   const string& newSensorId)
{
    Json::Value body;
    body["sensorId"] = newSensorId;
    return callSensorApi("/api/v1/sensor/" + oldSensorId + "/replace", "POST", body);
}

SensorApiResult postSensorReboot(const string& sensorId)
{
    return callSensorApi("/api/v1/sensor/" + sensorId + "/reboot", "POST");
}

SensorApiResult deleteSensor(const string& sensorId)
{
    return callSensorApi("/api/v1/sensor/" + sensorId, "DELETE");
}

// ---- Generic wildcard caller --------------------------------------------

SensorApiResult callSensorApi(const string& url,
                               const string& method,
                               const Json::Value& body,
                               const string& query)
{
    unique_ptr<SensorManagementApis> apis(createSensorApis());
    Json::Value req = createSensorRequest(url, method, query);

    // Per-sensor routes are dispatched through the wildcard entry
    MockConnection mockConn;
    mockConn.requestInfo.setMethod(method);
    mockConn.requestInfo.setUri(url);
    if (!query.empty())
        mockConn.requestInfo.setQueryString(query);
    struct mg_connection* conn = reinterpret_cast<struct mg_connection*>(&mockConn);

    return invokeRoute(apis.get(), "/api/v1/sensor/*", req, body, conn);
}

} // namespace SensorTestUtils
