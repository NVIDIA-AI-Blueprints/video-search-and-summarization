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

"""Track A incident fields and objects: V2 reproduces records made by V1's own pipeline, offline and exactly."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from evalv2.adapters.base import CallContext
from evalv2.adapters.fake import FakeAdapter, Scripted
from evalv2.core.artifacts import ArtifactRef, ArtifactStore
from evalv2.core.budget import BudgetLedger, Cap
from evalv2.core.netguard import no_network
from evalv2.core.retry import POLICIES, Failure
from evalv2.tasks import TASKS
from evalv2.tasks.incident_objects import v1
from evalv2.tasks.incident_objects.aggregate import aggregate
from evalv2.tasks.incident_objects.scoring import embedder_similarity, score_video, stored_similarity
from evalv2.tasks.judging import CachedJudge, JudgeCache, JudgeIdentity, JudgeVerdict, RecordingJudge
from evalv2.verify_v1 import compare_with_report, verify_record

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import eval_gt_schema  # noqa: E402
import eval_run  # noqa: E402
from eval_vlm_client import ChatResult, P1Outcome  # noqa: E402

import embed_client  # noqa: E402
import eval_gt  # noqa: E402
import ground_truth_freeze as gtf  # noqa: E402
import matching  # noqa: E402

MODEL = "nvidia/cosmos-3-super-reasoner"
DIM = 48


def _sheets():
    def incident(clip, kind, start, end, description, severity):
        return {"Filename": f"{clip}_x264.mp4", "Incident_ID (P_key)": clip, "Type": kind, "Start_Timestamp": start,
                "End_Timestamp": end, "Duration": end - start, "Description": description, "Severity_Level": severity}

    def child(clip, **values):
        return {"Filename": f"{clip}_x264.mp4", "Incident_ID (P_key, F_key)": clip, **values}

    return {
        "gt_incidents": [incident("Assault001", "Assault", 2, 9, "Two people fight near the door.", 3),
                         incident("Burglary002", "Burglary", 0, 20, "A door is forced open.", 2)],
        "gt_entities": [child("Assault001", **{"Entity_ID (P_key)": "E1", "Type": "human",
                                               "Description": "Person in red"}),
                        child("Assault001", **{"Entity_ID (P_key)": "E2", "Type": "human",
                                               "Description": "Person in blue jacket"})],
        "gt_instruments": [child("Assault001", **{"Instrument_ID (P_key)": "I1", "Entity_ID": "E1", "Name": "stick",
                                                  "Description": "Wooden stick", "Threat_Level": 2})],
        "gt_assets": [child("Burglary002", **{"Asset_ID (P_key)": "A1", "Name": "door",
                                              "Description": "Shop door"})],
    }


@pytest.fixture(scope="module")
def frozen(tmp_path_factory):
    directory = tmp_path_factory.mktemp("gt") / "ground-truth-v1.0"
    directory.mkdir()
    split = {"version": "split-v2", "dev": ["Assault001"], "test": ["Burglary002"], "excluded": [],
             "by_category": {"Assault": {"dev": ["Assault001"], "test": []},
                             "Burglary": {"dev": [], "test": ["Burglary002"]}}}
    files = {"ground_truth.json": gtf.canonical_json({"version": gtf.VERSION, "status": "frozen", "sheets": _sheets()}),
             "split-v2.json": gtf.canonical_json(split)}
    for name, content in files.items():
        (directory / name).write_bytes(content)
    (directory / "MANIFEST.json").write_text(json.dumps(
        {"status": "frozen", "blocking": [], "files": {n: gtf.sha256_bytes(c) for n, c in files.items()}}))
    return eval_gt_schema.load_frozen_gt_index(directory), gtf.read_frozen(directory)["sheets"]


def _vector(text):
    vec = [0.0] * DIM
    for word in text.lower().split():
        vec[int(hashlib.sha256(word.encode()).hexdigest(), 16) % DIM] += 1.0
    return vec


def _embed(texts):
    return [_vector(t) for t in texts]


def _score_text(expected, predicted):
    return 0.9 if (expected or "").strip().lower() == (predicted or "").strip().lower() else 0.2


def v2_judge(expected, predicted):
    return JudgeVerdict(_score_text(expected, predicted))


def _report(**incident_overrides):
    return {
        "incident": {"type": "assault", "title": "Two people fight", "start_timestamp": 3, "end_timestamp": 9,
                     "description": "Two people fight near the door.", "severity_level": 4,
                     "severity_reason": "Blows are exchanged.", "confidence_score": 0.8, "location": "street",
                     **incident_overrides},
        "entities": [
            {"entity_id": "E1", "type": "human", "description": "Person in red", "timestamp_seconds": 3,
             "first_seen_seconds": 1, "last_seen_seconds": 9, "label": "attacker"},
            {"entity_id": "E2", "type": "human", "description": "Person in a blue jacket", "timestamp_seconds": 3,
             "first_seen_seconds": 1, "last_seen_seconds": 9, "label": "victim"},
            {"entity_id": "E3", "type": "animal", "description": "Small dog barking", "timestamp_seconds": 5,
             "first_seen_seconds": 4, "last_seen_seconds": 8, "label": "dog"},
        ],
        "instruments": [{"instrument_id": "I1", "entity_id": "E1", "name": "stick", "description": "Wooden stick",
                         "threat_level": 3, "timestamp_seconds": 4, "label": "weapon"}],
        "assets": [],
        "timeline": [{"start_seconds": 3, "end_seconds": 9, "description": "E1 hits E2 with I1."}],
        "uncertainties": [],
    }


def _v1_video(frozen, clip, run_id, chat):
    """One video record made by V1's own ``run_one_video`` with a fake embedder and judge."""
    index, sheets = frozen
    store = v1.gt_store(sheets)
    entry = index["by_incident_id"][clip]
    def fake_embed(texts):
        return embed_client.Result(ok=True, data=_embed(texts))

    with patch.object(matching, "embed_texts", side_effect=fake_embed), \
            patch.object(eval_gt, "judge_description_similarity",
                         side_effect=lambda e, p, **_: eval_gt.Result(ok=True, data=_score_text(e, p))), \
            patch.object(eval_run, "check_embedding_server_healthy", lambda *a, **k: None), \
            patch.object(eval_run, "generate_rp1_report", lambda prediction, **k: {"ok": False, "skipped": True}):
        return eval_run.run_one_video(
            store, model=MODEL, category=entry["category"], filename=entry["filename"], incident_id=clip,
            r2_object_key="k", video_url="u", gt_p1_shaped=entry["p1_shaped"], model_run_id=run_id,
            split_manifest_ref="split-v2.json", video_duration_seconds=30.0, incident_only=False,
            p1_call=lambda *_: P1Outcome(chat, []))


