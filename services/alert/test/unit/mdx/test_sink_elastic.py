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

"""Unit tests for ``mdx.sink.vlm_enhanced_sink.sink_elastic``.

The Elastic sink is the terminal step for an enriched event. Three properties
are load-bearing:

* **Alerts and incidents go to different indices**, chosen per event; a
  mix-up silently pollutes one index with the other's documents.
* **The output-category override is resolved per publish** from the live
  ``alert_config_store``, so a PUT to the alert-config API takes effect on the
  next event without a restart. The sink passes it as a mapping argument
  rather than mutating the document, because ``write_event_response`` applies
  it only after the fingerprint (the document id) is computed.
* **A write failure must not raise.** The sink runs inside the publish loop;
  it logs and returns so one bad document cannot stall ingestion. The verdict
  is only marked confirmed in Redis *after* a successful write — marking it
  first would suppress a retry of a document that never landed.

``from_config`` is the operator-facing constructor and rejects an incomplete
config loudly rather than defaulting to a wrong index.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mdx.sink.vlm_enhanced_sink.sink_elastic import VLMEnhancedElasticSink
from schemas.vlm_responses import EnrichmentResponse

INCIDENT = {
    "Id": "fingerprint-1",
    "id": "inc-1",
    "sensorId": "cam-1",
    "category": "collision",
    "timestamp": "2025-09-19T08:23:06.870Z",
    "info": {"verdict": "confirmed", "user_prompt": "is there a crash?"},
}


def make_sink(**overrides):
    kwargs = {
        "elastic_client": MagicMock(),
        "incident_index": "mdx-vlm-incidents",
        "alert_index": "mdx-vlm-alerts",
    }
    kwargs.update(overrides)
    return VLMEnhancedElasticSink(**kwargs)


@pytest.fixture
def sink():
    return make_sink()


class TestConstruction:
    def test_indices_are_kept(self, sink):
        assert sink._incident_index == "mdx-vlm-incidents"
        assert sink._alert_index == "mdx-vlm-alerts"

    def test_verdict_description_mapping_defaults_to_empty(self, sink):
        assert sink._verdict_description_mapping == {}

    def test_redis_handler_is_optional(self, sink):
        assert sink._redis_handler is None


class TestFromConfig:
    BASE_CONFIG = {
        "elastic": {"enabled": True, "hosts": ["http://es:9200"]},
        "vlm_enhanced_sink": {
            "incident": {"elastic": {"index": "mdx-vlm-incidents"}},
            "alert": {"elastic": {"index": "mdx-vlm-alerts"}},
        },
    }

    def _build(self, config):
        with patch(
            "mdx.sink.vlm_enhanced_sink.sink_elastic.ElasticClient"
        ) as client_cls, patch(
            "mdx.sink.vlm_enhanced_sink.sink_elastic.ElasticConfig"
        ) as config_cls:
            sink = VLMEnhancedElasticSink.from_config(config)
        return sink, client_cls, config_cls

    def test_builds_a_client_and_keeps_both_indices(self):
        sink, client_cls, _config_cls = self._build(self.BASE_CONFIG)

        assert sink._incident_index == "mdx-vlm-incidents"
        assert sink._alert_index == "mdx-vlm-alerts"
        assert sink._elastic is client_cls.return_value

    def test_a_single_host_string_is_accepted(self):
        config = dict(self.BASE_CONFIG, elastic={"enabled": True, "hosts": "http://es:9200"})
        _sink, _client_cls, config_cls = self._build(config)

        assert config_cls.call_args.kwargs["hosts"] == ("http://es:9200",)

    def test_a_host_list_is_normalised_and_trimmed(self):
        config = dict(
            self.BASE_CONFIG,
            elastic={"enabled": True, "hosts": [" http://a:9200 ", "http://b:9200", ""]},
        )
        _sink, _client_cls, config_cls = self._build(config)

        assert config_cls.call_args.kwargs["hosts"] == ("http://a:9200", "http://b:9200")

    def test_missing_incident_index_raises(self):
        config = dict(
            self.BASE_CONFIG,
            vlm_enhanced_sink={"alert": {"elastic": {"index": "mdx-vlm-alerts"}}},
        )
        with pytest.raises(ValueError, match="incident.elastic.index"):
            self._build(config)

    def test_missing_alert_index_raises(self):
        config = dict(
            self.BASE_CONFIG,
            vlm_enhanced_sink={"incident": {"elastic": {"index": "mdx-vlm-incidents"}}},
        )
        with pytest.raises(ValueError, match="alert.elastic.index"):
            self._build(config)

    def test_disabled_elastic_raises(self):
        config = dict(self.BASE_CONFIG, elastic={"enabled": False, "hosts": ["http://es:9200"]})
        with pytest.raises(ValueError, match="elastic.enabled is false"):
            self._build(config)

    def test_missing_elastic_section_raises(self):
        config = dict(self.BASE_CONFIG)
        config.pop("elastic")
        with pytest.raises(ValueError, match="elastic.enabled is false"):
            self._build(config)

    @pytest.mark.parametrize("hosts", [None, [], 42, [""]])
    def test_missing_hosts_raises(self, hosts):
        config = dict(self.BASE_CONFIG, elastic={"enabled": True, "hosts": hosts})
        with pytest.raises(ValueError, match="no hosts configured"):
            self._build(config)

    @pytest.mark.xfail(
        strict=True,
        reason=(
            'Open defect: hosts: "" becomes the one-element tuple ("",), which '
            "is truthy, so the guard passes and the blank host only fails later "
            "at connection time instead of at config parse. Fix: drop blank "
            "entries before the check. When that lands this test XPASSes — drop "
            "the marker then."
        ),
    )
    def test_a_blank_host_string_is_rejected(self):
        config = dict(self.BASE_CONFIG, elastic={"enabled": True, "hosts": ""})

        with pytest.raises(ValueError, match="no hosts configured"):
            self._build(config)

    def test_null_sink_subsections_are_tolerated(self):
        config = dict(
            self.BASE_CONFIG,
            vlm_enhanced_sink={"incident": None, "alert": None},
        )
        with pytest.raises(ValueError, match="incident.elastic.index"):
            self._build(config)


class TestBuildRuntimeCategoryMapping:
    def test_empty_without_a_store_or_static_mapping(self, sink):
        assert sink._build_runtime_category_mapping({"category": "collision"}) == {}

    def test_static_mapping_is_used(self):
        sink = make_sink(category_mapping={"collision": "Crash"})
        assert sink._build_runtime_category_mapping({"category": "collision"}) == (
            {"collision": "Crash"}
        )

    def test_store_wins_over_the_static_mapping(self):
        store = MagicMock()
        store.get.return_value = {"output_category": "Vehicle collision"}
        sink = make_sink(category_mapping={"collision": "Crash"}, alert_config_store=store)

        assert sink._build_runtime_category_mapping({"category": "collision"}) == (
            {"collision": "Vehicle collision"}
        )

    def test_cleared_store_override_suppresses_the_static_mapping(self):
        """A PUT that clears output_category must not resurrect the file value."""
        store = MagicMock()
        store.get.return_value = {"output_category": None}
        sink = make_sink(category_mapping={"collision": "Crash"}, alert_config_store=store)

        assert sink._build_runtime_category_mapping({"category": "collision"}) == {}

    def test_store_failure_falls_back_to_the_static_mapping(self):
        store = MagicMock()
        store.get.side_effect = RuntimeError("ES timeout")
        sink = make_sink(category_mapping={"collision": "Crash"}, alert_config_store=store)

        assert sink._build_runtime_category_mapping({"category": "collision"}) == (
            {"collision": "Crash"}
        )

    def test_identity_mapping_is_dropped(self):
        sink = make_sink(category_mapping={"collision": "collision"})
        assert sink._build_runtime_category_mapping({"category": "collision"}) == {}

    def test_missing_category_yields_an_empty_mapping(self, sink):
        assert sink._build_runtime_category_mapping({}) == {}


class TestStoreSuccess:
    def test_incidents_go_to_the_incident_index(self, sink):
        sink._store_success("incident", dict(INCIDENT), {"verdict": "yes"}, "prompt")

        assert sink._elastic.write_event_response.call_args.args[3] == "mdx-vlm-incidents"

    def test_alerts_go_to_the_alert_index(self, sink):
        sink._store_success("alert", dict(INCIDENT), {"verdict": "yes"}, "prompt")

        assert sink._elastic.write_event_response.call_args.args[3] == "mdx-vlm-alerts"

    def test_the_vlm_payload_and_prompt_are_forwarded(self, sink):
        payload = {"verdict": "yes"}
        sink._store_success("incident", dict(INCIDENT), payload, "is there a crash?")

        args = sink._elastic.write_event_response.call_args.args
        assert args[1] is payload
        assert args[2] == "is there a crash?"

    def test_the_runtime_category_mapping_is_passed_as_an_argument(self):
        sink = make_sink(category_mapping={"collision": "Crash"})
        sink._store_success("incident", dict(INCIDENT), {}, "p")

        kwargs = sink._elastic.write_event_response.call_args.kwargs
        assert kwargs["category_mapping"] == {"collision": "Crash"}

    def test_the_document_category_is_not_mutated(self):
        sink = make_sink(category_mapping={"collision": "Crash"})
        document = dict(INCIDENT)

        sink._store_success("incident", document, {}, "p")

        assert document["category"] == "collision"

    def test_verdict_description_mapping_is_forwarded(self):
        mapping = {"collision": {"confirmed": "Crash confirmed"}}
        sink = make_sink(verdict_description_mapping=mapping)

        sink._store_success("incident", dict(INCIDENT), {}, "p")

        assert sink._elastic.write_event_response.call_args.kwargs[
            "verdict_description_mapping"
        ] is mapping

    def test_confirmed_verdict_is_marked_in_redis(self):
        redis = MagicMock()
        sink = make_sink(redis_handler=redis)

        sink._store_success("incident", dict(INCIDENT), {}, "p")

        redis.mark_verdict_confirmed.assert_called_once_with("fingerprint-1")

    def test_verdict_marking_is_case_insensitive(self):
        redis = MagicMock()
        sink = make_sink(redis_handler=redis)

        sink._store_success("incident", dict(INCIDENT, info={"verdict": "CONFIRMED"}), {}, "p")

        redis.mark_verdict_confirmed.assert_called_once()

    def test_other_verdicts_are_not_marked(self):
        redis = MagicMock()
        sink = make_sink(redis_handler=redis)

        sink._store_success("incident", dict(INCIDENT, info={"verdict": "rejected"}), {}, "p")

        redis.mark_verdict_confirmed.assert_not_called()

    def test_a_document_without_a_fingerprint_is_not_marked(self):
        redis = MagicMock()
        sink = make_sink(redis_handler=redis)
        document = dict(INCIDENT)
        del document["Id"]

        sink._store_success("incident", document, {}, "p")

        redis.mark_verdict_confirmed.assert_not_called()

    def test_a_write_failure_does_not_raise(self, sink):
        sink._elastic.write_event_response.side_effect = RuntimeError("cluster down")

        sink._store_success("incident", dict(INCIDENT), {}, "p")

    def test_a_failed_write_leaves_the_verdict_unmarked(self):
        """Marking first would suppress a retry of a document that never landed."""
        redis = MagicMock()
        sink = make_sink(redis_handler=redis)
        sink._elastic.write_event_response.side_effect = RuntimeError("cluster down")

        sink._store_success("incident", dict(INCIDENT), {}, "p")

        redis.mark_verdict_confirmed.assert_not_called()


class TestStoreError:
    ERROR_PAYLOAD = {"error": "VLM timeout"}

    def test_incidents_go_to_the_incident_index(self, sink):
        sink._store_error("incident", dict(INCIDENT), self.ERROR_PAYLOAD)

        assert sink._elastic.write_event_response.call_args.args[3] == "mdx-vlm-incidents"

    def test_alerts_go_to_the_alert_index(self, sink):
        sink._store_error("alert", dict(INCIDENT), self.ERROR_PAYLOAD)

        assert sink._elastic.write_event_response.call_args.args[3] == "mdx-vlm-alerts"

    def test_the_stored_user_prompt_is_reused(self, sink):
        sink._store_error("incident", dict(INCIDENT), self.ERROR_PAYLOAD)

        assert sink._elastic.write_event_response.call_args.args[2] == "is there a crash?"

    def test_a_document_without_a_prompt_is_tolerated(self, sink):
        document = dict(INCIDENT, info={})
        sink._store_error("incident", document, self.ERROR_PAYLOAD)

        assert sink._elastic.write_event_response.call_args.args[2] is None

    def test_a_write_failure_does_not_raise(self, sink):
        sink._elastic.write_event_response.side_effect = RuntimeError("cluster down")

        sink._store_error("incident", dict(INCIDENT), self.ERROR_PAYLOAD)


class TestUpdateEnrichment:
    ENRICHMENT = EnrichmentResponse(
        reasoning="two vehicles involved", response_code=200, response_status="OK"
    )

    def test_updates_the_daily_index_document(self, sink):
        sink._elastic.generate_daily_index_name.return_value = "mdx-vlm-incidents-2025-09-19"

        sink.update_enrichment(dict(INCIDENT), self.ENRICHMENT)

        kwargs = sink._elastic.update_document.call_args.kwargs
        assert kwargs["index"] == "mdx-vlm-incidents-2025-09-19"
        assert kwargs["doc_id"] == "fingerprint-1"

    def test_the_enrichment_is_stored_as_compact_json(self, sink):
        sink.update_enrichment(dict(INCIDENT), self.ENRICHMENT)

        stored = sink._elastic.update_document.call_args.kwargs["partial_doc"]["info"]["enrichment"]
        assert json.loads(stored)["reasoning"] == "two vehicles involved"
        assert ", " not in stored  # separators=(',', ':')

    def test_alerts_resolve_against_the_alert_index(self, sink):
        document = dict(INCIDENT, notification_type="alert")

        sink.update_enrichment(document, self.ENRICHMENT)

        assert sink._elastic.generate_daily_index_name.call_args.args[0] == "mdx-vlm-alerts"

    def test_incidents_resolve_against_the_incident_index(self, sink):
        sink.update_enrichment(dict(INCIDENT), self.ENRICHMENT)

        assert sink._elastic.generate_daily_index_name.call_args.args[0] == "mdx-vlm-incidents"

    def test_a_document_without_a_fingerprint_is_skipped(self, sink):
        document = dict(INCIDENT)
        del document["Id"]

        sink.update_enrichment(document, self.ENRICHMENT)

        sink._elastic.update_document.assert_not_called()

    def test_non_string_timestamps_are_stringified(self, sink):
        sink.update_enrichment(dict(INCIDENT, timestamp=1700000000), self.ENRICHMENT)

        assert sink._elastic.generate_daily_index_name.call_args.args[1] == "1700000000"

    def test_an_update_failure_does_not_raise(self, sink):
        sink._elastic.update_document.side_effect = RuntimeError("cluster down")

        sink.update_enrichment(dict(INCIDENT), self.ENRICHMENT)


class TestAsyncPublishPath:
    """Event-loop mode awaits the sink instead of off-loading it to a thread.

    The async mirrors must keep the sync contract: index routing by event
    kind, the runtime category override, a swallowed write failure, and the
    verdict only marked after a successful write.
    """

    @pytest.fixture
    def sink(self):
        sink = make_sink()
        sink._elastic.write_event_response_async = AsyncMock()
        sink._elastic.update_document_async = AsyncMock()
        sink._elastic.aclose_async = AsyncMock()
        sink._prepare_publish = MagicMock(return_value=("incident", dict(INCIDENT)))
        return sink

    @pytest.mark.asyncio
    async def test_publish_success_prepares_then_stores(self, sink):
        await sink.publish_success_async(dict(INCIDENT), "prompt", "sys", {"verdict": "yes"})

        sink._prepare_publish.assert_called_once()
        sink._elastic.write_event_response_async.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_publish_error_prepares_then_stores(self, sink):
        await sink.publish_error_async(dict(INCIDENT), "prompt", "sys", {"error": "timeout"})

        sink._prepare_publish.assert_called_once()
        sink._elastic.write_event_response_async.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_incidents_go_to_the_incident_index(self, sink):
        await sink._store_success_async("incident", dict(INCIDENT), {}, "prompt")

        assert sink._elastic.write_event_response_async.call_args.args[3] == "mdx-vlm-incidents"

    @pytest.mark.asyncio
    async def test_alerts_go_to_the_alert_index(self, sink):
        await sink._store_success_async("alert", dict(INCIDENT), {}, "prompt")

        assert sink._elastic.write_event_response_async.call_args.args[3] == "mdx-vlm-alerts"

    @pytest.mark.asyncio
    async def test_the_runtime_category_mapping_is_passed(self):
        sink = make_sink(category_mapping={"collision": "Crash"})
        sink._elastic.write_event_response_async = AsyncMock()

        await sink._store_success_async("incident", dict(INCIDENT), {}, "prompt")

        kwargs = sink._elastic.write_event_response_async.call_args.kwargs
        assert kwargs["category_mapping"] == {"collision": "Crash"}

    @pytest.mark.asyncio
    async def test_a_confirmed_verdict_is_marked(self):
        redis = MagicMock()
        redis.mark_verdict_confirmed_async = AsyncMock()
        sink = make_sink(redis_handler=redis)
        sink._elastic.write_event_response_async = AsyncMock()

        await sink._store_success_async("incident", dict(INCIDENT), {}, "prompt")

        redis.mark_verdict_confirmed_async.assert_awaited_once_with("fingerprint-1")

    @pytest.mark.asyncio
    async def test_a_failed_write_leaves_the_verdict_unmarked(self):
        redis = MagicMock()
        redis.mark_verdict_confirmed_async = AsyncMock()
        sink = make_sink(redis_handler=redis)
        sink._elastic.write_event_response_async = AsyncMock(
            side_effect=RuntimeError("cluster down")
        )

        await sink._store_success_async("incident", dict(INCIDENT), {}, "prompt")

        redis.mark_verdict_confirmed_async.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_other_verdicts_are_not_marked(self):
        redis = MagicMock()
        redis.mark_verdict_confirmed_async = AsyncMock()
        sink = make_sink(redis_handler=redis)
        sink._elastic.write_event_response_async = AsyncMock()

        await sink._store_success_async(
            "incident", dict(INCIDENT, info={"verdict": "rejected"}), {}, "prompt"
        )

        redis.mark_verdict_confirmed_async.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_stored_user_prompt_is_reused_on_the_error_path(self, sink):
        await sink._store_error_async("incident", dict(INCIDENT), {"error": "timeout"})

        assert sink._elastic.write_event_response_async.call_args.args[2] == "is there a crash?"

    @pytest.mark.asyncio
    async def test_an_error_write_failure_does_not_raise(self, sink):
        sink._elastic.write_event_response_async.side_effect = RuntimeError("cluster down")

        await sink._store_error_async("incident", dict(INCIDENT), {"error": "timeout"})


class TestUpdateEnrichmentAsync:
    ENRICHMENT = EnrichmentResponse(
        reasoning="two vehicles involved", response_code=200, response_status="OK"
    )

    @pytest.fixture
    def sink(self):
        sink = make_sink()
        sink._elastic.update_document_async = AsyncMock()
        sink._elastic.generate_daily_index_name.return_value = "mdx-vlm-incidents-2025-09-19"
        return sink

    @pytest.mark.asyncio
    async def test_updates_the_daily_index_document(self, sink):
        await sink.update_enrichment_async(dict(INCIDENT), self.ENRICHMENT)

        kwargs = sink._elastic.update_document_async.call_args.kwargs
        assert kwargs["index"] == "mdx-vlm-incidents-2025-09-19"
        assert kwargs["doc_id"] == "fingerprint-1"

    @pytest.mark.asyncio
    async def test_the_enrichment_is_stored_as_compact_json(self, sink):
        await sink.update_enrichment_async(dict(INCIDENT), self.ENRICHMENT)

        stored = sink._elastic.update_document_async.call_args.kwargs["partial_doc"]["info"][
            "enrichment"
        ]
        assert json.loads(stored)["reasoning"] == "two vehicles involved"
        assert ", " not in stored

    @pytest.mark.asyncio
    async def test_alerts_resolve_against_the_alert_index(self, sink):
        await sink.update_enrichment_async(
            dict(INCIDENT, notification_type="alert"), self.ENRICHMENT
        )

        assert sink._elastic.generate_daily_index_name.call_args.args[0] == "mdx-vlm-alerts"

    @pytest.mark.asyncio
    async def test_a_document_without_a_fingerprint_is_skipped(self, sink):
        document = dict(INCIDENT)
        del document["Id"]

        await sink.update_enrichment_async(document, self.ENRICHMENT)

        sink._elastic.update_document_async.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_string_timestamps_are_stringified(self, sink):
        await sink.update_enrichment_async(dict(INCIDENT, timestamp=1700000000), self.ENRICHMENT)

        assert sink._elastic.generate_daily_index_name.call_args.args[1] == "1700000000"

    @pytest.mark.asyncio
    async def test_an_update_failure_does_not_raise(self, sink):
        sink._elastic.update_document_async.side_effect = RuntimeError("cluster down")

        await sink.update_enrichment_async(dict(INCIDENT), self.ENRICHMENT)


class TestAcloseAsync:
    @pytest.mark.asyncio
    async def test_closes_the_elastic_client(self):
        sink = make_sink()
        sink._elastic.aclose_async = AsyncMock()

        await sink.aclose_async()

        sink._elastic.aclose_async.assert_awaited_once()
