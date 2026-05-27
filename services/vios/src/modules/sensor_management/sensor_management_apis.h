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

#pragma once

#include "sensor_management.h"
#include <jsoncpp/json/json.h>
#include "HttpServerRequestHandler.h"
#include "modules_apis.h"
#include "testRTSP.h"

using namespace nv_vms;

class SensorManagementApis
{
public:
    SensorManagementApis(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, std::shared_ptr<DeviceManager> deviceManager);
    const std::map<std::string,HttpServerRequestHandler::httpFunction, std::less<>> getHttpApi() { return m_func; };
    VmsErrorCode getSensorInfoList(const Json::Value& req_info, Json::Value &response);
    VmsErrorCode handleSensorConfiguration(const Json::Value& req_info, const Json::Value &in, Json::Value &response);
    VmsErrorCode getVersion(const Json::Value& req_info, const Json::Value &in, Json::Value &response);
    VmsErrorCode getSensorHelp(const Json::Value& req_info, const Json::Value &in, Json::Value &response);

    VmsErrorCode handleSensorAPIrequest(const Json::Value& req_info, const Json::Value &in, Json::Value &response,
                                        struct mg_connection *conn);
    VmsErrorCode handleSensorAPI(const Json::Value& req_info, const Json::Value &in, Json::Value &response,
                                        struct mg_connection *conn);
    VmsErrorCode handleSensorDebugAPI(const Json::Value& req_info, const Json::Value &in, Json::Value &response,
                                        struct mg_connection *conn);
    VmsErrorCode getSensorQosInfo(const Json::Value& req_info, Json::Value &response);
    VmsErrorCode getAllSensorTimelines(const Json::Value& req_info, Json::Value &response);

private:
    std::map<std::string,HttpServerRequestHandler::httpFunction, std::less<>>  m_func;
    std::shared_ptr<nv_vms::SensorManagement> m_sensorManagement;
    std::shared_ptr<DeviceManager> m_deviceManager;
};
