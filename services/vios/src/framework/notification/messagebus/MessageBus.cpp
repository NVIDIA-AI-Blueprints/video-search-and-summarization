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

#include <MessageBus.h>

#define SET_VMS_ERROR(err_code, value) { std::pair<string, string> err = getCameraErrorCodeString(err_code); \
                                        value["error_code"] = err.first; \
                                        value["error_message"] = err.second; }

#define SET_VMS_ERROR2(err_code, value, message) { std::pair<string, string> err = getCameraErrorCodeString(err_code); \
                                        value["error_code"] = err.first; \
                                        string msg(message); \
                                        if (msg.empty()) {value["error_message"] = err.second;} else {value["error_message"] = msg;} }

bool MessageBus::sendMessage(const string& clientId, const string& message, std::shared_ptr<MessageObject> messageObj)
{
    {
        std::lock_guard<std::mutex> lock(m_callerListMutex);
        m_callerList[messageObj->m_responseId] = messageObj;
    }
    return GET_DATA_CHANNEL()->sendMessageOnDataChannel(clientId, message);
}

bool MessageBus::sendMessage(const string& clientId, const string& message)
{
    return GET_DATA_CHANNEL()->sendMessageOnDataChannel(clientId, message);
}

void MessageBus::onMessage(Json::Value& jMessage)
{
    LOG(verbose2) << jMessage.toStyledString() << endl;
    if (jMessage == Json::nullValue)
    {
        LOG(error) << "Invalid JSON received" << endl;
        return;
    }
    // get the websocket API key
    std::string webSocketApi = jMessage.get("apiKey", EMPTY_STRING).asString();
    std::string clientId = jMessage.get("clientId", EMPTY_STRING).asString();
    Json::Value response;
    if (webSocketApi == EMPTY_STRING)
    {
        // If apiKey is not present, it could be a response to earlier request. In that case
        // fill the response and notify. VST edge to cloud use-case.
        LOG(verbose2) << "apiKey not found, checking if requestId is present" << endl;
        std::string requestId = jMessage.get("requestId", EMPTY_STRING).asString();
        if (!requestId.empty())
        {
            LOG(verbose2) << "requestId found: " << requestId << " filling up response and notify" << endl;
            Json::Value data = jMessage.get("data", Json::nullValue);
            LOG(verbose2) << "data: " << data << endl;
            fillResponseAndNotify(data, requestId);
            return;
        }
        // Invalid JSON
        LOG(error) << "Invalid JSON data received" << endl;
        return;
    }
    // check if API is registered in VST
    else if (m_callbackMap.find(webSocketApi) == m_callbackMap.end())
    {
        SET_VMS_ERROR(VmsErrorCode::MethodNotAllowedError, response)
        sendMessage(clientId, jsonToString(response));
        return;
    }
    // execute the API
    m_callbackMap[webSocketApi](jMessage, response);
    // send back the response
    sendMessage(clientId, jsonToString(response));
    return;
}

void MessageBus::fillResponseAndNotify(Json::Value &response, string requestId)
{
    std::lock_guard<std::mutex> lock(m_callerListMutex);
    auto itr = m_callerList.find(requestId);
    if (itr != m_callerList.end())
    {
        LOG(verbose) << "Found a pending caller" << endl;
        auto obj = itr->second;
        LOG(verbose2) << "response: " << response << endl;
        obj->m_response = response;
        LOG(verbose) << "Signaling the caller" << endl;
        obj->m_isResponseReceived = true;
        obj->m_sync.signal();
        LOG(verbose) << "Signaled" << endl;
        // safe to erase if key not present
        m_callerList.erase(requestId);
        LOG(verbose2) << "Notified and erased caller, request ID: " << requestId << endl;
    }
    else
    {
        LOG(error) << "Failed to notify, no pending request found" << endl;
    }
}

void MessageBus::addRequestHandler(std::map<std::string, dataChannelFunction, std::less<>> &func)
{
    // register handlers
    for (auto it : func)
    {
        std::lock_guard<std::mutex> callbackLock(m_callbackMapMutex);
        m_callbackMap.insert({it.first, it.second});
    }
}

void MessageBus::messageBusCleanupTask()
{
    std::lock_guard<std::mutex> lock(m_callerListMutex);
    std::vector<std::string> requestsToRemove;
    auto currentTime = std::chrono::system_clock::now();

    for (auto it = m_callerList.begin(); it != m_callerList.end(); ++it)
    {
        auto &obj = it->second;
        auto timeDifference = currentTime - obj->m_timestamp;
        double seconds = std::chrono::duration<double>(timeDifference).count();

        if (seconds > MESSAGE_BUS_REQUEST_TIMEOUT)
        {
            requestsToRemove.push_back(it->first);
        }
    }

    for (const auto &requestId : requestsToRemove)
    {
        LOG(info) << "Removing stale message bus requests: " << requestId << std::endl;
        m_callerList.erase(requestId);
    }
}