@pytest.fixture(scope="module")
def v1_record(frozen):
    ok = ChatResult(ok=True, content=json.dumps(_report()), finish_reason="stop")
    invalid = ChatResult(ok=True, content=json.dumps({"incident": {"type": "assault"}}), finish_reason="stop")
    timeout = ChatResult(ok=False, error="ReadTimeout: timed out", failure_kind="timeout")
    videos = [_v1_video(frozen, "Assault001", "MR-1", ok), _v1_video(frozen, "Burglary002", "MR-2", invalid),
              _v1_video(frozen, "Assault001", "MR-3", timeout)]
    return {"experiment_id": "s3-test", "videos": videos}


def test_the_v1_fixture_covers_matches_misses_judging_and_outcomes(v1_record):
    scored, invalid, timeout = v1_record["videos"]
    assert [v["outcome"] for v in v1_record["videos"]] == ["scored", "contract_invalid", "timeout"]
    assert scored["entities"]["counts"]["tp"] >= 2 and scored["entities"]["unmatched_model"]
    assert scored["instruments"]["matches"][0]["attribute_scores"]["holder"]["status"] == "correct"
    assert scored["incident_field_scores"]["description"]["judged"] is True
    assert invalid["incident_field_scores"]["description"]["judged"] is False


def _v2_score(frozen, video, similarity, judge):
    store = v1.gt_store(frozen[1])
    v1.put_prediction(store, video["incident_id"], video["run_id"], video["prediction"])
    return score_video(store, video["incident_id"], video["run_id"], similarity=similarity, judge=judge,
                       out_of_taxonomy=v1.is_out_of_taxonomy(video))


@pytest.mark.parametrize("position", [0, 1, 2])
def test_v2_scoring_equals_v1s_pipeline_with_the_same_embedder_and_judge(frozen, v1_record, position):
    video = v1_record["videos"][position]
    result = _v2_score(frozen, video, embedder_similarity(_embed), v2_judge)
    assert result["incident_field_scores"] == video["incident_field_scores"]
    for kind in v1.KINDS:
        for block in ("counts", "matches", "unmatched_model", "unmatched_gt"):
            assert result[kind][block] == video[kind][block], (kind, block)


