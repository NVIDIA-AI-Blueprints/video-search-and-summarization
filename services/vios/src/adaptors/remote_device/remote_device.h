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


class RemoteDevice : public nv_vms::ISensorControlInterface
{
    public:
        RemoteDevice() 
        {
            GET_DATA_CHANNEL();
            m_messageBus = std::make_shared<MessageBus>();
        }
        virtual ~RemoteDevice() 
        {
            LOG(info) << __PRETTY_FUNCTION__ << std::endl;
            LOG(info) << "Waiting for "<< m_dataChannelTasks.size() << " async tasks to finish" << std::endl;
            for (auto &asyncTasks: m_dataChannelTasks)
            {
                asyncTasks.get();
            }
            LOG(info) << "Async tasks finished" << std::endl;
            m_sensorStatusMonitoring.reset();
            m_messageBus.reset();
            GET_DATA_CHANNEL()->deleteDataChannelInstance();
        }

        int connect();
        int getSensorImageSettings(std::shared_ptr<nv_vms::SensorInfo>& sensor, const std::string& stream_id, nv_vms::SensorSettings& settings);
        int setSensorImageSettings(std::shared_ptr<nv_vms::SensorInfo>& sensor, const nv_vms::SensorImageSettingsValues& settings);
        int getSensorEncodeSettings(std::shared_ptr<nv_vms::SensorInfo>& sensor, const std::string& stream_id, nv_vms::SensorSettings& settings);
        int setSensorEncodeSettings(std::shared_ptr<nv_vms::SensorInfo>& sensor, const nv_vms::SensorVideoEncoderSettingsValues& settings);
        bool validateCredentials(std::shared_ptr<nv_vms::SensorInfo>& sensor, const std::string username, const std::string password);
        nv_vms::VmsErrorCode addSensor(const Json::Value& sensorInfo);
        bool deleteSensor(std::shared_ptr<nv_vms::SensorInfo>& sensor);

        int getSensorStreamInfo(std::vector<std::shared_ptr<nv_vms::SensorInfo>>& sensors);
        int getSensorStreamInfo(std::shared_ptr<nv_vms::SensorInfo>& sensor);
        int getNetworkInfo(std::shared_ptr<nv_vms::SensorInfo>& sensor, nv_vms::SensorNetworkInfo& networkInfo);
        int setNetworkInfo(std::shared_ptr<nv_vms::SensorInfo>& sensor, const nv_vms::SensorNetworkInfo& networkInfo, bool& rebootNeeded);
        int setSensorInfo(std::shared_ptr<nv_vms::SensorInfo> &sensor);

        bool isServerOnline(const std::string & url) { return true; }

    private:
        nv_vms::VmsErrorCode getSensorSettings(std::shared_ptr<nv_vms::SensorInfo>& sensor, const std::string& type, Json::Value &response);
        nv_vms::VmsErrorCode setSensorSettings(std::shared_ptr<nv_vms::SensorInfo> &sensor, const Json::Value& settings, Json::Value &response);
        nv_vms::VmsErrorCode validateCredentials(std::shared_ptr<nv_vms::SensorInfo>& sensor, Json::Value &credentials, Json::Value &response);
        nv_vms::VmsErrorCode deleteSensor(std::shared_ptr<nv_vms::SensorInfo>& sensor, Json::Value &response);
        nv_vms::VmsErrorCode getSensorNetworkSettings(std::shared_ptr<nv_vms::SensorInfo>& sensor, Json::Value &response);
        nv_vms::VmsErrorCode setSensorNetworkSettings(std::shared_ptr<nv_vms::SensorInfo> &sensor, const Json::Value &settings, Json::Value &response);
        nv_vms::VmsErrorCode setSensorInfoSettings(std::shared_ptr<nv_vms::SensorInfo> &sensor, const Json::Value &settings, Json::Value &response);
        void syncSensorStatus(std::pair<std::string, std::string> sensorInfo);
        void syncSensorStatus();

        std::unique_ptr<Bosma::Scheduler>                   m_sensorStatusMonitoring;
        std::shared_ptr<MessageBus>                         m_messageBus;
        std::vector<async::task<void>>                           m_dataChannelTasks;
};