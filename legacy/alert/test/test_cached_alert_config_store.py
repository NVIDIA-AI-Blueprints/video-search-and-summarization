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

"""Unit tests for handlers/alert_config/cached_store.py (the alert-config ES hydration)."""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from handlers.alert_config import CachedAlertConfigStore
from persistence.exceptions import PersistenceError


def _doc(alert_type="collision", prompt="p", **extra):
    return {"alert_type": alert_type, "prompt": prompt, **extra}


@pytest.fixture
def primary():
    return MagicMock()


@pytest.fixture
def cache():
    return MagicMock()


@pytest.fixture
def memory():
    return {}


@pytest.fixture
def store(primary, cache, memory):
    return CachedAlertConfigStore(primary=primary, cache=cache, memory=memory)


# ── Writes ───────────────────────────────────────────────────────────


def test_set_writes_to_primary_then_cache_and_memory(store, primary, cache, memory):
    doc = _doc()
    assert store.set("collision", doc) is True
    primary.set.assert_called_once_with("collision", doc)
    cache.set.assert_called_once_with("collision", doc)
    assert memory["collision"] == doc


def test_set_propagates_primary_failure_and_skips_cache(store, primary, cache, memory):
    primary.set.side_effect = PersistenceError("es down")
    with pytest.raises(PersistenceError):
        store.set("collision", _doc())
    cache.set.assert_not_called()
    assert memory == {}


def test_set_swallows_cache_failure(store, primary, cache, memory):
    cache.set.side_effect = RuntimeError("redis down")
    doc = _doc()
    assert store.set("collision", doc) is True
    # Memory must still reflect the successful primary write.
    assert memory["collision"] == doc


def test_set_if_absent_updates_cache_when_created(store, primary, cache, memory):
    primary.set_if_absent.return_value = True
    doc = _doc()
    assert store.set_if_absent("collision", doc) is True
    cache.set.assert_called_once_with("collision", doc)
    assert memory["collision"] == doc


def test_set_if_absent_skips_cache_when_already_exists(store, primary, cache, memory):
    primary.set_if_absent.return_value = False
    assert store.set_if_absent("collision", _doc()) is False
    cache.set.assert_not_called()
    assert memory == {}


def test_delete_propagates_through_both_layers(store, primary, cache, memory):
    memory["collision"] = _doc()
    primary.delete.return_value = True
    assert store.delete("collision") is True
    primary.delete.assert_called_once_with("collision")
    cache.delete.assert_called_once_with("collision")
    assert "collision" not in memory


def test_delete_returns_primary_result_when_missing(store, primary, cache):
    primary.delete.return_value = False
    assert store.delete("collision") is False
    # Cache still evicted defensively.
    cache.delete.assert_called_once_with("collision")


# ── Reads ────────────────────────────────────────────────────────────


def test_get_returns_cache_hit_without_touching_primary(store, primary, cache):
    doc = _doc()
    cache.get.return_value = doc
    assert store.get("collision") == doc
    primary.get.assert_not_called()


def test_get_falls_through_to_primary_on_cache_miss(store, primary, cache, memory):
    cache.get.return_value = None
    doc = _doc()
    primary.get.return_value = doc
    assert store.get("collision") == doc
    # Cache populated from primary.
    cache.set.assert_called_once_with("collision", doc)
    assert memory["collision"] == doc


def test_get_falls_through_to_primary_on_cache_error(store, primary, cache):
    cache.get.side_effect = RuntimeError("redis down")
    doc = _doc()
    primary.get.return_value = doc
    assert store.get("collision") == doc


def test_get_returns_none_when_primary_reports_missing(store, primary, cache, memory):
    memory["collision"] = _doc()  # stale snapshot
    cache.get.return_value = None
    primary.get.return_value = None
    assert store.get("collision") is None
    # Stale memory purged so we don't serve a ghost record.
    assert "collision" not in memory


def test_get_falls_back_to_memory_when_primary_errors(store, primary, cache, memory):
    memory["collision"] = _doc()
    cache.get.return_value = None
    primary.get.side_effect = PersistenceError("es down")
    assert store.get("collision") == memory["collision"]


def test_get_returns_none_when_all_three_miss(store, primary, cache):
    cache.get.return_value = None
    primary.get.side_effect = PersistenceError("es down")
    assert store.get("collision") is None


def test_get_all_uses_primary_and_refreshes_snapshot(store, primary, cache, memory):
    primary.get_all.return_value = {"collision": _doc(), "unsafe_behavior": _doc("u")}
    memory["stale"] = {"alert_type": "stale"}
    result = store.get_all()
    assert result == {"collision": _doc(), "unsafe_behavior": _doc("u")}
    assert memory == result  # snapshot rewritten


def test_get_all_falls_back_to_memory_on_primary_error(store, primary, memory):
    primary.get_all.side_effect = PersistenceError("es down")
    memory["collision"] = _doc()
    result = store.get_all()
    assert result == {"collision": _doc()}
    # Should be a copy — mutating the return value must not corrupt memory.
    result["injected"] = {"alert_type": "injected"}
    assert "injected" not in memory


# ── Strict reads (REST API path) ────────────────────────────────────


def test_get_strict_propagates_persistence_error_instead_of_memory(
    store, primary, cache, memory,
):
    """The REST API opts out of the memory-snapshot fallback so an
    operator running ``GET /verification/config`` to debug a backend
    outage gets a 503-friendly ``PersistenceError`` rather than a 200
    with whatever the process happened to cache last. The sink path
    keeps the default (``fallback_to_memory=True``) for service
    availability — see ``cached_store.get`` docstring."""
    memory["collision"] = _doc()
    cache.get.return_value = None
    primary.get.side_effect = PersistenceError("es down")
    with pytest.raises(PersistenceError):
        store.get("collision", fallback_to_memory=False)


def test_get_strict_still_uses_cache_hit_when_redis_serves(store, primary, cache):
    """Strict mode disables only the *memory-after-primary-error*
    fallback. A healthy Redis hit is still the hot path and must
    serve the request without involving ES — that's the whole point
    of the cache layer."""
    cache.get.return_value = _doc()
    assert store.get("collision", fallback_to_memory=False) == _doc()
    primary.get.assert_not_called()


def test_get_strict_returns_none_when_primary_says_absent(store, primary, cache):
    """A clean ``None`` from ES is not an error — the doc just isn't
    there. Strict mode must NOT swap that for a stale memory copy
    either, so ``None`` propagates and the route returns 404."""
    cache.get.return_value = None
    primary.get.return_value = None
    assert store.get("collision", fallback_to_memory=False) is None


def test_get_all_strict_propagates_persistence_error(store, primary, memory):
    """Listing the configs through the REST API must surface a
    backend outage as 503 too — same reasoning as ``get`` above."""
    primary.get_all.side_effect = PersistenceError("es down")
    memory["collision"] = _doc()
    with pytest.raises(PersistenceError):
        store.get_all(fallback_to_memory=False)
