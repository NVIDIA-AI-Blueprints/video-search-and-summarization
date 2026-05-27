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

#include <string>
#include <vector>
#include <memory>
#include <algorithm>

#include "device_manager.h"

namespace nv_vms {

class ISensorDiscoveryEvent
{
    public:
        ISensorDiscoveryEvent() {}
        virtual ~ISensorDiscoveryEvent() {}

        virtual int onSensorFound(SensorInfo& sensorInfo) = 0;
        virtual int onSensorChanged(SensorInfo& sensorInfo) = 0;
        virtual int onSensorRemoved(const string& sensorInfo) = 0;

        virtual void notifyEvent(const SensorStatus& status, const string& url) {}
        virtual void refreshSensorList() {}
};

class ISensorDiscoveryInterface
{	
public:
    ISensorDiscoveryInterface() {}
    virtual ~ISensorDiscoveryInterface() {}

    virtual void start() = 0;
    virtual void stop()  = 0;
    virtual int searchSensor(SensorInfo& sensor) { return -1; }

    void registerSensorDiscoveryListener(ISensorDiscoveryEvent* listner)
    {
        std::lock_guard<std::mutex> lock(m_sensorDiscoveryLock);
        if (std::find(m_listners.begin(), m_listners.end(), listner) == m_listners.end())
        {
            m_listners.push_back(listner);
        }
    }

    void deregisterSensorDiscoveryListener(ISensorDiscoveryEvent* listner)
    {
        std::lock_guard<std::mutex> lock(m_sensorDiscoveryLock);
        m_listners.erase(std::remove(m_listners.begin(), m_listners.end(), listner), m_listners.end());
    }

    int publishOnSensorFound(SensorInfo& sensor)
    {
        std::lock_guard<std::mutex> lock(m_sensorDiscoveryLock);
        int ret = 0;
        for (ISensorDiscoveryEvent* listner : m_listners)
        {
            ret |= listner->onSensorFound(sensor);
        }
        return ret;
    }

    int publishOnSensorChanged(SensorInfo& sensor)
    {
        std::lock_guard<std::mutex> lock(m_sensorDiscoveryLock);
        int ret = 0;
        for (ISensorDiscoveryEvent* listner : m_listners)
        {
            ret |= listner->onSensorChanged(sensor);
        }
        return ret;
    }

    int publishOnSensorRemoved(const string& sensor_id)
    {
        std::lock_guard<std::mutex> lock(m_sensorDiscoveryLock);
        int ret = 0;
        for (ISensorDiscoveryEvent* listner : m_listners)
        {
            ret |= listner->onSensorRemoved(sensor_id);
        }
        return ret;
    }

    void refreshCacheSensorList()
    {
        std::lock_guard<std::mutex> lock(m_sensorDiscoveryLock);
        for (ISensorDiscoveryEvent* listner : m_listners)
        {
            listner->refreshSensorList();
        }
    }

    void setCacheSensorList(std::vector<shared_ptr<SensorInfo>> list)
    {
        std::lock_guard<std::mutex> lock(m_cacheSensorLock);
        m_cacheSensorList = list;
    }
    std::vector<shared_ptr<SensorInfo>> getCacheSensorList()
    {
        std::lock_guard<std::mutex> lock(m_cacheSensorLock);
        return m_cacheSensorList;
    }

private:
    std::vector<ISensorDiscoveryEvent*> m_listners;
    std::mutex m_sensorDiscoveryLock;
    std::mutex m_cacheSensorLock;
    std::vector<shared_ptr<SensorInfo>> m_cacheSensorList;
};

ISensorDiscoveryInterface* createDiscoveryObject();
void destroyDiscoveryObject(ISensorDiscoveryInterface* object);

typedef ISensorDiscoveryInterface* (*createDiscoveryObject_t) (void);
typedef void (*destroyDiscoveryObject_t) (ISensorDiscoveryInterface*);

} //nv_vms