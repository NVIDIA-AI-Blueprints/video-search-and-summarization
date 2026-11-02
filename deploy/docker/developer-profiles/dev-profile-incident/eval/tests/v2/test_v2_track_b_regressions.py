# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regressions from the Evaluation V2 review of Track B: in-flight holds, P1 retries, the tracker's real cap, flags."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

from evalv2.adapters.vss import AgentDriftError, ProfileMismatchError, VssAgentAdapter
from evalv2.core.artifacts import ArtifactRef
from evalv2.core.budget import BudgetExceeded, BudgetLedger, Cap
from evalv2.requests import vss as requests_vss
from evalv2.tasks.pipeline import scoring
from evalv2.tasks.pipeline.runner import PipelineTask

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v2_track_b import MODEL, SIGNED, FakeAgent, _answer, _config, _ctx, _provenance, _request  # noqa: E402

TRACKED = requests_vss.PROFILES["production-v4-object-tracking"]


class Killed(BaseException):
    """The eval process dies while the agent is still working (not an httpx error: nothing handles it)."""


# --- B5: a request in flight is in the ledger --------------------------------------------------------------------


def test_a_run_killed_mid_call_resumes_from_counts_that_include_the_call(tmp_path):
    ctx = _ctx(tmp_path)

    def dies(url, *, json, timeout):
        raise Killed

    with pytest.raises(Killed):
        VssAgentAdapter("super-b", MODEL, post=dies).analyze("Assault001", _request(), ctx)
    resumed = BudgetLedger(ctx.ledger.caps, tmp_path / "ledger.jsonl")
    assert resumed.totals("zoom").attempts == 80 and resumed.totals("grounding").attempts == 40, (
        "the worst case was held before the POST, so the resumed run cannot re-send under a lower count")
    assert resumed.totals("inference").tokens == 70000


def test_two_requests_in_flight_cannot_both_pass_a_cap_that_covers_one(tmp_path):
    ctx = _ctx(tmp_path, [Cap("inference", max_attempts=10), Cap("grounding", max_attempts=200),
                          Cap("zoom", max_attempts=120)])  # one run's worst case is 80 zoom calls
    inner = {}

    def concurrent(url, *, json, timeout):  # a second request starts while the first is still in flight
        with pytest.raises(BudgetExceeded, match="zoom"):
            VssAgentAdapter("super-b", MODEL, post=FakeAgent()).analyze("Burglary002", _request(), ctx)
        inner["refused"] = True
        return FakeAgent(_answer())(url, json=json, timeout=timeout)

    result = VssAgentAdapter("super-b", MODEL, post=concurrent).analyze("Assault001", _request(), ctx)
    assert inner == {"refused": True} and result.outcome == "answered"
    assert ctx.ledger.totals("zoom").attempts == 6, "released to the measured calls once the reply arrived"


def test_holds_release_to_what_was_sent_and_replay_from_the_log(tmp_path):
    ledger = BudgetLedger([Cap("zoom", max_attempts=100)], tmp_path / "l.jsonl")
    hold = ledger.hold({"zoom": (80, 320000)}, subject="s", task="t")
    assert ledger.totals("zoom").attempts == 80
    with pytest.raises(BudgetExceeded):
        ledger.hold({"zoom": (80, 320000)}, subject="s", task="t")
    ledger.release(hold, {"zoom": (6, 12000), "caption": (3, 900)})
    assert (ledger.totals("zoom").attempts, ledger.totals("caption").attempts) == (6, 3)
    with pytest.raises(ValueError, match="already released"):
        ledger.release(hold, {})
    replayed = BudgetLedger([Cap("zoom", max_attempts=100)], tmp_path / "l.jsonl")
    assert (replayed.totals("zoom").attempts, replayed.totals("zoom").tokens) == (6, 12000)


def test_a_refused_request_releases_its_hold_to_nothing(tmp_path):
    from evalv2.adapters.vss import AgentRefusedError

    ctx = _ctx(tmp_path)
    with pytest.raises(AgentRefusedError):
        VssAgentAdapter("super-b", MODEL, post=FakeAgent((409, {"detail": "R2 configured"}))).analyze(
            "Assault001", _request(), ctx)
    assert ctx.ledger.totals("zoom").attempts == 0 and ctx.ledger.totals("inference").attempts == 0