def test_matrices_and_judgements_recorded_once_make_scoring_repeatable_offline(frozen, v1_record, tmp_path):
    video = v1_record["videos"][0]
    identity = JudgeIdentity("judge-model", "a" * 64, {"temperature": 0})
    cache = JudgeCache(tmp_path / "judge.jsonl", identity)
    recording = RecordingJudge(cache, v2_judge)
    live = _v2_score(frozen, video, embedder_similarity(_embed), recording)
    assert recording.calls > 0 and set(live["similarity"]) == {"entities", "instruments"}
    with no_network():
        offline_judge = CachedJudge(JudgeCache(tmp_path / "judge.jsonl", identity))
        offline = _v2_score(frozen, video, stored_similarity(live["similarity"]), offline_judge)
    assert offline_judge.misses == []
    def scores(result):
        return {k: v for k, v in result.items() if k != "similarity"}

    assert scores(offline) == scores(live)


def test_a_missing_judgement_is_unscored_not_failed(frozen, v1_record, tmp_path):
    video = v1_record["videos"][0]
    empty = CachedJudge(JudgeCache(tmp_path / "none.jsonl", JudgeIdentity("m", "b" * 64, {})))
    result = _v2_score(frozen, video, embedder_similarity(_embed), empty)
    description = result["incident_field_scores"]["description"]
    assert description["pass"] is None and description["score"] is None and result["scoring_errors"]


def test_a_changed_text_cannot_reuse_a_stored_matrix(frozen, v1_record):
    video = copy.deepcopy(v1_record["videos"][0])
    live = _v2_score(frozen, video, embedder_similarity(_embed), v2_judge)
    video["prediction"]["entities"][0]["description"] = "Someone else entirely"
    result = _v2_score(frozen, video, stored_similarity(live["similarity"]), v2_judge)
    assert result["entities"]["counts"] is None and any("other texts" in e["error"] for e in result["scoring_errors"])


def test_judge_cache_refuses_a_conflicting_result(tmp_path):
    cache = JudgeCache(tmp_path / "j.jsonl", JudgeIdentity("m", "c" * 64, {}))
    cache.put("a", "b", 0.5)
    cache.put("a", "b", 0.5)
    with pytest.raises(ValueError, match="already holds"):
        cache.put("a", "b", 0.6)
    other = JudgeIdentity("m2", "c" * 64, {})
    assert JudgeCache(tmp_path / "j.jsonl", other).get("a", "b") is None, "another judge never reuses a result"


def test_verify_v1_reproduces_the_record_and_its_aggregates(frozen, v1_record):
    result = verify_record(v1_record, frozen[1])
    assert result["mismatched_videos"] == 0 and result["continuity_equal"] and result["videos"] == 3


def test_verify_v1_catches_a_changed_stored_score(frozen, v1_record):
    tampered = copy.deepcopy(v1_record)
    tampered["videos"][0]["incident_field_scores"]["severity_level"]["pass"] = True
    tampered["videos"][0]["entities"]["matches"][0]["attribute_scores"]["description"]["score"] = 0.1
    result = verify_record(tampered, frozen[1])
    assert result["mismatched_videos"] == 1
    problems = result["problems"]["Assault001#1"]
    assert any("severity_level" in p for p in problems) and any("entities.matches" in p for p in problems)


def test_the_report_comparison_uses_v1s_items_and_severity(frozen, v1_record):
    verified = verify_record(v1_record, frozen[1])
    report = {"configs": {"C1": {"experiment_id": "s3-test", "items": copy.deepcopy(verified["continuity"]["items"]),
                                 "severity": copy.deepcopy(verified["continuity"]["severity"])}}}
    assert compare_with_report([verified], report) == []
    report["configs"]["C1"]["items"]["entities"]["tp"] += 1
    assert compare_with_report([verified], report) == ["C1.items: the report differs from V2's re-scored aggregate"]
    assert compare_with_report([verified], {"configs": {"C2": {"experiment_id": "other"}}})[0].startswith("C2")


