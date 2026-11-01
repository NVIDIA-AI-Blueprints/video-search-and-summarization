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

"""Track B: the experiment-agent adapter's accounting and pinning, the pipeline task, and stage scoring (fake agent)."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import httpx
import pytest

from evalv2.adapters.base import AdapterConfigError, CallContext
from evalv2.adapters.registry import build_pipeline_adapter
from evalv2.adapters.vss import AgentDriftError, AgentRefusedError, ProfileMismatchError, VssAgentAdapter
from evalv2.core.artifacts import ArtifactRef, ArtifactStore
from evalv2.core.budget import BudgetExceeded, BudgetLedger, Cap
from evalv2.core.config import RunConfig
from evalv2.core.netguard import no_network
from evalv2.core.retry import POLICIES
from evalv2.requests import vss as requests_vss
from evalv2.tasks import TASKS
from evalv2.tasks.incident_objects import v1
from evalv2.tasks.incident_objects.scoring import embedder_similarity
from evalv2.tasks.pipeline import scoring
from evalv2.tasks.pipeline.runner import PipelineTask, undo_zoom

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v2_incident_objects import _embed, _report, _sheets, v2_judge  # noqa: E402

MODEL = "nvidia/cosmos-3-super-reasoner"
SIGNED = "https://r2.example/videos/Assault001.mp4?X-Amz-Signature=secret&X-Amz-Expires=3600"
PRODUCTION = requests_vss.PROFILES["production-v4"]


def _provenance(sha="abc123", flags=None, stages=None):
    return {"schema": "incident-run-provenance-v1",
            "agent": {"git_sha": sha, "source": "git", "dirty": False, "package_version": "0.1.0"},
            "contract_version": "incident-contract-v4",
            "flags": {**requests_vss.PRODUCTION_FLAGS, "chunking": {"enabled": False}, "blur_detector_call_cap": 40,
                      **(flags or {})},
            "stages": stages if stages is not None else {
                "p1": {"model": MODEL, "calls": 1, "total_tokens": 30000, "calls_without_usage": 0},
                "grounding": {"model": MODEL, "calls": 5, "total_tokens": 10000, "calls_without_usage": 1},
                "zoom": {"model": MODEL, "calls": 6, "total_tokens": 12000, "calls_without_usage": 0}}}


def _zoomed():
    """A run whose zoomed second look corrected E2's description (to the ground truth's wording)."""
    before = _report()
    after = copy.deepcopy(before)
    after["entities"][1]["description"] = "Person in blue jacket"
    evidence = [{"item_type": "entity", "item_id": "E2", "item_name": "human", "description": "Person in blue jacket",
                 "name_before": "human", "description_before": "Person in a blue jacket", "zoom_checked": True},
                {"item_type": "instrument", "item_id": "I1", "item_name": "stick", "description": "Wooden stick",
                 "zoom_checked": True}]
    return before, after, evidence


def _answer(provenance=None, *, with_p1_report=True):
    before, after, evidence = _zoomed()
    experiment = {"prompt_id": None, "persisted": False, "max_crop_calls": 40, "max_zoom_calls": 80,
                  "crop": {"calls": 5, "cap": 40}, "zoom": {"calls": 6, "cap": 80}}
    if with_p1_report:
        experiment["p1_report"] = before
    return 200, {"report": after, "raw_output": json.dumps(before), "finish_reason": "stop", "revision_id": None,
                 "evidence": evidence, "experiment": experiment, "provenance": provenance or _provenance()}


class FakeAgent:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, url, *, json, timeout):
        self.calls.append((url, json))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        status, body = reply
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))


def _ctx(tmp_path, caps=None):
    caps = caps if caps is not None else [Cap("inference", max_attempts=10), Cap("grounding", max_attempts=200),
                                          Cap("zoom", max_attempts=400)]
    return CallContext(store=ArtifactStore(tmp_path / "store"), ledger=BudgetLedger(caps, tmp_path / "ledger.jsonl"),
                       policy=POLICIES["retry-v2"], subject="super-b", task="pipeline", sleep=lambda s: None)


def _request(profile=PRODUCTION):
    return requests_vss.build_analyze_request(profile, video_url=SIGNED, model=MODEL, duration_seconds=30.0)


# --- ledger -------------------------------------------------------------------------------------------------------

def test_check_refuses_a_worst_case_over_a_cap_and_charges_nothing(tmp_path):
    ledger = BudgetLedger([Cap("zoom", max_attempts=50)], tmp_path / "l.jsonl")
    ledger.check("zoom", subject="s", task="t", attempts=50, tokens=0)
    with pytest.raises(BudgetExceeded, match="worst case 51"):
        ledger.check("zoom", subject="s", task="t", attempts=51, tokens=0)
    assert ledger.totals("zoom").attempts == 0


def test_measured_charges_count_persist_and_replay(tmp_path):
    ledger = BudgetLedger([Cap("grounding", max_attempts=10)], tmp_path / "l.jsonl")
    ledger.charge_measured("grounding", subject="s", task="t", attempts=4, tokens=900, source="agent")
    assert (ledger.totals("grounding").attempts, ledger.totals("grounding").tokens) == (4, 900)
    with pytest.raises(BudgetExceeded):
        ledger.check("grounding", subject="s", task="t", attempts=7, tokens=0)
    resumed = BudgetLedger([Cap("grounding", max_attempts=10)], tmp_path / "l.jsonl")
    assert resumed.totals("grounding").attempts == 4, "a resumed run starts from the measured counts"
    with pytest.raises(ValueError):
        ledger.charge_measured("grounding", subject="s", task="t", attempts=-1, tokens=0)
    with pytest.raises(ValueError):
        ledger.charge_measured("detector", subject="s", task="t", attempts=1, tokens=0)


# --- requests -----------------------------------------------------------------------------------------------------

def test_the_production_profile_sends_the_p4_experiment_fields():
    request = _request()
    assert request["body"] == {"video_url": SIGNED, "model": MODEL, "skip_rp1": True, "max_crop_calls": 40,
                               "max_zoom_calls": 80, "video_duration_seconds": 30.0}
    accounting = request["accounting"]
    assert accounting["worst_case"] == {"inference": [2, 70000], "grounding": [40, 240000], "zoom": [80, 320000]}
    assert accounting["stages"] == ["grounding", "p1", "zoom"]
    assert accounting["expected_flags"]["zoom_names"] is True and accounting["expected_flags"]["tracking"] is False


def test_profiles_are_versioned_and_bounded():
    tracked = requests_vss.build_analyze_request(requests_vss.PROFILES["production-v4-object-tracking"],
                                                 video_url=SIGNED, model=MODEL, duration_seconds=None)
    assert tracked["body"]["experiment_object_tracking"] is True and "video_duration_seconds" not in tracked["body"]
    assert tracked["accounting"]["worst_case"]["object_tracking"] == [80, 480000]
    assert requests_vss.profile_for("production-v4", {"max_zoom_calls": 20}).max_zoom_calls == 20
    with pytest.raises(ValueError, match="new profile"):
        requests_vss.profile_for("production-v4", {"object_tracking": True})
    with pytest.raises(ValueError, match="unknown Track B profile"):
        requests_vss.profile_for("production-v5")
    for bad in (0, 201):
        with pytest.raises(ValueError, match="max_zoom_calls"):
            requests_vss.VssProfile("x", max_crop_calls=10, max_zoom_calls=bad)


# --- adapter ------------------------------------------------------------------------------------------------------

def test_an_answer_is_charged_stage_by_stage_and_the_agent_is_pinned(tmp_path):
    agent = FakeAgent(_answer())
    ctx = _ctx(tmp_path)
    adapter = VssAgentAdapter("super-b", MODEL, post=agent)
    result = adapter.analyze("Assault001", _request(), ctx)
    assert result.outcome == "answered" and agent.calls[0][0] == "http://127.0.0.1:8765/api/v1/incidents/Assault001/analyze"
    assert result.charged == {"inference": {"attempts": 1, "tokens": 30000},
                              "grounding": {"attempts": 5, "tokens": 16000}, "zoom": {"attempts": 6, "tokens": 12000}}
    assert ctx.ledger.totals("grounding").tokens == 16000, "an attempt without usage is charged at the bound"
    assert adapter.pinned["agent"]["git_sha"] == "abc123"
    stored = ctx.store.get(ArtifactRef.parse(result.request_ref))
    assert stored["body"]["video_url"] == "https://r2.example/videos/Assault001.mp4", "the signature is never stored"
    assert "secret" not in json.dumps(stored) and agent.calls[0][1]["video_url"] == SIGNED


def test_a_cap_that_cannot_cover_the_worst_case_sends_nothing(tmp_path):
    agent = FakeAgent()
    ctx = _ctx(tmp_path, [Cap("zoom", max_attempts=79)])
    with pytest.raises(BudgetExceeded, match="zoom"):
        VssAgentAdapter("super-b", MODEL, post=agent).analyze("Assault001", _request(), ctx)
    assert agent.calls == [] and ctx.ledger.totals("zoom").attempts == 0


def test_a_changed_agent_stops_the_run(tmp_path):
    agent = FakeAgent(_answer(), _answer(_provenance(sha="def456")))
    adapter, ctx = VssAgentAdapter("super-b", MODEL, post=agent), _ctx(tmp_path)
    adapter.analyze("Assault001", _request(), ctx)
    with pytest.raises(AgentDriftError, match="def456"):
        adapter.analyze("Assault001", _request(), ctx)
    resumed = VssAgentAdapter("super-b", MODEL, post=FakeAgent(_answer(_provenance(sha="def456"))),
                              pinned=copy.deepcopy(adapter.pinned))
    with pytest.raises(AgentDriftError):
        resumed.analyze("Assault001", _request(), ctx)


def test_flags_and_stages_outside_the_profile_stop_the_run_after_charging(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ProfileMismatchError, match="zoom_names"):
        VssAgentAdapter("super-b", MODEL, post=FakeAgent(_answer(_provenance(flags={"zoom_names": False})))).analyze(
            "Assault001", _request(), ctx)
    stages = {**_provenance()["stages"], "caption": {"calls": 3, "total_tokens": 9000, "calls_without_usage": 0}}
    with pytest.raises(ProfileMismatchError, match="caption"):
        VssAgentAdapter("super-b", MODEL, post=FakeAgent(_answer(_provenance(stages=stages)))).analyze(
            "Assault001", _request(), ctx)
    assert ctx.ledger.totals("caption").attempts == 3, "what the agent sent is charged before the run stops"


def test_rejections_failures_and_refusals(tmp_path):
    ctx = _ctx(tmp_path)
    rejected = (422, {"detail": {"message": "contract violation", "raw_output": "{}", "finish_reason": "stop",
                                 "provenance": _provenance(stages={"p1": {"calls": 2, "total_tokens": 50000}})}})
    result = VssAgentAdapter("super-b", MODEL, post=FakeAgent(rejected)).analyze("Assault001", _request(), ctx)
    assert result.outcome == "contract_rejected" and result.raw_output == "{}" and result.final_report is None
    assert result.charged == {"inference": {"attempts": 2, "tokens": 50000}}

    timeout = VssAgentAdapter("super-b", MODEL, post=FakeAgent((504, {"detail": "timed out"}))).analyze(
        "Assault001", _request(), _ctx(tmp_path / "a"))
    assert timeout.outcome == "request_failed" and timeout.charged["zoom"] == {"attempts": 80, "tokens": 320000}

    refused = httpx.ConnectError("connection refused")
    ctx_b = _ctx(tmp_path / "b")
    down = VssAgentAdapter("super-b", MODEL, post=FakeAgent(refused)).analyze("Assault001", _request(), ctx_b)
    assert down.status == 0 and down.charged == {} and ctx_b.ledger.totals("inference").attempts == 0

    lost = VssAgentAdapter("super-b", MODEL, post=FakeAgent(httpx.ReadTimeout("read timed out"))).analyze(
        "Assault001", _request(), _ctx(tmp_path / "c"))
    assert lost.charged["grounding"] == {"attempts": 40, "tokens": 240000}, "unknown calls are charged at worst case"

    with pytest.raises(AgentRefusedError, match="409"):
        VssAgentAdapter("super-b", MODEL, post=FakeAgent((409, {"detail": "R2 configured"}))).analyze(
            "Assault001", _request(), _ctx(tmp_path / "d"))
    with pytest.raises(ProfileMismatchError, match="P3"):
        VssAgentAdapter("super-b", MODEL, post=FakeAgent((200, {"report": {}, "raw_output": "{}"}))).analyze(
            "Assault001", _request(), _ctx(tmp_path / "e"))
    with pytest.raises(ValueError, match="names"):
        VssAgentAdapter("super-b", "nvidia/cosmos-3-nano", post=FakeAgent()).analyze(
            "Assault001", _request(), _ctx(tmp_path / "f"))


def test_pipeline_subjects_run_only_on_a_loopback_experiment_agent():
    from evalv2.core.config import SubjectConfig

    subject = SubjectConfig(id="super-b", adapter="vss", model=MODEL, endpoint="experiment-agent",
                            profile="production-v4")
    assert build_pipeline_adapter(subject, environ={}).base_url == "http://127.0.0.1:8765"
    with pytest.raises(AdapterConfigError, match="shared VM agent"):
        build_pipeline_adapter(subject, environ={"EVALV2_VSS_AGENT_URL": "http://kwanz-ws:8000"})
    with pytest.raises(AdapterConfigError, match="experiment agent"):
        build_pipeline_adapter(subject.model_copy(update={"endpoint": "switchyard"}), environ={})
    with pytest.raises(ValueError, match="pipeline adapter"):
        build_pipeline_adapter(subject.model_copy(update={"adapter": "openai_compat"}), environ={})


# --- task ---------------------------------------------------------------------------------------------------------

def _config(subjects=None, caps=None):
    return RunConfig.model_validate({
        "experiment": "v2-dev-track-b", "split": {"version": "split-v2", "half": "dev"},
        "subjects": subjects or [{"id": "super-b", "adapter": "vss", "model": MODEL, "endpoint": "experiment-agent",
                                  "profile": "production-v4"}],
        "tasks": {"pipeline": {}}, "repeats": 1, "seed": 1,
        "caps": caps if caps is not None else [{"ledger": "inference", "max_attempts": 4},
                                               {"ledger": "grounding", "max_attempts": 80},
                                               {"ledger": "zoom", "max_attempts": 160}]})


def test_the_task_plans_pipeline_subjects_and_preflights_their_worst_case():
    from evalv2 import cli

    task = TASKS["pipeline"]()
    items = task.plan(_config(), {"clips": ["Assault001", "Burglary002"]})
    assert [i.spec["profile"] for i in items] == ["production-v4"] * 2
    assert task.budget_estimate(_config(), items)["zoom"] == {"attempts": 160, "tokens": 640000}
    assert cli.preflight(_config(), {"pipeline": items}) == [], "the agent's retries are inside the worst case"
    tight = _config(caps=[{"ledger": "inference", "max_attempts": 4}, {"ledger": "grounding", "max_attempts": 80}])
    assert any("ledger 'zoom'" in p for p in cli.preflight(tight, {"pipeline": items}))
    with pytest.raises(ValueError, match="pipeline subjects"):
        task.plan(_config([{"id": "super", "adapter": "openai_compat", "model": MODEL, "endpoint": "switchyard"}]),
                  {"clips": ["Assault001"]})
    with pytest.raises(ValueError, match="unknown Track B profile"):
        task.plan(_config([{"id": "b", "adapter": "vss", "model": MODEL, "profile": "nope"}]), {"clips": ["x"]})


def _execute(tmp_path, *replies):
    task = PipelineTask(video_url=lambda clip: SIGNED, video_duration=lambda clip: 30.0)
    item = task.plan(_config(), {"clips": ["Assault001"]})[0]
    ctx = _ctx(tmp_path)
    record = task.execute(item, VssAgentAdapter("super-b", MODEL, post=FakeAgent(*replies)), ctx)
    stored = ctx.store.get(ArtifactRef.parse(record.prediction_ref)) if record.prediction_ref else None
    return record, stored


def test_an_answered_run_stores_three_stages_evidence_and_provenance(tmp_path):
    record, stored = _execute(tmp_path, _answer())
    before, after, evidence = _zoomed()
    assert record.outcome == "scored" and stored["policy_agrees"] is True
    assert stored["predictions"]["p1_agent"] == before and stored["predictions"]["final"] == after
    assert stored["predictions"]["p1"]["entities"][1]["description"] == "Person in a blue jacket"
    assert stored["p1_agent_source"] == "experiment.p1_report" and stored["zoom_undo_consistent"] is True
    assert stored["evidence"] == evidence and "p1_report" not in stored["experiment"]
    assert stored["caption_pass"] == {"ran": False, "calls": 0}
    assert record.provenance["track"] == "B" and record.provenance["stages"]["zoom"]["calls"] == 6
    assert record.provenance["profile"]["id"] == "production-v4"
    assert record.usage["ledgers"]["zoom"] == {"attempts": 6, "tokens": 12000}


def test_without_p1_report_the_zoom_is_undone_from_the_evidence(tmp_path):
    record, stored = _execute(tmp_path, _answer(with_p1_report=False))
    assert stored["p1_agent_source"] == "zoom_undone" and stored["zoom_undo_consistent"] is None
    assert stored["predictions"]["p1_agent"] == _zoomed()[0]
    before, after, evidence = _zoomed()
    assert undo_zoom(after, evidence) == before and undo_zoom(after, []) == after


def test_rejected_and_failed_runs_are_labelled_as_v1_labels_them(tmp_path):
    rejected = (422, {"detail": {"raw_output": json.dumps({"incident": {"type": "assault"}}), "finish_reason": "stop",
                                 "provenance": _provenance(stages={"p1": {"calls": 1, "total_tokens": 9000}})}})
    record, stored = _execute(tmp_path / "a", rejected)
    assert record.outcome == "contract_invalid" and stored["predictions"]["final"] is None
    assert stored["predictions"]["p1_agent"] is None and stored["agent_verdict"] == "contract_rejected"
    record, stored = _execute(tmp_path / "b", (504, {"detail": "Incident analysis timed out"}))
    assert record.outcome == "timeout" and stored is None and record.failure["kind"] == "timeout"
    with pytest.raises(RuntimeError, match="approval-gated"):
        TASKS["pipeline"]().execute(PipelineTask().plan(_config(), {"clips": ["Assault001"]})[0],
                                    VssAgentAdapter("super-b", MODEL, post=FakeAgent()), _ctx(tmp_path / "c"))


# --- scoring ------------------------------------------------------------------------------------------------------

def test_stages_score_like_track_a_and_the_zoom_effect_is_paired(tmp_path):
    record, stored = _execute(tmp_path, _answer())
    gt = v1.gt_store(_sheets())
    similarity = embedder_similarity(_embed)
    with no_network():
        scored = {stage: scoring.score_stage(gt, stored, stage, incident_id="Assault001", run_id=record.work_key,
                                             similarity=similarity, judge=v2_judge)
                  for stage in ("p1", "p1_agent", "final")}
    assert scored["p1"]["entities"]["counts"] == scored["p1_agent"]["entities"]["counts"]
    before, after = scoring.run_metrics(scored["p1_agent"]), scoring.run_metrics(scored["final"])
    assert before["entities.description_pass"] == 0.5 and after["entities.description_pass"] == 1.0
    effect = scoring.paired_effect({"r1": scored["p1_agent"]}, {"r1": scored["final"]})
    assert effect["metrics"]["entities.description_pass"] == {"pairs": 1, "mean_before": 0.5, "mean_after": 1.0,
                                                              "mean_delta": 0.5, "improved": 1, "worsened": 0,
                                                              "unchanged": 0}
    assert effect["metrics"]["incident.description_pass"]["unchanged"] == 1


def test_a_stage_without_a_report_is_unscored_and_left_out_of_the_pairs(tmp_path):
    gt = v1.gt_store(_sheets())
    artifact = {"predictions": {"p1": v1.empty_prediction(), "p1_agent": None, "final": None}}
    assert scoring.score_stage(gt, artifact, "final", incident_id="Assault001", run_id="r", similarity=None,
                               judge=v2_judge) is None
    with pytest.raises(ValueError, match="stage"):
        scoring.score_stage(gt, artifact, "captions", incident_id="Assault001", run_id="r", similarity=None,
                            judge=v2_judge)
    effect = scoring.paired_effect({"r1": {}, "r2": {}}, {"r1": {}})
    assert effect["runs_paired"] == 1 and effect["only_before"] == 1
