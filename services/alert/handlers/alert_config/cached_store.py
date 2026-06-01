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

"""Read-through / write-through cached alert config store.

Implements the behaviour described in the alert-config ES hydration:

* **ES = source of truth** — all writes hit ES first; ES errors propagate
  as 5xx to the caller so we never acknowledge a write that was not
  durably stored.
* **Redis = ephemeral cache** — writes update Redis on a best-effort basis
  after ES succeeds; cache failures are logged and swallowed.
* **In-memory snapshot = last-resort read fallback** — populated on every
  successful read/write so the service can still answer ``get`` even when
  both ES and Redis are unreachable.

Read path: Redis → ES → in-memory snapshot.
Write path: ES → Redis (best-effort) → memory snapshot.
"""

import logging
from typing import Any, Dict, Optional

from persistence.exceptions import PersistenceError

from .base import AlertConfigStoreABC

logger = logging.getLogger(__name__)


class CachedAlertConfigStore(AlertConfigStoreABC):
    """Composite: ES primary + Redis cache + in-memory fallback."""

    def __init__(
        self,
        primary: AlertConfigStoreABC,
        cache: AlertConfigStoreABC,
        memory: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        """
        Args:
            primary: Durable source of truth (ES).
            cache: Hot-path cache (Redis).
            memory: Mutable dict shared with the hydration step, keyed by
                normalised alert type. Used only when both primary and
                cache are unreachable.
        """
        self._primary = primary
        self._cache = cache
        self._memory: Dict[str, Dict[str, Any]] = memory if memory is not None else {}

    # ── Writes ───────────────────────────────────────────────────────

    def set(self, alert_type: str, data: Dict[str, Any]) -> bool:
        # ES first. A failure here must propagate — the caller should
        # see a 5xx instead of a silently-applied-to-cache write that
        # will evaporate on the next container restart.
        self._primary.set(alert_type, data)
        self._update_cache_best_effort("set", alert_type, data)
        self._memory[alert_type] = data
        return True

    def set_if_absent(self, alert_type: str, data: Dict[str, Any]) -> bool:
        created = self._primary.set_if_absent(alert_type, data)
        if not created:
            return False
        self._update_cache_best_effort("set", alert_type, data)
        self._memory[alert_type] = data
        return True

    def delete(self, alert_type: str) -> bool:
        deleted = self._primary.delete(alert_type)
        self._update_cache_best_effort("delete", alert_type, None)
        self._memory.pop(alert_type, None)
        return deleted

    # ── Reads ────────────────────────────────────────────────────────

    def get(
        self,
        alert_type: str,
        *,
        fallback_to_memory: bool = True,
    ) -> Optional[Dict[str, Any]]:
        # 1. Cache hit is the hot path.
        try:
            cached = self._cache.get(alert_type)
            if cached is not None:
                return cached
        except Exception:
            logger.warning(
                "Cache read failed for %s, falling through to ES", alert_type,
                exc_info=True,
            )

        # 2. Primary is the source of truth. A genuine None from ES means
        #    the record does not exist — we must not serve a stale in-memory
        #    copy in that case, so memory is only consulted after ES errors.
        try:
            data = self._primary.get(alert_type)
        except PersistenceError:
            if not fallback_to_memory:
                # The REST API path opts out of the memory fallback so
                # that a 503 surfaces the dual-outage instead of
                # quietly returning a stale snapshot to an operator
                # who's GET-ing precisely to detect the outage.
                logger.warning(
                    "Primary read failed for %s and memory fallback "
                    "disabled — surfacing PersistenceError",
                    alert_type,
                )
                raise
            logger.warning(
                "Primary read failed for %s, falling back to in-memory snapshot",
                alert_type, exc_info=True,
            )
            return self._memory.get(alert_type)

        if data is not None:
            self._update_cache_best_effort("set", alert_type, data)
            self._memory[alert_type] = data
        else:
            # Record genuinely absent in ES; make sure memory agrees.
            self._memory.pop(alert_type, None)
        return data

    def get_all(
        self,
        *,
        fallback_to_memory: bool = True,
    ) -> Dict[str, Dict[str, Any]]:
        # Always serve from the source of truth. Listing is rare and
        # must not drift when the cache is stale.
        try:
            data = self._primary.get_all()
        except PersistenceError:
            if not fallback_to_memory:
                logger.warning(
                    "Primary list failed and memory fallback disabled — "
                    "surfacing PersistenceError"
                )
                raise
            logger.warning(
                "Primary list failed, falling back to in-memory snapshot",
                exc_info=True,
            )
            return dict(self._memory)
        # Refresh the snapshot so later single-record fallbacks stay in sync.
        self._memory.clear()
        self._memory.update(data)
        return data

    # ── Internals ────────────────────────────────────────────────────

    def _update_cache_best_effort(
        self, op: str, alert_type: str, data: Optional[Dict[str, Any]]
    ) -> None:
        try:
            if op == "set" and data is not None:
                self._cache.set(alert_type, data)
            elif op == "delete":
                self._cache.delete(alert_type)
        except Exception:
            logger.warning(
                "Cache %s failed for %s (non-fatal)", op, alert_type,
                exc_info=True,
            )
