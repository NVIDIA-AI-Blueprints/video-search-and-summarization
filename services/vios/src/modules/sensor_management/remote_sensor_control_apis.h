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
#include "database.h"
#include "datachannellistenerinterface.h"
#include "webrtcDataChannel.h"

using namespace nv_vms;

class RemoteSensorControlApis
{
    public:
        RemoteSensorControlApis(std::shared_ptr<nv_vms::SensorManagement> sensorMgmt, std::shared_ptr<DeviceManager> deviceMngr);
        ~RemoteSensorControlApis()
        {
            LOG(info) << __PRETTY_FUNCTION__ << endl;
        }
        typedef std::function<void(const Json::Value& receivedData, Json::Value& response)> remoteSensorFunc;
        const std::map<std::string, remoteSensorFunc, std::less<>> getRemoteSensorControlApis() { return m_func; };
        void handleSensorCredentials(const Json::Value &data, Json::Value &response);
        void handleSensorSettings(const Json::Value &receivedData, Json::Value &response);
        void handleSensorAdd(const Json::Value &receivedData, Json::Value &response);
        void handleSensorRemove(const Json::Value &receivedData, Json::Value &response);
        void handleSensorStatus(const Json::Value &receivedData, Json::Value &response);
        void handleNetworkSettings(const Json::Value &receivedData, Json::Value &response);
        void handleSensorInfoSettings(const Json::Value &receivedData, Json::Value &response);
    private:
        std::shared_ptr<nv_vms::SensorManagement> m_sensorManagement;
        std::map<std::string, remoteSensorFunc, std::less<>>  m_func;
        std::shared_ptr<DeviceManager> m_deviceManager;
};
