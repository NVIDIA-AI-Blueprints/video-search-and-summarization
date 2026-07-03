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

Elasticsearch is the source of truth. The previous Redis cache layer has
been removed: Alert MS no longer runs a Redis pod, so the hot-path cache
is an in-process store (``InMemoryAlertConfigStore``) instead of a
shared Redis instance. Because that cache is per-process — the pipeline
process and the REST-API process are separate — the default cache TTL is
``0`` (read-through), so a config edit made through the API is picked up
by the pipeline on the next publish via ES. Operators who can tolerate
bounded cross-process staleness may set ``persistence.cache_ttl_seconds``
> 0 to cut ES read volume.
"""

import logging
from typing import Any, Dict

from persistence import create_persistence_store

from .base import AlertConfigStoreABC
from .cached_store import CachedAlertConfigStore
from .es_store import ESAlertConfigStore
from .hydration import hydrate_cache
from .memory_store import InMemoryAlertConfigStore

logger = logging.getLogger(__name__)

# Read-through by default so a config edit in the REST-API process is
# reflected in the pipeline process (via ES) without cross-process cache
# invalidation. This replaces the old shared-Redis 1h TTL.
DEFAULT_CACHE_TTL_SECONDS = 0


def build_alert_config_store(
    app_config: Dict[str, Any],
    *,
    hydrate: bool = True,
) -> AlertConfigStoreABC:
    """Return a ready-to-use alert-config store.

    * ``persistence.enabled: false`` → a per-process
      :class:`InMemoryAlertConfigStore` (no external dependency). Seeded
      from ``alert_type_config.json`` at startup; non-durable.
    * ``persistence`` enabled and ES healthy → the cached composite (ES
      primary + in-process cache + in-memory fallback), hydrated up front.
    * ``persistence`` enabled but ES unreachable → ``RuntimeError``
      (fail-fast; we refuse to serve writes with a degraded backend).

    Args:
        app_config: Parsed ``config.yaml`` as a dict.
        hydrate: When ``True`` (default), pre-populate the cache and
            in-memory snapshot from ES before returning.

    Raises:
        RuntimeError: Persistence is enabled but Elasticsearch is not
            reachable.
    """
    persistence = create_persistence_store(app_config)
    if persistence is None:
        logger.info(
            "Persistence layer disabled or unavailable — using in-process "
            "alert config store (backward-compat mode, non-durable)."
        )
        return InMemoryAlertConfigStore(ttl_seconds=None)

    if not persistence.health():
        raise RuntimeError(
            "Persistence layer enabled but Elasticsearch is unreachable; "
            "refusing to build alert config store with a degraded backend."
        )

    persistence_cfg = app_config.get("persistence") or {}
    cache_ttl = persistence_cfg.get("cache_ttl_seconds", DEFAULT_CACHE_TTL_SECONDS)
    cache_store = InMemoryAlertConfigStore(ttl_seconds=cache_ttl)

    es_store = ESAlertConfigStore(persistence)
    memory_snapshot: Dict[str, Dict[str, Any]] = {}
    if hydrate:
        count = hydrate_cache(es_store, cache_store, memory_snapshot)
        logger.info("Alert config store hydrated with %d records from ES", count)
    return CachedAlertConfigStore(
        primary=es_store, cache=cache_store, memory=memory_snapshot,
    )
