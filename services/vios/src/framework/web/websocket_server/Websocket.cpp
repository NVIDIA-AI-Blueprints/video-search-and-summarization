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
#include "Websocket.h"
#include "logger.h"
#include "utils.h"
#include "error_code.h"
#include "config.h"
#include "rtspserver.h"
#include <utility>

std::shared_ptr<Websocket> Websocket::m_instance = nullptr;

using namespace std;

#define WEBSOCKET_TIMEOUT_THREAD_COUNT 1

void Websocket::addConnection(std::string peerId, struct mg_connection *conn)
{
    {
        std::lock_guard<std::mutex> lock(m_connectionsMutex);
        LOG(info) << "Saving connection ID: " << peerId << std::endl;
        
        // Create a new WebsocketData instance
        std::shared_ptr<WebsocketData> ws = std::make_shared<WebsocketData>();
        ws->connection = conn;
        ws->peerId = peerId;
        
        // Add to the connections map
        m_connections.emplace(peerId, ws);
        
        // Set the user connection data to the WebsocketData pointer
        mg_set_user_connection_data(conn, ws.get());
        
        LOG(info) << "Memory allocated to websocket connection ID: " << peerId << std::endl;
    }
    LOG(info) << "Total websocket connections: " << m_connections.size() << std::endl;
}

void Websocket::removeConnection(const struct mg_connection *conn)
{
    LOG(info) << "Closing websocket connection" << endl;
    std::string connectionId = EMPTY_STRING;
    
    // Get the WebsocketData pointer directly
    WebsocketData *websocketData = (WebsocketData *)mg_get_user_connection_data(conn);
    if (websocketData == nullptr)
    {
        LOG(error) << "Unable to remove websocket connection" << endl;
        return;
    }
    connectionId = websocketData->peerId;
    
    {
        std::lock_guard<std::mutex> lock(m_connectionsMutex);
        auto itr = m_connections.find(connectionId);
        if (itr != m_connections.end())
        {
            LOG(info) << "Found connection ID to close" << std::endl;
            if (itr->second.get() == websocketData)
            {
                LOG(verbose2) << "Reference count before erase: " << itr->second.use_count() << std::endl;
                m_connections.erase(itr); // Erase the connection
                LOG(info) << "Memory de-allocated from websocket connection ID: " << connectionId << std::endl;
            }
            else
            {
                LOG(error) << "Websocket context mismatch" << std::endl;
            }
        }
        else
        {
            LOG(error) << "Unable to get user context, can't close connection" << endl;
        }
    }
    LOG(info) << "Connection closed. Total websocket connections: " << m_connections.size() << endl;
}

void Websocket::fillResponseAndNotify(Json::Value &response, string requestId)
{
    std::lock_guard<std::mutex> lock(m_callerListMutex);
    auto itr = m_callerList.find(requestId);
    if (itr != m_callerList.end())
    {
        LOG(verbose) << "Found a pending caller" << endl;
        auto obj = itr->second;
        LOG(verbose2) << "response: \n" << response << endl;
        obj->m_response = response;
        LOG(verbose) << "Signaling the caller" << endl;
        obj->m_isResponseReceived = true;
        obj->m_sync.signal();
        LOG(verbose) << "Signaled" << endl;
        // safe to erase if key not present
        m_callerList.erase(requestId);
        LOG(info) << "Notified and erased caller, request ID: " << requestId << endl;
    }
    else
    {
        LOG(error) << "Failed to notify, no pending request found" << endl;
    }
}

bool Websocket::sendMessage(std::string peerId, std::string msg, int op_code, std::shared_ptr<MessageObject> wsResponseInfo)
{
    {
        std::lock_guard<std::mutex> lock(m_callerListMutex);
        m_callerList[wsResponseInfo->m_responseId] = wsResponseInfo;
    }
    return sendMessage(peerId, msg, op_code);
}

bool Websocket::sendMessage(struct mg_connection *conn, std::string msg, int op_code, std::shared_ptr<MessageObject> wsResponseInfo)
{
    {
        std::lock_guard<std::mutex> lock(m_callerListMutex);
        m_callerList[wsResponseInfo->m_responseId] = wsResponseInfo;
    }
    return sendMessage(conn, msg, op_code);
}

