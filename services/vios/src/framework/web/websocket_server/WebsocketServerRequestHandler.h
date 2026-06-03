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

#include <list>
#include <map>
#include <functional>
#include <jsoncpp/json/json.h>

#include "CivetServer.h"
#include "error_code.h"
#include "Websocket.h"


using namespace nv_vms;

/* ---------------------------------------------------------------------------
**  Civet Websocket callback 
** -------------------------------------------------------------------------*/
class WebsocketServerRequestHandler : public CivetWebSocketHandler
{
public:
    WebsocketServerRequestHandler()
    {
    }
    ~WebsocketServerRequestHandler()
    {
        LOG(info) << "Exiting from websocket server request handler" << endl;
    }
    // standard websocket handlers from CivetWebSocketHandler
    bool handleConnection(CivetServer *server, const struct mg_connection *conn);
    void handleReadyState(CivetServer *server, struct mg_connection *conn);
    bool handleData(CivetServer *server, struct mg_connection *conn, int bits, char *data, size_t data_len);
    void handleClose(CivetServer *server, const struct mg_connection *conn);
    // handling different types of received data
    bool handleJsonData(const std::string &data, Json::Value &response, struct mg_connection *conn);
    bool handleBinaryData(const std::string &data, Json::Value &response, struct mg_connection *conn);
    // sending data on websockets
    bool sendData(string connectionId, const std::string &data, int op_code);
    bool sendData(struct mg_connection *conn, const std::string &data, int op_code);

    typedef std::function<void(const Json::Value &, Json::Value &, struct mg_connection *conn)> httpFunction;
    void addRequestHandler(std::map<std::string,httpFunction, std::less<>>& func);

private:
    /**
     * APIs are inserted into this map at VST boot time and that is when m_callbackMapMutex is used. The 
     * corresponding std::function(s) are executed for the APIs if API key is matched.
     */
    std::map<std::string, httpFunction, std::less<>>                     m_callbackMap;
    std::mutex                                              m_callbackMapMutex;
    /**
     * Multiple threads share the same thread_local variable, but changes to the variable are isolated 
     * to each thread. In case of receiving fragmented message, websocket opcode and accumulated data is stored.
     */
    thread_local static unsigned char current_opcode_;
    thread_local static std::stringstream data_;
};

