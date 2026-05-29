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

"""Unit tests for handlers/alert_config/factory.py (the alert-config ES hydration)."""

import os
import sys
from unittest.mock import MagicMock, patch

import fakeredis
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from handlers.alert_config import (
    CachedAlertConfigStore,
    RedisAlertConfigStore,
    build_alert_config_store,
)


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis(decode_responses=True)


@patch("handlers.alert_config.factory.create_persistence_store")
def test_returns_redis_only_when_persistence_disabled(mock_factory, redis_client):
    mock_factory.return_value = None
    store = build_alert_config_store(redis_client, {"persistence": {"enabled": False}})
    assert isinstance(store, RedisAlertConfigStore)


@patch("handlers.alert_config.factory.create_persistence_store")
def test_returns_redis_only_when_factory_has_no_hosts(mock_factory, redis_client):
    mock_factory.return_value = None
    store = build_alert_config_store(redis_client, {})
    assert isinstance(store, RedisAlertConfigStore)


@patch("handlers.alert_config.factory.create_persistence_store")
def test_returns_cached_store_when_persistence_healthy(mock_factory, redis_client):
    persistence = MagicMock()
    persistence.health.return_value = True
    persistence.list.return_value = {"items": [], "total": 0}
    mock_factory.return_value = persistence

    store = build_alert_config_store(redis_client, {"persistence": {"enabled": True}})

    assert isinstance(store, CachedAlertConfigStore)
    persistence.list.assert_called_once()  # hydration ran


@patch("handlers.alert_config.factory.create_persistence_store")
def test_hydration_populates_cache(mock_factory, redis_client):
    existing_doc = {"alert_type": "collision", "prompt": "p"}
    persistence = MagicMock()
    persistence.health.return_value = True
    persistence.list.return_value = {"items": [existing_doc], "total": 1}
    mock_factory.return_value = persistence

    build_alert_config_store(redis_client, {"persistence": {"enabled": True}})

    # After hydration, the Redis client should have the record cached.
    cached = redis_client.json().get("alert_config:collision")
    assert cached == existing_doc


@patch("handlers.alert_config.factory.create_persistence_store")
def test_raises_when_persistence_unhealthy(mock_factory, redis_client):
    persistence = MagicMock()
    persistence.health.return_value = False
    mock_factory.return_value = persistence

    with pytest.raises(RuntimeError, match="Elasticsearch is unreachable"):
        build_alert_config_store(redis_client, {"persistence": {"enabled": True}})


@patch("handlers.alert_config.factory.create_persistence_store")
def test_hydrate_false_skips_initial_population(mock_factory, redis_client):
    persistence = MagicMock()
    persistence.health.return_value = True
    persistence.list.return_value = {"items": [], "total": 0}
    mock_factory.return_value = persistence

    build_alert_config_store(
        redis_client, {"persistence": {"enabled": True}}, hydrate=False,
    )

    # Hydration should NOT have called list().
    persistence.list.assert_not_called()


@patch("handlers.alert_config.factory.create_persistence_store")
def test_cached_store_writes_propagate_to_both_backends(mock_factory, redis_client):
    persistence = MagicMock()
    persistence.health.return_value = True
    persistence.list.return_value = {"items": [], "total": 0}
    persistence.create.return_value = {"alert_type": "collision", "prompt": "p"}
    mock_factory.return_value = persistence

    store = build_alert_config_store(redis_client, {"persistence": {"enabled": True}})
    store.set_if_absent("collision", {"alert_type": "collision", "prompt": "p"})

    persistence.create.assert_called_once()
    assert redis_client.json().get("alert_config:collision") is not None


# ── TTL semantics by deployment mode ────────────────────────────────


@patch("handlers.alert_config.factory.create_persistence_store")
def test_redis_only_mode_disables_ttl_to_avoid_data_loss(mock_factory, redis_client):
    """Backward-compat (``persistence.enabled: false``) makes Redis the
    *source of truth*, not a cache. Stamping a TTL there would silently
    expire operator configs after the cache window — turning the
    bounded-staleness fix for the cached composite into a data-loss
    bug for legacy deployments. The factory must hand the Redis-only
    store ``cache_ttl_seconds=None``."""
    mock_factory.return_value = None
    store = build_alert_config_store(redis_client, {"persistence": {"enabled": False}})
    assert isinstance(store, RedisAlertConfigStore)
    # Confirm via real Redis behaviour, not just an attribute read:
    # writing to the store must not stamp a TTL.
    store.set("collision", {"alert_type": "collision", "prompt": "p"})
    assert redis_client.ttl("alert_config:collision") == -1  # no expiry


@patch("handlers.alert_config.factory.create_persistence_store")
def test_cached_composite_mode_applies_ttl_to_cache_layer(mock_factory, redis_client):
    """In the normal mode (ES is source of truth, Redis is cache),
    every Redis write must stamp the TTL so a dropped DELETE
    invalidation cannot leave a stale entry visible forever."""
    persistence = MagicMock()
    persistence.health.return_value = True
    persistence.list.return_value = {"items": [], "total": 0}
    persistence.create.return_value = {"alert_type": "collision", "prompt": "p"}
    mock_factory.return_value = persistence

    store = build_alert_config_store(redis_client, {"persistence": {"enabled": True}})
    store.set_if_absent("collision", {"alert_type": "collision", "prompt": "p"})

    ttl = redis_client.ttl("alert_config:collision")
    # fakeredis returns the remaining seconds; a positive value means
    # the TTL was stamped. Bound matches the configured default.
    assert 0 < ttl <= RedisAlertConfigStore.DEFAULT_CACHE_TTL_SECONDS


@patch("handlers.alert_config.factory.create_persistence_store")
def test_cache_ttl_seconds_config_overrides_default(mock_factory, redis_client):
    persistence = MagicMock()
    persistence.health.return_value = True
    persistence.list.return_value = {"items": [], "total": 0}
    persistence.create.return_value = {"alert_type": "x", "prompt": "p"}
    mock_factory.return_value = persistence

    store = build_alert_config_store(
        redis_client,
        {"persistence": {"enabled": True, "cache_ttl_seconds": 30}},
    )
    store.set_if_absent("x", {"alert_type": "x", "prompt": "p"})
    ttl = redis_client.ttl("alert_config:x")
    assert 0 < ttl <= 30
