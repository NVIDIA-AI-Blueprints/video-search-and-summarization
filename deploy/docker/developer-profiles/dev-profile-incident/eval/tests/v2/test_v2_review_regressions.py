# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regressions from the Evaluation V2 review of the review kit: label types in ``review kappa``, exact sample sizes."""

from __future__ import annotations

import json
import math

import pytest

from evalv2 import cli
from evalv2.datasets import records as rec
from evalv2.review import kappa, sampler


def _write(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def test_numeric_labels_match_the_ordered_categories(tmp_path, capsys):
    a = _write(tmp_path / "a.jsonl", [{"id": "1", "label": 3}, {"id": "2", "label": 4}, {"id": "3", "label": 1}])
    b = _write(tmp_path / "b.jsonl", [{"id": "1", "label": 3}, {"id": "2", "label": 5}, {"id": "3", "label": 1}])
    assert cli.main(["review", "kappa", "--a", str(a), "--b", str(b), "--categories", "1,2,3,4,5"]) == 0
    result = json.loads(capsys.readouterr().out)
    same_types = kappa.weighted_kappa({"1": 3, "2": 4, "3": 1}, {"1": 3, "2": 5, "3": 1}, categories=[1, 2, 3, 4, 5])
    assert result["n"] == 3 and result["kappa"] == pytest.approx(same_types["kappa"]) == pytest.approx(12 / 13)


def test_an_int_label_and_the_same_label_as_text_agree(tmp_path, capsys):
    a = _write(tmp_path / "a.jsonl", [{"id": str(i), "label": i % 3} for i in range(9)])
    b = _write(tmp_path / "b.jsonl", [{"id": str(i), "label": str(i % 3)} for i in range(9)])
    assert cli.main(["review", "kappa", "--a", str(a), "--b", str(b)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["observed"] == 1.0 and result["kappa"] == pytest.approx(1.0)


def _stratum(n):
    return [rec.new_record(record_id=f"box:Clip{i:03d}:E1:3", dataset="box-references-v0", kind="box",
                           clip=f"Clip{i:03d}", strata={"category": "Assault"}, payload={"second": 3},
                           generator={"prompt": "gen-box-select-v1", "model": "claude"}) for i in range(n)]


@pytest.mark.parametrize(("fraction", "population", "expected"), [
    (0.55, 100, 55),  # 0.55 * 100 == 55.00000000000001 in floating point
    (0.07, 100, 7),   # 7.000000000000001
    (0.28, 25, 7),    # 7.000000000000001
    (0.2, 15, 3),     # the default: exact in floating point too
    (0.2, 7, 2),      # a true fraction still rounds up
])
def test_the_sample_size_is_the_exact_ceiling_of_fraction_times_population(fraction, population, expected):
    sample = sampler.sample_for_review(_stratum(population), seed=7, fraction=fraction)
    (stratum,) = sample.strata.values()
    assert stratum["sampled"] == len(sample.ids) == expected
    assert stratum["inclusion"] == expected / population


def test_the_reproducing_fractions_do_overshoot_in_floating_point():
    """Guards the cases above: they are the ones where the float product rounds up past the true ceiling."""
    assert math.ceil(0.55 * 100) == 56 and math.ceil(0.07 * 100) == 8 and math.ceil(0.28 * 25) == 8
    assert math.ceil(0.2 * 15) == 3, "the review's example (default 0.2 x 15) is exact and never overshot"
