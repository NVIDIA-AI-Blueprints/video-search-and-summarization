/*
 * SPDX-FileCopyrightText: Copyright (c) 2023-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "device_manager.h"

#include<iostream>
#include<vector>
#include<memory>


static const std::string HTTP  = "http";
static const std::string HTTPS = "https";
static const std::string RTSP  = "rtsp";

namespace nv_vms
{

struct AdaptorInfo
{
    AdaptorInfo(): m_id("")
                 , m_name("")
                 , m_type("")
                 , m_user ("")
                 , m_password ("")
                 , m_port("")
                 , m_ipaddress("")
                 , m_url("")
    {}
    AdaptorInfo(const AdaptorInfo& obj) = default;
    AdaptorInfo& operator=(const AdaptorInfo& obj) = default;
    std::string m_id;
    std::string m_name;
    std::string m_type;
    std::string m_user;
    std::string m_password;
    std::string m_port;
    std::string m_ipaddress;
    std::string m_url;
};

class ISensorControlInterface
{
public:
    virtual int connect() = 0;
    virtual int getSensorStreamInfo(std::vector<std::shared_ptr<SensorInfo>>& sensors) = 0;
    virtual int getSensorStreamInfo(std::shared_ptr<SensorInfo>& sensor) = 0;
    virtual int synchronizeSensorTime(std::shared_ptr<SensorInfo>& sensor) { return -1; };
    virtual int getSensorStatus(const std::string& cameraId, SensorStatus& status) { return -1; };
    virtual int getSensorStatus(const std::vector<std::string>& camera_ids, std::vector<SensorStatus>& status) { return -1; };
    virtual int rebootSensor(std::shared_ptr<SensorInfo>& sensor) { return -1; };
    virtual bool isServerOnline(const std::string & url) = 0;
    virtual int getSensorImageSettings(std::shared_ptr<SensorInfo>& sensor, const std::string& stream_id, SensorSettings& settings) { return -1; };
    virtual int setSensorImageSettings(std::shared_ptr<SensorInfo>& sensor, const SensorImageSettingsValues& settings) { return -1; };
    virtual int getNetworkInfo(std::shared_ptr<SensorInfo>& sensor, SensorNetworkInfo& networkInfo) { return -1; };
    virtual int setNetworkInfo(std::shared_ptr<SensorInfo>& sensor, const SensorNetworkInfo& networkInfo, bool& rebootNeeded) { return -1; };
    virtual int getSensorEncodeSettings(std::shared_ptr<SensorInfo>& sensor, const std::string& stream_id, SensorSettings& settings) { return -1; };
    virtual int setSensorEncodeSettings(std::shared_ptr<SensorInfo>& sensor, const SensorVideoEncoderSettingsValues& settings) { return -1; };
    virtual int getStreamSettings(std::shared_ptr<SensorInfo>& sensor, const std::string& stream_id) { return 0;}
    virtual int setPTZ(std::shared_ptr<SensorInfo>& sensor, PTZAction, std::string x, std::string y) { return 0; };
    virtual std::map<PTZAction, ptzRange> getPTZ(std::shared_ptr<SensorInfo>& sensor) { std::map<PTZAction, ptzRange>ptz; return ptz; };
    virtual bool validateCredentials(std::shared_ptr<SensorInfo>& sensor, const std::string username, const std::string password) { return false; }
    virtual VmsErrorCode addSensor(const Json::Value& sensorInfo) { return VmsErrorCode::NoError; }
    virtual bool deleteSensor(std::shared_ptr<SensorInfo>& sensor) { return true; }
    virtual int setSensorInfo(std::shared_ptr<SensorInfo> &sensor) { return 0; }
    virtual int getRecordingTimelines(std::shared_ptr<SensorInfo>& sensor, Json::Value& timelinesJson) { return -1; }

    void setAdaptorInfo(AdaptorInfo& info) { m_adaptorInfo = info; }
    void setCacheSensorList(std::vector<std::shared_ptr<SensorInfo>> list) { m_cacheSensorList = list; }
    std::vector<std::shared_ptr<SensorInfo>> getCacheSensorList() { return m_cacheSensorList; }
protected:
    const AdaptorInfo& adaptorInfo() const { return m_adaptorInfo; }
    std::vector<std::shared_ptr<SensorInfo>>& cacheSensorList() { return m_cacheSensorList; }
private:
    AdaptorInfo m_adaptorInfo;
    std::vector<std::shared_ptr<SensorInfo>> m_cacheSensorList;
};

ISensorControlInterface* createObject();
void destroyObject(ISensorControlInterface* object);

typedef ISensorControlInterface* (*createControlObject_t) (void);
typedef void (*destroyControlObject_t) (ISensorControlInterface*);

} //nv_vms