# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
Unit tests for src/via_stream_handler.py

Tests RequestInfo, LiveStreamInfo, DCSerializer,
helper functions, and key ViaStreamHandler public methods using heavy mocking.
"""

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# RequestInfo data class
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestInfo:
    def test_default_values(self):
        from via_stream_handler import RequestInfo

        ri = RequestInfo()
        assert ri.request_id is not None
        assert ri.status == RequestInfo.Status.QUEUED
        assert ri.chunk_count == 0
        assert ri.is_live is False
        assert ri.progress == 0
        assert ri.response == []
        assert ri.enable_audio is False

    def test_status_enum_values(self):
        from via_stream_handler import RequestInfo

        assert RequestInfo.Status.QUEUED.value == "queued"
        assert RequestInfo.Status.PROCESSING.value == "processing"
        assert RequestInfo.Status.SUCCESSFUL.value == "successful"
        assert RequestInfo.Status.FAILED.value == "failed"
        assert RequestInfo.Status.STOPPING.value == "stopping"

    def test_response_object(self):
        from via_stream_handler import RequestInfo

        resp = RequestInfo.Response("0", "60", "A summary")
        assert resp.start_timestamp == "0"
        assert resp.end_timestamp == "60"
        assert resp.response == "A summary"
        assert resp.reasoning_description == ""

    def test_response_with_reasoning(self):
        from via_stream_handler import RequestInfo

        resp = RequestInfo.Response("0", "60", "summary", "because...")
        assert resp.reasoning_description == "because..."

    def test_usage_defaults(self):
        from via_stream_handler import RequestInfo

        usage = RequestInfo.Usage()
        assert usage.summary_tokens == 0
        assert usage.aggregation_tokens == 0
        assert usage.summary_requests == 0
        assert usage.summary_latency == 0.0
        assert usage.aggregation_latency == 0.0

    def test_usage_with_values(self):
        from via_stream_handler import RequestInfo

        usage = RequestInfo.Usage(
            summary_tokens=100,
            aggregation_tokens=50,
            summary_requests=3,
            summary_latency=1.5,
            aggregation_latency=0.5,
        )
        assert usage.summary_tokens == 100
        assert usage.aggregation_tokens == 50


# ---------------------------------------------------------------------------
# LiveStreamInfo
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLiveStreamInfo:
    def test_default_values(self):
        from via_stream_handler import LiveStreamInfo

        lsi = LiveStreamInfo()
        assert lsi.chunk_size == 0
        assert lsi.req_info == []
        assert lsi.source_id == ""
        assert lsi.stop is False
        assert lsi.live_stream_ended is False
        assert lsi.pending_futures == []


# ---------------------------------------------------------------------------
# ntp_to_unix_timestamp
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNtpToUnixTimestamp:
    def test_known_timestamp(self):
        from via_stream_handler import ntp_to_unix_timestamp

        ts = "2024-01-01T00:00:00.000000Z"
        result = ntp_to_unix_timestamp(ts)
        expected = datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()
        assert abs(result - expected) < 0.001

    def test_microsecond_precision(self):
        from via_stream_handler import ntp_to_unix_timestamp

        ts = "2024-06-15T12:30:45.123456Z"
        result = ntp_to_unix_timestamp(ts)
        expected = datetime(2024, 6, 15, 12, 30, 45, 123456, tzinfo=timezone.utc).timestamp()
        assert abs(result - expected) < 0.001


# ---------------------------------------------------------------------------
# DCSerializer
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDCSerializer:
    def test_to_json_and_from_json_roundtrip(self, tmp_path):
        from via_stream_handler import DCSerializer, RequestInfo

        ri = RequestInfo()
        mock_chunk = MagicMock()
        mock_chunk.sourceId = "stream-1"
        mock_chunk.chunkIdx = 0
        mock_chunk.file = "/tmp/video.mp4"
        mock_chunk.pts_offset_ns = 0
        mock_chunk.start_pts = 0
        mock_chunk.end_pts = 60000000000
        mock_chunk.start_ntp = "2024-01-01T00:00:00.000000Z"
        mock_chunk.end_ntp = "2024-01-01T00:01:00.000000Z"
        mock_chunk.start_ntp_float = 0.0
        mock_chunk.end_ntp_float = 60.0
        mock_chunk.is_first = True
        mock_chunk.is_last = False

        mock_response = MagicMock()
        mock_response.vlm_response = "A description"
        mock_response.rtvi_frame_count = 3
        mock_response.chunk = mock_chunk

        ri.processed_chunk_list = [mock_response]

        file_path = str(tmp_path / "test.json")
        DCSerializer.to_json(ri, file_path)

        result = DCSerializer.from_json(file_path)
        assert len(result.processed_chunk_list) == 1
        assert result.processed_chunk_list[0].vlm_response == "A description"
        assert result.processed_chunk_list[0].chunk.sourceId == "stream-1"

    def test_from_json_missing_file(self, tmp_path):
        from via_stream_handler import DCSerializer

        result = DCSerializer.from_json(str(tmp_path / "nonexistent.json"))
        assert result.processed_chunk_list == []

    def test_to_json_exception_handling(self, tmp_path):
        from via_stream_handler import DCSerializer, RequestInfo

        ri = RequestInfo()
        ri.processed_chunk_list = [MagicMock(side_effect=Exception("bad"))]
        file_path = str(tmp_path / "bad.json")
        # Should not raise, just log a warning
        DCSerializer.to_json(ri, file_path)

    def test_from_json_sorting(self, tmp_path):
        from via_stream_handler import DCSerializer

        file_path = str(tmp_path / "multi.json")
        lines = []
        for idx in [2, 0, 1]:
            lines.append(
                json.dumps(
                    {
                        "vlm_response": f"chunk-{idx}",
                        "frame_times": [],
                        "chunk": {
                            "sourceId": "s",
                            "chunkIdx": idx,
                            "file": "",
                            "pts_offset_ns": 0,
                            "start_pts": 0,
                            "end_pts": 0,
                            "start_ntp": "",
                            "end_ntp": "",
                            "start_ntp_float": 0.0,
                            "end_ntp_float": 0.0,
                            "is_first": False,
                            "is_last": False,
                        },
                    }
                )
            )
        with open(file_path, "w") as f:
            f.write("\n".join(lines) + "\n")

        result = DCSerializer.from_json(file_path)
        indices = [r.chunk.chunkIdx for r in result.processed_chunk_list]
        assert indices == [0, 1, 2]


# ---------------------------------------------------------------------------
# ViaStreamHandler — mocked construction for testing public methods
# ---------------------------------------------------------------------------


def _make_mock_stream_handler():
    """Create a ViaStreamHandler with mocked __init__ for isolated method testing."""
    from via_stream_handler import ViaStreamHandler

    with patch.object(ViaStreamHandler, "__init__", lambda self, *a, **k: None):
        handler = ViaStreamHandler.__new__(ViaStreamHandler)
        from threading import RLock

        handler._lock = RLock()
        handler._running = True
        handler._request_info_map = {}
        handler._live_stream_info_map = {}
        handler._metrics = MagicMock()
        handler._vlm_pipeline = MagicMock()
        handler._ctx_mgr = None
        handler._ctx_mgr_pool = []
        handler._qa_ctx_mgr_pool = []
        handler._args = MagicMock()
        handler._notification_llm_api_key = None
        handler._notification_llm_params = None
        handler._ca_rag_config = {}
        handler.first_init = True
        handler.default_caption_prompt = "Summarize"
        handler.NUM_CA_RAG_PROCESSES_LAUNCH = 10
        handler.num_ctx_mgr = 0
        handler.MAX_STREAMS = 4
        handler._start_time = time.time()
        # Bypass real __init__; empty-guard needs this (0 => single attempt).
        handler._aggregation_empty_retries = 0
        return handler


# ---------------------------------------------------------------------------
# get_response
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetResponse:
    def test_get_response_all(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri.response = [
            RequestInfo.Response("0", "30", "chunk1"),
            RequestInfo.Response("30", "60", "chunk2"),
        ]
        handler._request_info_map[ri.request_id] = ri

        req, responses = handler.get_response(ri.request_id)
        assert len(responses) == 2
        assert req.response == []

    def test_get_response_with_chunk_size(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri.response = [
            RequestInfo.Response("0", "30", "chunk1"),
            RequestInfo.Response("30", "60", "chunk2"),
            RequestInfo.Response("60", "90", "chunk3"),
        ]
        handler._request_info_map[ri.request_id] = ri

        req, responses = handler.get_response(ri.request_id, 2)
        assert len(responses) == 2
        assert len(req.response) == 1

    def test_get_response_invalid_id_raises(self):
        from via_exception import ViaException

        handler = _make_mock_stream_handler()
        with pytest.raises(ViaException):
            handler.get_response("nonexistent-id")

    def test_get_response_zero_chunk_size(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri.response = [RequestInfo.Response("0", "30", "chunk1")]
        handler._request_info_map[ri.request_id] = ri

        req, responses = handler.get_response(ri.request_id, 0)
        assert len(responses) == 0
        assert len(req.response) == 1

    def test_returns_all_response_and_clears_when_no_chunk_size(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri.response = ["r1", "r2", "r3"]
        handler._request_info_map[ri.request_id] = ri

        returned_info, response = handler.get_response(ri.request_id)
        assert returned_info is ri
        assert response == ["r1", "r2", "r3"]
        assert ri.response == []

    def test_returns_chunk_slice_and_removes_it(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri.response = ["r1", "r2", "r3", "r4"]
        handler._request_info_map[ri.request_id] = ri

        returned_info, response = handler.get_response(ri.request_id, chunk_response_size=2)
        assert response == ["r1", "r2"]
        assert ri.response == ["r3", "r4"]

    def test_chunk_size_larger_than_response_returns_all(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri.response = ["only"]
        handler._request_info_map[ri.request_id] = ri

        _, response = handler.get_response(ri.request_id, chunk_response_size=10)
        assert response == ["only"]
        assert ri.response == []


# ---------------------------------------------------------------------------
# wait_for_request_done
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWaitForRequestDone:
    def test_wait_invalid_id_raises(self):
        from via_exception import ViaException

        handler = _make_mock_stream_handler()
        with pytest.raises(ViaException):
            handler.wait_for_request_done("nonexistent-id")

    def test_returns_immediately_when_status_is_successful(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri.status = RequestInfo.Status.SUCCESSFUL
        handler._request_info_map[ri.request_id] = ri

        handler.wait_for_request_done(ri.request_id)

    def test_returns_immediately_when_status_is_failed(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri.status = RequestInfo.Status.FAILED
        handler._request_info_map[ri.request_id] = ri

        handler.wait_for_request_done(ri.request_id)

    def test_waits_then_returns_when_status_becomes_successful(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri.status = RequestInfo.Status.PROCESSING
        handler._request_info_map[ri.request_id] = ri

        call_count = 0

        def mock_wait(timeout=None):
            nonlocal call_count
            call_count += 1
            ri.status = RequestInfo.Status.SUCCESSFUL

        ri.status_event.wait = mock_wait
        handler.wait_for_request_done(ri.request_id)
        assert call_count == 1


# ---------------------------------------------------------------------------
# check_status_remove_req_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckStatusRemoveReqId:
    def test_removes_completed_request(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri.is_live = False
        ri.progress = 100
        ri._ctx_mgr = None
        ri.assets = [MagicMock(asset_id="a1")]
        handler._request_info_map[ri.request_id] = ri

        handler.check_status_remove_req_id(ri.request_id)
        assert ri.request_id not in handler._request_info_map

    def test_nonexistent_request_does_nothing(self):
        handler = _make_mock_stream_handler()
        handler.check_status_remove_req_id("does-not-exist")

    def test_returns_ctx_mgr_to_pool(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri.is_live = False
        ri.progress = 100
        mock_ctx = MagicMock()
        ri._ctx_mgr = mock_ctx
        ri.source_id = "stream-1"
        ri.delete_external_collection = False
        ri.assets = [MagicMock(asset_id="a1")]
        handler._request_info_map[ri.request_id] = ri

        handler.check_status_remove_req_id(ri.request_id)
        assert mock_ctx in handler._ctx_mgr_pool

    def test_nonexistent_request_id_returns_silently(self):
        handler = _make_mock_stream_handler()
        handler.check_status_remove_req_id("nonexistent-id")

    def test_progress_100_non_live_removes(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri.progress = 100
        ri.is_live = False
        ri._ctx_mgr = None
        handler._request_info_map[ri.request_id] = ri

        with patch.dict(os.environ, {"LVS_DISABLE_DB_RESET_ON_REQUEST_DONE": "false"}):
            handler.check_status_remove_req_id(ri.request_id)

        assert ri.request_id not in handler._request_info_map

    def test_progress_less_than_100_non_live_keeps(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri.progress = 50
        ri.is_live = False
        ri._ctx_mgr = None
        handler._request_info_map[ri.request_id] = ri

        handler.check_status_remove_req_id(ri.request_id)
        assert ri.request_id in handler._request_info_map

    def test_ctx_mgr_reset_and_returned_to_pool_when_removed(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri.progress = 100
        ri.is_live = False
        ri.source_id = "test-stream"
        ri.delete_external_collection = False
        mock_ctx = MagicMock()
        mock_ctx._process_index = 5
        ri._ctx_mgr = mock_ctx
        handler._request_info_map[ri.request_id] = ri

        with patch.dict(os.environ, {"LVS_DISABLE_DB_RESET_ON_REQUEST_DONE": "false"}):
            handler.check_status_remove_req_id(ri.request_id)

        mock_ctx.reset.assert_called_once()
        assert mock_ctx in handler._ctx_mgr_pool


# ---------------------------------------------------------------------------
# remove_request_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRemoveRequestId:
    def test_remove_single(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        handler._request_info_map[ri.request_id] = ri
        handler.remove_request_id(ri.request_id)
        assert ri.request_id not in handler._request_info_map

    def test_remove_nonexistent(self):
        handler = _make_mock_stream_handler()
        handler.remove_request_id("does-not-exist")

    def test_removes_existing_request(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        handler._request_info_map[ri.request_id] = ri
        handler.remove_request_id(ri.request_id)
        assert ri.request_id not in handler._request_info_map

    def test_nonexistent_request_id_is_noop(self):
        handler = _make_mock_stream_handler()
        handler.remove_request_id("does-not-exist")
        assert handler._request_info_map == {}

    def test_only_target_request_removed(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri1 = RequestInfo()
        ri2 = RequestInfo()
        handler._request_info_map[ri1.request_id] = ri1
        handler._request_info_map[ri2.request_id] = ri2
        handler.remove_request_id(ri1.request_id)
        assert ri1.request_id not in handler._request_info_map
        assert ri2.request_id in handler._request_info_map


# ---------------------------------------------------------------------------
# get_models_info
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetModelsInfo:
    def test_delegates_to_vlm_pipeline(self):
        handler = _make_mock_stream_handler()
        expected = SimpleNamespace(id="test-model", created=1700000000, owned_by="nvidia")
        handler._vlm_pipeline.get_models_info.return_value = expected

        result = handler.get_models_info()
        assert result.id == "test-model"
        handler._vlm_pipeline.get_models_info.assert_called_once()


# ---------------------------------------------------------------------------
# format_response
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatResponse:
    def test_short_response_unchanged(self):
        handler = _make_mock_stream_handler()
        data = json.dumps({"events": [], "video_summary": "short"})
        assert handler.format_response(data) == data

    def test_long_response_removes_events(self):
        handler = _make_mock_stream_handler()
        data = {
            "events": [{"e": "x" * 500}] * 100,
            "total_events": 100,
            "video_summary": "summary",
            "uuid": "u1",
        }
        result = handler.format_response(json.dumps(data), max_chars=200)
        parsed = json.loads(result)
        assert parsed["events"] == []
        assert parsed["total_events"] == 100

    def test_very_long_summary_truncated(self):
        handler = _make_mock_stream_handler()
        data = {
            "events": [],
            "total_events": 0,
            "video_summary": "x" * 10000,
            "uuid": "u1",
        }
        result = handler.format_response(json.dumps(data), max_chars=200)
        assert len(result) <= 200

    def test_invalid_json_truncated(self):
        handler = _make_mock_stream_handler()
        raw = "not valid json " * 1000
        result = handler.format_response(raw, max_chars=100)
        assert len(result) == 100

    def test_no_events_key_truncated(self):
        handler = _make_mock_stream_handler()
        data = {"summary": "x" * 10000}
        result = handler.format_response(json.dumps(data), max_chars=200)
        assert len(result) == 200

    def test_response_within_limit_returned_as_is(self):
        handler = _make_mock_stream_handler()
        response = json.dumps({"video_summary": "short", "events": [], "uuid": "u1"})
        result = handler.format_response(response, max_chars=100000)
        assert result == response

    def test_non_json_over_limit_truncated(self):
        handler = _make_mock_stream_handler()
        response = "x" * 20
        result = handler.format_response(response, max_chars=10)
        assert result == "x" * 10

    def test_json_with_events_over_limit_removes_events(self):
        handler = _make_mock_stream_handler()
        big_events = [{"id": i, "description": "event " * 100} for i in range(50)]
        data = {
            "events": big_events,
            "total_events": len(big_events),
            "video_summary": "short summary",
            "uuid": "u1",
        }
        response = json.dumps(data)
        max_chars = (
            len(
                json.dumps(
                    {
                        "events": [],
                        "total_events": 50,
                        "video_summary": "short summary",
                        "uuid": "u1",
                    }
                )
            )
            + 50
        )
        result = handler.format_response(response, max_chars=max_chars)
        parsed = json.loads(result)
        assert parsed["events"] == []
        assert parsed["video_summary"] == "short summary"

    def test_still_too_large_after_removing_events_truncates_summary(self):
        handler = _make_mock_stream_handler()
        long_summary = "s" * 10000
        big_events = [{"id": i, "data": "x" * 200} for i in range(20)]
        data = {
            "events": big_events,
            "total_events": len(big_events),
            "video_summary": long_summary,
            "uuid": "u2",
        }
        response = json.dumps(data)
        result = handler.format_response(response, max_chars=200)
        assert len(result) <= 200
        parsed = json.loads(result)
        assert parsed["events"] == []
        assert len(parsed.get("video_summary", "")) < len(long_summary)

    def test_json_without_events_key_over_limit_truncated(self):
        handler = _make_mock_stream_handler()
        data = {"video_summary": "x" * 500, "uuid": "u3"}
        response = json.dumps(data)
        max_chars = 20
        result = handler.format_response(response, max_chars=max_chars)
        assert len(result) == max_chars


# ---------------------------------------------------------------------------
# _get_db_tool_name
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetDbToolName:
    def test_returns_db_tool(self):
        handler = _make_mock_stream_handler()
        config = {"functions": {"summarization": {"tools": {"db": "vector_db"}}}}
        assert handler._get_db_tool_name(config) == "vector_db"

    def test_returns_none_when_missing(self):
        handler = _make_mock_stream_handler()
        assert handler._get_db_tool_name({}) is None


# ---------------------------------------------------------------------------
# _update_db_tool_param
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateDbToolParam:
    def test_sets_param(self):
        handler = _make_mock_stream_handler()
        config = {"tools": {"vector_db": {"params": {}}}}
        handler._update_db_tool_param(config, "vector_db", "collection_name", "test_coll")
        assert config["tools"]["vector_db"]["params"]["collection_name"] == "test_coll"

    def test_removes_param_when_none(self):
        handler = _make_mock_stream_handler()
        config = {"tools": {"vector_db": {"params": {"collection_name": "old"}}}}
        handler._update_db_tool_param(config, "vector_db", "collection_name", None)
        assert "collection_name" not in config["tools"]["vector_db"]["params"]

    def test_creates_params_section(self):
        handler = _make_mock_stream_handler()
        config = {"tools": {"vector_db": {}}}
        handler._update_db_tool_param(config, "vector_db", "key", "value")
        assert config["tools"]["vector_db"]["params"]["key"] == "value"

    def test_nonexistent_tool_does_nothing(self):
        handler = _make_mock_stream_handler()
        config = {"tools": {}}
        handler._update_db_tool_param(config, "no_tool", "key", "value")
        assert "no_tool" not in config["tools"]


# ---------------------------------------------------------------------------
# _update_llm_tool_param
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateLlmToolParam:
    def test_sets_param(self):
        handler = _make_mock_stream_handler()
        config = {
            "functions": {"summarization": {"tools": {"llm": "llm_tool"}}},
            "tools": {"llm_tool": {"params": {}}},
        }
        handler._update_llm_tool_param(config, "summarization", "temperature", 0.7)
        assert config["tools"]["llm_tool"]["params"]["temperature"] == 0.7

    def test_none_value_skipped(self):
        handler = _make_mock_stream_handler()
        config = {
            "functions": {"summarization": {"tools": {"llm": "llm_tool"}}},
            "tools": {"llm_tool": {"params": {}}},
        }
        handler._update_llm_tool_param(config, "summarization", "temperature", None)
        assert "temperature" not in config["tools"]["llm_tool"]["params"]

    def test_missing_function_does_nothing(self):
        handler = _make_mock_stream_handler()
        config = {"functions": {}, "tools": {}}
        handler._update_llm_tool_param(config, "nonexistent", "key", "val")

    def test_creates_tool_section(self):
        handler = _make_mock_stream_handler()
        config = {
            "functions": {"summarization": {"tools": {"llm": "new_tool"}}},
            "tools": {},
        }
        handler._update_llm_tool_param(config, "summarization", "max_tokens", 1024)
        assert config["tools"]["new_tool"]["params"]["max_tokens"] == 1024


# ---------------------------------------------------------------------------
# _remove_segmasks_from_cv_meta (static)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRemoveSegmasksFromCvMeta:
    def test_removes_segmasks(self):
        from via_stream_handler import ViaStreamHandler

        cv_meta = [
            {"objects": [{"id": 1, "misc": [{"seg": {"mask": [1, 2, 3]}, "other": "keep"}]}]}
        ]
        result = ViaStreamHandler._remove_segmasks_from_cv_meta(cv_meta)
        assert result[0]["objects"][0]["misc"][0]["seg"] == {}
        assert result[0]["objects"][0]["misc"][0]["other"] == "keep"
        # Original should be unmodified
        assert cv_meta[0]["objects"][0]["misc"][0]["seg"]["mask"] == [1, 2, 3]

    def test_no_misc_key(self):
        from via_stream_handler import ViaStreamHandler

        cv_meta = [{"objects": [{"id": 1}]}]
        result = ViaStreamHandler._remove_segmasks_from_cv_meta(cv_meta)
        assert result == cv_meta

    def test_empty_input(self):
        from via_stream_handler import ViaStreamHandler

        assert ViaStreamHandler._remove_segmasks_from_cv_meta([]) == []

    def test_objects_without_misc_are_unchanged(self):
        from via_stream_handler import ViaStreamHandler

        cv_meta = [{"objects": [{"id": 1, "label": "car"}]}]
        result = ViaStreamHandler._remove_segmasks_from_cv_meta(cv_meta)
        assert result[0]["objects"][0] == {"id": 1, "label": "car"}

    def test_seg_cleared_in_misc(self):
        from via_stream_handler import ViaStreamHandler

        cv_meta = [
            {
                "objects": [
                    {
                        "id": 1,
                        "misc": [{"seg": {"mask": [1, 2, 3]}, "other": "data"}],
                    }
                ]
            }
        ]
        result = ViaStreamHandler._remove_segmasks_from_cv_meta(cv_meta)
        assert result[0]["objects"][0]["misc"][0]["seg"] == {}
        assert result[0]["objects"][0]["misc"][0]["other"] == "data"

    def test_original_is_not_mutated(self):
        from via_stream_handler import ViaStreamHandler

        cv_meta = [
            {
                "objects": [
                    {"id": 1, "misc": [{"seg": {"mask": [9, 8, 7]}}]},
                ]
            }
        ]
        ViaStreamHandler._remove_segmasks_from_cv_meta(cv_meta)
        assert cv_meta[0]["objects"][0]["misc"][0]["seg"] == {"mask": [9, 8, 7]}

    def test_multiple_objects_and_misc_all_cleared(self):
        from via_stream_handler import ViaStreamHandler

        cv_meta = [
            {
                "objects": [
                    {"id": 1, "misc": [{"seg": {"a": 1}}, {"seg": {"b": 2}}]},
                    {"id": 2, "misc": [{"seg": {"c": 3}}]},
                ]
            }
        ]
        result = ViaStreamHandler._remove_segmasks_from_cv_meta(cv_meta)
        for obj in result[0]["objects"]:
            for misc in obj["misc"]:
                assert misc["seg"] == {}

    def test_data_without_objects_key(self):
        from via_stream_handler import ViaStreamHandler

        cv_meta = [{"timestamp": 12345}]
        result = ViaStreamHandler._remove_segmasks_from_cv_meta(cv_meta)
        assert result == [{"timestamp": 12345}]


# ---------------------------------------------------------------------------
# _get_cv_metadata_for_chunk
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetCvMetadataForChunk:
    def test_finds_matching_frames(self, tmp_path):
        handler = _make_mock_stream_handler()
        data = [
            {"timestamp": 1e9, "objects": []},
            {"timestamp": 2e9, "objects": []},
            {"timestamp": 3e9, "objects": []},
        ]
        json_file = str(tmp_path / "cv_meta.json")
        with open(json_file, "w") as f:
            json.dump(data, f)

        result = handler._get_cv_metadata_for_chunk(json_file, [1.0, 2.0])
        assert len(result) == 2

    def test_no_json_file(self):
        handler = _make_mock_stream_handler()
        result = handler._get_cv_metadata_for_chunk("", [1.0, 2.0])
        assert result == []

    def test_empty_frame_times(self, tmp_path):
        handler = _make_mock_stream_handler()
        json_file = str(tmp_path / "cv_meta.json")
        with open(json_file, "w") as f:
            json.dump([{"timestamp": 1e9}], f)
        result = handler._get_cv_metadata_for_chunk(json_file, [])
        assert result == []


# ---------------------------------------------------------------------------
# _get_request_fps / get_active_streams_info
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFpsTracking:
    def test_get_request_fps_active(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri._fps_is_active = True
        ri._fps_start_time = 100.0
        ri._fps_last_update_time = 110.0
        ri._fps_frame_count = 300

        fps = handler._get_request_fps(ri)
        assert fps == 30.0

    def test_get_request_fps_inactive(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri._fps_is_active = False
        assert handler._get_request_fps(ri) == 0.0

    def test_get_request_fps_zero_elapsed(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri._fps_is_active = True
        ri._fps_start_time = 100.0
        ri._fps_last_update_time = 100.0
        ri._fps_frame_count = 10
        assert handler._get_request_fps(ri) == 0.0

    def test_get_active_streams_info(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri._fps_is_active = True
        ri._fps_start_time = 100.0
        ri._fps_last_update_time = 110.0
        ri._fps_frame_count = 300
        ri.source_id = "stream-1"
        handler._request_info_map[ri.request_id] = ri

        info = handler.get_active_streams_info()
        assert "stream-1" in info
        assert info["stream-1"] == 30.0

    def test_get_active_streams_info_empty(self):
        handler = _make_mock_stream_handler()
        assert handler.get_active_streams_info() == {}


# ---------------------------------------------------------------------------
# update_live_stream_*_latency (no-ops)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLatencyMetricNoOps:
    def test_update_summary_latency(self):
        handler = _make_mock_stream_handler()
        handler.update_live_stream_summary_latency(1.5)

    def test_update_captions_latency(self):
        handler = _make_mock_stream_handler()
        handler.update_live_stream_captions_latency(2.0)


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStreamHandlerStop:
    def test_stop_sets_running_false(self):
        handler = _make_mock_stream_handler()
        handler.stop()
        assert handler._running is False

    def test_stop_cleans_up_vlm_pipeline(self):
        handler = _make_mock_stream_handler()
        handler.stop()
        handler._vlm_pipeline.stop.assert_called_once_with(force=True)

    def test_stop_unregisters_metrics(self):
        handler = _make_mock_stream_handler()
        handler.stop()
        handler._metrics.unregister.assert_called_once()

    def test_stop_cleans_up_ctx_mgr_pool(self):
        handler = _make_mock_stream_handler()
        mock_ctx = MagicMock()
        handler._ctx_mgr_pool = [mock_ctx]
        handler.stop()
        mock_ctx.process.kill.assert_called_once()

    def test_stop_handles_ctx_mgr_kill_exception(self):
        handler = _make_mock_stream_handler()
        mock_ctx = MagicMock()
        mock_ctx.process.kill.side_effect = Exception("already dead")
        handler._ctx_mgr_pool = [mock_ctx]
        handler.stop()


# ---------------------------------------------------------------------------
# _create_named_thread_pool
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateNamedThreadPool:
    def test_creates_pool(self):
        handler = _make_mock_stream_handler()
        pool = handler._create_named_thread_pool(max_workers=2, prefix="test")
        assert pool._max_workers == 2
        pool.shutdown(wait=False)


# ---------------------------------------------------------------------------
# _start_stream_fps_tracking / _finalize_stream_fps_tracking
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStreamFpsTracking:
    def test_start_tracking(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        handler._start_stream_fps_tracking(ri)
        assert ri._fps_is_active is True
        assert ri._fps_frame_count == 0
        assert ri._fps_start_time is not None

    def test_finalize_tracking(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri._fps_is_active = True
        ri._fps_start_time = time.time() - 10
        ri._fps_last_update_time = time.time()
        ri._fps_frame_count = 100
        handler._finalize_stream_fps_tracking(ri)
        assert ri._fps_is_active is False

    def test_finalize_inactive_noop(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri._fps_is_active = False
        handler._finalize_stream_fps_tracking(ri)
        assert ri._fps_is_active is False


# ---------------------------------------------------------------------------
# _update_stream_fps
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateStreamFps:
    def test_update_with_video_fps(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri._fps_is_active = True
        ri._fps_frame_count = 0
        ri.video_fps = 30.0
        ri.chunk_size = 60

        response = MagicMock(spec=["rtvi_frame_count", "chunk"])
        response.rtvi_frame_count = 0
        handler._update_stream_fps(response, ri)
        assert ri._fps_frame_count == 1800  # 30 * 60

    def test_update_without_video_fps(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri._fps_is_active = True
        ri._fps_frame_count = 0
        ri.video_fps = None

        response = MagicMock(spec=["rtvi_frame_count", "chunk"])
        response.rtvi_frame_count = 3
        handler._update_stream_fps(response, ri)
        assert ri._fps_frame_count == 3

    def test_update_inactive_noop(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri._fps_is_active = False
        ri._fps_frame_count = 0

        handler._update_stream_fps(MagicMock(), ri)
        assert ri._fps_frame_count == 0


# ---------------------------------------------------------------------------
# update_ca_rag_config
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateCaRagConfig:
    def test_sets_uuid_in_context_manager(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        handler._ca_rag_config = {
            "context_manager": {"functions": []},
            "functions": {},
            "tools": {},
        }
        ri = RequestInfo()
        ri.source_id = "stream-abc"
        ri.is_live = False
        ri.summarize_batch_size = None
        ri.enable_vlm_structured_output = True
        ri.summarize = False
        ri.user_specified_collection_name = None
        ri.custom_metadata = None
        ri.delete_external_collection = False
        ri.enable_audio = False

        config = handler.update_ca_rag_config(ri)
        assert config["context_manager"]["uuid"] == "stream-abc"

    def test_summarization_removed_when_disabled(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        handler._ca_rag_config = {
            "context_manager": {"functions": ["summarization"]},
            "functions": {"summarization": {"tools": {"db": "vector_db"}}},
            "tools": {"vector_db": {"params": {}}},
        }
        ri = RequestInfo()
        ri.source_id = "s1"
        ri.is_live = False
        ri.summarize_batch_size = None
        ri.enable_vlm_structured_output = True
        ri.summarize = False
        ri.user_specified_collection_name = None
        ri.custom_metadata = None
        ri.delete_external_collection = False
        ri.enable_audio = False

        config = handler.update_ca_rag_config(ri)
        assert "summarization" not in config["context_manager"]["functions"]


# ---------------------------------------------------------------------------
# populate_argument_parser
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPopulateArgumentParser:
    def test_adds_expected_arguments(self):
        from via_stream_handler import ViaStreamHandler

        parser = MagicMock()
        ViaStreamHandler.populate_argument_parser(parser)
        add_calls = [c[0][0] for c in parser.add_argument.call_args_list]
        assert "--max-live-streams" in add_calls
        assert "--enable-audio" in add_calls
        assert "--enable-dev-dc-gen" in add_calls
        assert "--max-file-duration" in add_calls
        assert "--disable-ca-rag" in add_calls
        assert "--ca-rag-config" in add_calls


# ---------------------------------------------------------------------------
# _create_vlm_prompt
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateVlmPrompt:
    def _make_handler(self):
        from via_stream_handler import ViaStreamHandler

        handler = ViaStreamHandler.__new__(ViaStreamHandler)
        return handler

    def test_disabled_returns_prompt_unchanged(self):
        handler = self._make_handler()
        result = handler._create_vlm_prompt("my prompt", False, ["car"], ["crash"], "traffic")
        assert result == "my prompt"

    def test_default_structured_prompt_with_scenario_events_objects(self):
        handler = self._make_handler()
        env_keys = [
            "LVS_PROMPT_VLM_ROLE",
            "LVS_PROMPT_VLM_INSTRUCTION",
            "LVS_PROMPT_VLM_CONSTRAINTS",
            "LVS_PROMPT_VLM_STRUCTURED_OUTPUT",
        ]
        cleaned = {k: os.environ.pop(k) for k in env_keys if k in os.environ}
        try:
            result = handler._create_vlm_prompt(
                "ignored", True, ["person", "car"], ["fire", "theft"], "warehouse"
            )
            assert "warehouse" in result
            assert "fire, theft" in result
            assert "person, car" in result
            assert "start_time" in result
        finally:
            os.environ.update(cleaned)

    def test_env_var_override_role(self):
        handler = self._make_handler()
        with patch.dict(
            os.environ,
            {"LVS_PROMPT_VLM_ROLE": "You are a custom analyst for {scenario}"},
            clear=False,
        ):
            result = handler._create_vlm_prompt("ignored", True, [], [], "parking")
            assert "You are a custom analyst for parking" in result
            assert "advanced intelligent video analysis" not in result

    def test_env_var_override_instruction(self):
        handler = self._make_handler()
        with patch.dict(
            os.environ,
            {"LVS_PROMPT_VLM_INSTRUCTION": "Watch for {events} near {objects_of_interest}"},
            clear=False,
        ):
            result = handler._create_vlm_prompt(
                "ignored", True, ["door"], ["intrusion"], "building"
            )
            assert "Watch for intrusion near door" in result

    def test_env_var_override_constraints(self):
        handler = self._make_handler()
        with patch.dict(
            os.environ,
            {"LVS_PROMPT_VLM_CONSTRAINTS": "Only output valid JSON. Scenario: {scenario}"},
            clear=False,
        ):
            result = handler._create_vlm_prompt("ignored", True, [], [], "lobby")
            assert "Only output valid JSON. Scenario: lobby" in result

    def test_empty_events_and_objects_lists(self):
        handler = self._make_handler()
        env_keys = [
            "LVS_PROMPT_VLM_ROLE",
            "LVS_PROMPT_VLM_INSTRUCTION",
            "LVS_PROMPT_VLM_CONSTRAINTS",
            "LVS_PROMPT_VLM_STRUCTURED_OUTPUT",
        ]
        cleaned = {k: os.environ.pop(k) for k in env_keys if k in os.environ}
        try:
            result = handler._create_vlm_prompt("ignored", True, [], [], "general")
            assert "general" in result
            assert "{events}" not in result
            assert "{objects_of_interest}" not in result
        finally:
            os.environ.update(cleaned)

    def test_none_scenario_handled_gracefully(self):
        handler = self._make_handler()
        env_keys = [
            "LVS_PROMPT_VLM_ROLE",
            "LVS_PROMPT_VLM_INSTRUCTION",
            "LVS_PROMPT_VLM_CONSTRAINTS",
            "LVS_PROMPT_VLM_STRUCTURED_OUTPUT",
        ]
        cleaned = {k: os.environ.pop(k) for k in env_keys if k in os.environ}
        try:
            result = handler._create_vlm_prompt("ignored", True, None, None, None)
            assert "{scenario}" not in result
            assert isinstance(result, str)
        finally:
            os.environ.update(cleaned)


# ---------------------------------------------------------------------------
# update_ca_rag_config — additional branch coverage
# ---------------------------------------------------------------------------


def _make_ri_for_ca_rag(**overrides):
    """Create a RequestInfo pre-populated with sensible defaults for update_ca_rag_config tests."""
    from via_stream_handler import RequestInfo

    ri = RequestInfo()
    defaults = dict(
        source_id="test-stream",
        is_live=False,
        chunk_size=10,
        summarize_batch_size=None,
        enable_vlm_structured_output=True,
        summarize=False,
        enable_audio=False,
        user_specified_collection_name=None,
        custom_metadata=None,
        delete_external_collection=False,
        schema=None,
        batch_response_method=None,
        scenario=None,
        events=None,
        auto_generate_prompt=None,
        time_metadata_keys=None,
        summarize_top_p=None,
        summarize_temperature=None,
        summarize_max_tokens=None,
    )
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(ri, k, v)
    return ri


def _base_ca_rag_config():
    """Return a ca_rag_config skeleton covering all function types."""
    return {
        "context_manager": {
            "functions": [
                "summarization",
                "retriever_function",
                "ingestion_function",
                "notification",
            ]
        },
        "functions": {
            "summarization": {"tools": {"db": "vector_db", "llm": "llm_tool"}},
            "retriever_function": {"tools": {"llm": "llm_tool"}},
            "ingestion_function": {"tools": {"llm": "llm_tool"}},
            "notification": {"tools": {"llm": "notif_llm"}},
        },
        "tools": {
            "vector_db": {"params": {}},
            "llm_tool": {"params": {}},
            "notif_llm": {"params": {}},
        },
    }


@pytest.mark.unit
class TestUpdateCaRagConfigBranches:
    def _make_handler(self, config=None):
        from via_stream_handler import ViaStreamHandler

        handler = ViaStreamHandler.__new__(ViaStreamHandler)
        from threading import RLock

        handler._lock = RLock()
        handler._ca_rag_config = config if config is not None else _base_ca_rag_config()
        handler._live_stream_info_map = {}
        return handler

    def test_summarize_batch_size_overrides(self):
        handler = self._make_handler()
        ri = _make_ri_for_ca_rag(summarize_batch_size=8)
        config = handler.update_ca_rag_config(ri)
        assert config["functions"]["summarization"]["params"]["batch_size"] == 8

    def test_summarize_batch_size_with_audio_makes_even(self):
        handler = self._make_handler()
        ri = _make_ri_for_ca_rag(summarize_batch_size=7, enable_audio=True)
        config = handler.update_ca_rag_config(ri)
        assert config["functions"]["summarization"]["params"]["batch_size"] == 8

    def test_summarize_batch_size_already_even_with_audio(self):
        handler = self._make_handler()
        ri = _make_ri_for_ca_rag(summarize_batch_size=6, enable_audio=True)
        config = handler.update_ca_rag_config(ri)
        assert config["functions"]["summarization"]["params"]["batch_size"] == 6

    def test_schema_and_batch_response_method_params(self):
        handler = self._make_handler()
        ri = _make_ri_for_ca_rag(
            enable_vlm_structured_output=False,
            schema={"type": "object"},
            batch_response_method="merge",
            scenario="traffic",
            events=["crash", "congestion"],
            auto_generate_prompt=True,
            time_metadata_keys=["start_time", "end_time"],
        )
        config = handler.update_ca_rag_config(ri)
        params = config["functions"]["summarization"]["params"]
        assert params["schema"] == {"type": "object"}
        assert params["batch_response_method"] == "merge"
        assert params["scenario"] == "traffic"
        assert params["events"] == ["crash", "congestion"]
        assert params["auto_generate_prompt"] is True
        assert params["time_metadata_keys"] == ["start_time", "end_time"]

    def test_summarize_true_updates_llm_params(self):
        handler = self._make_handler()
        ri = _make_ri_for_ca_rag(
            summarize=True,
            summarize_top_p=0.95,
            summarize_temperature=0.7,
            summarize_max_tokens=2048,
        )
        config = handler.update_ca_rag_config(ri)
        assert "summarization" in config["context_manager"]["functions"]
        llm_params = config["tools"]["llm_tool"]["params"]
        assert llm_params["top_p"] == 0.95
        assert llm_params["temperature"] == 0.7
        assert llm_params["max_tokens"] == 2048


# ---------------------------------------------------------------------------
# _get_aggregated_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAggregatedSummary:
    def test_file_kafka_db_mode_waits_for_logstash_flush_before_aggregation(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        handler._args.enable_dev_dc_gen = False
        handler._kafka_enabled = True
        handler._caption_source = "db"
        handler._ca_rag_config = {
            "functions": {
                "summarization": {
                    "params": {"kafka_enabled": True},
                    "tools": {"db": "elasticsearch_db"},
                }
            },
            "tools": {"elasticsearch_db": {"params": {"kafka_consumer_settle_secs": 2.5}}},
        }
        handler._publish_aggregate_to_kafka = MagicMock()

        req_info = RequestInfo()
        req_info.file = "/tmp/video.mp4"
        req_info.source_id = "source-1"
        req_info.request_id = "request-1"
        req_info.enable_audio = False
        req_info.is_live = False
        req_info.summarize = True
        req_info.camera_id = "default"
        req_info._ctx_mgr = MagicMock()
        req_info._ctx_mgr.call.return_value = {
            "summarization": {
                "result": (
                    '{"events":[],"total_events":0,' '"video_summary":"","uuids":["source-1"]}'
                ),
                "metadata": {},
            }
        }

        chunk = SimpleNamespace(
            chunkIdx=0,
            start_pts=0,
            end_pts=10_000_000_000,
            start_ntp="1970-01-01T00:00:00.000Z",
            end_ntp="1970-01-01T00:00:10.000Z",
        )
        chunk_response = SimpleNamespace(
            chunk=chunk,
            vlm_response='{"events":[]}',
            vlm_stats={},
        )

        with patch("via_stream_handler.time.sleep") as sleep_mock:
            handler._get_aggregated_summary(req_info, [chunk_response])

        sleep_mock.assert_called_once_with(2.5)
        req_info._ctx_mgr.call.assert_called_once_with({"summarization": {"uuids": ["source-1"]}})


# ---------------------------------------------------------------------------
# _create_video_from_cached_frames
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateVideoFromCachedFrames:
    def _make_handler(self):
        from via_stream_handler import ViaStreamHandler

        handler = ViaStreamHandler.__new__(ViaStreamHandler)
        return handler

    @patch("via_stream_handler.glob.glob", return_value=[])
    @patch("via_stream_handler.subprocess.run")
    @patch("via_stream_handler.os.listdir", return_value=["frame_001.jpg", "frame_002.jpg"])
    @patch("via_stream_handler.os.path.exists", return_value=True)
    @patch("via_stream_handler.shutil.which", return_value="/usr/bin/ffmpeg_for_overlay_video")
    def test_creates_video_successfully(
        self, mock_which, mock_exists, mock_listdir, mock_run, mock_glob
    ):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri.file_duration = 2e9
        result = handler._create_video_from_cached_frames(ri)
        assert result is not None
        assert ri.request_id in result
        mock_run.assert_called_once()

    @patch("via_stream_handler.os.path.exists", return_value=True)
    @patch("via_stream_handler.shutil.which", return_value=None)
    def test_returns_none_when_ffmpeg_missing(self, mock_which, mock_exists):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri.file_duration = 1e9
        result = handler._create_video_from_cached_frames(ri)
        assert result is None

    @patch("via_stream_handler.os.path.exists", return_value=False)
    def test_returns_none_when_dir_missing(self, mock_exists):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri.file_duration = 1e9
        result = handler._create_video_from_cached_frames(ri)
        assert result is None

    @patch(
        "via_stream_handler.subprocess.run", side_effect=subprocess.CalledProcessError(1, "ffmpeg")
    )
    @patch("via_stream_handler.os.listdir", return_value=["frame_001.jpg"])
    @patch("via_stream_handler.os.path.exists", return_value=True)
    @patch("via_stream_handler.shutil.which", return_value="/usr/bin/ffmpeg_for_overlay_video")
    def test_returns_none_on_ffmpeg_error(self, mock_which, mock_exists, mock_listdir, mock_run):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri.file_duration = 1e9
        result = handler._create_video_from_cached_frames(ri)
        assert result is None


# ---------------------------------------------------------------------------
# _process_output
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProcessOutput:
    def _make_handler(self):
        from via_stream_handler import ViaStreamHandler

        handler = ViaStreamHandler.__new__(ViaStreamHandler)
        from threading import RLock

        handler._lock = RLock()
        handler._running = True
        handler._request_info_map = {}
        handler._live_stream_info_map = {}
        handler._metrics = MagicMock()
        handler._args = MagicMock()
        return handler

    def _make_chunk_response(self, start_ntp="t0", end_ntp="t1"):
        cr = MagicMock()
        cr.chunk.start_ntp = start_ntp
        cr.chunk.end_ntp = end_ntp
        return cr

    def test_successful_video_file(self):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri.is_live = False
        ri.status = RequestInfo.Status.PROCESSING
        ri.alert_review = False
        ri.start_time = time.time()

        resp = RequestInfo.Response("0", "60", "A summary")
        handler._get_aggregated_summary = MagicMock(return_value=[resp])
        handler.stop_via_gpu_monitor = MagicMock()

        chunk_responses = [self._make_chunk_response()]
        handler._process_output(ri, False, chunk_responses)

        assert ri.status == RequestInfo.Status.SUCCESSFUL
        assert ri.progress == 100
        assert len(ri.response) == 1

    def test_failed_summarization_non_live(self):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri.is_live = False
        ri.status = RequestInfo.Status.PROCESSING
        ri.alert_review = False
        ri.start_time = time.time()

        handler._get_aggregated_summary = MagicMock(side_effect=Exception("boom"))
        handler.stop_via_gpu_monitor = MagicMock()

        chunk_responses = [self._make_chunk_response()]
        handler._process_output(ri, False, chunk_responses)

        assert ri.status == RequestInfo.Status.FAILED

    def test_failed_summarization_live_appends_failure_response(self):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri.is_live = True
        ri.status = RequestInfo.Status.PROCESSING
        ri.alert_review = False
        ri.source_id = "live-1"
        ri.start_time = time.time()

        handler._get_aggregated_summary = MagicMock(side_effect=Exception("fail"))
        handler.stop_via_gpu_monitor = MagicMock()

        cr = self._make_chunk_response("ntp0", "ntp1")
        handler._process_output(ri, False, [cr])

        assert any("Summarization failed" in r.response for r in ri.response)

    def test_live_stream_ended(self):
        from via_stream_handler import LiveStreamInfo, RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri.is_live = True
        ri.status = RequestInfo.Status.PROCESSING
        ri.alert_review = False
        ri.source_id = "ls-1"
        ri.start_time = time.time()

        lsi = LiveStreamInfo()
        lsi.stop = False
        lsi.pending_futures = []
        handler._live_stream_info_map["ls-1"] = lsi

        resp = RequestInfo.Response("0", "60", "summary")
        handler._get_aggregated_summary = MagicMock(return_value=[resp])
        handler.stop_via_gpu_monitor = MagicMock()

        handler._process_output(ri, True, [self._make_chunk_response()])

        assert ri.status == RequestInfo.Status.SUCCESSFUL
        assert ri.progress == 100
        assert lsi.live_stream_ended is True


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# remove_rtsp_stream
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRemoveRtspStream:
    def _make_handler(self):
        from via_stream_handler import ViaStreamHandler

        handler = ViaStreamHandler.__new__(ViaStreamHandler)
        from threading import RLock

        handler._lock = RLock()
        handler._request_info_map = {}
        handler._live_stream_info_map = {}
        handler._ctx_mgr_pool = []
        handler._vlm_pipeline = MagicMock()
        return handler

    def test_returns_early_if_stream_not_active(self):
        handler = self._make_handler()
        handler.remove_rtsp_stream("not-active")
        handler._vlm_pipeline.remove_live_stream.assert_not_called()

    @patch("via_stream_handler.shutil.rmtree")
    def test_removes_active_stream(self, mock_rmtree):
        from via_stream_handler import LiveStreamInfo

        handler = self._make_handler()
        source_id = "live-stream-1"

        lsi = LiveStreamInfo()
        lsi.source_id = source_id
        handler._live_stream_info_map[source_id] = lsi

        handler.remove_rtsp_stream(source_id)

        handler._vlm_pipeline.remove_live_stream.assert_called_once_with(source_id)
        assert source_id not in handler._live_stream_info_map

    @patch("via_stream_handler.shutil.rmtree")
    def test_resets_ctx_mgr_for_chat_request(self, mock_rmtree):
        from via_stream_handler import LiveStreamInfo, RequestInfo

        handler = self._make_handler()
        source_id = "ls-chat"

        lsi = LiveStreamInfo()
        lsi.source_id = source_id
        handler._live_stream_info_map[source_id] = lsi

        ri = RequestInfo()
        ri.summarize = False
        ri.source_id = source_id
        ri.delete_external_collection = False
        mock_ctx = MagicMock()
        mock_ctx._process_index = 0
        ri._ctx_mgr = mock_ctx
        handler._request_info_map[ri.request_id] = ri

        handler.remove_rtsp_stream(source_id)

        mock_ctx.reset.assert_not_called()
        assert mock_ctx in handler._ctx_mgr_pool
        assert ri.request_id not in handler._request_info_map

    @patch("via_stream_handler.shutil.rmtree")
    def test_resets_ctx_mgr_for_summarize_request(self, mock_rmtree):
        from via_stream_handler import LiveStreamInfo, RequestInfo

        handler = self._make_handler()
        source_id = "ls-summ"

        lsi = LiveStreamInfo()
        lsi.source_id = source_id
        handler._live_stream_info_map[source_id] = lsi

        ri = RequestInfo()
        ri.summarize = True
        ri.source_id = source_id
        ri.delete_external_collection = True
        mock_ctx = MagicMock()
        mock_ctx._process_index = 1
        ri._ctx_mgr = mock_ctx
        handler._request_info_map[ri.request_id] = ri

        handler.remove_rtsp_stream(source_id)

        mock_ctx.reset.assert_called_once()
        call_args = mock_ctx.reset.call_args[0][0]
        assert "summarization" in call_args
        assert mock_ctx in handler._ctx_mgr_pool

    @patch("via_stream_handler.shutil.rmtree", side_effect=FileNotFoundError)
    def test_handles_missing_cached_frames_dir(self, mock_rmtree):
        from via_stream_handler import LiveStreamInfo

        handler = self._make_handler()
        source_id = "ls-no-cache"

        lsi = LiveStreamInfo()
        lsi.source_id = source_id
        handler._live_stream_info_map[source_id] = lsi

        handler.remove_rtsp_stream(source_id)
        mock_rmtree.assert_called_once()


# ---------------------------------------------------------------------------
# _sanitize_vlm_response
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSanitizeVlmResponse:
    def _sanitize(self, text):
        from via_stream_handler import ViaStreamHandler

        return ViaStreamHandler._sanitize_vlm_response(text)

    def test_empty_string_returns_empty(self):
        assert self._sanitize("") == ""

    def test_none_returns_none(self):
        assert self._sanitize(None) is None

    def test_strips_literal_unicode_escape_sequences(self):
        # Literal backslash-u followed by 4 hex digits should be removed
        result = self._sanitize(r"hello \u0041 world")
        assert r"\u0041" not in result
        assert "hello" in result
        assert "world" in result

    def test_removes_non_ascii_characters(self):
        result = self._sanitize("caf\u00e9")  # café
        assert result == "caf"

    def test_mixed_ascii_and_non_ascii_keeps_ascii(self):
        result = self._sanitize("hello \u00e9l\u00e8ve")  # hello élève
        assert result == "hello lve"

    def test_already_clean_ascii_unchanged(self):
        text = "Hello, world! 1234"
        assert self._sanitize(text) == text

    def test_multiple_unicode_escapes_all_stripped(self):
        result = self._sanitize(r"\u0041\u0042\u0043 rest")
        assert r"\u0041" not in result
        assert r"\u0042" not in result
        assert r"\u0043" not in result
        assert "rest" in result


# ---------------------------------------------------------------------------
# extract_json_from_vlm_response
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractJsonFromVlmResponse:
    def _make_handler(self):
        from via_stream_handler import ViaStreamHandler

        return ViaStreamHandler.__new__(ViaStreamHandler)

    def test_extracts_json_from_json_markdown_block(self):
        handler = self._make_handler()
        vlm_response = '```json\n{"key": "value"}\n```'
        result = handler.extract_json_from_vlm_response(vlm_response)
        assert result == '{"key": "value"}'

    def test_extracts_json_from_plain_markdown_block(self):
        handler = self._make_handler()
        vlm_response = '```\n{"key": "value"}\n```'
        result = handler.extract_json_from_vlm_response(vlm_response)
        assert result == '{"key": "value"}'

    def test_plain_json_returned_as_is(self):
        handler = self._make_handler()
        vlm_response = '{"key": "value"}'
        result = handler.extract_json_from_vlm_response(vlm_response)
        assert result == '{"key": "value"}'

    def test_embedded_json_in_text_extracted(self):
        handler = self._make_handler()
        vlm_response = 'Some preamble text {"key": "value"} trailing text'
        result = handler.extract_json_from_vlm_response(vlm_response)
        assert result == '{"key": "value"}'

    def test_unescapes_newline_sequences(self):
        handler = self._make_handler()
        vlm_response = '{"key": "line1\\nline2"}'
        result = handler.extract_json_from_vlm_response(vlm_response)
        assert "line1\nline2" in result

    def test_unescapes_quote_sequences(self):
        handler = self._make_handler()
        vlm_response = '{"key": \\"value\\"}'
        result = handler.extract_json_from_vlm_response(vlm_response)
        assert '\\"' not in result

    def test_array_json_preserved(self):
        handler = self._make_handler()
        vlm_response = '[{"a": 1}, {"b": 2}]'
        result = handler.extract_json_from_vlm_response(vlm_response)
        assert result == '[{"a": 1}, {"b": 2}]'

    def test_no_braces_returns_stripped_input(self):
        handler = self._make_handler()
        vlm_response = "  no json here  "
        result = handler.extract_json_from_vlm_response(vlm_response)
        assert result == "no json here"


# ---------------------------------------------------------------------------
# _process_output — additional branch coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProcessOutputAdditional:
    def _make_handler(self):
        from threading import RLock

        from via_stream_handler import ViaStreamHandler

        handler = ViaStreamHandler.__new__(ViaStreamHandler)
        handler._lock = RLock()
        handler._running = True
        handler._request_info_map = {}
        handler._live_stream_info_map = {}
        handler._metrics = MagicMock()
        handler._args = MagicMock()
        return handler

    def _make_chunk_response(self, start_ntp="t0", end_ntp="t1"):
        cr = MagicMock()
        cr.chunk.start_ntp = start_ntp
        cr.chunk.end_ntp = end_ntp
        return cr

    def test_live_stream_new_response_logs_info(self):
        """Covers line 812 (logger.info when live and new_response non-empty)."""
        from via_stream_handler import LiveStreamInfo, RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri.is_live = True
        ri.status = RequestInfo.Status.PROCESSING
        ri.alert_review = False
        ri.source_id = "ls-live-1"
        ri.start_time = time.time()

        lsi = LiveStreamInfo()
        lsi.stop = True  # stop=True avoids pending_futures.wait
        lsi.pending_futures = []
        handler._live_stream_info_map["ls-live-1"] = lsi

        resp = MagicMock()
        resp.start_timestamp = "t0"
        resp.end_timestamp = "t1"
        resp.response = "A good summary"  # avoid triggering "Summarization failed" branch
        handler._get_aggregated_summary = MagicMock(return_value=[resp])
        handler.stop_via_gpu_monitor = MagicMock()

        # is_live_stream_ended=False so aggregation runs and new_response is non-empty
        handler._process_output(ri, False, [self._make_chunk_response()])

        # The live-stream non-ended path: status not necessarily SUCCESSFUL here
        # but line 812 (logger.info for new_response) must have been hit
        handler._get_aggregated_summary.assert_called_once()

    def test_summarization_failed_in_response_text_raises(self):
        """Covers line 794 (raise when 'Summarization failed' in response text)."""
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri.is_live = False
        ri.status = RequestInfo.Status.PROCESSING
        ri.alert_review = False
        ri.start_time = time.time()

        # Return a response that contains "Summarization failed"
        bad_resp = MagicMock()
        bad_resp.response = "Summarization failed: something went wrong"
        handler._get_aggregated_summary = MagicMock(return_value=[bad_resp])
        handler.stop_via_gpu_monitor = MagicMock()

        handler._process_output(ri, False, [self._make_chunk_response()])

        # Exception is caught internally; status should be FAILED for non-live
        assert ri.status == RequestInfo.Status.FAILED


# ---------------------------------------------------------------------------
# _create_ctx_mgr_pool
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateCtxMgrPool:
    def _make_handler(self):
        from threading import RLock

        from via_stream_handler import ViaStreamHandler

        handler = ViaStreamHandler.__new__(ViaStreamHandler)
        handler._lock = RLock()
        handler._ctx_mgr_pool = []
        handler._args = MagicMock()
        handler._args.max_live_streams = 4
        handler.num_ctx_mgr = 0
        handler.MAX_STREAMS = 4
        handler.NUM_CA_RAG_PROCESSES_LAUNCH = 2
        return handler

    def _ctx_rag_patch(self):
        """Return a sys.modules patch that stubs out vss_ctx_rag.context_manager."""
        import sys

        mock_ctx_rag = MagicMock()
        mock_cm_mod = MagicMock()
        mock_ctx_rag.context_manager = mock_cm_mod
        return patch.dict(
            sys.modules,
            {
                "vss_ctx_rag": mock_ctx_rag,
                "vss_ctx_rag.context_manager": mock_cm_mod,
            },
        )

    def test_returns_early_when_pool_already_populated(self):
        handler = self._make_handler()
        handler._ctx_mgr_pool = [MagicMock()]  # non-empty pool
        with self._ctx_rag_patch():
            handler._create_ctx_mgr_pool(config={})
        # ContextManager should never have been called
        assert len(handler._ctx_mgr_pool) == 1  # unchanged

    def test_raises_when_num_ctx_mgr_at_max_streams(self):
        from via_stream_handler import ViaException

        handler = self._make_handler()
        handler.num_ctx_mgr = 4  # equal to MAX_STREAMS
        with self._ctx_rag_patch():
            with pytest.raises(ViaException):
                handler._create_ctx_mgr_pool(config={})

    def test_creates_context_managers_up_to_launch_count(self):
        import sys

        handler = self._make_handler()
        mock_cm_mod = MagicMock()
        mock_cm_instance = MagicMock()
        mock_cm_mod.ContextManager.return_value = mock_cm_instance
        with patch.dict(
            sys.modules,
            {
                "vss_ctx_rag": MagicMock(context_manager=mock_cm_mod),
                "vss_ctx_rag.context_manager": mock_cm_mod,
            },
        ):
            handler._create_ctx_mgr_pool(config={"key": "val"})
        assert mock_cm_mod.ContextManager.call_count == 2  # NUM_CA_RAG_PROCESSES_LAUNCH
        assert handler.num_ctx_mgr == 2

    def test_stops_adding_when_num_ctx_mgr_reaches_max_streams(self):
        import sys

        handler = self._make_handler()
        handler.num_ctx_mgr = 3  # 1 away from MAX_STREAMS=4
        handler.NUM_CA_RAG_PROCESSES_LAUNCH = 5  # would add 5, but stops at MAX
        mock_cm_mod = MagicMock()
        mock_cm_mod.ContextManager.return_value = MagicMock()
        with patch.dict(
            sys.modules,
            {
                "vss_ctx_rag": MagicMock(context_manager=mock_cm_mod),
                "vss_ctx_rag.context_manager": mock_cm_mod,
            },
        ):
            handler._create_ctx_mgr_pool(config={})
        # Only 1 should be created (num_ctx_mgr goes 3→4 then returns)
        assert mock_cm_mod.ContextManager.call_count == 1
        assert handler.num_ctx_mgr == 4


# ---------------------------------------------------------------------------
# ViaStreamHandler._sanitize_vlm_response
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSanitizeVlmResponseNew:
    def test_empty_string_returned_as_is(self):
        from via_stream_handler import ViaStreamHandler

        assert ViaStreamHandler._sanitize_vlm_response("") == ""

    def test_none_returned_as_is(self):
        from via_stream_handler import ViaStreamHandler

        assert ViaStreamHandler._sanitize_vlm_response(None) is None

    def test_removes_unicode_escape_sequences(self):
        from via_stream_handler import ViaStreamHandler

        result = ViaStreamHandler._sanitize_vlm_response(r"Hello \u0041 world")
        assert r"\u0041" not in result
        assert "Hello" in result
        assert "world" in result

    def test_removes_multiple_unicode_escapes(self):
        from via_stream_handler import ViaStreamHandler

        result = ViaStreamHandler._sanitize_vlm_response(r"\u0041\u0042\u0043")
        assert result == ""

    def test_strips_non_ascii_characters(self):
        from via_stream_handler import ViaStreamHandler

        result = ViaStreamHandler._sanitize_vlm_response("caf\u00e9 resumé")
        assert result == "caf resum"

    def test_plain_ascii_unchanged(self):
        from via_stream_handler import ViaStreamHandler

        text = "A normal ASCII string."
        assert ViaStreamHandler._sanitize_vlm_response(text) == text

    def test_mixed_unicode_escape_and_non_ascii(self):
        from via_stream_handler import ViaStreamHandler

        # literal backslash-u sequence plus actual non-ASCII
        result = ViaStreamHandler._sanitize_vlm_response("abc \\u1234 caf\u00e9")
        assert "\\u1234" not in result
        assert "abc" in result
        # non-ASCII é is dropped
        assert "\u00e9" not in result


# ---------------------------------------------------------------------------
# ViaStreamHandler.remove_request_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRemoveRequestIdAdditional:
    def _make_handler(self):
        from threading import RLock

        from via_stream_handler import ViaStreamHandler

        handler = ViaStreamHandler.__new__(ViaStreamHandler)
        handler._lock = RLock()
        handler._request_info_map = {}
        handler._live_stream_info_map = {}
        handler._ctx_mgr_pool = []
        return handler

    def test_removes_existing_request(self):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        handler._request_info_map[ri.request_id] = ri
        handler.remove_request_id(ri.request_id)
        assert ri.request_id not in handler._request_info_map

    def test_nonexistent_request_id_is_noop(self):
        handler = self._make_handler()
        # Should not raise
        handler.remove_request_id("does-not-exist")
        assert handler._request_info_map == {}

    def test_only_target_request_removed(self):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri1 = RequestInfo()
        ri2 = RequestInfo()
        handler._request_info_map[ri1.request_id] = ri1
        handler._request_info_map[ri2.request_id] = ri2
        handler.remove_request_id(ri1.request_id)
        assert ri1.request_id not in handler._request_info_map
        assert ri2.request_id in handler._request_info_map


# ---------------------------------------------------------------------------
# ViaStreamHandler.get_response
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetResponseAdditional:
    def _make_handler(self):
        from threading import RLock

        from via_stream_handler import ViaStreamHandler

        handler = ViaStreamHandler.__new__(ViaStreamHandler)
        handler._lock = RLock()
        handler._request_info_map = {}
        handler._live_stream_info_map = {}
        handler._ctx_mgr_pool = []
        return handler

    def test_raises_for_unknown_request_id(self):
        from via_stream_handler import ViaException

        handler = self._make_handler()
        with pytest.raises(ViaException):
            handler.get_response("no-such-id")

    def test_returns_all_response_and_clears_when_no_chunk_size(self):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri.response = ["r1", "r2", "r3"]
        handler._request_info_map[ri.request_id] = ri

        returned_info, response = handler.get_response(ri.request_id)
        assert returned_info is ri
        assert response == ["r1", "r2", "r3"]
        assert ri.response == []

    def test_returns_chunk_slice_and_removes_it(self):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri.response = ["r1", "r2", "r3", "r4"]
        handler._request_info_map[ri.request_id] = ri

        returned_info, response = handler.get_response(ri.request_id, chunk_response_size=2)
        assert response == ["r1", "r2"]
        assert ri.response == ["r3", "r4"]

    def test_chunk_size_larger_than_response_returns_all(self):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri.response = ["only"]
        handler._request_info_map[ri.request_id] = ri

        _, response = handler.get_response(ri.request_id, chunk_response_size=10)
        assert response == ["only"]
        assert ri.response == []


# ---------------------------------------------------------------------------
# ViaStreamHandler.wait_for_request_done
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWaitForRequestDoneAdditional:
    def _make_handler(self):
        from threading import RLock

        from via_stream_handler import ViaStreamHandler

        handler = ViaStreamHandler.__new__(ViaStreamHandler)
        handler._lock = RLock()
        handler._request_info_map = {}
        handler._live_stream_info_map = {}
        handler._ctx_mgr_pool = []
        return handler

    def test_raises_for_unknown_request_id(self):
        from via_stream_handler import ViaException

        handler = self._make_handler()
        with pytest.raises(ViaException):
            handler.wait_for_request_done("nonexistent")

    def test_returns_immediately_when_status_is_successful(self):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri.status = RequestInfo.Status.SUCCESSFUL
        handler._request_info_map[ri.request_id] = ri

        # Should return without blocking since status is already terminal
        handler.wait_for_request_done(ri.request_id)

    def test_returns_immediately_when_status_is_failed(self):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri.status = RequestInfo.Status.FAILED
        handler._request_info_map[ri.request_id] = ri

        handler.wait_for_request_done(ri.request_id)

    def test_waits_then_returns_when_status_becomes_successful(self):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri.status = RequestInfo.Status.PROCESSING
        handler._request_info_map[ri.request_id] = ri

        call_count = 0

        def mock_wait(timeout=None):
            nonlocal call_count
            call_count += 1
            ri.status = RequestInfo.Status.SUCCESSFUL

        ri.status_event.wait = mock_wait
        handler.wait_for_request_done(ri.request_id)
        assert call_count == 1


# ---------------------------------------------------------------------------
# ViaStreamHandler._remove_segmasks_from_cv_meta
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRemoveSegmasksFromCvMetaAdditional:
    def test_empty_cv_meta_returns_empty(self):
        from via_stream_handler import ViaStreamHandler

        result = ViaStreamHandler._remove_segmasks_from_cv_meta([])
        assert result == []

    def test_objects_without_misc_are_unchanged(self):
        from via_stream_handler import ViaStreamHandler

        cv_meta = [{"objects": [{"id": 1, "label": "car"}]}]
        result = ViaStreamHandler._remove_segmasks_from_cv_meta(cv_meta)
        assert result[0]["objects"][0] == {"id": 1, "label": "car"}

    def test_seg_cleared_in_misc(self):
        from via_stream_handler import ViaStreamHandler

        cv_meta = [
            {
                "objects": [
                    {
                        "id": 1,
                        "misc": [{"seg": {"mask": [1, 2, 3]}, "other": "data"}],
                    }
                ]
            }
        ]
        result = ViaStreamHandler._remove_segmasks_from_cv_meta(cv_meta)
        assert result[0]["objects"][0]["misc"][0]["seg"] == {}
        # other fields preserved
        assert result[0]["objects"][0]["misc"][0]["other"] == "data"

    def test_original_is_not_mutated(self):
        from via_stream_handler import ViaStreamHandler

        cv_meta = [
            {
                "objects": [
                    {"id": 1, "misc": [{"seg": {"mask": [9, 8, 7]}}]},
                ]
            }
        ]
        ViaStreamHandler._remove_segmasks_from_cv_meta(cv_meta)
        # Original must remain intact
        assert cv_meta[0]["objects"][0]["misc"][0]["seg"] == {"mask": [9, 8, 7]}

    def test_multiple_objects_and_misc_all_cleared(self):
        from via_stream_handler import ViaStreamHandler

        cv_meta = [
            {
                "objects": [
                    {"id": 1, "misc": [{"seg": {"a": 1}}, {"seg": {"b": 2}}]},
                    {"id": 2, "misc": [{"seg": {"c": 3}}]},
                ]
            }
        ]
        result = ViaStreamHandler._remove_segmasks_from_cv_meta(cv_meta)
        for obj in result[0]["objects"]:
            for misc in obj["misc"]:
                assert misc["seg"] == {}

    def test_data_without_objects_key(self):
        from via_stream_handler import ViaStreamHandler

        cv_meta = [{"timestamp": 12345}]
        result = ViaStreamHandler._remove_segmasks_from_cv_meta(cv_meta)
        assert result == [{"timestamp": 12345}]


# ---------------------------------------------------------------------------
# ViaStreamHandler.check_status_remove_req_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckStatusRemoveReqIdAdditional:
    def _make_handler(self):
        from threading import RLock

        from via_stream_handler import ViaStreamHandler

        handler = ViaStreamHandler.__new__(ViaStreamHandler)
        handler._lock = RLock()
        handler._request_info_map = {}
        handler._live_stream_info_map = {}
        handler._ctx_mgr_pool = []
        return handler

    def test_nonexistent_request_id_returns_silently(self):
        handler = self._make_handler()
        # Should not raise
        handler.check_status_remove_req_id("nonexistent-id")

    def test_progress_100_non_live_removes(self):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri.progress = 100
        ri.is_live = False
        ri._ctx_mgr = None
        handler._request_info_map[ri.request_id] = ri

        with patch.dict(os.environ, {"LVS_DISABLE_DB_RESET_ON_REQUEST_DONE": "false"}):
            handler.check_status_remove_req_id(ri.request_id)

        assert ri.request_id not in handler._request_info_map

    def test_progress_less_than_100_non_live_keeps(self):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri.progress = 50
        ri.is_live = False
        ri._ctx_mgr = None
        handler._request_info_map[ri.request_id] = ri

        handler.check_status_remove_req_id(ri.request_id)
        assert ri.request_id in handler._request_info_map

    def test_ctx_mgr_reset_and_returned_to_pool_when_removed(self):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri.progress = 100
        ri.is_live = False
        ri.source_id = "test-stream"
        ri.delete_external_collection = False
        mock_ctx = MagicMock()
        mock_ctx._process_index = 5
        ri._ctx_mgr = mock_ctx
        handler._request_info_map[ri.request_id] = ri

        with patch.dict(os.environ, {"LVS_DISABLE_DB_RESET_ON_REQUEST_DONE": "false"}):
            handler.check_status_remove_req_id(ri.request_id)

        mock_ctx.reset.assert_called_once()
        assert mock_ctx in handler._ctx_mgr_pool


# ---------------------------------------------------------------------------
# ViaStreamHandler.update_live_stream_summary_latency and
# ViaStreamHandler.update_live_stream_captions_latency
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLiveStreamLatencyUpdates:
    def _make_handler(self):
        from threading import RLock

        from via_stream_handler import ViaStreamHandler

        handler = ViaStreamHandler.__new__(ViaStreamHandler)
        handler._lock = RLock()
        handler._request_info_map = {}
        handler._live_stream_info_map = {}
        handler._ctx_mgr_pool = []
        return handler

    def test_update_summary_latency_does_not_raise(self):
        handler = self._make_handler()
        # Should be a no-op and not raise any exception
        handler.update_live_stream_summary_latency(1.23)

    def test_update_captions_latency_does_not_raise(self):
        handler = self._make_handler()
        handler.update_live_stream_captions_latency(4.56)

    def test_update_summary_latency_with_zero(self):
        handler = self._make_handler()
        handler.update_live_stream_summary_latency(0.0)

    def test_update_captions_latency_with_zero(self):
        handler = self._make_handler()
        handler.update_live_stream_captions_latency(0.0)


# ---------------------------------------------------------------------------
# ViaStreamHandler.get_active_streams_info
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetActiveStreamsInfo:
    def _make_handler(self):
        from threading import RLock

        from via_stream_handler import ViaStreamHandler

        handler = ViaStreamHandler.__new__(ViaStreamHandler)
        handler._lock = RLock()
        handler._request_info_map = {}
        handler._live_stream_info_map = {}
        handler._ctx_mgr_pool = []
        return handler

    def test_returns_empty_dict_when_no_requests(self):
        handler = self._make_handler()
        result = handler.get_active_streams_info()
        assert result == {}

    def test_returns_empty_dict_when_no_active_streams(self):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri._fps_is_active = False
        ri.source_id = "stream-1"
        handler._request_info_map[ri.request_id] = ri

        result = handler.get_active_streams_info()
        assert result == {}

    def test_returns_fps_for_active_stream(self):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri._fps_is_active = True
        ri._fps_start_time = 0.0
        ri._fps_last_update_time = 10.0
        ri._fps_frame_count = 250
        ri.source_id = "stream-active"
        handler._request_info_map[ri.request_id] = ri

        result = handler.get_active_streams_info()
        assert "stream-active" in result
        assert result["stream-active"] == pytest.approx(25.0)

    def test_inactive_stream_excluded_from_result(self):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()

        ri_active = RequestInfo()
        ri_active._fps_is_active = True
        ri_active._fps_start_time = 0.0
        ri_active._fps_last_update_time = 5.0
        ri_active._fps_frame_count = 100
        ri_active.source_id = "stream-active"

        ri_inactive = RequestInfo()
        ri_inactive._fps_is_active = False
        ri_inactive.source_id = "stream-inactive"

        handler._request_info_map[ri_active.request_id] = ri_active
        handler._request_info_map[ri_inactive.request_id] = ri_inactive

        result = handler.get_active_streams_info()
        assert "stream-active" in result
        assert "stream-inactive" not in result

    def test_stream_with_no_source_id_excluded(self):
        from via_stream_handler import RequestInfo

        handler = self._make_handler()
        ri = RequestInfo()
        ri._fps_is_active = True
        ri.source_id = ""
        handler._request_info_map[ri.request_id] = ri

        result = handler.get_active_streams_info()
        assert result == {}


# ---------------------------------------------------------------------------
# ES dependency error classifier
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClassifyEsError:
    """``lvs_errors.classify_es_error`` translates raw ES / wrapper
    exceptions into ``(http_status, user_message)`` pairs that are safe to
    surface in HTTP 503 responses.
    """

    _SHARD_MESSAGE = (
        "Service temporarily unavailable: Elasticsearch shard limit exceeded. "
        "See server logs for details."
    )
    _GENERIC_PREFIX = "Service temporarily unavailable: Elasticsearch dependency error"
    _INTERNAL_MESSAGE = "Internal server error. See server logs for details."

    def test_shard_cap_exhaustion_message(self):
        from lvs_errors import classify_es_error

        exc = Exception(
            "BadRequestError(400, 'validation_exception', "
            "'this cluster currently has [1000]/[1000] maximum normal shards open')"
        )
        status, message = classify_es_error(exc)
        assert status == 503
        assert message == self._SHARD_MESSAGE
        assert "1000" not in message  # No raw shard counts leak to user.

    def test_shard_cap_secondary_fingerprint(self):
        from lvs_errors import classify_es_error

        exc = Exception("Some wrapping: max_shards_per_node setting reached")
        status, message = classify_es_error(exc)
        assert status == 503
        assert message == self._SHARD_MESSAGE

    def test_shard_cap_inside_cause_chain(self):
        """RagAdapter wraps ES errors in ViaException ``from exc``;
        the classifier must walk the cause chain to find the fingerprint."""
        from lvs_errors import classify_es_error

        try:
            try:
                raise Exception("BadRequestError(400, '...maximum normal shards open...')")
            except Exception as inner:
                raise RuntimeError("RAG add_doc failed: see cause") from inner
        except RuntimeError as outer:
            status, message = classify_es_error(outer)

        assert status == 503
        assert message == self._SHARD_MESSAGE

    def test_generic_es_4xx_returns_sanitised_message(self):
        from lvs_errors import classify_es_error

        class FakeESError(Exception):
            def __init__(self, msg, status):
                super().__init__(msg)
                self.status_code = status

        exc = FakeESError("index_not_found_exception: no such index", 404)
        status, message = classify_es_error(exc)
        assert status == 503
        assert message.startswith(self._GENERIC_PREFIX)
        assert "(404)" in message
        # Raw ES message must NOT leak into the user-facing string.
        assert "index_not_found_exception" not in message

    def test_generic_es_5xx_returns_sanitised_message(self):
        from lvs_errors import classify_es_error

        class FakeESError(Exception):
            def __init__(self, msg, status):
                super().__init__(msg)
                self.status_code = status

        exc = FakeESError("internal server error", 503)
        status, message = classify_es_error(exc)
        assert status == 503
        assert "(503)" in message

    def test_status_walks_cause_chain(self):
        """When the ES status_code is on the inner exception, the
        classifier should still surface it via __cause__ traversal."""
        from lvs_errors import classify_es_error

        class FakeESError(Exception):
            def __init__(self, msg, status):
                super().__init__(msg)
                self.status_code = status

        try:
            try:
                raise FakeESError("transport error", 502)
            except FakeESError as inner:
                raise RuntimeError("wrapper") from inner
        except RuntimeError as outer:
            status, message = classify_es_error(outer)

        assert status == 503
        assert "(502)" in message

    def test_non_es_exception_returns_500(self):
        from lvs_errors import classify_es_error

        exc = ValueError("not an elasticsearch error at all")
        status, message = classify_es_error(exc)
        assert status == 500
        assert message == self._INTERNAL_MESSAGE

    def test_logs_full_detail_at_error_level(self, caplog):
        import logging

        from lvs_errors import classify_es_error

        with caplog.at_level(logging.ERROR, logger="lvs_errors"):
            classify_es_error(Exception("kept-secret-internal-detail"))

        # Full underlying exception detail must reach the logs even
        # though the user-facing message hides it.
        joined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "kept-secret-internal-detail" in joined


# ---------------------------------------------------------------------------
# File-path completion handler drops the per-file index
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckStatusRemoveReqIdDropsIndex:
    """``check_status_remove_req_id`` should call
    ``drop_collection_for_asset(force_legacy=True)`` after a file
    summarize completes, but ONLY when:

      * ``LVS_DISABLE_DB_RESET_ON_REQUEST_DONE`` is unset / "false"
      * ``req_info.is_live`` is False (live-stream completions reuse
        the index intentionally)

    Live-stream completions and retain-mode completions must NOT drop
    the index.
    """

    @staticmethod
    def _make_completed_file_request():
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri.progress = 100
        ri.is_live = False
        ri.source_id = "file-id-abc"
        ri.delete_external_collection = False
        mock_ctx = MagicMock()
        mock_ctx._process_index = 7
        ri._ctx_mgr = mock_ctx
        handler._request_info_map[ri.request_id] = ri
        return handler, ri, mock_ctx

    def test_drops_index_when_gate_unset(self):
        handler, ri, _ = self._make_completed_file_request()
        handler.drop_collection_for_asset = MagicMock(return_value={"ok": True})

        with patch.dict(os.environ, {"LVS_DISABLE_DB_RESET_ON_REQUEST_DONE": "false"}):
            handler.check_status_remove_req_id(ri.request_id)

        handler.drop_collection_for_asset.assert_called_once_with(ri.source_id, force_legacy=True)

    def test_skips_drop_when_gate_true(self):
        handler, ri, _ = self._make_completed_file_request()
        handler.drop_collection_for_asset = MagicMock(return_value={"ok": True})

        with patch.dict(os.environ, {"LVS_DISABLE_DB_RESET_ON_REQUEST_DONE": "true"}):
            handler.check_status_remove_req_id(ri.request_id)

        handler.drop_collection_for_asset.assert_not_called()

    def test_skips_drop_for_live_stream_completion(self):
        from via_stream_handler import LiveStreamInfo, RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri.progress = 100
        ri.is_live = True
        ri.source_id = "live-stream-xyz"
        ri.delete_external_collection = False
        mock_ctx = MagicMock()
        mock_ctx._process_index = 4
        ri._ctx_mgr = mock_ctx
        handler._request_info_map[ri.request_id] = ri
        # Live-stream branch needs a live_stream_info_map entry that
        # claims the stream has ended for the cleanup branch to fire.
        lsi = LiveStreamInfo()
        lsi.live_stream_ended = True
        handler._live_stream_info_map[ri.source_id] = lsi
        handler.drop_collection_for_asset = MagicMock(return_value={"ok": True})

        with patch.dict(os.environ, {"LVS_DISABLE_DB_RESET_ON_REQUEST_DONE": "false"}):
            handler.check_status_remove_req_id(ri.request_id)

        handler.drop_collection_for_asset.assert_not_called()

    def test_drop_failure_does_not_break_pool_return(self):
        """If drop_collection_for_asset raises (transient ES blip during
        cleanup), the ctx_mgr must still be returned to the pool.
        """
        handler, ri, mock_ctx = self._make_completed_file_request()
        handler.drop_collection_for_asset = MagicMock(
            side_effect=Exception("transient ES failure during cleanup")
        )

        with patch.dict(os.environ, {"LVS_DISABLE_DB_RESET_ON_REQUEST_DONE": "false"}):
            handler.check_status_remove_req_id(ri.request_id)

        assert mock_ctx in handler._ctx_mgr_pool


# ---------------------------------------------------------------------------
# drop_collection_for_asset's force_legacy bypass
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDropCollectionForceLegacy:
    """``force_legacy=True`` bypasses the KAFKA_ENABLED guard so the
    file-path completion handler can drop legacy in-process indices.
    Default callers (DELETE /files, etc.) keep their existing behaviour.
    """

    @staticmethod
    def _make_handler(*, kafka_enabled: bool, disable_ca_rag: bool = False):
        handler = _make_mock_stream_handler()
        handler._kafka_enabled = kafka_enabled
        handler._args = MagicMock()
        handler._args.disable_ca_rag = disable_ca_rag
        handler._create_ctx_mgr_pool = MagicMock()
        # drop_collection_for_asset deepcopies _ca_rag_config and writes
        # context_manager.uuid before calling ctx_mgr.configure(); seed the
        # nested key so the test isn't testing an empty-dict KeyError.
        handler._ca_rag_config = {"context_manager": {"uuid": ""}}
        return handler

    def test_kafka_disabled_default_skips(self):
        handler = self._make_handler(kafka_enabled=False)
        result = handler.drop_collection_for_asset("any-id")
        assert result == {"skipped": True, "reason": "KAFKA_ENABLED=false"}

    def test_kafka_disabled_force_legacy_runs(self):
        handler = self._make_handler(kafka_enabled=False)
        mock_ctx = MagicMock()
        mock_ctx.drop_collection.return_value = {"dropped": "idx"}
        handler._ctx_mgr_pool = [mock_ctx]

        result = handler.drop_collection_for_asset("file-id-abc", force_legacy=True)

        assert result == {"dropped": "idx"}
        mock_ctx.drop_collection.assert_called_once()
        mock_ctx in handler._ctx_mgr_pool  # returned to pool

    def test_kafka_enabled_runs_without_force(self):
        handler = self._make_handler(kafka_enabled=True)
        mock_ctx = MagicMock()
        mock_ctx.drop_collection.return_value = {"dropped": "idx"}
        handler._ctx_mgr_pool = [mock_ctx]

        result = handler.drop_collection_for_asset("asset-id")
        assert result == {"dropped": "idx"}


# ---------------------------------------------------------------------------
# Per-chunk add_doc ES failure propagation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleEsDependencyError:
    """The shared ``_handle_es_dependency_error`` helper sets the
    request status to FAILED, stashes the classified user message + 503,
    aborts pending VLM chunks (for files), and unblocks
    ``wait_for_request_done`` via ``status_event.set()``.
    """

    def test_shard_cap_marks_failed_and_503(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri.is_live = False
        ri.source_id = "file-id"
        baseline_progress = ri.progress
        exc = Exception(
            "BadRequestError: this cluster currently has [1000]/[1000] "
            "maximum normal shards open"
        )

        http_status, user_message = handler._handle_es_dependency_error(ri, exc)

        assert http_status == 503
        assert ri.status == RequestInfo.Status.FAILED
        assert ri.error_message == user_message
        assert ri.dependency_http_status == 503
        assert ri.dependency_error_code == "DependencyError"
        assert "shard limit exceeded" in user_message
        # The helper deliberately does NOT set progress=100 here so that
        # check_status_remove_req_id does not recycle the ctx_mgr while
        # in-flight VLM chunks are still bound to it. The natural
        # _process_output completion path is the only safe place to
        # mark a failed request finished.
        assert ri.progress == baseline_progress

    def test_aborts_chunks_for_file_path(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri.is_live = False
        ri.source_id = "file-id-xyz"

        handler._handle_es_dependency_error(ri, Exception("transient ES blip"))

        handler._vlm_pipeline.abort_chunks.assert_called_once_with("file-id-xyz")

    def test_does_not_abort_for_live_stream(self):
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri.is_live = True
        ri.source_id = "live-stream"

        handler._handle_es_dependency_error(ri, Exception("transient ES blip"))

        handler._vlm_pipeline.abort_chunks.assert_not_called()

    def test_does_not_set_status_event(self):
        """Helper must NOT call ``status_event.set()``: doing so from
        the chunk callback wakes /v1/summarize before the in-flight
        VLM chunks have stopped, which lets cleanup recycle the
        ctx_mgr out from under those chunks. ``wait_for_request_done``
        polls ``status`` every 5 s, so a FAILED status still surfaces
        the 503 to the user without the chunk-callback race.
        """
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri.is_live = False
        ri.source_id = "file-id"
        ri.status_event = MagicMock()

        handler._handle_es_dependency_error(ri, Exception("ES error"))

        ri.status_event.set.assert_not_called()

    def test_abort_chunks_failure_does_not_propagate(self):
        """A flaky abort_chunks shouldn't shadow the original ES error."""
        from via_stream_handler import RequestInfo

        handler = _make_mock_stream_handler()
        ri = RequestInfo()
        ri.is_live = False
        ri.source_id = "file-id"
        handler._vlm_pipeline.abort_chunks.side_effect = Exception("abort failed")

        # Should NOT raise.
        http_status, _ = handler._handle_es_dependency_error(
            ri, Exception("max_shards_per_node exceeded")
        )
        assert http_status == 503


