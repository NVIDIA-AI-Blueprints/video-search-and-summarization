/*
 * SPDX-FileCopyrightText: Copyright (c) 2020-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include "device_manager.h"
#include "NotificationFactory.h"
#include "logger.h"

#include <mutex>
#include <map>

namespace nv_vms
{

class SensorManagement;

    class SensorMonitoring : public ISensorDiscoveryEvent
    {
    public:
        SensorMonitoring(SensorManagement* sensorMgmt,
                           std::vector<std::pair<ISensorDiscoveryInterface*, void*>>& objs);
        virtual ~SensorMonitoring()
        {
            std::lock_guard<std::mutex> lock(m_discoveryObjectsLock);
            LOG(info) << "Destroying Sesnor Status Monitor" << endl;
            for (ISensorDiscoveryInterface* object : m_discoveryObjects)
            {
                object->deregisterSensorDiscoveryListener(this);
            }
            for (auto item : m_sensorDiscoveryObjectPairList)
            {
                ISensorDiscoveryInterface* discoveryObject = item.first;
                destroyDiscoveryObject_t delObject = (destroyDiscoveryObject_t ) item.second;
                if (delObject)
                {
                    delObject(discoveryObject);
                }
            }
            m_discoveryObjects.clear();
            m_sensorDiscoveryObjectPairList.clear();
        }

        int restartDiscovery();
        int searchSensor(SensorInfo& sensorInfo);

        // ISensorDiscoveryEvent Interfaces
        int onSensorFound(SensorInfo& sensorInfo);
        int onSensorChanged(SensorInfo& sensorInfo);
        int onSensorRemoved(const string& sensor_id);
        void notifyEvent(const SensorStatus& status, const string& url, const string &ipc_url = "");
        void refreshSensorList();
        void addAndRegisterDiscoveryObject(ISensorDiscoveryInterface* discoveryObject);
        void removeAndDeRegisterDiscoveryObject(ISensorDiscoveryInterface* discoveryObject);
    private:
        SensorManagement* m_sensorManagement;
        std::mutex m_sensorEventMutex;
        INotificationInterface* m_notifier;
        std::vector<std::pair<ISensorDiscoveryInterface*, void*>> m_sensorDiscoveryObjectPairList;
        std::vector<ISensorDiscoveryInterface*> m_discoveryObjects;
        std::mutex m_discoveryObjectsLock;
    };
} // nv_vms