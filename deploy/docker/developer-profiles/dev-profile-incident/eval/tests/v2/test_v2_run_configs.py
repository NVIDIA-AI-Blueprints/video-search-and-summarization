# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The committed pilot configs (C1a, C1b, C1b with captions): dev-only pilots, plan and preflight pass, and the caps are
the owner-approved budgets (``budget_mode: hard_stop``), with every gap to the worst case listed."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from evalv2 import cli
from evalv2.core.config import load_config
from evalv2.datasets import registry
from evalv2.datasets.loader import load_for_config

CONFIGS = Path(__file__).resolve().parents[2] / "experiments" / "v2"
REAL_DATA_ROOT = Path(os.environ.get(registry.DATA_ROOT_ENV) or registry.DEFAULT_DATA_ROOT)
REAL_GT = (REAL_DATA_ROOT / "ground_truth" / "ground-truth-v1.0").is_dir()
EXPECTED = {
    "c1a-track-a-pilot.json": ("incident_objects", {"nano-direct": 50, "super-direct": 50, "omni-direct": 50}),
    "c1b-track-b-pilot.json": ("pipeline", {"nano-pipeline": 25, "super-pipeline": 25}),
    "c1b-track-b-pilot-captions.json": ("pipeline", {"nano-pipeline": 25, "super-pipeline": 25}),
    "c1a-recovery-super.json": ("incident_objects", {"super-direct": 14}),
    "c1e-external-baseline-pilot.json": ("incident_objects", {"gemma4-31b-direct": 50}),
    "c1e-openrouter-gemma4-31b.json": ("incident_objects", {"gemma4-31b-openrouter": 50}),
    "c1f-openrouter-qwen35-9b.json": ("incident_objects", {"qwen35-9b-openrouter": 50}),
}


def _split_only(config):
    """The config's datasets without the private ground truth: the split alone gives the clips."""
    return load_for_config(config.model_copy(update={"datasets": {}}))


