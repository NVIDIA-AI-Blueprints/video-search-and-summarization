# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Run provenance: every HTTP attempt of every stage is counted, failed and refused ones included."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import httpx
import numpy as np
import pytest

from vss_agents import incident_contract
from vss_agents.tools.incident_report_gen import IncidentReportGenInput
from vss_agents.tools.incident_report_gen import incident_report_gen
from vss_agents.tools.incident_report_gen import run_p1
from vss_agents.utils import crop_evidence
from vss_agents.utils import run_provenance

from ..tools.test_incident_report_gen import MODEL_OUTPUT
from ..tools.test_incident_report_gen import VIDEO_URL
from ..tools.test_incident_report_gen import _config


class _Reply:
    def __init__(self, status: int, payload: dict | None = None, usage: dict | None = None) -> None:
        self.status_code = status
        self.headers: dict[str, str] = {"retry-after": "0"}
        self._payload = {**(payload or {}), **({"usage": usage} if usage else {})}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=MagicMock(), response=MagicMock())


def _content(obj: dict) -> dict:
    return {"choices": [{"message": {"content": json.dumps(obj)}}]}


def _detector() -> crop_evidence.DefaultDetector:
    return crop_evidence.DefaultDetector(model_name="grounder", base_url="http://golden.invalid", api_key="k")


def test_meters_count_calls_failures_tokens_and_time():
    with run_provenance.collecting() as provenance:
        run_provenance.record_call(
            "p1", ok=True, usage={"prompt_tokens": 5, "completion_tokens": 7}, elapsed_ms=10, model="m"
        )
        run_provenance.record_call("p1", ok=False, usage=None, elapsed_ms=2.5)
        run_provenance.record_refused("grounding")
        provenance.use("rp1", model="r", prompts={"contract/report_generation_prompt": "a" * 64})
    stages = provenance.to_dict(contract_version="incident-contract-v4", flags={})["stages"]
    assert stages["p1"] == {
        "model": "m",
        "prompts": {},
        "calls": 2,
        "failed": 1,
        "refused": 0,
        "prompt_tokens": 5,
        "completion_tokens": 7,
        "total_tokens": 12,
        "calls_without_usage": 1,
        "elapsed_ms": 12.5,
    }
    assert stages["grounding"]["refused"] == 1 and stages["rp1"]["calls"] == 0
    with pytest.raises(ValueError, match="unknown stage"):
        provenance.stage("judge")


def test_recording_outside_an_analysis_is_a_no_op():
    assert run_provenance.current() is None
    run_provenance.record_call("p1", ok=True, usage=None, elapsed_ms=1)  # nothing to record into, nothing raised


def test_detector_calls_are_counted_in_worker_threads_under_the_current_stage(monkeypatch):
    replies = iter(
        [
            _Reply(429),
            _Reply(200, {"choices": [{"message": {"content": "not json"}}]}),
            _Reply(200, _content({"bbox_2d": [1, 2, 300, 400]}), {"total_tokens": 900}),
            _Reply(200, _content({"detections": []}), {"total_tokens": 50}),
        ]
    )
    monkeypatch.setattr(crop_evidence.httpx, "post", lambda *_a, **_k: next(replies))
    monkeypatch.setattr(crop_evidence.time, "sleep", lambda _s: None)
    frame = np.zeros((20, 20, 3), dtype=np.uint8)

    async def run() -> run_provenance.RunProvenance:
        with run_provenance.collecting() as provenance:
            await asyncio.to_thread(_detector().detect, frame, "stick")
            with run_provenance.stage_scope("privacy_heads"):
                await asyncio.to_thread(_detector().detect_all, frame, "every human head or face")
        return provenance

    stages = asyncio.run(run()).to_dict(contract_version="v", flags={})["stages"]
    assert stages["grounding"]["calls"] == 3 and stages["grounding"]["failed"] == 2
    assert stages["grounding"]["model"] == "grounder" and stages["grounding"]["total_tokens"] == 900
    assert stages["privacy_heads"]["calls"] == 1 and stages["privacy_heads"]["total_tokens"] == 50


