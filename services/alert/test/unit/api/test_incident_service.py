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
from realtime.services.incident_service import IncidentService, TimeBoundCombiner


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

    def test_large_gap_splits_even_same_session(self):
        # Same requestId + consecutive chunkIdx, but the inter-alert gap exceeds
        # the bound (65s > 60s): the gap is authoritative, so these split.
        docs = [
            _chunk(idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z"),
            _chunk(idx=2, start="2025-01-01T00:01:35.000Z", end="2025-01-01T00:02:05.000Z"),
        ]
        events = _consolidator()._consolidate(docs)
        assert len(events) == 2

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

    def test_events_sorted_by_start_not_end(self):
        docs = [
            # cam-1: started earliest, ends latest (two chunks merged)
            _chunk(sensor="cam-1", idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z"),
            _chunk(sensor="cam-1", idx=2, start="2025-01-01T00:00:20.000Z", end="2025-01-01T00:05:00.000Z"),
            # cam-2: started later but ends earlier
            _chunk(sensor="cam-2", idx=1, start="2025-01-01T00:03:00.000Z", end="2025-01-01T00:03:30.000Z"),
        ]
        events = _consolidator()._consolidate(docs)
        assert len(events) == 2
        assert events[0]["sensorId"] == "cam-2"  # later start ranks first
        assert events[1]["sensorId"] == "cam-1"

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

    def test_gap_exactly_at_bound_merges(self):
        # gap == max_inter_alert_gap_seconds (60s) is inclusive -> merge
        docs = [
            _chunk(idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z"),
            _chunk(idx=2, start="2025-01-01T00:01:30.000Z", end="2025-01-01T00:02:00.000Z"),
        ]
        assert len(_consolidator()._consolidate(docs)) == 1

    def test_gap_one_second_over_bound_splits(self):
        docs = [
            _chunk(idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z"),
            _chunk(idx=2, start="2025-01-01T00:01:31.000Z", end="2025-01-01T00:02:01.000Z"),
        ]
        assert len(_consolidator()._consolidate(docs)) == 2

    def test_duration_exactly_at_cap_merges(self):
        # span == max_event_duration_seconds is inclusive -> merge
        cfg = dict(_CONSOL_DEFAULT, max_event_duration_seconds=60)
        docs = [
            _chunk(idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z"),
            _chunk(idx=2, start="2025-01-01T00:00:30.000Z", end="2025-01-01T00:01:00.000Z"),
        ]
        assert len(_consolidator(cfg)._consolidate(docs)) == 1

    def test_duration_one_second_over_cap_splits(self):
        cfg = dict(_CONSOL_DEFAULT, max_event_duration_seconds=60)
        docs = [
            _chunk(idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z"),
            _chunk(idx=2, start="2025-01-01T00:00:30.000Z", end="2025-01-01T00:01:01.000Z"),
        ]
        assert len(_consolidator(cfg)._consolidate(docs)) == 2

    def test_null_max_duration_is_unbounded(self):
        # max_event_duration_seconds=None disables the outer cap entirely
        cfg = dict(_CONSOL_DEFAULT, max_event_duration_seconds=None)
        docs = [
            _chunk(
                idx=i,
                start=f"2025-01-01T00:{i:02d}:00.000Z",
                end=f"2025-01-01T00:{i:02d}:30.000Z",
            )
            for i in range(0, 12)  # ~11 min span, well past the old 300s cap
        ]
        events = _consolidator(cfg)._consolidate(docs)
        assert len(events) == 1
        assert events[0]["info"]["chunkCount"] == "12"

    def test_malformed_chunks_are_dropped(self):
        good = _chunk(idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z")
        no_ts = _chunk(idx=2, start="2025-01-01T00:00:25.000Z", end="2025-01-01T00:00:55.000Z")
        no_ts.pop("timestamp")
        no_sensor = _chunk(sensor=None, idx=3, start="2025-01-01T00:00:40.000Z")
        no_cat = _chunk(category=None, idx=4, start="2025-01-01T00:00:45.000Z")
        events = _consolidator()._consolidate([good, no_ts, no_sensor, no_cat])
        # malformed chunks (missing sensorId/category/timestamp) are dropped
        assert len(events) == 1
        assert events[0]["info"]["chunkCount"] == "1"

    def test_unparseable_timestamp_dropped(self):
        good = _chunk(idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z")
        bad = _chunk(idx=2, start="not-a-timestamp", end="2025-01-01T00:00:55.000Z")
        events = _consolidator()._consolidate([good, bad])
        assert len(events) == 1
        assert events[0]["info"]["chunkCount"] == "1"

    def test_unparseable_end_falls_back_to_timestamp(self):
        c = _chunk(idx=1, start="2025-01-01T00:00:00.000Z", end="not-a-timestamp")
        event = _consolidator()._consolidate([c])[0]
        assert event["end"] == "2025-01-01T00:00:00.000Z"

    def test_event_carries_chunk_ids_list(self):
        docs = [
            _chunk(idx=1, doc_id="c1", start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z"),
            _chunk(idx=2, doc_id="c2", start="2025-01-01T00:00:25.000Z", end="2025-01-01T00:00:55.000Z"),
        ]
        event = _consolidator()._consolidate(docs)[0]
        assert isinstance(event["chunk_ids"], list)
        assert set(event["chunk_ids"]) == {"c1", "c2"}
        assert len(event["chunk_ids"]) == int(event["info"]["chunkCount"])

    def test_merged_llm_queries_are_capped(self):
        # one chunk carrying more queries than the cap -> capped, true count kept
        big = _chunk(idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z")
        big["llm"] = {"queries": [{"id": f"q{i}"} for i in range(500)]}
        event = _consolidator()._consolidate([big])[0]
        assert len(event["llm"]["queries"]) == 200      # _MAX_MERGED_QUERIES
        assert event["info"]["mergedQueryCount"] == "500"

    def test_missing_info_key_dropped(self):
        # No info block means no confirmed verdict — such chunks must not
        # consolidate (and must not crash the fold).
        c1 = _chunk(idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z")
        c2 = _chunk(idx=2, start="2025-01-01T00:00:25.000Z", end="2025-01-01T00:00:55.000Z")
        c1.pop("info")
        c2.pop("info")
        assert _consolidator()._consolidate([c1, c2]) == []

    def test_rejected_chunk_never_joins_an_event(self):
        # An adjacent rejected chunk must not merge, and must never supply
        # the event's verdict even when it is the latest chunk.
        confirmed = _chunk(idx=0, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z")
        rejected = _chunk(idx=1, start="2025-01-01T00:00:30.000Z", end="2025-01-01T00:01:00.000Z")
        rejected["info"]["verdict"] = "rejected"
        rejected["info"]["triggerPhrase"] = "no"
        events = _consolidator()._consolidate([confirmed, rejected])
        assert len(events) == 1
        e = events[0]
        assert e["info"]["chunkCount"] == "1"
        assert e["info"]["verdict"] == "confirmed"       # representative=latest must not pick the rejected chunk
        assert e["chunk_ids"] == [confirmed["_id"]]
        assert e["end"] == confirmed["end"]              # span not extended by the rejected chunk

    def test_missing_verdict_dropped(self):
        c = _chunk(idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z")
        del c["info"]["verdict"]
        assert _consolidator()._consolidate([c]) == []


class TestConsolidationSplitReason:
    """_split_reason classifies why an event ended (for the dedup metric)."""

    def test_reason_gap(self):
        current = [_chunk(idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z")]
        doc = _chunk(idx=2, start="2025-01-01T00:02:00.000Z", end="2025-01-01T00:02:30.000Z")
        assert IncidentService._split_reason(current, doc, 60, 300) == "gap"

    def test_reason_outer(self):
        current = [_chunk(idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z")]
        doc = _chunk(idx=2, start="2025-01-01T00:05:30.000Z", end="2025-01-01T00:06:00.000Z")
        assert IncidentService._split_reason(current, doc, 60, 300) == "outer"


class TestTimeBoundCombiner:
    """The pluggable combination strategy — should_extend -> (extend, reason)."""

    def test_extends_within_bounds(self):
        event = [_chunk(idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z")]
        cand = _chunk(idx=2, start="2025-01-01T00:00:25.000Z", end="2025-01-01T00:00:55.000Z")
        assert TimeBoundCombiner(60, 300).should_extend(event, cand) == (True, "")

    def test_gap_break(self):
        event = [_chunk(idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z")]
        cand = _chunk(idx=2, start="2025-01-01T00:02:00.000Z", end="2025-01-01T00:02:30.000Z")
        assert TimeBoundCombiner(60, 300).should_extend(event, cand) == (False, "gap")

    def test_outer_break(self):
        event = [_chunk(idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z")]
        cand = _chunk(idx=2, start="2025-01-01T00:05:30.000Z", end="2025-01-01T00:06:00.000Z")
        assert TimeBoundCombiner(60, 300).should_extend(event, cand) == (False, "outer")

    def test_malformed_candidate(self):
        event = [_chunk(idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z")]
        cand = _chunk(idx=2, start="2025-01-01T00:00:25.000Z", end="2025-01-01T00:00:55.000Z")
        cand.pop("timestamp")
        assert TimeBoundCombiner(60, 300).should_extend(event, cand) == (False, "malformed")

    def test_consolidate_uses_the_combiner(self):
        # the fold uses IncidentService._combiner(); default is TimeBoundCombiner
        svc = IncidentService(consolidation=dict(_CONSOL_DEFAULT))
        assert isinstance(svc._combiner(), TimeBoundCombiner)

    def test_fold_emits_dedup_metrics(self, monkeypatch):
        # the fold path must emit the aggregate counters + split reason
        from realtime.services import incident_service as mod

        chunks_in, events_out, duration = MagicMock(), MagicMock(), MagicMock()
        split = MagicMock()
        monkeypatch.setattr(mod, "DEDUP_CHUNKS_IN", chunks_in)
        monkeypatch.setattr(mod, "DEDUP_EVENTS_OUT", events_out)
        monkeypatch.setattr(mod, "DEDUP_SPLIT_REASON", split)
        monkeypatch.setattr(mod, "DEDUP_DURATION", duration)

        docs = [
            _chunk(idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z"),
            _chunk(idx=2, start="2025-01-01T00:00:25.000Z", end="2025-01-01T00:00:55.000Z"),  # merges
            _chunk(idx=9, start="2025-01-01T00:02:30.000Z", end="2025-01-01T00:03:00.000Z"),  # gap split (within outer cap)
        ]
        bad = _chunk(idx=3, start="not-a-timestamp")  # dropped, but still counted as fed-in
        events = _consolidator()._consolidate(docs + [bad])

        assert len(events) == 2
        chunks_in.inc.assert_called_once_with(4)          # all fed docs incl. malformed
        events_out.inc.assert_called_once_with(2)
        split.labels.assert_called_once_with(reason="gap")
        split.labels.return_value.inc.assert_called_once()
        duration.observe.assert_called_once()


class TestConsolidationConfigValidation:
    """IncidentService construction validates consolidation tuning (fail-fast)."""

    @pytest.mark.parametrize("bad", [
        {"max_inter_alert_gap_seconds": 0},
        {"max_inter_alert_gap_seconds": 3601},
        {"max_inter_alert_gap_seconds": -1},
        {"max_inter_alert_gap_seconds": "60"},
        {"max_inter_alert_gap_seconds": True},
        {"max_event_duration_seconds": 0},
        {"max_event_duration_seconds": 3601},
        {"representative": "bogus"},
    ])
    def test_invalid_config_rejected(self, bad):
        with pytest.raises(ValueError):
            IncidentService(consolidation=dict(_CONSOL_DEFAULT, **bad))

    def test_null_duration_allowed(self):
        IncidentService(consolidation=dict(_CONSOL_DEFAULT, max_event_duration_seconds=None))

    def test_empty_config_ok(self):
        IncidentService(consolidation={})
        IncidentService(consolidation=None)


class TestConsolidationService:
    """IncidentService.list_incidents — consolidation behaviour."""

    @pytest.mark.asyncio
    async def test_consolidate_filters_to_realtime_docs(self, mock_es_client):
        # REQ-007: consolidated query must exclude verifier-path docs via the
        # realtime discriminator (read-time filter; ES is never modified).
        mock_es_client.client.search.return_value = {"hits": {"total": {"value": 0}, "hits": []}}
        svc = IncidentService(es_client=mock_es_client, consolidation=dict(_CONSOL_DEFAULT))
        await svc.list_incidents(
            sensor_id="cam1", category="intrusion",
            start_time="2025-01-01T00:00:00Z", end_time="2025-01-01T01:00:00Z",
            consolidate=True,
        )
        musts = mock_es_client.client.search.call_args.kwargs["query"]["bool"]["must"]
        assert {"exists": {"field": "info.chunkIdx"}} in musts

    @pytest.mark.asyncio
    async def test_consolidate_query_requires_confirmed_verdict(self, mock_es_client):
        # The consolidated query must exclude non-confirmed docs server-side.
        mock_es_client.client.search.return_value = {"hits": {"total": {"value": 0}, "hits": []}}
        svc = IncidentService(es_client=mock_es_client, consolidation=dict(_CONSOL_DEFAULT))
        await svc.list_incidents(
            sensor_id="cam1", category="intrusion",
            start_time="2025-01-01T00:00:00Z", end_time="2025-01-01T01:00:00Z",
            consolidate=True,
        )
        musts = mock_es_client.client.search.call_args.kwargs["query"]["bool"]["must"]
        assert {"term": {"info.verdict.keyword": "confirmed"}} in musts

    @pytest.mark.asyncio
    async def test_raw_view_does_not_filter_verdict(self, mock_es_client):
        mock_es_client.client.search.return_value = {"hits": {"total": {"value": 0}, "hits": []}}
        svc = IncidentService(es_client=mock_es_client, consolidation=dict(_CONSOL_DEFAULT))
        await svc.list_incidents(sensor_id="cam1", consolidate=False)
        musts = mock_es_client.client.search.call_args.kwargs["query"].get("bool", {}).get("must", [])
        assert {"term": {"info.verdict.keyword": "confirmed"}} not in musts

    @pytest.mark.asyncio
    async def test_raw_view_does_not_filter_verifier_docs(self, mock_es_client):
        mock_es_client.client.search.return_value = {"hits": {"total": {"value": 0}, "hits": []}}
        svc = IncidentService(es_client=mock_es_client, consolidation=dict(_CONSOL_DEFAULT))
        await svc.list_incidents(sensor_id="cam1", consolidate=False)
        query = mock_es_client.client.search.call_args.kwargs["query"]
        musts = query.get("bool", {}).get("must", [])
        assert {"exists": {"field": "info.chunkIdx"}} not in musts

    @pytest.mark.asyncio
    async def test_truncated_true_when_total_exceeds_scanned(self, mock_es_client):
        # ES caps hits.total; a window denser than the scan must flag truncated.
        chunks = [
            _chunk(idx=1),
            _chunk(idx=2, start="2025-01-01T00:00:25.000Z", end="2025-01-01T00:00:55.000Z"),
        ]
        mock_es_client.client.search.return_value = {
            "hits": {"total": {"value": 15000, "relation": "gte"},
                     "hits": [_as_hit(c) for c in chunks]}
        }
        svc = IncidentService(es_client=mock_es_client, consolidation=dict(_CONSOL_DEFAULT))
        data, _ = await svc.list_incidents(
            sensor_id="cam-1", category="alert",
            start_time="2025-01-01T00:00:00Z", end_time="2025-01-01T01:00:00Z",
            consolidate=True,
        )
        assert data["truncated"] is True
        # track_total_hits must be requested so the count is exact
        assert mock_es_client.client.search.call_args.kwargs.get("track_total_hits") is True

    @pytest.mark.asyncio
    async def test_truncated_false_when_complete(self, mock_es_client):
        mock_es_client.client.search.return_value = {
            "hits": {"total": {"value": 1}, "hits": [_as_hit(_chunk(idx=1))]}
        }
        svc = IncidentService(es_client=mock_es_client, consolidation=dict(_CONSOL_DEFAULT))
        data, _ = await svc.list_incidents(
            sensor_id="cam-1", category="alert",
            start_time="2025-01-01T00:00:00Z", end_time="2025-01-01T01:00:00Z",
            consolidate=True,
        )
        assert data["truncated"] is False

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
    async def test_consolidate_true_total_is_event_count(self, mock_es_client):
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
        assert data["count"] == 2          # cam-1 (2 chunks) -> 1 event, cam-2 -> 1
        assert data["total"] == 2          # total counts events, not raw chunks
        assert all(i["info"]["isConsolidated"] == "true" for i in data["incidents"])

    @pytest.mark.asyncio
    async def test_consolidate_paginates_on_events(self, mock_es_client):
        # Three far-apart positives -> three separate events; pagination must
        # apply to events (never split one across a page boundary).
        chunks = [
            _chunk(idx=1, start="2025-01-01T00:00:00.000Z", end="2025-01-01T00:00:30.000Z"),
            _chunk(idx=5, start="2025-01-01T00:05:00.000Z", end="2025-01-01T00:05:30.000Z"),
            _chunk(idx=9, start="2025-01-01T00:10:00.000Z", end="2025-01-01T00:10:30.000Z"),
        ]
        mock_es_client.client.search.return_value = {
            "hits": {"total": {"value": 3}, "hits": [_as_hit(c) for c in chunks]}
        }
        svc = IncidentService(es_client=mock_es_client, consolidation=dict(_CONSOL_DEFAULT))
        page1, _ = await svc.list_incidents(consolidate=True, limit=2, offset=0)
        page2, _ = await svc.list_incidents(consolidate=True, limit=2, offset=2)
        assert page1["count"] == 2 and page1["total"] == 3   # page 1: 2 of 3 events
        assert page2["count"] == 1 and page2["total"] == 3   # page 2: remaining event
        ids1 = {e["Id"] for e in page1["incidents"]}
        ids2 = {e["Id"] for e in page2["incidents"]}
        assert ids1.isdisjoint(ids2)                          # no event on both pages

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
