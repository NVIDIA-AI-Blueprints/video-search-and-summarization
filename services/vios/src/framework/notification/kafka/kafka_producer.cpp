/*
 * SPDX-FileCopyrightText: Copyright (c) 2022-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <iostream>
#include "kafka_producer.h"
#include "config.h"
#include "logger.h"
#include <dlfcn.h>

#define ABSOLUTE_LIBRARY_PATH_X86_64 "/usr/lib/x86_64-linux-gnu/librdkafka.so"

using namespace std;

#if !defined(AARCH64_PLATFORM) && !defined(JETSON_PLATFORM)
static void
DeliveryCallback(rd_kafka_t *rk, const rd_kafka_message_t *rkmessage, void *opaque)
{
    if (rkmessage->err)
    {
        LOG(error) << "Message delivery failed: " << endl;
    }
    else
    {
        LOG(info) << "Message delivered (" << rkmessage->len <<
                    " bytes) partition " << rkmessage->partition << endl;
    }
}
#endif

NvKafka* NvKafka::_instance = nullptr;

NvKafka* NvKafka::getInstance()
{
    if (_instance == nullptr)
    {
        _instance = new NvKafka();
    }
    return _instance;
}

void NvKafka::deleteInstance()
{
    if (_instance != nullptr)
    {
        delete _instance;
        _instance = nullptr;
    }
}

NvKafka::NvKafka()
        : m_error(false)
#if !defined(AARCH64_PLATFORM) && !defined(JETSON_PLATFORM)
        , m_kafkaHandle(nullptr)
        , rd_kafka_conf_new(nullptr)
        , rd_kafka_conf_set(nullptr)
        , rd_kafka_conf_set_dr_msg_cb(nullptr)
        , rd_kafka_new(nullptr)
        , rd_kafka_producev(nullptr)
        , rd_kafka_poll(nullptr)
        , rd_kafka_err2str(nullptr)
        , m_producer(nullptr)
#endif
{
#if !defined(AARCH64_PLATFORM) && !defined(JETSON_PLATFORM)
    m_kafkaHandle = dlopen(ABSOLUTE_LIBRARY_PATH_X86_64, RTLD_LAZY);
    if (!m_kafkaHandle)
    {
        LOG(error) << "Cannot open librdkafka library: " << dlerror() << endl;
        m_error = true;
        m_libError = true;
    }
    else
    {
        dlerror();
        rd_kafka_conf_new = (rd_kafka_conf_new_t) dlsym(m_kafkaHandle, "rd_kafka_conf_new");
        if (rd_kafka_conf_new == nullptr)
        {
            goto error;
        }

        rd_kafka_conf_set = (rd_kafka_conf_set_t) dlsym(m_kafkaHandle, "rd_kafka_conf_set");
        if (rd_kafka_conf_set == nullptr)
        {
            goto error;
        }

        rd_kafka_conf_set_dr_msg_cb = (rd_kafka_conf_set_dr_msg_cb_t) dlsym(m_kafkaHandle, "rd_kafka_conf_set_dr_msg_cb");
        if (rd_kafka_conf_set_dr_msg_cb == nullptr)
        {
            goto error;
        }

        rd_kafka_new = (rd_kafka_new_t) dlsym(m_kafkaHandle, "rd_kafka_new");
        if (rd_kafka_new == nullptr)
        {
            goto error;
        }

        rd_kafka_producev = (rd_kafka_producev_t) dlsym(m_kafkaHandle, "rd_kafka_producev");
        if (rd_kafka_producev == nullptr)
        {
            goto error;
        }

        rd_kafka_poll = (rd_kafka_poll_t) dlsym(m_kafkaHandle, "rd_kafka_poll");
        if (rd_kafka_poll == nullptr)
        {
            goto error;
        }

        rd_kafka_err2str = (rd_kafka_err2str_t) dlsym(m_kafkaHandle, "rd_kafka_err2str");
        if (rd_kafka_err2str == nullptr)
        {
            goto error;
        }

        /* kafka server initialization */
        kafka_init();
        return;
error:
        const char *dlsym_error = dlerror();
        if (dlsym_error)
        {
            LOG(error) << "Error while loading symbol 'rd_kafka_': " << dlsym_error << endl;
            m_error = true;
            m_libError = true;
            if (m_kafkaHandle)
            {
                dlclose(m_kafkaHandle);
            }
        }
    }