def test_zoom_calls_are_counted_as_zoom_and_a_refused_attempt_as_refused(monkeypatch):
    look = _content({"name": "stick", "description": "a stick", "confidence": 0.9, "changed": False})
    monkeypatch.setattr(crop_evidence.httpx, "post", lambda *_a, **_k: _Reply(200, look, {"total_tokens": 10}))
    with run_provenance.collecting() as provenance:
        _detector().zoom_name(
            b"close", item_type="instrument", name="stick", description="a stick", context_jpeg=b"frame"
        )
        budget = crop_evidence.CropCallBudget(1)
        budget.acquire()  # the cap is used up, so the next attempt is refused before sending
        with pytest.raises(crop_evidence.CropCallCapReachedError):
            _detector().with_call_budget(budget)._request_box({"model": "grounder"})
    stages = provenance.to_dict(contract_version="v", flags={})["stages"]
    assert stages["zoom"]["calls"] == 3 and stages["zoom"]["total_tokens"] == 30, "two looks and the merge"
    assert stages["grounding"]["refused"] == 1 and stages["grounding"]["calls"] == 0


@pytest.mark.asyncio
async def test_p1_attempts_are_counted_with_their_usage_and_failures():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(MODEL_OUTPUT)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 6000, "completion_tokens": 800},
            },
        )

    with run_provenance.collecting() as provenance:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await run_p1(client, _config(), VIDEO_URL, video_duration_seconds=30)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(503, text="busy"))) as c:
            with pytest.raises(RuntimeError, match="HTTP 503"):
                await run_p1(c, _config(), VIDEO_URL)
    p1 = provenance.to_dict(contract_version="v", flags={})["stages"]["p1"]
    assert p1["calls"] == 2 and p1["failed"] == 1 and p1["prompt_tokens"] == 6000 and p1["completion_tokens"] == 800
    assert p1["model"] == _config().vlm_model_name


@pytest.mark.asyncio
async def test_the_tool_returns_one_provenance_record_with_every_stage_it_used(monkeypatch):
    monkeypatch.delenv("INCIDENT_CHUNKING_ENABLED", raising=False)
    monkeypatch.delenv("INCIDENT_TRACKING_ENABLED", raising=False)
    transport = httpx.MockTransport(
        lambda _r: httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(MODEL_OUTPUT)}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 7000},
            },
        )
    )
    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    with (
        patch("vss_agents.tools.incident_report_gen.httpx.AsyncClient", side_effect=client_factory),
        patch("vss_agents.tools.incident_report_gen.collect_evidence", new_callable=AsyncMock, return_value=[]),
        patch("vss_agents.tools.incident_report_gen.persist_revision", new_callable=AsyncMock, return_value=7),
    ):
        async with incident_report_gen(_config(), MagicMock()) as function_info:
            result = await function_info.single_fn(
                IncidentReportGenInput(
                    incident_id="v1",
                    video_url=VIDEO_URL,
                    video_duration_seconds=30,
                    skip_rp1=True,
                    reviewer_prompt="look left",
                )
            )
    provenance = result.provenance
    assert provenance["schema"] == run_provenance.SCHEMA and set(provenance["agent"]) == {
        "git_sha",
        "source",
        "dirty",
        "package_version",
    }
    assert provenance["contract_version"] == incident_contract.contract_version()
    p1 = provenance["stages"]["p1"]
    assert p1["calls"] == 1 and p1["total_tokens"] == 7000
    assert p1["prompts"]["p1/reviewer-preamble"] == incident_contract.prompt_sha256("p1/reviewer-preamble")
    assert "contract/incident_extraction_prompt" in p1["prompts"]
    assert "rp1" not in provenance["stages"], "skip_rp1 declares no RP1 stage"
    assert provenance["stages"]["grounding"]["prompts"] == {
        "grounding/best-match": incident_contract.prompt_sha256("grounding/best-match")
    }
    assert set(provenance["flags"]) >= {"chunking", "tracking", "object_tracking", "set_of_marks", "zoom_names"}


