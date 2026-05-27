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

#include <string.h>
#include <iostream>
#include "WebsocketServerRequestHandler.h"
#include "Websocket.h"
#include "logger.h"
#include "utils.h"
#include "error_code.h"
#include "config.h"
#include "rtspserver.h"
#include <utility>
#include <SchemaValidator.h>

using namespace std;

bool WebsocketServerRequestHandler::handleConnection(CivetServer *server, const struct mg_connection *conn)
{
    std::string queryParamName = "connectionId";
    std::string connectionId = EMPTY_STRING;
    const char* queryParam = queryParamName.c_str();
    struct mg_connection *connection = const_cast<struct mg_connection *>(conn);
    if(!CivetServer::getParam(connection, queryParam, connectionId))
    {
        LOG(error) << "Query param not found" << endl;
        return false;
    }
    if(!validatePassword(connectionId))
    {
        LOG(error) << "Invalid connection ID" << endl;
        return false;
    }
    if(!GET_WEBSOCKET_INSTANCE()->checkUniqueId(connectionId))
    {
        LOG(error) << "A websocket connection already exists with provided ID" << endl;
        return false;
    }
    return true;
}
void WebsocketServerRequestHandler::handleReadyState(CivetServer *server, struct mg_connection *conn)
{
    std::string queryParamName = "connectionId";
    std::string connectionId = EMPTY_STRING;
    const char* queryParam = queryParamName.c_str();
    if(!CivetServer::getParam(conn, queryParam, connectionId))
    {
        LOG(error) << "Invalid connection ID" << endl;
        // control will not come here
    }
    GET_WEBSOCKET_INSTANCE()->addConnection(connectionId, conn);
    return;
}

thread_local unsigned char WebsocketServerRequestHandler::current_opcode_ = 0x00;
thread_local std::stringstream WebsocketServerRequestHandler::data_;
bool WebsocketServerRequestHandler::handleData(CivetServer *server, struct mg_connection *conn, int bits, char *data, size_t data_len)
{
    Json::Value response = Json::nullValue;

    // Ignore connection close request.
    if ((bits & 0x0F) == MG_WEBSOCKET_OPCODE_CONNECTION_CLOSE)
    {
        return false;
    }

    data_.write(data, data_len);
    if (current_opcode_ == 0x00) 
    {
        current_opcode_ = bits & 0x7f;
    }

    bool ret = true;

    /**
     * The FIN bit tells whether this is the last message in a series. If it's 0, then the server 
     * keeps listening for more parts of the message; otherwise, the server should consider 
     * the message delivered.
     */
    bool is_final_fragment = bits & 0x80;
    if (is_final_fragment) 
    {
        switch (current_opcode_) 
        {
            case MG_WEBSOCKET_OPCODE_TEXT:
                ret = handleJsonData(data_.str(), response, conn);
                break;
            case MG_WEBSOCKET_OPCODE_BINARY:
                ret = handleBinaryData(data_.str(), response, conn);
                break;
            default:
                LOG(error) << "Unknown WebSocket bits flag: " << bits << endl;
                break;
        }
        // If FIN bit is set that means message is delievered. Reset the opcode and data.
        current_opcode_ = 0x00;
        data_.clear();
        data_.str(std::string());
    }
    return ret;
}

void WebsocketServerRequestHandler::handleClose(CivetServer *server, const struct mg_connection *conn)
{
    Json::Value temp_json;
    m_callbackMap["/event/disconnect"](temp_json, temp_json, (struct mg_connection *)conn);
    return GET_WEBSOCKET_INSTANCE()->removeConnection(conn);
}

bool WebsocketServerRequestHandler::sendData(string connectionId, const std::string &data, int op_code) 
{
    return GET_WEBSOCKET_INSTANCE()->sendMessage(connectionId, data, op_code);
}

bool WebsocketServerRequestHandler::sendData(struct mg_connection *conn, const std::string &data, int op_code) 
{
    return GET_WEBSOCKET_INSTANCE()->sendMessage(conn, data, op_code);
}

bool WebsocketServerRequestHandler::handleJsonData(const std::string &data, Json::Value &response, struct mg_connection *conn)
{
    Json::Value receivedData = stringToJson(data);
    if (receivedData == Json::nullValue)
    {
        // Silently drop invalid data formats
        LOG(verbose) << "Invalid JSON received on websocket connection" << endl;
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return true;
    }
    // get the websocket API key
    std::string webSocketApi = receivedData.get("apiKey", EMPTY_STRING).asString();
    if (!SchemaValidator::validateWebSocketRequest(webSocketApi, receivedData))
    {
        // Silently drop invalid data formats
        LOG(verbose) << "WebSocket Message validation failed" << endl;
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return true;
    }
    if (webSocketApi == EMPTY_STRING)
    {
        // If apiKey is not present, it could be a response to earlier request. In that case
        // fill the response and notify. VST edge to cloud use-case.
        LOG(info) << "apiKey not found, checking if requestId is present" << endl;
        std::string requestId = receivedData.get("requestId", EMPTY_STRING).asString();
        if (!requestId.empty())
        {
            LOG(info) << "requestId found: " << requestId << " filling up response and notify" << endl;
            Json::Value data = receivedData.get("data", Json::nullValue);
            GET_WEBSOCKET_INSTANCE()->fillResponseAndNotify(data, requestId);
            return true;
        }
        // Invalid JSON
        LOG(error) << "Invalid JSON data received" << endl;
        return true;
    }
    // URL prefix for optional routing (e.g., /vst/api/v1/... → /api/v1/...)
    const std::string URL_PREFIX = "/vst";

    // check if API is registered in VST
    std::string lookupApi = webSocketApi;
    if (m_callbackMap.find(lookupApi) == m_callbackMap.end())
    {
        // If not found, try stripping /vst prefix if present
        if (webSocketApi.find(URL_PREFIX) == 0)
        {
            lookupApi = webSocketApi.substr(URL_PREFIX.length());
        }

        if (m_callbackMap.find(lookupApi) == m_callbackMap.end())
        {
            LOG(error) << "API not found: " << webSocketApi << endl;
            SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
            sendData(conn, jsonToString(response), MG_WEBSOCKET_OPCODE_TEXT);
            return true;
        }
    }
    // execute the API
    m_callbackMap[lookupApi](receivedData, response, conn);
    // send back the response
    sendData(conn, jsonToString(response), MG_WEBSOCKET_OPCODE_TEXT);
    return true;
}

bool WebsocketServerRequestHandler::handleBinaryData(const std::string &data, Json::Value &response, struct mg_connection *conn)
{
    // Binary data not supported yet
    SET_VMS_ERROR(VmsErrorCode::VMSNotSupportedError, response)
    return true;
}

void WebsocketServerRequestHandler::addRequestHandler(std::map<std::string, httpFunction, std::less<>>& func)
{
    // register handlers
    for (auto it : func)
    {
        std::lock_guard<std::mutex> callbackLock(m_callbackMapMutex);
        m_callbackMap.insert({it.first, it.second});
    }
}
