/*
 * SPDX-FileCopyrightText: Copyright (c) 2022-2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "notification_manager.h"
#include <string>
#include <mutex>
#if !defined(AARCH64_PLATFORM) && !defined(JETSON_PLATFORM)
#include <librdkafka/rdkafka.h>
#endif

using namespace nv_vms;

#if !defined(AARCH64_PLATFORM) && !defined(JETSON_PLATFORM)
typedef rd_kafka_conf_t* (*rd_kafka_conf_new_t) (void);
typedef rd_kafka_conf_res_t (*rd_kafka_conf_set_t) (rd_kafka_conf_t*, const char*, const char*, char*, size_t);
typedef void (*dr_msg_cb_t) (rd_kafka_t*, const rd_kafka_message_t*, void*);
typedef void (*rd_kafka_conf_set_dr_msg_cb_t) (rd_kafka_conf_t*, dr_msg_cb_t);
typedef rd_kafka_t* (*rd_kafka_new_t) (rd_kafka_type_t, rd_kafka_conf_t*, char*, size_t);
typedef rd_kafka_resp_err_t (*rd_kafka_producev_t) (rd_kafka_t*, ...);
typedef int (*rd_kafka_poll_t) (rd_kafka_t*, int);
typedef const char* (*rd_kafka_err2str_t) (rd_kafka_resp_err_t);
#endif

class NvKafka : public INotificationInterface
{
public:
    static NvKafka* getInstance();
    static void deleteInstance();
    bool isError()  { return m_error; }
    bool deliverMessage(Json::Value& message);
    void retryConnection();
    bool sendToKafka(std::string& payload);
    virtual ~NvKafka();

private:
    static NvKafka* _instance;
    bool m_error;
    std::string m_topic_vms_event;
    std::string m_kafkaServerEndpoint;
    std::string m_message;
    std::mutex m_messageLock;

#if !defined(AARCH64_PLATFORM) && !defined(JETSON_PLATFORM)
    void* m_kafkaHandle;
    rd_kafka_conf_new_t rd_kafka_conf_new;
    rd_kafka_conf_set_t rd_kafka_conf_set;
    rd_kafka_conf_set_dr_msg_cb_t rd_kafka_conf_set_dr_msg_cb;
    rd_kafka_new_t rd_kafka_new;
    rd_kafka_producev_t rd_kafka_producev;
    rd_kafka_poll_t rd_kafka_poll;
    rd_kafka_err2str_t rd_kafka_err2str;

    rd_kafka_t *m_producer;
#endif

    void kafka_init();
    NvKafka();
};
