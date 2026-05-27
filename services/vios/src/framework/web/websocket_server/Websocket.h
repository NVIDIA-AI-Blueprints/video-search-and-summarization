/*
 * SPDX-FileCopyrightText: Copyright (c) 2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <chrono>
#include <list>
#include <map>
#include <functional>
#include <jsoncpp/json/json.h>

#include "CivetServer.h"
#include "error_code.h"
#include "database.h"
#include "MessageObject.h"
#include <Scheduler.h>
#include <memory>

#define GET_WEBSOCKET_INSTANCE Websocket::getInstance
inline constexpr std::chrono::seconds WS_SERVER_WATCH_DOG_INTERVAL{10};

using namespace nv_vms;

struct WebsocketData
{
    struct mg_connection *connection;
    std::mutex connectionMutex;
    std::string peerId;
};

class Websocket
{
    public:
        static std::shared_ptr<Websocket> getInstance()
        {
            if (m_instance == nullptr)
            {
                LOG(info) << "Websocket Instance Created" << endl;
                m_instance = std::shared_ptr<Websocket>(new Websocket());
            }
            return m_instance;
        }
        
        static void deleteInstance()
        {
            if (m_instance != nullptr)
            {
                LOG(info) << "Websocket Instance Deleted" << endl;
                m_instance.reset();
            }
        }

        void addConnection(std::string peerId, struct mg_connection *conn);
        void removeConnection(const struct mg_connection *conn);
        bool sendMessage(std::string peerId, std::string msg, int op_code);
        bool sendMessage(struct mg_connection *conn, std::string msg, int op_code);
        bool sendMessage(std::string peerId, std::string msg, int op_code, std::shared_ptr<MessageObject> wsResponseInfo);
        bool sendMessage(struct mg_connection *conn, std::string msg, int op_code, std::shared_ptr<MessageObject> wsResponseInfo);
        void fillResponseAndNotify(Json::Value &response, string requestId);
        bool broadcastMessage(std::string msg);
        bool checkUniqueId(std::string id);
        void checkPendingRequests();
        bool isConnected(std::string connectionId);
        bool waitingForConnection(std::string connectionId);
        std::string getFirstConnectionId();

        ~Websocket()
        {
            LOG(info) << "Exiting from websocket" << endl;
            m_isExiting = true;
            m_watchdog.reset();
            {
                LOG(info) << "Clearing websocket connections" << endl;
                std::lock_guard<std::mutex> lock(m_connectionsMutex);
                m_connections.clear();
            }
            LOG(info) << "Websocket connections cleared" << endl;
        }

    private:
        Websocket()
        {
            m_watchdog = make_unique<Bosma::Scheduler>(1);
            m_watchdog->interval(WS_SERVER_WATCH_DOG_INTERVAL, [=]()
                                  { checkPendingRequests(); });
        }

        static std::shared_ptr<Websocket>                                   m_instance;
        std::unordered_map<std::string, std::shared_ptr<WebsocketData>>     m_connections;
        std::mutex                                                          m_connectionsMutex;
        std::map<std::string, std::shared_ptr<MessageObject>, std::less<>>               m_callerList;
        std::mutex                                                          m_callerListMutex;
        std::unique_ptr<Bosma::Scheduler>                                   m_watchdog;
        bool                                                                m_isExiting = false;
};
