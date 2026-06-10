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

#include "error_code.h"
#include "MessageObject.h"
#include <Scheduler.h>
#include "logger.h"
#include "webrtcDataChannel.h"
#include "datachannellistenerinterface.h"
#include "utils.h"


using namespace std;
using namespace nv_vms;
#define MESSAGE_BUS_CLEANUP_INTERVAL 20000
#define MESSAGE_BUS_REQUEST_TIMEOUT 10

class MessageBus : public IDataChannelListener
{
public:
    MessageBus()
    {
        LOG(verbose) << __PRETTY_FUNCTION__ << endl;
        GET_DATA_CHANNEL()->registerListener(this);
        auto messageBusCleanupInterval = std::chrono::milliseconds(MESSAGE_BUS_CLEANUP_INTERVAL);
        m_watchdog = make_unique<Bosma::Scheduler>(1);
        m_watchdog->interval(messageBusCleanupInterval, [=]()
                            { messageBusCleanupTask(); });
    }
    ~MessageBus()
    {
        LOG(info) << __PRETTY_FUNCTION__ << endl;
        m_watchdog.reset();
        GET_DATA_CHANNEL()->deRegisterListener(this);
    }

    // IDataChannelListener Interface
    void onMessage(Json::Value& message);
    bool sendMessage(const string& clientId, const string& message, std::shared_ptr<MessageObject> messageObj);
    bool sendMessage(const string& clientId, const string& message);

    typedef std::function<void(const Json::Value& receivedData, Json::Value& response)> dataChannelFunction;
    void addRequestHandler(std::map<std::string, dataChannelFunction, std::less<>> &func);

private:
    void fillResponseAndNotify(Json::Value& response, string requestId);
    void messageBusCleanupTask();
    /**
     * Multiple threads share the same thread_local variable, but changes to the variable are isolated
     * to each thread. In case of receiving fragmented message, websocket opcode and accumulated data is stored.
     */
    std::map<string, std::shared_ptr<MessageObject>>                    m_callerList;
    std::mutex                                                          m_callerListMutex;
    // Callback map and its mutex
    std::map<std::string, dataChannelFunction>                          m_callbackMap;
    std::mutex                                                          m_callbackMapMutex;
    std::unique_ptr<Bosma::Scheduler>                                   m_watchdog;
};
