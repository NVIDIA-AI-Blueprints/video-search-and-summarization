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

#include "WebsocketClient.h"
#include "error_code.h"
#include "database.h"

using namespace std;

constexpr const char* WEBSOCKET_SERVER_PATH = "/vms/ws?connectionId=";
constexpr const char* HTTPS_PROTOCOL = "https://";

WebsocketClient *WebsocketClient::m_instance = nullptr;

// WebSocket data handler implementation
bool WebsocketClient::processReceivedMessage(struct mg_connection *conn, int flags, char *data, size_t data_len)
{
    Json::Value response;
    // Convert the received data to a std::string
    std::string receivedString(data, data_len);

    // Print the received data
    LOG(verbose) << "Received data: " << receivedString << std::endl;

    Json::Value receivedData = stringToJson(receivedString);
    if (receivedData == Json::nullValue)
    {
        // Silently drop invalid data formats
        LOG(verbose) << "Invalid JSON received on websocket connection" << endl;
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response)
        return true;
    }
    // get the websocket API key
    std::string webSocketApi = receivedData.get("apiKey", EMPTY_STRING).asString();
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
            LOG(info) << "data: " << data << endl;
            fillResponseAndNotify(data, requestId);
            return true;
        }
        // Invalid JSON
        LOG(error) << "Invalid JSON data received" << endl;
        return true;
    }
    // check if API is registered in VST
    if (m_callbackMap.find(webSocketApi) == m_callbackMap.end())
    {
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        sendMsg(jsonToString(response));
        return true;
    }
    // execute the API
    m_callbackMap[webSocketApi](receivedData, response, conn);
    // send back the response
    sendMsg(jsonToString(response));
    return true;
}

// WebSocket data handler
static int wSDataHandler(struct mg_connection *conn, int flags, char *data, size_t data_len, void *user_data)
{
    return WebsocketClient::getInstance()->processReceivedMessage(conn, flags, data, data_len);
}

// WebSocket close handler
static void wSCloseHandler(const struct mg_connection *conn, void *user_data)
{
    WebsocketClient *ws = (WebsocketClient *)user_data;
    if (ws)
    {
        return ws->handleClose(conn);
    }
    return;
}

// Constructor
WebsocketClient::WebsocketClient() : m_connection(nullptr),
                                     m_ebuf(WS_EBUF_LEN), 
                                     m_ip(""),
                                     m_port(80), 
                                     m_path(""), 
                                     m_useSSL(0),
                                     m_isConnected(false)
{
    LOG(info) << __PRETTY_FUNCTION__ << endl;
    std::fill(m_ebuf.begin(), m_ebuf.end(), 0);
    parseRemoteAddress();
    int keepAliveMs = max(5000, GET_CONFIG().websocket_keep_alive_ms);
    auto chronoMs = std::chrono::milliseconds(keepAliveMs);
    m_watchdog = make_unique<Bosma::Scheduler>(1);
    m_watchdog->interval(chronoMs, [=]()
                         { websocketClientMonitorTask(); });
}

// Destructor
WebsocketClient::~WebsocketClient()
{
    std::lock_guard<std::mutex> lock(m_connectionMutex);
    LOG(info) << __PRETTY_FUNCTION__ << endl;
    m_watchdog.reset();
    if (m_connection)
    {
        mg_close_connection(m_connection);
    }
}

void WebsocketClient::websocketClientMonitorTask()
{
    LOG(verbose2) << __PRETTY_FUNCTION__ << endl;
    if (isConnected())
    {
        sendMsg("ping");
        checkPendingRequests();
    }
    else
    {
        LOG(info) << "WS client not connected, retrying..." << endl;
        connect();
    }
}

void WebsocketClient::handleClose(const struct mg_connection *conn)
{
    m_isConnected.store(false);
}

void WebsocketClient::parseRemoteAddress()
{
    const string remoteVstAddress = GET_CONFIG().remote_vst_address;
    string ipOrHost = EMPTY_STRING;
    string path = EMPTY_STRING;
    string protocol = EMPTY_STRING;
    int port = 80;
    extractUrlInfo(remoteVstAddress, protocol, ipOrHost, port, path);
    LOG(info) << "\tProtocol: " << protocol << endl;
    LOG(info) << "\tIP or host: " << ipOrHost << endl;
    LOG(info) << "\tPort: " << port << endl;
    LOG(info) << "\tPath: " << path << endl;
    {
        std::lock_guard<std::mutex> lock(m_connectionMutex);
        m_ip = ipOrHost;
        m_port = port;
        m_useSSL = protocol == HTTPS_PROTOCOL ? 1 : 0;
        m_path = path + WEBSOCKET_SERVER_PATH + GET_DB_INSTANCE()->getLocalDeviceId();
        LOG(info) << "\tAPI Path: " << m_path << endl;
        LOG(info) << "\tUse SSL: " << m_useSSL << endl;
    }
}

