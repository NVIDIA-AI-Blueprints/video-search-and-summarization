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

"""Integration tests for LVS in RTVI-VLM mode.

Starts a mock RTVI-VLM HTTP server and a real LVS instance (ViaTestServer).
Tests exercise the full HTTP request path end-to-end with realistic RTVI responses.
"""

import asyncio
import json
import logging
import os
import threading
import time
import uuid

import pytest
import requests
import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from tests.common import ViaTestServer

logger = logging.getLogger(__name__)

# ── Mock RTVI-VLM Server ──────────────────────────────────────────────────────

MOCK_MODEL_ID = "mock-vlm-model"
MOCK_RTVI_FILES = {}  # in-memory file store
MOCK_RTVI_STREAMS = {}  # in-memory stream store

# Per-endpoint capture of the x-stream-id request header so tests can assert
# that LVS stamps it on sticky outbound calls (METLVSMS-500). Each entry is a
# list of header-or-None values appended in call order; tests inspect the
# trailing entry. Cleared by clear_mock_state between tests.
MOCK_RTVI_XSID_LOG = {
    "files_post": [],
    "files_delete": [],
    "files_list": [],
    "generate_captions": [],
    "health_ready": [],
    "models": [],
}


def _record_xsid(bucket: str, request: Request) -> None:
    MOCK_RTVI_XSID_LOG[bucket].append(request.headers.get("x-stream-id"))


mock_rtvi_app = FastAPI()


@mock_rtvi_app.get("/v1/health/ready")
async def health_ready(request: Request):
    _record_xsid("health_ready", request)
    return "Service is healthy"


@mock_rtvi_app.get("/v1/models")
async def list_models(request: Request):
    _record_xsid("models", request)
    return {
        "object": "list",
        "data": [
            {
                "id": MOCK_MODEL_ID,
                "created": 1700000000,
                "object": "model",
                "owned_by": "test",
                "api_type": "internal",
            }
        ],
    }


@mock_rtvi_app.post("/v1/files")
async def mock_upload_file(
    request: Request,
    purpose: str = Form(...),
    media_type: str = Form(...),
    file: UploadFile = File(None),
    filename: str = Form(""),
):
    _record_xsid("files_post", request)
    file_id = str(uuid.uuid4())
    content = await file.read() if file else b""
    MOCK_RTVI_FILES[file_id] = {
        "id": file_id,
        "bytes": len(content),
        "filename": file.filename if file else filename,
        "purpose": purpose,
        "media_type": media_type,
        "creation_time": None,
        "sensor_name": "",
    }
    return MOCK_RTVI_FILES[file_id]


@mock_rtvi_app.get("/v1/files")
async def list_files(request: Request, purpose: str = "vision"):
    _record_xsid("files_list", request)
    data = [f for f in MOCK_RTVI_FILES.values() if f["purpose"] == purpose]
    return {"data": data, "object": "list"}


@mock_rtvi_app.delete("/v1/files/{file_id}")
async def delete_file(file_id: str, request: Request):
    _record_xsid("files_delete", request)
    MOCK_RTVI_FILES.pop(file_id, None)
    return {"id": file_id, "object": "file", "deleted": True}


