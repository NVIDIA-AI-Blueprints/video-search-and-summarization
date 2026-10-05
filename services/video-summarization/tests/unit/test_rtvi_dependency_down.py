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
Unit tests for RTVI-dependency-down error handling.

Verifies that when RTVI is unreachable (ConnectionError / Timeout), each
ViaStreamHandler code path logs "RTVI dependency is down" and surfaces
a clear 503 error instead of an opaque traceback.

Covers:
  - start_stream_captions  → raises ViaException(503)
  - _trigger_query          → sets RequestInfo.status=FAILED with 503
"""

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import RLock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests.exceptions

from via_exception import ViaException
from via_stream_handler import RequestInfo, ViaStreamHandler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_RTVI_URL = "http://10.99.99.99:8083"


def _make_handler():
    """Create a ViaStreamHandler with mocked __init__ for isolated testing."""
    with patch.object(ViaStreamHandler, "__init__", lambda self, *a, **k: None):
        handler = ViaStreamHandler.__new__(ViaStreamHandler)
        handler._lock = RLock()
        handler._running = True
        handler._request_info_map = {}
        handler._live_stream_info_map = {}
        handler._metrics = MagicMock()
        handler._vlm_pipeline = MagicMock()
        handler._vlm_pipeline._base_url = FAKE_RTVI_URL
        handler._vlm_pipeline.get_models_info.return_value = SimpleNamespace(
            id="test-model", created=0, owned_by="nvidia", api_type=""
        )
        handler._ctx_mgr = None
        handler._ctx_mgr_pool = []
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
        handler._kafka_enabled = False
        return handler


def _make_generate_captions_request():
    """Build a minimal GenerateCaptionsRequest for start_stream_captions."""
    from vss_api_models import GenerateCaptionsRequest

    return GenerateCaptionsRequest(
        id=uuid.uuid4(),
        model="test-model",
        scenario="test",
        events=["object"],
        chunk_duration=10,
    )


def _make_req_info(*, is_live=False):
    """Build a RequestInfo wired up enough for the query paths."""
    ri = RequestInfo()
    ri.source_id = str(uuid.uuid4())
    ri.is_live = is_live
    ri.start_time = time.time()
    ri.chunk_size = 10
    ri.chunk_overlap_duration = 0
    ri.enable_audio = False
    ri.vlm_input_width = 0
    ri.vlm_input_height = 0
    ri.vlm_request_params = SimpleNamespace(
        vlm_prompt="test prompt",
        vlm_generation_config={},
    )
    ri._ctx_mgr = None
    ri._output_process_thread_pool = None
    ri._e2e_span = None
    ri._e2e_span_context = None
    ri.vlm_pipeline_span = None
    ri._vlm_pipeline_span_context = None
    ri.start_timestamp = None
    ri.end_timestamp = None
    ri.status_event = MagicMock()
    return ri


# Connection errors that mimic real RTVI-down scenarios
CONNECTION_ERRORS = [
    requests.exceptions.ConnectionError(
        "HTTPConnectionPool(host='10.99.99.99', port=8083): "
        "Max retries exceeded with url: /v1/generate_captions"
    ),
    requests.exceptions.Timeout("Read timed out. (read timeout=30)"),
]


# ---------------------------------------------------------------------------
# start_stream_captions — raises ViaException on RTVI down
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStartStreamCaptionsRtviDown:
    @pytest.mark.parametrize("exc", CONNECTION_ERRORS, ids=["ConnectionError", "Timeout"])
    def test_raises_via_exception_503(self, exc):
        handler = _make_handler()
        handler._vlm_pipeline.start_captions.side_effect = exc

        with pytest.raises(ViaException) as exc_info:
            handler.start_stream_captions(_make_generate_captions_request())

        assert exc_info.value.status_code == 503
        assert "RTVI dependency is down" in exc_info.value.message
        assert FAKE_RTVI_URL in exc_info.value.message
        assert exc_info.value.code == "DependencyUnavailable"

    def test_error_message_includes_rtvi_url(self):
        handler = _make_handler()
        handler._vlm_pipeline._base_url = "http://custom-host:9999"
        handler._vlm_pipeline.start_captions.side_effect = requests.exceptions.ConnectionError(
            "connection refused"
        )

        with pytest.raises(ViaException) as exc_info:
            handler.start_stream_captions(_make_generate_captions_request())

        assert "http://custom-host:9999" in exc_info.value.message

    def test_non_connection_errors_still_propagate(self):
        handler = _make_handler()
        handler._vlm_pipeline.start_captions.side_effect = RuntimeError("GPU OOM")

        with pytest.raises(RuntimeError, match="GPU OOM"):
            handler.start_stream_captions(_make_generate_captions_request())


# ---------------------------------------------------------------------------
# _trigger_query (file summarization) — sets RequestInfo.status = FAILED
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTriggerQueryRtviDown:
    @pytest.mark.parametrize("exc", CONNECTION_ERRORS, ids=["ConnectionError", "Timeout"])
    def test_request_info_marked_failed_503(self, exc):
        handler = _make_handler()
        handler._vlm_pipeline.generate_captions_stream.side_effect = exc

        req_info = _make_req_info()
        handler._request_info_map[req_info.request_id] = req_info

        with patch.dict(os.environ, {"ENABLE_DENSE_CAPTION": ""}):
            handler._trigger_query(req_info)

        assert req_info.status == RequestInfo.Status.FAILED
        assert req_info.rtvi_status_code == 503
        assert req_info.rtvi_error_code == "DependencyUnavailable"
        assert "RTVI dependency is down" in req_info.error_message
        assert FAKE_RTVI_URL in req_info.error_message

    def test_progress_set_to_100_and_event_signalled(self):
        handler = _make_handler()
        handler._vlm_pipeline.generate_captions_stream.side_effect = (
            requests.exceptions.ConnectionError("refused")
        )

        req_info = _make_req_info()
        handler._request_info_map[req_info.request_id] = req_info

        with patch.dict(os.environ, {"ENABLE_DENSE_CAPTION": ""}):
            handler._trigger_query(req_info)

        assert req_info.progress == 100
        req_info.status_event.set.assert_called_once()

    def test_metrics_updated_on_connection_failure(self):
        handler = _make_handler()
        handler._vlm_pipeline.generate_captions_stream.side_effect = requests.exceptions.Timeout(
            "timeout"
        )

        req_info = _make_req_info()
        handler._request_info_map[req_info.request_id] = req_info

        with patch.dict(os.environ, {"ENABLE_DENSE_CAPTION": ""}):
            handler._trigger_query(req_info)

        handler._metrics.queries_processed.inc.assert_called_once()
        handler._metrics.queries_pending.dec.assert_called_once()


@pytest.mark.unit
@pytest.mark.parametrize(
    "chunks", [[], [{"chunk_responses": []}]], ids=["no-sse-data", "empty-chunks"]
)
def test_file_without_captions_fails_before_persistence(chunks):
    """The nightly's zero-chunk result must not become an empty successful completion."""
    handler = _make_handler()
    handler._args.enable_dev_dc_gen = False
    handler._vlm_pipeline.generate_captions_stream.return_value = iter(chunks)
    req_info = _make_req_info()
    req_info._output_process_thread_pool = ThreadPoolExecutor(max_workers=1)
    handler._request_info_map[req_info.request_id] = req_info

    with patch.dict(os.environ, {"ENABLE_DENSE_CAPTION": ""}):
        handler._trigger_query(req_info)
    req_info._output_process_thread_pool.shutdown(wait=True)

    assert req_info.status == RequestInfo.Status.FAILED
    assert req_info.error_status_code == 502
    assert req_info.error_code == "NoCaptionsGenerated"
    assert req_info.failed_stage == "caption_generation"
    assert req_info.progress == 100
    assert req_info.end_time is not None
    assert req_info.response == []
    handler._vlm_pipeline.generate_captions_stream.assert_called_once()


