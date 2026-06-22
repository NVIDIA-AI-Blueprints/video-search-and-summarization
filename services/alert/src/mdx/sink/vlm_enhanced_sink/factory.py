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
from typing import Any, Dict

from .sink_base import VLMEnhancedSink
from .sink_elastic import VLMEnhancedElasticSink
from .sink_kafka import VLMEnhancedKafkaSink


logger = logging.getLogger(__name__)


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

    ``alert_config_store`` must be the same store the verification API
    writes through (built from ``event_bridge.redis_source``) so
    output_category PUT edits hot-reload. We do NOT derive it from
    ``redis_handler`` because that one reads top-level ``redis`` and
    can target a different backend.
    """

    sink_root = config.get("vlm_enhanced_sink", {}) or {}
    sink_type = (sink_root.get("type") or "elastic").lower()

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

    raise ValueError(f"Unsupported vlm_enhanced_sink.type: {sink_type}")


