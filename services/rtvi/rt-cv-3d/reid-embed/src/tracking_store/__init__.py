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

"""Tracking store abstraction for the ReID service.

This package hides the in-memory ``object_tracking_db`` dict (and the
multiprocessing queues that fan it out across the Kafka consumer,
maintenance, and FastAPI processes) behind a single ``TrackingStore``
interface.

Phase 1 ships :class:`InMemoryTrackingStore`, which preserves the
legacy ``object_tracking_db`` behaviour bit-for-bit: a Python dict in
the main process, fed by ``multiprocessing.Queue`` from child processes
via an in-process drainer thread.

Phase 2 adds :class:`SqliteTrackingStore`, an SQLite-WAL backend that
removes the owner/non-owner distinction entirely: every process opens
its own connection to the same database file and reads/writes
directly. Reads can now happen from any process (Kafka consumer,
maintenance, search workers, ...) without queueing back to the owner.

Future phases add other backends (Redis, ...) behind the same protocol;
``main.py`` does not change again.

Public API
----------

* :class:`TrackedObject` -- the row schema stored by every backend.
* :class:`TrackingStore` -- the abstract base class every backend subclasses.
* :class:`InMemoryTrackingStore` -- the Phase 1 default backend.
* :class:`SqliteTrackingStore` -- the Phase 2 SQLite-WAL backend.
* :func:`make_store` -- factory that selects a backend by env var.
* :data:`TrackingRelabelUpdate` -- ``(old_oid, stream_id, frame_id, new_oid)``
  tuple consumed by :meth:`TrackingStore.apply_relabel`.
* :data:`TrackingKey` -- ``(object_id, sensor_id, frame_id)`` join key.

Selection
---------

``make_store()`` honours the ``TRACKING_STORE_BACKEND`` env var:

* ``memory`` (default) -- :class:`InMemoryTrackingStore`.
* ``sqlite``           -- :class:`SqliteTrackingStore`.
* ``redis``            -- reserved for Phase 3; currently raises.
"""

from tracking_store.models import TrackedObject
from tracking_store.base import (
    TrackingStore,
    TrackingRelabelUpdate,
    TrackingKey,
    SensorIdToMaxFrameIdMap,
)
from tracking_store.memory import InMemoryTrackingStore
from tracking_store.sqlite import SqliteTrackingStore
from tracking_store.factory import make_store

__all__ = [
    "TrackedObject",
    "TrackingStore",
    "TrackingRelabelUpdate",
    "TrackingKey",
    "SensorIdToMaxFrameIdMap",
    "InMemoryTrackingStore",
    "SqliteTrackingStore",
    "make_store",
]
