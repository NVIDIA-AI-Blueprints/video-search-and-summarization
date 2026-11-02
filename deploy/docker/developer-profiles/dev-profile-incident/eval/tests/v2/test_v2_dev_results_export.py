# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The committed development results are aggregates only and say they are development results."""

from __future__ import annotations

import csv
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[2] / "methodology" / "results" / "v2-dev"
DOC = RESULTS.parents[1] / "v2-dev-pilot-results.md"


def test_the_results_hold_no_ground_truth_text_urls_or_credentials():
    for path in RESULTS.iterdir():
        text = path.read_text(encoding="utf-8").replace('xmlns="http://www.w3.org/2000/svg"', "")
        for marker in ("X-Amz-", "sk-or-", "nvapi-", "http://", "r2.cloudflarestorage", "data:video"):
            assert marker not in text, (path.name, marker)
        assert path.stat().st_size < 64_000, path.name


def test_every_dimension_has_a_status_and_the_doc_flags_development_only():
    rows = list(csv.DictReader((RESULTS / "dimension_status.csv").open(encoding="utf-8")))
    assert len(rows) == 7 and all(r["status"] for r in rows)
    text = DOC.read_text(encoding="utf-8")
    assert "development results only" in text and "No held-out (C3) result exists yet" in text


def test_the_model_table_has_intervals_for_the_primary_metrics():
    rows = list(csv.DictReader((RESULTS / "track_a_models.csv").open(encoding="utf-8")))
    assert len(rows) == 5
    for r in rows:
        for k in ("contract_valid", "severity_mae"):
            assert r[k] and r[k + "_ci_low"] and r[k + "_ci_high"]
