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

"""Shared pytest fixtures for tracking-store tests.

The conformance suite (``test_conformance.py``) is parameterised over
every backend listed in :data:`STORE_FACTORIES`. To add a new backend
(Phase 2 SQLite, Phase 3 Redis, ...), append a ``(name, factory)``
entry here. The factory takes a ``tmp_path`` and returns a started
:class:`TrackingStore` instance ready for use.
"""

from __future__ import annotations

from typing import Any, Callable, List

import pytest

from tracking_store import (
    InMemoryTrackingStore,
    SqliteTrackingStore,
    TrackedObject,
    TrackingStore,
)


def make_obj(
    object_id: str,
    stream_id: str,
    frame_id: int,
    **overrides: Any,
) -> TrackedObject:
    """Build a :class:`TrackedObject` with test-friendly defaults.

    Only the bare-minimum identifying fields (``object_id``,
    ``stream_id``, ``frame_id``) are positional; everything else
    defaults to a deterministic, non-zero value so tests don't have to
    pass repetitive bbox / confidence kwargs.
    """
    defaults = dict(
        timestamp="0",
        leftX=0.0,
        topY=0.0,
        rightX=1.0,
        bottomY=1.0,
        confidence=1.0,
        visibility=1.0,
    )
    defaults.update(overrides)
    return TrackedObject(
        object_id=object_id,
        stream_id=stream_id,
        frame_id=frame_id,
        **defaults,
    )


# ---------------------------------------------------------------------------
# Backend registry. Phase 2/3 backends register themselves here.
# ---------------------------------------------------------------------------


def _make_memory(tmp_path) -> TrackingStore:  # noqa: ARG001 -- tmp_path unused
    store = InMemoryTrackingStore()
    store.start_drainers()
    return store


def _make_sqlite(tmp_path) -> TrackingStore:
    # Per-test DB file under pytest's ``tmp_path`` so concurrent test
    # runs (and our own conformance/per-backend tests) cannot tread on
    # each other. ``reset_on_start=True`` keeps semantics aligned with
    # the in-memory factory, which always returns an empty store.
    db_path = tmp_path / "tracking.db"
    return SqliteTrackingStore(path=str(db_path), reset_on_start=True)


# Each entry: (param-id, factory). The factory is invoked per test so
# every test gets a fresh store.
STORE_FACTORIES: List = [
    pytest.param(("memory", _make_memory), id="memory"),
    pytest.param(("sqlite", _make_sqlite), id="sqlite"),
]


@pytest.fixture(params=STORE_FACTORIES)
def store(request, tmp_path) -> TrackingStore:
    """Yield a ready-to-use TrackingStore from each registered backend."""
    name, factory = request.param
    instance = factory(tmp_path)
    yield instance
    instance.shutdown()


@pytest.fixture
def obj_factory() -> Callable[..., TrackedObject]:
    return make_obj