def test_the_agent_identity_prefers_an_explicit_sha(monkeypatch):
    run_provenance.agent_identity.cache_clear()
    monkeypatch.setenv(run_provenance.GIT_SHA_ENV, "abc123")
    try:
        identity = run_provenance.agent_identity()
        assert identity["git_sha"] == "abc123" and identity["source"] == "env" and identity["dirty"] is None
    finally:
        run_provenance.agent_identity.cache_clear()
    monkeypatch.delenv(run_provenance.GIT_SHA_ENV)
    with patch.object(run_provenance, "_git", return_value=None):
        identity = run_provenance.agent_identity()
        assert identity["git_sha"] is None and identity["source"] is None
    run_provenance.agent_identity.cache_clear()


def test_usage_of_tolerates_unreadable_bodies():
    assert run_provenance.usage_of(SimpleNamespace(json=lambda: {"usage": {"total_tokens": 3}})) == {"total_tokens": 3}
    assert run_provenance.usage_of(SimpleNamespace(json=lambda: 1 / 0)) is None


# -- review fixes: what a stage counts as failed, malformed usage, refused blur calls, the embedding, the flags ------


@pytest.mark.parametrize(
    "usage",
    [{"prompt_tokens": "n/a", "completion_tokens": None}, "n/a", ["x"], {"total_tokens": float("nan")}, None],
)
def test_a_malformed_usage_counts_as_none_and_never_raises(usage):
    with run_provenance.collecting() as provenance:
        run_provenance.record_call("p1", ok=True, usage=usage, elapsed_ms=1)
    p1 = provenance.to_dict(contract_version="v", flags={})["stages"]["p1"]
    assert p1["calls"] == 1 and p1["total_tokens"] == 0 and p1["calls_without_usage"] == 1
    budget = crop_evidence.CropCallBudget(2)
    budget.record_response(SimpleNamespace(status_code=200, json=lambda: {"usage": usage}))
    assert budget.summary()["tokens"] == 0


