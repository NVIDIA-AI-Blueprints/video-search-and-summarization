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

import json
import logging
from typing import Any, Dict, Optional

from elastic.elastic import ElasticClient, ElasticConfig
from its_redis.redis_handler import RedisHandler
from models.responses import EnrichmentResponse

from .sink_base import (
    VLMEnhancedSink,
    VLMResponsePayload,
    log_enriched_event,
)


class VLMEnhancedElasticSink(VLMEnhancedSink):
    def __init__(
        self,
        elastic_client: ElasticClient,
        incident_index: str,
        alert_index: str,
        redis_handler: "RedisHandler | None" = None,
        category_mapping: Optional[Dict[str, str]] = None,
        verdict_description_mapping: Optional[Dict[str, Dict[str, str]]] = None,
        alert_config_store: Any = None,
    ) -> None:
        super().__init__(
            alert_config_store=alert_config_store,
            category_mapping=category_mapping,
        )
        self._elastic = elastic_client
        self._incident_index = incident_index
        self._alert_index = alert_index
        # Use provided RedisHandler or None (verdict protection disabled if None)
        self._redis_handler = redis_handler
        # verdict_description is not API-managed; stays file-loaded.
        self._verdict_description_mapping = verdict_description_mapping or {}

    @classmethod
    def from_config(
        cls,
        config: Dict[str, Any],
        redis_handler: "RedisHandler | None" = None,
        category_mapping: Optional[Dict[str, str]] = None,
        verdict_description_mapping: Optional[Dict[str, Dict[str, str]]] = None,
        alert_config_store: Any = None,
    ) -> "VLMEnhancedElasticSink":
        """Construct sink by parsing config and creating the Elastic client internally."""
        sink_root = config.get("vlm_enhanced_sink", {}) or {}

        # Require per-sink index configuration; no fallback
        elastic_cfg = config.get("elastic", {}) or {}
        incident_cfg = (sink_root.get("incident") or {}).get("elastic") or {}
        alert_cfg = (sink_root.get("alert") or {}).get("elastic") or {}

        incident_index = incident_cfg.get("index")
        alert_index = alert_cfg.get("index")

        if not incident_index:
            raise ValueError("Missing required configuration: vlm_enhanced_sink.incident.elastic.index")
        if not alert_index:
            raise ValueError("Missing required configuration: vlm_enhanced_sink.alert.elastic.index")

        # Build Elastic client
        if not elastic_cfg.get("enabled", False):
            raise ValueError("Elastic sink requested but elastic.enabled is false")
        hosts_config = elastic_cfg.get("hosts")
        if isinstance(hosts_config, str):
            hosts = (hosts_config,)
        elif isinstance(hosts_config, (list, tuple)):
            hosts = tuple(str(h).strip() for h in hosts_config if h)
        else:
            hosts = tuple()
        if not hosts:
            raise ValueError("Elastic sink requested but no hosts configured")
        client = ElasticClient(config=ElasticConfig(hosts=hosts))
        return cls(
            elastic_client=client,
            incident_index=incident_index,
            alert_index=alert_index,
            redis_handler=redis_handler,
            category_mapping=category_mapping,
            verdict_description_mapping=verdict_description_mapping,
            alert_config_store=alert_config_store,
        )

    def _build_runtime_category_mapping(
        self,
        document: Dict[str, Any],
    ) -> Dict[str, str]:
        """Build a single-entry mapping for ``write_event_response``.

        ``write_event_response`` applies category overrides after the
        fingerprint is computed, so we still pass a dict-shaped argument
        rather than pre-mutating ``document``. The dict only carries the
        one entry that's relevant for this publish, sourced from the
        live ``alert_config_store`` when available.
        """
        original = document.get("category")
        resolved = self._resolve_output_category(original)
        if resolved and resolved != original:
            return {original: resolved}
        return {}

    def _store_success(
        self,
        event_kind: str,
        document: Dict[str, Any],
        raw_vlm_response: VLMResponsePayload,
        user_prompt: str,
    ) -> None:
        index = self._alert_index if event_kind == 'alert' else self._incident_index
        self._logger.info("Publishing to Elastic [sensor=%s category=%s start=%s] index=%s",
                          document.get('sensorId', 'N/A'), document.get('category', 'N/A'),
                          document.get('timestamp', 'N/A'), index)
        try:
            self._elastic.write_event_response(
                document,
                raw_vlm_response,
                user_prompt,
                index,
                # Resolved per-event so PUT API edits to ``output_category``
                # take effect on the next publish without a restart.
                # ``write_event_response`` applies the mapping after the
                # fingerprint is computed, so we don't mutate ``document``
                # here.
                category_mapping=self._build_runtime_category_mapping(document),
                verdict_description_mapping=self._verdict_description_mapping,
            )
        except Exception:
            self._logger.error("Failed to publish to Elastic [sensor=%s category=%s start=%s]",
                               document.get('sensorId', 'N/A'), document.get('category', 'N/A'),
                               document.get('timestamp', 'N/A'), exc_info=True)
            return

        # ─── Mark confirmed after successful write ───
        fingerprint = document.get("Id")
        verdict = (document.get("info", {}).get("verdict") or "").lower()
        if self._redis_handler and fingerprint and verdict == "confirmed":
            self._redis_handler.mark_verdict_confirmed(fingerprint)
        # ─────────────────────────────────────────────

        log_enriched_event(self._logger, "Elastic", document.get("id"), document)

    def _store_error(
        self,
        event_kind: str,
        document: Dict[str, Any],
        error_payload: Dict[str, Any],
    ) -> None:
        index = self._alert_index if event_kind == 'alert' else self._incident_index
        try:
            self._elastic.write_event_response(
                document,
                error_payload,
                document.get("info", {}).get("user_prompt"),
                index,
                category_mapping=self._build_runtime_category_mapping(document),
                verdict_description_mapping=self._verdict_description_mapping,
            )
        except Exception:
            self._logger.error(
                "Failed to publish VLM-enhanced event to Elastic",
                extra={"incident_id": document.get("id")},
                exc_info=True,
            )
            return
        log_enriched_event(self._logger, "Elastic", document.get("id"), document)

    def update_enrichment(
        self,
        document: Dict[str, Any],
        enrichment_response: EnrichmentResponse,
    ) -> None:
        """
        Update an existing document with enrichment data.
        
        Args:
            document: The original document (must have 'Id' and 'timestamp' for index lookup)
            enrichment_response: The enrichment response to add
        """
        from utils.event_utils import is_alert
        
        event_kind = 'alert' if is_alert(document) else 'incident'
        base_index = self._alert_index if event_kind == 'alert' else self._incident_index
        
        # Get document ID (fingerprint)
        doc_id = document.get("Id")
        if not doc_id:
            self._logger.warning("Cannot update enrichment: document has no Id field")
            return
        
        # Generate daily index name
        timestamp_value = document.get("timestamp", "")
        if not isinstance(timestamp_value, str):
            timestamp_value = str(timestamp_value)
        daily_index = self._elastic.generate_daily_index_name(base_index, timestamp_value)
        
        partial_doc = {
            "info": {
                "enrichment": json.dumps(
                    enrichment_response.model_dump(), separators=(',', ':'),
                )
            }
        }
        
        try:
            self._elastic.update_document(
                index=daily_index,
                doc_id=doc_id,
                partial_doc=partial_doc,
            )
            self._logger.info(
                "Updated document with enrichment [sensor=%s category=%s] index=%s",
                document.get('sensorId', 'N/A'),
                document.get('category', 'N/A'),
                daily_index,
            )
        except Exception:
            self._logger.error(
                "Failed to update document with enrichment [sensor=%s category=%s]",
                document.get('sensorId', 'N/A'),
                document.get('category', 'N/A'),
                exc_info=True,
            )
