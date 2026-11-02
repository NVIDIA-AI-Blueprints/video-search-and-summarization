# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Severity scoring ``severity-v2-mae``: MAE primary, exact accuracy and QWK secondary, over one shared denominator."""

from __future__ import annotations

import pytest

from evalv2.review.kappa import weighted_kappa
from evalv2.tasks.incident_objects.aggregate import (
    SEVERITY_METRIC_VERSION,
    SEVERITY_STATUSES,
    aggregate,
    severity_metrics,
    severity_status,
)


def run(expected, predicted, *, outcome="scored", incident_type="assault", clip="c", repeat=1):
    return {"incident_id": clip, "repeat": repeat, "outcome": outcome,
            "ground_truth": {"incident": {"type": incident_type, "severity_level": expected}},
            "incident_field_scores": {"type": {"expected": incident_type, "predicted": incident_type, "pass": True},
                                      "severity_level": {"expected": expected, "predicted": predicted,
                                                         "pass": None if predicted is None else expected == predicted}}}


def test_exact_matches_give_zero_mae_full_accuracy_and_perfect_qwk():
    result = severity_metrics([run(1, 1), run(3, 3), run(5, 5)])
    assert result["metric_version"] == SEVERITY_METRIC_VERSION and result["primary"] == "mae"
    assert (result["n"], result["mae"], result["exact"], result["qwk"]) == (3, 0.0, 1.0, 1.0)
    assert result["direction"] == {"mae": "lower_is_better", "exact": "higher_is_better", "qwk": "higher_is_better"}


def test_one_level_errors_cost_one_level_each_and_keep_within_one():
    result = severity_metrics([run(2, 3), run(4, 3), run(3, 3), run(1, 1)])
    assert result["mae"] == 0.5 and result["exact"] == 0.5 and result["within_one"] == 1.0
    assert result["mean_signed_error"] == 0.0 and (result["over_rated"], result["under_rated"]) == (1, 1)
    assert result["abs_error_histogram"] == {"0": 2, "1": 2, "2": 0, "3": 0, "4": 0}


def test_large_errors_weigh_by_distance_where_accuracy_cannot_tell_them_apart():
    near, far = severity_metrics([run(1, 2), run(5, 4)]), severity_metrics([run(1, 5), run(5, 1)])
    assert near["exact"] == far["exact"] == 0.0, "exact accuracy calls both equally wrong"
    assert near["mae"] == 1.0 and far["mae"] == 4.0 and far["within_one"] == 0.0
    assert far["mean_signed_error"] == 0.0 and far["abs_error_histogram"]["4"] == 2


def test_invalid_labels_are_counted_never_scored():
    videos = [run(3, 3), run(3, 0), run(3, 6), run(3, 2.5), run(3, "high"), run(3, True), run(3, "4")]
    result = severity_metrics(videos)
    assert result["n"] == 2 and result["mae"] == 0.5, "the numeric string '4' is a level (V1's rule)"
    assert result["counts"]["invalid_label"] == 5 and result["attempted"] == 7 and result["coverage"] == round(2 / 7, 4)
    assert severity_status(run(3, "high"))[0] == "invalid_label"


def test_missing_predictions_invalid_outputs_and_failed_requests_are_reported_separately():
    videos = [run(2, 2), run(2, None), run(2, None, outcome="contract_invalid"), run(2, None, outcome="parse_failure"),
              run(2, None, outcome="timeout"), run(None, 3)]
    result = severity_metrics(videos)
    assert result["n"] == 1 and result["attempted"] == 6 and result["coverage"] == round(1 / 6, 4)
    assert result["counts"] == {"valid": 1, "missing_prediction": 1, "invalid_output": 2, "no_model_output": 1,
                                "missing_ground_truth": 1, "invalid_label": 0}
    assert set(result["counts"]) == set(SEVERITY_STATUSES) and sum(result["counts"].values()) == 6
    assert result["outcomes_without_valid_severity"] == {"scored": 2, "contract_invalid": 1, "parse_failure": 1,
                                                         "timeout": 1}


def test_an_invalid_output_never_contributes_a_severity_even_if_one_was_parsed():
    assert severity_status(run(2, 2, outcome="contract_invalid"))[0] == "invalid_output"


