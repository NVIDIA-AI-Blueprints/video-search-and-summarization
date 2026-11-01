# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regressions from the Evaluation V2 review of the adapters: V1's per-attempt token bound and host-suspend relabel."""

from __future__ import annotations

import httpx
import pytest

from evalv2.adapters import openai_compat
from evalv2.adapters.base import P1_TOKENS_CEILING, CallContext
from evalv2.adapters.openai_compat import ENDPOINTS, OpenAICompatAdapter
from evalv2.adapters.registry import capabilities_for
from evalv2.core.artifacts import ArtifactStore
from evalv2.core.budget import BudgetLedger, Cap
from evalv2.core.checkpoint import Checkpoint
from evalv2.core.instrument import Stopwatch
from evalv2.core.retry import POLICIES
from evalv2.requests.p1 import build_p1_request

MODEL = "nvidia/cosmos-3-super-reasoner"
ENV = {"VLM_GATEWAY_BASE_URL": "https://gateway.example/v1", "VLM_GATEWAY_API_KEY": "test-key"}
COMPLETION = {"choices": [{"message": {"role": "assistant", "content": "{}"}, "finish_reason": "stop"}],
              "usage": {"prompt_tokens": 8000, "completion_tokens": 900}}


def _adapter(*responses):
    queue = list(responses)
    sent = []

    def handler(request):
        sent.append(request)
        return queue.pop(0)

    adapter = OpenAICompatAdapter("super-direct", MODEL, ENDPOINTS["switchyard"], capabilities_for(MODEL),
                                  transport=httpx.MockTransport(handler), environ=ENV)
    return adapter, sent


def _ctx(tmp_path, *, policy="retry-v2", caps=(Cap("inference", max_attempts=10),)):
    """The run's context, with the default per-attempt token bound (nothing passed)."""
    return CallContext(store=ArtifactStore(tmp_path / "store"), ledger=BudgetLedger(list(caps)),
                       policy=POLICIES[policy], subject="super-direct", task="incident", sleep=lambda s: None)


# --- A1: the default bound is V1's P1 ceiling ----------------------------------------------------------------------


def test_the_default_token_bound_is_v1s_p1_ceiling(tmp_path):
    from experiment_runner import P1_TOKENS_CEILING as V1_CEILING

    assert P1_TOKENS_CEILING == V1_CEILING == 35000
    assert _ctx(tmp_path).tokens_bound == V1_CEILING


def test_an_attempt_without_usage_is_charged_the_full_p1_ceiling(tmp_path):
    adapter, _ = _adapter(httpx.Response(524, text="origin timeout"))
    ctx = _ctx(tmp_path, policy="retry-v1")
    assert adapter.complete(build_p1_request(MODEL, "https://r2.example/A.mp4"), ctx).failure.kind == "timeout"
    assert ctx.ledger.totals("inference").tokens == 35000, "a timeout may have used a whole P1 attempt"


def test_a_tokens_cap_leaves_room_for_a_whole_attempt(tmp_path):
    # 30,000 tokens already settled: a 20,000 bound let the next attempt in although it could reach 65,000 > 60,000.
    ctx = _ctx(tmp_path, caps=(Cap("inference", max_tokens=60000),))
    ctx.ledger.settle(ctx.ledger.reserve("inference", subject="super-direct", task="incident", tokens_bound=30000),
                      tokens_used=30000)
    adapter, sent = _adapter(httpx.Response(200, json=COMPLETION))
    result = adapter.complete(build_p1_request(MODEL, "https://r2.example/A.mp4"), ctx)
    assert result.failure.kind == "budget_exceeded" and sent == []


# --- A5: any failed attempt across a host suspend is host_suspended ------------------------------------------------


@pytest.fixture
def first_attempt_suspended(monkeypatch):
    """The first attempt's wall clock runs an hour ahead of its monotonic clock (a laptop lid closed mid-call)."""
    made = []

    def stopwatch(**kwargs):
        made.append(1)
        if len(made) == 1:
            mono, wall = iter([0.0, 2.0]), iter([0.0, 3602.0])
            return Stopwatch(monotonic=lambda: next(mono), wall=lambda: next(wall), **kwargs)
        return Stopwatch(**kwargs)

    monkeypatch.setattr(openai_compat, "Stopwatch", stopwatch)


@pytest.mark.parametrize("failed", [
    httpx.Response(502, text="bad gateway"),
    httpx.Response(200, content=b"null", headers={"content-type": "application/json"}),  # an undecodable clip
], ids=["http_error", "invalid_response"])
def test_a_non_infrastructure_failure_across_a_suspend_is_host_suspended_and_retried(
        tmp_path, first_attempt_suspended, failed):
    adapter, sent = _adapter(failed, httpx.Response(200, json=COMPLETION))
    result = adapter.complete(build_p1_request(MODEL, "https://r2.example/A.mp4"), _ctx(tmp_path))
    assert result.attempts[0]["failure"] == "host_suspended", "V1's rate_limited() relabels any failed attempt"
    assert result.ok and len(sent) == 2, "retry-v2 retries host_suspended, as V1 does"


def test_under_retry_v1_the_suspended_failure_stays_open_for_recovery(tmp_path, first_attempt_suspended):
    adapter, sent = _adapter(httpx.Response(502, text="bad gateway"))
    result = adapter.complete(build_p1_request(MODEL, "https://r2.example/A.mp4"), _ctx(tmp_path, policy="retry-v1"))
    assert result.failure.kind == "host_suspended" and result.failure.http_status == 502 and len(sent) == 1
    checkpoint = Checkpoint(tmp_path / "progress.jsonl")
    checkpoint.record("k1", "http_error", failure_kind=result.failure.kind)  # V1's label for the failure
    assert not checkpoint.is_final("k1")


def test_a_non_completion_without_a_suspend_is_final_for_the_checkpoint(tmp_path):
    checkpoint = Checkpoint(tmp_path / "progress.jsonl")
    checkpoint.record("k1", "http_error", failure_kind="invalid_response")
    assert checkpoint.is_final("k1"), "JSON null for an undecodable clip is never paid for again"
