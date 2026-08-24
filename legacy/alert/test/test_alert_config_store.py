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

"""Unit tests for handlers/alert_config/store.py (the alert-config REST API)."""

import os
import sys
from unittest.mock import MagicMock

import fakeredis
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from handlers.alert_config import AlertConfigStore, AlertConfigStoreError


@pytest.fixture
def store():
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    return AlertConfigStore(redis_client)


class TestAlertConfigStoreCRUD:

    def test_set_and_get(self, store):
        assert store.set("collision", {"prompt": "p", "vlm_params": {"max_tokens": 256}})
        data = store.get("collision")
        assert data["prompt"] == "p"
        assert data["vlm_params"]["max_tokens"] == 256

    def test_enrichment_prompt_persisted(self, store):
        store.set("collision", {
            "prompt": "p",
            "system_prompt": "s",
            "enrichment_prompt": "e",
        })
        data = store.get("collision")
        assert data["enrichment_prompt"] == "e"

    def test_get_missing_returns_none(self, store):
        assert store.get("does_not_exist") is None

    def test_set_overwrites(self, store):
        store.set("x", {"prompt": "v1"})
        store.set("x", {"prompt": "v2"})
        assert store.get("x")["prompt"] == "v2"

    def test_delete(self, store):
        store.set("y", {"prompt": "p"})
        assert store.delete("y") is True
        assert store.get("y") is None

    def test_delete_missing_returns_false(self, store):
        assert store.delete("never_there") is False

    def test_get_all(self, store):
        store.set("a", {"prompt": "pa"})
        store.set("b", {"prompt": "pb"})
        all_configs = store.get_all()
        assert set(all_configs.keys()) == {"a", "b"}
        assert all_configs["a"]["prompt"] == "pa"

    def test_get_all_empty(self, store):
        assert store.get_all() == {}


class TestAlertConfigStoreNormalization:

    def test_alert_type_lowercased(self, store):
        store.set("Collision", {"prompt": "p"})
        # Same key under different case must collide
        assert store.get("collision") is not None
        assert store.get("COLLISION") is not None

    def test_alert_type_stripped(self, store):
        store.set("  spaced  ", {"prompt": "p"})
        assert store.get("spaced") is not None


class TestAlertConfigStoreAtomicity:

    def test_set_if_absent_creates_new(self, store):
        assert store.set_if_absent("new_one", {"prompt": "p"}) is True
        assert store.get("new_one")["prompt"] == "p"

    def test_set_if_absent_rejects_existing(self, store):
        store.set("existing", {"prompt": "original"})
        assert store.set_if_absent("existing", {"prompt": "should_not_overwrite"}) is False
        # Original data must remain
        assert store.get("existing")["prompt"] == "original"

    def test_set_if_absent_normalized(self, store):
        store.set_if_absent("Foo", {"prompt": "p"})
        # Different case should be detected as duplicate
        assert store.set_if_absent("foo", {"prompt": "other"}) is False


class TestAlertConfigStoreErrorPropagation:
    """Backend failures must surface as AlertConfigStoreError so callers
    can map them to 5xx instead of mistaking them for normal False
    results (e.g. 409 conflict)."""

    def _store_with_failing_set(self):
        broken_redis = MagicMock()
        broken_redis.json.return_value.set.side_effect = RuntimeError("redis down")
        return AlertConfigStore(broken_redis)

    def test_set_raises_on_redis_failure(self):
        store = self._store_with_failing_set()
        with pytest.raises(AlertConfigStoreError):
            store.set("collision", {"prompt": "p"})

    def test_set_if_absent_raises_on_redis_failure(self):
        store = self._store_with_failing_set()
        with pytest.raises(AlertConfigStoreError):
            store.set_if_absent("collision", {"prompt": "p"})

    def test_delete_raises_on_redis_failure(self):
        broken_redis = MagicMock()
        broken_redis.json.return_value.delete.side_effect = RuntimeError("redis down")
        store = AlertConfigStore(broken_redis)
        with pytest.raises(AlertConfigStoreError):
            store.delete("collision")


