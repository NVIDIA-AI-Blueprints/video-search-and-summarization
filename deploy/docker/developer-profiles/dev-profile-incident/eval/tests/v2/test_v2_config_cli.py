# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run config rules and the plan / preflight / dry-run / audit commands."""

from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

from evalv2 import cli
from evalv2.core.config import RunConfig
from evalv2.core.manifest import build_run_manifest, git_info, input_drift
from evalv2.tasks import base

CONFIG = {
    "experiment": "v2-dev-incident-super-direct",
    "split": {"version": "split-v2", "half": "dev"},
    "datasets": {"incident_gt": "ground-truth-v1.0"},
    "subjects": [{"id": "super-direct", "adapter": "openai_compat", "endpoint": "switchyard",
                  "model": "nvidia/cosmos-3-super-reasoner", "request": {"builder": "p1", "prompt": "p1-v4-base"}}],
    "tasks": {"fake": {"subjects": ["super-direct"], "scorers": ["fake.metric@1"]}},
    "repeats": 2,
    "seed": 20261101,
    "retry_policy": "retry-v2",
    "caps": [{"ledger": "inference", "max_attempts": 30, "max_tokens": 300000}],
}


class FakeTask:
    task = "fake"
    requires = frozenset({"video_input"})

    def plan(self, config, datasets):
        return [base.WorkItem("fake", s.id, f"clip{n}", r, {}) for s in config.task_subjects("fake")
                for n in range(5) for r in range(1, config.repeats + 1)]

    def execute(self, item, subject, ctx):
        raise AssertionError("a plan or dry run must never execute")

    def budget_estimate(self, config, items):
        return {"inference": {"attempts": len(items), "tokens": 9000 * len(items)}}


@pytest.fixture
def fake_task(monkeypatch):
    monkeypatch.setitem(base.TASKS, "fake", FakeTask)
    return FakeTask


def _write(tmp_path, config):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    return path


def test_a_dev_config_validates():
    config = RunConfig.model_validate(CONFIG)
    assert [s.id for s in config.task_subjects("fake")] == ["super-direct"]
    assert config.budget_caps()[0].max_tokens == 300000


def test_a_test_half_run_needs_the_freeze_manifest():
    bad = copy.deepcopy(CONFIG)
    bad["split"]["half"] = "test"
    with pytest.raises(ValidationError, match="freeze manifest"):
        RunConfig.model_validate(bad)
    bad["freeze_manifest"] = "a" * 64
    assert RunConfig.model_validate(bad).split.half == "test"


@pytest.mark.parametrize(("path", "value", "message"), [
    (("tasks", "fake", "subjects"), ["nobody"], "undefined subjects"),
    (("tasks", "fake", "scorers"), ["unversioned"], "task.metric@version"),
    (("retry_policy",), "retry-v9", "unknown retry policy"),
    (("caps",), [{"ledger": "gpu", "max_attempts": 1}], "ledger"),
    (("surprise",), 1, "Extra inputs"),
])
def test_invalid_configs_are_refused(path, value, message):
    bad = copy.deepcopy(CONFIG)
    target = bad
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError, match=message):
        RunConfig.model_validate(bad)


def test_duplicate_subjects_are_refused():
    bad = copy.deepcopy(CONFIG)
    bad["subjects"].append(copy.deepcopy(bad["subjects"][0]))
    with pytest.raises(ValidationError, match="duplicate subject"):
        RunConfig.model_validate(bad)


def test_plan_and_dry_run_never_execute(tmp_path, fake_task, capsys):
    path = _write(tmp_path, CONFIG)
    assert cli.main(["plan", "--config", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["tasks"]["fake"]["items"] == 10
    assert cli.main(["run", "--config", str(path), "--dry-run"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] and report["tasks"]["fake"]["by_subject"] == {"super-direct": 10}


def test_preflight_fails_when_a_cap_cannot_cover_the_worst_case(tmp_path, fake_task, capsys):
    tight = copy.deepcopy(CONFIG)
    tight["caps"] = [{"ledger": "inference", "max_attempts": 20}]  # 10 items x (1 + 2 retries) = 30 needed
    assert cli.main(["preflight", "--config", str(_write(tmp_path, tight))]) == 1
    assert "attempts cap 20 < worst case 30" in capsys.readouterr().out
    missing = copy.deepcopy(CONFIG)
    missing["caps"] = []
    assert cli.main(["preflight", "--config", str(_write(tmp_path, missing))]) == 1
    assert "no cap is set" in capsys.readouterr().out


def test_an_unregistered_task_is_reported(tmp_path, capsys):
    assert cli.main(["plan", "--config", str(_write(tmp_path, CONFIG))]) == 1
    assert "no task registered" in capsys.readouterr().out


def test_a_live_run_is_refused_without_explicit_approval_switches(tmp_path, fake_task, monkeypatch, capsys):
    path = _write(tmp_path, CONFIG)
    monkeypatch.delenv(cli.LIVE_ENV, raising=False)
    assert cli.main(["run", "--config", str(path)]) == 2
    assert cli.main(["run", "--config", str(path), "--live"]) == 2
    assert "refusing a live run" in capsys.readouterr().err


def test_audit_frozen_v1_passes_on_this_tree(capsys):
    assert cli.main(["audit", "frozen-v1"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "problems": []}


def test_manifests_pin_config_inputs_and_code(tmp_path):
    manifest = build_run_manifest(run_id="r1", config=CONFIG, inputs={"split": "s1", "gt": "g1"}, code_dir=tmp_path)
    assert manifest["inputs"] == {"gt": "g1", "split": "s1"} and len(manifest["config_sha256"]) == 64
    assert manifest["code"]["git_sha"] is None and manifest["code"]["evalv2_version"]
    assert git_info(tmp_path) == {"git_sha": None, "dirty": None}
    assert input_drift({"gt": "g1", "split": "s1"}, {"gt": "g2", "prompt": "p"}) == [
        "input 'gt' changed: g1... -> g2...", "input 'prompt' is new since the manifest was written",
        "input 'split' recorded but no longer present"]
