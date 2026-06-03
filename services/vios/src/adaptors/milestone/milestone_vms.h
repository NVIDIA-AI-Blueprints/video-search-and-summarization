/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include <thread>

using namespace std;
using namespace nv_vms;

namespace nv_vms {

enum SoapAuthType
{
    SOAP_AUTH_BASIC,
    SOAP_AUTH_NTLM,
    SOAP_AUTH_NONE,
    SOAP_AUTH_UNKNOWN
};

class ICameraStatusEvent
{
    public:
        virtual void onDeviceEvent(const SensorStatus& status) = 0;
        virtual void notifyEvent(const SensorStatus& status, const string& camera_ip){}
};


class MSCameraEvents
{
    public:
        MSCameraEvents (std::shared_ptr<DeviceManager> deviceMngr, const string token,
                      std::shared_ptr<ICameraStatusEvent> cb) : m_token(token)
                                            , m_deviceManager(deviceMngr)
                                            , m_callback(cb)
                                            , m_exit(false)
        {
            m_url = deviceMngr->url + string(":") + string("7563") + string("/recorderstatusservice/recorderstatusservice2.asmx");
            m_thread = std::thread([this] { this->cameraEventTask(); });
        }
        ~MSCameraEvents()
        {
            LOG(info) << __METHOD_NAME__ << endl;
            m_exit = true;
            m_thread.join();
        }
        void cameraEventTask();
        int getCurrentSensorStatus(const string& camera_id, SensorStatus& status);
        int getCurrentSensorStatus(const vector<string>& camera_ids, vector<SensorStatus>& status);
    private:
        int subscribeToStatusSession();
        int unSubscribeFromStatusSession();
        int getSensorStatus(SensorStatus& status);
    private:
        string m_token;
        std::shared_ptr<DeviceManager> m_deviceManager;
        std::shared_ptr<ICameraStatusEvent> m_callback;
        bool m_exit;
        string m_url;
        string m_statusSessionId;
        std::thread m_thread;
};


class MilestoneVmsVendor : public ISensorControlInterface
{
public:
    MilestoneVmsVendor() : m_cameraEventThreadRunning(false) {}
    virtual ~MilestoneVmsVendor ()
    {
        LOG(info) << __METHOD_NAME__ << endl;
    }

    int connect();
    int getSensorStreamInfo(vector<shared_ptr<SensorInfo>>& sensors);
    int getSensorStreamInfo(shared_ptr<SensorInfo>& sensor);
    int getSensorStatus(const string& cameraId, SensorStatus& status);
    int getSensorStatus(const vector<string>& camera_ids, vector<SensorStatus>& status);
    bool isServerOnline(const string & url);
private:
    int parseCameraInfo(const string& server_url, const string& xmlData, vector<shared_ptr<SensorInfo>>& sensors);
private:
    std::unique_ptr<MSCameraEvents> m_cameraEvent;
    string m_token;
    bool m_cameraEventThreadRunning;
    std::shared_ptr<ICameraStatusEvent> m_deviceEventCB;
};

} //nv_vms