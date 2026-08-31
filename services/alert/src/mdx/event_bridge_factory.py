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
from mdx.stream_routing import (
    EVENT_BRIDGE_SINK_ROUTES, HEARTBEAT_KIND, LEGACY_KIND_ALIASES,
    SUPPORTED_KINDS, canonical_kind, require_distinct_streams,
    require_kind_coverage, require_known_keys, require_stream_map,
    require_stream_name,
)
# The transport vocabulary and the folding rule are shared with the terminal
# sink's factory, which reads the same operator-supplied names out of the same
# config file. Re-exported here because both are part of this module's published
# surface.
from mdx.transport.names import (
    CONSOLE, KAFKA, REDIS_STREAM, normalize as _normalize_transport,
    require_terminal_sink_type,
)

logger = logging.getLogger(__name__)

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

#: Keys that must carry a value inside each required section, beyond the section
#: merely existing. Checking only for the section made ``validate_configuration``
#: return ``True`` for a section holding nothing usable, which pushed the real
#: error into the source or sink constructor further into startup — where it is
#: reported against a transport class rather than against the config the operator
#: has open. Streams are checked separately because they are nested.
_REQUIRED_SECTION_KEYS = {
    'redis_source': ('consumer_group',),
    'redis_sink': (),
}


#: Keys each Redis section's stream map accepts. The source's are event kinds;
#: the sink's are the output routes it publishes, which is why the two cannot
#: share one vocabulary or one set of rules.
_REDIS_STREAM_KEYS = {
    'redis_source': SUPPORTED_KINDS,
    'redis_sink': EVENT_BRIDGE_SINK_ROUTES,
}

#: Keys a section takes but that are not what an operator should be told to
#: write: the heartbeat stream, which is not an event kind and is optional, and
#: the legacy kind spellings the source still folds. Listed so an unknown key is
#: refused here without these being refused with it.
_REDIS_STREAM_EXTRA_KEYS = {
    'redis_source': (HEARTBEAT_KIND, *LEGACY_KIND_ALIASES),
    'redis_sink': (),
}


def _validate_redis_streams(section_name: str, section: Dict[str, Any]) -> bool:
    """Report whether a Redis section names a valid set of streams.

    The rules live in :mod:`mdx.stream_routing` because the source and both sinks
    enforce the same ones; this only adapts them to the boolean contract
    ``validate_configuration`` has, so that an operator gets the same sentence
    here as they would from the constructor that runs later.
    """
    setting = f"event_bridge.{section_name}.streams"
    keys = _REDIS_STREAM_KEYS[section_name]
    try:
        streams = require_stream_map(section.get('streams'), setting, keys)
        require_known_keys(
            streams, setting, keys, _REDIS_STREAM_EXTRA_KEYS[section_name],
        )
        for key, value in streams.items():
            require_stream_name(value, f"{setting}['{key}']")
        require_distinct_streams(streams, setting)
        if section_name == 'redis_source':
            # Only the source. Its keys are event kinds, so "both kinds present"
            # is a question that can be asked of them; the sink's keys are output
            # route names and the same check would reject every valid config.
            require_kind_coverage(
                {canonical_kind(key) for key in streams}, setting,
            )
    except ValueError as exc:
        logger.error("%s", exc)
        return False
    return True

def _validate_redis_connection(config: Dict[str, Any], section_name: Optional[str],
                               selected_by: str,
                               override: Optional[Dict[str, Any]] = None) -> bool:
    """Report whether a Redis component can reach the instance it names.

    Everything here decides *where* this connects or whether it will be let in --
    host, port, logical database, credential -- so all of it is checked together
    and none of it falls back to a default. They also fail as one thing to the
    operator, who is looking at one connection block.

    At startup rather than where the client is built, which is inside a forked
    pipeline child: a mistyped port, a database that is not a number or a Secret
    whose mount never appeared each crash-looped a child on a traceback instead
    of failing the container with the key to fix.

    Imported inside the function, as the Redis source and sink are: this module
    is on the Kafka path, and a top-level import would load the ``redis``
    package for a deployment that never uses it.
    """
    from mdx.redis_stream_broker import (
        require_redis_db, require_redis_endpoint, require_redis_port,
        resolve_redis_config,
    )
    from mdx.transport.secrets import resolve_secret

    try:
        merged = resolve_redis_config(config, section_name, override=override)
        require_redis_endpoint(merged, selected_by)
        require_redis_port(merged.get("port"))
        require_redis_db(merged.get("db"))
        # For the raise, not the value: a `password_file` that names an unmounted
        # path, or a `password_env` naming an unset variable, is refused here
        # rather than at the first command's NOAUTH. The secret itself is read
        # again where the client is built and is deliberately not carried out of
        # this function.
        resolve_secret(merged, "password")
    except ValueError as exc:
        logger.error("%s", exc)
        return False
    return True


