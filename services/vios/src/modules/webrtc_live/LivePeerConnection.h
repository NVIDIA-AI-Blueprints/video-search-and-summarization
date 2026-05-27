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

#include <memory>
#include "PeerConnectionManager.h"
#include "device_manager.h"
#include "vstmodule.h"
#include "TempFileScheduler.h"

using namespace std;
using namespace nv_vms;

class LivePeerConnection : public IVstModule
{
public:
    IVstModule* createPeerConnectionLiveManagerObject();
    void deletePeerConnectionLiveManagerObject( IVstModule* object );
private:
    std::shared_ptr<PeerConnectionManager> m_peerConnectionManager;
    std::shared_ptr<nv_vms::DeviceManager> m_deviceManager;

    VmsErrorCode handleLiveConfiguration(const Json::Value &req_info, const Json::Value &in, Json::Value &response);
    VmsErrorCode getVersion(const Json::Value& req_info, const Json::Value &in, Json::Value &response);
    VmsErrorCode getLiveHelp(const Json::Value& req_info, const Json::Value &in, Json::Value &response);
    VmsErrorCode handleLiveAPIrequest(const Json::Value &, const Json::Value &in, Json::Value &out, struct mg_connection *conn);

    std::unique_ptr<TempFileScheduler> m_imageCleanupScheduler;

public:
    LivePeerConnection(std::shared_ptr<PeerConnectionManager> peerConnectionManager, std::shared_ptr<nv_vms::DeviceManager> deviceManager);
    ~LivePeerConnection();
    const std::map<std::string,HttpServerRequestHandler::httpFunction, std::less<>> getHttpApi() override { return m_func; };
};

inline LivePeerConnection* GET_PEERCONNECTION_LIVE_MNGR()
{
    return static_cast<LivePeerConnection*>(ModuleLoader::getInstance()->getPeerConnectionLiveInstance());
}