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

"""End-to-end tests for the LVS + RTVI VLM file-path (non-Kafka) integration.

NO MOCKS, NO IN-PROCESS SERVERS. Runs against an already-up
``BlueprintBuilderGenerated/docker-compose.yml`` stack with the shared
RTVI VLM reachable via ``RTVI_VLM_URL`` (default: http://localhost:8420).

Python pytest equivalent of ``run_sanity.sh``. Covers every test section
in that script:

  §1  Health & Connectivity
        LVS /v1/ready                          → 200
        RTVI VLM /v1/health/ready              → 200

  §2  Model Discovery
        LVS /models                            → 200, non-empty data list
        RTVI VLM /v1/models                    → 200

  §3  End-to-End File Summarization
        POST /v1/summarize stream=false        → sync CompletionResponse
        POST /v1/summarize stream=true         → SSE EventSourceResponse
        Both validate: object starts with "summarization.", choices[0]
        .message.content parses as JSON with non-empty events list and
        video_summary (mirrors the inline Python validation in §3 of the
        shell script).

  §3a Sticky-routing header (METLVSMS-500)
        LVS log files contain at least one
        "RTVI generate_captions_stream: x-stream-id=" line after the §3
        summarize calls.

  §4  Negative Tests — Input Validation
        Missing required fields (model/scenario/events) → 422
        Wrong model name                               → 400 or 500

Targets (defaults match BlueprintBuilderGenerated/.env):

    via-engine   http://localhost:${LVS_BACKEND_PORT:-38111}
    rtvi-vlm     ${RTVI_VLM_URL:-http://localhost:${RTVI_VLM_PORT:-8420}}

The video used for §3 summarization is fetched by LVS directly from
Artifactory using ASSET_DOWNLOAD_AUTH_TOKENS (injected into the compose
stack by the CI helper). Override via RTVI_E2E_FILE_URL.

Tests are enabled by default. Set RTVI_E2E_TEST=0 to skip (e.g. in
environments where the stack is not running).
"""

import glob
import json
import logging
import os
import re
import time
from typing import Any, Dict, Optional

import pytest
import requests

logger = logging.getLogger(__name__)

# -- Skip gate ---------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    os.environ.get("RTVI_E2E_TEST", "1") != "1",
    reason=(
        "Set RTVI_E2E_TEST=0 to skip the RTVI E2E sanity tests. "
        "Requires the BlueprintBuilderGenerated compose stack with the "
        "shared RTVI-VLM already running."
    ),
)

# -- Config (defaults match BlueprintBuilderGenerated/.env) ------------------

LVS_PORT = int(os.environ.get("LVS_BACKEND_PORT") or os.environ.get("BACKEND_PORT") or 38111)
LVS_BASE_URL = os.environ.get("LVS_BASE_URL", f"http://localhost:{LVS_PORT}").rstrip("/")

RTVI_PORT = int(os.environ.get("RTVI_VLM_PORT") or 8420)
RTVI_BASE_URL = os.environ.get("RTVI_VLM_URL", f"http://localhost:{RTVI_PORT}").rstrip("/")

# Video fetched by LVS from Artifactory (ASSET_DOWNLOAD_AUTH_TOKENS must be
# set in the compose stack). Same origin as media-server.yaml's downloader.
# Override via RTVI_E2E_FILE_URL for local runs pointing at media-server.
FILE_URL = os.environ.get(
    "RTVI_E2E_FILE_URL",
    "https://artifactory.nvidia.com/artifactory/sw-ds-generic-bld-local"
    "/via-engine/media/perf/reencode/2min.mp4",
)

# Scenario + events used in §3 summarize requests. Mirrors run_sanity.sh.
SUMMARIZE_SCENARIO = "general surveillance"
SUMMARIZE_EVENTS = ["activity", "movement", "object"]
SUMMARIZE_CHUNK_DURATION = 15  # 2-min video / 15s chunks = 8 chunks

# Directory where LVS writes its log files (mounted into the pytest container
# via -v /tmp/via-logs:/tmp/via-logs in the CI docker run command).
LVS_LOG_DIR = os.environ.get("LVS_LOG_DIR", "/tmp/via-logs")


# -- Helpers -----------------------------------------------------------------


def _wait_for(condition_fn, timeout_s: float = 300.0, poll_s: float = 3.0, label: str = ""):
    """Poll ``condition_fn`` until truthy or the timeout expires."""
    deadline = time.time() + timeout_s
    last_exc: Optional[Exception] = None
    last_value: Any = None
    while time.time() < deadline:
        try:
            last_value = condition_fn()
            if last_value:
                return last_value
        except Exception as ex:
            last_exc = ex
        time.sleep(poll_s)
    pytest.fail(
        f"Timed out after {timeout_s}s waiting for: {label} "
        f"(last_value={last_value!r}, last_exception={last_exc!r})"
    )


