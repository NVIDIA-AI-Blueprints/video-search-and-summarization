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

#include "kafka_consumer.h"

#include <dlfcn.h>
#include <iostream>
#include "logger.h"
#include "config.h"
#include "utils.h"
#include <chrono>
#include <iomanip>
#include <sstream>
#include <ctime>
#include <algorithm>
#include <cstring>
#include <thread>
#include <unistd.h>
#include "ds_proto_parser.h"

constexpr const char* ABSOLUTE_LIBRARY_PATH_X86_64 = "/usr/lib/x86_64-linux-gnu/librdkafka.so";
constexpr const char* ABSOLUTE_LIBRARY_PATH_AARCH64 = "/usr/lib/aarch64-linux-gnu/librdkafka.so";

#define SET_ERROR_RETURN_FALSE \
    do { \
        m_fatalError = true; \
        return false; \
    } while(0)

constexpr const char* SESSION_TIMEOUT_MS = "30000";
constexpr const char* HEARTBEAT_INTERVAL_MS = "10000";
constexpr const char* MAX_POLL_INTERVAL_MS = "300000";
constexpr int POLL_INTERVAL_MS = 30;

constexpr const char* ABSOLUTE_LIBRARY_PATH_2D_X86_64 = "/home/vst/vst_release/prebuilts/x86_64/libnvds_schema_2d.so";
constexpr const char* ABSOLUTE_LIBRARY_PATH_3D_X86_64 = "/home/vst/vst_release/prebuilts/x86_64/libnvds_schema_3d.so";

// Forward declarations for rdkafka types
typedef struct rd_kafka_conf_s rd_kafka_conf_t;
typedef struct rd_kafka_topic_conf_s rd_kafka_topic_conf_t;
typedef struct rd_kafka_topic_partition_list_s rd_kafka_topic_partition_list_t;
typedef enum rd_kafka_type_t rd_kafka_type_t;
typedef struct rd_kafka_message_s rd_kafka_message_t;

KafkaConsumer* KafkaConsumer::getInstance()
{
    static KafkaConsumer instance;
    return &instance;
}

KafkaConsumer::KafkaConsumer()
{
    if (!kafkaInit())
    {
        LOG(error) << "Failed to initialize Kafka consumer" << std::endl;
    }
    else
    {
        DsProtoParser::getInstance();
        LOG(info) << "Successfully loaded Kafka consumer module" << endl;
    }
}

KafkaConsumer::~KafkaConsumer()
{
    LOG(info) << __METHOD_NAME__ << endl;
    m_running = false;
    if (m_pollThread.joinable())
    {
        m_pollThread.join();
    }

    if (m_topic)
    {
        rd_kafka_topic_destroy(m_topic);
        m_topic = nullptr;
    }
    if (m_consumer)
    {
        rd_kafka_destroy(m_consumer);
        m_consumer = nullptr;
    }
    if (m_libHandle)
    {
        dlclose(m_libHandle);
        m_libHandle = nullptr;
    }
}