@pytest.mark.unit
@pytest.mark.parametrize("failure", [requests.exceptions.Timeout("RTVI stream timed out"), None])
def test_partial_rtvi_failure_does_not_write_captions_or_qa(failure):
    """A file stream's early captions must not outlive its terminal failure."""
    from rtvi_vlm_client import RtviError

    handler = _make_handler()
    req_info = _make_req_info()
    req_info._ctx_mgr = MagicMock()
    req_info._qa_ctx_mgr = MagicMock()
    req_info.enable_qa = True
    existing_data = ["existing caption", "existing QA", "another request's caption"]
    req_info._ctx_mgr.add_doc.side_effect = lambda *_a, **_k: existing_data.append(
        "partial caption"
    )
    req_info._qa_ctx_mgr.add_doc.side_effect = lambda *_a, **_k: existing_data.append("partial QA")
    handler._qa_ctx_mgr_pool = []
    handler._request_info_map[req_info.request_id] = req_info
    handler.drop_collection_for_asset = MagicMock()

    def stream(**_kwargs):
        yield {
            "chunk_responses": [
                {
                    "chunk_id": 0,
                    "start_time": 0,
                    "end_time": 10,
                    "content": '{"video_summary": "Partial", "events": []}',
                }
            ]
        }
        raise failure or RtviError(503, "InternalServerError", "RTVI inference failed")

    handler._vlm_pipeline.generate_captions_stream.side_effect = stream
    with patch.dict(
        os.environ, {"ENABLE_DENSE_CAPTION": "", "LVS_DISABLE_DB_RESET_ON_REQUEST_DONE": "false"}
    ):
        handler._trigger_query(req_info)
        handler.check_status_remove_req_id(req_info.request_id)

    assert req_info.status == RequestInfo.Status.FAILED
    assert req_info.failed_stage == "caption_generation"
    assert existing_data == ["existing caption", "existing QA", "another request's caption"]
    req_info._ctx_mgr.add_doc.assert_not_called()
    handler.drop_collection_for_asset.assert_not_called()
    assert req_info._ctx_mgr in handler._ctx_mgr_pool
    assert req_info.request_id not in handler._request_info_map


