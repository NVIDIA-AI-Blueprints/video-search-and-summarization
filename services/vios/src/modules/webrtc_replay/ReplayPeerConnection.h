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
#include "unified_storage_reader.h"
#include "TempFileScheduler.h"

using namespace std;
using namespace nv_vms;

class ReplayPeerConnection : public IVstModule
{
public:
    IVstModule* createPeerConnectionLiveManagerObject();
    void deletePeerConnectionLiveManagerObject( IVstModule* object );
private:
    std::shared_ptr<PeerConnectionManager> m_peerConnectionManager;
    std::shared_ptr<nv_vms::DeviceManager> m_deviceManager;

    VmsErrorCode handleReplayConfiguration(const Json::Value &req_info, const Json::Value &in, Json::Value &response);
    VmsErrorCode getVersion(const Json::Value& req_info, const Json::Value &in, Json::Value &response);
    VmsErrorCode getReplayHelp(const Json::Value& req_info, const Json::Value &in, Json::Value &response);
    VmsErrorCode handleReplayAPIrequest(const Json::Value &, const Json::Value &in, Json::Value &out, struct mg_connection *conn);

    std::shared_ptr<UnifiedStorageReader> m_unifiedStorageReader;
    std::unique_ptr<TempFileScheduler> m_imageCleanupScheduler;

public:
    ReplayPeerConnection(std::shared_ptr<PeerConnectionManager> peerConnectionManager, std::shared_ptr<nv_vms::DeviceManager> deviceManager);
    ~ReplayPeerConnection();
    const std::map<std::string,HttpServerRequestHandler::httpFunction, std::less<>> getHttpApi() override { return m_func; };

    std::shared_ptr<UnifiedStorageReader> getUnifiedStorageReader() { return m_unifiedStorageReader; }
    bool initUnifiedStorageReader();
};

inline ReplayPeerConnection* GET_PEERCONNECTION_REPLAY_MNGR()
{
    return static_cast<ReplayPeerConnection*>(ModuleLoader::getInstance()->getPeerConnectionReplayInstance());
}