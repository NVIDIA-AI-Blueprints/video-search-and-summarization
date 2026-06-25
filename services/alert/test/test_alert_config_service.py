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

"""Unit tests for handlers/alert_config/service.py (the alert-config REST API)."""

import os
import sys

import fakeredis
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from handlers.alert_config import (
    AlertConfigAlreadyExists,
    AlertConfigNotFound,
    AlertConfigService,
    AlertConfigStore,
)


@pytest.fixture
def store():
    return AlertConfigStore(fakeredis.FakeRedis(decode_responses=True))


@pytest.fixture
def service(store):
    return AlertConfigService(store=store)


class TestServiceCreate:

    def test_create_persists_data_with_timestamps(self, service, store):
        result = service.create(
            alert_type="collision",
            prompt="P",
            system_prompt="S",
            vlm_params={"max_tokens": 256},
            output_category="Vehicle Collision",
        )
        assert result["alert_type"] == "collision"
        assert result["prompt"] == "P"
        assert result["created_at"]  # populated
        assert result["updated_at"]  # populated
        assert result["created_at"] == result["updated_at"]
        # Persisted in store
        assert store.get("collision")["prompt"] == "P"

    def test_create_normalizes_alert_type(self, service):
        service.create(alert_type="  Collision  ", prompt="p")
        assert service.get("collision")["prompt"] == "p"

    def test_create_duplicate_raises(self, service):
        service.create(alert_type="x", prompt="p")
        with pytest.raises(AlertConfigAlreadyExists):
            service.create(alert_type="x", prompt="other")

    def test_create_with_enrichment_prompt(self, service, store):
        result = service.create(
            alert_type="collision",
            prompt="P",
            system_prompt="S",
            enrichment_prompt="E",
        )
        assert result["enrichment_prompt"] == "E"
        assert store.get("collision")["enrichment_prompt"] == "E"

    def test_create_without_enrichment_prompt_defaults_to_none(self, service):
        result = service.create(alert_type="x", prompt="p")
        assert result.get("enrichment_prompt") is None


class TestServiceUpdate:

    def test_update_deep_merges_vlm_params(self, service):
        service.create(
            alert_type="x", prompt="p",
            vlm_params={"max_tokens": 256, "num_frames": 5},
        )
        service.update(alert_type="x", vlm_params={"max_tokens": 1024})
        result = service.get("x")
        assert result["vlm_params"]["max_tokens"] == 1024  # updated
        assert result["vlm_params"]["num_frames"] == 5     # preserved

    def test_update_preserves_created_at(self, service):
        service.create(alert_type="x", prompt="p")
        original_created = service.get("x")["created_at"]
        service.update(alert_type="x", prompt="p2")
        result = service.get("x")
        assert result["created_at"] == original_created
        assert result["updated_at"] >= original_created

    def test_update_missing_raises(self, service):
        with pytest.raises(AlertConfigNotFound):
            service.update(alert_type="missing", prompt="p")

    def test_update_only_supplied_fields(self, service):
        service.create(
            alert_type="x", prompt="orig_p",
            system_prompt="orig_s", output_category="orig_c",
        )
        service.update(alert_type="x", prompt="new_p")
        result = service.get("x")
        assert result["prompt"] == "new_p"
        assert result["system_prompt"] == "orig_s"  # untouched
        assert result["output_category"] == "orig_c"


class TestServiceGetListDelete:

    def test_get_missing_raises(self, service):
        with pytest.raises(AlertConfigNotFound):
            service.get("never")

    def test_list_all_returns_persisted(self, service):
        service.create(alert_type="a", prompt="pa")
        service.create(alert_type="b", prompt="pb")
        results = service.list_all()
        types = sorted(r["alert_type"] for r in results)
        assert types == ["a", "b"]

    def test_delete_removes_alert_config(self, service, store):
        service.create(alert_type="x", prompt="p")
        service.delete("x")
        assert store.get("x") is None
        with pytest.raises(AlertConfigNotFound):
            service.get("x")

    def test_delete_missing_raises(self, service):
        with pytest.raises(AlertConfigNotFound):
            service.delete("never")


class TestServiceUpdateEnrichment:

    def test_update_enrichment_prompt_only(self, service):
        service.create(alert_type="x", prompt="p")
        service.update(alert_type="x", enrichment_prompt="enrich_text")
        assert service.get("x")["enrichment_prompt"] == "enrich_text"

    def test_enrichment_prompt_optional_after_create(self, service):
        # Service must not raise when caller never sets enrichment_prompt.
        service.create(alert_type="x", prompt="p")
        result = service.get("x")
        assert result.get("enrichment_prompt") is None


