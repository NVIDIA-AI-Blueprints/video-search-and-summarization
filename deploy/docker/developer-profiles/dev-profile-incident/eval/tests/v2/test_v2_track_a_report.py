# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The Track A comparison report: full-set counts never drop items; valid-output metrics share one denominator."""

from __future__ import annotations

import json

import pytest

from evalv2 import track_a_report as tar


def row(subject, clip, outcome="scored", sev=(2, 2), type_pass=True, repeat=1):
    return {"subject": subject, "incident_id": clip, "repeat": repeat, "outcome": outcome,
            "ground_truth": {"incident": {"type": "assault"}},
            "incident_field_scores": {
                "type": {"expected": "assault", "predicted": "assault" if type_pass else None, "pass": type_pass},
                "severity_level": {"expected": sev[0], "predicted": sev[1] if outcome == "scored" else None,
                                   "pass": sev[0] == sev[1] and outcome == "scored"}},
            "entities": {"counts": None, "matches": [], "unmatched_gt": [], "unmatched_model": []},
            "instruments": {"counts": None, "matches": [], "unmatched_gt": [], "unmatched_model": []},
            "assets": {"counts": None, "matches": [], "unmatched_gt": [], "unmatched_model": []},
            "scoring_errors": []}


def test_full_set_keeps_invalid_items_and_valid_metrics_use_only_valid_outputs():
    videos = [row("a", "c1", sev=(2, 3)), row("a", "c2", outcome="contract_invalid", type_pass=False),
              row("b", "c1", sev=(2, 2)), row("b", "c2", sev=(4, 1))]
    result = tar.report(videos, [], {"c1": 20.0, "c2": 90.0})
    a, b = result["subjects"]["a"], result["subjects"]["b"]
    assert a["full_set"]["attempted"] == 2 and a["full_set"]["contract_valid_share"] == 0.5
    assert a["full_set"]["type_accuracy_end_to_end"] == 0.5 and a["valid"]["type_accuracy"] == 1.0
    assert a["valid"]["severity"]["n"] == 1 and a["valid"]["severity"]["mae"] == 1.0
    assert result["paired_items"] == 1 and b["paired"]["severity"]["mae"] == 0.0, "paired: only c1, both scored"
    assert set(a["by_duration"]) == {"<=30s", ">60s"} and a["by_duration"][">60s"]["valid"] is None


def test_a_work_item_in_two_runs_is_refused(tmp_path):
    for run in ("r1", "r2"):
        d = tmp_path / run / "scoring" / "v" / "primary"
        d.mkdir(parents=True)
        (d / "offline-videos.jsonl").write_text(json.dumps(row("a", "c1")) + "\n")
    with pytest.raises(tar.TrackAReportError, match="appears in both"):
        tar.load([tmp_path / "r1", tmp_path / "r2"], "v")
