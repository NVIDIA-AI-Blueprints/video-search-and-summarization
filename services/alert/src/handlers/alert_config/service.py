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

"""Domain service for alert configuration CRUD.

Owns timestamps and deep-merge semantics for partial updates and is
callable from any context (REST handler, CLI, tests). The store layer
remains a dumb persistence adapter.

``alert_config:*`` is the single source of truth for prompts, system
prompts, enrichment prompts, vlm_params, and output_category — the
legacy ``prompts:*`` mirror has been retired.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .base import AlertConfigStoreABC
from .normalize import normalize_alert_type

logger = logging.getLogger(__name__)


# Sentinel separating "field omitted from request" from "field explicitly
# set to null". Pydantic's Optional[str]=None collapses both cases, so
# callers wanting PATCH semantics (None == clear) must pass ``None``
# explicitly while leaving omitted fields as ``_UNSET``.
class _Unset:
    def __repr__(self) -> str:  # pragma: no cover - debug only
        return "<UNSET>"


_UNSET: Any = _Unset()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class AlertConfigAlreadyExists(Exception):
    """Raised when create is called for an alert_type that already exists."""


class AlertConfigNotFound(Exception):
    """Raised when get/update/delete targets a missing alert_type."""


class AlertConfigService:
    """Business-rule layer between REST routes and the Redis store."""

    # Field whitelist for write/update so we never silently drop new fields
    # when callers rename / re-shape the schema.
    _UPDATABLE_FIELDS = (
        "prompt",
        "system_prompt",
        "enrichment_prompt",
        "vlm_params",
        "output_category",
    )

    def __init__(self, store: AlertConfigStoreABC):
        self._store = store

    # ── CRUD ──────────────────────────────────────────────────────────

    def create(
        self,
        alert_type: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        enrichment_prompt: Optional[str] = None,
        vlm_params: Optional[Dict[str, Any]] = None,
        output_category: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized = normalize_alert_type(alert_type)
        now = _utc_now_iso()
        data = {
            "alert_type": normalized,
            "prompt": prompt,
            "system_prompt": system_prompt,
            "enrichment_prompt": enrichment_prompt,
            "vlm_params": vlm_params,
            "output_category": output_category,
            "created_at": now,
            "updated_at": now,
        }
        if not self._store.set_if_absent(normalized, data):
            raise AlertConfigAlreadyExists(
                f"Config already exists for alert type: {normalized}"
            )
        return self._store.get(normalized) or data

    def update(
        self,
        alert_type: str,
        prompt: Any = _UNSET,
        system_prompt: Any = _UNSET,
        enrichment_prompt: Any = _UNSET,
        vlm_params: Any = _UNSET,
        output_category: Any = _UNSET,
    ) -> Dict[str, Any]:
        """Patch an existing alert config.

        Pass ``_UNSET`` (default) to leave a field untouched and pass
        ``None`` to clear it. Any non-``_UNSET`` value overwrites the
        stored entry — ``vlm_params`` is the only exception; a dict is
        deep-merged with the existing record so callers do not have to
        re-send every key on each update.
        """
        normalized = normalize_alert_type(alert_type)
        # Strict read on the existence check too — see ``get`` comment.
        existing = self._store.get(normalized, fallback_to_memory=False)
        if not existing:
            raise AlertConfigNotFound(
                f"No config found for alert type: {normalized}"
            )

        if prompt is not _UNSET:
            existing["prompt"] = prompt
        if system_prompt is not _UNSET:
            existing["system_prompt"] = system_prompt
        if enrichment_prompt is not _UNSET:
            existing["enrichment_prompt"] = enrichment_prompt
        if vlm_params is not _UNSET:
            if vlm_params is None:
                existing["vlm_params"] = None
            else:
                current_vlm = existing.get("vlm_params") or {}
                current_vlm.update(vlm_params)
                existing["vlm_params"] = current_vlm
        if output_category is not _UNSET:
            existing["output_category"] = output_category
        existing["updated_at"] = _utc_now_iso()

        # store.set() raises AlertConfigStoreError on Redis failure, which
        # bubbles up to the route handler and surfaces as 500.
        self._store.set(normalized, existing)
        return self._store.get(normalized) or existing

    def get(self, alert_type: str) -> Dict[str, Any]:
        normalized = normalize_alert_type(alert_type)
        # The REST API path must NOT silently swap in a stale memory
        # snapshot when both ES and Redis are down — operators GET-ing
        # to debug an outage need a hard 503, not a 200 with whatever
        # this process happened to cache last. Sink and prompt-handler
        # paths still call ``store.get`` directly with the default
        # ``fallback_to_memory=True`` so the publish flow stays up.
        data = self._store.get(normalized, fallback_to_memory=False)
        if not data:
            raise AlertConfigNotFound(
                f"No config found for alert type: {normalized}"
            )
        return data

    def list_all(self) -> List[Dict[str, Any]]:
        # Same strict-read semantics as ``get`` — see comment there.
        return list(self._store.get_all(fallback_to_memory=False).values())

    def delete(self, alert_type: str) -> None:
        normalized = normalize_alert_type(alert_type)
        if not self._store.get(normalized, fallback_to_memory=False):
            raise AlertConfigNotFound(
                f"No config found for alert type: {normalized}"
            )
        # store.delete() returns False only when the key disappears between
        # the existence check and the actual delete — treat that as success
        # since the caller's intent (the key is gone) is satisfied. Backend
        # failures bubble up as AlertConfigStoreError so the route handler
        # surfaces them as 500 instead of pretending the delete succeeded.
        self._store.delete(normalized)