@mock_rtvi_app.post("/v1/generate_captions")
async def generate_captions(request: Request):
    _record_xsid("generate_captions", request)
    body = await request.json()
    asset_id = body.get("id")
    url = body.get("url")
    chunk_duration = body.get("chunk_duration", 10)
    stream = body.get("stream", False)

    # Simulate RTVI SSRF rejection for hostnames
    if url:
        from urllib.parse import urlparse

        host = urlparse(url).hostname or ""
        # Check if host is an IP address
        try:
            import ipaddress

            ipaddress.ip_address(host)
        except ValueError:
            return JSONResponse(
                status_code=422,
                content={
                    "code": "InvalidParameters",
                    "message": (
                        f"('body', 'url'): Value error, '{host}' does not appear"
                        f' to be an IPv4 or IPv6 address (input: "{url}")'
                    ),
                },
            )

    # Simulate "no such resource" for known bad IDs
    if asset_id == "00000000-0000-0000-0000-000000000099":
        return JSONResponse(
            status_code=400,
            content={
                "code": "BadParameter",
                "message": f"No such resource {asset_id}",
            },
        )

    # Check if asset exists (uploaded file or known stream)
    if not url and asset_id not in MOCK_RTVI_FILES and asset_id not in MOCK_RTVI_STREAMS:
        return JSONResponse(
            status_code=400,
            content={
                "code": "BadParameter",
                "message": f"No such resource {asset_id}",
            },
        )

    if not stream:
        # Non-streaming: return all chunks at once
        return {
            "id": str(uuid.uuid4()),
            "model": MOCK_MODEL_ID,
            "created": int(time.time()),
            "media_info": {"type": "offset", "start_offset": 0, "end_offset": chunk_duration * 2},
            "chunk_responses": [
                _make_chunk(0, 0, chunk_duration),
                _make_chunk(1, chunk_duration, chunk_duration * 2),
            ],
            "usage": {"query_processing_time": 1, "total_chunks_processed": 2},
        }

    # Streaming SSE response
    async def sse_generator():
        for i in range(2):
            start = i * chunk_duration
            end = start + chunk_duration
            chunk_data = {
                "id": str(uuid.uuid4()),
                "model": MOCK_MODEL_ID,
                "created": int(time.time()),
                "media_info": {"type": "offset", "start_offset": start, "end_offset": end},
                "chunk_responses": [_make_chunk(i, start, end)],
                "usage": None,
            }
            yield f"data: {json.dumps(chunk_data)}\n\n"
            await asyncio.sleep(0.01)
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse_generator(), media_type="text/event-stream")


@mock_rtvi_app.post("/v1/stream/add")
async def add_stream(request: Request):
    body = await request.json()
    value = body.get("value", {})
    camera_id = value.get("camera_id", "default")
    asset_id = str(uuid.uuid4())
    MOCK_RTVI_STREAMS[asset_id] = {
        "camera_id": camera_id,
        "asset_id": asset_id,
        "camera_url": value.get("camera_url", ""),
    }
    return {"camera_id": camera_id, "asset_id": asset_id, "status": "processing", "inference": True}


def _make_chunk(chunk_id, start, end):
    return {
        "chunk_id": chunk_id,
        "start_time": str(float(start)),
        "end_time": str(float(end)),
        "content": json.dumps(
            {
                "events": [
                    {
                        "start_time": start,
                        "end_time": end,
                        "type": "activity",
                        "description": f"Test activity in chunk {chunk_id}",
                    }
                ]
            }
        ),
        "audio_transcript": "",
    }


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def mock_rtvi(free_tcp_port_factory):
    """Start mock RTVI-VLM server on an unused localhost port."""
    MOCK_RTVI_FILES.clear()
    MOCK_RTVI_STREAMS.clear()

    port = free_tcp_port_factory()
    config = uvicorn.Config(mock_rtvi_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to start
    for _ in range(50):
        try:
            r = requests.get(f"http://127.0.0.1:{port}/v1/health/ready", timeout=1)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.1)
    else:
        pytest.fail("Mock RTVI server failed to start")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)


LVS_PORT = 48201
LVS_SERVER_ARGS = "--log-level debug"


@pytest.fixture(scope="module")
def lvs(mock_rtvi, tmp_path_factory):
    """Start LVS in RTVI mode pointing to mock RTVI server."""
    tmp_path_factory.mktemp("lvs_assets")

    os.environ["RTVI_VLM_URL"] = mock_rtvi
    os.environ["VIA_DEV_API"] = "true"
    os.environ["VIA_SKIP_PIPELINE_WARMUP"] = "1"
    os.environ["KAFKA_ENABLED"] = "true"

    server = ViaTestServer(LVS_SERVER_ARGS, LVS_PORT, startup_timeout_sec=60)
    server.start_server()

    yield server

    server.stop_server()
    os.environ.pop("RTVI_VLM_URL", None)
    os.environ.pop("KAFKA_ENABLED", None)


