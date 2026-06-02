# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import logging
from typing import Dict, Any

from mdx.anomaly.source.source_base import SourceBase
from mdx.anomaly.sink.sink_base import SinkBase

logger = logging.getLogger(__name__)


class EventBridgeFactory:
    """Factory class for creating event bridge sources and sinks based on configuration"""
    
    @staticmethod
    def create_source(config: Dict[str, Any]) -> SourceBase:
        """
        Create a source instance based on configuration
        
        Args:
            config: Configuration dictionary
            
        Returns:
            SourceBase: Configured source instance
            
        Raises:
            ValueError: If source type is not supported
        """
        try:
            # Get source type from event_bridge configuration
            source_type = config.get('event_bridge', {}).get('sourceType', 'kafka')
            
            logger.info(f"Creating source of type: {source_type}")
            
            if source_type == 'kafka':
                from mdx.anomaly.source.source_kafka import SourceKafka
                return SourceKafka(config)
            elif source_type == 'redisStream':
                from mdx.anomaly.source.source_redis_stream import SourceRedisStream
                return SourceRedisStream(config)
            else:
                raise ValueError(f"Unsupported source type: {source_type}")
                
        except Exception as e:
            logger.error(f"Failed to create source: {e}")
            raise
    
    @staticmethod
    def create_sink(config: Dict[str, Any]) -> SinkBase:
        """
        Create a sink instance based on configuration
        
        Args:
            config: Configuration dictionary
            
        Returns:
            SinkBase: Configured sink instance
            
        Raises:
            ValueError: If sink type is not supported
        """
        try:
            # Get sink type from event_bridge configuration
            sink_type = config.get('event_bridge', {}).get('sinkType', 'kafka')
            
            logger.info(f"Creating sink of type: {sink_type}")
            
            if sink_type == 'kafka':
                from mdx.anomaly.sink.sink_kafka import KafkaSink
                return KafkaSink(config)
            elif sink_type == 'redisStream':
                from mdx.anomaly.sink.sink_redis_stream import SinkRedisStream
                return SinkRedisStream(config)
            else:
                raise ValueError(f"Unsupported sink type: {sink_type}")
                
        except Exception as e:
            logger.error(f"Failed to create sink: {e}")
            raise
    
    @staticmethod
    def get_available_source_types() -> Dict[str, str]:
        """Get available source types with descriptions"""
        return {
            'kafka': 'Apache Kafka message broker',
            'redisStream': 'Redis Streams'
        }
    
    @staticmethod
    def get_available_sink_types() -> Dict[str, str]:
        """Get available sink types with descriptions"""
        return {
            'kafka': 'Apache Kafka message broker',
            'redisStream': 'Redis Streams'
        }
    
    @staticmethod
    def validate_configuration(config: Dict[str, Any]) -> bool:
        """
        Validate event bridge configuration
        
        Args:
            config: Configuration dictionary
            
        Returns:
            bool: True if configuration is valid
        """
        try:
            event_bridge = config.get('event_bridge', {})
            
            # Check source type
            source_type = event_bridge.get('sourceType', 'kafka')
            if source_type not in EventBridgeFactory.get_available_source_types():
                logger.error(f"Invalid source type: {source_type}")
                return False
                
            # Check sink type
            sink_type = event_bridge.get('sinkType', 'kafka')
            if sink_type not in EventBridgeFactory.get_available_sink_types():
                logger.error(f"Invalid sink type: {sink_type}")
                return False
                
            # Validate specific configuration sections
            if source_type == 'kafka' and 'kafka_source' not in event_bridge:
                logger.warning("Kafka source selected but kafka_source configuration not found, falling back to legacy kafka config")
                
            if source_type == 'redisStream' and 'redis_source' not in event_bridge:
                logger.error("Redis Stream source selected but redis_source configuration not found")
                return False
                
            if sink_type == 'kafka' and 'kafka_sink' not in event_bridge:
                logger.warning("Kafka sink selected but kafka_sink configuration not found, falling back to legacy kafka config")
                
            if sink_type == 'redisStream' and 'redis_sink' not in event_bridge:
                logger.error("Redis Stream sink selected but redis_sink configuration not found")
                return False
                
            logger.info("Event bridge configuration validation passed")
            return True
            
        except Exception as e:
            logger.error(f"Configuration validation failed: {e}")
            return False 