class TestAlertConfigStoreCacheTTL:
    """TTL on Redis cache entries bounds the staleness window when a
    cache invalidation is dropped — e.g., a transient Redis blip on
    DELETE after the ES delete already succeeded. ``CachedAlertConfigStore``
    swallows that error to keep the API responsive, so without a TTL
    the orphan Redis entry would short-circuit subsequent ES reads
    forever (``cached_store.get`` returns the cache hit before reaching
    the source of truth). The TTL caps the divergence; the next read
    after expiry falls through to ES and re-populates the cache with
    the correct value."""

    def test_set_stamps_ttl_with_default_one_hour(self, store):
        store.set("collision", {"prompt": "p"})
        ttl = store._redis.ttl("alert_config:collision")
        # fakeredis returns the remaining seconds; -1 means no expiry,
        # -2 means key absent. We expect a positive value bounded by
        # the configured default (1h).
        assert 0 < ttl <= AlertConfigStore.DEFAULT_CACHE_TTL_SECONDS

    def test_set_if_absent_stamps_ttl_when_created(self, store):
        assert store.set_if_absent("new_x", {"prompt": "p"}) is True
        ttl = store._redis.ttl("alert_config:new_x")
        assert 0 < ttl <= AlertConfigStore.DEFAULT_CACHE_TTL_SECONDS

    def test_set_if_absent_does_not_refresh_ttl_on_collision(self, store):
        """A failed NX (key already exists) must NOT bump the TTL of
        the existing entry — otherwise a flood of duplicate POSTs
        from a buggy client could indefinitely keep an obsolete value
        alive."""
        # Seed via direct Redis to get a known short TTL.
        store._redis.json().set("alert_config:dup", "$", {"prompt": "first"})
        store._redis.expire("alert_config:dup", 30)
        ttl_before = store._redis.ttl("alert_config:dup")

        # Collision returns False — the existing entry's TTL must
        # stay capped near 30, not jump back up to the default 3600.
        assert store.set_if_absent("dup", {"prompt": "second"}) is False

        ttl_after = store._redis.ttl("alert_config:dup")
        assert ttl_after <= ttl_before

    def test_overwrite_via_set_refreshes_ttl(self, store):
        """An unconditional ``set`` (the PUT path) is operator-driven
        and intentional, so refreshing TTL on every write is correct
        and matches the freshness expectation."""
        store.set("foo", {"prompt": "v1"})
        store._redis.expire("alert_config:foo", 30)
        store.set("foo", {"prompt": "v2"})  # operator update
        ttl_after = store._redis.ttl("alert_config:foo")
        # New TTL should be near the default (3600), well above the 30
        # we forced in. ``> 30`` is the cleanest assertion that the
        # write reset the TTL.
        assert ttl_after > 30

    def test_no_ttl_when_disabled_via_constructor(self):
        """``cache_ttl_seconds=None`` disables TTL stamping entirely.
        Used by tests that want to inspect Redis state without
        time-pressure, and by deployments that prefer external TTL
        management (e.g., MAXMEMORY policy)."""
        redis_client = fakeredis.FakeRedis(decode_responses=True)
        store = AlertConfigStore(redis_client, cache_ttl_seconds=None)
        store.set("collision", {"prompt": "p"})
        # -1 means "no expiration" in Redis ``TTL`` semantics.
        assert redis_client.ttl("alert_config:collision") == -1

    def test_ttl_failure_is_non_fatal(self):
        """A flaky ``EXPIRE`` after a successful ``JSON.SET`` must NOT
        fail the write. The value is committed to Redis, and the TTL
        will be re-stamped on the next successful write — the worst
        case is one slightly-stale entry, not a failed API call."""
        broken_redis = MagicMock()
        # JSON.SET succeeds…
        broken_redis.json.return_value.set.return_value = True
        # …but EXPIRE blows up.
        broken_redis.expire.side_effect = RuntimeError("expire failed")
        store = AlertConfigStore(broken_redis)
        # Must NOT raise — TTL stamp is best-effort.
        assert store.set("collision", {"prompt": "p"}) is True
        broken_redis.expire.assert_called_once()
