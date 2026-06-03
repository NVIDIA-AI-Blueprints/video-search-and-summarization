/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <jsoncpp/json/json.h>
#include <queue>
#include <mutex>
#include <condition_variable>
#include <thread>
#include <atomic>
#include <set>

namespace nv_vms
{
    class INotificationListener
    {
    public:
        virtual ~INotificationListener() = default;
        virtual void onMessage(const Json::Value payload) = 0;
    };

    class INotificationInterface
    {
    public:
        INotificationInterface();
        void processMessageQueue();
        void sendMessage (Json::Value& message);
        
        /* 
        * Deriving class destructor should call stopMessageProcessing() to eliminate
        * the possibility of deliverMessage to be called when derived class is
        * already destroyed.
        */
        void stopMessageProcessing();

        virtual bool deliverMessage (Json::Value& message) = 0;
        virtual void retryConnection () = 0;

        // Notification Consumer related methods
        virtual void registerMessageListener(INotificationListener* listener) {}
        virtual void deregisterMessageListener(INotificationListener* listener) {}

        bool                            m_libError = false;
        std::queue<Json::Value>         m_messages;
        std::mutex                      m_msgQueueMutex;
        std::condition_variable         m_msgQueueCv;
        std::mutex                      m_retryConnectionMutex;
        std::condition_variable         m_retryConnectionCv;
        std::thread                     m_retryThread;
        std::atomic<bool>               m_exitRetryThread{false};
        std::atomic<bool>               m_connected{false};
        bool                            m_fatalError = false;

        // Notification Consumer related variables
        std::mutex                      m_listenerMutex;
        std::set<INotificationListener*> m_listeners;
    };
} // nv_vms
