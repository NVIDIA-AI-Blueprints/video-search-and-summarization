/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
        virtual void notifyEvent(const SensorStatus& status, const std::string& camera_ip)
        {
            // Optional notification hook: implementers that only care about onDeviceEvent()
            // intentionally leave this as a no-op.
        }
};


class MSCameraEvents
{
    public:
        MSCameraEvents (std::shared_ptr<DeviceManager> deviceMngr, const std::string token,
                      std::shared_ptr<ICameraStatusEvent> cb) : m_token(token)
                                            , m_deviceManager(deviceMngr)
                                            , m_callback(cb)
                                            , m_exit(false)
        {
            m_url = deviceMngr->url + std::string(":") + std::string("7563") + std::string("/recorderstatusservice/recorderstatusservice2.asmx");
            m_thread = std::thread([this] { this->cameraEventTask(); });
        }
        ~MSCameraEvents()
        {
            LOG(info) << __METHOD_NAME__ << std::endl;
            m_exit = true;
            m_thread.join();
        }
        void cameraEventTask();
        int getCurrentSensorStatus(const std::string& camera_id, SensorStatus& status);
        int getCurrentSensorStatus(const std::vector<std::string>& camera_ids, std::vector<SensorStatus>& status);
    private:
        int subscribeToStatusSession();
        int unSubscribeFromStatusSession();
        int getSensorStatus(SensorStatus& status);
    private:
        std::string m_token;
        std::shared_ptr<DeviceManager> m_deviceManager;
        std::shared_ptr<ICameraStatusEvent> m_callback;
        bool m_exit;
        std::string m_url;
        std::string m_statusSessionId;
        std::thread m_thread;
};


class MilestoneVmsVendor : public ISensorControlInterface
{
public:
    MilestoneVmsVendor() : m_cameraEventThreadRunning(false) {}
    virtual ~MilestoneVmsVendor ()
    {
        LOG(info) << __METHOD_NAME__ << std::endl;
    }

    int connect();
    int getSensorStreamInfo(std::vector<std::shared_ptr<SensorInfo>>& sensors);
    int getSensorStreamInfo(std::shared_ptr<SensorInfo>& sensor);
    int getSensorStatus(const std::string& cameraId, SensorStatus& status);
    int getSensorStatus(const std::vector<std::string>& camera_ids, std::vector<SensorStatus>& status);
    bool isServerOnline(const std::string & url);
private:
    int parseCameraInfo(const std::string& server_url, const std::string& xmlData, std::vector<std::shared_ptr<SensorInfo>>& sensors);
private:
    std::unique_ptr<MSCameraEvents> m_cameraEvent;
    std::string m_token;
    bool m_cameraEventThreadRunning;
    std::shared_ptr<ICameraStatusEvent> m_deviceEventCB;
};

} //nv_vms