def _validate_vlm_enhanced_sink(config: Dict[str, Any]) -> bool:
    """Report whether the terminal sink's own transport config holds up.

    The terminal sink is selected separately from the event bridge's — see
    :func:`_warn_on_split_transports` — so its Redis settings are its own and
    are not reached by the loop over event-bridge roles. Unchecked here, they
    were checked where that sink is constructed: inside the pipeline child, after
    the API child, the metrics port and the fork.

    The name is checked first, and against the terminal sink's own vocabulary.
    Read through the event bridge's table instead -- which has no Elasticsearch
    entry, that being something only a terminal sink can be -- every value it
    did not recognize resolved to ``None`` and was waved through as "not Redis".
    So ``type: mongo`` passed validation and failed in the forked child, and
    ``type: elasticsearc`` did the same while looking like the default.

    Past the name, only the redisStream selection has anything to check.
    Elasticsearch is the default and Kafka reuses the broker config the event
    bridge already validated, so both fall through untouched.
    """
    configured = (config.get('vlm_enhanced_sink') or {}).get('type')
    try:
        resolved = require_terminal_sink_type(configured)
    except ValueError as exc:
        logger.error("%s", exc)
        return False

    if resolved != REDIS_STREAM:
        return True

    from mdx.sink.vlm_enhanced_sink.sink_redis_stream import resolve_routes

    try:
        resolve_routes(config)
    except ValueError as exc:
        logger.error("%s", exc)
        return False

    # Resolved the way the sink resolves it, through the same overlay rule, so a
    # per-sink `redisStream` block naming a different instance is judged as that
    # sink will read it rather than as the event bridge's connection.
    return _validate_redis_connection(
        config, None, 'vlm_enhanced_sink.type',
        override=(config.get('vlm_enhanced_sink') or {}).get('redisStream') or {},
    )


def _warn_on_split_transports(config: Dict[str, Any], sink_type: Optional[str]) -> None:
    """Point out an error sink on a different broker than the terminal sink.

    Two sinks are constructed in every deployment: this one, which carries
    validation-error responses, and ``vlm_enhanced_sink``, which carries
    verified results. They are selected independently and that is deliberate —
    results to Redis with errors to Elasticsearch is a reasonable shape.

    What is not reasonable is reaching it by accident. ``sinkType`` defaults to
    ``kafka``, so an operator who sets only ``vlm_enhanced_sink.type:
    redisStream`` gets a Kafka error sink they did not ask for, and in a
    deployment with no Kafka their validation errors go nowhere. Nothing raises,
    because the two sinks legitimately differ; this is the line that lets an
    operator see it in the log they are already reading at boot.
    """
    configured_vlm = ((config.get('vlm_enhanced_sink') or {}).get('type') or 'elastic')
    # Read against the event-bridge vocabulary rather than the terminal sink's,
    # which is what makes an Elasticsearch terminal sink resolve to nothing here
    # and pass without a warning: it has no event-bridge equivalent, so pairing
    # it with a Kafka error sink is the default deployment, not a mismatch.
    vlm_transport = _normalize_transport(configured_vlm)
    if vlm_transport is None or vlm_transport == sink_type:
        return
    logger.warning(
        "Event bridge sink is '%s' but VLM-enhanced results go to '%s'. "
        "Validation-error responses and verified results will use different "
        "transports; set event_bridge.sinkType explicitly if that is not "
        "intended.",
        sink_type, configured_vlm,
    )


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

            # Both spellings: the configured value is what an operator can grep
            # for in their config or Helm values, the resolved one is what
            # actually picked the implementation. Logging only the raw string
            # hides alias and casing mistakes behind a plausible-looking line.
            logger.info(
                "Creating source: configured %r resolved to %r", configured, source_type
            )

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

            logger.info(
                "Creating sink: configured %r resolved to %r", configured, sink_type
            )
            _warn_on_split_transports(config, sink_type)

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
                if not required:
                    continue
                section = event_bridge.get(required)
                if not section:
                    logger.error(
                        "%s selected as the %s but event_bridge.%s is missing or empty",
                        transport, role, required,
                    )
                    return False
                if not isinstance(section, dict):
                    logger.error(
                        "event_bridge.%s must be a mapping, got %s",
                        required, type(section).__name__,
                    )
                    return False

                if not _validate_redis_streams(required, section):
                    return False

                if not _validate_redis_connection(
                    config, required, f"event_bridge.{role}Type",
                ):
                    return False

                for key in _REQUIRED_SECTION_KEYS.get(required, ()):
                    value = section.get(key)
                    if not (value.strip() if isinstance(value, str) else value):
                        logger.error(
                            "event_bridge.%s.%s is required when %s is the %s",
                            required, key, transport, role,
                        )
                        return False

            # Independent of the loop above, because the terminal sink's
            # transport is selected independently of the event bridge's.
            if not _validate_vlm_enhanced_sink(config):
                return False

            logger.info("Event bridge configuration validation passed")
            return True

        except Exception as e:
            logger.error(f"Configuration validation failed: {e}")
            return False
