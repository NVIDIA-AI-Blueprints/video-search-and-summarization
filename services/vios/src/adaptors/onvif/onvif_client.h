/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

using namespace std;
using namespace nv_vms;

namespace nv_vms
{

class OnvifClient : public ISensorControlInterface
{
public:
    OnvifClient();
    virtual  ~OnvifClient();

    int connect();
    int getSensorStreamInfo(vector<shared_ptr<SensorInfo>>& sensors);
    int getSensorStreamInfo(shared_ptr<SensorInfo>& sensor);
    int synchronizeSensorTime(shared_ptr<SensorInfo>& sensor);
    bool isServerOnline(const string & url);
    int setPTZ(shared_ptr<SensorInfo>& sensor, PTZAction, string x, string y);
    map<PTZAction, ptzRange> getPTZ(shared_ptr<SensorInfo> sensor);
    bool validateCredentials(shared_ptr<SensorInfo>& sensor, const string username, const string password);
    int getNetworkInfo(shared_ptr<SensorInfo>& sensor, SensorNetworkInfo& networkInfo);
    int setNetworkInfo(shared_ptr<SensorInfo>& sensor, const SensorNetworkInfo& networkInfo, bool& rebootNeeded);
    int getSensorImageSettings(shared_ptr<SensorInfo>& sensor, const string& stream_id, SensorSettings& settings);
    int setSensorImageSettings(shared_ptr<SensorInfo>& sensor, const SensorImageSettingsValues& settings);
    int getSensorEncodeSettings(shared_ptr<SensorInfo>& sensor, const string& stream_id, SensorSettings& settings);
    int setSensorEncodeSettings(shared_ptr<SensorInfo>& sensor, const SensorVideoEncoderSettingsValues& settings);
    int rebootSensor(shared_ptr<SensorInfo>& sensor);
    int getStreamSettings(shared_ptr<SensorInfo>& sensor, const string& stream_id);
    
    // Profile G - Recording Timeline APIs
    int getRecordingTimelines(shared_ptr<SensorInfo>& sensor, Json::Value& timelinesJson);
private:
    int fetchSensorStreamInfo(shared_ptr<SensorInfo> sensor);
    int fetchSensorStreamInfo(vector<shared_ptr<SensorInfo>>& sensors);
    int getSensorStreamInfo(SensorInfo& sensor);
    bool isSensorExists(const string& id);
    std::shared_ptr<SensorInfo> getSensor(const string& id);
    int restoreSensorCapabilitiesFromCache(shared_ptr<SensorInfo>& sensor);
};

} //nv_vms
