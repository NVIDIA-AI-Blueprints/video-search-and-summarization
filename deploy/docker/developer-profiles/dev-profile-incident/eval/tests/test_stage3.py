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

"""Stage 3: the committed configs and manifest, the offline preflight checks and the report. Hermetic: no call."""

from __future__ import annotations

import copy
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

import contract
import experiment_runner as er
import stage3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import eval_experiment_plumbing  # noqa: E402
import eval_vlm_client  # noqa: E402
from test_ground_truth_freeze import _write  # noqa: E402

SPLIT = json.loads(er.SPLIT_PATH.read_text())


@pytest.fixture
def configs():
    return stage3.load_configs()


def test_the_committed_configs_are_final_test_runs_of_the_selected_prompt(configs, tmp_path):
    assert stage3.role_problems(configs) == []
    assert stage3.shared_setting_problems(configs) == []
    for config in configs.values():
        assert er.config_problems(config) == []
        # an empty results root: hermetic, and independent of whether Stage 3 has run on this host
        assert er.preflight(config, SPLIT, results_root=tmp_path)["planned_p1_calls"] == 55
    assert {c["prompt_id"] for c in configs.values()} == {"p1-v4-base", stage3.selected_prompt()}
    assert stage3.selected_prompt() == "p1-v4-e01-severity"


def test_the_committed_manifest_equals_a_fresh_build(configs):
    committed = json.loads(stage3.MANIFEST_PATH.read_text())
    problems = stage3.manifest_problems(committed, stage3.build_manifest(configs, SPLIT))
    if committed["p1_request"]["contract_version"] == contract.contract_version():
        assert problems == []
    else:  # Stage 3 is frozen on incident-contract-v4; under a later contract only its contract-bound section drifts
        assert committed["p1_request"]["contract_version"] == "incident-contract-v4"
        assert problems and all("'p1_request'" in problem for problem in problems)
    assert committed["split"]["clips"] == 55 and committed["order"]["video_runs"] == 220
    assert committed["methodology"]["match_threshold"] == 0.40
    assert committed["ground_truth"]["status"] == "frozen"
    assert committed["configs"]["C3"]["prompt_sha256"] == committed["configs"]["C4"]["prompt_sha256"]


def test_an_edited_config_is_manifest_drift(configs, tmp_path):
    directory = tmp_path / "configs"
    shutil.copytree(stage3.CONFIG_DIR, directory)
    edited = copy.deepcopy(configs)
    edited["C3"]["pipeline"]["retry_policy"] = "retry-v1"
    (directory / "s3-c3-vss-v1.json").write_text(json.dumps(edited["C3"]))
    committed = json.loads(stage3.MANIFEST_PATH.read_text())
    assert any("configs" in p for p in stage3.manifest_problems(committed, stage3.build_manifest(
        stage3.load_configs(directory), SPLIT, config_dir=directory)))
    assert stage3.shared_setting_problems(edited)


@pytest.mark.parametrize(("name", "key", "value", "expect"), [
    ("C3", "prompt_id", "p1-v4-e08-combined", "must use p1-v4-e01-severity"),
    ("C2", "arm", "direct-vlm", "must be the vss arm"),
    ("C1", "repeats", 2, "1 repeat"),
    ("C4", "frozen_baseline", False, "runs once"),
    ("C2", "split", "dev", "final test config"),
])
def test_a_wrong_role_is_refused(configs, name, key, value, expect):
    configs[name][key] = value
    assert any(expect in p for p in stage3.role_problems(configs))


def test_the_direct_payload_is_what_the_client_sends(monkeypatch):
    sent = {}

    class Response:
        status_code = 200
        headers: dict = {}  # noqa: RUF012

        def json(self):
            return {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}], "usage": {}}

    def post(url, *, headers, json, timeout):
        sent.update(json)
        return Response()

    monkeypatch.setattr(eval_vlm_client, "gateway_credentials", lambda: ("https://gw.invalid/v1", "k"))
    monkeypatch.setattr(eval_vlm_client.requests, "post", post)
    eval_vlm_client.analyze_video_with_p1("m", "https://v.invalid/a.mp4", prompt_text="P")
    assert sent == eval_vlm_client.p1_request_payload("m", "https://v.invalid/a.mp4", "P")


def test_parity_allows_only_the_transport_keys(configs):
    def agent(model, prompt_id):
        text = stage3.prompt_registry.prompt_text(prompt_id)
        return {**eval_vlm_client.p1_request_payload(model, stage3.PARITY_URL, text), "stream": False}

    out = stage3.parity(configs, agent=agent)
    assert all(row["differences"] == [] and row["transport_only"] == ["stream"] for row in out.values())
    assert stage3.body_difference({"temperature": 0.0, "stream": False}, {"temperature": 0.2}) == ["temperature"]


