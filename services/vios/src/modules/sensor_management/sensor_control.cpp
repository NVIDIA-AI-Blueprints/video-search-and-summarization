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

#include "sensor_control.h"
#include "logger.h"
#include "sensor_info.h"

#include <dlfcn.h>
#include <string>

using namespace nv_vms;

SensorControl::SensorControl(DeviceManager* deviceMngr)
                     : m_deviceManager(deviceMngr)
                     , m_sensorControlobjectPair(deviceMngr->m_sensorControlobjectPair)
                     , m_adaptor(deviceMngr->m_sensorControlobjectPair.first)
{
    AdaptorInfo info;
    info.m_id = m_deviceManager->id;
    info.m_name = m_deviceManager->name;
    info.m_type = m_deviceManager->type;
    info.m_user = m_deviceManager->user;
    info.m_password = m_deviceManager->password;
    info.m_port = m_deviceManager->port;
    info.m_ipaddress = m_deviceManager->ip;
    info.m_url = m_deviceManager->url;
    m_adaptor->setAdaptorInfo(info);
}

SensorControl::~SensorControl()
{
    LOG(verbose) << "Deleted SensorControl Object" << endl;
}

int SensorControl::connect()
{
    if (m_deviceManager == nullptr || m_adaptor == nullptr)
    {
        LOG(error) << "Invalid deviceMngr/adaptor object" << endl;
        return -1;
    }
    std::vector<std::shared_ptr<SensorInfo>> sensors = m_deviceManager->getSensorList();
    m_adaptor->setCacheSensorList(sensors);
    int ret =  m_adaptor->connect();
    if(ret == 0)
    {
        sensors = m_adaptor->getCacheSensorList();
        m_deviceManager->addSensorList(sensors);
    }
    return ret;
}

int SensorControl::setCacheSensorList()
{
    if (m_deviceManager == nullptr || m_adaptor == nullptr)
    {
        LOG(error) << "Invalid deviceMngr/adaptor object" << endl;
        return -1;
    }

    std::vector<std::shared_ptr<SensorInfo>> sensors = m_deviceManager->getSensorList();
    m_adaptor->setCacheSensorList(sensors);

    return 0;
}

int SensorControl::getSensorsStreamInfo()
{
    int ret = -1;
    std::vector<std::shared_ptr<SensorInfo>> sensors = m_deviceManager->getSensorList();
    ret = m_adaptor->getSensorStreamInfo(sensors);
    m_deviceManager->addSensorList(sensors);
    for (auto sensor : sensors)
    {
        vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
        for (auto stream : streams)
        {
            getSensorSettings(sensor->id, stream->id, stream->settings, "");
        }
    }
    return ret;
}

int SensorControl::getSensorStreamInfo(shared_ptr<SensorInfo>& sensorInfo)
{
    int ret = -1;
    ret = m_adaptor->getSensorStreamInfo(sensorInfo);
    vector<shared_ptr<StreamInfo>> streams = sensorInfo->getStreams();
    for (auto stream : streams)
    {
        getSensorSettings(sensorInfo->id, stream->id, stream->settings, "");
    }
    return ret;
}

int SensorControl::getStreamInfo(const string & sensor_id, const string & stream_id, shared_ptr<StreamInfo>& stream)
{
    int ret = -1;

    return ret;
}

int SensorControl::rebootSensor(const string sensor_id)
{
    shared_ptr<SensorInfo> sensor = getSensor(sensor_id);
    return sensor != nullptr? m_adaptor->rebootSensor(sensor) : -1;
}

int SensorControl::getSensorNetworkInfo(const string sensor_id, SensorNetworkInfo& networkInfo)
{
    int ret = -1;
    shared_ptr<SensorInfo> sensor = getSensor(sensor_id);
    if(sensor)
    {
        ret = m_adaptor->getNetworkInfo(sensor, networkInfo);
    }
    return ret;
}
int SensorControl::setSensorNetworkInfo(const string sensor_id, const SensorNetworkInfo& networkInfo, bool& rebootNeeded)
{
    int ret = -1;
    shared_ptr<SensorInfo> sensor = getSensor(sensor_id);
    if(sensor)
    {
        ret = m_adaptor->setNetworkInfo(sensor, networkInfo, rebootNeeded);
    }
    return ret;
}

int SensorControl::synchronizeSensorTime(const string sensor_id)
{
    int ret = -1;
    shared_ptr<SensorInfo> sensor = getSensor(sensor_id);
    if(sensor)
    {
        ret = m_adaptor->synchronizeSensorTime(sensor);
    }
    return ret;
}