def test_numeric_strings_still_count():
    assert run_provenance.token_counts({"prompt_tokens": "6", "completion_tokens": 4.0}) == (6, 4, 10)
    assert run_provenance.token_counts({"total_tokens": True}) == (0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("replies", "failed"),
    [
        # a reply the contract rejects (a 422 run) is a failed attempt
        ([{"choices": [{"message": {"content": '{"incident": {}}'}, "finish_reason": "stop"}]}], 1),
        # a runaway (no content, finish_reason=length) is a failed attempt
        ([{"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}], 1),
        # a 200 with no answer (an undecodable video) is a failed attempt
        ([None], 1),
    ],
)
async def test_a_p1_attempt_without_a_valid_report_is_a_failed_call(replies, failed):
    from vss_agents.tools.incident_report_gen import IncidentExtractionError
    from vss_agents.tools.incident_report_gen import VlmNoAnswerError

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=json.dumps(replies[0]).encode(), headers={"content-type": "application/json"}
        )

    with run_provenance.collecting() as provenance:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises((IncidentExtractionError, VlmNoAnswerError)):
                await run_p1(client, _config(), VIDEO_URL, video_duration_seconds=30)
    p1 = provenance.to_dict(contract_version="v", flags={})["stages"]["p1"]
    assert p1["calls"] == 1 and p1["failed"] == failed


def test_a_zoom_reply_the_strict_check_rejects_is_a_failed_call(monkeypatch):
    no_name = _content({"description": "a stick", "confidence": 0.9})  # a look reply must name the object
    monkeypatch.setattr(crop_evidence.httpx, "post", lambda *_a, **_k: _Reply(200, no_name, {"total_tokens": 10}))
    with run_provenance.collecting() as provenance:
        with pytest.raises(ValueError):
            _detector().zoom_name(
                b"close", item_type="instrument", name="stick", description="a stick", context_jpeg=b"frame"
            )
    zoom = provenance.to_dict(contract_version="v", flags={})["stages"]["zoom"]
    assert zoom["calls"] == 1 and zoom["failed"] == 1


@pytest.mark.parametrize("status", [101, 302])
def test_a_reply_that_is_not_2xx_is_a_failed_call(monkeypatch, status):
    reply = _Reply(status, _content({"bbox_2d": [1, 2, 3, 4]}))
    reply.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("x", request=MagicMock(), response=MagicMock())
    )
    monkeypatch.setattr(crop_evidence.httpx, "post", lambda *_a, **_k: reply)
    with run_provenance.collecting() as provenance:
        with pytest.raises(httpx.HTTPStatusError):
            _detector()._request_box({"model": "grounder"})
    grounding = provenance.to_dict(contract_version="v", flags={})["stages"]["grounding"]
    assert grounding["calls"] == 1 and grounding["failed"] == 1


@pytest.mark.asyncio
async def test_the_revision_embedding_is_metered_as_its_own_stage():
    from vss_agents.utils.embeddings import RemoteTextEmbedder

    calls = iter([httpx.Response(503, text="busy"), None])

    def handler(request: httpx.Request) -> httpx.Response:
        reply = next(calls)
        if reply is not None:
            return reply
        size = len(json.loads(request.content)["input"])
        return httpx.Response(
            200,
            json={"data": [{"index": i, "embedding": [0.0] * 1024} for i in range(size)], "usage": {"total_tokens": 9}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        embedder = RemoteTextEmbedder(api_key="k", base_url="http://embed.invalid/v1", http_client=client)
        with patch("vss_agents.utils.embeddings.asyncio.sleep", new_callable=AsyncMock):
            with run_provenance.collecting() as provenance:
                await embedder.embed_passage("a report")
    embedding = provenance.to_dict(contract_version="v", flags={})["stages"]["embedding"]
    assert embedding["calls"] == 2 and embedding["failed"] == 1 and embedding["total_tokens"] == 9


def test_the_flags_say_what_the_analysis_ran_over_the_agents_settings():
    settings = {"chunking": {"enabled": True, "window_seconds": 20}, "tracking": True, "experiments": True}
    with run_provenance.collecting() as provenance:
        provenance.ran(experiment=True, chunking=False, tracking=False, object_tracking=True)
    flags = provenance.to_dict(contract_version="v", flags=settings)["flags"]
    assert flags == {
        "chunking": {"enabled": False, "window_seconds": 20},
        "tracking": False,
        "experiments": True,
        "experiment": True,
        "object_tracking": True,
    }


@pytest.mark.asyncio
async def test_an_experiment_reports_the_stages_it_ran_not_the_agents_settings(monkeypatch):
    monkeypatch.setenv("INCIDENT_EXPERIMENTS_ENABLED", "1")
    monkeypatch.setenv("INCIDENT_TRACKING_ENABLED", "1")
    monkeypatch.setenv("INCIDENT_CHUNKING_ENABLED", "1")
    transport = httpx.MockTransport(
        lambda _r: httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(MODEL_OUTPUT)}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 7000},
            },
        )
    )
    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    with patch("vss_agents.tools.incident_report_gen.httpx.AsyncClient", side_effect=client_factory):
        async with incident_report_gen(_config(), MagicMock()) as function_info:
            result = await function_info.single_fn(
                IncidentReportGenInput(
                    incident_id="v1", video_url=VIDEO_URL, video_duration_seconds=30, skip_rp1=True, skip_crops=True
                )
            )
    flags = result.provenance["flags"]
    assert flags["experiment"] is True and flags["experiments"] is True
    assert flags["tracking"] is False and flags["set_of_marks"] is False and flags["object_tracking"] is False
    assert flags["chunking"]["enabled"] is False, "the experiment ran no caption pass, whatever the agent's setting"
    assert flags["zoom_names"] is False and flags["privacy_blur"] is False, "skip_crops: no crop, no zoom, no blur"
    assert "caption" not in result.provenance["stages"]
