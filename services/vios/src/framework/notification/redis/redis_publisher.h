/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#include "nvds_msgapi.h"
#include <string>
#include <mutex>
#include <fstream>

using namespace nv_vms;

typedef NvDsMsgApiHandle (*nvds_msgapi_connect_t) (char*, nvds_msgapi_connect_cb_t, char*);
typedef NvDsMsgApiErrorType (*nvds_msgapi_send_t) (NvDsMsgApiHandle, char*, const uint8_t*, size_t);
typedef NvDsMsgApiErrorType (*nvds_msgapi_disconnect_t) (NvDsMsgApiHandle);

class NvRedis : public INotificationInterface
{
public:
    nvds_msgapi_connect_t nvds_msgapi_connect;
    nvds_msgapi_send_t nvds_msgapi_send;
    nvds_msgapi_disconnect_t nvds_msgapi_disconnect;

    static NvRedis* getInstance();
    static void deleteInstance();
    bool isError()  { return m_error; }
    bool deliverMessage(Json::Value& message);
    void retryConnection();
    bool sendToRedis(std::string& payload);
    virtual ~NvRedis();
    void reconnectToRedisServer();

private:
    static NvRedis* _instance;
    bool m_error;
    void* m_redisHandle;
    void* m_handle_redis_proto;
    NvDsMsgApiHandle m_conn_handle;
    std::string m_topic_vms_event;
    std::string m_message;
    std::mutex m_messageLock;
    std::ofstream m_redisconfigFile;
    std::string m_redisEndpoint;

    void redis_init();
    NvRedis();
};
