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
Functional tests for the VIA Engine chat (RAG query) API.

Chat with a previously summarized video is done via POST /summarize with:
  - ``id``          = video_id returned by the original summarization
  - ``prompt``      = the user's question

There is no separate /chat/completions endpoint; the /summarize route handles
both initial summarization (``url``/``id`` + ``summarize=True``) and follow-up
chat queries (``id`` + ``prompt``).
"""

import logging

import pytest
import requests

logger = logging.getLogger(__name__)

_ARTIFACT_VIDEO_URL = (
    "https://artifactory.nvidia.com/artifactory/"
    "sw-ds-generic-bld-local/via-engine/media/bp_preview/its_264.mp4"
)

SUMMARIZATION_PAYLOAD = {
    "url": _ARTIFACT_VIDEO_URL,
    "model": "nvidia/cosmos-reason2-8b",
    "events": ["accident", "emergency vehicle"],
    "scenario": "traffic monitoring",
    "chunk_duration": 10,
    "max_tokens": 512,
    "stream": False,
}


def _get_model_id(base_url: str, session: requests.Session) -> str:
    resp = session.get(f"{base_url}/models", timeout=15)
    resp.raise_for_status()
    return resp.json()["data"][0]["id"]


def _summarize_video(base_url: str, session: requests.Session, model_id: str) -> str:
    """Run summarization and return the video_id."""
    payload = dict(SUMMARIZATION_PAYLOAD)
    payload["model"] = model_id
    resp = session.post(f"{base_url}/summarize", json=payload, timeout=120)
    if resp.status_code in (500, 503):
        pytest.skip(
            f"LLM/CA-RAG back-end not available in test environment: "
            f"{resp.status_code} {resp.text[:200]}"
        )
    assert resp.status_code == 200, f"Summarize failed: {resp.status_code} {resp.text[:500]}"
    return resp.json().get("video_id", "")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.test_in_ci
def test_chat_endpoint_returns_answer_after_summarization(base_url, session, shared_state):
    """After summarization, a follow-up chat query returns an answer.

    Chat queries reuse POST /summarize with ``id=<video_id>`` and
    ``prompt`` — there is no separate /chat/completions route.
    """
    model_id = shared_state.get("model_id") or _get_model_id(base_url, session)

    # Ensure we have a video that was summarized
    video_id = shared_state.get("summarized_video_id")
    if not video_id:
        video_id = _summarize_video(base_url, session, model_id)
        shared_state["summarized_video_id"] = video_id

    if not video_id:
        pytest.skip("Could not obtain a summarized video_id")

    # Follow-up chat query: ask a question about the already-indexed video
    chat_payload = {
        "id": video_id,
        "model": model_id,
        "prompt": "What happened in the video?",
        "scenario": "traffic monitoring",
        "events": ["accident", "emergency vehicle"],
        "stream": False,
        "max_tokens": 256,
    }

    resp = session.post(f"{base_url}/summarize", json=chat_payload, timeout=60)
    logger.info("Chat response: %s %s", resp.status_code, resp.text[:500])

    if resp.status_code in (500, 503):
        pytest.skip(
            f"LLM/CA-RAG back-end not available in test environment: "
            f"{resp.status_code} {resp.text[:200]}"
        )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "choices" in data, f"Response missing 'choices': {data}"
    assert len(data["choices"]) > 0, "'choices' array is empty"

    message = data["choices"][0].get("message", {})
    assert "content" in message, f"Choice missing 'content': {message}"
    content = message["content"]
    assert isinstance(content, str) and len(content.strip()) > 0, "Empty chat response content"


@pytest.mark.test_in_ci
def test_chat_query_with_nonexistent_video_id_returns_error(base_url, session, shared_state):
    """A chat query with a non-existent video_id returns a 4xx error."""
    model_id = shared_state.get("model_id", "unknown-model")

    chat_payload = {
        "id": "00000000-0000-0000-0000-deadbeef0000",  # does not exist
        "model": model_id,
        "prompt": "Tell me about the video.",
        "scenario": "traffic monitoring",
        "events": ["accident"],
        "stream": False,
        "max_tokens": 128,
    }

    resp = session.post(f"{base_url}/summarize", json=chat_payload, timeout=15)
    logger.info("Chat (no video) response: %s %s", resp.status_code, resp.text[:500])

    assert resp.status_code in (
        400,
        404,
        422,
    ), f"Expected 4xx for unknown video_id, got {resp.status_code}"
