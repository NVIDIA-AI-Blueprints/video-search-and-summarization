# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Adapters: request parity with V1, accounting, failure classes, and no secrets in artifacts (no network)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

from evalv2.adapters.base import AdapterConfigError, CallContext, Capabilities, ModelAdapter
from evalv2.adapters.fake import FakeAdapter, Scripted
from evalv2.adapters.openai_compat import ENDPOINTS, OpenAICompatAdapter
from evalv2.adapters.registry import build_adapter, capabilities_for, load_capabilities
from evalv2.core.artifacts import ArtifactStore
from evalv2.core.budget import BudgetLedger, Cap
from evalv2.core.config import SubjectConfig
from evalv2.core.hashing import canonical_json
from evalv2.core.retry import POLICIES, Failure
from evalv2.requests.p1 import base_prompt_id, build_p1_request, p1_provenance

MODEL = "nvidia/cosmos-3-super-reasoner"
KEY = "test-secret-key-never-stored"
ENV = {"VLM_GATEWAY_BASE_URL": "https://gateway.example/v1", "VLM_GATEWAY_API_KEY": KEY}
COMPLETION = {"choices": [{"message": {"role": "assistant", "content": '{"incident": {}}'}, "finish_reason": "stop"}],
              "usage": {"prompt_tokens": 8000, "completion_tokens": 900}}


def _v1():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import eval_vlm_client
    return eval_vlm_client


@pytest.mark.parametrize("model", [MODEL, "nvidia/cosmos-3-nano-reasoner"])
@pytest.mark.parametrize("prompt_id", [None, "p1-v4-e01-severity"])
def test_the_p1_request_equals_v1s_payload(model, prompt_id):
    import prompt_registry

    v1 = _v1()
    prompt = None if prompt_id is None else prompt_registry.prompt_text(prompt_id)
    expected = v1.p1_request_payload(model, "https://r2.example/A.mp4?sig", prompt_text=prompt)
    built = build_p1_request(model, "https://r2.example/A.mp4?sig", prompt_id=prompt_id)
    assert canonical_json(built) == canonical_json(expected)


def test_p1_provenance_pins_prompt_contract_and_settings():
    record = p1_provenance()
    assert record["prompt_id"] == base_prompt_id() == "p1-v4-base"
    assert len(record["prompt_sha256"]) == len(record["response_format_sha256"]) == 64
    assert p1_provenance("p1-v4-e01-severity")["prompt_sha256"] != record["prompt_sha256"]


class _Server:
    """A scripted HTTP server for httpx.MockTransport; records every request it receives."""

    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


DEFAULT_CAPS = (Cap("inference", max_attempts=10),)


def _ctx(tmp_path, *, policy="retry-v2", caps=DEFAULT_CAPS, sleeps=None):
    return CallContext(store=ArtifactStore(tmp_path / "store"), ledger=BudgetLedger(list(caps)),
                       policy=POLICIES[policy], subject="super-direct", task="incident", tokens_bound=20000,
                       sleep=(sleeps.append if sleeps is not None else (lambda s: None)))


def _adapter(server):
    return OpenAICompatAdapter("super-direct", MODEL, ENDPOINTS["switchyard"], capabilities_for(MODEL),
                               transport=httpx.MockTransport(server), environ=ENV)


def _request():
    return build_p1_request(MODEL, "https://r2.example/A.mp4?sig")


def test_a_successful_call_stores_artifacts_and_settles_measured_tokens(tmp_path):
    server = _Server(httpx.Response(200, json=COMPLETION))
    ctx = _ctx(tmp_path)
    result = _adapter(server).complete(_request(), ctx)
    assert result.ok and result.text == '{"incident": {}}' and result.finish_reason == "stop"
    assert result.usage.total_tokens == 8900 and ctx.ledger.totals("inference").tokens == 8900
    assert ctx.store.get(result.request_ref) == _request() and ctx.store.get(result.response_ref) == COMPLETION
    assert str(server.requests[0].url) == "https://gateway.example/v1/chat/completions"
    assert server.requests[0].headers["authorization"] == f"Bearer {KEY}"
    stored = b"".join(p.read_bytes() for p in (tmp_path / "store").rglob("*") if p.is_file())
    assert KEY.encode() not in stored  # the key is sent, never stored


def test_a_rate_limit_is_retried_after_retry_after_and_every_attempt_is_charged(tmp_path):
    sleeps = []
    server = _Server(httpx.Response(429, headers={"Retry-After": "90"}, text="slow down"),
                     httpx.Response(200, json=COMPLETION))
    ctx = _ctx(tmp_path, sleeps=sleeps)
    result = _adapter(server).complete(_request(), ctx)
    assert result.ok and sleeps == [90.0] and len(result.attempts) == 2
    assert ctx.ledger.totals("inference").attempts == 2
    assert result.attempts[0]["failure"] == "rate_limited" and result.attempts[1]["timing"]["waited_ms"] == 90000.0


