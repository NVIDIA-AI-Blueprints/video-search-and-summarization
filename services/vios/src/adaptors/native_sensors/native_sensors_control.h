/*
 * SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include "logger.h"

class NativeSensorControlInterface : public nv_vms::ISensorControlInterface
{
    public:
    NativeSensorControlInterface();
    virtual ~NativeSensorControlInterface() = default;

    int connect();
    int getSensorStreamInfo(std::vector<std::shared_ptr<nv_vms::SensorInfo>>& sensors);
    int getSensorStreamInfo(std::shared_ptr<nv_vms::SensorInfo>& sensor);
    bool isServerOnline(const std::string & url) { return true; }
    int getSensorImageSettings(std::shared_ptr<nv_vms::SensorInfo>& sensor, const std::string& stream_id, nv_vms::SensorSettings& settings);
    int setSensorImageSettings(std::shared_ptr<nv_vms::SensorInfo>& sensor, const nv_vms::SensorImageSettingsValues& settings);
    int getSensorEncodeSettings(std::shared_ptr<nv_vms::SensorInfo>& sensor, const std::string& stream_id, nv_vms::SensorSettings& settings);
    int setSensorEncodeSettings(std::shared_ptr<nv_vms::SensorInfo>& sensor, const nv_vms::SensorVideoEncoderSettingsValues& settings);
    int getNetworkInfo(std::shared_ptr<nv_vms::SensorInfo>& sensor, nv_vms::SensorNetworkInfo& networkInfo);
    int setNetworkInfo(std::shared_ptr<nv_vms::SensorInfo>& sensor, const nv_vms::SensorNetworkInfo& networkInfo, bool& rebootNeeded);
}; //nv_vms
