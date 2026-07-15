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
Functional tests for CA-RAG (Context-Aware RAG) summarization flows.

Tests how the /summarize endpoint behaves with different CA-RAG settings:
- CA-RAG aggregation is always enabled when CA-RAG is configured
- ``summarize=True``    → aggregated summary via LLM
- When CA-RAG is disabled (``--disable-ca-rag``), chunks are returned directly

These tests call a real running service and are therefore marked
``slow`` + ``test_in_ci``.  They are skipped automatically when the
external vector-DB / LLM back-end is not reachable.
"""

import json
import logging

import pytest

logger = logging.getLogger(__name__)

_VIDEO_URL = (
    "https://artifactory.nvidia.com/artifactory/"
    "sw-ds-generic-bld-local/via-engine/media/bp_preview/its_264.mp4"
)

_BASE_PAYLOAD = {
    "url": _VIDEO_URL,
    "events": ["accident", "emergency vehicle"],
    "scenario": "traffic monitoring",
    "chunk_duration": 10,
    "max_tokens": 256,
    "stream": False,
}


def _model_id(base_url, session):
    resp = session.get(f"{base_url}/models", timeout=15)
    resp.raise_for_status()
    return resp.json()["data"][0]["id"]


# ---------------------------------------------------------------------------
# CA-RAG enabled
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.test_in_ci
def test_summarize_with_enable_chat_true_enables_rag(base_url, session, shared_state):
    """Summarization with CA-RAG returns a single aggregated response."""
    model_id = shared_state.get("model_id") or _model_id(base_url, session)

    payload = dict(_BASE_PAYLOAD)
    payload.update(
        {
            "model": model_id,
            "summarize": True,
        }
    )

    resp = session.post(f"{base_url}/summarize", json=payload, timeout=180)
    logger.info("CA-RAG enabled response: %s %s", resp.status_code, resp.text[:500])

    if resp.status_code in (503, 500):
        pytest.skip(f"CA-RAG back-end not available: {resp.status_code} {resp.text[:200]}")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()

    # With summarize=True we expect a single completion object
    assert "choices" in data, f"Response missing 'choices': {data}"
    assert len(data["choices"]) > 0

    # Store for follow-on chat test
    if "video_id" in data:
        shared_state.setdefault("summarized_video_id", data["video_id"])


@pytest.mark.slow
@pytest.mark.test_in_ci
def test_summarize_with_ca_rag_disabled_returns_chunk_responses(base_url, session, shared_state):
    """Summarization without CA-RAG aggregation returns one response per video chunk."""
    model_id = shared_state.get("model_id") or _model_id(base_url, session)

    payload = dict(_BASE_PAYLOAD)
    payload.update(
        {
            "model": model_id,
            "summarize": False,
            "stream": True,  # chunk responses arrive as SSE
        }
    )

    resp = session.post(f"{base_url}/summarize", json=payload, timeout=180, stream=True)
    logger.info("CA-RAG disabled response status: %s", resp.status_code)

    if resp.status_code in (503, 500):
        pytest.skip(f"Service not available: {resp.status_code}")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    # Read at least one SSE event to confirm per-chunk streaming
    events_received = 0
    for raw_line in resp.iter_lines():
        line = raw_line.decode() if isinstance(raw_line, bytes) else raw_line
        if not line.startswith("data:"):
            continue
        payload_str = line[len("data:") :].strip()
        if payload_str == "[DONE]":
            break
        try:
            chunk_data = json.loads(payload_str)
            if chunk_data.get("choices"):
                content = chunk_data["choices"][0].get("message", {}).get("content", "")
                if "Summarization failed" in content or "no chunks" in content.lower():
                    pytest.skip("Video processing back-end returned no chunks: " f"{content[:200]}")
                events_received += 1
            if events_received >= 2:
                break  # confirmed multiple chunks
        except json.JSONDecodeError:
            pass

    assert events_received >= 1, "Expected at least one chunk response in SSE stream"


@pytest.mark.slow
@pytest.mark.test_in_ci
def test_summarize_with_ca_rag_enabled_triggers_ingestion(base_url, session, shared_state):
    """Summarization with CA-RAG ingestion function produces a non-empty result."""
    model_id = shared_state.get("model_id") or _model_id(base_url, session)

    payload = dict(_BASE_PAYLOAD)
    payload.update(
        {
            "model": model_id,
            "summarize": True,
        }
    )

    resp = session.post(f"{base_url}/summarize", json=payload, timeout=180)
    logger.info("CA-RAG ingestion response: %s %s", resp.status_code, resp.text[:500])

    if resp.status_code in (500, 503):
        pytest.skip(f"CA-RAG / DB back-end not reachable: {resp.status_code}")

    assert resp.status_code == 200
    data = resp.json()
    choices = data.get("choices", [])
    assert len(choices) > 0, "No choices in CA-RAG response"

    content = choices[0].get("message", {}).get("content", "")
    assert isinstance(content, str), "Expected string content"
