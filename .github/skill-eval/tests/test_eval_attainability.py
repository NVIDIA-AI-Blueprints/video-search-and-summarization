#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Tests for eval_attainability."""

from __future__ import annotations

import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import eval_attainability as att  # noqa: E402


@pytest.mark.parametrize(
    "name,expected",
    [
        ("skills-eval-daily-results-vss-search-archive__search__RTXPRO6000BW-31770137518.tar.gz",
         ("vss-search-archive", "search", "RTXPRO6000BW")),
        ("something-else.tar.gz", None),
        ("skills-eval-daily-results-malformed-1.tar.gz", None),
    ],
)
def test_parse_artifact_name(name, expected):
    assert att.parse_artifact_name(name) == expected


def test_chain_abort_marks_later_cells_skipped_not_absent():
    """A cell after a sub-1.0 step is starved every night that step is imperfect,
    which is a different problem from a leg that simply did not run. Conflating
    the two hides a permanently unreachable cell."""
    labels = att.classify_leg(9, {1: 1.0, 2: 0.6})
    assert labels[1] == labels[2] == att.EXECUTED
    assert all(labels[c] == att.SKIPPED for c in range(3, 10))
    assert set(att.classify_leg(3, {}).values()) == {att.ABSENT}
    assert set(att.classify_leg(3, {1: 1.0, 2: 1.0, 3: 1.0}).values()) == {att.EXECUTED}


def _tarball(path: Path, steps: dict[int, float], flat: bool = False) -> None:
    """`flat` mimics a single-step spec: trial dir `<platform>__<hash>`, no step."""
    with tarfile.open(path, "w:gz") as tf:
        for step, reward in steps.items():
            payload = json.dumps({"step": step, "reward": reward}).encode()
            trial = "l40s__abc" if flat else f"step-{step}__abc"
            info = tarfile.TarInfo(f"123/2026-01-01/{trial}/verifier/judge.json")
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))


@pytest.mark.parametrize("flat", [False, True])
def test_read_leg_takes_the_step_from_judge_json_not_the_path(tmp_path, flat):
    """Single-step specs produce a flat trial dir. Keying off the path would drop
    every one of their verdicts and misreport those cells as never sampled."""
    p = tmp_path / "leg.tar.gz"
    _tarball(p, {1: 1.0}, flat=flat)
    assert att.read_leg(p) == {1: 1.0}


def test_analyse_counts_samples_across_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(att, "spec_cell_counts", lambda: {("skl", "spec"): 3})
    for run in (1, 2):
        _tarball(tmp_path / f"skills-eval-daily-results-skl__spec__L40S-{run}.tar.gz",
                 {1: 1.0, 2: 0.5})

    result = att.analyse(tmp_path, min_samples=2)
    by_cell = {c["cell"]: c for c in result["cells"]}
    assert result["runs_seen"] == 2
    # Cells 1 and 2 ran on both nights; cell 3 was starved by the abort both times.
    assert by_cell[1]["samples"] == 2 and by_cell[1]["reachable"]
    assert by_cell[2]["samples"] == 2
    assert by_cell[3]["samples"] == 0 and not by_cell[3]["reachable"]
    assert by_cell[3]["labels"] == {att.SKIPPED: 2}
    assert result["cells_starved"] == 1
