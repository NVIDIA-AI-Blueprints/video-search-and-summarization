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

"""Stage 3 automation: checkpoint integrity, progress, the recovered-0 merge and the supervisor's decisions.

Hermetic: synthetic stage directories and a fake runner process. Nothing is sent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import contract
import experiment_runner as er
import stage3
import stage3_automation as auto

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
CONFIGS = stage3.load_configs()
IDS = [CONFIGS[n]["experiment_id"] for n in stage3.CONFIGS]


def _video(incident_id: str, outcome: str = "scored", error: str | None = None) -> dict:
    video = {"incident_id": incident_id, "repeat": 1, "outcome": outcome, "run_id": f"r-{incident_id}",
             "p1_raw": {"content": "{}"} if outcome == "scored" else {"error": error, "failure_kind": outcome},
             "validation": {"core_ok": outcome == "scored"}}
    return video


def _stage(tmp_path: Path, done: int, *, videos=None, cost_lines: int | None = None) -> Path:
    stage_dir = tmp_path / "stages" / f"20261101T000000Z-{auto.stage_id(CONFIGS)}"
    stage_dir.mkdir(parents=True)
    split = json.loads(er.SPLIT_PATH.read_text())
    order = er.schedule(list(CONFIGS.values()), sorted(split["test"]), 1)
    (stage_dir / "schedule.json").write_text(json.dumps({"configs": IDS, "order": [
        {"repeat": r, "position": p, "incident_id": v, "config": IDS[k]} for r, p, v, k in order]}))
    records = [{"experiment_id": i, "videos": [], "usage": {"p1_calls": 0, "p1_attempts": 0}} for i in IDS]
    for r, _p, v, k in order[:done]:
        records[k]["videos"].append((videos or {}).get((IDS[k], v)) or _video(v))
        records[k]["usage"]["p1_calls"] += 1
        records[k]["usage"]["p1_attempts"] += 1
    (stage_dir / "checkpoint.json").write_text(json.dumps({"order": [list(o) for o in order], "done": done,
                                                           "records": records}))
    lines = done if cost_lines is None else cost_lines
    (stage_dir / "cost-log.jsonl").write_text("".join(
        json.dumps({"time": f"2026-11-01T00:{i // 60:02d}:{i % 60:02d}+00:00", "p1_attempts": 1}) + "\n"
        for i in range(lines)))
    return stage_dir


def test_a_consistent_checkpoint_has_no_problems(tmp_path):
    assert auto.checkpoint_problems(_stage(tmp_path, 9)) == []


def test_a_corrupt_checkpoint_is_found(tmp_path):
    stage_dir = _stage(tmp_path, 9)
    checkpoint = json.loads((stage_dir / "checkpoint.json").read_text())
    checkpoint["records"][0]["videos"].append(checkpoint["records"][0]["videos"][0])
    (stage_dir / "checkpoint.json").write_text(json.dumps(checkpoint))
    problems = auto.checkpoint_problems(stage_dir)
    assert any("twice" in p for p in problems) and any("says 9" in p for p in problems)
    checkpoint["order"] = checkpoint["order"][::-1]
    (stage_dir / "checkpoint.json").write_text(json.dumps(checkpoint))
    assert any("differs from schedule.json" in p for p in auto.checkpoint_problems(stage_dir))


def test_progress_counts_runs_failures_and_a_replayed_cost_line(tmp_path, monkeypatch):
    split = json.loads(er.SPLIT_PATH.read_text())
    first = er.schedule(list(CONFIGS.values()), sorted(split["test"]), 1)[0]
    failed = {(IDS[first[3]], first[2]): _video(first[2], "timeout", "HTTP 524")}
    stage_dir = _stage(tmp_path, 8, videos=failed, cost_lines=9)
    state = auto.progress(stage_dir, CONFIGS, results_root=tmp_path)
    assert (state["done"], state["remaining"], state["scheduled"]) == (8, 212, 220)
    name = next(n for n in stage3.CONFIGS if CONFIGS[n]["experiment_id"] == IDS[first[3]])
    assert state["configs"][name]["infrastructure_failures"] == {"timeout": 1}
    assert state["integrity"]["cost_log_lines_beyond_checkpoint"] == 1
    # Stage 3 is frozen on its contract: under a later contract the manifest drifts and the automation must refuse
    # to resume, so integrity holds only while the current contract is the one Stage 3 ran on.
    frozen_on = json.loads(stage3.MANIFEST_PATH.read_text())["p1_request"]["contract_version"]
    assert state["integrity_ok"] is (frozen_on == contract.contract_version())
    assert state["elapsed"]["runs_per_hour"] == 3600.0


def test_merge_none_records_zero_recoveries_and_refuses_an_eligible_failure(tmp_path):
    record = {"experiment_id": "s3-c1-direct-base", "complete": True, "videos": [_video("Animal001")]}
    path = tmp_path / "results.json"
    path.write_text(json.dumps(record))
    merged = json.loads(auto.merge_none(path, tmp_path).read_text())
    assert merged["protocol"] == "original+recovery-v1" and merged["recovery"]["recovered"] == 0
    assert merged["videos"] == record["videos"]
    record["videos"].append(_video("Animal002", "timeout", "HTTP 524"))
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="recoverable"):
        auto.merge_none(path, tmp_path / "other")


class FakeRunner:
    """Stands in for ``keep-awake.sh -- eval_experiment.py run``: prints a scripted session and exits."""

    script: list[tuple[int, str]] = []  # noqa: RUF012
    commands: list[list[str]] = []  # noqa: RUF012

    def __init__(self, command, *, stdout, **kwargs):
        FakeRunner.commands.append(command)
        self.code, text = FakeRunner.script.pop(0)
        stdout.write(text)
        stdout.flush()
        self.pid = 0

    def wait(self):
        return self.code


@pytest.fixture
def harness(tmp_path, monkeypatch):
    stage_dir = tmp_path / "stages" / "20261101T000000Z-abc"
    state = {"stages": [], "finalized": 0, "waits": []}
    monkeypatch.setattr(auto, "EVAL_DIR", tmp_path)
    monkeypatch.setattr(auto, "find_stage", lambda configs: state["stages"])
    monkeypatch.setattr(auto, "stage_results", lambda d, c: {n: d / n for n in stage3.CONFIGS})
    monkeypatch.setattr(auto, "checkpoint_problems", lambda d: [])
    monkeypatch.setattr(auto, "write_progress", lambda d, c: {})
    monkeypatch.setattr(auto, "on_ac_power", lambda: True)
    monkeypatch.setattr(auto, "dns_ok", lambda: True)
    monkeypatch.setattr(auto, "runner_env", dict)
    monkeypatch.setattr(auto.stage3, "preflight_checks", lambda: {"ok": True, "checks": {}})
    monkeypatch.setattr(auto.Services, "ensure", lambda self: [])
    monkeypatch.setattr(auto.er, "completed_runs", lambda eid: [])
    monkeypatch.setattr(auto.subprocess, "Popen", FakeRunner)

    def finalize(d, c, services=None):
        state["finalized"] += 1
        return 0

    monkeypatch.setattr(auto, "finalize", finalize)
    FakeRunner.commands = []

    def run(script, after_first=None):
        FakeRunner.script = list(script)

        def popen(command, **kwargs):
            runner = FakeRunner(command, **kwargs)
            state["stages"] = [stage_dir]  # the first session creates the stage directory
            if runner.code == 0:
                stage_dir.mkdir(parents=True, exist_ok=True)
                for n in stage3.CONFIGS:
                    (stage_dir / n).write_text("{}")
            return runner

        monkeypatch.setattr(auto.subprocess, "Popen", popen)
        return auto.supervise(wait=lambda s: state["waits"].append(s))

    return run, state


def test_a_session_limit_pause_resumes_from_the_stage_and_then_finalises(harness):
    run, state = harness
    assert run([(4, "PAUSED: session time limit (4.0 h). Resume with --resume x\n"), (0, "done\n")]) == 0
    assert "--resume" not in FakeRunner.commands[0] and "--resume" in FakeRunner.commands[1]
    assert state["finalized"] == 1 and state["waits"] == []


def test_an_outage_pause_waits_then_resumes_a_bounded_number_of_times(harness):
    run, state = harness
    outage = (4, "PAUSED: 4 consecutive video-runs with no model output (gateway unavailable?). Resume\n")
    assert run([outage, outage, outage]) == 1
    assert state["waits"] == [auto.OUTAGE_WAIT_S] * auto.OUTAGE_RESUMES and state["finalized"] == 0


@pytest.mark.parametrize("code", [2, 3])
def test_a_refusal_or_a_cap_stop_is_never_retried(harness, code):
    run, state = harness
    assert run([(code, "REFUSED or STOP\n")]) == 1
    assert len(FakeRunner.commands) == 1 and state["finalized"] == 0


def test_an_interrupted_runner_resumes_from_the_checkpoint(harness):
    run, state = harness
    assert run([(130, "KeyboardInterrupt\n"), (0, "done\n")]) == 0
    assert "--resume" in FakeRunner.commands[1] and state["finalized"] == 1


def test_a_failed_preflight_stops_before_any_session(harness, monkeypatch):
    run, _state = harness
    monkeypatch.setattr(auto.stage3, "preflight_checks", lambda: {"ok": False, "checks": {"manifest": ["drift"]}})
    assert run([]) == 1 and FakeRunner.commands == []


def test_two_stage_directories_stop_the_supervisor(harness, tmp_path):
    run, state = harness
    state["stages"] = [tmp_path / "a", tmp_path / "b"]
    assert run([]) == 1 and FakeRunner.commands == []
