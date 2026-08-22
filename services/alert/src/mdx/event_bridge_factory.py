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
from typing import Dict, Any, Optional

from mdx.source.source_base import SourceBase
from mdx.sink.sink_base import SinkBase

logger = logging.getLogger(__name__)

KAFKA = 'kafka'
REDIS_STREAM = 'redisStream'
CONSOLE = 'console'

#: Accepted spellings for each transport. Selection is case- and
#: separator-insensitive so ``redisStream``, ``redis_stream`` and
#: ``redis-stream`` all resolve to the same transport, matching the
#: ``redisStream`` spelling used by vss-behavior-analytics.
_TRANSPORT_ALIASES = {
    'kafka': KAFKA,
    'redisstream': REDIS_STREAM,
    'redis': REDIS_STREAM,
    'console': CONSOLE,
}

_SOURCE_TYPES = {
    KAFKA: 'Apache Kafka message broker',
    REDIS_STREAM: 'Redis Streams consumer group',
}

_SINK_TYPES = {
    KAFKA: 'Apache Kafka message broker',
    REDIS_STREAM: 'Redis Streams publisher',
    CONSOLE: 'Log-only sink for local development and debugging',
}

#: Configuration section each non-default transport requires.
_REQUIRED_SECTIONS = {
    'source': {REDIS_STREAM: 'redis_source'},
    'sink': {REDIS_STREAM: 'redis_sink'},
}


def _configured_transport(config: Dict[str, Any], key: str) -> Any:
    """Read a transport selection, treating a blank value as unset.

    Deployment configs are rendered by substituting ``${VAR}`` placeholders, and
    an unset variable substitutes to an empty string. Falling back to the
    default there is what keeps a Kafka deployment working when it is upgraded
    before its environment gains the new Redis variables.
    """
    raw = (config.get('event_bridge') or {}).get(key, KAFKA)
    if isinstance(raw, str) and not raw.strip():
        return KAFKA
    return raw


def _normalize_transport(value: Any) -> Optional[str]:
    """Resolve a configured transport name to its canonical form.

    Returns ``None`` when the value is not a recognized transport.
    """
    if not isinstance(value, str):
        return None
    key = value.strip().lower().replace('_', '').replace('-', '')
    return _TRANSPORT_ALIASES.get(key)


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
            configured = _configured_transport(config, 'sourceType')
            source_type = _normalize_transport(configured)

            logger.info(f"Creating source of type: {configured}")

            if source_type == KAFKA:
                from mdx.source.source_kafka import SourceKafka
                return SourceKafka(config)
            elif source_type == REDIS_STREAM:
                from mdx.source.source_redis_stream import SourceRedisStream
                return SourceRedisStream(config)
            else:
                supported = "', '".join(_SOURCE_TYPES)
                raise ValueError(
                    f"Unsupported source type: {configured} (supported: '{supported}')"
                )

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
            configured = _configured_transport(config, 'sinkType')
            sink_type = _normalize_transport(configured)

            logger.info(f"Creating sink of type: {configured}")

            if sink_type == KAFKA:
                from mdx.sink.sink_kafka import KafkaSink
                return KafkaSink(config)
            elif sink_type == REDIS_STREAM:
                from mdx.sink.sink_redis_stream import SinkRedisStream
                return SinkRedisStream(config)
            elif sink_type == CONSOLE:
                from mdx.sink.sink_console import ConsoleSink
                return ConsoleSink(config)
            else:
                supported = "', '".join(_SINK_TYPES)
                raise ValueError(
                    f"Unsupported sink type: {configured} (supported: '{supported}')"
                )

        except Exception as e:
            logger.error(f"Failed to create sink: {e}")
            raise

    @staticmethod
    def get_available_source_types() -> Dict[str, str]:
        """Get available source types with descriptions"""
        return dict(_SOURCE_TYPES)

    @staticmethod
    def get_available_sink_types() -> Dict[str, str]:
        """Get available sink types with descriptions"""
        return dict(_SINK_TYPES)

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
            configured_source = _configured_transport(config, 'sourceType')
            source_type = _normalize_transport(configured_source)
            if source_type not in _SOURCE_TYPES:
                logger.error(f"Invalid source type: {configured_source}")
                return False

            # Check sink type
            configured_sink = _configured_transport(config, 'sinkType')
            sink_type = _normalize_transport(configured_sink)
            if sink_type not in _SINK_TYPES:
                logger.error(f"Invalid sink type: {configured_sink}")
                return False

            # Validate specific configuration sections
            if source_type == KAFKA and 'kafka_source' not in event_bridge:
                logger.warning("Kafka source selected but kafka_source configuration not found, falling back to legacy kafka config")

            if sink_type == KAFKA and 'kafka_sink' not in event_bridge:
                logger.warning("Kafka sink selected but kafka_sink configuration not found, falling back to legacy kafka config")

            # Non-Kafka transports have no legacy fallback, so a missing
            # section is a hard error rather than a warning.
            for role, transport in (('source', source_type), ('sink', sink_type)):
                required = _REQUIRED_SECTIONS[role].get(transport)
                if required and not event_bridge.get(required):
                    logger.error(
                        "%s selected as the %s but event_bridge.%s is missing or empty",
                        transport, role, required,
                    )
                    return False

            logger.info("Event bridge configuration validation passed")
            return True

        except Exception as e:
            logger.error(f"Configuration validation failed: {e}")
            return False