# --- B8: a P1 the agent retried is stored and kept out of the caption-effect pairs --------------------------------


def _retried_answer():
    status, body = _answer()
    body = copy.deepcopy(body)
    body["p1_attempts"] = [{"attempt": 1, "outcome": "retried", "finish_reason": "length", "output_chars": 90000},
                           {"attempt": 2, "outcome": "valid", "finish_reason": "stop", "output_chars": 2100}]
    return status, body


def _execute(tmp_path, reply):
    task = PipelineTask(video_url=lambda clip: SIGNED, video_duration=lambda clip: 30.0)
    item = task.plan(_config(), {"clips": ["Assault001"]})[0]
    ctx = _ctx(tmp_path)
    record = task.execute(item, VssAgentAdapter("super-b", MODEL, post=FakeAgent(reply)), ctx)
    return record, ctx.store.get(ArtifactRef.parse(record.prediction_ref))


def test_the_agents_p1_attempts_are_stored_and_a_retry_is_flagged(tmp_path):
    _, stored = _execute(tmp_path, _retried_answer())
    assert stored["p1_retried"] is True and [a["outcome"] for a in stored["p1_attempts"]] == ["retried", "valid"]
    _, single = _execute(tmp_path / "b", _answer())
    assert single["p1_retried"] is False and single["p1_attempts"] == []


def test_a_rejected_run_keeps_its_p1_attempts(tmp_path):
    attempts = [{"attempt": 1, "outcome": "retried"}, {"attempt": 2, "outcome": "failed"}]
    rejected = (422, {"detail": {"raw_output": "{", "finish_reason": "length", "p1_attempts": attempts,
                                 "provenance": _provenance(stages={"p1": {"calls": 2, "total_tokens": 70000}})}})
    _, stored = _execute(tmp_path, rejected)
    assert stored["p1_retried"] is True and stored["p1_attempts"] == attempts


def test_retried_runs_are_left_out_of_the_caption_effect():
    scored = {"entities": {"counts": {"f1": 0.0}, "matches": []}}
    better = {"entities": {"counts": {"f1": 1.0}, "matches": []}}
    track_a = {"Assault001#1": scored, "Burglary002#1": scored}
    track_b = {"Assault001#1": better, "Burglary002#1": scored}  # Assault001's P1 was retried after a runaway
    artifacts = {"Assault001#1": {"p1_retried": True}, "Burglary002#1": {"p1_retried": False}}
    effect = scoring.caption_effect(track_a, track_b, artifacts)
    assert effect["runs_paired"] == 1 and effect["excluded"] == 1
    assert effect["metrics"]["entities.f1"]["mean_delta"] == 0.0, "no false Track B gain from the retry"
    assert scoring.paired_effect(track_a, track_b)["metrics"]["entities.f1"]["mean_delta"] == 0.5


# --- B11: the object tracker's real detector cap ------------------------------------------------------------------


def _tracked_answer(cap, calls=12):
    stages = {**_provenance()["stages"], "object_tracking": {"model": MODEL, "calls": calls, "total_tokens": 24000,
                                                             "calls_without_usage": 0}}
    status, body = _answer(_provenance(flags={"object_tracking": True}, stages=stages))
    body = copy.deepcopy(body)
    if cap is not None:
        body["object_tracking"] = {"enabled": True, "seeds": 1, "tracked": 1, "tracker": "TrackerMIL",
                                   "elapsed_ms": 900.0, "time_cap_reached": False, "skipped": None, "items": {},
                                   "detector": {"cap": cap, "calls": calls, "refused_by_cap": 0, "cap_reached": False,
                                                "tokens": 24000, "calls_without_usage": 0}}
    return status, body


def _tracked_request():
    return requests_vss.build_analyze_request(TRACKED, video_url=SIGNED, model=MODEL, duration_seconds=30.0)


def _tracked_ctx(tmp_path):
    return _ctx(tmp_path, [Cap("inference", max_attempts=10), Cap("grounding", max_attempts=200),
                           Cap("zoom", max_attempts=400), Cap("object_tracking", max_attempts=400)])


