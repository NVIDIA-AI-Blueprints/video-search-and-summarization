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

"""Unit tests for IncidentService."""

from unittest.mock import MagicMock

import pytest

from realtime.config import ErrorCode, ResponseStatus
from realtime.services.incident_service import IncidentService


# ---------------------------------------------------------------------------
# list_incidents — happy path
# ---------------------------------------------------------------------------

class TestListIncidentsSuccess:
    """IncidentService.list_incidents — success paths."""

    @pytest.mark.asyncio
    async def test_returns_200_with_hits(self, incident_service, mock_es_client):
        data, code = await incident_service.list_incidents()

        assert code == 200
        assert data["status"] == ResponseStatus.SUCCESS
        assert data["count"] == 2
        assert data["total"] == 2
        assert len(data["incidents"]) == 2

    @pytest.mark.asyncio
    async def test_each_incident_has_meta(self, incident_service):
        data, _ = await incident_service.list_incidents()

        for inc in data["incidents"]:
            assert "_id" in inc
            assert "_index" in inc

    @pytest.mark.asyncio
    async def test_sensor_id_filter(self, incident_service, mock_es_client):
        await incident_service.list_incidents(sensor_id="cam-1")

        call_kwargs = mock_es_client.client.search.call_args
        query = call_kwargs.kwargs.get("query") or call_kwargs[1].get("query")
        must = query["bool"]["must"]
        assert any(c.get("term", {}).get("sensorId.keyword") == "cam-1" for c in must)

    @pytest.mark.asyncio
    async def test_sensor_id_filter_uses_keyword_for_hyphenated_ids(
        self,
        incident_service,
        mock_es_client,
    ):
        await incident_service.list_incidents(sensor_id="realtime-source")

        call_kwargs = mock_es_client.client.search.call_args
        query = call_kwargs.kwargs.get("query") or call_kwargs[1].get("query")
        must = query["bool"]["must"]
        terms = [c.get("term", {}) for c in must]
        assert {"sensorId.keyword": "realtime-source"} in terms
        assert {"sensorId": "realtime-source"} not in terms

    @pytest.mark.asyncio
    async def test_category_filter(self, incident_service, mock_es_client):
        await incident_service.list_incidents(category="fire")

        call_kwargs = mock_es_client.client.search.call_args
        query = call_kwargs.kwargs.get("query") or call_kwargs[1].get("query")
        must = query["bool"]["must"]
        assert any(c.get("term", {}).get("category.keyword") == "fire" for c in must)

    @pytest.mark.asyncio
    async def test_time_range_filter(self, incident_service, mock_es_client):
        await incident_service.list_incidents(
            start_time="2025-01-01T00:00:00Z",
            end_time="2025-01-02T00:00:00Z",
        )

        call_kwargs = mock_es_client.client.search.call_args
        query = call_kwargs.kwargs.get("query") or call_kwargs[1].get("query")
        must = query["bool"]["must"]
        range_clauses = [c for c in must if "range" in c]
        assert len(range_clauses) == 1
        ts = range_clauses[0]["range"]["timestamp"]
        assert ts["gte"] == "2025-01-01T00:00:00Z"
        assert ts["lte"] == "2025-01-02T00:00:00Z"

    @pytest.mark.asyncio
    async def test_no_filters_uses_match_all(self, incident_service, mock_es_client):
        await incident_service.list_incidents()

        call_kwargs = mock_es_client.client.search.call_args
        query = call_kwargs.kwargs.get("query") or call_kwargs[1].get("query")
        assert "match_all" in query

    @pytest.mark.asyncio
    async def test_pagination_params_forwarded(self, incident_service, mock_es_client):
        await incident_service.list_incidents(limit=25, offset=50)

        call_kwargs = mock_es_client.client.search.call_args.kwargs
        assert call_kwargs["size"] == 25
        assert call_kwargs["from_"] == 50

    @pytest.mark.asyncio
    async def test_index_pattern(self, incident_service, mock_es_client):
        await incident_service.list_incidents()

        call_kwargs = mock_es_client.client.search.call_args.kwargs
        assert call_kwargs["index"] == "mdx-vlm-incidents-*"

    @pytest.mark.asyncio
    async def test_sort_descending_timestamp(self, incident_service, mock_es_client):
        await incident_service.list_incidents()

        call_kwargs = mock_es_client.client.search.call_args.kwargs
        assert call_kwargs["sort"] == [{"timestamp": {"order": "desc"}}]


