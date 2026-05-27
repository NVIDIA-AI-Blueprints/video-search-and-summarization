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

using namespace std;
using namespace nv_vms;

class StreamBridgeService : public IVstModule
{
public:
    IVstModule* createStreamBridgeObject();
    void deleteStreamBridgeObject( IVstModule* object );
private:
    typedef std::function<VmsErrorCode(const Json::Value &, const Json::Value &, Json::Value &, struct mg_connection *conn)> httpFunction;
    std::shared_ptr<PeerConnectionManager> m_peerConnectionManager;
    std::shared_ptr<nv_vms::DeviceManager> m_deviceManager;
    std::unordered_set<std::string> m_remoteConnections;
    std::map<std::string, httpFunction, std::less<>> m_callbackMap;

    VmsErrorCode handleStreambridgeConfiguration(const Json::Value &req_info, const Json::Value &in, Json::Value &response);
    VmsErrorCode getVersion(const Json::Value& req_info, const Json::Value &in, Json::Value &response);
    VmsErrorCode getStreambridgeHelp(const Json::Value& req_info, const Json::Value &in, Json::Value &response);
    VmsErrorCode handleStreambridgeAPIrequest(const Json::Value &, const Json::Value &in, Json::Value &out, struct mg_connection *conn);
    VmsErrorCode handleWsDisconnect(const string peerId);

public:
    StreamBridgeService(std::shared_ptr<PeerConnectionManager> peerConnectionManager, std::shared_ptr<nv_vms::DeviceManager> deviceManager);
    ~StreamBridgeService();
    const std::map<std::string,HttpServerRequestHandler::httpFunction, std::less<>> getHttpApi() override { return m_func; };
    void addRequestHandler(std::map<std::string, httpFunction, std::less<>>& func);
};

inline StreamBridgeService* GET_STREAM_BRIDGE_MNGR()
{
    return static_cast<StreamBridgeService*>(ModuleLoader::getInstance()->getStreamBridgeInstance());
}