def test_a_reply_whose_tracker_cap_exceeds_the_worst_case_stops_the_run_after_charging(tmp_path):
    ctx = _tracked_ctx(tmp_path)
    adapter = VssAgentAdapter("super-b", MODEL, post=FakeAgent(_tracked_answer(cap=200, calls=150)))
    with pytest.raises(ProfileMismatchError, match="detector cap is 200, above the profile's worst case of 80"):
        adapter.analyze("Assault001", _tracked_request(), ctx)
    assert ctx.ledger.totals("object_tracking").attempts == 150, "what the agent sent is charged, then the run stops"


def test_the_tracker_cap_is_pinned_and_must_be_reported(tmp_path):
    ctx = _tracked_ctx(tmp_path)
    adapter = VssAgentAdapter("super-b", MODEL, post=FakeAgent(_tracked_answer(cap=80), _tracked_answer(cap=60),
                                                               _tracked_answer(cap=None)))
    assert adapter.analyze("Assault001", _tracked_request(), ctx).outcome == "answered"
    assert adapter.pinned["object_tracking_cap"] == 80
    with pytest.raises(AgentDriftError, match="detector cap changed"):
        adapter.analyze("Assault001", _tracked_request(), ctx)
    with pytest.raises(ProfileMismatchError, match="reported no detector cap"):
        VssAgentAdapter("super-b", MODEL, post=FakeAgent(_tracked_answer(cap=None))).analyze(
            "Assault001", _tracked_request(), _tracked_ctx(tmp_path / "b"))


# --- P3 flags describe what the request ran -----------------------------------------------------------------------


def test_the_tracking_profile_expects_object_tracking_and_every_profile_an_experiment(tmp_path):
    assert TRACKED.expected_flags["object_tracking"] is True
    assert requests_vss.PROFILES["production-v4"].expected_flags["object_tracking"] is False
    assert all(p.expected_flags["experiment"] is True for p in requests_vss.PROFILES.values())
    with pytest.raises(ProfileMismatchError, match="experiment"):  # an agent reporting environment flags (pre-P3 fix)
        VssAgentAdapter("super-b", MODEL, post=FakeAgent(_answer(_provenance(flags={"experiment": None})))).analyze(
            "Assault001", _request(), _ctx(tmp_path))
    assert requests_vss.STAGE_LEDGERS["embedding"] == "embedding"


def test_the_trackers_summary_is_stored_from_the_replys_top_level_field(tmp_path):
    task = PipelineTask(video_url=lambda clip: SIGNED, video_duration=lambda clip: 30.0)
    config = _config([{"id": "super-b", "adapter": "vss", "model": MODEL, "endpoint": "experiment-agent",
                       "profile": "production-v4-object-tracking"}])
    item = task.plan(config, {"clips": ["Assault001"]})[0]
    status, body = _tracked_answer(cap=80)
    body["experiment"]["object_tracking"] = True  # the request's setting stays a bool (P4)
    ctx = _tracked_ctx(tmp_path)
    record = task.execute(item, VssAgentAdapter("super-b", MODEL, post=FakeAgent((status, body))), ctx)
    stored = ctx.store.get(ArtifactRef.parse(record.prediction_ref))
    assert stored["object_tracking"]["detector"]["cap"] == 80 and stored["experiment"]["object_tracking"] is True
    _, untracked = _execute(tmp_path / "b", _answer())
    assert untracked["object_tracking"] is None


# --- B3: per-run flags are recorded, never pinned ------------------------------------------------------------------

CAPTIONS = requests_vss.PROFILES["production-v4-captions"]
CHUNK_SETTINGS = {"window_seconds": 20.0, "overlap_seconds": 2.0, "min_video_seconds": 30.0, "max_windows": 40}


def _caption_answer(enabled, skipped=None, settings=None):
    stages = {"p1": {"model": MODEL, "calls": 1, "total_tokens": 9000, "calls_without_usage": 0},
              "grounding": {"model": MODEL, "calls": 2, "total_tokens": 900, "calls_without_usage": 0},
              "zoom": {"model": MODEL, "calls": 2, "total_tokens": 900, "calls_without_usage": 0}}
    if enabled:
        stages["caption"] = {"model": MODEL, "calls": 3, "total_tokens": 21000, "calls_without_usage": 0}
    provenance = _provenance(flags={"chunking": {**(settings or CHUNK_SETTINGS), "enabled": enabled}}, stages=stages)
    status, body = _answer(provenance)
    body["experiment"]["caption"] = {"store": "inline", "skipped": skipped, "calls": 3 if enabled else 0, "cap": 30}
    return status, body


