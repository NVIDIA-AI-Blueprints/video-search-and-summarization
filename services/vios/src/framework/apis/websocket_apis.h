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

#include "sensor_management.h"
#include <jsoncpp/json/json.h>
#include "WebsocketServerRequestHandler.h"
#include "database.h"
#include "HttpServerRequestHandler.h"

using namespace nv_vms;

class WebsocketApis
{
    public:
        WebsocketApis(std::shared_ptr<DeviceManager> deviceMngr);
        typedef std::function<VmsErrorCode(const Json::Value &, const Json::Value &, Json::Value &, struct mg_connection *conn)> httpFunction;
        const std::map<std::string,WebsocketServerRequestHandler::httpFunction, std::less<>> getWebsocketApis() { return m_func; };
        void addRequestHandler(std::map<std::string, httpFunction, std::less<>>& func);

#ifndef UNIT_TEST
    private:
#endif
#ifndef RELEASE
    void getVstLogs(const Json::Value& req_info, Json::Value &response, struct mg_connection *conn);
#endif
#ifdef USE_GRPC_CLIENT
    void addDummyUdpTrack(const Json::Value& receivedData);
#endif
    void handleSensorCredentials(const Json::Value &data, Json::Value &response);
    void handleSensorSettings(const Json::Value &receivedData, Json::Value &response);
    void handleSensorRemove(const Json::Value &receivedData, Json::Value &response);
    void handleSensorStatus(const Json::Value &receivedData, Json::Value &response);
    void getIceServers(const Json::Value& receivedData, string api, Json::Value &response);
    void streamStart(const Json::Value& receivedData, string api, Json::Value &response, struct mg_connection *conn);
    void streamStop(const Json::Value& receivedData, string api, Json::Value &response);
    void setAnswer(const Json::Value& receivedData, string api, Json::Value &response);
    void postIceCandidate(const Json::Value& receivedData, string api, Json::Value &response);
    void getConfiguration(const Json::Value& receivedData, string api, Json::Value &response);
    void setRAGStatus(const Json::Value& req_info, Json::Value &response);
    void setRAGEndpoint(const Json::Value& req_info, Json::Value &response);
    void handleWsDisconnect(struct mg_connection *conn);
    void getStreamQuery(const Json::Value& req_info, string api, Json::Value &response);
    void getStreamStatus(const Json::Value& req_info, string api, Json::Value &response);
    private:
        std::map<std::string,WebsocketServerRequestHandler::httpFunction, std::less<>>  m_func;
        std::shared_ptr<DeviceManager> m_deviceManager;
        std::map<std::string, httpFunction, std::less<>> m_callbackMap;
};
