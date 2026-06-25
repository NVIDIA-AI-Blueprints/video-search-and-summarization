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

#include <cstring>
#include <cstdlib>
#include <iostream>
#include <dlfcn.h>
#include "redis_publisher.h"
#include "config.h"
#include "logger.h"
#include "utils.h"

#define REDIS_CONFIG_FILE "./cfg_redis.txt"

using namespace std;

NvRedis* NvRedis::_instance = nullptr;

NvRedis* NvRedis::getInstance()
{
    if (_instance == nullptr)
    {
        _instance = new NvRedis();
    }
    return _instance;
}

void NvRedis::deleteInstance()
{
    if (_instance != nullptr)
    {
        delete _instance;
        _instance = nullptr;
    }
}

NvRedis::NvRedis()
      : nvds_msgapi_connect(nullptr)
      , nvds_msgapi_send(nullptr)
      , m_error(false)
      , m_redisHandle(nullptr)
      , m_handle_redis_proto(nullptr)
      , m_conn_handle(nullptr)
{
    const char* lib_path;
    // Temporary solution to load libnvds_logger in memory
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_ARCH64, "libnvds_logger.so");
    m_redisHandle = dlopen(lib_path, RTLD_LAZY);
    if (!m_redisHandle)
    {
        lib_path = CONCATENATE_STRINGS(RELATIVE_PREBUILT_LIBRARY_PATH_ARCH64, "libnvds_logger.so");
        m_redisHandle = dlopen(lib_path, RTLD_LAZY);
    }
#else
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_X86_64, "libnvds_logger.so");
    m_redisHandle = dlopen(lib_path, RTLD_LAZY);
#endif
    if (!m_redisHandle)
    {
        LOG(error) << "Cannot open nvds_logger library: " << dlerror() << endl;
        goto error;
    }

#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_ARCH64, "libnvds_redis_proto.so");
    m_handle_redis_proto = dlopen(lib_path, RTLD_LAZY);
    if (!m_handle_redis_proto)
    {
        lib_path = CONCATENATE_STRINGS(RELATIVE_PREBUILT_LIBRARY_PATH_ARCH64, "libnvds_redis_proto.so");
        m_handle_redis_proto = dlopen(lib_path, RTLD_LAZY);
    }
#else
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_X86_64, "libnvds_redis_proto.so");
    m_handle_redis_proto = dlopen(lib_path, RTLD_LAZY);
#endif

    if (!m_handle_redis_proto)
    {
        LOG(error) << "Cannot open nvds_redis library: " << dlerror() << endl;
        goto error;
    }
    else
    {
        dlerror();
        nvds_msgapi_connect = (nvds_msgapi_connect_t) dlsym(m_handle_redis_proto, "nvds_msgapi_connect");
        if (nvds_msgapi_connect == nullptr)
        {
            goto error;
        }

        nvds_msgapi_send = (nvds_msgapi_send_t) dlsym(m_handle_redis_proto, "nvds_msgapi_send");
        if (nvds_msgapi_send == nullptr)
        {
            goto error;
        }

        nvds_msgapi_disconnect = (nvds_msgapi_disconnect_t) dlsym(m_handle_redis_proto, "nvds_msgapi_disconnect");
        if (nvds_msgapi_disconnect == nullptr)
        {
            goto error;
        }

        /* redis server initialization */
        redis_init();
    }
    return;

error:
    const char *dlsym_error = dlerror();
    if (dlsym_error)
    {
        LOG(error) << "Cannot load symbol 'nvds_msgapi_': " << dlsym_error << endl;
        if (m_redisHandle)
        {
            dlclose(m_redisHandle);
        }
        if (m_handle_redis_proto)
        {
            dlclose(m_handle_redis_proto);
        }
        m_error = true;
        m_libError = true;
    }
}

NvRedis::~NvRedis()
{
    LOG(info) << " ::~NvRedis" << endl;
    stopMessageProcessing();
    if (m_conn_handle)
    {
        nvds_msgapi_disconnect(m_conn_handle);
    }
    if (m_redisHandle)
    {
        dlclose(m_redisHandle);
    }
    if (m_handle_redis_proto)
    {
        dlclose(m_handle_redis_proto);
    }
}

void NvRedis::redis_init()
{
    string payload_key;
    m_topic_vms_event = GET_CONFIG().message_broker_topic;
    m_redisEndpoint = getRedisServerEndpoint();

    /* Create redis config file */
    payload_key = string("payloadkey=") + GET_CONFIG().message_broker_payload_key + "\n";
    m_redisconfigFile.open(REDIS_CONFIG_FILE);
    if (m_redisconfigFile.is_open())
    {
        m_redisconfigFile << "[message-broker]\n";
        m_redisconfigFile << "#hostname=localhost\n";
        m_redisconfigFile << "#port=6379\n";
        m_redisconfigFile << "#streamsize=10000\n";
        m_redisconfigFile << payload_key;
        m_redisconfigFile << "#consumergroup=mygroup\n";
        m_redisconfigFile << "#consumername=myname\n";
        m_redisconfigFile << "#share-connection = 1\n";

        m_redisconfigFile.close();
    }

    LOG(info) << "Radis server address:port= " << m_redisEndpoint << endl;
    m_conn_handle = nvds_msgapi_connect((char*)m_redisEndpoint.c_str(),
                                        nullptr, (char*)REDIS_CONFIG_FILE);
    if (!m_conn_handle)
    {
        LOG(error) << "Redis Connect failed. Exiting" << endl;
        m_connected = false;
        goto error;
    }
    else
    {
        LOG(info) << "Redis connect success." << endl;
        m_connected = true;
        m_error = false;
    }
    return;
error:
    m_error = true;
}

void NvRedis::reconnectToRedisServer()
{
    if (m_conn_handle)
    {
        nvds_msgapi_disconnect(m_conn_handle);
        m_conn_handle = nullptr;
    }
    redis_init();
}

bool NvRedis::deliverMessage(Json::Value& message)
{
    // Using lock so that m_message is not parallelly modified
    std::lock_guard<std::mutex> lock(m_messageLock);
    m_message = jsonToString(message);
    return sendToRedis(m_message);
}

bool NvRedis::sendToRedis(std::string& payload)
{
    bool ret_val = true;
    if(m_error)
    {
        LOG(error) << "Cann't send message in error condition" << endl;
        return false;
    }

    int ret = nvds_msgapi_send(m_conn_handle, (char*)m_topic_vms_event.c_str(),
                            (const uint8_t*) payload.c_str(), payload.size());
    if(ret == 0)
    {
        LOG(info) << "Event sent to Redis Successfully" << endl;
    }
    else
    {
        LOG(error) << "Could not send event to Redis" << endl;
        ret_val = false;
    }
    return ret_val;
}

void NvRedis::retryConnection()
{
    if (m_redisEndpoint.empty() == false)
    {
        LOG(info) << "Try redis-server reconnecting ..." << endl;
        reconnectToRedisServer();
        if (m_conn_handle)
        {
            m_connected = true;
        }
    }
}
