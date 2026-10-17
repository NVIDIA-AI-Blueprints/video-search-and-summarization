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

"""Cluster-statistics store abstraction for the ReID service.

This package hides the per-object ``(centroid, variance, count)``
summaries behind a single :class:`ClusterStatsStore` interface, mirroring
the design of the ``tracking_store`` package. There is one store instance
per Milvus collection (main + compressed).

Backends
--------

* :class:`InMemoryClusterStatsStore` (Phase 1, default) -- a dict in the
  FastAPI parent with an atomic whole-set swap on write, fed across
  processes by a ``multiprocessing.Queue`` + owner-side drainer thread.
  Preserves the legacy ``ClusterStatsCache`` behaviour exactly.
* :class:`SqliteClusterStatsStore` (Phase 2) -- an SQLite-WAL backend.
  Every process opens its own connection and reads live data; writes go
  straight to the shared DB and bump an epoch counter. Readers keep a
  lazily-refreshed in-process view so the search hot path stays a dict
  lookup. This is the backend that unlocks a multi-process search tier.

Public API
----------

* :class:`ClusterStats` -- the public per-object stats model.
* :class:`CachedStats` -- the compact internal storage tuple returned by
  :meth:`ClusterStatsStore.snapshot_for`.
* :class:`ClusterStatsStore` -- the abstract base every backend subclasses.
* :class:`InMemoryClusterStatsStore` / :class:`SqliteClusterStatsStore`.
* :func:`make_cluster_stats_store` -- factory selecting a backend by the
  ``CLUSTER_STATS_BACKEND`` env var (default ``memory``).

Per-dimension ``(count, mean, variance)`` summaries and their exact O(d)
merge live in :mod:`cluster_stats.moments`, imported directly by the
compression pipeline.
"""

from cluster_stats.models import CachedStats, ClusterStats
from cluster_stats.base import ClusterStatsStore
from cluster_stats.memory import InMemoryClusterStatsStore
from cluster_stats.sqlite import SqliteClusterStatsStore
from cluster_stats.factory import make_cluster_stats_store

__all__ = [
    "CachedStats",
    "ClusterStats",
    "ClusterStatsStore",
    "InMemoryClusterStatsStore",
    "SqliteClusterStatsStore",
    "make_cluster_stats_store",
]
