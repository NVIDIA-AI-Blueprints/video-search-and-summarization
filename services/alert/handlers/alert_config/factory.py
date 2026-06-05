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

"""Single entry point for assembling the alert-config storage backend.

Call sites (REST route wire-up, ``PromptManager``, etc.) previously
constructed their own store instances, which left the file-seeding
path bypassing the persistence layer — see the alert-config ES hydration for the
resulting drift between list and get-by-id. This factory centralises
the choice so every consumer ends up with the same composite.
"""

import logging
from typing import Any, Dict, Optional

import redis

from persistence import create_persistence_store

from .base import AlertConfigStoreABC
from .cached_store import CachedAlertConfigStore
from .es_store import ESAlertConfigStore
from .hydration import hydrate_cache
from .store import RedisAlertConfigStore

logger = logging.getLogger(__name__)


def build_alert_config_store(
    redis_client: redis.Redis,
    app_config: Dict[str, Any],
    *,
    hydrate: bool = True,
) -> AlertConfigStoreABC:
    """Return a ready-to-use alert-config store.

    When the ``persistence`` section of ``app_config`` is enabled and the
    Elasticsearch backend is healthy, the result is the cached composite
    from the alert-config ES hydration (ES primary, Redis cache, in-memory fallback,
    hydrated up front). When persistence is disabled — or the factory
    function returns ``None`` because no hosts are configured — we fall
    back to the Redis-only store so existing deployments continue to
    work unchanged.

    Args:
        redis_client: Already-initialised Redis client shared with the
            rest of the process.
        app_config: Parsed ``config.yaml`` as a dict. Must contain the
            standard ``persistence`` and ``elastic`` sections for the
            persistence path to activate.
        hydrate: When ``True`` (default), pre-populate the Redis cache
            and in-memory snapshot from ES before returning. Callers
            that intentionally defer hydration (e.g. the migration
            script that writes *into* ES) can pass ``False``.

    Raises:
        RuntimeError: Persistence is enabled but Elasticsearch is not
            reachable. Fail-fast semantics — we refuse to
            serve writes with a degraded backend because the
            "ES is source of truth" invariant would be violated.
    """
    persistence = create_persistence_store(app_config)
    if persistence is None:
        # Backward-compat mode: Redis is the source of truth, NOT a
        # cache. Applying a TTL here would silently expire operator
        # configs after the cache window — turning a feature meant
        # to bound *cache* staleness into a data-loss bug for any
        # deployment still on ``persistence.enabled: false``.
        logger.info(
            "Persistence layer disabled or unavailable — using Redis-only "
            "alert config store (backward-compat mode); TTL disabled to "
            "treat Redis as durable storage."
        )
        return RedisAlertConfigStore(redis_client, cache_ttl_seconds=None)

    if not persistence.health():
        raise RuntimeError(
            "Persistence layer enabled but Elasticsearch is unreachable; "
            "refusing to build alert config store with a degraded backend "
            "(see alert-config ES hydration startup semantics)."
        )

    # Cached composite mode: ES is source of truth, Redis is the cache
    # layer — TTL caps how long a stale entry can live if the cache
    # invalidation on DELETE is dropped (transient Redis blip after a
    # successful ES delete). Operator can tune via
    # ``persistence.cache_ttl_seconds`` — default 1h matches the
    # alert-config write cadence (low) and the maximum acceptable
    # divergence from the source of truth.
    persistence_cfg = app_config.get("persistence") or {}
    cache_ttl = persistence_cfg.get(
        "cache_ttl_seconds",
        RedisAlertConfigStore.DEFAULT_CACHE_TTL_SECONDS,
    )
    redis_store = RedisAlertConfigStore(redis_client, cache_ttl_seconds=cache_ttl)

    es_store = ESAlertConfigStore(persistence)
    memory_snapshot: Dict[str, Dict[str, Any]] = {}
    if hydrate:
        count = hydrate_cache(es_store, redis_store, memory_snapshot)
        logger.info("Alert config store hydrated with %d records from ES", count)
    return CachedAlertConfigStore(
        primary=es_store, cache=redis_store, memory=memory_snapshot,
    )
