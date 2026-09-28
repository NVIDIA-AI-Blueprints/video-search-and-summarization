/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <vector>
#include <jsoncpp/json/json.h>


class LocalStreams : public nv_vms::ISensorControlInterface
{
    public:
        LocalStreams() = default;
        virtual ~LocalStreams() = default;

        int connect();
        int getSensorStreamInfo(std::vector<std::shared_ptr<nv_vms::SensorInfo>>& sensors);
        int getSensorStreamInfo(std::shared_ptr<nv_vms::SensorInfo>& sensor);
        bool isServerOnline(const std::string & url) { return true; }
        bool transcodeIfNeeded(const std::string& filePath, const Json::Value& mediainfo, const Json::Value& keyFrameParseResult);

    private:
        void addLocalStreams(std::vector<std::shared_ptr<nv_vms::SensorInfo>>& sensors);
        bool isSensorExist(std::vector<std::shared_ptr<nv_vms::SensorInfo>>& sensors, const std::string &filepath);
};