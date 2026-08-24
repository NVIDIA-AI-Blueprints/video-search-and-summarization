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

"""Unit tests for handlers/alert_config/es_store.py (the alert-config ES hydration)."""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from handlers.alert_config import ALERT_CONFIG_COLLECTION, ESAlertConfigStore
from persistence.exceptions import (
    DocumentAlreadyExistsError,
    DocumentNotFoundError,
    PersistenceError,
)


@pytest.fixture
def persistence():
    return MagicMock()


@pytest.fixture
def store(persistence):
    return ESAlertConfigStore(persistence)


def _doc(alert_type="collision", **extra):
    return {"alert_type": alert_type, "prompt": "p", **extra}


# ── set_if_absent ────────────────────────────────────────────────────


def test_set_if_absent_calls_create_with_normalized_key(store, persistence):
    ok = store.set_if_absent("Collision", _doc())
    assert ok is True
    persistence.create.assert_called_once_with(
        ALERT_CONFIG_COLLECTION, "collision", _doc()
    )


def test_set_if_absent_returns_false_when_already_exists(store, persistence):
    persistence.create.side_effect = DocumentAlreadyExistsError(
        ALERT_CONFIG_COLLECTION, "collision"
    )
    assert store.set_if_absent("collision", _doc()) is False


def test_set_if_absent_propagates_other_persistence_errors(store, persistence):
    persistence.create.side_effect = PersistenceError("boom")
    with pytest.raises(PersistenceError):
        store.set_if_absent("collision", _doc())


# ── set ──────────────────────────────────────────────────────────────


def test_set_uses_update_on_existing_record(store, persistence):
    assert store.set("collision", _doc()) is True
    persistence.update.assert_called_once_with(
        ALERT_CONFIG_COLLECTION, "collision", _doc()
    )
    persistence.create.assert_not_called()


def test_set_falls_back_to_create_when_missing(store, persistence):
    persistence.update.side_effect = DocumentNotFoundError(
        ALERT_CONFIG_COLLECTION, "collision"
    )
    assert store.set("collision", _doc()) is True
    persistence.create.assert_called_once_with(
        ALERT_CONFIG_COLLECTION, "collision", _doc()
    )


def test_set_propagates_non_not_found_errors(store, persistence):
    persistence.update.side_effect = PersistenceError("es down")
    with pytest.raises(PersistenceError):
        store.set("collision", _doc())


def test_set_handles_concurrent_create_after_update_not_found(store, persistence):
    """Multi-replica race: replica A's ``update`` sees the doc absent,
    replica B wins the create in between, replica A's ``create`` then
    fails with ``DocumentAlreadyExistsError``. The store must NOT
    surface that as a 5xx — it retries ``update`` against the
    just-created doc so the caller's payload is the latest version.
    Without this catch, every losing replica would leak
    ``DocumentAlreadyExistsError`` to the API caller."""
    doc = _doc()
    persistence.update.side_effect = [
        DocumentNotFoundError(ALERT_CONFIG_COLLECTION, "collision"),
        None,  # retry succeeds
    ]
    persistence.create.side_effect = DocumentAlreadyExistsError(
        ALERT_CONFIG_COLLECTION, "collision"
    )

    assert store.set("collision", doc) is True

    assert persistence.update.call_count == 2
    persistence.update.assert_any_call(ALERT_CONFIG_COLLECTION, "collision", doc)
    persistence.create.assert_called_once_with(
        ALERT_CONFIG_COLLECTION, "collision", doc
    )


def test_set_propagates_create_error_other_than_already_exists(store, persistence):
    """Only ``DocumentAlreadyExistsError`` from the recovery ``create``
    is the concurrent-race signal worth retrying. Any other persistence
    failure (auth, network, mapping) must surface as 5xx instead of
    silently retrying or swallowing — the cache wrapper above this
    decides degradation policy, not the durable store."""
    persistence.update.side_effect = DocumentNotFoundError(
        ALERT_CONFIG_COLLECTION, "collision"
    )
    persistence.create.side_effect = PersistenceError("es down")
    with pytest.raises(PersistenceError):
        store.set("collision", _doc())


