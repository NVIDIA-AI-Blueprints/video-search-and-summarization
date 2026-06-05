/*
 * SPDX-FileCopyrightText: Copyright (c) 2023-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "nvds_msgapi.h"
#include <string>
#include <mutex>
#include <memory>
#include "notification/notification_manager.h"

typedef NvDsMsgApiHandle (*msgapi_connect_ptr) (char*, nvds_msgapi_connect_cb_t, char*);
typedef NvDsMsgApiErrorType (*msgapi_subscribe_ptr) (NvDsMsgApiHandle, char **, int, nvds_msgapi_subscribe_request_cb_t, void*);
typedef NvDsMsgApiErrorType (*msgapi_disconnect_ptr) (NvDsMsgApiHandle);

class RedisSubscriber : public nv_vms::INotificationInterface
{
public:
    static RedisSubscriber* getInstance();
    static void deleteInstance();
    bool isError()  { return m_error; }
    void registerMessageListener(nv_vms::INotificationListener* listener) override;
    void deregisterMessageListener(nv_vms::INotificationListener* listener) override;
    void deliverMessage(void *msg, int len);

    bool deliverMessage (Json::Value& message) override { return true; }
    void retryConnection () override {}

private:
    static RedisSubscriber*                         _instance;
    msgapi_connect_ptr                              nvds_msgapi_connect;
    msgapi_subscribe_ptr                            nvds_msgapi_subscribe;
    msgapi_disconnect_ptr                           nvds_msgapi_disconnect;
    NvDsMsgApiHandle                                m_connHandle;
    void*                                           m_handleRedis;
    void*                                           m_handleRedisProto;
    std::string                                     m_redisEndpoint;
    std::string                                     m_subscribeTopic;
    std::string                                     m_sensorMngtTopic;
    bool                                            m_error;

    void redisInit();
    RedisSubscriber();
    virtual ~RedisSubscriber();
};