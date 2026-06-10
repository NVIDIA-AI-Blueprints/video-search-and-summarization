/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "notification/notification_manager.h"
#include <mqtt/async_client.h>
#include <mqtt/callback.h>
#include <mqtt/message.h>
#include <memory>
#include <string>

 class MqttSubscriber : public nv_vms::INotificationInterface
 {
public:
    static MqttSubscriber* getInstance();
    static void deleteInstance();
    void registerMessageListener(nv_vms::INotificationListener* listener) override;
    void deregisterMessageListener(nv_vms::INotificationListener* listener) override;
    void retryConnection() override {}
    bool deliverMessage(Json::Value& message) override { return true; }

private:
    MqttSubscriber();
    virtual ~MqttSubscriber();
    void clientInit();

    class MqttCallback : public virtual mqtt::callback
    {
    private:
        MqttSubscriber* m_parent;

    public:
        explicit MqttCallback(MqttSubscriber* parentPtr);

        void connected(const std::string& cause) override;
        void connection_lost(const std::string& cause) override;
        void message_arrived(mqtt::const_message_ptr msg) override;
        void delivery_complete(mqtt::delivery_token_ptr token) override;
    };

    static MqttSubscriber*              _instance;
    static std::mutex                   m_instanceMutex;
    std::unique_ptr<mqtt::async_client> m_client = nullptr;
    std::unique_ptr<MqttCallback>       m_callback = nullptr;
    std::string                         m_topic = "";
    int                                 m_qos = 1;

private:
    void handleConnected(const std::string& cause);
    void handleConnectionLost(const std::string& cause);
    void handleMessageArrived(mqtt::const_message_ptr msg);
    void handleDeliveryComplete(mqtt::delivery_token_ptr token);
    void disconnect();
};