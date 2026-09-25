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
Unit tests for the CA-RAG aggregation empty-result guard.

The aggregation LLM intermittently samples an unparseable response, which used to
surface to the caller as HTTP 200 with total_events=0, events=[] and
video_summary="". These tests cover structured-result repair, bounded retries,
and the actionable failure returned when every attempt remains empty.
"""

import json

import pytest


def _make_handler(retries=2):
    from via_stream_handler import ViaStreamHandler

    handler = ViaStreamHandler.__new__(ViaStreamHandler)
    handler._aggregation_empty_retries = retries
    return handler


class _FakeCtxMgr:
    """ctx_mgr stub returning a scripted response per call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def call(self, state):
        self.calls.append(state)
        return self._responses.pop(0)


def _response(function_name, result, metadata=None):
    return {function_name: {"result": result, "metadata": metadata or {}}}


def _summary(events=None, video_summary=""):
    return json.dumps(
        {
            "events": events or [],
            "total_events": len(events or []),
            "video_summary": video_summary,
            "uuid": "test-id",
        }
    )


class TestIsEmptyAggregationResult:
    """Only results with neither events nor a summary count as empty."""

    @pytest.mark.parametrize(
        "result",
        [
            None,
            "",
            "   ",
            _summary(),
            _summary(video_summary="   "),
            {"events": [], "total_events": 0, "video_summary": ""},
            "[]",
        ],
        ids=[
            "none",
            "blank",
            "whitespace",
            "no-events-no-summary",
            "whitespace-summary",
            "dict-result",
            "empty-json-array",
        ],
    )
    def test_empty_results(self, result):
        handler = _make_handler()
        assert handler._is_empty_aggregation_result(result) is True

    @pytest.mark.parametrize(
        "result",
        [
            _summary(video_summary="A forklift crossed the aisle."),
            _summary(events=[{"type": "forklift"}]),
            "A forklift crossed the aisle.",
        ],
        ids=["summary-only", "events-only", "free-text"],
    )
    def test_non_empty_results(self, result):
        handler = _make_handler()
        assert handler._is_empty_aggregation_result(result) is False