def test_every_committed_v2_config_is_expected():
    assert sorted(p.name for p in CONFIGS.glob("*.json")) == sorted(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_a_pilot_config_runs_on_dev_only_and_preflights(name):
    config = load_config(CONFIGS / name)
    assert config.split.half == "dev" and config.freeze_manifest is None
    assert config.datasets == {"incident_gt": "ground-truth-v1.0"}
    task, by_subject = EXPECTED[name]
    items = cli.plan(config, _split_only)
    assert {s: sum(1 for i in items[task] if i.subject == s) for s in by_subject} == by_subject
    assert config.purpose == "pilot" and config.budget_mode == "hard_stop"
    blocking, hard_stop = cli.preflight_problems(config, items)
    assert blocking == [] and hard_stop, "approved caps sit below the pessimistic worst case, and say so"
    split = json.loads((Path(registry.EVAL_DIR) / "eval_reproducibility" / "split-v2.json").read_text())
    test_clips = set(split["test"])
    assert not {i.item_id for i in items[task]} & test_clips, "a pilot never touches the held-out test split"


APPROVED_TOKENS = {"c1a-track-a-pilot.json": 6_000_000, "c1b-track-b-pilot.json": 9_000_000,
                   "c1b-track-b-pilot-captions.json": 9_000_000,
                   "c1a-recovery-super.json": 500_000,
                   "c1e-external-baseline-pilot.json": 1_500_000,
                   "c1e-openrouter-gemma4-31b.json": 1_500_000,  # proposed: OpenRouter route
                   "c1f-openrouter-qwen35-9b.json": 1_500_000}  # proposed: OpenRouter route  # proposed (not yet approved): external baseline  # inside C1a's 6 M with C1a's 1.84 M spend


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_run_wide_caps_sum_to_the_approved_budget_and_every_subject_is_capped(name):
    config = load_config(CONFIGS / name)
    items = cli.plan(config, _split_only)
    needs = cli.worst_cases(config, items)
    run_wide = [cap for cap in config.budget_caps() if cap.subject == "*" and cap.ledger != "judge"]
    assert sum(cap.max_tokens for cap in run_wide) == APPROVED_TOKENS[name]
    assert {cap.ledger for cap in run_wide} == {key[2] for key in needs}, "every charged ledger has a run-wide cap"
    for cap in config.budget_caps():
        if cap.subject != "*":
            whole = next(c for c in run_wide if c.ledger == cap.ledger)
            assert cap.max_tokens <= whole.max_tokens and cap.max_attempts <= whole.max_attempts
    ledgers = {(key[1], key[2]) for key in needs}
    covered = {(cap.subject, cap.ledger) for cap in config.budget_caps() if cap.subject != "*"}
    assert ledgers == covered, "every subject has its own cap on every ledger it draws"
    judge = [cap for cap in config.budget_caps() if cap.ledger == "judge"]
    assert len(judge) == 1 and config.tasks[EXPECTED[name][0]].options["judges"]["primary"] == "catalog-gpt-oss-20b-v1"


def test_the_judge_shares_stay_within_the_approved_judge_budget():
    shares = [next(c for c in load_config(CONFIGS / name).caps if c.ledger == "judge")
              for name in ("c1a-track-a-pilot.json", "c1b-track-b-pilot.json")]
    assert sum(c.max_attempts for c in shares) <= 2000 and sum(c.max_tokens for c in shares) <= 3_000_000


def test_worst_case_mode_still_blocks_a_shortfall():
    config = load_config(CONFIGS / "c1a-track-a-pilot.json").model_copy(update={"budget_mode": "worst_case"})
    blocking, hard_stop = cli.preflight_problems(config, cli.plan(config, _split_only))
    assert hard_stop == [] and any("< worst case" in p for p in blocking)


def test_a_tighter_cap_fails_preflight():
    config = load_config(CONFIGS / "c1b-track-b-pilot.json")
    caps = [c if c.ledger != "zoom" or c.subject != "*" else c.model_copy(update={"max_attempts": 3999})
            for c in config.caps]
    tight = config.model_copy(update={"caps": caps, "budget_mode": "worst_case"})
    tight = tight.model_copy(update={"caps": [c.model_copy(update={"max_tokens": 10**9}) if c.ledger != "judge" else c
                                              for c in tight.caps]})
    tight = tight.model_copy(update={"caps": [c.model_copy(update={"max_attempts": 10**6})
                                              if c.ledger != "judge" and (c.ledger, c.subject) != ("zoom", "*") else c
                                              for c in tight.caps]})
    expected = ("zoom attempts cap 3999 < worst case 4000 (cap subject=*, task=*; draws: pipeline/nano-pipeline, "
                "pipeline/super-pipeline)")
    assert cli.preflight(tight, cli.plan(tight, _split_only)) == [expected]


def test_the_caption_variant_budgets_the_caption_ledger_and_is_gated():
    config = load_config(CONFIGS / "c1b-track-b-pilot-captions.json")
    assert {s.profile for s in config.subjects} == {"production-v4-captions"}
    needs = cli.worst_cases(config, cli.plan(config, _split_only))
    assert needs[("pipeline", "nano-pipeline", "caption")] == (25 * 30, 25 * 30 * 13000)
    assert "enabled_profiles" in config.notes


@pytest.mark.skipif(not REAL_GT, reason="the frozen ground truth is private and not on this machine")
@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_plan_and_preflight_pass_offline_on_the_frozen_ground_truth(name, capsys):
    for command in ("plan", "preflight"):
        assert cli.main([command, "--config", str(CONFIGS / name), "--data-root", str(REAL_DATA_ROOT)]) == 0
    printed, decoder, reports = capsys.readouterr().out, json.JSONDecoder(), []
    while printed.strip():
        report, end = decoder.raw_decode(printed.lstrip())
        reports.append(report)
        printed = printed.lstrip()[end:]
    assert reports[1]["ok"] and reports[1]["half"] == "dev" and reports[1]["problems"] == []
    assert reports[1]["budget_mode"] == "hard_stop" and reports[1]["hard_stop"]


def test_the_c1a_recovery_stays_inside_the_approved_track_a_totals():
    """C1a's caps plus the recovery's: no new work items, at most 195 attempts and 6 M tokens after C1a's spend."""
    c1a = load_config(CONFIGS / "c1a-track-a-pilot.json")
    recovery = load_config(CONFIGS / "c1a-recovery-super.json")
    assert recovery.recovery is not None and recovery.recovery.of_experiment == c1a.experiment
    whole = next(c for c in recovery.caps if c.ledger == "inference" and c.subject == "*")
    c1a_used_attempts, c1a_used_tokens = 175, 1_836_208  # C1a's settled ledger at its exit 4
    assert c1a_used_attempts + whole.max_attempts <= 195 and c1a_used_tokens + whole.max_tokens <= 6_000_000
    keys = {(i.subject, i.item_id, i.repeat) for i in recovery.recovery.items}
    planned = cli.plan(c1a, _split_only)["incident_objects"]
    assert keys <= {(i.subject, i.item_id, i.repeat) for i in planned}, "only C1a's own work items"
    assert next(c for c in recovery.caps if c.ledger == "judge").max_attempts == 0
