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

#include "sensor_monitoring.h"
#include "logger.h"
#include "sensor_management.h"
#include "network_utils.h"
#include "sensor_management_utils.h"
#include "vst_common.h"

unsigned int max_n_threads = 10;

SensorMonitoring::SensorMonitoring(SensorManagement* sensorMgmt,
                             std::vector<std::pair<ISensorDiscoveryInterface*, void*>>& objs)
                           : m_sensorManagement(sensorMgmt)
                           , m_sensorDiscoveryObjectPairList(objs)
{
    m_notifier = NotificationFactory::CreatePlatformNotification();
    for (auto item : m_sensorDiscoveryObjectPairList)
    {
        ISensorDiscoveryInterface* discoveryObject = item.first;
        m_discoveryObjects.push_back(discoveryObject);
    }
    for (ISensorDiscoveryInterface* object : m_discoveryObjects)
    {
        object->registerSensorDiscoveryListener(this);
        object->setCacheSensorList(m_sensorManagement->getSensors());
        object->start();
    }
}

int SensorMonitoring::restartDiscovery()
{
    std::lock_guard<std::mutex> lock(m_discoveryObjectsLock);
    for (ISensorDiscoveryInterface* object : m_discoveryObjects)
    {
        object->stop();
        object->start();
    }
    return 0;
}

int SensorMonitoring::searchSensor(SensorInfo& sensorInfo)
{
    std::lock_guard<std::mutex> lock(m_discoveryObjectsLock);
    int ret = -1;
    for (ISensorDiscoveryInterface* object : m_discoveryObjects)
    {
        ret = object->searchSensor(sensorInfo);
        if (ret == 0)
        {
            break;
        }
    }
    return ret;
}

int SensorMonitoring::onSensorFound(SensorInfo& sensorInfo)
{
    std::lock_guard<std::mutex> guard(m_sensorEventMutex);
    std::shared_ptr<DeviceManager> deviceManager = m_sensorManagement->getDeviceManagerObject();
    if (m_sensorManagement == nullptr || deviceManager == nullptr)
    {
        return -1;
    }

    /**
     * After a sensor has been removed from VST using remove API. It can be added back in below cases:
     * 1. VST is restarted
     * 2. Scan API is called
     * 3. Sensor is manually added using new API
     */
    if (m_sensorManagement->isRemovedByUser(sensorInfo) && sensorInfo.isAutoDiscovered)
    {
        LOG(verbose) << "Ignoring this sensor, since it is part of user-removed list" << endl;
        sensorInfo.updateHttpErrorStatus(std::make_pair(405, "User-removed sensor"));
        return -1;
    }

    shared_ptr<SensorInfo> sensor = std::make_shared<SensorInfo>(sensorInfo);
    string response;
    if (vst_common::addSensorManually(sensor, response, deviceManager) != 0)
    {
        sensorInfo = *sensor;
        LOG(verbose) << "Found sensor is already with vms, so ignore it" << endl;
        sensorInfo.updateHttpErrorStatus(std::make_pair(405, "Sensor already Exists"));
        return -1;
    }

    if (sensor->getSensorStatus() == SensorStatusOnline)
    {
        if (m_sensorManagement->getSensorInfo(sensor->id, true) == nullptr)
        {
            /* Notify external modules that camera is ready to stream */
            LOG(error) << "getSensorInfo() failed, sensor is not yet ready" << endl;
        }
        else
        {
            LOG(info) << "Added sensor successfully: " << sensor->sensorId << endl;
        }
    }

    if (!GET_CONFIG().remote_vst_address.empty())
    {
        vst_common::addSensorToRemoteDevice(sensor, deviceManager);
    }
    sensorInfo = *sensor;

    return 0;
}

int SensorMonitoring::onSensorChanged(SensorInfo& sensorInfo)
{
    return onSensorFound(sensorInfo);
}

int  SensorMonitoring::onSensorRemoved(const string& sensor_id)
{
    std::lock_guard<std::mutex> guard(m_sensorEventMutex);
    std::shared_ptr<DeviceManager> deviceManager = m_sensorManagement->getDeviceManagerObject();
    if (m_sensorManagement == nullptr || deviceManager == nullptr)
    {
        return -1;
    }
    std::shared_ptr<SensorInfo> existed_sensor = deviceManager->findSensor(sensor_id);
    if(existed_sensor)
    {
        SensorStatus status;
        status.timeStamp = getCurrentTime();
        status.sensorId = existed_sensor->id;
        status.sensorName = existed_sensor->name;
        status.serverId = deviceManager->getDeviceId();
        status.event = SensorStatusOffline;
        status.type = TYPE_VST;

        deviceManager->updateSensorStatus(status);

        if (status.event != SensorStatusUnknown)
        {
            LOG(info) << "Sensor Removed: " << existed_sensor->ip << endl;
            LOG(info) << "Time: " << status.timeStamp << endl;
            LOG(info) << "EventId: " << SensorStatus::getEventString(status.event) << endl;
            LOG(info) << "CameraId: " << status.sensorId << endl;
            LOG(info) <<  "ServerId: " << status.serverId << endl;

            notifyEvent(status, "");

            existed_sensor->m_notify = true;
            existed_sensor->streams.clear();
        }
    }
    return 0;
}

void SensorMonitoring::notifyEvent(const SensorStatus& status, const string& url, const string& ipc_url)
{
    string change = vst_common::sensorStatusEventToString(status.event);

    Json::Value payload, event;
    event["camera_id"] = status.sensorId;
    event["camera_name"] = status.sensorName;
    event["camera_url"] = change == "camera_add" ? "" : url; // Use original URL for payload
    event["ipc_url"]    = GET_CONFIG().enable_ipc_path == true ? ipc_url : "";
    event["change"] = change;
    if (status.tags.empty() == false)
    {
        event["tags"] = status.tags;
    }
    payload["created_at"] = status.timeStamp;
    payload["source"] = "vst";
    payload["alert_type"] = "camera_status_change";
    payload["event"] = event;
    if (m_notifier)
    {
        m_notifier->sendMessage(payload);
    }
    else
    {
        LOG(error) << "Notification Manager instance is not created" << endl;
    }
    
    // Create a copy for logging with masked URL
    Json::Value logPayload = payload;
    logPayload["event"]["camera_url"] = change == "camera_add" ? "" : secureUrlForLogging(url);
    LOG(info) << logPayload.toStyledString() << endl;
}

void SensorMonitoring::refreshSensorList()
{
    std::lock_guard<std::mutex> lock(m_discoveryObjectsLock);
    for (ISensorDiscoveryInterface* object : m_discoveryObjects)
    {
        LOG(verbose) <<"Refershing cache sensor list" << endl;
        object->setCacheSensorList(m_sensorManagement->getSensors());
    }
}

void SensorMonitoring::addAndRegisterDiscoveryObject(ISensorDiscoveryInterface* discoveryObject)
{
    std::lock_guard<std::mutex> lock(m_discoveryObjectsLock);
    m_discoveryObjects.push_back(discoveryObject);
    discoveryObject->registerSensorDiscoveryListener(this);
    discoveryObject->start();
}

void SensorMonitoring::removeAndDeRegisterDiscoveryObject(ISensorDiscoveryInterface* discoveryObject)
{
    std::lock_guard<std::mutex> lock(m_discoveryObjectsLock);
    discoveryObject->deregisterSensorDiscoveryListener(this);

    m_discoveryObjects.erase(std::remove(m_discoveryObjects.begin(), m_discoveryObjects.end(), discoveryObject), m_discoveryObjects.end());
}