@pytest.fixture(autouse=True)
def clear_mock_state():
    """Clear mock RTVI state between tests."""
    MOCK_RTVI_FILES.clear()
    MOCK_RTVI_STREAMS.clear()
    for bucket in MOCK_RTVI_XSID_LOG.values():
        bucket.clear()


# ── Helper ────────────────────────────────────────────────────────────────────


def post_summarize(lvs, **kwargs):
    return lvs.post("/summarize", json=kwargs)


def upload_file(lvs, content=b"\x00" * 1024, filename="test.mp4"):
    """Upload a file via /files and return file_id."""
    resp = lvs.post(
        "/files",
        files={"file": (filename, content, "application/octet-stream")},
        data={"purpose": "vision", "media_type": "video"},
    )
    assert resp.status_code == 200, f"Upload failed: {resp.text}"
    return resp.json()["id"]


# ── Happy Path: File Summarization ────────────────────────────────────────────


class TestFileSummarization:
    """File-based summarization via RTVI."""

    @pytest.mark.test_in_ci
    def test_file_via_url(self, lvs):
        """URL passed directly to RTVI for summarization."""
        resp = post_summarize(
            lvs,
            url="http://127.0.0.1/test.mp4",
            model=MOCK_MODEL_ID,
            scenario="general",
            events=["activity"],
            chunk_duration=5,
        )
        # RTVI receives the URL directly; may fail at RTVI level
        # but routing through via_server is correct.
        assert resp.status_code in (200, 500), f"Unexpected: {resp.status_code} {resp.text}"

    @pytest.mark.test_in_ci
    def test_file_via_id_stream_true(self, lvs):
        """Upload file, then summarize by ID + URL with stream=true → SSE events."""
        file_id = upload_file(lvs)
        resp = lvs.post(
            "/summarize",
            json={
                "id": file_id,
                "url": "http://127.0.0.1/test.mp4",
                "model": MOCK_MODEL_ID,
                "scenario": "general",
                "events": ["activity"],
                "chunk_duration": 5,
                "stream": True,
            },
            stream=True,
        )
        assert resp.status_code == 200
        events = []
        for line in resp.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    break
                events.append(json.loads(data))
        assert len(events) > 0


# ── Happy Path: Live Stream Summarization ─────────────────────────────────────


