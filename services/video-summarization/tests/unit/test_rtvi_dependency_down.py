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
