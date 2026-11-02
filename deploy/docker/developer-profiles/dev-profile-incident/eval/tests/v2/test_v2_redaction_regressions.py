# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regressions from the Evaluation V2 review of URL redaction: user info, and signed URLs echoed in errors."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

from evalv2.adapters.base import CallContext, redact_text, redact_url, stored_request
from evalv2.adapters.openai_compat import ENDPOINTS, OpenAICompatAdapter
from evalv2.adapters.registry import capabilities_for
from evalv2.adapters.vss import VssAgentAdapter
from evalv2.core.artifacts import ArtifactRef, ArtifactStore
from evalv2.core.budget import BudgetLedger, Cap
from evalv2.core.retry import POLICIES
from evalv2.requests.p1 import build_p1_request
from evalv2.tasks.incident_objects.runner import IncidentObjectsTask

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v2_track_b import MODEL, FakeAgent, _ctx, _request  # noqa: E402

SIGNATURE = "X-Amz-Signature=0123456789abcdef"
SIGNED = f"https://r2.example/videos/Assault001.mp4?X-Amz-Expires=3600&{SIGNATURE}"
ENV = {"VLM_GATEWAY_BASE_URL": "https://gateway.example/v1", "VLM_GATEWAY_API_KEY": "test-key"}


def _stored_bytes(root):
    return b"".join(p.read_bytes() for p in Path(root).rglob("*") if p.is_file())


@pytest.mark.parametrize(("url", "expected"), [
    ("https://user:pw@host.example/v.mp4", "https://host.example/v.mp4"),
    ("https://user:pw@host.example:8443/v.mp4?sig=1", "https://host.example:8443/v.mp4"),
    ("https://host.example/v.mp4#t=4", "https://host.example/v.mp4"),
    ("https://host.example/v.mp4", "https://host.example/v.mp4"),
    ("data:video/mp4;base64,AAAA", "data:video/mp4;base64,AAAA"),
])
def test_redact_url_drops_user_info_query_and_fragment(url, expected):
    assert redact_url(url) == expected


def test_a_stored_request_never_keeps_user_info():
    request = build_p1_request(MODEL, "https://reviewer:secret-pass@videos.example/A.mp4")
    assert "secret-pass" not in json.dumps(stored_request(request))


def test_a_signed_url_echoed_in_an_error_body_is_never_stored(tmp_path):
    echoed = httpx.Response(400, text=f"Error loading video: ClientResponseError(403, url='{SIGNED}')")
    adapter = OpenAICompatAdapter("super-direct", MODEL, ENDPOINTS["switchyard"], capabilities_for(MODEL),
                                  transport=httpx.MockTransport(lambda request: echoed), environ=ENV)
    task = IncidentObjectsTask(video_url=lambda clip: SIGNED, video_duration=lambda clip: 30.0)
    from evalv2.core.config import RunConfig

    config = RunConfig.model_validate({
        "experiment": "v2-dev-redact", "split": {"version": "split-v2", "half": "dev"},
        "subjects": [{"id": "super-direct", "adapter": "openai_compat", "endpoint": "switchyard", "model": MODEL}],
        "tasks": {"incident_objects": {}}, "repeats": 1, "seed": 1,
        "caps": [{"ledger": "inference", "max_attempts": 10}]})
    item = task.plan(config, {"clips": ["Assault001"]})[0]
    ctx = CallContext(store=ArtifactStore(tmp_path / "store"), ledger=BudgetLedger([Cap("inference", max_attempts=9)]),
                      policy=POLICIES["retry-v1"], subject="super-direct", task="incident_objects",
                      sleep=lambda s: None)
    record = task.execute(item, adapter, ctx)
    assert record.failure["kind"] == "http_error" and "r2.example/videos/Assault001.mp4" in record.failure["detail"]
    assert SIGNATURE not in json.dumps(record.failure), "the prediction record keeps no signature"
    assert SIGNATURE.encode() not in _stored_bytes(tmp_path / "store")


def test_transport_errors_naming_a_signed_url_are_redacted(tmp_path):
    def refused(request):
        raise httpx.ConnectError(f"could not reach {SIGNED}")

    adapter = OpenAICompatAdapter("super-direct", MODEL, ENDPOINTS["switchyard"], capabilities_for(MODEL),
                                  transport=httpx.MockTransport(refused), environ=ENV)
    ctx = CallContext(store=ArtifactStore(tmp_path / "store"), ledger=BudgetLedger([Cap("inference", max_attempts=9)]),
                      policy=POLICIES["retry-v1"], subject="super-direct", task="incident_objects",
                      sleep=lambda s: None)
    result = adapter.complete(build_p1_request(MODEL, SIGNED), ctx)
    assert result.failure.kind == "transport_error" and SIGNATURE not in result.failure.detail


def test_an_agent_error_quoting_the_signed_url_is_stored_without_it(tmp_path):
    detail = f"Analysis failed: 403, message='Forbidden', url='{SIGNED}'"
    ctx = _ctx(tmp_path)
    result = VssAgentAdapter("super-b", MODEL, post=FakeAgent((500, {"detail": detail}))).analyze(
        "Assault001", _request(), ctx)
    assert result.outcome == "request_failed" and SIGNATURE not in (result.error or "")
    assert "r2.example/videos/Assault001.mp4" in result.error
    stored = ctx.store.get(ArtifactRef.parse(result.response_ref))
    assert SIGNATURE not in json.dumps(stored) and SIGNATURE.encode() not in _stored_bytes(tmp_path / "store")


def test_redact_text_leaves_text_without_urls_alone():
    assert redact_text("timed out after 300 s") == "timed out after 300 s"
    assert redact_text(None) == ""