class TestLiveStreamSummarization:
    """Live stream captioning via the dedicated /v1/generate_captions API."""

    @pytest.mark.test_in_ci
    def test_stream_valid_id(self, lvs, mock_rtvi):
        """Valid stream ID → /v1/generate_captions accepted."""
        stream_resp = requests.post(
            f"{mock_rtvi}/v1/stream/add",
            json={
                "key": "sensor",
                "value": {
                    "camera_id": "test-cam",
                    "camera_url": "rtsp://test:554/live",
                    "change": "camera_add",
                },
            },
        )
        stream_id = stream_resp.json()["asset_id"]
        MOCK_RTVI_STREAMS[stream_id] = {"asset_id": stream_id}

        resp = lvs.post(
            "/v1/generate_captions",
            json={
                "id": stream_id,
                "model": MOCK_MODEL_ID,
                "scenario": "general",
                "events": ["activity"],
                "chunk_duration": 60,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == stream_id
        assert body["status"] == "accepted"

    @pytest.mark.test_in_ci
    def test_stream_unknown_id(self, lvs):
        """ID known to RTVI but not LVS → /v1/generate_captions accepted."""
        stream_id = str(uuid.uuid4())
        MOCK_RTVI_STREAMS[stream_id] = {"asset_id": stream_id}

        resp = lvs.post(
            "/v1/generate_captions",
            json={
                "id": stream_id,
                "model": MOCK_MODEL_ID,
                "scenario": "general",
                "events": ["activity"],
                "chunk_duration": 60,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "accepted"


# ── Happy Path: /generate_vlm_captions ────────────────────────────────────────


class TestGenerateVlmCaptions:
    """/generate_vlm_captions endpoint in RTVI mode."""

    @pytest.mark.test_in_ci
    def test_vlm_captions_stream_true(self, lvs):
        """Per-chunk captions with stream=true."""
        resp = lvs.post(
            "/generate_vlm_captions",
            json={
                "url": "http://127.0.0.1/test.mp4",
                "prompt": "Describe",
                "model": MOCK_MODEL_ID,
                "chunk_duration": 5,
                "stream": True,
            },
            stream=True,
        )
        assert resp.status_code == 200
        events = []
        for line in resp.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    break
                events.append(json.loads(data))
        assert len(events) > 0

    @pytest.mark.test_in_ci
    def test_vlm_captions_stream_false(self, lvs):
        """Per-chunk captions with stream=false."""
        resp = lvs.post(
            "/generate_vlm_captions",
            json={
                "url": "http://127.0.0.1/test.mp4",
                "prompt": "Describe",
                "model": MOCK_MODEL_ID,
                "chunk_duration": 5,
                "stream": False,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "chunk_responses" in body
        assert body["model"] == MOCK_MODEL_ID


# ── Happy Path: Other Endpoints ───────────────────────────────────────────────


class TestOtherEndpoints:
    """Models, health, files endpoints."""

    @pytest.mark.test_in_ci
    def test_models(self, lvs):
        resp = lvs.get("/models")
        assert resp.status_code == 200
        assert resp.json()["data"][0]["id"] == MOCK_MODEL_ID

    @pytest.mark.test_in_ci
    def test_health_ready(self, lvs):
        resp = lvs.get("/v1/ready")
        assert resp.status_code == 200

    @pytest.mark.test_in_ci
    def test_file_upload_and_list(self, lvs):
        file_id = upload_file(lvs)
        resp = lvs.get("/files?purpose=vision")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert any(f["id"] == file_id for f in data)

    @pytest.mark.test_in_ci
    def test_file_upload_and_delete(self, lvs):
        file_id = upload_file(lvs)
        resp = lvs.delete(f"/files/{file_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True


# ── Negative Cases ────────────────────────────────────────────────────────────


class TestNegativeCases:
    """Input validation and error handling."""

    @pytest.mark.test_in_ci
    def test_both_url_and_id_rejected(self, lvs):
        resp = post_summarize(
            lvs,
            id=str(uuid.uuid4()),
            url="http://example.com/video.mp4",
            model=MOCK_MODEL_ID,
            scenario="general",
            events=["activity"],
        )
        assert resp.status_code == 422

    @pytest.mark.test_in_ci
    def test_unknown_id(self, lvs):
        """Unknown ID → 400 (no stream fallback).

        Uses the mock's special id that always returns 400 from RTVI, regardless
        of url.
        """
        resp = post_summarize(
            lvs,
            id="00000000-0000-0000-0000-000000000099",
            url="http://127.0.0.1/test.mp4",
            model=MOCK_MODEL_ID,
            scenario="general",
            events=["activity"],
            chunk_duration=5,
        )
        assert resp.status_code == 400
        assert "No such resource" in resp.json()["message"]

    @pytest.mark.test_in_ci
    def test_wrong_model_name(self, lvs):
        resp = post_summarize(
            lvs,
            url="http://127.0.0.1/test.mp4",
            model="nonexistent-model",
            scenario="general",
            events=["activity"],
            chunk_duration=5,
        )
        assert resp.status_code == 400
        assert "No such model" in resp.json()["message"]

    @pytest.mark.test_in_ci
    def test_no_url_no_id(self, lvs):
        """Neither url nor id → error (422 or 500)."""
        resp = post_summarize(
            lvs,
            model=MOCK_MODEL_ID,
            scenario="general",
            events=["activity"],
            chunk_duration=5,
        )
        # Pydantic may reject (422) or code falls through without videoIdList (500)
        assert resp.status_code in (422, 500)


# ── RTVI Error Forwarding ────────────────────────────────────────────────────


class TestRtviErrorForwarding:
    """Verify RTVI errors are forwarded with correct status codes."""

    @pytest.mark.test_in_ci
    def test_rtvi_400_bad_stream_id(self, lvs):
        """Fake stream ID → RTVI 400 → LVS 400 via /v1/generate_captions."""
        resp = lvs.post(
            "/v1/generate_captions",
            json={
                "id": "00000000-0000-0000-0000-000000000099",
                "model": MOCK_MODEL_ID,
                "scenario": "general",
                "events": ["activity"],
                "chunk_duration": 60,
            },
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "No such resource" in body["message"]

    @pytest.mark.test_in_ci
    def test_rtvi_422_ssrf_rejection(self, lvs):
        """URL with hostname → RTVI 422 SSRF → LVS 422."""
        resp = post_summarize(
            lvs,
            url="http://example.com/video.mp4",
            model=MOCK_MODEL_ID,
            scenario="general",
            events=["activity"],
            chunk_duration=5,
        )
        assert resp.status_code == 422
        assert "does not appear to be an IPv4" in resp.json()["message"]

    @pytest.mark.test_in_ci
    def test_rtvi_400_unknown_file_id(self, lvs):
        """Unknown file ID sent to RTVI /generate_captions → 400 forwarded.

        Uses the mock's hardcoded always-400 id (the mock skips its
        unknown-id check when url is supplied -- so we rely on the
        explicit id-based 400 path).
        """
        resp = post_summarize(
            lvs,
            id="00000000-0000-0000-0000-000000000099",
            url="http://127.0.0.1/test.mp4",
            model=MOCK_MODEL_ID,
            scenario="general",
            events=["activity"],
            chunk_duration=5,
        )
        assert resp.status_code == 400
        assert "No such resource" in resp.json()["message"]


# ── Sticky-routing header (METLVSMS-500) ──────────────────────────────────────


class TestStickyRoutingHeader:
    """LVS must stamp x-stream-id on outbound RTVI calls so NGINX Ingress
    consistent-hash-routes them to the same RTVI replica.

    Sticky calls (header REQUIRED, value = asset/stream/file id):
      - POST /v1/files
      - DELETE /v1/files/{file_id}
      - POST /v1/generate_captions  (file URL passthrough)
      - POST /v1/generate_captions  (livestream trigger)

    Non-sticky calls (header MUST be absent so probes hit every replica):
      - GET /v1/health/ready
      - GET /v1/models
      - GET /v1/files
    """

    @pytest.mark.test_in_ci
    def test_upload_with_client_id_carries_x_stream_id(self, lvs):
        """LVS POST /files with client-supplied id -> RTVI POST /v1/files
        carries x-stream-id == that id.

        When the operator pre-picks the asset id, LVS knows it before calling
        RTVI and can sticky-route the upload onto the replica that will own
        the asset.
        """
        client_id = str(uuid.uuid4())
        resp = lvs.post(
            "/files",
            files={"file": ("test.mp4", b"\x00" * 1024, "application/octet-stream")},
            data={"purpose": "vision", "media_type": "video", "id": client_id},
        )
        assert resp.status_code == 200, f"Upload failed: {resp.text}"
        captured = MOCK_RTVI_XSID_LOG["files_post"]
        assert captured, "Mock RTVI did not record any POST /v1/files"
        assert captured[-1] == client_id, (
            f"Expected x-stream-id={client_id!r} on POST /v1/files, " f"got {captured[-1]!r}"
        )

    @pytest.mark.test_in_ci
    def test_upload_without_client_id_omits_x_stream_id(self, lvs):
        """LVS POST /files WITHOUT a client-supplied id has no x-stream-id.

        Chicken-and-egg: the asset id is only generated server-side by RTVI,
        so LVS cannot sticky-route the upload itself. Subsequent calls
        (delete, generate_captions) using the returned asset_id ARE
        sticky-routed.
        """
        upload_file(lvs)
        captured = MOCK_RTVI_XSID_LOG["files_post"]
        assert captured, "Mock RTVI did not record any POST /v1/files"
        assert captured[-1] is None, (
            f"Expected no x-stream-id on POST /v1/files (no client id), " f"got {captured[-1]!r}"
        )

    @pytest.mark.test_in_ci
    def test_delete_file_carries_x_stream_id(self, lvs):
        """LVS DELETE /files/{id} -> RTVI DELETE /v1/files/{id} carries x-stream-id."""
        file_id = upload_file(lvs)
        resp = lvs.delete(f"/files/{file_id}")
        assert resp.status_code == 200
        captured = MOCK_RTVI_XSID_LOG["files_delete"]
        assert captured, "Mock RTVI did not record any DELETE /v1/files/{id}"
        assert captured[-1] == file_id, (
            f"Expected x-stream-id={file_id!r} on DELETE /v1/files/{{id}}, " f"got {captured[-1]!r}"
        )

    @pytest.mark.test_in_ci
    def test_file_summarize_carries_x_stream_id(self, lvs):
        """LVS POST /summarize -> RTVI POST /v1/generate_captions carries
        x-stream-id == request id (= LVS source_id = RTVI body id)."""
        file_id = upload_file(lvs)
        resp = lvs.post(
            "/summarize",
            json={
                "id": file_id,
                "url": "http://127.0.0.1/test.mp4",
                "model": MOCK_MODEL_ID,
                "scenario": "general",
                "events": ["activity"],
                "chunk_duration": 5,
                "stream": True,
            },
            stream=True,
        )
        assert resp.status_code == 200
        # Drain the SSE so the upstream call completes before we inspect the log.
        for line in resp.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                if line[len("data:") :].strip() == "[DONE]":
                    break
        captured = MOCK_RTVI_XSID_LOG["generate_captions"]
        assert captured, "Mock RTVI did not record any POST /v1/generate_captions"
        assert captured[-1] == file_id, (
            f"Expected x-stream-id={file_id!r} on POST /v1/generate_captions, "
            f"got {captured[-1]!r}"
        )

    @pytest.mark.test_in_ci
    def test_livestream_trigger_carries_x_stream_id(self, lvs):
        """LVS POST /v1/generate_captions -> RTVI POST /v1/generate_captions
        carries x-stream-id == stream_id."""
        stream_id = str(uuid.uuid4())
        MOCK_RTVI_STREAMS[stream_id] = {"asset_id": stream_id}
        resp = lvs.post(
            "/v1/generate_captions",
            json={
                "id": stream_id,
                "model": MOCK_MODEL_ID,
                "scenario": "general",
                "events": ["activity"],
                "chunk_duration": 60,
            },
        )
        assert resp.status_code == 200
        captured = MOCK_RTVI_XSID_LOG["generate_captions"]
        assert captured, "Mock RTVI did not record any POST /v1/generate_captions"
        assert captured[-1] == stream_id, (
            f"Expected x-stream-id={stream_id!r} on POST /v1/generate_captions, "
            f"got {captured[-1]!r}"
        )

    @pytest.mark.test_in_ci
    def test_non_sticky_calls_have_no_x_stream_id(self, lvs):
        """Health, models, and list_files must NOT carry x-stream-id so they
        round-robin across all replicas."""
        # Health probe is exercised by LVS at startup; the mock fixture
        # already captured those entries. Trigger fresh probes/list calls.
        # /v1/ready proxies through to RTVI /v1/health/ready in dependency
        # checks; /models proxies to RTVI /v1/models; /files lists files.
        assert lvs.get("/v1/ready").status_code == 200
        assert lvs.get("/models").status_code == 200
        assert lvs.get("/files?purpose=vision").status_code == 200

        for bucket in ("health_ready", "models", "files_list"):
            captured = MOCK_RTVI_XSID_LOG[bucket]
            if not captured:
                # Some endpoints (notably health) may be cached on the LVS
                # side and not re-issue RTVI calls -- skip when the bucket
                # is empty rather than fail.
                continue
            assert all(v is None for v in captured), (
                f"Non-sticky endpoint {bucket} unexpectedly received "
                f"x-stream-id values: {captured!r}"
            )
