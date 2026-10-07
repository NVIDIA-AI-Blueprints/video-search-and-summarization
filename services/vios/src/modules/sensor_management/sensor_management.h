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

#include "vstmodule.h"
#include "sensor_monitoring.h"
#include "device_manager.h"
#include "Scheduler.h"
#include "sensor_control.h"
#include "stream_monitor.h"
#include "WebsocketInterface.h"
#include <cstdint>
#include <memory>
#include <string>
#include <vector>
#include <map>
#include <unordered_map>
#include <jsoncpp/json/json.h>
#include "NotificationFactory.h"


#define GET_DEVICE_MANAGER  SensorManagement::getDeviceManagerObject
namespace nv_vms
{
    class SensorManagement : public std::enable_shared_from_this<SensorManagement>, public IVstModule, public INotificationListener, public IStreamStatusEvent
    {
    public:
        IVstModule* createSensorManagementObject();
        void deleteSensorManagementObject(IVstModule* object);
        explicit SensorManagement(std::shared_ptr<DeviceManager> deviceMngr);
        ~SensorManagement();
        std::shared_ptr<DeviceManager> getDeviceManagerObject();
        std::shared_ptr<SensorManagement> getptr() { return std::shared_ptr<SensorManagement>(this); }
        void start();
        void stop();
        void scanCameras();
        void scanCameras(bool force);

        std::vector<std::shared_ptr<SensorInfo>> getSensorInfo(bool rescan = true);
        std::shared_ptr<SensorInfo> getSensorInfo(const std::string & sensor_id, bool force = false);
        int setSensorInfo(const std::string sensor_id);
        int addSensorManually(std::shared_ptr<SensorInfo>& sensorInfo, std::string& response); // TODO: This API is calling from the storage management also for the NVstreamer case, We need to remove the dependency in storge management
        std::string addStream(std::shared_ptr<StreamInfo> stream);
        int deleteSensor(const std::string sensor_id, bool isReqFromCloudDevice = false, bool isReqFromEdgeDevice = false); // TODO: This API is calling from the storage management also for the NVstreamer case, We need to remove the dependency in storge management
        void notifyVmsRedinessEvent();
        void notifyVmsExitEvent();
        virtual void onDecoderPlayingStatus(const std::string &url);
        int rebootSensorDiscovery();
        VmsErrorCode replaceSensor(const std::string& old_sensor_id, const std::string& new_sensor_id);
        bool isRemovedByUser(const SensorInfo& sensorInfo);
        VmsErrorCode addSensorToEdgeVst(const Json::Value& sensorInfo);
        std::vector<std::shared_ptr<SensorInfo>> getSensors();
        std::shared_ptr<SensorControl> getSensorControl();
        SensorMonitoring* startSensorDiscovery();
        void onMessage (Json::Value payload) override;
        void onCameraStreaming(const std::string &streamId, const std::string &proxy_url, const std::string &vod_url, const StreamStatus newStatus);
        void onStreamStatusChange(const std::string &url, const StreamStatus newStatus, StreamEncParam& details) override { return; }
    private:
        void setConfigValues(std::shared_ptr<DeviceManager>);
        int getAndAddProxyUrl(std::shared_ptr<SensorInfo>& sensorInfo, const std::string&);
        void deleteSensorDetails(const std::string& sensor_id);
        void makeNativeSensorsOffline();

        std::shared_ptr<SensorManagement> getself()
        {
            try
            {
                return shared_from_this();
            }
            catch (const std::bad_weak_ptr& e)
            {
                LOG(error) << "Bad Weak pointer error: " << e.what() << std::endl;
            }
            return std::shared_ptr<SensorManagement>(nullptr);
        }

    private:
        std::shared_ptr<DeviceManager> m_deviceManager;
        std::mutex m_sensorsMutex;
        std::mutex m_cameraStatusMutex;
        std::shared_ptr<SensorMonitoring>  m_sensorMonitoring;
        std::shared_ptr<SensorControl>  m_sensorControl;
        std::vector<std::string> m_userRemovedList;
        std::mutex m_userRemovedListMutex;
    };

    struct SensorManagementObjDeleter {
        void operator()(SensorManagement* ptr) const
        {
            LOG(info) << "Custom deleter is called for SensorManagement object" << std::endl;
        }
    };

    inline SensorManagement* GET_SENSOR_MNGT()
    {
        return static_cast<SensorManagement*>(ModuleLoader::getInstance()->getSensorMngtInstance());
    }
} //nv_vms