@pytest.mark.unit
def test_file_captions_are_ingested_in_order_after_rtvi_completes():
    handler = _make_handler()
    req_info = _make_req_info()
    rtvi_complete = False
    ingested = []

    def stream(**_kwargs):
        nonlocal rtvi_complete
        for idx in range(2):
            yield {
                "chunk_responses": [
                    {
                        "chunk_id": idx,
                        "start_time": idx * 10,
                        "end_time": (idx + 1) * 10,
                        "content": str(idx),
                    }
                ]
            }
        rtvi_complete = True

    def ingest(response, _req_info):
        assert rtvi_complete, "file captions were written before RTVI succeeded"
        ingested.append(response.chunk.chunkIdx)

    handler._vlm_pipeline.generate_captions_stream.side_effect = stream
    handler._on_vlm_chunk_response = ingest
    with patch.dict(os.environ, {"ENABLE_DENSE_CAPTION": ""}):
        handler._trigger_query(req_info)

    assert ingested == [0, 1]
    assert req_info.chunk_count == 2


@pytest.mark.unit
def test_provisional_file_captions_use_bounded_memory():
    import tracemalloc

    handler = _make_handler()
    req_info = _make_req_info()
    ingested = []

    def stream(**_kwargs):
        for idx in range(64):
            yield {
                "chunk_responses": [
                    {
                        "chunk_id": idx,
                        "start_time": idx * 10,
                        "end_time": (idx + 1) * 10,
                        "content": str(idx) + "x" * (256 * 1024),
                    }
                ]
            }

    handler._vlm_pipeline.generate_captions_stream.side_effect = stream
    handler._on_vlm_chunk_response = lambda response, _req_info: ingested.append(
        response.chunk.chunkIdx
    )
    with patch.dict(os.environ, {"ENABLE_DENSE_CAPTION": ""}):
        tracemalloc.start()
        try:
            handler._trigger_query(req_info)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

    assert ingested == list(range(64))
    assert peak < 8 * 1024 * 1024, f"provisional captions used {peak} bytes of RAM"


@pytest.mark.unit
def test_provisional_caption_disk_limit_fails_without_partial_writes():
    handler = _make_handler()
    req_info = _make_req_info()
    req_info._ctx_mgr = MagicMock()
    handler._on_vlm_chunk_response = MagicMock()
    handler._vlm_pipeline.generate_captions_stream.return_value = iter(
        [{"chunk_responses": [{"chunk_id": idx, "content": "x" * 2048}]} for idx in range(4)]
    )
    with patch.dict(
        os.environ, {"ENABLE_DENSE_CAPTION": "", "LVS_FILE_CAPTION_STAGING_MAX_BYTES": "4096"}
    ):
        handler._trigger_query(req_info)

    assert req_info.status == RequestInfo.Status.FAILED
    assert req_info.error_status_code == 507
    assert req_info.error_code == "CaptionStagingLimitExceeded"
    assert req_info.failed_stage == "caption_generation"
    handler._on_vlm_chunk_response.assert_not_called()
    req_info._ctx_mgr.add_doc.assert_not_called()
