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
"""Tests for lib.knowledge.adapters.es_caption.

Covers:
- query body shape: BM25 match, collection->uuid filter, default_doc_type,
  camera_id passthrough, time_range overlap bounds (full and partial),
  generic field equality, and the raw es_query escape hatch,
- ES hit -> Chunk normalisation: chunk_id seq fallback, NTP-float surfacing,
  display_citation formatting, stream_name derivation,
- retrieve() flow: dict vs callable filter handling, transport/HTTP errors.
"""
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import aiohttp
import pytest

from lib.knowledge.adapters.es_caption import EsCaptionAdapter
from lib.knowledge.adapters.es_caption import EsCaptionConfig
from lib.knowledge.adapters.es_caption import _derive_stream_name
from lib.knowledge.adapters.es_caption import _normalise_hit


def _hit(content_metadata: dict, text: str = "x", score: float = 1.0,
         sensor: dict | None = None) -> dict:
    src = {"text": text, "metadata": {"content_metadata": content_metadata}}
    if sensor is not None:
        src["sensor"] = sensor
    return {"_score": score, "_source": src}


class TestNormaliseHit:
    """ES hit -> Chunk normalisation."""

    def test_chunkidx_used_for_chunk_id_seq(self):
        chunk = _normalise_hit(
            _hit({"uuid": "u1", "doc_type": "raw_events", "chunkIdx": 3})
        )
        assert chunk.chunk_id == "u1_raw_events_3"

    def test_batch_i_falls_back_when_chunkidx_is_sentinel(self):
        # structured_events uses chunkIdx=-1 as a sentinel meaning "spans all chunks"
        # — fall through to batch_i for the chunk_id sequence.
        chunk = _normalise_hit(
            _hit({"uuid": "u1", "doc_type": "structured_events", "chunkIdx": -1, "batch_i": 2})
        )
        assert chunk.chunk_id == "u1_structured_events_2"

    def test_chunk_id_omits_seq_when_neither_present(self):
        # aggregated_summary has neither chunkIdx nor batch_i.
        chunk = _normalise_hit(_hit({"uuid": "u1", "doc_type": "aggregated_summary"}))
        assert chunk.chunk_id == "u1_aggregated_summary"

    def test_ntp_float_surfaced_as_seconds_in_metadata(self):
        chunk = _normalise_hit(
            _hit({"uuid": "u1", "doc_type": "raw_events",
                  "start_ntp_float": 45.0, "end_ntp_float": 60.0,
                  "camera_id": "cam-A"})
        )
        assert chunk.metadata["start_seconds"] == 45.0
        assert chunk.metadata["end_seconds"] == 60.0
        assert chunk.metadata["display_citation"] == "[cam-A, 45-60s]"

    def test_citation_falls_back_to_uuid_when_no_camera(self):
        chunk = _normalise_hit(_hit({"uuid": "u1", "doc_type": "aggregated_summary"}))
        assert chunk.metadata["display_citation"] == "[u1]"

    def test_citation_keeps_time_bounds_when_camera_missing(self):
        # Real raw_events docs may carry NTP times but no camera_id.
        chunk = _normalise_hit(
            _hit({"uuid": "u1", "doc_type": "raw_events", "chunkIdx": 6,
                  "start_ntp_float": 60.0, "end_ntp_float": 69.5})
        )
        assert chunk.metadata["display_citation"] == "[u1, 60-69.5s]"

    def test_non_dict_input_returns_none(self):
        assert _normalise_hit("not a dict") is None

    def test_stream_name_from_rtsp_description(self):
        chunk = _normalise_hit(_hit(
            {"uuid": "u1", "doc_type": "raw_events", "chunkIdx": 0},
            sensor={"description": "warehouse_stream_2",
                    "info": {"path": "rtsp://host/live/u1"}},
        ))
        assert chunk.metadata["stream_name"] == "warehouse_stream_2"
        # description-only path: no source_url surfaced.
        assert chunk.metadata["source_url"] is None
        assert chunk.metadata["display_citation"].startswith("[warehouse_stream_2")

    def test_stream_name_from_uploaded_video_filename(self):
        # Filename stem is surfaced as-is (no decoration stripping).
        chunk = _normalise_hit(_hit(
            {"uuid": "u1", "doc_type": "raw_events", "chunkIdx": 0,
             "start_ntp_float": 0, "end_ntp_float": 10},
            sensor={"description": "",
                    "info": {"url": "http://h/temp_files/warehouse_video_20250101_000000_4591d.mp4"}},
        ))
        assert chunk.metadata["stream_name"] == "warehouse_video_20250101_000000_4591d"
        assert (
            chunk.metadata["source_url"]
            == "http://h/temp_files/warehouse_video_20250101_000000_4591d.mp4"
        )
        assert (
            chunk.metadata["display_citation"]
            == "[warehouse_video_20250101_000000_4591d, 0-10s]"
        )

    def test_stream_name_falls_through_to_uuid_when_no_sensor(self):
        chunk = _normalise_hit(_hit({"uuid": "u1", "doc_type": "aggregated_summary"}))
        assert chunk.metadata["stream_name"] is None
        assert chunk.metadata["display_citation"] == "[u1]"

    def test_derive_stream_name_uses_filename_stem(self):
        assert _derive_stream_name(
            {"description": "", "info": {"path": "/tmp/plain_name.mp4"}}
        ) == "plain_name"

    def test_derive_stream_name_handles_non_dict_input(self):
        # Defensive: ES hits with no `sensor` block (summary/structured docs).
        assert _derive_stream_name(None) is None
        assert _derive_stream_name("not a dict") is None