def test_a_prompt_mismatch_between_the_arms_is_a_body_difference(configs):
    def agent(model, prompt_id):
        return eval_vlm_client.p1_request_payload(model, stage3.PARITY_URL, "another prompt")

    assert all("messages" in row["differences"] for row in stage3.parity(configs, agent=agent).values())


def test_caps_cover_every_retry_and_the_worst_recovery(configs):
    caps = stage3.stage_caps(configs, SPLIT)
    assert caps["stage_total"]["video_runs"] == 220 and caps["stage_total"]["max_p1_calls"] == 660
    assert caps["configs"]["C1"]["recovery_max_p1_calls"] == 45
    assert caps["stage_total"]["max_p1_tokens"] == 660 * er.P1_TOKENS_CEILING


def test_a_completed_test_run_blocks_a_second_one(configs, tmp_path):
    run = tmp_path / "s3-c2-vss-base" / "20261101T000000Z"
    run.mkdir(parents=True)
    (run / "results.json").write_text(json.dumps({"complete": True}))
    assert stage3.run_once_problems(configs, tmp_path) == ["C2: s3-c2-vss-base already has a completed run"]


def test_clip_checks_find_missing_and_overlapping_clips():
    split = {"dev": ["A1"], "test": ["A1", "B2", "C3"]}
    manifest = {"clips": [{"incident_id": "A1", "r2_key": "k", "duration_seconds": 1.0},
                          {"incident_id": "B2", "r2_key": "k", "duration_seconds": 1.0}]}
    problems = stage3.clip_problems(split, manifest, {"by_incident_id": {"A1": {}, "C3": {}}})
    assert problems == ["C3: not in the dataset manifest", "B2: not in the frozen ground truth", "A1: also in dev"]


@pytest.fixture(scope="module")
def plumbing(tmp_path_factory):
    root = tmp_path_factory.mktemp("plumbing")
    gt = _write(root / "gt", status="candidate")
    split = {"version": "split-v2", "dev": ["Assault001", "Burglary002"], "test": [],
             "by_category": {"Assault": {"dev": ["Assault001"], "test": []},
                             "Burglary": {"dev": ["Burglary002"], "test": []}}}
    return eval_experiment_plumbing.plumbing_run(root / "results", gt_dir=gt, split=split,
                                                 now=datetime(2026, 11, 1, tzinfo=UTC))["records"]


def test_the_report_has_every_comparison_and_section(plumbing):
    summary = stage3.report(plumbing)
    assert summary["problems"] == []
    assert list(summary["comparisons"]) == ["C2-C1", "C3-C2", "C4-C1", "C3-C4", "C3-C1"]
    assert set(summary["comparisons"]["C3-C2"]) >= {"d9", "severity", "per_category", "examples",
                                                    "guardrails_informational", "d9_excluding_pilot_clips"}
    assert "guardrails_informational" not in summary["comparisons"]["C2-C1"]
    text = stage3.render(summary, plumbing, title="t")
    for section in ("## Stage 3: D9 v2 paired differences", "## Stage 3: severity", "## Stage 3: ΔI per category",
                    "## Stage 3: latency", "## Stage 3: reliability and failures", "## Stage 3: representative clips",
                    "never an architectural extraction advantage", "not individually human-verified",
                    "held out from Stage 2 selection but not unseen", "not an unseen subset", "## Incident fields", "## Reliability"):
        assert section in text


def test_latency_splits_model_processing_from_pipeline_overhead(plumbing):
    vss, direct = stage3.timing(plumbing["C2"]), stage3.timing(plumbing["C1"])
    assert vss["model_processing"]["p50"] == 10.0 and vss["pipeline_overhead"]["p50"] == 2.5
    assert vss["end_to_end"]["p50"] == 12.5
    assert direct["model_processing"]["p50"] == 10.0 and direct["pipeline_overhead"]["n"] == 0


def test_the_pilot_clip_subset_drops_only_the_named_clips():
    record = {"videos": [{"incident_id": i} for i in ("Animal003", "Animal004", "Assault018", "Burglary005")]}
    assert [v["incident_id"] for v in stage3.without_pilot_clips(record)["videos"]] == ["Animal004"]
    assert all(c in SPLIT["test"] for c in stage3.PILOT_CLIPS)
