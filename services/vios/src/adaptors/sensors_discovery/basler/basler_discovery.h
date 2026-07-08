/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <map>
#include <mutex>
#include <set>
#include <string>
#include <thread>

namespace nv_vms {

class BaslerDiscovery : public ISensorDiscoveryInterface
{
public:
    BaslerDiscovery();
    ~BaslerDiscovery() override;

    // ISensorDiscoveryInterface
    void start() override;
    void stop() override;

private:
    void discoveryTask();
    void doBaslerDiscovery(std::map<std::string, SensorInfo>& freshList);
    int addNewSensor(SensorInfo& sensor);
    static std::string buildSensorIdFromSerial(const std::string& serial);

    std::thread m_discoveryThread;
    std::atomic<bool> m_exit{true};
    std::mutex m_monitorMutex;
    std::mutex m_sleeperLock;
    std::condition_variable m_sleeperWait;
    std::map<std::string, SensorInfo> m_freshList;  // keyed by serial number
    bool m_pylonInitialized{false};                 // true once PylonInitialize succeeded

    static constexpr std::chrono::milliseconds kPollInterval{5000};
};

} // namespace nv_vms