# ── get ──────────────────────────────────────────────────────────────


def test_get_returns_doc(store, persistence):
    doc = _doc()
    persistence.read.return_value = doc
    assert store.get("Collision ") == doc
    persistence.read.assert_called_once_with(ALERT_CONFIG_COLLECTION, "collision")


def test_get_returns_none_on_not_found(store, persistence):
    persistence.read.side_effect = DocumentNotFoundError(
        ALERT_CONFIG_COLLECTION, "collision"
    )
    assert store.get("collision") is None


def test_get_propagates_persistence_error(store, persistence):
    persistence.read.side_effect = PersistenceError("es down")
    with pytest.raises(PersistenceError):
        store.get("collision")


# ── get_all ──────────────────────────────────────────────────────────


def test_get_all_keys_by_normalized_alert_type(store, persistence):
    persistence.list.return_value = {
        "items": [
            _doc(alert_type="Collision"),
            _doc(alert_type="unsafe_behavior", prompt="q"),
        ],
        "total": 2,
    }
    result = store.get_all()
    assert set(result.keys()) == {"collision", "unsafe_behavior"}
    persistence.list.assert_called_once_with(ALERT_CONFIG_COLLECTION, size=1000)


def test_get_all_skips_docs_without_alert_type(store, persistence):
    persistence.list.return_value = {
        "items": [_doc(), {"prompt": "no alert_type field"}],
        "total": 2,
    }
    result = store.get_all()
    assert list(result.keys()) == ["collision"]


# ── delete ───────────────────────────────────────────────────────────


def test_delete_calls_persistence_delete(store, persistence):
    persistence.delete.return_value = True
    assert store.delete("Collision") is True
    persistence.delete.assert_called_once_with(ALERT_CONFIG_COLLECTION, "collision")


def test_delete_returns_false_when_missing(store, persistence):
    persistence.delete.side_effect = DocumentNotFoundError(
        ALERT_CONFIG_COLLECTION, "collision"
    )
    assert store.delete("collision") is False


def test_delete_propagates_other_errors(store, persistence):
    persistence.delete.side_effect = PersistenceError("es down")
    with pytest.raises(PersistenceError):
        store.delete("collision")


# ── round-trip safety against persistence._id injection ──────────────


def test_set_round_trip_with_injected_id_does_not_raise(store, persistence):
    """Pin the integration-level contract that any caller doing
    read → modify → write through the alert_config layer (e.g.
    ``AlertConfigService.update``, or the startup ``save_to_redis``
    seed merge) is safe even when the input dict carries
    ``_id`` / ``_seq_no`` echoed back from a previous read.

    The persistence layer is responsible for stripping ES metadata
    before forwarding the write — this test guards against a future
    refactor that bypasses the persistence layer or re-injects
    metadata into the body."""
    payload = {
        "alert_type": "collision",
        "prompt": "p",
        "_id": "collision",
        "_seq_no": 7,
        "_primary_term": 1,
    }
    # Forwarding succeeds — the responsibility for stripping ES
    # metadata sits one layer down (``persistence.update`` /
    # ``persistence.create``); this test exists so a future refactor
    # cannot bypass that layer without the full suite turning red.
    assert store.set("Collision", payload) is True
    persistence.update.assert_called_once_with(
        ALERT_CONFIG_COLLECTION, "collision", payload
    )


def test_set_if_absent_round_trip_with_injected_id_does_not_raise(store, persistence):
    payload = {
        "alert_type": "collision",
        "prompt": "p",
        "_id": "collision",
    }
    assert store.set_if_absent("Collision", payload) is True
    persistence.create.assert_called_once_with(
        ALERT_CONFIG_COLLECTION, "collision", payload
    )