def test_mae_accuracy_and_qwk_share_one_denominator():
    videos = [run(1, 2), run(2, 2), run(4, 5), run(5, 3), run(3, None), run(3, 9), run(3, None, outcome="timeout")]
    result = severity_metrics(videos)
    valid = [(1, 2), (2, 2), (4, 5), (5, 3)]
    assert result["n"] == result["valid"] == len(valid)
    assert result["mae"] == round(sum(abs(p - g) for g, p in valid) / 4, 4) and result["exact"] == 0.25
    kappa = weighted_kappa({str(i): g for i, (g, _) in enumerate(valid)}, {str(i): p for i, (_, p) in enumerate(valid)},
                           categories=[1, 2, 3, 4, 5], weights="quadratic")["kappa"]
    assert result["qwk"] == pytest.approx(round(kappa, 4))


def test_no_valid_pair_gives_none_metrics_not_zero():
    result = severity_metrics([run(2, None, outcome="contract_invalid")])
    assert (result["n"], result["mae"], result["exact"], result["qwk"], result["coverage"]) == (0, None, None, None, 0.0)


def test_per_category_breakdown_by_ground_truth_type():
    videos = [run(2, 2, incident_type="assault"), run(2, 4, incident_type="assault"),
              run(5, 1, incident_type="explosion"), run(5, None, incident_type="explosion", outcome="contract_invalid")]
    by = severity_metrics(videos)["by_category"]
    assert by["assault"]["n"] == 2 and by["assault"]["mae"] == 1.0 and by["assault"]["exact"] == 0.5
    assert by["explosion"]["n"] == 1 and by["explosion"]["mae"] == 4.0 and by["explosion"]["attempted"] == 2


def test_aggregate_carries_the_v2_severity_block_beside_v1_continuity():
    block = aggregate([run(2, 3), run(4, 4)])
    assert block["severity"]["primary"] == "mae" and block["severity"]["mae"] == 0.5
    assert "severity" in block["continuity_v1"], "V1's own severity summary is kept unchanged for continuity"


def test_severity_report_merges_a_pilot_and_its_recovery_and_refuses_duplicates(tmp_path, capsys):
    import json

    from evalv2 import cli

    pilot, recovery = tmp_path / "pilot.jsonl", tmp_path / "recovery.jsonl"
    rows = [dict(run(2, 3, clip="a"), subject="m"), dict(run(4, None, clip="b", outcome="timeout"), subject="m")]
    pilot.write_text("\n".join(json.dumps(r) for r in rows[:1]) + "\n")
    recovery.write_text(json.dumps(dict(run(4, 4, clip="b"), subject="m")) + "\n")
    out = tmp_path / "report"
    assert cli.main(["severity-report", "--videos", str(pilot), "--videos", str(recovery), "--out", str(out)]) == 0
    row = json.loads(capsys.readouterr().out)["rows"][0]
    assert (row["subject"], row["mae"], row["exact"], row["n"], row["attempted"]) == ("m", 0.5, 0.5, 2, 2)
    assert (out / "severity-mae.svg").read_text().startswith("<svg") and "MAE" in (out / "severity-report.md").read_text()
    assert cli.main(["severity-report", "--videos", str(pilot), "--videos", str(pilot), "--out", str(out)]) == 1
    assert "appears in both" in capsys.readouterr().out


def test_a_track_b_run_without_a_reply_stays_in_every_later_stage_denominator():
    from evalv2.severity_report import report

    ok = [dict(run(2, 2, clip=c), subject="s", stage=st) for c in ("a", "b") for st in ("p1", "final")]
    failed = dict(run(3, None, clip="z", outcome="http_error"), subject="s", stage="p1")
    invalid = dict(run(3, None, clip="y", outcome="parse_failure"), subject="s", stage="p1")
    rows = {r["stage"]: r["severity"] for r in report([*ok, failed, invalid])["rows"]}
    assert rows["final"]["attempted"] == rows["p1"]["attempted"] == 4 and rows["final"]["coverage"] == 0.5
    assert rows["final"]["counts"]["no_model_output"] == 1 and rows["final"]["counts"]["invalid_output"] == 1
