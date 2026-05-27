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

#pragma once

#include "device_manager.h"

#include<iostream>
#include<vector>
#include<memory>

using namespace std; 

static const string HTTP  = "http";
static const string HTTPS = "https";
static const string RTSP  = "rtsp";

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
    AdaptorInfo(const AdaptorInfo& obj)
    {
        this->m_id = obj.m_id;
        this->m_name = obj.m_name;
        this->m_type = obj.m_type;
        this->m_user = obj.m_user;
        this->m_password = obj.m_password;
        this->m_port = obj.m_port;
        this->m_ipaddress = obj.m_ipaddress;
        this->m_url = obj.m_url;
    }
    void operator=(const AdaptorInfo& obj)
    {
        this->m_id = obj.m_id;
        this->m_name = obj.m_name;
        this->m_type = obj.m_type;
        this->m_user = obj.m_user;
        this->m_password = obj.m_password;
        this->m_port = obj.m_port;
        this->m_ipaddress = obj.m_ipaddress;
        this->m_url = obj.m_url;
    }
    string m_id;
    string m_name;
    string m_type;
    string m_user;
    string m_password;
    string m_port;
    string m_ipaddress;
    string m_url;
};

class ISensorControlInterface
{
public:
    virtual int connect() = 0;
    virtual int getSensorStreamInfo(vector<shared_ptr<SensorInfo>>& sensors) = 0;
    virtual int getSensorStreamInfo(shared_ptr<SensorInfo>& sensor) = 0;
    virtual int synchronizeSensorTime(shared_ptr<SensorInfo>& sensor) { return -1; };
    virtual int getSensorStatus(const string& cameraId, SensorStatus& status) { return -1; };
    virtual int getSensorStatus(const vector<string>& camera_ids, vector<SensorStatus>& status) { return -1; };
    virtual int rebootSensor(shared_ptr<SensorInfo>& sensor) { return -1; };
    virtual bool isServerOnline(const string & url) = 0;
    virtual int getSensorImageSettings(shared_ptr<SensorInfo>& sensor, const string& stream_id, SensorSettings& settings) { return -1; };
    virtual int setSensorImageSettings(shared_ptr<SensorInfo>& sensor, const SensorImageSettingsValues& settings) { return -1; };
    virtual int getNetworkInfo(shared_ptr<SensorInfo>& sensor, SensorNetworkInfo& networkInfo) { return -1; };
    virtual int setNetworkInfo(shared_ptr<SensorInfo>& sensor, const SensorNetworkInfo& networkInfo, bool& rebootNeeded) { return -1; };
    virtual int getSensorEncodeSettings(shared_ptr<SensorInfo>& sensor, const string& stream_id, SensorSettings& settings) { return -1; };
    virtual int setSensorEncodeSettings(shared_ptr<SensorInfo>& sensor, const SensorVideoEncoderSettingsValues& settings) { return -1; };
    virtual int getStreamSettings(shared_ptr<SensorInfo>& sensor, const string& stream_id) { return 0;}
    virtual int setPTZ(shared_ptr<SensorInfo>& sensor, PTZAction, string x, string y) { return 0; };
    map<PTZAction, ptzRange> getPTZ(shared_ptr<SensorInfo>& sensor) { map<PTZAction, ptzRange>ptz; return ptz; };
    virtual bool validateCredentials(shared_ptr<SensorInfo>& sensor, const string username, const string password) { return false; }
    virtual VmsErrorCode addSensor(const Json::Value& sensorInfo) { return VmsErrorCode::NoError; }
    virtual bool deleteSensor(shared_ptr<SensorInfo>& sensor) { return true; }
    virtual int setSensorInfo(shared_ptr<SensorInfo> &sensor) { return 0; }
    virtual int getRecordingTimelines(shared_ptr<SensorInfo>& sensor, Json::Value& timelinesJson) { return -1; }

    void setAdaptorInfo(AdaptorInfo& info) { m_adaptorInfo = info; }
    void setCacheSensorList(std::vector<shared_ptr<SensorInfo>> list) { m_cacheSensorList = list; }
    std::vector<shared_ptr<SensorInfo>> getCacheSensorList() { return m_cacheSensorList; }
protected:
    AdaptorInfo m_adaptorInfo;
    std::vector<shared_ptr<SensorInfo>> m_cacheSensorList;
};

ISensorControlInterface* createObject();
void destroyObject(ISensorControlInterface* object);

typedef ISensorControlInterface* (*createControlObject_t) (void);
typedef void (*destroyControlObject_t) (ISensorControlInterface*);

} //nv_vms