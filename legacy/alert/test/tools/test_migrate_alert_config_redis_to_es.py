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

"""Unit tests for tools/migrate_alert_config_redis_to_es.py (the alert-config ES hydration)."""

import os
import sys
from unittest.mock import MagicMock

import fakeredis
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from tools.migrate_alert_config_redis_to_es import (
    REDIS_KEY_PREFIX,
    iter_redis_configs,
    migrate,
)


def _record(alert_type="collision", prompt="p", **extra):
    return {"alert_type": alert_type, "prompt": prompt, **extra}


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def es_store():
    store = MagicMock()
    store.set_if_absent.return_value = True
    return store


def _seed(redis_client, records):
    for alert_type, data in records:
        redis_client.json().set(f"{REDIS_KEY_PREFIX}{alert_type}", "$", data)


# ── iter_redis_configs ──────────────────────────────────────────────


def test_iter_yields_nothing_when_no_keys(redis_client):
    assert list(iter_redis_configs(redis_client)) == []


def test_iter_returns_records_keyed_by_normalized_alert_type(redis_client):
    _seed(redis_client, [
        ("collision", _record("Collision")),
        ("unsafe_behavior", _record("unsafe_behavior")),
    ])
    out = dict(iter_redis_configs(redis_client))
    assert set(out.keys()) == {"collision", "unsafe_behavior"}


def test_iter_falls_back_to_key_suffix_when_record_has_no_alert_type(redis_client):
    """Legacy records may have lived in Redis
    under ``alert_config:<id>`` without an embedded ``alert_type``
    field. The iterator must derive the type from the key suffix AND
    stamp it onto the yielded record — otherwise downstream
    ``set_if_absent`` would write a doc that ``ESAlertConfigStore.get_all``
    silently filters out (it keys results by the ``alert_type`` field on
    each doc), making the migrated record invisible to listing /
    hydration even though the migration reports success."""
    redis_client.json().set(f"{REDIS_KEY_PREFIX}legacy", "$", {"prompt": "p"})
    out = dict(iter_redis_configs(redis_client))
    # Field must be injected so the record is visible to ``get_all``.
    assert out["legacy"] == {"alert_type": "legacy", "prompt": "p"}


def test_iter_does_not_mutate_original_redis_record(redis_client):
    """Stamping the derived ``alert_type`` onto the yielded record
    must not write back to Redis or mutate the in-Redis dict —
    migration is read-only against Redis by design."""
    redis_client.json().set(f"{REDIS_KEY_PREFIX}legacy", "$", {"prompt": "p"})
    list(iter_redis_configs(redis_client))
    raw = redis_client.json().get(f"{REDIS_KEY_PREFIX}legacy")
    assert raw == {"prompt": "p"}  # unchanged in Redis


def test_iter_normalizes_alert_type_when_record_has_uppercase(redis_client):
    """Records with an embedded but non-normalized ``alert_type``
    (e.g., ``"Collision"``) get normalized to lower-case before
    yielding so the doc id and the embedded field agree."""
    redis_client.json().set(
        f"{REDIS_KEY_PREFIX}collision", "$",
        {"alert_type": "Collision", "prompt": "p"},
    )
    out = dict(iter_redis_configs(redis_client))
    assert "collision" in out
    assert out["collision"]["alert_type"] == "collision"


def test_iter_ignores_unrelated_redis_keys(redis_client):
    _seed(redis_client, [("collision", _record())])
    redis_client.set("prompts:unrelated", "value")
    out = dict(iter_redis_configs(redis_client))
    assert set(out.keys()) == {"collision"}


# ── migrate ─────────────────────────────────────────────────────────


def test_migrate_empty_redis_is_noop(redis_client, es_store):
    stats = migrate(redis_client, es_store)
    assert stats == {"total": 0, "migrated": 0, "skipped": 0, "errors": 0}
    es_store.set_if_absent.assert_not_called()


def test_migrate_copies_every_record_to_es(redis_client, es_store):
    _seed(redis_client, [
        ("collision", _record("collision")),
        ("unsafe_behavior", _record("unsafe_behavior")),
    ])
    stats = migrate(redis_client, es_store)
    assert stats == {"total": 2, "migrated": 2, "skipped": 0, "errors": 0}
    assert es_store.set_if_absent.call_count == 2


def test_migrate_reports_skipped_when_doc_already_in_es(redis_client, es_store):
    _seed(redis_client, [("collision", _record("collision"))])
    es_store.set_if_absent.return_value = False
    stats = migrate(redis_client, es_store)
    assert stats == {"total": 1, "migrated": 0, "skipped": 1, "errors": 0}


def test_migrate_is_idempotent_on_second_run(redis_client, es_store):
    _seed(redis_client, [("collision", _record("collision"))])
    # First run: fresh insert.
    migrate(redis_client, es_store)
    # Second run: ES already has it, so set_if_absent returns False.
    es_store.set_if_absent.return_value = False
    stats = migrate(redis_client, es_store)
    assert stats["migrated"] == 0
    assert stats["skipped"] == 1


def test_migrate_continues_after_per_record_error(redis_client, es_store):
    _seed(redis_client, [
        ("collision", _record("collision")),
        ("unsafe_behavior", _record("unsafe_behavior")),
    ])
    # One call blows up, the other succeeds.
    es_store.set_if_absent.side_effect = [RuntimeError("es blip"), True]
    stats = migrate(redis_client, es_store)
    assert stats["total"] == 2
    assert stats["errors"] == 1
    assert stats["migrated"] == 1


def test_migrate_dry_run_does_not_touch_es(redis_client, es_store):
    _seed(redis_client, [("collision", _record("collision"))])
    stats = migrate(redis_client, es_store, dry_run=True)
    assert stats == {"total": 1, "migrated": 1, "skipped": 0, "errors": 0}
    es_store.set_if_absent.assert_not_called()
