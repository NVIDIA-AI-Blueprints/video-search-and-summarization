/*
 * SPDX-FileCopyrightText: Copyright (c) 2023-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#ifdef USE_GRPC_CLIENT
#include "nvgrpc.h"
#include "gst_utils.h"
#endif
#include "remote_sensor_control_apis.h"
#include "testRTSP.h"
#include "utils.h"
#include "sensor_management_utils.h"

using namespace std;

RemoteSensorControlApis::RemoteSensorControlApis(std::shared_ptr<SensorManagement> sensorMgmt, std::shared_ptr<DeviceManager> deviceMngr): m_sensorManagement(sensorMgmt), m_deviceManager(deviceMngr)
{
    // VST edge to cloud APIs
    m_func["api/v1/sensor/credentials"] = [this](const Json::Value& receivedData, Json::Value &response) -> void
    {
        handleSensorCredentials(receivedData, response);
    };
    m_func["api/v1/sensor/add"] = [this](const Json::Value& receivedData, Json::Value &response) -> void
    {
        handleSensorAdd(receivedData, response);
    };
    m_func["api/v1/sensor/remove"] = [this](const Json::Value& receivedData, Json::Value &response) -> void
    {
        handleSensorRemove(receivedData, response);
    };
    m_func["api/v1/sensor/settings"] = [this](const Json::Value& receivedData, Json::Value &response) -> void
    {
        handleSensorSettings(receivedData, response);
    };
    m_func["api/v1/sensor/status"] = [this](const Json::Value& receivedData, Json::Value &response) -> void
    {
        handleSensorStatus(receivedData, response);
    };
    m_func["api/v1/sensor/netsettings"] = [this](const Json::Value& receivedData, Json::Value &response) -> void
    {
        handleNetworkSettings(receivedData, response);
    };
    m_func["api/v1/sensor/info"] = [this](const Json::Value& receivedData, Json::Value &response) -> void
    {
        handleSensorInfoSettings(receivedData, response);
    };
}

void RemoteSensorControlApis::handleSensorInfoSettings(const Json::Value &receivedData, Json::Value &response)
{
    Json::Value apiData;
    std::string requestId;
    std::string sensorId;
    Json::Value responseData;
    std::string requestMethod;
    VmsErrorCode ret = VmsErrorCode::NoError;
    apiData = receivedData.get("data", Json::nullValue);
    requestId = receivedData.get("requestId", EMPTY_STRING).asString();
    sensorId = receivedData.get("sensorId", EMPTY_STRING).asString();
    requestMethod = receivedData.get("requestMethod", EMPTY_STRING).asString();
    if(iequals(requestMethod, "post"))
    {
        ret = setSensorInfo(m_sensorManagement, sensorId, apiData, responseData, true, false);
    }
    else
    {
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, responseData)
        ret = VmsErrorCode::MethodNotAllowedError;
    }
    response["requestId"] = requestId;
    response["data"] = responseData;
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "Unable to set sensor info settings " << sensorId << endl;
    }
}

void RemoteSensorControlApis::handleNetworkSettings(const Json::Value &receivedData, Json::Value &response)
{
    Json::Value apiData;
    std::string requestId;
    std::string sensorId;
    Json::Value responseData;
    std::string requestMethod;
    VmsErrorCode ret = VmsErrorCode::NoError;
    apiData = receivedData.get("data", Json::nullValue);
    requestId = receivedData.get("requestId", EMPTY_STRING).asString();
    sensorId = receivedData.get("sensorId", EMPTY_STRING).asString();
    requestMethod = receivedData.get("requestMethod", EMPTY_STRING).asString();
    if(iequals(requestMethod, "get"))
    {
        ret = getSensorNetworkInfo(m_sensorManagement, sensorId, responseData);
    }
    else if(iequals(requestMethod, "post"))
    {
        ret = setSensorNetworkInfo(m_sensorManagement, sensorId, apiData, responseData, true, false);
    }
    else
    {
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, responseData)
        ret = VmsErrorCode::MethodNotAllowedError;
    }
    response["requestId"] = requestId;
    response["data"] = responseData;
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "Unable to set/get sensor network settings " << sensorId << endl;
    }
}

void RemoteSensorControlApis::handleSensorCredentials(const Json::Value &receivedData, Json::Value &response)
{
    Json::Value apiData;
    std::string requestId;
    std::string sensorId;
    Json::Value responseData;
    apiData = receivedData.get("data", Json::nullValue);
    requestId = receivedData.get("requestId", EMPTY_STRING).asString();
    sensorId = receivedData.get("sensorId", EMPTY_STRING).asString();
    VmsErrorCode ret = setSensorCredentials(m_sensorManagement, sensorId, apiData, responseData);
    response["requestId"] = requestId;
    response["data"] = responseData;
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "Unable to verify camera credentials" << endl;
    }
}

void RemoteSensorControlApis::handleSensorStatus(const Json::Value &receivedData, Json::Value &response)
{
    Json::Value apiData;
    std::string requestId;
    std::string sensorId;
    Json::Value responseData;
    apiData = receivedData.get("data", Json::nullValue);
    requestId = receivedData.get("requestId", EMPTY_STRING).asString();
    sensorId = receivedData.get("sensorId", EMPTY_STRING).asString();
    VmsErrorCode ret = getSensorStatus(m_sensorManagement, sensorId, responseData);
    response["requestId"] = requestId;
    response["data"] = responseData;
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "Unable to get sensor status, sensor ID: " << sensorId << endl;
    }
}

void RemoteSensorControlApis::handleSensorAdd(const Json::Value &receivedData, Json::Value &response)
{
    Json::Value reqInfo, apiData;
    std::string requestId;
    std::string sensorId;
    Json::Value responseData;
    apiData = receivedData.get("data", Json::nullValue);
    requestId = receivedData.get("requestId", EMPTY_STRING).asString();

    VmsErrorCode ret = addSensor(m_sensorManagement, reqInfo, apiData, responseData);
    response["requestId"] = requestId;
    response["data"] = responseData;
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "Unable to add sensor " << sensorId << endl;
    }
}

void RemoteSensorControlApis::handleSensorRemove(const Json::Value &receivedData, Json::Value &response)
{
    Json::Value apiData;
    std::string requestId;
    std::string sensorId;
    Json::Value responseData;
    std::string requestMethod;
    VmsErrorCode ret = VmsErrorCode::NoError;
    apiData = receivedData.get("data", Json::nullValue);
    requestId = receivedData.get("requestId", EMPTY_STRING).asString();
    sensorId = receivedData.get("sensorId", EMPTY_STRING).asString();
    requestMethod = receivedData.get("requestMethod", EMPTY_STRING).asString();
    if(iequals(requestMethod, "delete"))
    {
        ret = deleteSensor(m_sensorManagement, sensorId, responseData, true, false);
    }
    else
    {
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, responseData)
        ret = VmsErrorCode::MethodNotAllowedError;
    }
    response["requestId"] = requestId;
    response["data"] = responseData;
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "Unable to remove sensor " << sensorId << endl;
    }
}

void RemoteSensorControlApis::handleSensorSettings(const Json::Value &receivedData, Json::Value &response)
{
    Json::Value apiData;
    std::string requestId;
    std::string sensorId;
    Json::Value responseData;
    std::string requestMethod;
    VmsErrorCode ret = VmsErrorCode::NoError;
    apiData = receivedData.get("data", Json::nullValue);
    requestId = receivedData.get("requestId", EMPTY_STRING).asString();
    sensorId = receivedData.get("sensorId", EMPTY_STRING).asString();
    requestMethod = receivedData.get("requestMethod", EMPTY_STRING).asString();
    if(iequals(requestMethod, "get"))
    {
        ret = getSensorSettings(m_sensorManagement, sensorId, "", responseData);
    }
    else if(iequals(requestMethod, "post"))
    {
        ret = setSensorSettings(m_sensorManagement, sensorId, apiData, responseData);
    }
    else
    {
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, responseData)
        ret = VmsErrorCode::MethodNotAllowedError;
    }
    response["requestId"] = requestId;
    response["data"] = responseData;
    if (ret != VmsErrorCode::NoError)
    {
        LOG(error) << "Unable to set/get sensor settings " << sensorId << endl;
    }
}
