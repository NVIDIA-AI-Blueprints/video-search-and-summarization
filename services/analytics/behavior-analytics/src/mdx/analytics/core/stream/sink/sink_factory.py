# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from mdx.analytics.core.schema.config import AppConfig, MISSING
from mdx.analytics.core.stream.sink.sink_kafka import SinkKafka
from mdx.analytics.core.stream.sink.sink_mqtt import SinkMQTT
from mdx.analytics.core.stream.sink.sink_redis_stream import SinkRedisStream


def get_sink(config: AppConfig) -> SinkKafka | SinkRedisStream | SinkMQTT:
    """
    Create and return an appropriate sink instance based on the application configuration.
    
    This factory function examines the sink type specified in the configuration and 
    creates the corresponding sink implementation. It supports Kafka, Redis Stream and MQTT 
    sink types, each with their own configuration requirements.
    
    :param config: The application configuration object containing sink type 
                   and connection details for the appropriate messaging system.
    :param AppConfig config: Application configuration.
    
    :return SinkKafka | SinkRedisStream | SinkMQTT: A concrete sink implementation that implements the Sink abstract base class interface.
    
    :raises ValueError: If no valid sink configuration is found or if the sink type is 
                        unsupported, or if required connection parameters are missing 
                        for the specified sink type.
    """

    sink_type = config.get_app_config("sinkType")

    if sink_type == "kafka" and config.kafka and config.kafka.brokers != MISSING:
        return SinkKafka(config)

    elif sink_type == "redisStream" and config.redisStream and config.redisStream.host != MISSING:
        return SinkRedisStream(config)

    elif sink_type == "mqtt" and config.mqtt and config.mqtt.host != MISSING:
        return SinkMQTT(config)

    raise ValueError("Missing a valid sink configuration")