int SensorControl:: getSensorSettings(const string sensor_id, const string stream_id,
                            SensorSettings& settings, const string& type)
{
    int ret = -1;
    shared_ptr<SensorInfo> sensor = getSensor(sensor_id);

    if(sensor)
    {
        shared_ptr<StreamInfo> stream = sensor->getStream(stream_id);
        if (type == "Image")
        {
            ret = m_adaptor->getSensorImageSettings(sensor, stream_id, settings);
            if (ret == 0)
            {
                if (stream)
                {
                    stream->updateImageValues(settings.imageValues);
                }
            }
        }
        else if (type == "Encode")
        {
            ret = m_adaptor->getSensorEncodeSettings(sensor, stream_id, settings);
            if (ret == 0)
            {
                if (stream)
                {
                    if (settings.encoderValues.encoding.empty() == false)
                    {
                        stream->updateVideoEncoderValues(settings.encoderValues);
                    }
                    stream->updateVideoEncoderOptions(settings.encoderOptions);
                }
            }
        }
        else
        {
            ret = m_adaptor->getSensorImageSettings(sensor, stream_id, settings);
            if(ret == 0 && stream)
            {
                stream->updateImageValues(settings.imageValues);
            }
            ret = m_adaptor->getSensorEncodeSettings(sensor, stream_id, settings);
            if (ret == 0 && stream)
            {
                if (settings.encoderValues.encoding.empty() == false)
                {
                    stream->updateVideoEncoderValues(settings.encoderValues);
                }
                stream->updateVideoEncoderOptions(settings.encoderOptions);
            }
        }
    }
    return ret;
}

int SensorControl::setSensorImageSettings(const string sensor_id, const SensorImageSettingsValues& settings)
{
    int ret = -1;
    shared_ptr<SensorInfo> sensor = getSensor(sensor_id);
    if(sensor)
    {
        ret = m_adaptor->setSensorImageSettings(sensor, settings);
    }
    return ret;
}

int SensorControl::setSensorEncodeSettings(const string sensor_id, const SensorVideoEncoderSettingsValues& settings)
{
    int ret = -1;
    shared_ptr<SensorInfo> sensor = getSensor(sensor_id);
    if(sensor)
    {
        ret = m_adaptor->setSensorEncodeSettings(sensor, settings);
    }
    return ret;
}

int SensorControl::getStreamSettings(const string sensor_id, const string& stream_id)
{
    int ret = -1;
    shared_ptr<SensorInfo> sensor = getSensor(sensor_id);
    if(sensor)
    {
        ret = m_adaptor->getStreamSettings(sensor, stream_id);
    }
    return ret;
}

bool SensorControl::validateCredentials(const string sensor_id, const string username, const string password)
{
    int ret = -1;
    shared_ptr<SensorInfo> sensor = getSensor(sensor_id);
    if(sensor)
    {
        ret = m_adaptor->validateCredentials(sensor, username, password);
    }
    return ret;
}

bool SensorControl::deleteSensor(shared_ptr<SensorInfo>& sensorInfo)
{
    bool ret = false;
    if(sensorInfo)
    {
        ret = m_adaptor->deleteSensor(sensorInfo);
    }
    return ret;
}

VmsErrorCode SensorControl::addSensor(const Json::Value& sensorInfo)
{
    return m_adaptor->addSensor(sensorInfo);
}

int SensorControl::setPTZ(const string sensor_id, PTZAction ptz, string x, string y)
{
    int ret = -1;
    shared_ptr<SensorInfo> sensor = getSensor(sensor_id);
    if(sensor)
    {
        ret = m_adaptor->setPTZ(sensor, ptz, x, y);
    }
    return ret;
}

shared_ptr<SensorInfo> SensorControl::getSensor(const string& id)
{
    LOG(verbose) << "getSensor: "<< id << endl;
    vector<shared_ptr<SensorInfo>> sensors = m_deviceManager->getSensorList();
    for (uint32_t i = 0; i < sensors.size(); i++)
    {
        if (!sensors[i].get())
        {
            continue;
        }
        if (sensors[i]->id == id)
        {
            return sensors[i];
        }
        vector<shared_ptr<StreamInfo>>streams = sensors[i]->getStreams();
        for(uint32_t j = 0; j < streams.size(); j++)
        {
            if (streams[j].get() && id == streams[j]->id )
            {
                return sensors[i];
            }
        }
    }
    LOG(error) << "sensor not found" << endl;
    return nullptr;
}

int SensorControl::setSensorInfo(const string sensor_id)
{
    int ret = -1;
    shared_ptr<SensorInfo> sensor = getSensor(sensor_id);
    if(sensor)
    {
        ret = m_adaptor->setSensorInfo(sensor);
    }
    return ret;
}

int SensorControl::getRecordingTimelines(const string& sensor_id, Json::Value& timelinesJson)
{
    LOG(verbose) << "getRecordingTimelines for sensor: " << sensor_id << endl;
    int ret = -1;
    shared_ptr<SensorInfo> sensor = getSensor(sensor_id);
    if(sensor)
    {
        ret = m_adaptor->getRecordingTimelines(sensor, timelinesJson);
    }
    else
    {
        LOG(error) << "Sensor not found: " << sensor_id << endl;
    }
    return ret;
}