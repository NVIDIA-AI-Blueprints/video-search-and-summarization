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

"""Redis JSON persistence for alert_config:{alert_type} keys.

This class is a dumb persistence adapter — it does not own timestamps,
validation, prompt-sync or any other business rules. Service / route
layers compose this with domain logic.
"""

import logging
from typing import Any, Dict, Optional

import redis

from .base import AlertConfigStoreABC
from .normalize import normalize_alert_type

logger = logging.getLogger(__name__)


class AlertConfigStoreError(Exception):
    """Raised when the underlying Redis backend rejects a request.

    Distinct from existence conflicts so callers can map infrastructure
    failures to 5xx responses instead of 4xx.
    """


class RedisAlertConfigStore(AlertConfigStoreABC):
    """RedisJSON-backed CRUD for alert type configurations."""

    KEY_PREFIX = "alert_config:"
    DEFAULT_CACHE_TTL_SECONDS = 3600

    def __init__(
        self,
        redis_client: redis.Redis,
        cache_ttl_seconds: Optional[int] = DEFAULT_CACHE_TTL_SECONDS,
    ):
        """
        Args:
            redis_client: The Redis client.
            cache_ttl_seconds: Expire every cache entry this many seconds
                after the most recent write. ``CachedAlertConfigStore``
                makes the Redis layer best-effort on writes (errors are
                swallowed and logged), so a transient ``DEL`` failure on
                the DELETE path leaves a stale entry behind that
                ``cached_store.get`` would return for every subsequent
                call — cache hits short-circuit the ES read. A finite
                TTL bounds that divergence window: even if the cache
                eviction is dropped, the orphan key auto-expires and
                the next read falls through to ES (the source of
                truth). Pass ``None`` to disable expiry (test-only).
        """
        self._redis = redis_client
        self._cache_ttl_seconds = cache_ttl_seconds

    def _key(self, alert_type: str) -> str:
        return f"{self.KEY_PREFIX}{normalize_alert_type(alert_type)}"

    def _refresh_ttl(self, key: str) -> None:
        """Stamp / refresh the per-key TTL. Best-effort: an EXPIRE
        failure here is logged but not fatal — the value is already
        committed to Redis, and the next successful write will
        re-stamp the TTL anyway.
        """
        if not self._cache_ttl_seconds:
            return
        try:
            self._redis.expire(key, self._cache_ttl_seconds)
        except Exception as exc:
            logger.warning(
                "AlertConfigStore: failed to set TTL on %s (non-fatal): %s",
                key, exc,
            )

    def set(self, alert_type: str, data: Dict[str, Any]) -> bool:
        """Unconditional write. Caller owns timestamps and merge semantics.

        Raises ``AlertConfigStoreError`` on Redis failure so upstream
        services do not confuse infrastructure outages with normal
        ``False`` outcomes.
        """
        key = self._key(alert_type)
        try:
            self._redis.json().set(key, "$", data)
            self._refresh_ttl(key)
            return True
        except Exception as exc:
            logger.error("AlertConfigStore.set(%s) failed: %s", alert_type, exc)
            raise AlertConfigStoreError(str(exc)) from exc

    def set_if_absent(self, alert_type: str, data: Dict[str, Any]) -> bool:
        """Atomic create via Redis ``SET NX``.

        Returns ``True`` when the key was created and ``False`` when the
        key already exists. Backend failures raise
        ``AlertConfigStoreError`` so they are not silently mapped to
        existence conflicts.
        """
        key = self._key(alert_type)
        try:
            result = self._redis.json().set(key, "$", data, nx=True)
            if result:
                self._refresh_ttl(key)
            return bool(result)
        except Exception as exc:
            logger.error("AlertConfigStore.set_if_absent(%s) failed: %s", alert_type, exc)
            raise AlertConfigStoreError(str(exc)) from exc

    def get(
        self,
        alert_type: str,
        *,
        fallback_to_memory: bool = True,
    ) -> Optional[Dict[str, Any]]:
        # ``fallback_to_memory`` is part of the ABC for symmetry with
        # ``CachedAlertConfigStore`` but has no effect here — Redis-only
        # stores have no memory snapshot to fall back to.
        del fallback_to_memory
        try:
            data = self._redis.json().get(self._key(alert_type))
            return data if data else None
        except Exception as exc:
            logger.error("AlertConfigStore.get(%s) failed: %s", alert_type, exc)
            return None

    def get_all(
        self,
        *,
        fallback_to_memory: bool = True,
    ) -> Dict[str, Dict[str, Any]]:
        del fallback_to_memory  # unused; see ``get``
        try:
            keys = self._redis.keys(f"{self.KEY_PREFIX}*")
            configs: Dict[str, Dict[str, Any]] = {}
            for key in keys:
                key_str = key.decode("utf-8") if isinstance(key, bytes) else key
                data = self._redis.json().get(key_str)
                if not data:
                    continue
                alert_type = key_str.split(":", 1)[1] if ":" in key_str else key_str
                configs[alert_type] = data
            return configs
        except Exception as exc:
            logger.error("AlertConfigStore.get_all failed: %s", exc)
            return {}

    def delete(self, alert_type: str) -> bool:
        """Remove an alert config record.

        Returns ``True`` when Redis confirms the key was deleted and
        ``False`` when the key was already absent. Backend failures raise
        ``AlertConfigStoreError`` so callers do not mistake an outage for
        a successful no-op.
        """
        try:
            return bool(self._redis.json().delete(self._key(alert_type)))
        except Exception as exc:
            logger.error("AlertConfigStore.delete(%s) failed: %s", alert_type, exc)
            raise AlertConfigStoreError(str(exc)) from exc
