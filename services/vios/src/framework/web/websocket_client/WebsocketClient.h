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

#include <string>
#include <cstring>
#include <mutex>
#include <vector>
#include "civetweb.h"
#include "logger.h"
#include "MessageObject.h"
#include <Scheduler.h>
#include "WebsocketInterface.h"

#define GET_WEBSOCKET_CLIENT WebsocketClient::getInstance
#define WS_EBUF_LEN 1024

class WebsocketClient
{
public:
    static WebsocketClient *getInstance()
    {
        if (m_instance == nullptr)
        {
            m_instance = new WebsocketClient;
        }
        return m_instance;
    }
    static void destroy()
    {
        if (m_instance)
        {
            delete m_instance;
            m_instance = nullptr;
        }
    }
    typedef std::function<void(const Json::Value &, Json::Value &, struct mg_connection *conn)> httpFunction;
    void addRequestHandler(std::map<std::string, httpFunction, std::less<>> &func);

    bool connect();
    bool sendMsg(const std::string &msg, std::shared_ptr<MessageObject> wsResponseInfo);
    bool sendMsg(const std::string &msg);
    bool processReceivedMessage(struct mg_connection *conn, int flags, char *data, size_t data_len);
    bool isConnected();
    void handleClose(const struct mg_connection *conn);
    void registerListener(IWebsocketNotification *listener);
    void deRegisterListener(IWebsocketNotification *listener);

private:
    WebsocketClient();
    ~WebsocketClient();
    void fillResponseAndNotify(Json::Value &response, string requestId);
    void websocketClientMonitorTask();
    void checkPendingRequests();
    void parseRemoteAddress();

    WebsocketClient(const WebsocketClient &) = delete;
    WebsocketClient &operator=(const WebsocketClient &) = delete;
    static WebsocketClient *m_instance;
    // Websocket connection and its mutex
    struct mg_connection *m_connection;
    std::mutex m_connectionMutex;
    // Buffer to handle data
    std::vector<char> m_ebuf;
    // Callback map and its mutex
    std::map<std::string, httpFunction, std::less<>> m_callbackMap;
    std::mutex m_callbackMapMutex;
    // CallerList map and its mutex. It waits for WS response and notifies the caller.
    std::map<string, std::shared_ptr<MessageObject>, std::less<>> m_callerList;
    std::mutex m_callerListMutex;
    // Watchdog to clear stale pending requests
    std::unique_ptr<Bosma::Scheduler> m_watchdog;

    std::string m_ip;
    int m_port;
    std::string m_path;
    int m_useSSL;
    std::atomic<bool> m_isConnected;

    std::mutex m_listenerMutex;
    std::set<IWebsocketNotification*> m_listeners;
};