# ---------------------------------------------------------------------------
# list_incidents — failure paths
# ---------------------------------------------------------------------------

class TestListIncidentsFailure:
    """IncidentService.list_incidents — error handling."""

    @pytest.mark.asyncio
    async def test_no_es_client_returns_503(self):
        svc = IncidentService(es_client=None)
        data, code = await svc.list_incidents()

        assert code == 503
        assert data["error"] == ErrorCode.ELASTICSEARCH_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_es_search_exception_returns_500(self, mock_es_client):
        mock_es_client.client.search.side_effect = Exception("cluster timeout")
        svc = IncidentService(es_client=mock_es_client)

        data, code = await svc.list_incidents()

        assert code == 500
        assert data["error"] == ErrorCode.ELASTICSEARCH_QUERY_FAILED
        assert "cluster timeout" in data["message"]

    @pytest.mark.asyncio
    async def test_empty_results(self, mock_es_client):
        mock_es_client.client.search.return_value = {
            "hits": {"total": {"value": 0}, "hits": []}
        }
        svc = IncidentService(es_client=mock_es_client)

        data, code = await svc.list_incidents()

        assert code == 200
        assert data["count"] == 0
        assert data["total"] == 0
        assert data["incidents"] == []

    @pytest.mark.asyncio
    async def test_total_as_int(self, mock_es_client):
        """ES 6.x returns total as int, not dict."""
        mock_es_client.client.search.return_value = {
            "hits": {"total": 42, "hits": []}
        }
        svc = IncidentService(es_client=mock_es_client)

        data, code = await svc.list_incidents()

        assert code == 200
        assert data["total"] == 42


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------

class TestIncidentServiceInit:
    """IncidentService initialization."""

    def test_with_injected_client(self, mock_es_client):
        svc = IncidentService(es_client=mock_es_client, index_base="custom-index")
        assert svc._es_client is mock_es_client
        assert svc._index_base == "custom-index"

    def test_without_client(self):
        svc = IncidentService()
        assert svc._es_client is None

    def test_custom_index_base(self):
        svc = IncidentService(index_base="my-incidents")
        assert svc._index_base == "my-incidents"


# ---------------------------------------------------------------------------
# Consolidation — read-time grouping of repeated positives
# ---------------------------------------------------------------------------

_CONSOL_DEFAULT = {
    "max_inter_alert_gap_seconds": 60,
    "max_event_duration_seconds": 300,
    "representative": "latest",
}


def _chunk(
    sensor="cam-1",
    category="alert",
    req="req-1",
    idx=1,
    start="2025-01-01T00:00:00.000Z",
    end="2025-01-01T00:00:30.000Z",
    reasoning="r",
    doc_id=None,
    extra=None,
):
    """Build a raw chunk-level incident document (post-read shape)."""
    doc = {
        "sensorId": sensor,
        "category": category,
        "timestamp": start,
        "end": end,
        "info": {
            "requestId": req,
            "chunkIdx": str(idx),
            "verdict": "confirmed",
            "reasoning": reasoning,
        },
        "llm": {"queries": [{"id": f"{req}:{idx}"}]},
        "_id": doc_id or f"{sensor}-{req}-{idx}",
        "_index": "mdx-vlm-incidents-2025-01-01",
    }
    if extra:
        doc.update(extra)
    return doc


def _as_hit(chunk):
    """Convert a chunk document into an Elasticsearch search hit."""
    body = {k: v for k, v in chunk.items() if k not in ("_id", "_index")}
    return {"_id": chunk["_id"], "_index": chunk["_index"], "_source": body}


def _consolidator(cfg=None):
    return IncidentService(consolidation=cfg or dict(_CONSOL_DEFAULT))