bool KafkaConsumer::kafkaInit()
{
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    m_libHandle = dlopen(ABSOLUTE_LIBRARY_PATH_AARCH64, RTLD_LAZY);
#else
    m_libHandle = dlopen(ABSOLUTE_LIBRARY_PATH_X86_64, RTLD_LAZY);
#endif
    if (!m_libHandle)
    {
        LOG(error) << "Failed to load librdkafka: " << dlerror() << std::endl;
        m_fatalError = true;
        return false;
    }

    dlerror();
    // Load required symbols
    auto rd_kafka_conf_new =
        reinterpret_cast<rd_kafka_conf_t* (*)()>(
            dlsym(m_libHandle, "rd_kafka_conf_new"));
    auto rd_kafka_conf_set =
        reinterpret_cast<rd_kafka_conf_res_t (*)(rd_kafka_conf_t*, const char*, const char*, char*, size_t)>(
            dlsym(m_libHandle, "rd_kafka_conf_set"));
    auto rd_kafka_new =
        reinterpret_cast<rd_kafka_t* (*)(rd_kafka_type_t, rd_kafka_conf_t*, char*, size_t)>(
            dlsym(m_libHandle, "rd_kafka_new"));
    auto rd_kafka_topic_new =
        reinterpret_cast<rd_kafka_topic_t* (*)(rd_kafka_t*, const char*, rd_kafka_topic_conf_t*)>(
            dlsym(m_libHandle, "rd_kafka_topic_new"));
    auto rd_kafka_subscribe =
        reinterpret_cast<rd_kafka_resp_err_t (*)(rd_kafka_t*, rd_kafka_topic_partition_list_t*)>(
            dlsym(m_libHandle, "rd_kafka_subscribe"));
    auto rd_kafka_topic_partition_list_new =
        reinterpret_cast<rd_kafka_topic_partition_list_t* (*)(int)>(
            dlsym(m_libHandle, "rd_kafka_topic_partition_list_new"));
    auto rd_kafka_topic_partition_list_add =
        reinterpret_cast<rd_kafka_topic_partition_list_t* (*)(rd_kafka_topic_partition_list_t*, const char*, int)>(
            dlsym(m_libHandle, "rd_kafka_topic_partition_list_add"));
    auto rd_kafka_consumer_poll =
        reinterpret_cast<rd_kafka_message_t* (*)(rd_kafka_t*, int)>(
            dlsym(m_libHandle, "rd_kafka_consumer_poll"));
    auto rd_kafka_topic_partition_list_destroy =
        reinterpret_cast<void (*)(rd_kafka_topic_partition_list_t*)>(
            dlsym(m_libHandle, "rd_kafka_topic_partition_list_destroy"));
    auto rd_kafka_message_destroy =
        reinterpret_cast<void (*)(rd_kafka_message_t*)>(
            dlsym(m_libHandle, "rd_kafka_message_destroy"));

    if (!rd_kafka_conf_new || !rd_kafka_conf_set || !rd_kafka_new || !rd_kafka_topic_new || !rd_kafka_subscribe
        || !rd_kafka_topic_partition_list_new || !rd_kafka_topic_partition_list_add || !rd_kafka_consumer_poll
        || !rd_kafka_topic_partition_list_destroy || !rd_kafka_message_destroy)
    {
        LOG(error) << "Failed to load required symbols" << endl;
        const char *dlsym_error = dlerror();
        if (dlsym_error)
        {
            LOG(error) << "Error while loading symbol 'rd_kafka_': " << dlsym_error << endl;
            if (m_libHandle)
            {
                dlclose(m_libHandle);
                m_libHandle = nullptr;
            }
        }
        SET_ERROR_RETURN_FALSE;
    }

    rd_kafka_destroy = reinterpret_cast<void (*)(rd_kafka_t*)>(
        dlsym(m_libHandle, "rd_kafka_destroy"));
    rd_kafka_topic_destroy = reinterpret_cast<void (*)(rd_kafka_topic_t*)>(
        dlsym(m_libHandle, "rd_kafka_topic_destroy"));

    if (!rd_kafka_destroy || !rd_kafka_topic_destroy)
    {
        LOG(error) << "Failed to load destroy symbols: " << dlerror() << endl;
    }

    char errstr[512];
    // Create configuration object
    auto conf = rd_kafka_conf_new();
    if (!conf)
    {
        SET_ERROR_RETURN_FALSE;
    }

    nv_vms::DeviceConfig config =  GET_CONFIG();
    m_topic_vms_event = config.message_broker_topic_consumer;
    m_kafkaBootstrapServer = getKafkaServerEndpoint();
    if (m_kafkaBootstrapServer.empty())
    {
        LOG(error) << "ERROR- Kafka server address:port not specified" << endl;
        SET_ERROR_RETURN_FALSE;
    }
    LOG(info) << "Kafka bootstrap server address:port: " << m_kafkaBootstrapServer << endl;

    // Set configuration properties
    rd_kafka_conf_set(conf, "bootstrap.servers", m_kafkaBootstrapServer.c_str(),
                            errstr, sizeof(errstr));
    std::string group_id = generate_uuid();
    rd_kafka_conf_set(conf, "group.id", group_id.c_str(), errstr, sizeof(errstr));
    rd_kafka_conf_set(conf, "auto.offset.reset", "latest", errstr, sizeof(errstr));
    rd_kafka_conf_set(conf, "session.timeout.ms", SESSION_TIMEOUT_MS, errstr, sizeof(errstr));
    rd_kafka_conf_set(conf, "heartbeat.interval.ms", HEARTBEAT_INTERVAL_MS, errstr, sizeof(errstr));
    rd_kafka_conf_set(conf, "max.poll.interval.ms", MAX_POLL_INTERVAL_MS, errstr, sizeof(errstr));

    // Create consumer instance
    m_consumer = rd_kafka_new(RD_KAFKA_CONSUMER, conf, errstr, sizeof(errstr));
    if (!m_consumer)
    {
        LOG(error) << "Failed to create consumer: " << errstr << std::endl;
        SET_ERROR_RETURN_FALSE;
    }

    // Create topic partition list
    auto topics = rd_kafka_topic_partition_list_new(1);
    rd_kafka_topic_partition_list_add(topics, m_topic_vms_event.c_str(), RD_KAFKA_PARTITION_UA);

    // Subscribe to topics
    auto err = rd_kafka_subscribe(m_consumer, topics);
    rd_kafka_topic_partition_list_destroy(topics);
    if (err)
    {
        LOG(error) << "Failed to subscribe to topics" << std::endl;
        SET_ERROR_RETURN_FALSE;
    }

    // Start a thread for polling messages
    m_running = true;
    m_pollThread = std::thread([this, rd_kafka_consumer_poll, rd_kafka_message_destroy]()
    {
        rd_kafka_message_t *rkm;
        while (m_running)
        {
            rkm = rd_kafka_consumer_poll(m_consumer, POLL_INTERVAL_MS);
            if (!rkm)
            {
                continue;
            }
            if (rkm->payload)
            {
                deliverMessage((void*)rkm->payload, (int)rkm->len);
            }
            rd_kafka_message_destroy(rkm);
        }
    });
    return true;
}

