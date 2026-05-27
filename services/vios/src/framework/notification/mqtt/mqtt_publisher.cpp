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

#include "mqtt_publisher.h"
#include "config.h"
#include "logger.h"
#include "utils.h"
#include <mqtt/connect_options.h>
#include <mqtt/exception.h>

using namespace std;
#define MQTT_WAIT_TIMEOUT (10 * 1000)   // 10 seconds

MqttPublisher* MqttPublisher::getInstance()
{
    static MqttPublisher instance;
    return &instance;
}

MqttPublisher::MqttPublisher()
{
    clientInit();
}

MqttPublisher::~MqttPublisher()
{
    try {
        LOG(info) << "Destroying MQTT publisher" << endl;
        stopMessageProcessing();
        disconnect();
    } catch (const std::exception& e) {
        try { LOG(error) << "Exception in ~MqttPublisher: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
    } catch (...) {
        try { LOG(error) << "Unknown exception in ~MqttPublisher" << endl; } catch (...) { (void)std::current_exception(); }
    }
}

void MqttPublisher::clientInit()
{
    nv_vms::DeviceConfig config =  GET_CONFIG();
    string brokerAddress = config.mqtt_broker_address;
    if (brokerAddress.empty())
    {
        LOG(error) << "ERROR- MQTT broker address:port not specified" << endl;
        m_error = true;
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
        m_error = true;
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
            m_error = true;
            return;
        }
    }
    catch (const std::exception& e)
    {
        LOG(error) << "ERROR- MQTT connection failed: " << e.what() << endl;
        m_error = true;
        return;
    }

    m_connected = true;
    LOG(info) << "MQTT connection successful" << endl;

    m_topic = config.message_broker_topic;
    m_qos = 1;
    if (m_topic.empty())
    {
        LOG(error) << "ERROR- MQTT publish topic is empty; will not publish" << endl;
        m_error = true;
        return;
    }
    LOG(info) << "Publishing to topic: " << m_topic << " with QoS: " << m_qos << endl;
}

bool MqttPublisher::deliverMessage(Json::Value& message)
{
    // Using lock so that m_message is not parallelly modified
    string payload = jsonToString(message);
    return sendToMqtt(payload);
}

bool MqttPublisher::sendToMqtt(std::string payload)
{
    std::lock_guard<std::mutex> lock(m_processingMutex);
    bool ret_val = true;
    if(m_error)
    {
        LOG(error) << "Can't send message in error condition" << endl;
        return false;
    }
    if(!m_client || !m_client->is_connected())
    {
        LOG(error) << "MQTT client not connected to broker" << endl;
        return false;
    }

    // Create message
    mqtt::message_ptr pubmsg = mqtt::make_message(m_topic, payload);
    pubmsg->set_qos(m_qos);
    pubmsg->set_retained(true);

    // Publish and wait for completion
    if (!m_client)
    {
        LOG(error) << "MQTT client not initialized" << endl;
        return false;
    }
    try
    {
        mqtt::token_ptr pubtok = m_client->publish(pubmsg);
        long int timeout = MQTT_WAIT_TIMEOUT;
        bool published = pubtok->wait_for(timeout);
        if (!published)
        {
            LOG(error) << "Failed to publish message" << endl;
            return false;
        }
        LOG(info) << "Message published" << endl;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Failed to publish message: " << e.what() << endl;
        ret_val = false;
    }

    return ret_val;
}

void MqttPublisher::handleConnected(const std::string& cause)
{
    LOG(info) << "Connection established" << endl;
    if (!cause.empty())
    {
        LOG(info) << "Connection cause: " << cause << endl;
    }
}

void MqttPublisher::handleConnectionLost(const std::string& cause)
{
    LOG(info) << "Connection lost!" << endl;
    if (!cause.empty())
    {
        LOG(info) << "Cause: " << cause << endl;
    }
    LOG(info) << "Attempting to reconnect..." << endl;
}

void MqttPublisher::handleMessageArrived(mqtt::const_message_ptr msg)
{
}

void MqttPublisher::handleDeliveryComplete(mqtt::delivery_token_ptr token)
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

MqttPublisher::MqttCallback::MqttCallback(MqttPublisher* parentPtr)
    : m_parent(parentPtr)
{
}

void MqttPublisher::MqttCallback::connected(const std::string& cause)
{
    if (m_parent)
    {
        m_parent->handleConnected(cause);
    }
}

void MqttPublisher::MqttCallback::connection_lost(const std::string& cause)
{
    if (m_parent)
    {
        m_parent->handleConnectionLost(cause);
    }
}

void MqttPublisher::MqttCallback::message_arrived(mqtt::const_message_ptr msg)
{
    if (m_parent)
    {
        m_parent->handleMessageArrived(msg);
    }
}

void MqttPublisher::MqttCallback::delivery_complete(mqtt::delivery_token_ptr token)
{
    if (m_parent)
    {
        m_parent->handleDeliveryComplete(token);
    }
}

void MqttPublisher::disconnect()
{
    std::lock_guard<std::mutex> lock(m_processingMutex);
    try
    {
        if (m_client && m_client->is_connected())
        {
            LOG(info) << "Disconnecting from MQTT broker..." << endl;
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