class TestConsolidationGrouping:
    """Pure grouping logic — IncidentService._consolidate."""

    def test_consecutive_within_gap_merge(self):
        docs = [
            _chunk(idx=13, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z"),
            _chunk(idx=14, start="2025-01-01T00:00:25.000Z", end="2025-01-01T00:00:55.000Z"),
            _chunk(idx=20, start="2025-01-01T00:05:00.000Z", end="2025-01-01T00:05:30.000Z"),
        ]
        events = _consolidator()._consolidate(docs)
        assert len(events) == 2
        merged = next(e for e in events if e["info"]["chunkCount"] == "2")
        assert merged["info"]["isConsolidated"] == "true"
        assert merged["info"]["chunkIdxRange"] == "13-14"
        assert merged["timestamp"] == "2025-01-01T00:00:00.000Z"
        assert merged["end"] == "2025-01-01T00:00:55.000Z"
        assert len(merged["llm"]["queries"]) == 2

    def test_session_consecutive_chunkidx_merges_beyond_gap(self):
        # Time gap exceeds the bound, but same requestId + consecutive chunkIdx.
        docs = [
            _chunk(idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z"),
            _chunk(idx=2, start="2025-01-01T00:01:35.000Z", end="2025-01-01T00:02:05.000Z"),
        ]
        events = _consolidator()._consolidate(docs)
        assert len(events) == 1
        assert events[0]["info"]["chunkCount"] == "2"

    def test_new_event_after_bound(self):
        docs = [
            _chunk(req="a", idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z"),
            _chunk(req="b", idx=9, start="2025-01-01T00:02:00.000Z", end="2025-01-01T00:02:30.000Z"),
        ]
        events = _consolidator()._consolidate(docs)
        assert len(events) == 2
        assert all(e["info"]["chunkCount"] == "1" for e in events)

    def test_duration_cap_splits(self):
        cfg = dict(_CONSOL_DEFAULT, max_event_duration_seconds=60)
        docs = [
            _chunk(idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z"),
            _chunk(idx=2, start="2025-01-01T00:00:30.000Z", end="2025-01-01T00:01:00.000Z"),
            _chunk(idx=3, start="2025-01-01T00:01:00.000Z", end="2025-01-01T00:01:30.000Z"),
            _chunk(idx=4, start="2025-01-01T00:01:30.000Z", end="2025-01-01T00:02:00.000Z"),
        ]
        events = _consolidator(cfg)._consolidate(docs)
        assert len(events) == 2

    def test_different_sensor_not_merged(self):
        docs = [_chunk(sensor="cam-1", idx=1), _chunk(sensor="cam-2", idx=1)]
        assert len(_consolidator()._consolidate(docs)) == 2

    def test_different_category_not_merged(self):
        docs = [_chunk(category="fire", idx=1), _chunk(category="smoke", idx=1)]
        assert len(_consolidator()._consolidate(docs)) == 2

    def test_end_missing_falls_back_to_timestamp(self):
        d0 = _chunk(idx=1, start="2025-01-01T00:00:00.000Z")
        d1 = _chunk(idx=2, start="2025-01-01T00:00:20.000Z")
        d0.pop("end")
        d1.pop("end")
        events = _consolidator()._consolidate([d0, d1])
        assert len(events) == 1
        assert events[0]["end"] == "2025-01-01T00:00:20.000Z"

    def test_representative_longest_reasoning(self):
        cfg = dict(_CONSOL_DEFAULT, representative="longest_reasoning")
        docs = [
            _chunk(idx=1, reasoning="short"),
            _chunk(
                idx=2,
                start="2025-01-01T00:00:25.000Z",
                end="2025-01-01T00:00:55.000Z",
                reasoning="a much longer reasoning text",
            ),
        ]
        events = _consolidator(cfg)._consolidate(docs)
        assert len(events) == 1
        assert events[0]["info"]["reasoning"] == "a much longer reasoning text"

    def test_events_sorted_newest_first(self):
        docs = [
            _chunk(sensor="cam-1", idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z"),
            _chunk(sensor="cam-2", idx=1, start="2025-01-01T01:00:00.000Z", end="2025-01-01T01:00:30.000Z"),
        ]
        events = _consolidator()._consolidate(docs)
        assert events[0]["sensorId"] == "cam-2"
        assert events[-1]["sensorId"] == "cam-1"

    def test_empty_input(self):
        assert _consolidator()._consolidate([]) == []

    def test_single_doc_one_event(self):
        events = _consolidator()._consolidate([_chunk(idx=1)])
        assert len(events) == 1
        assert events[0]["info"]["chunkCount"] == "1"

    def test_nvschema_fields_preserved(self):
        extra = {
            "type": "mdx-vlm-incidents",
            "isAnomaly": True,
            "analyticsModule": {"source": "rtvi-vlm"},
            "place": {"name": "dock"},
        }
        docs = [
            _chunk(idx=1, extra=extra),
            _chunk(idx=2, start="2025-01-01T00:00:25.000Z", end="2025-01-01T00:00:55.000Z", extra=extra),
        ]
        e = _consolidator()._consolidate(docs)[0]
        assert e["type"] == "mdx-vlm-incidents"
        assert e["isAnomaly"] is True
        assert e["analyticsModule"]["source"] == "rtvi-vlm"
        assert e["place"]["name"] == "dock"

    def test_chunk_count_sums_to_input_no_loss(self):
        docs = [
            _chunk(idx=i, start=f"2025-01-01T00:0{i}:00.000Z", end=f"2025-01-01T00:0{i}:30.000Z")
            for i in range(1, 4)
        ]
        events = _consolidator()._consolidate(docs)
        total_chunks = sum(int(e["info"]["chunkCount"]) for e in events)
        assert total_chunks == len(docs)

    def test_event_id_distinct_from_raw_chunk_ids(self):
        docs = [
            _chunk(idx=1, doc_id="fp1", extra={"Id": "fp1"}),
            _chunk(
                idx=2,
                start="2025-01-01T00:00:25.000Z",
                end="2025-01-01T00:00:55.000Z",
                doc_id="fp2",
                extra={"Id": "fp2"},
            ),
        ]
        event = _consolidator()._consolidate(docs)[0]
        raw_ids = {"fp1", "fp2"}
        assert event["Id"] not in raw_ids
        assert event["_id"] == event["Id"]
        assert event["Id"].startswith("evt-")


class TestConsolidationService:
    """IncidentService.list_incidents — consolidation behaviour."""

    @pytest.mark.asyncio
    async def test_consolidate_false_passthrough(self, mock_es_client):
        chunks = [
            _chunk(idx=1),
            _chunk(idx=2, start="2025-01-01T00:00:25.000Z", end="2025-01-01T00:00:55.000Z"),
        ]
        mock_es_client.client.search.return_value = {
            "hits": {"total": {"value": 2}, "hits": [_as_hit(c) for c in chunks]}
        }
        svc = IncidentService(es_client=mock_es_client, consolidation=dict(_CONSOL_DEFAULT))
        data, code = await svc.list_incidents(consolidate=False)
        assert code == 200
        assert data["count"] == 2
        assert data["total"] == 2
        assert all("isConsolidated" not in i.get("info", {}) for i in data["incidents"])

    @pytest.mark.asyncio
    async def test_consolidate_true_groups_and_keeps_raw_total(self, mock_es_client):
        chunks = [
            _chunk(sensor="cam-1", idx=1),
            _chunk(sensor="cam-1", idx=2, start="2025-01-01T00:00:25.000Z", end="2025-01-01T00:00:55.000Z"),
            _chunk(sensor="cam-2", idx=1),
        ]
        mock_es_client.client.search.return_value = {
            "hits": {"total": {"value": 3}, "hits": [_as_hit(c) for c in chunks]}
        }
        svc = IncidentService(es_client=mock_es_client, consolidation=dict(_CONSOL_DEFAULT))
        data, code = await svc.list_incidents(consolidate=True)
        assert code == 200
        assert data["total"] == 3
        assert data["count"] == 2
        assert all(i["info"]["isConsolidated"] == "true" for i in data["incidents"])

    @pytest.mark.asyncio
    async def test_omit_param_returns_raw(self, mock_es_client):
        # Consolidation is opt-in: omitting the param returns raw chunks.
        chunks = [
            _chunk(idx=1),
            _chunk(idx=2, start="2025-01-01T00:00:25.000Z", end="2025-01-01T00:00:55.000Z"),
        ]
        mock_es_client.client.search.return_value = {
            "hits": {"total": {"value": 2}, "hits": [_as_hit(c) for c in chunks]}
        }
        svc = IncidentService(es_client=mock_es_client, consolidation=dict(_CONSOL_DEFAULT))
        data, _ = await svc.list_incidents()
        assert data["count"] == 2
        assert all("isConsolidated" not in i.get("info", {}) for i in data["incidents"])

    @pytest.mark.asyncio
    async def test_consolidate_true_groups_with_tuning_only_config(self, mock_es_client):
        chunks = [
            _chunk(idx=1),
            _chunk(idx=2, start="2025-01-01T00:00:25.000Z", end="2025-01-01T00:00:55.000Z"),
        ]
        mock_es_client.client.search.return_value = {
            "hits": {"total": {"value": 2}, "hits": [_as_hit(c) for c in chunks]}
        }
        svc = IncidentService(es_client=mock_es_client, consolidation=dict(_CONSOL_DEFAULT))
        data, _ = await svc.list_incidents(consolidate=True)
        assert data["count"] == 1