bool Websocket::sendMessage(std::string peerId, std::string data, int op_code)
{
    if (peerId.empty() || data.empty())
    {
        LOG(error) << "Invalid peer ID or empty data" << endl;
        return false;
    }

    std::shared_ptr<WebsocketData> ws;
    {
        std::lock_guard<std::mutex> lock(m_connectionsMutex);
        auto itr = m_connections.find(peerId);
        if (itr == m_connections.end())
        {
            LOG(error) << "Trying to send to an uncached connection, skipping." << endl;
            return false;
        }
        ws = itr->second;
    }

    std::lock_guard<std::mutex> lock(ws->connectionMutex);
    LOG(verbose2) << "Writing " << data.size() << " bytes on websocket with ID: " << peerId << std::endl;
    size_t ret = mg_websocket_write(ws->connection, op_code, data.c_str(), data.size());
    if (ret != data.size())
    {
        std::string msg = (ret == 0) ? "Connection Closed" : (ret < 0) ? "Send Error: " + std::string(std::strerror(errno)) : "Partial data sent";
        LOG(warning) << "Failed to send data via websocket connection. Reason: " << msg;
        return false;
    }

    return true;
}

bool Websocket::sendMessage(struct mg_connection *conn, std::string data, int op_code)
{
    struct WebsocketData *websocketContext =  (struct WebsocketData *)mg_get_user_connection_data(conn);
    if(websocketContext != nullptr)
    {
        return sendMessage(websocketContext->peerId, data, op_code);
    }
    LOG(error) << "Failed to send message" << endl;
    return true;
}

bool Websocket::broadcastMessage(std::string msg)
{
    std::vector<std::string> connectionsToSend;
    {
        std::lock_guard<std::mutex> lock(m_connectionsMutex);
        if (m_connections.empty())
        {
            return true;
        }
        for (auto &connection : m_connections)
        {
            connectionsToSend.push_back(connection.first);
        }
    }

    bool allSuccess = true;
    for (string peerId : connectionsToSend)
    {
        if (!sendMessage(peerId, msg, MG_WEBSOCKET_OPCODE_TEXT))
        {
            allSuccess = false;
        }
    }
    return allSuccess;
}

bool Websocket::checkUniqueId(std::string id)
{
    std::lock_guard<std::mutex> lock(m_connectionsMutex);
    if(m_connections.find(id) != m_connections.end())
    {
        return false;
    }
    return true;
}

void Websocket::checkPendingRequests()
{
    std::lock_guard<std::mutex> lock(m_callerListMutex);
    std::vector<std::string> requestsToRemove;
    auto currentTime = std::chrono::system_clock::now();

    for (auto it = m_callerList.begin(); it != m_callerList.end(); ++it)
    {
        auto &obj = it->second;
        auto timeDifference = currentTime - obj->m_timestamp;
        double seconds = std::chrono::duration<double>(timeDifference).count();

        if (seconds > 10)
        {
            requestsToRemove.push_back(it->first);
        }
    }

    for (const auto &requestId : requestsToRemove)
    {
        LOG(warning) << "Removing stale request with ID: " << requestId << std::endl;
        m_callerList.erase(requestId);
    }
}

bool Websocket::isConnected(std::string connectionId)
{
    std::lock_guard<std::mutex> lock(m_connectionsMutex);
    for (auto itr: m_connections)
    {
        if (itr.first == connectionId)
        {
            return true;
        }
    }
    return false;
}

bool Websocket::waitingForConnection(std::string connectionId)
{
    int attempts = 20;
    bool result = false;
    do
    {
        LOG(info) << "Waiting for connection " << connectionId << endl;
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
        result = isConnected(connectionId);
        attempts--;
    } while (!m_isExiting && !result && attempts > 0);
    LOG(info) << "Done waiting: " << connectionId << " result: " << result << endl;
    return result;
}

std::string Websocket::getFirstConnectionId()
{
    string connectionId = EMPTY_STRING;
    std::lock_guard<std::mutex> lock(m_connectionsMutex);
    if (m_connections.begin() != m_connections.end())
    {
        connectionId = m_connections.begin()->first;
    }
    return connectionId;
}
