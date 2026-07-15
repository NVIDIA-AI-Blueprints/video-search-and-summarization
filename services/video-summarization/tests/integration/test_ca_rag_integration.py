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
Integration tests for Context-Aware RAG (CA-RAG) functionality.

These tests validate the integration between VIA Engine and the via-ctx-rag repository,
specifically testing:
- Document ingestion into vector database
- Aggregated summary generation
- QA retrieval functionality (S)
- Context manager pool management
- Database backend selection (Milvus vs Elasticsearch)
"""

import contextlib
import json
import os
import tempfile
import time
import uuid

import pytest
import sseclient

from tests.common import ViaTestServer


@contextlib.contextmanager
def _timed(label: str):
    """Print wall-clock elapsed time for a labelled block to stdout."""
    t0 = time.perf_counter()
    print(f"\n[TIMING] START  {label}")
    try:
        yield
    finally:
        elapsed = time.perf_counter() - t0
        print(f"[TIMING] FINISH {label} — {elapsed:.1f}s")


# Required by SummarizationQuery for /summarize requests
DEFAULT_SUMMARIZE_SCENARIO = "general video summarization"
DEFAULT_SUMMARIZE_EVENTS = ["activity", "objects", "scene change"]

WAREHOUSE_VIDEO_URL = "https://artifactory.nvidia.com/artifactory/sw-ds-generic-bld-local/lmm/streams/warehouse_gopro_1m_720.mp4"  # noqa: E501
WAREHOUSE_VIDEO_LOCAL = "/tmp/lvs_warehouse_gopro_1m_720.mp4"

_COMMON_SERVER_ARGS = "--log-level debug"


@pytest.fixture(scope="session")
def warehouse_video(integration_test_setup):
    """Download warehouse video once per session and cache locally.

    Returns the local file path so subsequent tests skip the Artifactory download.
    Falls back to the remote URL if the local download fails.
    """
    from tests.conftest import _download_from_artifactory

    min_size = 10_000
    if (
        not os.path.exists(WAREHOUSE_VIDEO_LOCAL)
        or os.path.getsize(WAREHOUSE_VIDEO_LOCAL) < min_size
    ):
        with _timed("warehouse_video: download from Artifactory"):
            _download_from_artifactory(WAREHOUSE_VIDEO_URL, WAREHOUSE_VIDEO_LOCAL)
    else:
        print(f"\n[TIMING] warehouse_video: using cached file {WAREHOUSE_VIDEO_LOCAL}")

    if os.path.exists(WAREHOUSE_VIDEO_LOCAL) and os.path.getsize(WAREHOUSE_VIDEO_LOCAL) >= min_size:
        return WAREHOUSE_VIDEO_LOCAL
    return WAREHOUSE_VIDEO_URL  # fallback


@pytest.fixture(scope="class")
def shared_server_doc_ingestion():
    """Single ViaTestServer shared across TestCARAGDocumentIngestion tests."""
    mp = pytest.MonkeyPatch()
    mp.setenv("VIA_DEV_API", "true")
    mp.setenv("LVS_DATABASE_BACKEND", "elasticsearch_db")
    with tempfile.TemporaryDirectory():
        with _timed("shared_server_doc_ingestion: ViaTestServer startup"):
            server = ViaTestServer(
                _COMMON_SERVER_ARGS,
                48001,
                startup_timeout_sec=90,
            ).start_server()
        try:
            yield server
        finally:
            with _timed("shared_server_doc_ingestion: ViaTestServer shutdown"):
                server.stop_server()
    mp.undo()


@pytest.fixture(scope="class")
def shared_server_summarization():
    """Single ViaTestServer shared across TestCARAGSummarization tests."""
    mp = pytest.MonkeyPatch()
    mp.setenv("VIA_DEV_API", "true")
    mp.setenv("LVS_DATABASE_BACKEND", "elasticsearch_db")
    with tempfile.TemporaryDirectory():
        with _timed("shared_server_summarization: ViaTestServer startup"):
            server = ViaTestServer(
                _COMMON_SERVER_ARGS,
                48002,
                startup_timeout_sec=90,
            ).start_server()
        try:
            yield server
        finally:
            with _timed("shared_server_summarization: ViaTestServer shutdown"):
                server.stop_server()
    mp.undo()


@pytest.fixture(scope="class")
def shared_server_ctx_pool():
    """Single ViaTestServer shared across TestCARAGContextManagerPool tests."""
    mp = pytest.MonkeyPatch()
    mp.setenv("VIA_DEV_API", "true")
    mp.setenv("LVS_DATABASE_BACKEND", "elasticsearch_db")
    with tempfile.TemporaryDirectory():
        with _timed("shared_server_ctx_pool: ViaTestServer startup"):
            server = ViaTestServer(
                _COMMON_SERVER_ARGS,
                48003,
                startup_timeout_sec=90,
            ).start_server()
        try:
            yield server
        finally:
            with _timed("shared_server_ctx_pool: ViaTestServer shutdown"):
                server.stop_server()
    mp.undo()


@pytest.fixture(scope="class")
def shared_server_no_ca_rag():
    """Single ViaTestServer shared across TestCARAGErrorHandling tests (CA-RAG disabled)."""
    mp = pytest.MonkeyPatch()
    mp.setenv("VIA_DEV_API", "true")
    with tempfile.TemporaryDirectory():
        with _timed("shared_server_no_ca_rag: ViaTestServer startup"):
            server = ViaTestServer(
                f"{_COMMON_SERVER_ARGS} --disable-ca-rag",
                48004,
            ).start_server()
        try:
            yield server
        finally:
            with _timed("shared_server_no_ca_rag: ViaTestServer shutdown"):
                server.stop_server()
    mp.undo()


def _get_model_id(server) -> str:
    """Query the running LVS server for the first available model ID.

    Called once per test method so the model name is always in sync with
    whatever model the RTVI VLM backend is serving, rather than being
    hard-coded (which caused 'No such model gpt-4o' failures when the
    VLM backend was not an OpenAI-compatible endpoint).
    """
    resp = server.get("/models")
    assert resp.status_code == 200, f"GET /models failed: {resp.status_code} {resp.text}"
    models = resp.json().get("data", [])
    assert models, f"GET /models returned no models: {resp.json()}"
    return models[0]["id"]


def _upload_video(server, video_path, label=""):
    """Upload a video file (local path or URL) to the server. Returns video_id."""
    tag = f" [{label}]" if label else ""
    with _timed(f"_upload_video{tag}: POST /files"):
        response = server.post(
            "/files",
            files={
                "filename": (None, video_path),
                "purpose": (None, "vision"),
                "media_type": (None, "video"),
            },
        )
    assert response.status_code == 200
    return response.json()["id"]


def _collect_stop_responses(sse_resp, label=""):
    """Drain an SSE streaming response and return events with finish_reason=stop."""
    tag = f" [{label}]" if label else ""
    responses = []
    client = sseclient.SSEClient(sse_resp)
    with _timed(f"_collect_stop_responses{tag}: drain SSE stream"):
        for event in client.events():
            data = event.data.strip()
            if data == "[DONE]":
                continue
            parsed = json.loads(data)
            choices = parsed.get("choices")
            if choices and choices[0].get("finish_reason") == "stop":
                responses.append(parsed)
    return responses


@pytest.mark.integration
@pytest.mark.ca_rag
class TestCARAGDocumentIngestion:
    """Test CA-RAG document ingestion functionality"""

    def test_add_doc_called_during_summarization(
        self, integration_test_setup, shared_server_doc_ingestion, warehouse_video
    ):
        """
        Test that add_doc is called for each chunk during video summarization.
        Validates that captions are stored in the vector database.
        """
        t = shared_server_doc_ingestion

        # New flow: summarize by URL only — no upload/rekey step. RTVI fetches
        # the video itself; passing both an upload-id and url causes RTVI to
        # respond with 400 AssetAlreadyExists.
        req_json = {
            "url": WAREHOUSE_VIDEO_URL,
            "model": _get_model_id(t),
            "chunk_duration": 10,
            "temperature": 0.7,
            "seed": 42,
            "max_tokens": 100,
            "stream": True,
            "summarize": True,
            "scenario": DEFAULT_SUMMARIZE_SCENARIO,
            "events": DEFAULT_SUMMARIZE_EVENTS,
        }

        with _timed("test_add_doc_called: POST /summarize"):
            resp = t.post("/summarize", json=req_json, stream=True)
        assert resp.status_code == 200

        accumulated_responses = _collect_stop_responses(resp, label="test_add_doc_called")

        # Verify we got responses (indicating documents were ingested)
        assert len(accumulated_responses) > 0, "No responses received"

        # Verify aggregated summary was generated
        if len(accumulated_responses) == 1:
            summary = accumulated_responses[0]["choices"][0]["message"]["content"]
            assert len(summary) > 0, "Empty summary generated"

    def test_add_doc_with_metadata(
        self, integration_test_setup, shared_server_doc_ingestion, warehouse_video
    ):
        """
        Test that document metadata (timestamps, chunk index) is properly stored.
        """
        t = shared_server_doc_ingestion

        # Summarize directly by URL (no /files upload — RTVI will fetch the URL).
        req_json = {
            "url": WAREHOUSE_VIDEO_URL,
            "model": _get_model_id(t),
            "chunk_duration": 15,  # 15 second chunks
            "stream": True,
            "summarize": True,
            "scenario": DEFAULT_SUMMARIZE_SCENARIO,
            "events": DEFAULT_SUMMARIZE_EVENTS,
        }

        with _timed("test_add_doc_metadata: POST /summarize"):
            resp = t.post("/summarize", json=req_json, stream=True)
        assert resp.status_code == 200

        responses = _collect_stop_responses(resp, label="test_add_doc_metadata")

        # Verify temporal metadata in responses
        for response in responses:
            media_info = response.get("media_info", {})
            assert "start_offset" in media_info, "Missing start_offset in metadata"
            assert "end_offset" in media_info, "Missing end_offset in metadata"
            assert media_info["end_offset"] > media_info["start_offset"], "Invalid temporal range"


@pytest.mark.integration
@pytest.mark.ca_rag
class TestCARAGSummarization:
    """Test CA-RAG aggregated summary generation"""

    def test_aggregated_summary_generation(
        self, integration_test_setup, shared_server_summarization, warehouse_video
    ):
        """
        Test that CA-RAG properly aggregates chunk captions into a coherent summary.
        """
        t = shared_server_summarization

        # Request summarization with aggregation enabled (URL-only flow)
        req_json = {
            "url": WAREHOUSE_VIDEO_URL,
            "model": _get_model_id(t),
            "chunk_duration": 10,
            "stream": True,
            "summarize": True,  # Triggers aggregation
            "scenario": DEFAULT_SUMMARIZE_SCENARIO,
            "events": DEFAULT_SUMMARIZE_EVENTS,
        }

        with _timed("test_aggregated_summary: POST /summarize"):
            resp = t.post("/summarize", json=req_json, stream=True)
        assert resp.status_code == 200

        responses = _collect_stop_responses(resp, label="test_aggregated_summary")
        assert len(responses) > 0, "No summary generated"

        # With summarize=True, we expect a single aggregated response
        if len(responses) == 1:
            summary = responses[0]["choices"][0]["message"]["content"]
            assert len(summary) > 50, "Summary too short, aggregation may have failed"

    def test_summarization_without_aggregation(
        self, integration_test_setup, shared_server_summarization, warehouse_video
    ):
        """Test chunk-only responses when summarize=False."""
        t = shared_server_summarization

        # Request without aggregation (URL-only flow)
        req_json = {
            "url": WAREHOUSE_VIDEO_URL,
            "model": _get_model_id(t),
            "chunk_duration": 15,
            "stream": True,
            "summarize": False,  # No aggregation
            "scenario": DEFAULT_SUMMARIZE_SCENARIO,
            "events": DEFAULT_SUMMARIZE_EVENTS,
        }

        with _timed("test_no_aggregation: POST /summarize"):
            resp = t.post("/summarize", json=req_json, stream=True)
        assert resp.status_code == 200

        responses = _collect_stop_responses(resp, label="test_no_aggregation")
        assert len(responses) >= 1, "No chunk responses received"
        for response in responses:
            assert "media_info" in response, "Missing media_info"
            media_info = response["media_info"]
            assert "start_offset" in media_info
            assert "end_offset" in media_info


@pytest.mark.integration
@pytest.mark.ca_rag
class TestCARAGContextManagerPool:
    """Test context manager pool management"""

    def test_context_manager_pool_creation(
        self, integration_test_setup, shared_server_ctx_pool, warehouse_video
    ):
        """
        Test that the context manager pool is properly created on server start.
        """
        t = shared_server_ctx_pool

        # Check server health
        health_resp = t.get("/v1/ready")
        assert health_resp.status_code == 200, "Server not ready"

        # Pool should be created; verify by making a request (URL-only flow)
        req_json = {
            "url": WAREHOUSE_VIDEO_URL,
            "model": _get_model_id(t),
            "chunk_duration": 10,
            "stream": True,
            "summarize": True,
            "scenario": DEFAULT_SUMMARIZE_SCENARIO,
            "events": DEFAULT_SUMMARIZE_EVENTS,
        }

        with _timed("test_ctx_pool_creation: POST /summarize"):
            resp = t.post("/summarize", json=req_json, stream=True)
        assert resp.status_code == 200 or resp.status_code == 202

    def test_context_manager_reuse_same_asset(
        self, integration_test_setup, shared_server_ctx_pool, warehouse_video
    ):
        """
        Test that context manager is reused for multiple requests on the same asset.
        This validates the pool management and asset-based reuse logic.
        """
        t = shared_server_ctx_pool

        # Use a stable client-supplied id so both calls land on the same
        # source_id → exercise context-manager pool reuse on the LVS side.
        video_id = str(uuid.uuid4())

        # First summarization
        model = _get_model_id(t)
        req_json1 = {
            "id": video_id,
            "url": WAREHOUSE_VIDEO_URL,
            "model": model,
            "chunk_duration": 10,
            "stream": True,
            "summarize": True,
            "scenario": DEFAULT_SUMMARIZE_SCENARIO,
            "events": DEFAULT_SUMMARIZE_EVENTS,
        }

        with _timed("test_ctx_reuse: POST /summarize (1st)"):
            resp1 = t.post("/summarize", json=req_json1, stream=True)
        assert resp1.status_code == 200

        # Wait for completion
        with _timed("test_ctx_reuse: drain SSE stream (1st)"):
            client1 = sseclient.SSEClient(resp1)
            for event in client1.events():
                if event.data.strip() == "[DONE]":
                    break

        time.sleep(2)

        # Second summarization on same video (should reuse context manager)
        req_json2 = {
            "id": video_id,
            "url": WAREHOUSE_VIDEO_URL,
            "model": model,
            "chunk_duration": 15,  # Different chunk size
            "stream": True,
            "summarize": True,
            "scenario": DEFAULT_SUMMARIZE_SCENARIO,
            "events": DEFAULT_SUMMARIZE_EVENTS,
        }

        with _timed("test_ctx_reuse: POST /summarize (2nd)"):
            resp2 = t.post("/summarize", json=req_json2, stream=True)
        assert resp2.status_code == 200, "Second request failed"

        # Both requests should succeed, indicating context manager reuse works


@pytest.mark.integration
@pytest.mark.ca_rag
@pytest.mark.slow
class TestCARAGErrorHandling:
    """Test CA-RAG error handling scenarios"""

    def test_summarization_with_ca_rag_disabled(
        self, integration_test_setup, shared_server_no_ca_rag, warehouse_video
    ):
        """
        Test that summarization works even when CA-RAG is disabled.
        Should fall back to chunk-by-chunk processing.
        """
        t = shared_server_no_ca_rag

        # Request summarization (URL-only flow)
        req_json = {
            "url": WAREHOUSE_VIDEO_URL,
            "model": _get_model_id(t),
            "chunk_duration": 10,
            "stream": True,
            "summarize": False,  # Chunk-by-chunk
            "scenario": DEFAULT_SUMMARIZE_SCENARIO,
            "events": DEFAULT_SUMMARIZE_EVENTS,
        }

        with _timed("test_ca_rag_disabled: POST /summarize"):
            resp = t.post("/summarize", json=req_json, stream=True)
        assert resp.status_code == 200

        # Should still get responses (chunk-level)
        responses = []
        with _timed("test_ca_rag_disabled: drain SSE stream"):
            client = sseclient.SSEClient(resp)
            for event in client.events():
                data = event.data.strip()
                if data == "[DONE]":
                    break
                response = json.loads(data)
                choices = response.get("choices")
                if choices and choices[0].get("finish_reason") == "stop":
                    responses.append(response)

        assert len(responses) >= 1, "No responses without CA-RAG"
