/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "sensor_control_adaptor.h"
#include "device_manager.h"

#include <memory>
#include <sstream>
#include <thread>
#include <queue>
#include <mutex>
#include <condition_variable>
#include <chrono>
#include<vector>
#include <jsoncpp/json/json.h>
#include "nvsoap.h"
#include "logger.h"


namespace nv_vms
{

class OnvifClient : public ISensorControlInterface
{
public:
    OnvifClient();
    virtual  ~OnvifClient();

    OnvifClient(const OnvifClient&) = delete;
    OnvifClient& operator=(const OnvifClient&) = delete;
    OnvifClient(OnvifClient&&) = delete;
    OnvifClient& operator=(OnvifClient&&) = delete;

    int connect();
    int getSensorStreamInfo(std::vector<std::shared_ptr<SensorInfo>>& sensors);
    int getSensorStreamInfo(std::shared_ptr<SensorInfo>& sensor);
    int synchronizeSensorTime(std::shared_ptr<SensorInfo>& sensor);
    bool isServerOnline(const std::string & url);
    int setPTZ(std::shared_ptr<SensorInfo>& sensor, PTZAction, std::string x, std::string y);
    std::map<PTZAction, ptzRange> getPTZ(std::shared_ptr<SensorInfo>& sensor) override;
    bool validateCredentials(std::shared_ptr<SensorInfo>& sensor, const std::string username, const std::string password);
    int getNetworkInfo(std::shared_ptr<SensorInfo>& sensor, SensorNetworkInfo& networkInfo);
    int setNetworkInfo(std::shared_ptr<SensorInfo>& sensor, const SensorNetworkInfo& networkInfo, bool& rebootNeeded);
    int getSensorImageSettings(std::shared_ptr<SensorInfo>& sensor, const std::string& stream_id, SensorSettings& settings);
    int setSensorImageSettings(std::shared_ptr<SensorInfo>& sensor, const SensorImageSettingsValues& settings);
    int getSensorEncodeSettings(std::shared_ptr<SensorInfo>& sensor, const std::string& stream_id, SensorSettings& settings);
    int setSensorEncodeSettings(std::shared_ptr<SensorInfo>& sensor, const SensorVideoEncoderSettingsValues& settings);
    int rebootSensor(std::shared_ptr<SensorInfo>& sensor);
    int getStreamSettings(std::shared_ptr<SensorInfo>& sensor, const std::string& stream_id);
    
    // Profile G - Recording Timeline APIs
    int getRecordingTimelines(std::shared_ptr<SensorInfo>& sensor, Json::Value& timelinesJson);
private:
    int fetchSensorStreamInfo(std::shared_ptr<SensorInfo> sensor);
    int fetchSensorStreamInfo(std::vector<std::shared_ptr<SensorInfo>>& sensors);
    int getSensorStreamInfo(SensorInfo& sensor);
    bool isSensorExists(const std::string& id);
    std::shared_ptr<SensorInfo> getSensor(const std::string& id);
    int restoreSensorCapabilitiesFromCache(std::shared_ptr<SensorInfo>& sensor);
};

} //nv_vms
