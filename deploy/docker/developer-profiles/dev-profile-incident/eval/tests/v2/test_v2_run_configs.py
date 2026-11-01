# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The committed pilot configs (C1a, C1b, C1b with captions): dev only, plan and preflight pass, caps cover the
worst case with about 30% headroom."""

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
    assert cli.preflight(config, items) == []
    split = json.loads((Path(registry.EVAL_DIR) / "eval_reproducibility" / "split-v2.json").read_text())
    test_clips = set(split["test"])
    assert not {i.item_id for i in items[task]} & test_clips, "a pilot never touches the held-out test split"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_caps_cover_the_worst_case_with_about_thirty_percent_headroom(name):
    config = load_config(CONFIGS / name)
    items = cli.plan(config, _split_only)
    needs = cli.worst_cases(config, items)
    for cap in config.budget_caps():
        drawn = [need for key, need in needs.items() if cap.matches(key[2], key[1], key[0])]
        attempts, tokens = sum(n[0] for n in drawn), sum(n[1] for n in drawn)
        assert attempts and 1.25 <= cap.max_attempts / attempts <= 1.35, (cap, attempts)
        assert 1.25 <= cap.max_tokens / tokens <= 1.35, (cap, tokens)
    ledgers = {(key[1], key[2]) for key in needs}
    covered = {(cap.subject, cap.ledger) for cap in config.budget_caps() if cap.subject != "*"}
    assert ledgers == covered, "every subject has its own cap on every ledger it draws"


def test_a_tighter_cap_fails_preflight():
    config = load_config(CONFIGS / "c1b-track-b-pilot.json")
    caps = [c if c.ledger != "zoom" or c.subject != "*" else c.model_copy(update={"max_attempts": 3999})
            for c in config.caps]
    tight = config.model_copy(update={"caps": caps})
    expected = ("zoom attempts cap 3999 < worst case 4000 (cap subject=*, task=*; draws: pipeline/nano-pipeline, "
                "pipeline/super-pipeline)")
    assert cli.preflight(tight, cli.plan(tight, _split_only)) == [expected]


def test_the_caption_variant_budgets_the_caption_ledger_and_is_pending():
    config = load_config(CONFIGS / "c1b-track-b-pilot-captions.json")
    assert {s.profile for s in config.subjects} == {"production-v4-captions"}
    needs = cli.worst_cases(config, cli.plan(config, _split_only))
    assert needs[("pipeline", "nano-pipeline", "caption")] == (25 * 60, 25 * 60 * 24096)
    assert "PENDING X16" in config.notes


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
