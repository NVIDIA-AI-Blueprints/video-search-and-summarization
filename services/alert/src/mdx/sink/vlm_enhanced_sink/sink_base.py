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

from __future__ import annotations

import copy
import os
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Union

from utils.event_utils import is_alert
logger = logging.getLogger(__name__)

VLMResponsePayload = Union[str, Dict[str, Any]]


def parse_vlm_response(raw: VLMResponsePayload) -> Dict[str, Any]:
    """Normalize raw VLM responses to a dictionary."""

    if isinstance(raw, dict):
        return raw

    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("Unable to parse VLM response JSON; using raw string")

    return {"raw": raw}


def build_vlm_enriched_event(
    incident: Dict[str, Any],
    user_prompt: str,
    system_prompt: Optional[str],
    raw_vlm_response: VLMResponsePayload,
) -> Dict[str, Any]:
    """Return a deep copy of the input event enriched with VLM summary fields."""

    enriched = copy.deepcopy(incident)

    # If caller already populated an 'info' block (e.g., AlertBridgeResponse), do not modify it here.
    existing_info = enriched.get("info")
    if isinstance(existing_info, dict) and existing_info:
        return enriched

    # Do not populate an 'info' block when absent; the orchestrator is
    # responsible for setting AlertBridgeResponse. Return the event unchanged.
    return enriched


def log_enriched_event(
    logger: logging.Logger,
    sink_type: str,
    incident_id: Optional[str],
    document: Dict[str, Any],
) -> None:
    if not logger.isEnabledFor(logging.DEBUG):
        return

    verbose_sink = os.getenv('LOG_VERBOSE_SINK', 'false').lower() in ('1', 'true', 'yes')
    if verbose_sink:
        try:
            from utils.logging_helpers import redact_payload_for_log
            compact_redacted = redact_payload_for_log(document)
        except Exception:
            compact_redacted = "<unavailable>"
        logger.debug(
            "%s payload prepared for publish %s",
            sink_type,
            compact_redacted,
            extra={"incident_id": incident_id},
        )
    else:
        try:
            size_bytes = len(json.dumps(document, separators=(",", ":")))
        except Exception:
            size_bytes = -1
        logger.debug(
            "%s payload prepared for publish (size_bytes=%s)",
            sink_type,
            size_bytes,
            extra={"incident_id": incident_id},
        )


class VLMEnhancedSink(ABC):
    """Abstract base sink: public API and shared helpers. Concrete sinks are transport-specific."""

    def __init__(
        self,
        alert_config_store: Any = None,
        category_mapping: Optional[Dict[str, str]] = None,
    ) -> None:
        self._logger = logging.getLogger(self.__class__.__name__)
        # Live source of truth for output_category. When set, every
        # publish re-reads it so PUT API edits hot-reload.
        self._alert_config_store = alert_config_store
        # Fallback mapping (file-loaded at startup) used when no live
        # store is wired or the record predates the field.
        self._category_mapping: Dict[str, str] = category_mapping or {}

    def _resolve_output_category(self, original_category: Optional[str]) -> Optional[str]:
        """Return the configured ``output_category`` for ``original_category``.

        Store wins when present (key in dict, even with ``None`` value)
        — ``None`` means the operator cleared the override via PUT, and
        falling back to the file mapping would silently resurrect it.
        Store absent or record predates the field ⇒ static fallback.
        """
        if not original_category:
            return None
        if self._alert_config_store is not None:
            try:
                config = self._alert_config_store.get(original_category)
            except Exception as exc:
                self._logger.debug(
                    "alert_config_store lookup failed for '%s': %s",
                    original_category, exc,
                )
                config = None
            if isinstance(config, dict) and "output_category" in config:
                return config.get("output_category") or None
        return self._category_mapping.get(original_category)

    def publish_success(
        self,
        message: Dict[str, Any],
        user_prompt: str,
        system_prompt: Optional[str],
        vlm_response: VLMResponsePayload,
        latency: Optional[Dict[str, Any]] = None,
    ) -> None:
        latency = latency or {}
        event_kind = 'alert' if is_alert(message) else 'incident'
        # Preserve downstream hint
        message.setdefault('type', 'mdx-vlm-alerts' if event_kind == 'alert' else 'mdx-vlm-incidents')
        enriched = build_vlm_enriched_event(
            message,
            user_prompt,
            system_prompt,
            vlm_response,
        )
       
        self._store_success(event_kind, enriched, vlm_response, user_prompt)

    def publish_error(
        self,
        message: Dict[str, Any],
        user_prompt: str,
        system_prompt: Optional[str],
        error_payload: Dict[str, Any],
        latency: Optional[Dict[str, Any]] = None,
    ) -> None:
        latency = latency or {}
        event_kind = 'alert' if is_alert(message) else 'incident'
        # Preserve downstream hint
        message.setdefault('type', 'mdx-vlm-alerts' if event_kind == 'alert' else 'mdx-vlm-incidents')
        enriched = build_vlm_enriched_event(
            message,
            user_prompt,
            system_prompt,
            error_payload,
        )
       
        self._store_error(event_kind, enriched, error_payload)

    @abstractmethod
    def _store_success(
        self,
        event_kind: str,
        document: Dict[str, Any],
        raw_vlm_response: VLMResponsePayload,
        user_prompt: str,
    ) -> None:
        ...

    @abstractmethod
    def _store_error(
        self,
        event_kind: str,
        document: Dict[str, Any],
        error_payload: Dict[str, Any],
    ) -> None:
        ...