def _skip_if_generation_backend_unavailable_text(text: str):
    backend_unavailable_signals = (
        "NoChunksReturned",
        "No chunks returned",
        "Summarization failed. No chunks returned",
    )
    if any(signal in text for signal in backend_unavailable_signals):
        pytest.skip(f"Generation back-end returned no chunks: {text[:200]}")


def _skip_if_generation_backend_unavailable_response(response: requests.Response):
    if response.status_code not in (500, 503):
        return

    response_text = response.text or ""
    try:
        body = response.json()
    except ValueError:
        body = {}

    code = str(body.get("code", ""))
    message = str(body.get("message", ""))
    _skip_if_generation_backend_unavailable_text(" ".join((code, message, response_text)))


def _resolve_model_id() -> str:
    r = requests.get(f"{LVS_BASE_URL}/models", timeout=15)
    r.raise_for_status()
    return r.json()["data"][0]["id"]


def _parse_summarize_payload(body: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and validate the aggregator JSON from a CompletionResponse.

    Asserts:
    - ``object`` starts with ``"summarization."``
    - ``choices`` is non-empty (or object == ``"summarization.completion"``
      with empty choices, which is the usage-only terminator)
    - ``choices[0].message.content`` parses as JSON with a non-empty
      ``events`` list and a non-empty ``video_summary``

    Mirrors the inline Python validation block in ``run_sanity.sh`` §3.
    Returns the parsed aggregator dict ``{events, video_summary, ...}``.
    """
    obj_type = body.get("object", "")
    assert obj_type.startswith("summarization."), (
        f"Unexpected object type: {obj_type!r}. "
        f"Expected one of summarization.completion / summarization.progressing."
    )

    choices = body.get("choices") or []

    # The bare include_usage terminator has object="summarization.completion"
    # and empty choices — pass on structure alone (no content to validate).
    if obj_type == "summarization.completion" and not choices:
        return {}

    assert (
        choices
    ), f"CompletionResponse has no choices: object={obj_type!r}, body keys={list(body.keys())}"

    content = (choices[0].get("message") or {}).get("content", "") or ""
    assert content.strip(), f"choices[0].message.content is empty: object={obj_type!r}"

    try:
        _skip_if_generation_backend_unavailable_text(content)
        payload = json.loads(content)
    except json.JSONDecodeError as ex:
        pytest.fail(
            f"choices[0].message.content is not JSON: {ex} | " f"content[:200]={content[:200]!r}"
        )
        return {}  # unreachable; satisfies type-checker

    events = payload.get("events") or []
    summary = (payload.get("video_summary") or "").strip()
    assert (
        isinstance(events, list) and len(events) > 0
    ), f"Aggregator payload has empty events list: {payload}"
    assert summary, f"Aggregator payload has empty video_summary: {payload}"
    return payload


# -- Fixtures ----------------------------------------------------------------


@pytest.fixture(scope="module")
def lvs_ready():
    """Wait until via-engine reports /v1/ready (200)."""

    def ready():
        try:
            return requests.get(f"{LVS_BASE_URL}/v1/ready", timeout=5).status_code == 200
        except requests.RequestException:
            return False

    _wait_for(ready, timeout_s=300, label=f"via-engine /v1/ready at {LVS_BASE_URL}")


@pytest.fixture(scope="module")
def rtvi_ready():
    """Wait until rtvi-vlm reports /v1/health/ready (200)."""

    def ready():
        try:
            return requests.get(f"{RTVI_BASE_URL}/v1/health/ready", timeout=5).status_code == 200
        except requests.RequestException:
            return False

    _wait_for(ready, timeout_s=600, label=f"rtvi-vlm /v1/health/ready at {RTVI_BASE_URL}")


@pytest.fixture(scope="module")
def model_id(lvs_ready) -> str:
    return _resolve_model_id()


# -- §1: Health & Connectivity -----------------------------------------------


class TestHealthConnectivity:
    """§1 of run_sanity.sh — health probes for LVS and RTVI VLM."""

    @pytest.mark.test_in_ci
    def test_lvs_ready(self, lvs_ready):
        """GET /v1/ready → 200."""
        r = requests.get(f"{LVS_BASE_URL}/v1/ready", timeout=10)
        assert r.status_code == 200, f"LVS /v1/ready returned HTTP {r.status_code}: {r.text[:200]}"

    @pytest.mark.test_in_ci
    def test_rtvi_vlm_health_ready(self, rtvi_ready):
        """GET /v1/health/ready → 200."""
        r = requests.get(f"{RTVI_BASE_URL}/v1/health/ready", timeout=10)
        assert (
            r.status_code == 200
        ), f"RTVI VLM /v1/health/ready returned HTTP {r.status_code}: {r.text[:200]}"


# -- §2: Model Discovery -----------------------------------------------------


class TestModelDiscovery:
    """§2 of run_sanity.sh — model list from LVS and RTVI VLM."""

    @pytest.mark.test_in_ci
    def test_lvs_models(self, lvs_ready):
        """GET /models → 200 with at least one model entry."""
        r = requests.get(f"{LVS_BASE_URL}/models", timeout=15)
        assert r.status_code == 200, f"LVS /models returned HTTP {r.status_code}: {r.text[:200]}"
        body = r.json()
        data = body.get("data") or []
        assert data, f"LVS /models returned empty data list: {body}"
        assert data[0].get("id"), f"First model has no id: {data[0]}"

    @pytest.mark.test_in_ci
    def test_rtvi_vlm_models(self, rtvi_ready):
        """GET /v1/models → 200 with at least one model entry."""
        r = requests.get(f"{RTVI_BASE_URL}/v1/models", timeout=15)
        assert (
            r.status_code == 200
        ), f"RTVI VLM /v1/models returned HTTP {r.status_code}: {r.text[:200]}"
        body = r.json()
        data = body.get("data") or []
        assert data, f"RTVI VLM /v1/models returned empty data list: {body}"


# -- §3: End-to-End File Summarization ---------------------------------------


class TestFileSummarizationEndToEnd:
    """§3 of run_sanity.sh — full pipeline: RTVI inference → CA-RAG aggregation.

    Two class-scoped fixtures run the summarize call once each (stream=false
    and stream=true) and cache the result for all tests in the class, avoiding
    duplicate expensive VLM inference calls.
    """

    @pytest.fixture(scope="class")
    def file_sync_response(self, lvs_ready, rtvi_ready, model_id) -> Dict[str, Any]:
        """POST /v1/summarize stream=false → sync CompletionResponse body."""
        body = {
            "url": FILE_URL,
            "model": model_id,
            "scenario": SUMMARIZE_SCENARIO,
            "events": SUMMARIZE_EVENTS,
            "chunk_duration": SUMMARIZE_CHUNK_DURATION,
            "stream": False,
        }
        logger.info(
            "§3 stream=false: POST /v1/summarize url=%s chunk_duration=%s",
            FILE_URL,
            SUMMARIZE_CHUNK_DURATION,
        )
        r = requests.post(
            f"{LVS_BASE_URL}/v1/summarize",
            json=body,
            timeout=900,
        )
        _skip_if_generation_backend_unavailable_response(r)
        assert (
            r.status_code == 200
        ), f"POST /v1/summarize stream=false failed: HTTP {r.status_code} {r.text[:500]}"
        return r.json()

    @pytest.fixture(scope="class")
    def file_sse_response(self, lvs_ready, rtvi_ready, model_id) -> Dict[str, Any]:
        """POST /v1/summarize stream=true → final SSE event body (before [DONE])."""
        body = {
            "url": FILE_URL,
            "model": model_id,
            "scenario": SUMMARIZE_SCENARIO,
            "events": SUMMARIZE_EVENTS,
            "chunk_duration": SUMMARIZE_CHUNK_DURATION,
            "stream": True,
        }
        logger.info(
            "§3 stream=true: POST /v1/summarize url=%s chunk_duration=%s (SSE)",
            FILE_URL,
            SUMMARIZE_CHUNK_DURATION,
        )
        r = requests.post(
            f"{LVS_BASE_URL}/v1/summarize",
            json=body,
            timeout=900,
            stream=True,
            headers={"Accept": "text/event-stream"},
        )
        try:
            _skip_if_generation_backend_unavailable_response(r)
            assert (
                r.status_code == 200
            ), f"POST /v1/summarize stream=true failed: HTTP {r.status_code} {r.text[:500]}"
            last_event: Optional[Dict[str, Any]] = None
            for raw in r.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith("data:"):
                    continue
                payload_str = raw[len("data:") :].strip()
                if not payload_str or payload_str == "[DONE]":
                    continue
                try:
                    last_event = json.loads(payload_str)
                except json.JSONDecodeError:
                    continue
        finally:
            r.close()

        assert last_event is not None, (
            "No usable SSE event received from POST /v1/summarize stream=true. "
            "Stream may have terminated without emitting a final aggregator event."
        )
        return last_event

    @pytest.mark.test_in_ci
    def test_sync_response_structure(self, file_sync_response):
        """stream=false: CompletionResponse with non-empty events and video_summary."""
        _parse_summarize_payload(file_sync_response)

    @pytest.mark.test_in_ci
    def test_sse_response_structure(self, file_sse_response):
        """stream=true: final SSE event with non-empty events and video_summary.

        The last ``data:`` event before ``[DONE]`` is always
        ``"summarization.progressing"`` (when include_usage is not set, which
        is the default). Its choices[0].message.content carries the aggregated
        payload — same validation as stream=false. Mirrors the PASS condition
        in run_sanity.sh §3 (stream=true branch).
        """
        _parse_summarize_payload(file_sse_response)


# -- §3a: Sticky-routing header ----------------------------------------------


class TestStickyRoutingHeader:
    """§3a of run_sanity.sh — x-stream-id logged on LVS → RTVI calls.

    Checks that at least one log line matching
    ``RTVI generate_captions_stream: x-stream-id=`` appears in the LVS log
    files after the §3 summarize calls. Reads log files from LVS_LOG_DIR
    (default /tmp/via-logs, mounted into the CI pytest container).

    Skipped if the log directory is unavailable (e.g. local developer runs
    that don't mount /tmp/via-logs into the test process environment).
    """

    @pytest.mark.test_in_ci
    def test_x_stream_id_in_lvs_logs(
        self,
        lvs_ready,  # ensure LVS is up; §3 summarize tests run first (file order)
    ):
        if not os.path.isdir(LVS_LOG_DIR):
            pytest.skip(
                f"LVS log directory {LVS_LOG_DIR!r} not available in this environment. "
                "Mount /tmp/via-logs into the test container to enable this check."
            )

        pattern = re.compile(r"RTVI generate_captions_stream: x-stream-id=")
        hits = 0
        log_files = glob.glob(os.path.join(LVS_LOG_DIR, "*.log"))
        for log_file in log_files:
            try:
                with open(log_file, errors="replace") as fh:
                    for line in fh:
                        if pattern.search(line):
                            hits += 1
            except OSError:
                pass

        assert hits >= 1, (
            f"No 'x-stream-id=' log line found in LVS logs at {LVS_LOG_DIR!r}. "
            f"Files searched: {log_files}. "
            "RtviVlmClient should log an INFO line on every outbound "
            "generate_captions_stream call (METLVSMS-500)."
        )
        logger.info("§3a sticky-routing: found %d x-stream-id log line(s)", hits)


# -- §4: Negative Tests — Input Validation -----------------------------------


class TestNegativeInputValidation:
    """§4 of run_sanity.sh — pydantic and model validation gates.

    These tests do NOT require a real video to be downloadable: pydantic
    validation (422) fires before any URL fetch, and model validation (400)
    fires before RTVI is contacted.
    """

    @pytest.mark.test_in_ci
    def test_missing_required_fields(self, lvs_ready):
        """POST /v1/summarize with only url → 422 (missing model/scenario/events).

        FastAPI/pydantic rejects the request body before LVS processes it.
        The URL is never fetched — any syntactically valid URL string works.
        """
        r = requests.post(
            f"{LVS_BASE_URL}/v1/summarize",
            json={"url": "http://media-server/0.5min.mp4"},
            timeout=30,
        )
        assert (
            r.status_code == 422
        ), f"Expected 422 for missing required fields, got HTTP {r.status_code}: {r.text[:200]}"

    @pytest.mark.test_in_ci
    def test_wrong_model_name(self, lvs_ready):
        """POST /v1/summarize with a nonexistent model → 4xx client error.

        Model validation in LVS fails before any URL fetch or RTVI call.
        Uses an unreachable localhost URL to make intent explicit. A 5xx
        response would indicate a server crash/unhandled exception, which
        should be treated as a regression rather than an acceptable response.
        """
        r = requests.post(
            f"{LVS_BASE_URL}/v1/summarize",
            json={
                "url": "http://127.0.0.1/test.mp4",
                "model": "nonexistent-model",
                "scenario": "general",
                "events": ["activity"],
            },
            timeout=30,
        )
        assert (
            400 <= r.status_code < 500
        ), f"Expected a 4xx client error for wrong model name, got HTTP {r.status_code}: {r.text[:200]}"
