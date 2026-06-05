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

#include <set>
#include <memory>
#include <string>
#include <mutex>
#include <thread>
#include <vector>
#include <chrono>
#include <librdkafka/rdkafka.h>
#include "notification/notification_manager.h"

// Forward declarations for rdkafka types
typedef struct rd_kafka_s rd_kafka_t;
typedef struct rd_kafka_topic_s rd_kafka_topic_t;

class KafkaConsumer : public nv_vms::INotificationInterface
{
public:
    static KafkaConsumer* getInstance();

    void registerMessageListener(nv_vms::INotificationListener* listener) override;
    void deregisterMessageListener(nv_vms::INotificationListener* listener) override;
    bool deliverMessage (Json::Value& message) override { return true; }
    void retryConnection () override {}

    void deliverMessage(void* msg, int len);

    KafkaConsumer(const KafkaConsumer&) = delete;
    KafkaConsumer& operator=(const KafkaConsumer&) = delete;

private:
    KafkaConsumer();
    virtual ~KafkaConsumer();

    bool kafkaInit();

private:
    std::string     m_topic_vms_event {""};
    std::string     m_kafkaBootstrapServer {""};
    std::thread     m_pollThread;
    bool            m_running{false};
    // Kafka library handles
    void*           m_libHandle{nullptr};
    rd_kafka_t*     m_consumer{nullptr};
    rd_kafka_topic_t* m_topic{nullptr};

    // Function pointers for librdkafka symbols
    void (*rd_kafka_destroy)(rd_kafka_t*){nullptr};
    void (*rd_kafka_topic_destroy)(rd_kafka_topic_t*){nullptr};

    // Typedefs for schema C API
    typedef int64_t (*frame_get_timestamp_ms_t)(void*);
    typedef int (*frame_get_object_count_t)(void*);
    typedef void* (*frame_get_object_t)(void*, int);
    typedef char* (*object_get_id_t)(void*);
    typedef char* (*object_get_type_t)(void*);
    typedef float (*object_get_confidence_t)(void*);
    typedef bool (*object_has_bbox_t)(void*);
    typedef void (*object_get_bbox_t)(void*, float*, float*, float*, float*);
    typedef bool (*object_has_bbox3d_t)(void*);
    typedef int (*object_get_bbox3d_coordinates_t)(void*, double*, int);
    typedef float (*object_get_bbox3d_confidence_t)(void*);
    typedef bool (*object_has_pose_t)(void*);
    typedef char* (*object_get_pose_type_t)(void*);
    typedef int (*object_get_pose_keypoints_count_t)(void*);
    typedef void (*object_get_pose_keypoint_coordinates_t)(void*, int, float*, int);
    typedef int (*object_get_pose_keypoint_quaternion_t)(void*, int, float*, int);
    typedef int (*object_get_pose_actions_count_t)(void*);
    typedef char* (*object_get_pose_action_type_t)(void*, int);
    typedef float (*object_get_pose_action_confidence_t)(void*, int);
    typedef void* (*frame_new_t)();
    typedef bool (*frame_parse_t)(void*, const void*, size_t);
    typedef char* (*frame_get_sensorid_t)(void*);
    typedef void (*frame_destroy_t)(void*);

    // Function pointers for schema C API
    frame_get_timestamp_ms_t m_frame_get_timestamp_ms = nullptr;
    frame_get_object_count_t m_frame_get_object_count = nullptr;
    frame_get_object_t m_frame_get_object = nullptr;
    object_get_id_t m_object_get_id = nullptr;
    object_get_type_t m_object_get_type = nullptr;
    object_get_confidence_t m_object_get_confidence = nullptr;
    object_has_bbox_t m_object_has_bbox2d = nullptr;
    object_get_bbox_t m_object_get_bbox = nullptr;
    object_has_bbox3d_t m_object_has_bbox3d = nullptr;
    object_get_bbox3d_coordinates_t m_object_get_bbox3d_coordinates = nullptr;
    object_get_bbox3d_confidence_t m_object_get_bbox3d_confidence = nullptr;
    object_has_pose_t m_object_has_pose = nullptr;
    object_get_pose_type_t m_object_get_pose_type = nullptr;
    object_get_pose_keypoints_count_t m_object_get_pose_keypoints_count = nullptr;
    object_get_pose_keypoint_coordinates_t m_object_get_pose_keypoint_coordinates = nullptr;
    object_get_pose_keypoint_quaternion_t m_object_get_pose_keypoint_quaternion = nullptr;
    object_get_pose_actions_count_t m_object_get_pose_actions_count = nullptr;
    object_get_pose_action_type_t m_object_get_pose_action_type = nullptr;
    object_get_pose_action_confidence_t m_object_get_pose_action_confidence = nullptr;
    frame_new_t m_frame_new = nullptr;
    frame_parse_t m_frame_parse = nullptr;
    frame_get_sensorid_t m_frame_get_sensorid = nullptr;
    frame_destroy_t m_frame_destroy = nullptr;

    // Latency tracking
    std::mutex m_latencyMutex;
    std::vector<int64_t> m_latencies;
    std::chrono::time_point<std::chrono::steady_clock> m_lastPrintTime;
    std::atomic<bool> m_latencyTrackingInitialized{false};

    // Helper method to print latency statistics
    void printLatencyStats();
};