class TestEsCaptionAdapter:
    """Query-body shape and retrieve() flow."""

    @pytest.fixture
    def adapter(self):
        # Exercises the production default (`aggregated_summary`).
        return EsCaptionAdapter(
            EsCaptionConfig(
                elasticsearch_url="http://es:9200",
                index="default",
                timeout=30,
                verify_ssl=True,
            )
        )

    @staticmethod
    def _mock_response(json_body=None, raise_for_status_exc=None):
        resp = AsyncMock()
        resp.json = AsyncMock(return_value=json_body or {})
        resp.raise_for_status = MagicMock(side_effect=raise_for_status_exc)
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)
        return resp

    @staticmethod
    def _mock_session(post_return=None, post_side_effect=None):
        session = AsyncMock()
        session.post = MagicMock(return_value=post_return, side_effect=post_side_effect)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        return session

    @staticmethod
    def _filters(body):
        return body["query"]["bool"]["filter"]

    @staticmethod
    def _must(body):
        return body["query"]["bool"]["must"]

    def test_authorization_header_only_when_api_key_set(self):
        without = EsCaptionAdapter(EsCaptionConfig(elasticsearch_url="http://x"))
        with_key = EsCaptionAdapter(EsCaptionConfig(elasticsearch_url="http://x", api_key="secret"))
        assert "Authorization" not in without._headers()
        assert with_key._headers()["Authorization"] == "ApiKey secret"

    def test_query_body_basic_shape(self, adapter):
        body = adapter._build_query("graffiti on bridge", "stream-A", top_k=5, filters=None)
        assert body["size"] == 5
        assert self._must(body) == [{"match": {"text": "graffiti on bridge"}}]
        # Default doc_type + collection->uuid lifted in automatically.
        assert {"term": {"metadata.content_metadata.uuid": "stream-A"}} in self._filters(body)
        assert (
            {"term": {"metadata.content_metadata.doc_type": "aggregated_summary"}}
            in self._filters(body)
        )

    def test_doc_type_override_via_filters(self, adapter):
        body = adapter._build_query("q", "s", top_k=5, filters={"doc_type": "raw_events"})
        # Override wins, default is dropped.
        doc_terms = [
            f for f in self._filters(body) if "metadata.content_metadata.doc_type" in str(f)
        ]
        assert doc_terms == [{"term": {"metadata.content_metadata.doc_type": "raw_events"}}]

    def test_camera_id_lifted_to_term_filter(self, adapter):
        body = adapter._build_query("q", "s", top_k=5, filters={"camera_id": "cam-A"})
        assert {"term": {"metadata.content_metadata.camera_id": "cam-A"}} in self._filters(body)

    def test_time_range_overlap_bounds_in_seconds(self, adapter):
        body = adapter._build_query(
            "q", "s", top_k=5, filters={"time_range": {"start": 5, "end": 30}}
        )
        # Overlap semantics on raw_events NTP fields (seconds, float).
        assert (
            {"range": {"metadata.content_metadata.start_ntp_float": {"lte": 30}}}
            in self._filters(body)
        )
        assert (
            {"range": {"metadata.content_metadata.end_ntp_float": {"gte": 5}}}
            in self._filters(body)
        )

    def test_time_range_partial_bounds_emit_only_one_range(self, adapter):
        # Only `end` set -> upper bound only; only `start` set -> lower only.
        end_only = adapter._build_query("q", "s", top_k=5, filters={"time_range": {"end": 30}})
        ranges = [f for f in self._filters(end_only) if "range" in f]
        assert ranges == [
            {"range": {"metadata.content_metadata.start_ntp_float": {"lte": 30}}}
        ]

        start_only = adapter._build_query("q", "s", top_k=5, filters={"time_range": {"start": 5}})
        ranges = [f for f in self._filters(start_only) if "range" in f]
        assert ranges == [
            {"range": {"metadata.content_metadata.end_ntp_float": {"gte": 5}}}
        ]

    def test_time_range_iso_strings_filter_on_at_timestamp(self, adapter):
        # ISO strings flip the filter onto the `@timestamp` date field — ES
        # parses ISO natively, and @timestamp exists on every doc_type so this
        # also enables time-windowed retrieval for summary/structured docs.
        body = adapter._build_query(
            "q", "s", top_k=5,
            filters={"time_range": {"start": "2026-05-04T22:00:00Z",
                                     "end":   "2026-05-04T22:10:00Z"}},
        )
        ranges = [f for f in self._filters(body) if "range" in f]
        assert ranges == [{"range": {"@timestamp": {
            "gte": "2026-05-04T22:00:00Z",
            "lte": "2026-05-04T22:10:00Z",
        }}}]
        # Numeric NTP-float filters must NOT be emitted on the ISO path.
        assert all("ntp_float" not in str(f) for f in self._filters(body))

    def test_time_range_iso_partial_bound(self, adapter):
        body = adapter._build_query(
            "q", "s", top_k=5,
            filters={"time_range": {"end": "2026-05-04T22:10:00Z"}},
        )
        ranges = [f for f in self._filters(body) if "range" in f]
        assert ranges == [{"range": {"@timestamp": {"lte": "2026-05-04T22:10:00Z"}}}]

    def test_unknown_field_treated_as_term_equality(self, adapter):
        body = adapter._build_query("q", "s", top_k=5, filters={"streamId": "abc"})
        assert {"term": {"metadata.content_metadata.streamId": "abc"}} in self._filters(body)

    def test_es_query_escape_hatch_replaces_body(self, adapter):
        raw = {"query": {"match_all": {}}}
        body = adapter._build_query("ignored", "ignored", top_k=7, filters={"es_query": raw})
        # size is preserved; the rest comes from the caller's raw body.
        assert body == {"size": 7, "query": {"match_all": {}}}

    def test_callable_filter_does_not_appear_in_body(self, adapter):
        body = adapter._build_query("q", "s", top_k=5, filters=lambda _c: True)
        # Predicates run client-side; the body has only the defaults.
        assert all("camera_id" not in str(f) for f in self._filters(body))

    @pytest.mark.asyncio
    async def test_retrieve_posts_to_index_search_endpoint(self, adapter):
        resp = self._mock_response(json_body={
            "hits": {"hits": [
                _hit({"uuid": "u1", "doc_type": "raw_events", "chunkIdx": 0,
                      "camera_id": "cam-A",
                      "start_ntp_float": 0.0, "end_ntp_float": 30.0},
                     text="some events"),
            ]}
        })
        session = self._mock_session(post_return=resp)

        with patch("aiohttp.ClientSession", return_value=session):
            result = await adapter.retrieve(query="q", collection_name="u1", top_k=3)

        assert session.post.call_args.args[0] == "http://es:9200/default/_search"
        assert result.success is True
        assert len(result.chunks) == 1
        assert result.chunks[0].metadata["camera_id"] == "cam-A"
        assert result.chunks[0].metadata["display_citation"] == "[cam-A, 0-30s]"

    @pytest.mark.asyncio
    async def test_callable_filter_applied_client_side(self, adapter):
        resp = self._mock_response(json_body={
            "hits": {"hits": [
                _hit({"uuid": "u1", "doc_type": "raw_events", "chunkIdx": 0, "camera_id": "A"},
                     text="keep"),
                _hit({"uuid": "u1", "doc_type": "raw_events", "chunkIdx": 1, "camera_id": "B"},
                     text="drop"),
            ]}
        })
        session = self._mock_session(post_return=resp)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await adapter.retrieve(
                query="q", collection_name="u1",
                filters=lambda chunk: chunk.metadata.get("camera_id") == "A",
            )
        assert [c.content for c in result.chunks] == ["keep"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "exc,expected_substring",
        [
            (aiohttp.ClientConnectionError("refused"), "Cannot connect"),
            (TimeoutError("slow"), "timed out"),
            (aiohttp.ClientError("misc"), "Request failed"),
        ],
    )
    async def test_transport_errors_map_to_failure_result(
        self, adapter, exc, expected_substring
    ):
        session = self._mock_session(post_side_effect=exc)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await adapter.retrieve(query="q", collection_name="c")
        assert result.success is False
        assert expected_substring in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_http_error_maps_to_failure_result(self, adapter):
        resp = self._mock_response(
            raise_for_status_exc=aiohttp.ClientResponseError(
                request_info=MagicMock(), history=(), status=500, message="Server Error"
            )
        )
        session = self._mock_session(post_return=resp)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await adapter.retrieve(query="q", collection_name="c")
        assert result.success is False
        assert "Server error" in (result.error_message or "")

