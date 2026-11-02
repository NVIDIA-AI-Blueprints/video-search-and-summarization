# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regressions from the Evaluation V2 review of the core package: checkpoint finality, preflight scope, audit."""

from __future__ import annotations

import copy
import json

import pytest

from evalv2 import cli
from evalv2.core.artifacts import ArtifactStore
from evalv2.core.checkpoint import Checkpoint, CheckpointError, is_final_outcome
from evalv2.core.config import RunConfig
from evalv2.tasks import base

# --- checkpoint: only infrastructure failures, budget refusals and unsent attempts stay open ---------------------


@pytest.mark.parametrize("outcome", ["http_error", "scored", "contract_invalid", "parse_failure"])
def test_non_infrastructure_outcomes_are_final_and_never_resent(tmp_path, outcome):
    path = tmp_path / "progress.jsonl"
    Checkpoint(path).record("k1", outcome)
    resumed = Checkpoint(path)
    assert resumed.is_final("k1"), f"a resumed run would pay for {outcome!r} again"
    with pytest.raises(CheckpointError, match="already has a final result"):
        resumed.record("k1", "ok")


@pytest.mark.parametrize("outcome", ["rate_limited", "timeout", "transport_error", "host_suspended",
                                     "budget_exceeded", "not_sent"])
def test_infrastructure_failures_budget_refusals_and_unsent_attempts_stay_open(tmp_path, outcome):
    checkpoint = Checkpoint(tmp_path / "progress.jsonl")
    checkpoint.record("k1", outcome)
    assert not checkpoint.is_final("k1") and checkpoint.attempts("k1") == 1


def test_the_failure_kind_decides_when_v1s_label_folds_failures_into_http_error(tmp_path):
    # V1's classify_outcome labels a transport error and a budget refusal ``http_error`` too.
    path = tmp_path / "progress.jsonl"
    checkpoint = Checkpoint(path)
    checkpoint.record("transport", "http_error", failure_kind="transport_error")
    checkpoint.record("refused", "http_error", failure_kind="budget_exceeded")
    checkpoint.record("status", "http_error", failure_kind="http_error")
    resumed = Checkpoint(path)
    assert not resumed.is_final("transport") and not resumed.is_final("refused") and resumed.is_final("status")
    assert is_final_outcome("timeout") is False and is_final_outcome("http_error") is True


def test_an_unclassified_outcome_is_refused_not_guessed(tmp_path):
    checkpoint = Checkpoint(tmp_path / "progress.jsonl")
    with pytest.raises(CheckpointError, match="unclassified outcome 'answered'"):
        checkpoint.record("k1", "answered")
    with pytest.raises(CheckpointError, match="unknown failure kind"):
        checkpoint.record("k1", "http_error", failure_kind="gateway_hiccup")
    assert not (tmp_path / "progress.jsonl").exists(), "nothing is written for a refused outcome"


# --- preflight: subject scopes and caps shared across tasks -------------------------------------------------------

CONFIG = {
    "experiment": "v2-dev-preflight",
    "split": {"version": "split-v2", "half": "dev"},
    "subjects": [{"id": "super-direct", "adapter": "openai_compat", "endpoint": "switchyard",
                  "model": "nvidia/cosmos-3-super-reasoner"},
                 {"id": "nano-direct", "adapter": "openai_compat", "endpoint": "switchyard",
                  "model": "nvidia/cosmos-3-nano-reasoner"}],
    "tasks": {"fake": {}},
    "repeats": 2,
    "seed": 1,
    "retry_policy": "retry-v2",
}


class FakeTask:
    """Ten items per subject (5 clips x 2 repeats), one attempt each: 30 attempts per subject with retry-v2."""

    task = "fake"
    requires = frozenset({"video_input"})

    def plan(self, config, datasets):
        return [base.WorkItem(self.task, s.id, f"clip{n}", r, {}) for s in config.task_subjects(self.task)
                for n in range(5) for r in range(1, config.repeats + 1)]

    def execute(self, item, subject, ctx):
        raise AssertionError("preflight never executes")

    def budget_estimate(self, config, items):
        return {"inference": {"attempts": len(items), "tokens": 9000 * len(items)}}


