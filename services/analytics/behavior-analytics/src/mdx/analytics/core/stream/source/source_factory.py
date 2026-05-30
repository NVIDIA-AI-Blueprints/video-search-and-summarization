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
from mdx.analytics.core.stream.source.source_kafka import SourceKafka
from mdx.analytics.core.stream.source.source_mqtt import SourceMQTT
from mdx.analytics.core.stream.source.source_redis_stream import SourceRedisStream


def get_source(config: AppConfig) -> SourceKafka | SourceRedisStream | SourceMQTT:
    """
    Factory function to create and return the appropriate source instance based on configuration.
    
    This function examines the source type specified in the application configuration and 
    instantiates the corresponding source implementation (Kafka or Redis Stream or MQTT). It validates 
    that the required configuration parameters are present for the selected source type.
    
    :param config: The application configuration object containing source type 
                   and connection parameters for different source implementations.
    :param AppConfig config: Application configuration.
    :return SourceKafka | SourceRedisStream | SourceMQTT: An instance of the appropriate source class based on the configured source type.
    :raises ValueError: If no valid source configuration is found or if required configuration 
                       parameters are missing for the specified source type.
    """

    src_type = config.get_app_config("sourceType")

    if src_type == "kafka" and config.kafka and config.kafka.brokers != MISSING:
        return SourceKafka(config)

    elif src_type == "redisStream" and config.redisStream and config.redisStream.host != MISSING:
        return SourceRedisStream(config)

    elif src_type == "mqtt" and config.mqtt and config.mqtt.host != MISSING:
        return SourceMQTT(config)

    raise ValueError("Missing a valid source configuration")
