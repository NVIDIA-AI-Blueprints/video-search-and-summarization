/*
 * SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "sensor_discovery_adaptor.h"
#include <memory>
#include <thread>
#include <mutex>

namespace nv_vms {

class NativeSensorsDiscovery : public ISensorDiscoveryInterface
{
public:
    NativeSensorsDiscovery();
    ~NativeSensorsDiscovery();

    //ISensorDiscoveryInterface Interfaces
    void start();
    void stop();

private:
    int addNewSensor(SensorInfo& sensor);
    void nativeSensorsDiscoveryTask();
    std::string getDeviceName(const std::string& devicePath);
    void doNativeSensorDiscovery(vector<SensorInfo>& sensors);

    std::thread m_nativeSensorDiscoveryThread;
    bool m_exit;
    std::mutex   m_monitorMutex;
    map<string, SensorInfo> m_freshList;
    std::mutex   m_sleeperLock;
    std::condition_variable m_sleeperWait;
};

} //nv_vms