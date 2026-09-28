/*
 * SPDX-FileCopyrightText: Copyright (c) 2023-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "sensor_info.h"
#include "device_manager.h"
#include "sensor_control_adaptor.h"
#include "logger.h"

#include <map>
#include <memory>

namespace nv_vms
{

struct SensorContollerLoader
{
    ISensorControlInterface * m_contoller;
    destroyControlObject_t m_deleter;
};

class SensorControl
{
    public:
        explicit SensorControl(DeviceManager* deviceMngr);
        ~SensorControl();

        SensorControl(const SensorControl&) = delete;
        SensorControl& operator=(const SensorControl&) = delete;
        SensorControl(SensorControl&&) = delete;
        SensorControl& operator=(SensorControl&&) = delete;

        int connect();
        int getSensorsStreamInfo();
        int getSensorStreamInfo(std::shared_ptr<SensorInfo>& sensorInfo);
        int getStreamInfo(const std::string & sensor_id, const std::string & stream_id, std::shared_ptr<StreamInfo>& stream);
        int synchronizeSensorTime(const std::string sensor_id);
        int rebootSensor(const std::string sensor_id);
        int getSensorNetworkInfo(const std::string sensor_id, SensorNetworkInfo& networkInfo);
        int setSensorNetworkInfo(const std::string sensor_id, const SensorNetworkInfo& networkInfo, bool& rebootNeeded);
        int getSensorSettings(const std::string sensor_id, const std::string stream_id, SensorSettings& settings, const std::string& type);
        int setSensorImageSettings(const std::string sensor_id, const SensorImageSettingsValues& settings);
        int setSensorEncodeSettings(const std::string sensor_id, const SensorVideoEncoderSettingsValues& settings);
        int getStreamSettings(const std::string sensor_id, const std::string& stream_id);
        bool validateCredentials(const std::string sensor_id, const std::string username, const std::string password);
        int setPTZ(const std::string sensor_id, PTZAction ptz, std::string x, std::string y);
        
        // Profile G - Recording Timeline API
        int getRecordingTimelines(const std::string& sensor_id, Json::Value& timelinesJson);

        VmsErrorCode addSensor(const Json::Value& sensorInfo);
        bool deleteSensor(std::shared_ptr<SensorInfo>& sensorInfo);
        int setSensorInfo(const std::string sensor_id);
        int setCacheSensorList();

        std::shared_ptr<SensorInfo> getSensor(const std::string& id);

    private:
        DeviceManager* m_deviceManager;
        std::pair<ISensorControlInterface*, destroyControlObject_t> m_sensorControlobjectPair;
        ISensorControlInterface* m_adaptor;
};

} // nv_vms