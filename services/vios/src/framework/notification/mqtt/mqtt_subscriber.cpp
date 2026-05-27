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

#include "mqtt_subscriber.h"
#include "utils.h"
#include "logger.h"
#include "config.h"
#include "ds_proto_parser.h"
#include <mqtt/connect_options.h>
#include <mqtt/exception.h>

using namespace std;
#define MQTT_WAIT_TIMEOUT (10 * 1000)   // 10 seconds

MqttSubscriber* MqttSubscriber::_instance = nullptr;
std::mutex MqttSubscriber::m_instanceMutex;

MqttSubscriber* MqttSubscriber::getInstance()
{
    std::lock_guard<std::mutex> lock(m_instanceMutex);
    if (_instance == nullptr)
    {
        _instance = new MqttSubscriber();
    }
    return _instance;
}

void MqttSubscriber::deleteInstance()
{
    std::lock_guard<std::mutex> lock(m_instanceMutex);
    if (_instance != nullptr)
    {
        delete _instance;
        _instance = nullptr;
    }
}

MqttSubscriber::MqttSubscriber()
{
    clientInit();
}

MqttSubscriber::~MqttSubscriber()
{
    try {
        LOG(info) << "Destroying MQTT subscriber" << endl;
        disconnect();
    } catch (const std::exception& e) {
        try { LOG(error) << "Exception in ~MqttSubscriber: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
    } catch (...) {
        try { LOG(error) << "Unknown exception in ~MqttSubscriber" << endl; } catch (...) { (void)std::current_exception(); }
    }
}

void MqttSubscriber::clientInit()
{
    nv_vms::DeviceConfig config =  GET_CONFIG();
    string brokerAddress = config.mqtt_broker_address;
    if (brokerAddress.empty())
    {
        LOG(error) << "ERROR- MQTT broker address:port not specified" << endl;
        return;
    }
    std::string clientId = "vst_" + generate_uuid();

    try
    {
        m_client = std::make_unique<mqtt::async_client>(brokerAddress, clientId);
        m_callback = std::make_unique<MqttCallback>(this);
        m_client->set_callback(*m_callback);
    }
    catch (const std::exception& e)
    {
        LOG(error) << "ERROR- MQTT client creation failed: " << e.what() << endl;
        return;
    }

    mqtt::connect_options connOpts;
    connOpts.set_keep_alive_interval(20);
    connOpts.set_clean_session(true);
    connOpts.set_automatic_reconnect(true);

    // Connect and wait for completion
    try
    {
        mqtt::token_ptr conntok = m_client->connect(connOpts);
        long int timeout = MQTT_WAIT_TIMEOUT;
        bool connected = conntok->wait_for(timeout);
        if (!connected)
        {
            LOG(error) << "MQTT connection failed" << endl;
            return;
        }
    }
    catch (const std::exception& e)
    {
        LOG(error) << "ERROR- MQTT connection failed: " << e.what() << endl;
        return;
    }
    LOG(info) << "MQTT connection successful" << endl;

    // Subscribe to the topic
    m_topic = config.message_broker_topic_consumer;
    m_qos = 1;
    if (m_topic.empty())
    {
        LOG(error) << "ERROR- MQTT subscribe topic is empty; will not subscribe" << endl;
        return;
    }
    LOG(info) << "Subscribing to topic: " << m_topic << " with QoS: " << m_qos << endl;

    try
    {
        mqtt::token_ptr subtok = m_client->subscribe(m_topic, m_qos);
        long int timeout = MQTT_WAIT_TIMEOUT;
        bool subscribed = subtok->wait_for(timeout);
        if (!subscribed)
        {
            LOG(error) << "MQTT subscription failed" << endl;
            return;
        }
    }
    catch (const std::exception& e)
    {
        LOG(error) << "ERROR- MQTT subscription failed: " << e.what() << endl;
        return;
    }
    LOG(info) << "Subscription successful" << endl;
}

void MqttSubscriber::handleConnected(const std::string& cause)
{
    LOG(info) << "Connection established" << endl;
    if (!cause.empty())
    {
        LOG(info) << "Connection cause: " << cause << endl;
    }
}

void MqttSubscriber::handleConnectionLost(const std::string& cause)
{
    LOG(info) << "Connection lost!" << endl;
    if (!cause.empty())
    {
        LOG(info) << "Cause: " << cause << endl;
    }
    LOG(info) << "Attempting to reconnect..." << endl;
}

void MqttSubscriber::handleMessageArrived(mqtt::const_message_ptr msg)
{
    if (!m_listeners.size())
    {
        return;
    }

    string str_payload;
    try
    {
        str_payload = msg->to_string();
    }
    catch (const std::exception& e)
    {
        LOG(error) << "ERROR- Failed to convert message to string: " << e.what() << endl;
        return;
    }

    int64_t frameTimeMs = 0;
    Json::Value payload = DsProtoParser::getInstance()->parseMessage(str_payload.c_str(), str_payload.length(), frameTimeMs);
    if (payload == Json::nullValue)
    {
        static std::atomic<uint64_t> logError{0};
        if (logError < 10)
        {
            LOG(error) << "Unable to parse " << logError << " messages" << endl;
            logError++;
        }
        return;
    }

    std::lock_guard<std::mutex> lock(m_listenerMutex);
    for (auto listener: m_listeners)
    {
        if (listener == nullptr)
        {
            continue;
        }
        if (payload.isArray())
        {
            for (Json::ArrayIndex i = 0; i < payload.size(); i++)
            {
                listener->onMessage(payload[i]);
            }
        }
        else
        {
            listener->onMessage(payload);
        }
    }
}

void MqttSubscriber::handleDeliveryComplete(mqtt::delivery_token_ptr token)
{
    try
    {
        LOG(info) << "Delivery complete for token: " << token->get_message_id() << endl;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "ERROR- Failed to get message ID from token: " << e.what() << endl;
    }
}

void MqttSubscriber::registerMessageListener(nv_vms::INotificationListener* listener)
{
    LOG(info) << "Registering message listener" << endl;
    std::lock_guard<std::mutex> lock(m_listenerMutex);
    m_listeners.insert(listener);
}

void MqttSubscriber::deregisterMessageListener(nv_vms::INotificationListener* listener)
{
    LOG(info) << "Deregistering message listener" << endl;
    std::lock_guard<std::mutex> lock(m_listenerMutex);
    m_listeners.erase(listener);
}


MqttSubscriber::MqttCallback::MqttCallback(MqttSubscriber* parentPtr)
    : m_parent(parentPtr)
{
}

void MqttSubscriber::MqttCallback::connected(const std::string& cause)
{
    if (m_parent)
    {
        m_parent->handleConnected(cause);
    }
}

void MqttSubscriber::MqttCallback::connection_lost(const std::string& cause)
{
    if (m_parent)
    {
        m_parent->handleConnectionLost(cause);
    }
}

void MqttSubscriber::MqttCallback::message_arrived(mqtt::const_message_ptr msg)
{
    if (m_parent)
    {
        m_parent->handleMessageArrived(msg);
    }
}

void MqttSubscriber::MqttCallback::delivery_complete(mqtt::delivery_token_ptr token)
{
    if (m_parent)
    {
        m_parent->handleDeliveryComplete(token);
    }
}

void MqttSubscriber::disconnect()
{
    try
    {
        if (m_client && m_client->is_connected())
        {
            LOG(info) << "Disconnecting from MQTT broker..." << endl;
            if (!m_topic.empty())
            {
                m_client->unsubscribe(m_topic)->wait();
            }
            m_client->disconnect()->wait();
            LOG(info) << "Disconnected from MQTT broker" << endl;
        }
        else
        {
            LOG(info) << "MQTT client not connected, skipping disconnect" << endl;
        }
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Error during disconnect: " << e.what() << endl;
    }
}