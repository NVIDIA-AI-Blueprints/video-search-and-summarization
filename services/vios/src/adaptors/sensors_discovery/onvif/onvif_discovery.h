/*
 * SPDX-FileCopyrightText: Copyright (c) 2022-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include <sstream>
#include <thread>
#include <queue>
#include <mutex>
#include <condition_variable>
#include <chrono>

// Forward declarations
typedef void CURLM;

namespace nv_vms {

class OnvifDiscovery : public ISensorDiscoveryInterface
{
public :
    OnvifDiscovery();
    ~OnvifDiscovery();
    
    //ISensorDiscoveryInterface Interfaces
    void start();
    void stop();
    int searchSensor(SensorInfo& sensor);

private:
    int addNewSensor(SensorInfo& sensor);
    void onvifSensorMonitorTask();
    void onvifListnerTask();
    void synchronizeDateAndTime(std::vector<shared_ptr<SensorInfo>>& sensor_list);
private:
    std::thread m_monitorThread;
    std::thread m_onvifListnerThread;
    bool m_exit;
    std::queue<SensorInfo> m_queue;
    std::mutex   m_queuemutex;
    std::mutex   m_monitorMutex;
    std::mutex   m_sleeperLock;
    std::condition_variable m_sleeperWait;
    map<string, SensorInfo> m_freshList;
    CURLM* m_sensorSyncMultiHandle = nullptr;
    std::chrono::steady_clock::time_point m_timePrev = std::chrono::steady_clock::time_point::min();
};

} //nv_vms