class TestCallAggregationWithEmptyGuard:
    """An empty aggregation sample is retried before it reaches the caller."""

    def test_first_non_empty_result_returned_without_retry(self):
        handler = _make_handler(retries=2)
        expected = _response("summarization", _summary(video_summary="A busy aisle."))
        ctx_mgr = _FakeCtxMgr([expected])

        response = handler._call_aggregation_with_empty_guard(
            ctx_mgr, "summarization", {"start_index": 0, "end_index": 1}, "test-id"
        )

        assert response is expected
        assert len(ctx_mgr.calls) == 1

    def test_empty_result_is_retried_until_non_empty(self):
        handler = _make_handler(retries=2)
        good = _response("summarization", _summary(video_summary="A forklift tipped over."))
        ctx_mgr = _FakeCtxMgr([_response("summarization", _summary()), good])

        response = handler._call_aggregation_with_empty_guard(
            ctx_mgr, "summarization", {"start_index": 0, "end_index": 1}, "test-id"
        )

        assert response is good
        assert len(ctx_mgr.calls) == 2

    def test_210_second_sample_fails_actionably_after_bounded_retries(self):
        from via_exception import ViaException

        handler = _make_handler(retries=2)
        ctx_mgr = _FakeCtxMgr(
            [_response("summarization", _summary()) for _ in range(3)]
        )

        with pytest.raises(ViaException) as exc_info:
            handler._call_aggregation_with_empty_guard(
                ctx_mgr,
                "summarization",
                # The documented 210-second sample produces 21 ten-second chunks.
                {"start_index": 0, "end_index": 21},
                "warehouse-sample",
                "job-210-second-regression",
            )

        assert len(ctx_mgr.calls) == 3
        assert exc_info.value.status_code == 502
        assert exc_info.value.code == "AggregationFailed"
        assert exc_info.value.job_id == "job-210-second-regression"
        assert exc_info.value.failed_stage == "aggregation"

    def test_retries_disabled_fails_after_a_single_call(self):
        from via_exception import ViaException

        handler = _make_handler(retries=0)
        only = _response("summarization", _summary())
        ctx_mgr = _FakeCtxMgr([only])

        with pytest.raises(ViaException):
            handler._call_aggregation_with_empty_guard(
                ctx_mgr, "summarization", {"start_index": 0, "end_index": 1}, "test-id"
            )

        assert len(ctx_mgr.calls) == 1

    @pytest.mark.parametrize(
        "raw",
        [
            '```json\n{"events": [{"type": "forklift"}], "video_summary": ""}\n```',
            '{"events": [{"type": "forklift",}], "video_summary": "",}',
            '{"events": "[{\\"type\\": \\"forklift\\"}]", "video_summary": ""}',
        ],
        ids=["markdown-fence", "repairable-json", "serialized-events"],
    )
    def test_event_json_is_repaired_and_normalized(self, raw):
        handler = _make_handler(retries=2)
        response = _response("summarization", raw)
        ctx_mgr = _FakeCtxMgr([response])

        result = handler._call_aggregation_with_empty_guard(
            ctx_mgr, "summarization", {"start_index": 0, "end_index": 21}, "test-id"
        )

        parsed = json.loads(result["summarization"]["result"])
        assert parsed["events"] == [{"type": "forklift"}]
        assert len(ctx_mgr.calls) == 1

    @pytest.mark.parametrize(
        "raw",
        [
            '{"events": "null", "video_summary": ""}',
            '{"events": "42", "video_summary": ""}',
            '{"events": [null, "not-an-event"], "video_summary": {}}',
            '{"events": [], "video_summary": 42}',
        ],
        ids=[
            "serialized-null-events",
            "serialized-scalar-events",
            "scalar-event-list-and-object-summary",
            "numeric-summary",
        ],
    )
    def test_malformed_fields_do_not_bypass_empty_guard(self, raw):
        from via_exception import ViaException

        handler = _make_handler(retries=0)
        ctx_mgr = _FakeCtxMgr([_response("summarization", raw)])

        with pytest.raises(ViaException) as exc_info:
            handler._call_aggregation_with_empty_guard(
                ctx_mgr, "summarization", {"start_index": 0, "end_index": 21}, "test-id"
            )

        assert exc_info.value.code == "AggregationFailed"
        assert len(ctx_mgr.calls) == 1

    def test_malformed_events_do_not_discard_valid_summary(self):
        handler = _make_handler(retries=0)
        ctx_mgr = _FakeCtxMgr(
            [
                _response(
                    "summarization",
                    '{"events": "null", "video_summary": "Warehouse activity."}',
                )
            ]
        )

        result = handler._call_aggregation_with_empty_guard(
            ctx_mgr, "summarization", {"start_index": 0, "end_index": 21}, "test-id"
        )

        parsed = json.loads(result["summarization"]["result"])
        assert parsed["events"] == []
        assert parsed["video_summary"] == "Warehouse activity."
        assert len(ctx_mgr.calls) == 1

    def test_error_response_is_not_retried(self):
        handler = _make_handler(retries=2)
        error = {"error": "elasticsearch shard limit exceeded"}
        ctx_mgr = _FakeCtxMgr([error])

        response = handler._call_aggregation_with_empty_guard(
            ctx_mgr, "summarization", {"start_index": 0, "end_index": 1}, "test-id"
        )

        assert response is error
        assert len(ctx_mgr.calls) == 1

    def test_metadata_comes_from_the_successful_attempt(self):
        handler = _make_handler(retries=2)
        good = _response(
            "summarization_online",
            _summary(video_summary="A crash near the loading dock."),
            metadata={"total_tokens": 1234},
        )
        ctx_mgr = _FakeCtxMgr([_response("summarization_online", ""), good])

        response = handler._call_aggregation_with_empty_guard(
            ctx_mgr, "summarization_online", {"uuids": ["test-id"]}, "test-id"
        )

        assert response["summarization_online"]["metadata"] == {"total_tokens": 1234}
        assert len(ctx_mgr.calls) == 2


class TestReadAggregationEmptyRetries:
    """LVS_AGGREGATION_EMPTY_RETRIES overrides the default retry budget."""

    def test_default_when_unset(self, monkeypatch):
        from via_stream_handler import DEFAULT_AGGREGATION_EMPTY_RETRIES, ViaStreamHandler

        monkeypatch.delenv("LVS_AGGREGATION_EMPTY_RETRIES", raising=False)
        assert (
            ViaStreamHandler._read_aggregation_empty_retries() == DEFAULT_AGGREGATION_EMPTY_RETRIES
        )

    @pytest.mark.parametrize("raw,expected", [("0", 0), ("1", 1), ("5", 5)])
    def test_valid_override(self, monkeypatch, raw, expected):
        from via_stream_handler import ViaStreamHandler

        monkeypatch.setenv("LVS_AGGREGATION_EMPTY_RETRIES", raw)
        assert ViaStreamHandler._read_aggregation_empty_retries() == expected

    def test_invalid_value_falls_back_to_default(self, monkeypatch):
        from via_stream_handler import DEFAULT_AGGREGATION_EMPTY_RETRIES, ViaStreamHandler

        monkeypatch.setenv("LVS_AGGREGATION_EMPTY_RETRIES", "many")
        assert (
            ViaStreamHandler._read_aggregation_empty_retries() == DEFAULT_AGGREGATION_EMPTY_RETRIES
        )

    def test_negative_value_disables_retry(self, monkeypatch):
        from via_stream_handler import ViaStreamHandler

        monkeypatch.setenv("LVS_AGGREGATION_EMPTY_RETRIES", "-3")
        assert ViaStreamHandler._read_aggregation_empty_retries() == 0
