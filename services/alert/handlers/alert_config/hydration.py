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

"""Startup hydration: populate the Redis cache and in-memory snapshot
from the durable ES store before the service begins handling requests.

Required by the alert-config ES hydration:
    "Startup: hydrate Redis cache from Elasticsearch before serving requests"

Called once at wire-up time, not on the hot path.
"""

import logging
from typing import Any, Dict

from .base import AlertConfigStoreABC

logger = logging.getLogger(__name__)


def hydrate_cache(
    primary: AlertConfigStoreABC,
    cache: AlertConfigStoreABC,
    memory: Dict[str, Dict[str, Any]],
) -> int:
    """Copy every record from ``primary`` into ``cache`` and ``memory``.

    The in-memory snapshot doubles as the read-fallback when both ES and
    Redis are unavailable, so we fill it in the same pass.

    Args:
        primary: Durable store (ES). Must be reachable — call ``health()``
            upstream to fail-fast before invoking hydration.
        cache: Hot-path cache (Redis). Individual write failures here are
            non-fatal; they are logged but do not abort hydration.
        memory: Mutable dict shared with the cached store. Cleared and
            repopulated.

    Returns:
        The number of records hydrated.
    """
    records = primary.get_all()
    memory.clear()
    memory.update(records)

    cache_failures = 0
    for alert_type, data in records.items():
        try:
            cache.set(alert_type, data)
        except Exception:
            cache_failures += 1
            logger.warning(
                "Cache hydration failed for %s (non-fatal)", alert_type,
                exc_info=True,
            )

    count = len(records)
    if cache_failures:
        logger.warning(
            "Hydrated %d alert configs from primary; %d failed to reach cache",
            count, cache_failures,
        )
    else:
        logger.info("Hydrated %d alert configs from primary into cache", count)
    return count
