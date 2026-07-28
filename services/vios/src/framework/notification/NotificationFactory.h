/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "utilities/config.h"
#include "notification_manager.h"
#include "composite_notifier.h"
#include "redis/redis_publisher.h"
#include "redis/redis_subscriber.h"
#include "kafka/kafka_producer.h"
#include "kafka/kafka_consumer.h"
#include "mqtt/mqtt_publisher.h"
#include "mqtt/mqtt_subscriber.h"
#include "webhook/webhook_notifier.h"

namespace nv_vms
{
class NotificationFactory
{
    public:
        static INotificationInterface* CreatePlatformNotification()
        {
            nv_vms::DeviceConfig config =  GET_CONFIG();
            INotificationInterface* broker = nullptr;
            if (config.enable_notification)
            {
                if (config.use_message_broker == "redis")
                {
                    broker = NvRedis::getInstance();
                }
                else if (config.use_message_broker == "kafka")
                {
                    broker = NvKafka::getInstance();
                }
                else if (config.use_message_broker == "mqtt")
                {
                    broker = MqttPublisher::getInstance();
                }
            }

            // nullptr unless notification_config.json defines enabled webhooks.
            INotificationInterface* webhook = WebhookNotifier::getInstance();

            if (broker != nullptr && webhook != nullptr)
            {
                CompositeNotifier* composite = CompositeNotifier::getInstance();
                composite->addNotifier(broker);
                composite->addNotifier(webhook);
                return composite;
            }
            if (webhook != nullptr)
            {
                return webhook;
            }
            return broker;
        }

        static void DeletePlatformNotification()
        {
            // The composite only references the singletons below; drop it first.
            CompositeNotifier::deleteInstance();
            WebhookNotifier::deleteInstance();

            nv_vms::DeviceConfig config =  GET_CONFIG();
            if (config.enable_notification == false)
            {
                return;
            }
            if (config.use_message_broker == "redis")
            {
                return NvRedis::deleteInstance();
            }
            if (config.use_message_broker == "kafka")
            {
                return NvKafka::deleteInstance();
            }
        }

        static INotificationInterface* CreateNotificationConsumer()
        {
            nv_vms::DeviceConfig config =  GET_CONFIG();
            if (config.enable_notification_consumer == false)
            {
                return nullptr;
            }
            if (config.use_message_broker_consumer == "redis")
            {
                return RedisSubscriber::getInstance();
            }
            if (config.use_message_broker_consumer == "kafka")
            {
                return KafkaConsumer::getInstance();
            }
            if (config.use_message_broker_consumer == "mqtt")
            {
                return MqttSubscriber::getInstance();
            }

            return nullptr;
        }
};
} // nv_vms