class OtherTask(FakeTask):
    task = "other"


@pytest.fixture
def fake_tasks(monkeypatch):
    monkeypatch.setitem(base.TASKS, "fake", FakeTask)
    monkeypatch.setitem(base.TASKS, "other", OtherTask)


def _preflight(**changes):
    config = RunConfig.model_validate({**copy.deepcopy(CONFIG), **changes})
    return cli.preflight(config, cli.plan(config))


def test_a_subject_scoped_cap_does_not_cover_another_subject(fake_tasks):
    problems = _preflight(caps=[{"ledger": "inference", "max_attempts": 100, "subject": "super-direct"}])
    assert problems == ["task 'fake' needs ledger 'inference' for subject 'nano-direct' but no cap is set"]
    assert _preflight(caps=[{"ledger": "inference", "max_attempts": 30, "subject": "super-direct"},
                            {"ledger": "inference", "max_attempts": 30, "subject": "nano-direct"}]) == []


def test_a_subject_scoped_cap_is_checked_against_that_subject_only(fake_tasks):
    problems = _preflight(caps=[{"ledger": "inference", "max_attempts": 29, "subject": "super-direct"},
                                {"ledger": "inference", "max_attempts": 30, "subject": "nano-direct"}])
    assert len(problems) == 1 and problems[0].startswith("inference attempts cap 29 < worst case 30")
    assert "draws: fake/super-direct)" in problems[0]


def test_a_cap_shared_by_two_tasks_must_cover_both_together(fake_tasks):
    shared = {"tasks": {"fake": {"subjects": ["super-direct"]}, "other": {"subjects": ["super-direct"]}}}
    problems = _preflight(**shared, caps=[{"ledger": "inference", "max_attempts": 50}])  # each task fits alone
    assert len(problems) == 1 and problems[0].startswith("inference attempts cap 50 < worst case 60")
    assert "draws: fake/super-direct, other/super-direct" in problems[0]
    assert _preflight(**shared, caps=[{"ledger": "inference", "max_attempts": 60}]) == []
    per_task = [{"ledger": "inference", "max_attempts": 30, "task": "fake"},
                {"ledger": "inference", "max_attempts": 30, "task": "other"}]
    assert _preflight(**shared, caps=per_task) == []


def test_tokens_are_summed_like_attempts(fake_tasks):
    problems = _preflight(caps=[{"ledger": "inference", "max_tokens": 500000}])  # 2 subjects x 270,000 needed
    assert len(problems) == 1 and problems[0].startswith("inference tokens cap 500000 < worst case 540000")


# --- audit run: a damaged sealed run is listed, never a traceback -------------------------------------------------


def _sealed(tmp_path):
    store = ArtifactStore(tmp_path / "store")
    run = store.run("r1")
    run.record("item-1", store.put("prediction", {"n": 1}), outcome="ok")
    return store, run, run.seal({})


def test_a_missing_manifest_is_reported_not_raised(tmp_path, capsys):
    store, run, manifest = _sealed(tmp_path)
    store._path(manifest, ".json").unlink()
    problems = run.verify()
    assert len(problems) == 1 and "missing artifact run_manifest:" in problems[0]
    assert cli.main(["audit", "run", "--store", str(tmp_path / "store"), "--run", "r1"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_a_corrupt_manifest_and_a_broken_seal_are_reported_not_raised(tmp_path):
    store, run, manifest = _sealed(tmp_path)
    path = store._path(manifest, ".json")
    path.write_bytes(path.read_bytes() + b" ")
    assert any("corrupt artifact run_manifest:" in p for p in run.verify())
    (run.root / "SEALED").write_text("not a reference")
    assert run.verify()[0].startswith("run r1: unreadable SEALED marker")


def test_an_index_that_is_no_longer_jsonl_is_reported_not_raised(tmp_path):
    _, run, _ = _sealed(tmp_path)
    run.index_path.write_text(run.index_path.read_text() + "{broken\n")
    problems = run.verify()
    assert any("index.jsonl changed after sealing" in p for p in problems)
    assert any("index.jsonl is not valid JSONL" in p for p in problems)
