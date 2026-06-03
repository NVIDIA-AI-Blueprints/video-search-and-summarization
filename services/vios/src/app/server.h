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

#include <iostream>
#include <fstream>
#include <map>
#include <memory>

#include "gmainloop_manager.h"
#include "device_manager.h"
#include "sensor_management.h"
#include "webServer.h"
#ifdef USE_GRPC_SERVER
#include "nvgrpc.h"
#endif
#include "websocket_apis.h"
#include "user_apis.h"
#include "rtspserver.h"
#include "syncInterface.h"
#include "rtc_base/thread.h"
#include "libasync++/async++.h"
#include "macros.h"
#include "vstmodule.h"
#include "sensor_management_apis.h"
#include "remote_sensor_control_apis.h"
#include "peerconnection_manager_apis.h"
#include "MessageBus.h"

void signal_handler( int signal_num );
static const std::vector<std::string> g_deprecatedApis = {
    "/api/getLiveStreamUriList",
    "/api/getReplayStreamUriList"
};

class VmsServer
{
public:
    VmsServer(ModuleId module_id = ModuleAll);
    ~VmsServer();
    int start();
    void start_webrtc();
    friend void signal_handler( int signal_num );

#ifdef UNIT_TEST
    std::shared_ptr<UserRESTApis> getUserInstance() { return m_userApis; }
    std::shared_ptr<nv_vms::SensorManagement> getSensorManagement() { return m_sensorManagement; }
#endif
    std::atomic<bool> m_serverReady;
private:
    int startGLoop();
    void stopGLoop();
    Json::Value getAPIInfo();
    int initialize();
    void handleRestAPIs();
    bool remoteDeviceRequireHTTPS();
    void checkLibsSanity ();

private:
    ModuleId m_moduleId;
    std::map<std::string,HttpServerRequestHandler::httpFunction, std::less<>>  m_func;
    std::shared_ptr<SensorManagementApis> m_senorManagementApis = nullptr;
    std::shared_ptr<PeerConnectionManagerApis> m_peerConnectionManagerApis = nullptr;
    std::shared_ptr<WebsocketApis> m_websocketApis = nullptr;
    std::shared_ptr<RemoteSensorControlApis> m_remoteSensorControlApis = nullptr;
    std::shared_ptr<UserRESTApis> m_userApis = nullptr;
    std::shared_ptr<nv_vms::SensorManagement> m_sensorManagement = nullptr;
    std::shared_ptr<nv_vms::DeviceManager> m_deviceManager = nullptr;
    std::shared_ptr<WebServer> m_webServer = nullptr;
    std::shared_ptr<MessageBus> m_messageBus = nullptr;
#ifdef USE_GRPC_SERVER
    std::unique_ptr<GrpcServer> m_grpcServer = nullptr;
#endif
    ModuleLoader *m_moduleLoader;
    shared_ptr <IVstModule> m_rtspServer = nullptr;
    GMainLoopManager m_gmainLoop;
    async::task<void> m_gmainLoopTask;
};