@pytest.mark.parametrize(("policy", "attempts"), [("retry-v2", 3), ("retry-v1", 1)])
def test_a_gateway_timeout_is_a_timeout_and_follows_the_policy(tmp_path, policy, attempts):
    server = _Server(*[httpx.Response(524, text="origin timeout")] * 3)
    result = _adapter(server).complete(_request(), _ctx(tmp_path, policy=policy))
    assert result.failure.kind == "timeout" and len(server.requests) == attempts


def test_transport_errors_and_non_completions_are_classified(tmp_path):
    result = _adapter(_Server(httpx.ConnectError("refused"))).complete(_request(), _ctx(tmp_path, policy="retry-v1"))
    assert result.failure.kind == "transport_error" and result.text is None
    server = _Server(httpx.Response(200, content=b"null", headers={"content-type": "application/json"}))
    result = _adapter(server).complete(_request(), _ctx(tmp_path / "b"))
    assert result.failure.kind == "invalid_response" and len(server.requests) == 1  # never retried


def test_a_call_over_budget_is_never_sent(tmp_path):
    server = _Server(httpx.Response(200, json=COMPLETION))
    ctx = _ctx(tmp_path, caps=(Cap("inference", max_tokens=1000),))  # bound 20000 > cap
    result = _adapter(server).complete(_request(), ctx)
    assert result.failure.kind == "budget_exceeded" and server.requests == []


def test_a_missing_endpoint_or_key_fails_before_anything_is_charged(tmp_path):
    ctx = _ctx(tmp_path)
    adapter = OpenAICompatAdapter("s", MODEL, ENDPOINTS["switchyard"], Capabilities(), environ={})
    with pytest.raises(AdapterConfigError, match="VLM_GATEWAY_BASE_URL"):
        adapter.complete(_request(), ctx)
    assert ctx.ledger.totals("inference").attempts == 0


def test_a_request_for_another_model_is_refused(tmp_path):
    with pytest.raises(ValueError, match="adapter serves"):
        _adapter(_Server()).complete({**_request(), "model": "other"}, _ctx(tmp_path))


def _conforms(adapter: ModelAdapter, ctx: CallContext) -> None:
    """The contract every adapter meets: protocol, artifacts, usage, attempts and budget charged."""
    assert isinstance(adapter, ModelAdapter)
    result = adapter.complete({"model": adapter.model, "messages": []}, ctx)
    assert result.ok and ctx.store.exists(result.request_ref) and ctx.store.exists(result.response_ref)
    assert result.usage.provider_reported and result.attempts and ctx.ledger.totals("inference").attempts >= 1
    json.dumps(result.to_dict())  # serialisable for the run record


def test_every_adapter_meets_the_conformance_contract(tmp_path):
    _conforms(_adapter(_Server(httpx.Response(200, json=COMPLETION))), _ctx(tmp_path / "a"))
    fake = FakeAdapter("fake", "fake-model", [Scripted(failure=Failure("timeout")),
                                              Scripted(text="{}", usage={"prompt_tokens": 1, "completion_tokens": 1})])
    _conforms(fake, _ctx(tmp_path / "b"))
    assert fake.calls == 2


def test_capabilities_are_unverified_until_checked_and_decide_support():
    table = load_capabilities()
    assert table and all(not caps.verified for caps in table.values())
    assert table[MODEL].missing({"video_input", "json_schema", "bbox"}) == []
    assert table["nvidia/nemotron-3-ultra"].missing({"video_input"}) == ["video_input"]  # reported unsupported
    assert capabilities_for("unknown/model").missing({"video_input"}) == ["video_input"]
    with pytest.raises(ValueError):
        Capabilities().missing({"telepathy"})


def test_the_registry_builds_adapters_from_subject_configs():
    subject = SubjectConfig(id="super-direct", adapter="openai_compat", endpoint="switchyard", model=MODEL)
    adapter = build_adapter(subject, environ=ENV, transport=httpx.MockTransport(_Server()))
    assert adapter.model == MODEL and adapter.capabilities.bbox
    with pytest.raises(ValueError, match="unknown endpoint"):
        build_adapter(SubjectConfig(id="x", adapter="openai_compat", endpoint="nowhere", model=MODEL))
    with pytest.raises(ValueError, match="unknown adapter"):
        build_adapter(SubjectConfig(id="x", adapter="carrier_pigeon", model=MODEL))