#else
    LOG(error) << "Kafka not integrated for Jetson device" << endl;
#endif
    return;
}

NvKafka::~NvKafka()
{
    LOG(info) << " ::~NvKafka" << endl;
    stopMessageProcessing();
#if !defined(AARCH64_PLATFORM) && !defined(JETSON_PLATFORM)
    if (m_kafkaHandle)
    {
        dlclose(m_kafkaHandle);
    }
#endif
}

void NvKafka::kafka_init()
{
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    LOG(error) << "Kafka not integrated for Jetson device" << endl;
#else
    rd_kafka_conf_t *conf; /* Temporary configuration object */
    char errstr[512];      /* librdkafka API error reporting buffer */

    nv_vms::DeviceConfig config =  GET_CONFIG();
    m_topic_vms_event = config.message_broker_topic;
    m_kafkaServerEndpoint = getKafkaServerEndpoint();
    LOG(info) << "Kafka server address:port: " << m_kafkaServerEndpoint << endl;
    if (m_kafkaServerEndpoint.empty())
    {
        LOG(error) << "ERROR- Kafka server address:port not specified" << endl;
        m_fatalError = m_error = true;
        return;
    }

    /*
    * Create Kafka client configuration place-holder
    */
    conf = rd_kafka_conf_new();
    if (rd_kafka_conf_set(conf, "bootstrap.servers", m_kafkaServerEndpoint.c_str(),
                            errstr, sizeof(errstr)) != RD_KAFKA_CONF_OK)
    {
        LOG(error) << errstr << endl;
        goto error;
    }

    /* Set the delivery report callback.
    * The callback is only triggered from rd_kafka_poll() and
    * rd_kafka_flush(). */
    rd_kafka_conf_set_dr_msg_cb(conf, DeliveryCallback);

    /*
    * Create producer instance.
    */
    m_producer = rd_kafka_new(RD_KAFKA_PRODUCER, conf, errstr, sizeof(errstr));
    if (!m_producer)
    {
        LOG(error) << "Failed to create producer: " << errstr << endl;
        m_connected = false;
        goto error;
    }
    m_connected = true;
    m_error = false;
    return;

error:
    m_error = true;
#endif
}

bool NvKafka::deliverMessage(Json::Value& message)
{
    // Using lock so that m_message is not parallelly modified
    std::lock_guard<std::mutex> lock(m_messageLock);
    m_message = jsonToString(message);
    return sendToKafka(m_message);
}

bool NvKafka::sendToKafka(std::string& payload)
{
    bool ret = true;
    if(m_error)
    {
        LOG(error) << "Cann't send message in error condition" << endl;
        return false;
    }

#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    LOG(error) << "Kafka not integrated for Jetson device" << endl;
#else
    rd_kafka_resp_err_t err;
    string key = GET_CONFIG().message_broker_payload_key;

    /*
    * Send message. Asynchronous call.
    */
    err = rd_kafka_producev(
        /* Producer handle */
        m_producer,
        /* Topic name */
        RD_KAFKA_V_TOPIC(m_topic_vms_event.c_str()),
        /* Make a copy of the payload. */
        RD_KAFKA_V_MSGFLAGS(RD_KAFKA_MSG_F_COPY),
        /* Message value and length */
        RD_KAFKA_V_VALUE((void*)payload.c_str(), payload.length()),
        RD_KAFKA_V_KEY((void*)key.c_str(), key.length()),
        /* Per-Message opaque, provided in
        * delivery report callback as
        * msg_opaque. */
        RD_KAFKA_V_OPAQUE(NULL),
        /* End sentinel */
        RD_KAFKA_V_END);

    if (err)
    {
        /*
        * Failed to *enqueue* message for producing.
        */
        LOG(error) << "Failed to produce to topic " << m_topic_vms_event << ": "
            << rd_kafka_err2str(err) << endl;
        ret = false;
    }
    else
    {
        LOG(info) << "Enqueued message (" << payload.size() << " bytes) "
            << "for topic " << m_topic_vms_event << endl;
        LOG(warning) << "return: " << rd_kafka_err2str(err) << endl;
    }

    // Poll producer for delivery reports
    rd_kafka_poll(m_producer, 0 /*non-blocking*/);
#endif
    return ret;
}

void NvKafka::retryConnection()
{
    m_connected = true;
}
