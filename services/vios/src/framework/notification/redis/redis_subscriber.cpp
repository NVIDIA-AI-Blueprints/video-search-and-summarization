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

#include <dlfcn.h>
#include <cstdlib>
#include <jsoncpp/json/json.h>
#include "redis_subscriber.h"
#include "config.h"
#include "logger.h"
#include "utils.h"
#include "ds_proto_parser.h"

using namespace std;

static void* openLibrary(const char* libName)
{
    std::string lib_path;
    void* handle = nullptr;

#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    lib_path = std::string(ABSOLUTE_PREBUILT_LIBRARY_PATH_ARCH64) + libName;
    handle = dlopen(lib_path.c_str(), RTLD_LAZY);
    if (!handle)
    {
        lib_path = std::string(RELATIVE_PREBUILT_LIBRARY_PATH_ARCH64) + libName;
        handle = dlopen(lib_path.c_str(), RTLD_LAZY);
    }
#else
    lib_path = std::string(ABSOLUTE_PREBUILT_LIBRARY_PATH_X86_64) + libName;
    handle = dlopen(lib_path.c_str(), RTLD_LAZY);
#endif

    return handle;
}

static void subscribe_cb(NvDsMsgApiErrorType flag, void *msg, int len, char *topic, void *user_ptr)
{
    if(flag == NVDS_MSGAPI_ERR)
    {
        LOG(error) << "Error in consuming message from redis broker" << endl;
    }
    else
    {
        RedisSubscriber* subscriber = (RedisSubscriber*) user_ptr;
        if (subscriber)
        {
            subscriber->deliverMessage(msg, len);
        }
    }
}

RedisSubscriber* RedisSubscriber::_instance = nullptr;

RedisSubscriber* RedisSubscriber::getInstance()
{
    if (_instance == nullptr)
    {
        _instance = new RedisSubscriber();
    }
    return _instance;
}

void RedisSubscriber::deleteInstance()
{
    if (_instance != nullptr)
    {
        delete _instance;
        _instance = nullptr;
    }
}

RedisSubscriber::RedisSubscriber()
        : nvds_msgapi_connect(nullptr)
        , nvds_msgapi_subscribe(nullptr)
        , nvds_msgapi_disconnect(nullptr)
        , m_connHandle(nullptr)
        , m_handleRedis(nullptr)
        , m_handleRedisProto(nullptr)
        , m_subscribeTopic("")
        , m_error(false)
{
    // Temporary solution to load libnvds_logger in memory
    m_handleRedis = openLibrary("libnvds_logger.so");
    if (!m_handleRedis)
    {
        LOG(error) << "Cannot open nvds_logger library: " << dlerror() << endl;
        goto error;
    }

    m_handleRedisProto = openLibrary("libnvds_redis_proto.so");
    if (!m_handleRedisProto)
    {
        LOG(error) << "Cannot open nvds_redis library: " << dlerror() << endl;
        goto error;
    }

    dlerror();

    nvds_msgapi_connect = (msgapi_connect_ptr) dlsym(m_handleRedisProto, "nvds_msgapi_connect");
    nvds_msgapi_subscribe = (msgapi_subscribe_ptr) dlsym(m_handleRedisProto, "nvds_msgapi_subscribe");
    nvds_msgapi_disconnect = (msgapi_disconnect_ptr) dlsym(m_handleRedisProto, "nvds_msgapi_disconnect");

    if (nvds_msgapi_connect == nullptr || nvds_msgapi_subscribe == nullptr || nvds_msgapi_disconnect == nullptr)
    {
        goto error;
    }

    /* redis subscriber initialization */
    redisInit();

    /* Initialize DS Proto Parser */
    DsProtoParser::getInstance();
    return;

error:
    const char *dlsym_error = dlerror();
    if (dlsym_error)
    {
        LOG(error) << "Cannot load symbol 'nvds_msgapi_': " << dlsym_error << endl;
        if (m_handleRedis)
        {
            dlclose(m_handleRedis);
        }
        if (m_handleRedisProto)
        {
            dlclose(m_handleRedisProto);
        }
        m_error = true;
    }
}

RedisSubscriber::~RedisSubscriber()
{
    nvds_msgapi_disconnect(m_connHandle);
    if (m_handleRedis)
    {
        dlclose(m_handleRedis);
    }
    if (m_handleRedisProto)
    {
        dlclose(m_handleRedisProto);
    }
    m_listeners.clear();
}

void RedisSubscriber::redisInit()
{
    m_subscribeTopic = GET_CONFIG().message_broker_topic_consumer;
    const char *topic[] = {m_subscribeTopic.c_str()};

    m_redisEndpoint = getRedisServerEndpoint();
    // Connect to Redis server
    m_connHandle = nvds_msgapi_connect((char*)m_redisEndpoint.c_str(),
                                        nullptr, (char*)"");
    if (!m_connHandle)
    {
        LOG(error) << "Redis Subscriber Connect failed. Exiting" << endl;
        goto error;
    }
    LOG(info) << "Redis Subscriber connect success." << endl;

    //Subscribe to topic
    if(nvds_msgapi_subscribe(m_connHandle, (char **)topic, 1, subscribe_cb, (void*)this) != NVDS_MSGAPI_OK)
    {
        LOG(error) << "Redis subscription to topic[s] failed. Exiting" << endl;
        goto error;
    }
    return;
error:
    m_error = true;
}

void RedisSubscriber::registerMessageListener(nv_vms::INotificationListener* listener)
{
    LOG(info) << "Registering message listener" << endl;
    std::lock_guard<std::mutex> lock(m_listenerMutex);
    m_listeners.insert(listener);
}

void RedisSubscriber::deregisterMessageListener(nv_vms::INotificationListener* listener)
{
    LOG(info) << "Deregistering message listener" << endl;
    std::lock_guard<std::mutex> lock(m_listenerMutex);
    m_listeners.erase(listener);
}

void RedisSubscriber::deliverMessage(void *msg, int len)
{
    if (!m_listeners.size())
    {
        return;
    }

    int64_t frameTimeMs = 0;
    Json::Value payload = DsProtoParser::getInstance()->parseMessage(msg, len, frameTimeMs);
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