void KafkaConsumer::registerMessageListener(nv_vms::INotificationListener* listener)
{
    if (listener && !m_fatalError)
    {
        std::lock_guard<std::mutex> lock(m_listenerMutex);
        m_listeners.insert(listener);
    }
}

void KafkaConsumer::deregisterMessageListener(nv_vms::INotificationListener* listener)
{
    if (listener)
    {
        std::lock_guard<std::mutex> lock(m_listenerMutex);
        m_listeners.erase(listener);
    }
}

void KafkaConsumer::deliverMessage(void* msg, int len)
{
    {
        std::lock_guard<std::mutex> lock(m_listenerMutex);
        if (m_listeners.empty())
        {
            return;
        }
    }

    // Initialize latency tracking if not already done
    if (!m_latencyTrackingInitialized)
    {
        std::lock_guard<std::mutex> latencyLock(m_latencyMutex);
        m_lastPrintTime = std::chrono::steady_clock::now();
        m_latencyTrackingInitialized = true;
    }

    int64_t frameTimeMs = 0;
    Json::Value payload = DsProtoParser::getInstance()->parseMessage(msg, len, frameTimeMs);
    if (payload == Json::nullValue)
    {
        return;
    }
    // Latency/statistics logic
    int64_t currentTimeMs = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();

    // Prevent integer overflow by checking bounds and using safe arithmetic
    int64_t latency = 0;
    if (frameTimeMs <= currentTimeMs)
    {
        // Normal case: frame time is in the past or present
        latency = currentTimeMs - frameTimeMs;
    }
    else
    {
        // Frame time is in the future (clock skew or invalid timestamp)
        // Use absolute difference to avoid overflow
        latency = frameTimeMs - currentTimeMs;
        LOG(warning) << "Frame timestamp is in the future: frameTime=" << frameTimeMs
                    << ", currentTime=" << currentTimeMs << ", latency=" << latency << "ms" << std::endl;
    }

    if (GET_CONFIG().enable_latency_logging)
    {
        std::lock_guard<std::mutex> latencyLock(m_latencyMutex);
        m_latencies.push_back(latency);
        auto now = std::chrono::steady_clock::now();
        if (std::chrono::duration_cast<std::chrono::seconds>(now - m_lastPrintTime).count() >= 10)
        {
            printLatencyStats();
            m_lastPrintTime = now;
        }
    }
    {
        // Notify listeners
        std::lock_guard<std::mutex> lock(m_listenerMutex);
        for (auto& listener: m_listeners)
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
}

void KafkaConsumer::printLatencyStats()
{
    if (m_latencies.empty()) {
        LOG(info) << "No latency data available yet" << std::endl;
        return;
    }

    // Calculate average latency
    int64_t totalLatency = 0;
    for (const auto& latency : m_latencies) {
        totalLatency += latency;
    }
    double avgLatency = static_cast<double>(totalLatency) / m_latencies.size();

    // Calculate min and max latency
    int64_t minLatency = *std::min_element(m_latencies.begin(), m_latencies.end());
    int64_t maxLatency = *std::max_element(m_latencies.begin(), m_latencies.end());

    // Log statistics
    LOG(info) << "Latency statistics (ms) - Avg: " << avgLatency
              << ", Min: " << minLatency
              << ", Max: " << maxLatency
              << ", Samples: " << m_latencies.size() << std::endl;

    // Clear latencies for the next period
    m_latencies.clear();
}
