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

#include "sensor_control_adaptor.h"
#include "device_manager.h"
#include "logger.h"
#include "libasync++/async++.h"

#include <vector>
#include <jsoncpp/json/json.h>
#include "MessageBus.h"

using namespace std;

class RemoteDevice : public ISensorControlInterface
{
    public:
        RemoteDevice() 
        {
            GET_DATA_CHANNEL();
            m_messageBus = std::make_shared<MessageBus>();
        }
        virtual ~RemoteDevice() 
        {
            LOG(info) << __PRETTY_FUNCTION__ << endl;
            LOG(info) << "Waiting for "<< m_dataChannelTasks.size() << " async tasks to finish" << endl;
            for (auto &asyncTasks: m_dataChannelTasks)
            {
                asyncTasks.get();
            }
            LOG(info) << "Async tasks finished" << endl;
            m_sensorStatusMonitoring.reset();
            m_messageBus.reset();
            GET_DATA_CHANNEL()->deleteDataChannelInstance();
        }

        int connect();
        int getSensorImageSettings(shared_ptr<SensorInfo>& sensor, const string& stream_id, SensorSettings& settings);
        int setSensorImageSettings(shared_ptr<SensorInfo>& sensor, const SensorImageSettingsValues& settings);
        int getSensorEncodeSettings(shared_ptr<SensorInfo>& sensor, const string& stream_id, SensorSettings& settings);
        int setSensorEncodeSettings(shared_ptr<SensorInfo>& sensor, const SensorVideoEncoderSettingsValues& settings);
        bool validateCredentials(shared_ptr<SensorInfo>& sensor, const string username, const string password);
        VmsErrorCode addSensor(const Json::Value& sensorInfo);
        bool deleteSensor(shared_ptr<SensorInfo>& sensor);

        int getSensorStreamInfo(vector<shared_ptr<SensorInfo>>& sensors);
        int getSensorStreamInfo(shared_ptr<SensorInfo>& sensor);
        int getNetworkInfo(shared_ptr<SensorInfo>& sensor, SensorNetworkInfo& networkInfo);
        int setNetworkInfo(shared_ptr<SensorInfo>& sensor, const SensorNetworkInfo& networkInfo, bool& rebootNeeded);
        int setSensorInfo(shared_ptr<SensorInfo> &sensor);

        bool isServerOnline(const string & url) { return true; }

    private:
        VmsErrorCode getSensorSettings(shared_ptr<SensorInfo>& sensor, const string& type, Json::Value &response);
        VmsErrorCode setSensorSettings(shared_ptr<SensorInfo> &sensor, const Json::Value& settings, Json::Value &response);
        VmsErrorCode validateCredentials(shared_ptr<SensorInfo>& sensor, Json::Value &credentials, Json::Value &response);
        VmsErrorCode deleteSensor(shared_ptr<SensorInfo>& sensor, Json::Value &response);
        VmsErrorCode getSensorNetworkSettings(shared_ptr<SensorInfo>& sensor, Json::Value &response);
        VmsErrorCode setSensorNetworkSettings(shared_ptr<SensorInfo> &sensor, const Json::Value &settings, Json::Value &response);
        VmsErrorCode setSensorInfoSettings(shared_ptr<SensorInfo> &sensor, const Json::Value &settings, Json::Value &response);
        void syncSensorStatus(pair<string, string> sensorInfo);
        void syncSensorStatus();

        std::unique_ptr<Bosma::Scheduler>                   m_sensorStatusMonitoring;
        std::shared_ptr<MessageBus>                         m_messageBus;
        vector<async::task<void>>                           m_dataChannelTasks;
};