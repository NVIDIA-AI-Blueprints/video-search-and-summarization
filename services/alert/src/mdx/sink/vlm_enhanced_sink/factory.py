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

"""Factory for constructing a single VLM enhanced sink per deployment."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .sink_base import VLMEnhancedSink
from .sink_console import VLMEnhancedConsoleSink
from .sink_elastic import VLMEnhancedElasticSink
from .sink_kafka import VLMEnhancedKafkaSink


logger = logging.getLogger(__name__)

#: Accepted spellings per sink type. Matching is case- and
#: separator-insensitive so ``redisStream``, ``redis_stream`` and
#: ``redis-stream`` all select the Redis Streams sink.
_SINK_ALIASES = {
    "elastic": "elastic",
    "elasticsearch": "elastic",
    "kafka": "kafka",
    "redisstream": "redisStream",
    "redis": "redisStream",
    "console": "console",
}


def _normalize_sink_type(value: Any) -> Optional[str]:
    """Resolve a configured sink type to its canonical name.

    Returns ``None`` when the value is not a recognized sink, matching
    ``event_bridge_factory._normalize_transport``. The two normalizers are kept
    on one contract deliberately: they read operator-supplied transport names
    from the same config file, and a reader who checks one should not have to
    re-derive how the other treats an unknown or non-string value.
    """
    if not isinstance(value, str):
        return None
    return _SINK_ALIASES.get(value.strip().lower().replace("_", "").replace("-", ""))


def _warn_on_per_kind_type(sink_root: Dict[str, Any], resolved: str) -> None:
    """Point out ``incident.type`` / ``alert.type`` keys, which are never read.

    One sink serves both kinds, so the transport comes from the top-level
    ``vlm_enhanced_sink.type`` alone. Configs carrying a per-kind ``type`` are
    common and predate the extra transports, and while it was only ever
    decoration it now actively misleads: a chart that renders ``type:
    redisStream`` at the top and a hardcoded ``incident.type: elastic`` below
    reads as though incidents still go to Elasticsearch. This is a warning
    rather than an error because those stale keys sit in working deployments,
    and rejecting them would break the very upgrade that selects Redis.
    """
    for kind in ("incident", "alert"):
        section = sink_root.get(kind)
        if not isinstance(section, dict) or "type" not in section:
            continue
        declared = _normalize_sink_type(section.get("type"))
        if declared == resolved:
            logger.debug(
                "Ignoring redundant vlm_enhanced_sink.%s.type; the transport comes "
                "from vlm_enhanced_sink.type", kind,
            )
        else:
            logger.warning(
                "vlm_enhanced_sink.%s.type is '%s' but is never read: both kinds use "
                "vlm_enhanced_sink.type, which resolved to '%s'. Remove the per-kind "
                "'type' key so the config stops contradicting itself.",
                kind, section.get("type"), resolved,
            )


def _load_category_mapping(config: Dict[str, Any]) -> Dict[str, str]:
    """Load output category mapping from alert type configuration.

    Returns:
        Dict mapping original category names to custom output names.
        Returns empty dict if loading fails or no mappings are configured.
    """
    try:
        from handlers.prompt_handler.alert_type_config_loader import AlertTypeConfigLoader
        alert_config_file = config.get('alert_type_config_file')
        loader = AlertTypeConfigLoader(alert_config_file)
        mapping = loader.get_output_category_mapping()
        if mapping:
            logger.info(f"Loaded {len(mapping)} custom category mapping(s): {list(mapping.keys())}")
        return mapping
    except Exception as e:
        logger.debug(f"No custom category mappings loaded: {e}")
        return {}


def _load_verdict_description_mapping(config: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """Load verdict description mapping from alert type configuration.

    Returns:
        Dict mapping category -> {verdict -> description}.
        Returns empty dict if loading fails or no mappings are configured.
    """
    try:
        from handlers.prompt_handler.alert_type_config_loader import AlertTypeConfigLoader
        alert_config_file = config.get('alert_type_config_file')
        loader = AlertTypeConfigLoader(alert_config_file)
        mapping = loader.get_verdict_description_mapping()
        if mapping:
            logger.info(f"Loaded verdict description mapping(s) for: {list(mapping.keys())}")
        return mapping
    except Exception as e:
        logger.debug(f"No verdict description mappings loaded: {e}")
        return {}


def build_vlm_enhanced_sink(
    config: Dict[str, Any],
    kind: str = "incident",
    redis_handler: Any = None,
    alert_config_store: Any = None,
) -> VLMEnhancedSink:
    """Instantiate a single VLMEnhancedSink for the configured transport.

    ``alert_config_store`` must be the same alert-config store the
    verification API writes through (the ES-backed store from
    ``handlers.alert_config.build_alert_config_store``) so output_category
    PUT edits hot-reload. It is passed in explicitly rather than derived
    from ``redis_handler`` — that handler only owns dedup/verdict-protection
    state, not the alert-config store.
    """

    sink_root = config.get("vlm_enhanced_sink", {}) or {}
    configured = sink_root.get("type") or "elastic"
    sink_type = _normalize_sink_type(configured)
    # Log both spellings: the configured value is what an operator can grep for
    # in their config, the resolved one is what actually selected the sink.
    logger.info(
        "VLM enhanced sink type: %r resolved to '%s'", configured, sink_type
    )
    if sink_type is None:
        # Raise before the per-kind warnings so an operator sees the actual
        # problem instead of advice about keys on a sink that never resolved.
        raise ValueError(
            f"Unsupported vlm_enhanced_sink.type: {configured!r} "
            "(supported: 'elastic', 'kafka', 'redisStream', 'console')"
        )
    _warn_on_per_kind_type(sink_root, sink_type)

    category_mapping = _load_category_mapping(config)
    verdict_description_mapping = _load_verdict_description_mapping(config)

    logger.info(
        "VLM enhanced sink output_category source: %s",
        "live AlertConfigStore" if alert_config_store is not None
        else "static file mapping only",
    )

    if sink_type == "elastic":
        return VLMEnhancedElasticSink.from_config(
            config,
            redis_handler=redis_handler,
            category_mapping=category_mapping,
            verdict_description_mapping=verdict_description_mapping,
            alert_config_store=alert_config_store,
        )

    if sink_type == "kafka":
        return VLMEnhancedKafkaSink.from_config(
            config,
            category_mapping=category_mapping,
            alert_config_store=alert_config_store,
        )

    if sink_type == "redisStream":
        # Imported here rather than at module scope so the `redis` package is
        # only required by deployments that actually select this transport.
        from .sink_redis_stream import VLMEnhancedRedisStreamSink

        return VLMEnhancedRedisStreamSink.from_config(
            config,
            category_mapping=category_mapping,
            alert_config_store=alert_config_store,
        )

    if sink_type == "console":
        return VLMEnhancedConsoleSink.from_config(
            config,
            category_mapping=category_mapping,
            alert_config_store=alert_config_store,
        )

    raise AssertionError(  # pragma: no cover - every resolved type is handled above
        f"Resolved sink type {sink_type!r} has no branch"
    )