def test_v2_metrics_extend_v1s_without_changing_them(v1_record):
    result = aggregate(v1_record["videos"])
    assert result["videos"] == 3 and result["with_model_output"] == 2
    assert result["continuity_v1"]["fields"]["type"]["n"] == 2, "the timeout is left out"
    assert result["type"]["confusion"]["assault"]["assault"] == 1
    assert result["type"]["confusion"]["burglary"]["missing"] == 1
    start = result["timestamps"]["start_timestamp"]
    assert start["n"] == 1 and start["mean_abs_error_s"] == 1.0 and start["within"]["2s"] == 1.0
    assert math.isclose(result["timestamps"]["window_iou"]["mean"], 6 / 7, abs_tol=1e-4)
    assert result["attributes"]["instruments"]["threat_level"]["fail"] == 1
    assert result["attributes"]["instruments"]["holder"]["correct"] == 1


def _ctx(tmp_path):
    return CallContext(store=ArtifactStore(tmp_path / "store"), ledger=BudgetLedger([Cap("inference", max_attempts=9)]),
                       policy=POLICIES["retry-v2"], subject="s", task="incident_objects", sleep=lambda s: None)


def _config(repeats=2):
    from evalv2.core.config import RunConfig

    return RunConfig.model_validate({
        "experiment": "v2-dev-track-a", "split": {"version": "split-v2", "half": "dev"},
        "subjects": [{"id": "super", "adapter": "fake", "model": MODEL}],
        "tasks": {"incident_objects": {"options": {"prompt_id": None}}}, "repeats": repeats, "seed": 1,
        "caps": [{"ledger": "inference", "max_attempts": 100}]})


def test_the_runner_plans_every_clip_subject_and_repeat():
    task = TASKS["incident_objects"]()
    items = task.plan(_config(), {"clips": ["Assault001", "Burglary002"]})
    assert len(items) == 4 and {i.item_id for i in items} == {"Assault001", "Burglary002"}
    assert task.budget_estimate(_config(), items)["inference"]["tokens"] == 4 * v1.P1_TOKENS_CEILING
    with pytest.raises(ValueError, match="clips"):
        task.plan(_config(), {})


@pytest.mark.parametrize(("script", "outcome"), [
    ([Scripted(text=json.dumps(_report()))], "scored"),
    ([Scripted(text=json.dumps({"incident": {"type": "assault"}}))], "contract_invalid"),
    ([Scripted(text="not json at all")], "parse_failure"),
    ([Scripted(failure=Failure("timeout", "timed out"))] * 3, "timeout"),
])
def test_the_runner_stores_the_prediction_and_labels_outcomes_as_v1(tmp_path, script, outcome):
    from evalv2.tasks.incident_objects.runner import IncidentObjectsTask

    task = IncidentObjectsTask(video_url=lambda clip: f"https://signed.example/{clip}.mp4",
                               video_duration=lambda clip: 30.0)
    item = task.plan(_config(1), {"clips": ["Assault001"]})[0]
    ctx = _ctx(tmp_path)
    record = task.execute(item, FakeAdapter(id="fake", model=MODEL, script=script), ctx)
    assert record.outcome == outcome and record.provenance["stages"]["p1"]["model"] == MODEL
    assert record.provenance["stages"]["p1"]["prompt_id"] == "p1-v4-base"
    if outcome in ("scored", "contract_invalid", "parse_failure"):
        stored = ctx.store.get(ArtifactRef.parse(record.prediction_ref))
        assert (stored["prediction"]["incident"].get("type") == "assault") == (outcome == "scored")
    else:
        assert record.prediction_ref is None and record.failure["kind"] == "timeout"


def test_the_runner_refuses_to_execute_without_a_run_resolver(tmp_path):
    task = TASKS["incident_objects"]()
    item = task.plan(_config(1), {"clips": ["Assault001"]})[0]
    with pytest.raises(RuntimeError, match="approval-gated"):
        task.execute(item, FakeAdapter(id="fake", model=MODEL, script=[]), _ctx(tmp_path))


def test_verify_v1_cli_needs_the_private_ground_truth(tmp_path, capsys):
    from evalv2 import cli

    record = tmp_path / "results.json"
    record.write_text(json.dumps({"experiment_id": "x", "videos": []}))
    assert cli.main(["verify-v1", "--record", str(record), "--data-root", str(tmp_path / "none")]) == 1
    assert "ground-truth-v1.0" in json.loads(capsys.readouterr().out)["problems"][0]
