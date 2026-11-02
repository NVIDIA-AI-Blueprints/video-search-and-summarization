# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The video-level cluster bootstrap: V1's constants, clips as the unit, paired on both-valid runs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from evalv2 import cluster_bootstrap as cb
from evalv2.review.kappa import weighted_kappa

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import methodology


def row(subject, clip, repeat, gt, pred, outcome="scored", type_pass=True, start=(0, 0), end=(5, 5)):
    return {"subject": subject, "incident_id": clip, "repeat": repeat, "outcome": outcome,
            "ground_truth": {"incident": {"type": "assault"}},
            "incident_field_scores": {
                "type": {"expected": "assault", "pass": type_pass if outcome == "scored" else False},
                "severity_level": {"expected": gt, "predicted": pred if outcome == "scored" else None},
                "start_timestamp": {"expected": start[0], "predicted": start[1]},
                "end_timestamp": {"expected": end[0], "predicted": end[1]}},
            "entities": {"matches": [1], "unmatched_model": [], "unmatched_gt": []}}


def test_constants_are_v1s():
    assert (cb.RESAMPLES, cb.SEED, cb.CONFIDENCE) == (methodology.BOOTSTRAP_RESAMPLES, methodology.BOOTSTRAP_SEED,
                                                       methodology.BOOTSTRAP_CONFIDENCE)


def test_point_estimates_match_the_scorer_and_qwk_matches_the_review_kit():
    rows = [row("a", f"c{i}", r, gt, pred) for i, (gt, pred) in enumerate([(1, 2), (2, 2), (3, 5), (4, 4), (5, 3)])
            for r in (1, 2)]
    est = cb.subject_intervals(rows, resamples=500)["metrics"]
    assert est["severity_mae"]["estimate"] == pytest.approx((1 + 0 + 2 + 0 + 2) / 5)
    kappa = weighted_kappa({str(i): r["incident_field_scores"]["severity_level"]["expected"] for i, r in enumerate(rows)},
                           {str(i): r["incident_field_scores"]["severity_level"]["predicted"] for i, r in enumerate(rows)},
                           categories=[1, 2, 3, 4, 5], weights="quadratic")["kappa"]
    assert est["severity_qwk"]["estimate"] == pytest.approx(round(kappa, 4))


def test_repeats_are_clustered_so_duplicating_them_does_not_narrow_the_interval():
    base = [row("a", f"c{i}", 1, 3, 3 + (i % 3)) for i in range(12)]
    doubled = base + [{**r, "repeat": 2} for r in base]
    def width(rows):
        ci = cb.subject_intervals(rows, resamples=2000)["metrics"]["severity_mae"]["ci95"]
        return ci[1] - ci[0]

    assert width(doubled) == pytest.approx(width(base), abs=1e-9), "identical repeats add no independent evidence"


def test_invalid_runs_count_for_validity_but_not_for_conditional_metrics():
    rows = [row("a", "c1", 1, 3, 3), row("a", "c1", 2, 3, None, outcome="contract_invalid"), row("a", "c2", 1, 2, 4)]
    m = cb.subject_intervals(rows, resamples=200)["metrics"]
    assert m["contract_valid"]["estimate"] == pytest.approx(2 / 3, abs=1e-4) and m["severity_mae"]["estimate"] == 1.0


def test_paired_conditional_metrics_use_only_runs_both_models_got_valid():
    a = [row("a", "c1", 1, 3, 3), row("a", "c2", 1, 3, 3), row("a", "c3", 1, 3, 1)]
    b = [row("b", "c1", 1, 3, 4), row("b", "c2", 1, 3, None, outcome="contract_invalid"), row("b", "c3", 1, 3, 3)]
    d = cb.paired_difference(a, b, resamples=500)
    assert d["both_valid_runs"] == 2
    sev = d["metrics"]["severity_mae"]
    assert (sev["a"], sev["b"], sev["difference"]) == (1.0, 0.5, 0.5), "c2 is left out of both sides"
    assert d["metrics"]["contract_valid"]["difference"] == pytest.approx(1 / 3, abs=1e-4)


def test_results_are_reproducible_for_a_fixed_seed():
    rows = [row("a", f"c{i}", 1, 3, 1 + i % 5) for i in range(10)]
    assert cb.subject_intervals(rows, resamples=300) == cb.subject_intervals(rows, resamples=300)
