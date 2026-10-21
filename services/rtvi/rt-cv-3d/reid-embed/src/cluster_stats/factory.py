# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Backend selection for :class:`ClusterStatsStore`.

The factory is the only place that knows about concrete backend
classes. ``main.py`` calls :func:`make_cluster_stats_store` once per
collection (main + compressed) and never touches a backend constructor
directly.

Selection rules
---------------

Order of precedence for the backend name:

1. Explicit ``backend=`` argument to :func:`make_cluster_stats_store`.
2. ``CLUSTER_STATS_BACKEND`` environment variable.
3. ``"sqlite"`` (the default -- the cross-process SQLite-WAL backend
   that a multi-process search tier needs). Set
   ``CLUSTER_STATS_BACKEND=memory`` to fall back to the legacy
   in-process cache.

Recognised values:

* ``memory`` -- :class:`cluster_stats.memory.InMemoryClusterStatsStore`.
* ``sqlite`` -- :class:`cluster_stats.sqlite.SqliteClusterStatsStore`.
* ``redis``  -- reserved; currently raises ``NotImplementedError``.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from cluster_stats.base import ClusterStatsStore
from cluster_stats.memory import InMemoryClusterStatsStore
from cluster_stats.sqlite import SqliteClusterStatsStore


logger = logging.getLogger(__name__)


_BACKEND_ENV_VAR = "CLUSTER_STATS_BACKEND"
_DEFAULT_BACKEND = "sqlite"


def make_cluster_stats_store(
    label: str,
    backend: Optional[str] = None,
) -> ClusterStatsStore:
    """Return a :class:`ClusterStatsStore` for the requested backend.

    ``label`` names the collection this store serves ("main" /
    "compressed"); it appears in logs and (for SQLite) selects the DB
    file. See module docstring for backend selection rules.
    """
    name = (backend or os.getenv(_BACKEND_ENV_VAR) or _DEFAULT_BACKEND).strip().lower()

    if name == "memory":
        logger.info("ClusterStatsStore(%s): using in-memory backend", label)
        return InMemoryClusterStatsStore(label=label)

    if name == "sqlite":
        logger.info("ClusterStatsStore(%s): using SQLite WAL backend", label)
        return SqliteClusterStatsStore(label=label)

    if name == "redis":
        raise NotImplementedError(
            "CLUSTER_STATS_BACKEND=redis is reserved; set " "CLUSTER_STATS_BACKEND=memory or =sqlite."
        )

    raise ValueError(f"Unknown CLUSTER_STATS_BACKEND={name!r}; expected one of " "'memory', 'sqlite', 'redis'.")


__all__ = ["make_cluster_stats_store"]