class TestServiceUpdateClearFields:
    """Explicit ``None`` must clear an optional field; omitting the
    keyword must leave the previous value untouched."""

    def test_omitted_field_keeps_existing_value(self, service):
        service.create(alert_type="x", prompt="p", system_prompt="orig")
        # Update only output_category — system_prompt must remain.
        service.update(alert_type="x", output_category="cat")
        assert service.get("x")["system_prompt"] == "orig"

    def test_explicit_none_clears_system_prompt(self, service):
        service.create(alert_type="x", prompt="p", system_prompt="orig")
        service.update(alert_type="x", system_prompt=None)
        assert service.get("x")["system_prompt"] is None

    def test_explicit_none_clears_enrichment_prompt(self, service):
        service.create(alert_type="x", prompt="p", enrichment_prompt="enrich")
        service.update(alert_type="x", enrichment_prompt=None)
        assert service.get("x")["enrichment_prompt"] is None

    def test_explicit_none_clears_output_category(self, service):
        service.create(alert_type="x", prompt="p", output_category="orig")
        service.update(alert_type="x", output_category=None)
        assert service.get("x")["output_category"] is None

    def test_explicit_none_clears_vlm_params(self, service):
        service.create(alert_type="x", prompt="p", vlm_params={"max_tokens": 256})
        service.update(alert_type="x", vlm_params=None)
        assert service.get("x")["vlm_params"] is None

    def test_partial_dict_still_deep_merges_vlm_params(self, service):
        service.create(
            alert_type="x", prompt="p",
            vlm_params={"max_tokens": 256, "num_frames": 5},
        )
        service.update(alert_type="x", vlm_params={"max_tokens": 1024})
        # max_tokens overwritten, num_frames preserved
        assert service.get("x")["vlm_params"] == {"max_tokens": 1024, "num_frames": 5}


class TestServiceStrictReadsForRestApi:
    """The service is the entry point for the REST API. Read paths must
    NOT silently fall back to a stale memory snapshot when the durable
    backend is down — operators GET-ing to debug an outage need a
    real 5xx. We assert the service propagates ``PersistenceError`` by
    passing ``fallback_to_memory=False`` through to the underlying
    store; sink and prompt-handler paths bypass this service entirely
    and keep memory fallback for service availability."""

    def _service_with_mock_store(self):
        from unittest.mock import MagicMock
        store = MagicMock()
        return AlertConfigService(store=store), store

    def test_get_passes_fallback_to_memory_false(self):
        service, store = self._service_with_mock_store()
        store.get.return_value = {"alert_type": "collision", "prompt": "p"}
        service.get("collision")
        # The keyword is what matters — verifying it's the strict mode.
        store.get.assert_called_once_with("collision", fallback_to_memory=False)

    def test_get_propagates_persistence_error(self):
        from persistence.exceptions import PersistenceError
        service, store = self._service_with_mock_store()
        store.get.side_effect = PersistenceError("es+redis down")
        with pytest.raises(PersistenceError):
            service.get("collision")

    def test_list_all_passes_fallback_to_memory_false(self):
        service, store = self._service_with_mock_store()
        store.get_all.return_value = {}
        service.list_all()
        store.get_all.assert_called_once_with(fallback_to_memory=False)

    def test_list_all_propagates_persistence_error(self):
        from persistence.exceptions import PersistenceError
        service, store = self._service_with_mock_store()
        store.get_all.side_effect = PersistenceError("es+redis down")
        with pytest.raises(PersistenceError):
            service.list_all()

    def test_update_existence_check_uses_strict_get(self):
        """``update`` reads the current record before merging. That read
        must also surface backend outages as 503, otherwise a stale
        memory copy would be merged into the PUT and re-written to ES
        the moment ES recovers, silently overwriting whatever the real
        durable state had drifted to."""
        service, store = self._service_with_mock_store()
        store.get.return_value = {"alert_type": "x", "prompt": "p"}
        store.set.return_value = True
        service.update(alert_type="x", prompt="new")
        # Find the existence check among the get() calls.
        calls = [c for c in store.get.call_args_list
                 if c.kwargs.get("fallback_to_memory") is False]
        assert calls, "update must use fallback_to_memory=False on the existence read"

    def test_delete_existence_check_uses_strict_get(self):
        service, store = self._service_with_mock_store()
        store.get.return_value = {"alert_type": "x", "prompt": "p"}
        store.delete.return_value = True
        service.delete("x")
        store.get.assert_called_once_with("x", fallback_to_memory=False)
