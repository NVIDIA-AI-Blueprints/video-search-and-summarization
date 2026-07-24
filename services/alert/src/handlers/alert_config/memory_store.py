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

"""In-process alert-config store (Redis-free replacement for the cache).

Two roles:

* **Cache layer** for the ES-primary composite (``CachedAlertConfigStore``)
  — with a finite ``ttl_seconds`` so a stale entry cannot outlive the
  configured window. Because Alert MS no longer shares a Redis cache
  across processes, the pipeline process and the REST-API process each
  keep their own in-process cache; ``ttl_seconds=0`` (the deployment
  default) makes the cache read-through so a config edit in the API
  process is picked up by the pipeline on the next publish (via ES, the
  source of truth). Raise ``ttl_seconds`` only if you can tolerate that
  much cross-process staleness in exchange for fewer ES reads.

* **Standalone durable-ish store** for the ``persistence.enabled: false``
  backward-compat mode (``ttl_seconds=None`` → entries never expire),
  seeded from ``alert_type_config.json`` at startup. This is per-process
  and non-durable, matching the "no external store" deployment shape.
"""

import threading
import time
from typing import Any, Dict, Optional

from .base import AlertConfigStoreABC
from .normalize import normalize_alert_type

# Alert-config cache occupancy metric (guarded; no-op when metrics disabled).
try:  # pragma: no cover - exercised indirectly
    from metrics import recorder as _metrics
except Exception:  # pragma: no cover
    _metrics = None


def _publish_occupancy(count: int) -> None:
    if _metrics is None:
        return
    try:
        _metrics.set_cache_occupancy("alert_config", count)
    except Exception:  # pragma: no cover - metrics must never break the store
        pass


class InMemoryAlertConfigStore(AlertConfigStoreABC):
    """Thread-safe dict-backed alert-config store with optional TTL."""

    def __init__(
        self,
        ttl_seconds: Optional[int] = None,
        clock=time.monotonic,
    ) -> None:
        """
        Args:
            ttl_seconds: ``None`` → entries never expire (durable store
                role). A positive integer → entries expire that many
                seconds after their last write (cache role). ``0`` →
                read-through: writes are recorded but every read reports a
                miss, so the composite always falls through to the ES
                source of truth (default for the cache role so config
                edits propagate across processes without Redis).
            clock: Injectable monotonic clock for deterministic tests.
        """
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._data: Dict[str, tuple[Dict[str, Any], Optional[float]]] = {}
        self._lock = threading.Lock()

    def _expire_at(self, now: float) -> Optional[float]:
        if self._ttl_seconds is None:
            return None
        return now + self._ttl_seconds

    def _is_expired(self, expire_at: Optional[float], now: float) -> bool:
        return expire_at is not None and expire_at <= now

    def set(self, alert_type: str, data: Dict[str, Any]) -> bool:
        key = normalize_alert_type(alert_type)
        now = self._clock()
        with self._lock:
            self._data[key] = (data, self._expire_at(now))
            count = len(self._data)
        _publish_occupancy(count)
        return True

    def set_if_absent(self, alert_type: str, data: Dict[str, Any]) -> bool:
        key = normalize_alert_type(alert_type)
        now = self._clock()
        with self._lock:
            item = self._data.get(key)
            if item is not None and not self._is_expired(item[1], now):
                return False
            self._data[key] = (data, self._expire_at(now))
            count = len(self._data)
        _publish_occupancy(count)
        return True

    def get(
        self,
        alert_type: str,
        *,
        fallback_to_memory: bool = True,
    ) -> Optional[Dict[str, Any]]:
        del fallback_to_memory  # no separate memory snapshot here
        key = normalize_alert_type(alert_type)
        now = self._clock()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            data, expire_at = item
            if self._is_expired(expire_at, now):
                del self._data[key]
                return None
            return data

    def get_all(
        self,
        *,
        fallback_to_memory: bool = True,
    ) -> Dict[str, Dict[str, Any]]:
        del fallback_to_memory
        now = self._clock()
        with self._lock:
            live = {
                key: data
                for key, (data, expire_at) in self._data.items()
                if not self._is_expired(expire_at, now)
            }
            # Opportunistically drop any expired entries we just skipped.
            for key in [k for k, (_, exp) in self._data.items() if self._is_expired(exp, now)]:
                del self._data[key]
            return live

    def delete(self, alert_type: str) -> bool:
        key = normalize_alert_type(alert_type)
        with self._lock:
            removed = self._data.pop(key, None) is not None
            count = len(self._data)
        _publish_occupancy(count)
        return removed