def _caption_request():
    return requests_vss.build_analyze_request(CAPTIONS, video_url=SIGNED, model=MODEL, duration_seconds=60.0,
                                              enabled=[CAPTIONS.id])


def test_chunking_enabled_varies_per_clip_without_stopping_the_run(tmp_path):
    caps = [Cap(ledger, max_attempts=10_000) for ledger in ("inference", "grounding", "zoom", "caption")]
    ctx = _ctx(tmp_path, caps)
    agent = FakeAgent(_caption_answer(True), _caption_answer(False, "video of 30 s or less"), _caption_answer(True))
    adapter = VssAgentAdapter("super-b", MODEL, post=agent)
    for _ in range(3):
        adapter.analyze("Assault001", _caption_request(), ctx)
    assert adapter.pinned["flags"]["chunking"] == CHUNK_SETTINGS, "the settings are pinned without enabled"
    assert ctx.ledger.totals("caption").attempts == 6


def test_a_changed_chunking_setting_is_still_drift(tmp_path):
    caps = [Cap(ledger, max_attempts=10_000) for ledger in ("inference", "grounding", "zoom", "caption")]
    ctx = _ctx(tmp_path, caps)
    agent = FakeAgent(_caption_answer(True), _caption_answer(True, settings={**CHUNK_SETTINGS, "window_seconds": 30}))
    adapter = VssAgentAdapter("super-b", MODEL, post=agent)
    adapter.analyze("Assault001", _caption_request(), ctx)
    with pytest.raises(AgentDriftError, match="changed during the run"):
        adapter.analyze("Assault001", _caption_request(), ctx)


def test_a_resumed_run_pins_against_the_stored_settings(tmp_path):
    caps = [Cap(ledger, max_attempts=10_000) for ledger in ("inference", "grounding", "zoom", "caption")]
    first = VssAgentAdapter("super-b", MODEL, post=FakeAgent(_caption_answer(True)))
    first.analyze("Assault001", _caption_request(), _ctx(tmp_path, caps))
    resumed = VssAgentAdapter("super-b", MODEL, post=FakeAgent(_caption_answer(False, "cap")),
                              pinned=copy.deepcopy(first.pinned))
    resumed.analyze("Assault001", _caption_request(), _ctx(tmp_path, caps))  # enabled differs: no drift


def test_pinned_flags_drop_only_the_per_run_outcome():
    from evalv2.adapters.vss import per_run_flags, pinned_flags

    flags = {"zoom_names": True, "chunking": {"enabled": True, "window_seconds": 20.0}}
    assert pinned_flags(flags) == {"zoom_names": True, "chunking": {"window_seconds": 20.0}}
    assert pinned_flags({"chunking": False, "tracking": False}) == {"tracking": False}, "a bare boolean is the outcome"
    assert per_run_flags({"flags": flags}, {"caption": {"skipped": None, "store": "inline"}}) == {
        "chunking_enabled": True, "caption_skipped": None, "caption_store": "inline"}
    assert per_run_flags(None)["chunking_enabled"] is None


def test_the_pipeline_artifact_records_the_runs_caption_outcome(tmp_path):
    caps = [Cap(ledger, max_attempts=10_000) for ledger in ("inference", "grounding", "zoom", "caption")]
    ctx = _ctx(tmp_path, caps)
    task = PipelineTask(video_url=lambda clip: SIGNED, video_duration=lambda clip: 60.0,
                        enabled_profiles=[CAPTIONS.id])
    item = next(i for i in task.plan(_config(), {"clips": ["Assault001"]}) if i.subject == "super-b")
    item = type(item)(item.task, item.subject, item.item_id, item.repeat,
                      {**item.spec, "profile": CAPTIONS.id})
    record = task.execute(item, VssAgentAdapter("super-b", MODEL,
                                                post=FakeAgent(_caption_answer(False, "video of 30 s or less"))), ctx)
    stored = ctx.store.get(ArtifactRef.parse(record.prediction_ref))
    assert stored["caption_pass"] == {"ran": False, "calls": 0, "chunking_enabled": False,
                                      "caption_skipped": "video of 30 s or less", "caption_store": "inline"}
