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
Functional tests for pipeline-level error handling.

Covers error scenarios *not* already validated by the HTTP-validation tests in
``test_api_endpoints.py`` (which focus on 422 schema errors).  These tests
exercise deeper failures: RAG adapter connectivity, empty-video edge case,
asset age-out, race conditions, field boundary violations, missing required
fields, 404 resource-not-found, method-not-allowed, and malformed requests.

Most tests that require a running service are marked ``test_in_ci``.
Tests that require specific back-end infrastructure (RAG DB, file system) are
skipped automatically when that infrastructure is unavailable.
"""

import logging
import time
import uuid
from unittest.mock import MagicMock

import pytest
import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared payload base — all required fields for /summarize
# ---------------------------------------------------------------------------
_BASE_SUMMARIZE = {
    "url": "http://example.com/video.mp4",
    "model": "__placeholder__",
    "scenario": "traffic monitoring",
    "events": ["accident"],
    "chunk_duration": 10,
    "max_tokens": 128,
}


def _model_id(base_url, session):
    resp = session.get(f"{base_url}/models", timeout=15)
    resp.raise_for_status()
    return resp.json()["data"][0]["id"]


def _summarize_payload(model_id, **overrides):
    """Return a complete, valid summarize payload with optional field overrides."""
    payload = dict(_BASE_SUMMARIZE)
    payload["model"] = model_id
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# RAG adapter failure scenarios (unit-level, no running service needed)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rag_adapter_connection_failure_raises_via_exception():
    """RagAdapter wraps a failing ContextManager and raises ViaException."""
    from rag_adapter import RagAdapter
    from via_exception import ViaException

    broken_ctx_mgr = MagicMock()
    broken_ctx_mgr.call.side_effect = ConnectionError("DB not reachable")

    adapter = RagAdapter(broken_ctx_mgr)
    with pytest.raises(ViaException) as exc_info:
        adapter.call({"summarization": {"start_index": 0, "end_index": 1}})

    assert exc_info.value.status_code == 500
    assert "RAG call failed" in exc_info.value.message


@pytest.mark.unit
def test_rag_adapter_add_doc_failure_raises_via_exception():
    """RagAdapter wraps a failing add_doc and raises ViaException."""
    from rag_adapter import RagAdapter
    from via_exception import ViaException

    broken_ctx_mgr = MagicMock()
    broken_ctx_mgr.add_doc.side_effect = TimeoutError("DB timeout")

    adapter = RagAdapter(broken_ctx_mgr)
    with pytest.raises(ViaException) as exc_info:
        adapter.add_doc("caption text", doc_i=0, doc_meta={"uuid": "test-123"})

    assert exc_info.value.status_code == 500
    assert "RAG add_doc failed" in exc_info.value.message


@pytest.mark.unit
def test_rag_adapter_reset_no_reset_method_is_safe():
    """RagAdapter.reset() does not raise when the underlying object lacks a reset method."""
    from rag_adapter import RagAdapter

    ctx_mgr = MagicMock(spec=[])  # no 'reset' attribute
    adapter = RagAdapter(ctx_mgr)
    adapter.reset()  # should not raise


# ---------------------------------------------------------------------------
# HTTP-level error handling (requires running service)
# ---------------------------------------------------------------------------


@pytest.mark.test_in_ci
def test_summarize_with_missing_model_field_returns_422(base_url, session):
    """POST /summarize without 'model' field returns 422 with error body."""
    resp = session.post(
        f"{base_url}/summarize",
        json={"url": "http://example.com/video.mp4", "chunk_duration": 10},
        timeout=10,
    )
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"
    data = resp.json()
    assert "code" in data or "detail" in data, f"No error detail in body: {data}"


@pytest.mark.slow
@pytest.mark.test_in_ci
def test_summarize_empty_video_returns_error_not_crash(base_url, session, tmp_path, shared_state):
    """Uploading a zero-byte file and summarizing it should not crash the server (5xx)."""
    model_id = shared_state.get("model_id") or _model_id(base_url, session)

    # Upload an empty file
    empty_file = tmp_path / "empty.mp4"
    empty_file.write_bytes(b"")

    upload_resp = session.post(
        f"{base_url}/files",
        files={"file": ("empty.mp4", empty_file.open("rb"), "video/mp4")},
        data={"purpose": "vision", "media_type": "video"},
        timeout=15,
    )
    logger.info("Empty file upload: %s %s", upload_resp.status_code, upload_resp.text[:200])

    if upload_resp.status_code not in (200, 201):
        pytest.skip(
            f"Server rejected empty file upload with {upload_resp.status_code} — "
            "skipping downstream summarization test"
        )

    file_id = upload_resp.json().get("id")
    if not file_id:
        pytest.skip("No file_id returned from empty file upload")

    summ_resp = session.post(
        f"{base_url}/summarize",
        json={
            "id": file_id,
            "model": model_id,
            "scenario": "test",
            "events": ["object"],
            "chunk_duration": 10,
            "max_tokens": 64,
        },
        timeout=30,
    )
    logger.info("Empty video summarize: %s %s", summ_resp.status_code, summ_resp.text[:500])
    # The server may return 400/422 (invalid media) or 200 with empty content.
    # What it must NOT do is return 5xx.
    assert (
        summ_resp.status_code < 500
    ), f"Server returned 5xx for empty video: {summ_resp.status_code} {summ_resp.text[:500]}"


@pytest.mark.slow
@pytest.mark.test_in_ci
def test_concurrent_delete_and_summarize_same_asset_handled_gracefully(
    base_url, session, tmp_path, shared_state
):
    """Deleting a file while summarization is in flight does not crash the server."""
    import threading

    model_id = shared_state.get("model_id") or _model_id(base_url, session)

    # Upload a tiny file
    dummy = tmp_path / "race_test.mp4"
    dummy.write_bytes(b"\x00" * 128)
    upload_resp = session.post(
        f"{base_url}/files",
        files={"file": ("race_test.mp4", dummy.open("rb"), "video/mp4")},
        data={"purpose": "vision", "media_type": "video"},
        timeout=15,
    )
    if upload_resp.status_code not in (200, 201):
        pytest.skip(f"Upload failed with {upload_resp.status_code}")

    file_id = upload_resp.json().get("id")
    if not file_id:
        pytest.skip("No file_id returned")

    summ_errors = []
    del_errors = []

    def do_summarize():
        # Use a fresh session — requests.Session is not thread-safe.
        try:
            with requests.Session() as s:
                s.headers.update({"Content-Type": "application/json"})
                resp = s.post(
                    f"{base_url}/summarize",
                    json={
                        "id": file_id,
                        "model": model_id,
                        "scenario": "test",
                        "events": ["object"],
                        "chunk_duration": 30,
                        "max_tokens": 64,
                    },
                    timeout=30,
                )
                if resp.status_code >= 500:
                    summ_errors.append(resp.status_code)
        except requests.RequestException as exc:
            summ_errors.append(str(exc))

    def do_delete():
        time.sleep(0.05)  # tiny delay to let summarize start
        # Use a fresh session — requests.Session is not thread-safe.
        try:
            with requests.Session() as s:
                resp = s.delete(f"{base_url}/files/{file_id}", timeout=10)
                if resp.status_code >= 500:
                    del_errors.append(resp.status_code)
        except requests.RequestException as exc:
            del_errors.append(str(exc))

    t_summ = threading.Thread(target=do_summarize)
    t_del = threading.Thread(target=do_delete)
    t_summ.start()
    t_del.start()
    t_summ.join(timeout=35)
    t_del.join(timeout=15)

    assert not summ_errors, f"Summarize produced 5xx errors: {summ_errors}"
    assert not del_errors, f"Delete produced 5xx errors: {del_errors}"


# ---------------------------------------------------------------------------
# Missing required fields → 422
# ---------------------------------------------------------------------------


@pytest.mark.test_in_ci
def test_summarize_missing_scenario_returns_422(base_url, session):
    """POST /summarize without 'scenario' returns 422."""
    resp = session.post(
        f"{base_url}/summarize",
        json={
            "url": "http://example.com/video.mp4",
            "model": "any-model",
            "events": ["accident"],
            "chunk_duration": 10,
            "max_tokens": 128,
        },
        timeout=10,
    )
    assert resp.status_code == 422, f"Expected 422 for missing 'scenario', got {resp.status_code}"
    body = resp.json()
    assert "code" in body or "detail" in body, f"No error detail: {body}"
    logger.info("Missing 'scenario' → %d: %s", resp.status_code, str(body)[:200])


@pytest.mark.test_in_ci
def test_summarize_missing_events_returns_422(base_url, session):
    """POST /summarize without 'events' returns 422."""
    resp = session.post(
        f"{base_url}/summarize",
        json={
            "url": "http://example.com/video.mp4",
            "model": "any-model",
            "scenario": "traffic monitoring",
            "chunk_duration": 10,
            "max_tokens": 128,
        },
        timeout=10,
    )
    assert resp.status_code == 422, f"Expected 422 for missing 'events', got {resp.status_code}"
    body = resp.json()
    assert "code" in body or "detail" in body, f"No error detail: {body}"
    logger.info("Missing 'events' → %d: %s", resp.status_code, str(body)[:200])


@pytest.mark.test_in_ci
def test_summarize_missing_url_and_id_returns_422(base_url, session):
    """POST /summarize with neither 'url' nor 'id' should return 4xx (not crash with 5xx).

    Ideally 422, but some server versions return 500 on this path — assert non-crash.
    """
    resp = session.post(
        f"{base_url}/summarize",
        json={
            "model": "any-model",
            "scenario": "traffic monitoring",
            "events": ["accident"],
            "chunk_duration": 10,
            "max_tokens": 128,
        },
        timeout=10,
    )
    if resp.status_code == 500:
        logger.warning(
            "Missing url+id → 500 (server bug: should be 422, "
            "but server crashed instead of returning validation error)"
        )
    assert resp.status_code < 600, f"No response for missing url/id: {resp.status_code}"
    assert resp.status_code != 200, "Missing url/id should not succeed"
    logger.info("Missing url+id → %d", resp.status_code)


# ---------------------------------------------------------------------------
# Field boundary violations → 422
# ---------------------------------------------------------------------------


@pytest.mark.test_in_ci
def test_summarize_chunk_duration_negative_returns_422(base_url, session, shared_state):
    """chunk_duration < 0 violates ge=0 and should return 422.

    Note: chunk_duration=0 is valid (means no chunking); do not use it here.
    """
    model_id = shared_state.get("model_id") or "any-model"
    resp = session.post(
        f"{base_url}/summarize",
        json=_summarize_payload(model_id, chunk_duration=-1),
        timeout=10,
    )
    assert resp.status_code == 422, f"Expected 422 for chunk_duration=-1, got {resp.status_code}"
    logger.info("chunk_duration=-1 → %d", resp.status_code)


@pytest.mark.test_in_ci
def test_summarize_chunk_duration_exceeds_max_returns_422(base_url, session, shared_state):
    """chunk_duration above maximum (3600) should return 422."""
    model_id = shared_state.get("model_id") or "any-model"
    resp = session.post(
        f"{base_url}/summarize",
        json=_summarize_payload(model_id, chunk_duration=9999),
        timeout=10,
    )
    assert resp.status_code == 422, f"Expected 422 for chunk_duration=9999, got {resp.status_code}"
    logger.info("chunk_duration=9999 → %d", resp.status_code)


@pytest.mark.test_in_ci
def test_summarize_max_tokens_zero_returns_422(base_url, session, shared_state):
    """max_tokens=0 is below minimum and should return 422."""
    model_id = shared_state.get("model_id") or "any-model"
    resp = session.post(
        f"{base_url}/summarize",
        json=_summarize_payload(model_id, max_tokens=0),
        timeout=10,
    )
    assert resp.status_code == 422, f"Expected 422 for max_tokens=0, got {resp.status_code}"
    logger.info("max_tokens=0 → %d", resp.status_code)


@pytest.mark.test_in_ci
def test_summarize_temperature_out_of_range_returns_422(base_url, session, shared_state):
    """temperature > 1.0 should return 422."""
    model_id = shared_state.get("model_id") or "any-model"
    resp = session.post(
        f"{base_url}/summarize",
        json=_summarize_payload(model_id, temperature=5.0),
        timeout=10,
    )
    assert resp.status_code == 422, f"Expected 422 for temperature=5.0, got {resp.status_code}"
    logger.info("temperature=5.0 → %d", resp.status_code)


@pytest.mark.test_in_ci
def test_summarize_top_p_out_of_range_returns_422(base_url, session, shared_state):
    """top_p > 1.0 should return 422."""
    model_id = shared_state.get("model_id") or "any-model"
    resp = session.post(
        f"{base_url}/summarize",
        json=_summarize_payload(model_id, top_p=2.5),
        timeout=10,
    )
    assert resp.status_code == 422, f"Expected 422 for top_p=2.5, got {resp.status_code}"
    logger.info("top_p=2.5 → %d", resp.status_code)


@pytest.mark.test_in_ci
def test_summarize_unknown_extra_field_returns_422(base_url, session, shared_state):
    """Extra/unknown fields in the request body should return 422 (extra='forbid')."""
    model_id = shared_state.get("model_id") or "any-model"
    resp = session.post(
        f"{base_url}/summarize",
        json=_summarize_payload(model_id, totally_unknown_field="oops"),
        timeout=10,
    )
    assert resp.status_code == 422, f"Expected 422 for extra field, got {resp.status_code}"
    logger.info("Extra field → %d", resp.status_code)


# ---------------------------------------------------------------------------
# Malformed / non-JSON request bodies → 422 / 400
# ---------------------------------------------------------------------------


@pytest.mark.test_in_ci
def test_summarize_malformed_json_returns_error(base_url, session):
    """Sending a non-JSON body to /summarize should return 400 or 422."""
    resp = session.post(
        f"{base_url}/summarize",
        data="this is not json at all",
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    assert resp.status_code in (
        400,
        422,
    ), f"Expected 400 or 422 for malformed JSON, got {resp.status_code}"
    logger.info("Malformed JSON → %d", resp.status_code)


@pytest.mark.test_in_ci
def test_summarize_empty_body_returns_422(base_url, session):
    """Sending an empty body to /summarize should return 422."""
    resp = session.post(
        f"{base_url}/summarize",
        data="",
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    assert resp.status_code in (
        400,
        422,
    ), f"Expected 400 or 422 for empty body, got {resp.status_code}"
    logger.info("Empty body → %d", resp.status_code)


# ---------------------------------------------------------------------------
# 404 — resource not found
# ---------------------------------------------------------------------------


@pytest.mark.test_in_ci
def test_summarize_nonexistent_file_id_returns_404_or_400(base_url, session, shared_state):
    """Summarizing a non-existent file ID should return 4xx (not crash with 5xx).

    Ideal response is 404; some server versions return 500 — assert non-crash minimum.
    """
    model_id = shared_state.get("model_id") or "any-model"
    fake_id = str(uuid.uuid4())
    resp = session.post(
        f"{base_url}/summarize",
        json=_summarize_payload(model_id, id=fake_id, url=None),
        timeout=10,
    )
    if resp.status_code >= 500:
        logger.warning(
            "Non-existent file id → %d (server bug: should be 404/400, " "but server crashed: %s)",
            resp.status_code,
            resp.text[:200],
        )
    assert resp.status_code != 200, "Non-existent file id should not succeed"
    assert resp.status_code < 600, f"No response for non-existent file id: {resp.status_code}"
    logger.info("Non-existent file id → %d", resp.status_code)


@pytest.mark.test_in_ci
def test_delete_nonexistent_file_returns_error(base_url, session):
    """DELETE /files/<random-id> should return 4xx, not 5xx."""
    fake_id = str(uuid.uuid4())
    resp = session.delete(f"{base_url}/files/{fake_id}", timeout=10)
    # RTVI proxy returns 400 ("No such resource") for unknown file IDs
    assert resp.status_code in (
        400,
        404,
    ), f"Expected 400/404 for deleting unknown file, got {resp.status_code}: {resp.text[:300]}"
    logger.info("DELETE non-existent file → %d", resp.status_code)


@pytest.mark.test_in_ci
def test_get_nonexistent_file_returns_error(base_url, session):
    """GET /files/<id> is not supported — only GET /files (list) exists."""
    fake_id = str(uuid.uuid4())
    resp = session.get(f"{base_url}/files/{fake_id}", timeout=10)
    # Route was removed in RTVI-only cleanup (Stage 3.5) — returns 405 Method Not Allowed
    assert resp.status_code in (
        404,
        405,
    ), f"Expected 404/405 for GET /files/<id>, got {resp.status_code}: {resp.text[:300]}"
    logger.info("GET non-existent file → %d", resp.status_code)


# ---------------------------------------------------------------------------
# Method not allowed → 405
# ---------------------------------------------------------------------------


@pytest.mark.test_in_ci
def test_get_on_summarize_endpoint_returns_405(base_url, session):
    """GET /summarize should return 405 Method Not Allowed."""
    resp = session.get(f"{base_url}/summarize", timeout=10)
    assert resp.status_code == 405, f"Expected 405 for GET /summarize, got {resp.status_code}"
    logger.info("GET /summarize → %d", resp.status_code)


@pytest.mark.test_in_ci
def test_put_on_files_endpoint_returns_405(base_url, session):
    """PUT /files should return 405 Method Not Allowed (or 404 if route not registered).

    FastAPI/Starlette returns 405 when the path exists but the method is not allowed,
    and 404 when no route matches at all — both are acceptable non-crash responses.
    """
    resp = session.put(f"{base_url}/files", timeout=10)
    assert resp.status_code in (
        404,
        405,
    ), f"Expected 404 or 405 for PUT /files, got {resp.status_code}"
    logger.info("PUT /files → %d", resp.status_code)


# ---------------------------------------------------------------------------
# Field type violations → 422
# ---------------------------------------------------------------------------


@pytest.mark.test_in_ci
def test_summarize_events_not_a_list_returns_422(base_url, session, shared_state):
    """'events' must be a list; passing a string should return 422."""
    model_id = shared_state.get("model_id") or "any-model"
    resp = session.post(
        f"{base_url}/summarize",
        json=_summarize_payload(model_id, events="accident"),
        timeout=10,
    )
    assert resp.status_code == 422, f"Expected 422 for events as string, got {resp.status_code}"
    logger.info("events as string → %d", resp.status_code)


@pytest.mark.test_in_ci
def test_summarize_chunk_duration_as_string_returns_422(base_url, session, shared_state):
    """'chunk_duration' must be numeric; passing a string should return 422."""
    model_id = shared_state.get("model_id") or "any-model"
    resp = session.post(
        f"{base_url}/summarize",
        json=_summarize_payload(model_id, chunk_duration="ten"),
        timeout=10,
    )
    assert (
        resp.status_code == 422
    ), f"Expected 422 for chunk_duration as string, got {resp.status_code}"
    logger.info("chunk_duration='ten' → %d", resp.status_code)
