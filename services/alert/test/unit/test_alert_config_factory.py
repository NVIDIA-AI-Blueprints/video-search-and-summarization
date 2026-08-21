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

"""Unit tests for handlers/alert_config/factory.py (Redis-free build)."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from handlers.alert_config import (
    CachedAlertConfigStore,
    InMemoryAlertConfigStore,
    build_alert_config_store,
)


# Persistence disabled MUST fail readiness in a non-dev profile. The
# non-durable in-memory store is only permitted when the profile explicitly
# opts in via persistence.dev_allow_in_memory.
@patch("handlers.alert_config.factory.create_persistence_store")
def test_raises_when_persistence_disabled_without_dev_optin(mock_factory):
    mock_factory.return_value = None
    with pytest.raises(RuntimeError, match="dev_allow_in_memory"):
        build_alert_config_store({"persistence": {"enabled": False}})


@patch("handlers.alert_config.factory.create_persistence_store")
def test_raises_when_persistence_section_absent(mock_factory):
    # No persistence section at all is treated as non-dev → must fail rather
    # than silently serving a non-durable store.
    mock_factory.return_value = None
    with pytest.raises(RuntimeError, match="dev_allow_in_memory"):
        build_alert_config_store({})


@patch("handlers.alert_config.factory.create_persistence_store")
def test_returns_in_memory_when_dev_optin_set(mock_factory):
    mock_factory.return_value = None
    store = build_alert_config_store(
        {"persistence": {"enabled": False, "dev_allow_in_memory": True}}
    )
    assert isinstance(store, InMemoryAlertConfigStore)


@patch("handlers.alert_config.factory.create_persistence_store")
def test_returns_cached_store_when_persistence_healthy(mock_factory):
    persistence = MagicMock()
    persistence.health.return_value = True
    persistence.list.return_value = {"items": [], "total": 0}
    mock_factory.return_value = persistence

    store = build_alert_config_store({"persistence": {"enabled": True}})

    assert isinstance(store, CachedAlertConfigStore)
    persistence.list.assert_called_once()  # hydration ran


@patch("handlers.alert_config.factory.create_persistence_store")
def test_hydration_populates_memory_snapshot(mock_factory):
    existing_doc = {"alert_type": "collision", "prompt": "p"}
    persistence = MagicMock()
    persistence.health.return_value = True
    persistence.list.return_value = {"items": [existing_doc], "total": 1}
    persistence.read.return_value = existing_doc
    mock_factory.return_value = persistence

    store = build_alert_config_store({"persistence": {"enabled": True}})

    # The composite serves the hydrated record. With the default
    # read-through cache (ttl=0), the read falls through to the ES
    # primary — which the mocked persistence answers.
    assert store.get("collision") == existing_doc


@patch("handlers.alert_config.factory.create_persistence_store")
def test_raises_when_persistence_unhealthy(mock_factory):
    persistence = MagicMock()
    persistence.health.return_value = False
    mock_factory.return_value = persistence

    with pytest.raises(RuntimeError, match="Elasticsearch is unreachable"):
        build_alert_config_store({"persistence": {"enabled": True}})


@patch("handlers.alert_config.factory.create_persistence_store")
def test_hydrate_false_skips_initial_population(mock_factory):
    persistence = MagicMock()
    persistence.health.return_value = True
    persistence.list.return_value = {"items": [], "total": 0}
    mock_factory.return_value = persistence

    build_alert_config_store({"persistence": {"enabled": True}}, hydrate=False)

    persistence.list.assert_not_called()


@patch("handlers.alert_config.factory.create_persistence_store")
def test_cached_store_writes_propagate_to_primary(mock_factory):
    persistence = MagicMock()
    persistence.health.return_value = True
    persistence.list.return_value = {"items": [], "total": 0}
    persistence.create.return_value = {"alert_type": "collision", "prompt": "p"}
    mock_factory.return_value = persistence

    store = build_alert_config_store({"persistence": {"enabled": True}})
    store.set_if_absent("collision", {"alert_type": "collision", "prompt": "p"})

    persistence.create.assert_called_once()


# ── Cache TTL semantics ─────────────────────────────────────────────


@patch("handlers.alert_config.factory.create_persistence_store")
def test_default_cache_is_read_through(mock_factory):
    """Default deployment uses a read-through (ttl=0) in-process cache so
    a config edit made in the REST-API process is picked up by the
    pipeline process via ES (source of truth) on the next read — no
    shared Redis, no cross-process cache invalidation needed."""
    persistence = MagicMock()
    persistence.health.return_value = True
    persistence.list.return_value = {"items": [], "total": 0}
    fresh = {"alert_type": "collision", "prompt": "v2"}
    persistence.read.return_value = fresh
    mock_factory.return_value = persistence

    store = build_alert_config_store({"persistence": {"enabled": True}})
    # Even after a local set of a stale value, the read-through cache does
    # not shadow the ES primary.
    store._cache.set("collision", {"alert_type": "collision", "prompt": "stale"})
    assert store.get("collision") == fresh


@patch("handlers.alert_config.factory.create_persistence_store")
def test_cache_ttl_seconds_config_enables_caching(mock_factory):
    persistence = MagicMock()
    persistence.health.return_value = True
    persistence.list.return_value = {"items": [], "total": 0}
    mock_factory.return_value = persistence

    store = build_alert_config_store(
        {"persistence": {"enabled": True, "cache_ttl_seconds": 30}},
    )
    cached = {"alert_type": "x", "prompt": "cached"}
    store._cache.set("x", cached)
    # With a positive TTL the cache hit short-circuits the ES read.
    assert store.get("x") == cached
    persistence.read.assert_not_called()