# ---------------------------------------------------------------------------
# Dense caption cache span lifecycle (NVBug 6537736)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDenseCaptionCacheSpanLifecycle:
    """_trigger_query must not leak spans when it returns via the cache path.

    A span only reaches the exporter once end() is called, so an unclosed E2E
    span means the whole cache-hit trace is missing from the backend.
    """

    @staticmethod
    def _memory_tracer():
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        return provider.get_tracer(__name__), exporter

    def _trigger_with_cached_chunks(self, tmp_path, monkeypatch, cached_chunks):
        import via_stream_handler as vsh
        from via_stream_handler import RequestInfo, ViaStreamHandler

        tracer, exporter = self._memory_tracer()
        monkeypatch.setattr(vsh, "is_tracing_enabled", lambda: True)
        monkeypatch.setattr(vsh, "get_tracer", lambda: tracer)
        monkeypatch.setenv("ENABLE_DENSE_CAPTION", "true")
        monkeypatch.setenv("VIA_LOG_DIR", str(tmp_path))

        req_info = RequestInfo()
        req_info.source_id = "cache-src"
        (tmp_path / f"dc_{req_info.source_id}.json").write_text("{}")

        deserialized = MagicMock()
        deserialized.processed_chunk_list = cached_chunks
        monkeypatch.setattr(vsh.DCSerializer, "from_json", lambda _path: deserialized)

        handler = ViaStreamHandler.__new__(ViaStreamHandler)
        handler._on_vlm_chunk_response = MagicMock()
        handler._trigger_query(req_info)
        return handler, exporter

    def test_empty_cache_closes_both_spans(self, tmp_path, monkeypatch):
        handler, exporter = self._trigger_with_cached_chunks(tmp_path, monkeypatch, [])

        handler._on_vlm_chunk_response.assert_not_called()
        assert {span.name for span in exporter.get_finished_spans()} == {
            "Summarization E2E Latency",
            "VLM Pipeline Latency",
        }

    def test_populated_cache_leaves_spans_to_the_chunk_handler(self, tmp_path, monkeypatch):
        handler, exporter = self._trigger_with_cached_chunks(
            tmp_path, monkeypatch, ["chunk-a", "chunk-b"]
        )

        assert handler._on_vlm_chunk_response.call_count == 2
        # The real _on_vlm_chunk_response ends the pipeline span and queues
        # _process_output for the E2E span on the final chunk. It is mocked
        # here, so nothing should have closed them: _trigger_query closing
        # them itself would truncate the trace.
        assert len(exporter.get_finished_spans()) == 0