void WebsocketClient::checkPendingRequests()
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
        LOG(info) << "Removing stale request with ID: " << requestId << std::endl;
        m_callerList.erase(requestId);
    }
}

bool WebsocketClient::connect()
{
    bool isConnected = false;
    {
        std::lock_guard<std::mutex> lock(m_connectionMutex);
        if (m_isConnected.load())
        {
            LOG(info) << "WS already connected" << endl;
            return true;
        }

        // Create new WebSocket connection
        try
        {
            m_connection = mg_connect_websocket_client(
                m_ip.c_str(),
                m_port,
                m_useSSL,
                m_ebuf.data(),
                m_ebuf.size(),
                m_path.c_str(),
                nullptr,
                wSDataHandler,
                wSCloseHandler,
                this);
        }
        catch (const std::exception &e)
        {
            // Handle the exception
            LOG(error) << "Websocket client connection failed " << e.what() << std::endl;
            return false;
        }

        // update connection status and print error if any
        isConnected = m_connection != nullptr ? true : false;
        m_isConnected.store(isConnected);
        LOG(info) << "WS connection status: " << isConnected << std::endl;
    }
    
    // print error message
    if (!isConnected)
    {
        std::string str(m_ebuf.begin(), m_ebuf.end());
        LOG(error) << str << std::endl;
    }
    else
    {
        // sync remote devices
        std::lock_guard<std::mutex> devicesLock(m_listenerMutex);
        for (auto listener: m_listeners)
        {
            if (listener != nullptr)
            {
                listener->onConnection();
            }
        }
    }

    return isConnected;
}

bool WebsocketClient::sendMsg(const std::string &msg, std::shared_ptr<MessageObject> wsResponseInfo)
{
    {
        std::lock_guard<std::mutex> lock(m_callerListMutex);
        m_callerList[wsResponseInfo->m_responseId] = wsResponseInfo;
    }
    {
        std::lock_guard<std::mutex> lock(m_connectionMutex);
        if (!m_connection)
        {
            return false;
        }

        int ret = mg_websocket_client_write(m_connection, MG_WEBSOCKET_OPCODE_TEXT, msg.c_str(), msg.length());
        return ret == (int)msg.length();
    }
    return true;
}

bool WebsocketClient::sendMsg(const std::string &msg)
{
    {
        std::lock_guard<std::mutex> lock(m_connectionMutex);
        if (!m_connection)
        {
            return false;
        }

        int ret = mg_websocket_client_write(m_connection, MG_WEBSOCKET_OPCODE_TEXT, msg.c_str(), msg.length());
        return ret == (int)msg.length();
    }
    return true;
}

bool WebsocketClient::isConnected()
{
    return m_isConnected.load();
}

void WebsocketClient::fillResponseAndNotify(Json::Value &response, string requestId)
{
    std::lock_guard<std::mutex> lock(m_callerListMutex);
    auto itr = m_callerList.find(requestId);
    if (itr != m_callerList.end())
    {
        LOG(verbose) << "Found a pending caller" << endl;
        auto obj = itr->second;
        LOG(info) << "response: " << response << endl;
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

void WebsocketClient::addRequestHandler(std::map<std::string, httpFunction, std::less<>> &func)
{
    // register handlers
    for (auto it : func)
    {
        std::lock_guard<std::mutex> callbackLock(m_callbackMapMutex);
        m_callbackMap.insert({it.first, it.second});
    }
}

void WebsocketClient::registerListener(IWebsocketNotification *listener)
{
    std::lock_guard<std::mutex> listenerLock(m_listenerMutex);
    m_listeners.insert(listener);
}

void WebsocketClient::deRegisterListener(IWebsocketNotification *listener)
{
    std::lock_guard<std::mutex> listenerLock(m_listenerMutex);
    m_listeners.erase(listener);
}

