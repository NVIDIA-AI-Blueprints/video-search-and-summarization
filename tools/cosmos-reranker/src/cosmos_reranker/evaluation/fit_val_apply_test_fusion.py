#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fit normalized SigLIP2/CR3 fusion on validation and apply it to test."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from cosmos_reranker.evaluation.sweep_saved_rerank_fusion import evaluate, load_trace, unique_grid


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-rankings", type=Path, required=True)
    parser.add_argument("--test-rankings", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--calibration-summary",
        type=Path,
        help="Reuse a previously fitted the validation query subset alpha and score statistics instead of refitting.",
    )
    return parser.parse_args()


def fit_statistics(data: dict[str, np.ndarray]) -> dict[str, dict[str, float]]:
    return {
        key: {"mean": float(data[key].mean()), "std": float(data[key].std())}
        for key in ("stage1", "cr3")
    }


def normalize_with_statistics(
    data: dict[str, np.ndarray], statistics: dict[str, dict[str, float]]
) -> dict[str, np.ndarray]:
    normalized = dict(data)
    for key in ("stage1", "cr3"):
        mean = statistics[key]["mean"]
        std = statistics[key]["std"]
        if std <= 0:
            raise ValueError(f"validation standard deviation for {key} is not positive")
        normalized[key] = (data[key] - mean) / std
    return normalized


def sweep(data: dict[str, np.ndarray]) -> list[dict[str, float]]:
    coarse = unique_grid(np.linspace(0.0, 1.0, 201))
    coarse_rows = [evaluate(data, alpha) for alpha in coarse]
    coarse_best = max(coarse_rows, key=lambda row: row["overall_mAP"])
    fine = unique_grid(
        np.arange(coarse_best["alpha"] - 0.01, coarse_best["alpha"] + 0.01001, 0.0001)
    )
    fine_rows = [evaluate(data, alpha) for alpha in fine]
    fine_best = max(fine_rows, key=lambda row: row["overall_mAP"])
    ultra = unique_grid(
        np.arange(fine_best["alpha"] - 0.0002, fine_best["alpha"] + 0.000201, 0.00001)
    )
    ultra_rows = [evaluate(data, alpha) for alpha in ultra]
    rows_by_alpha = {row["alpha"]: row for row in coarse_rows + fine_rows + ultra_rows}
    return [rows_by_alpha[alpha] for alpha in sorted(rows_by_alpha)]


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    validation_raw = load_trace(args.validation_rankings)
    test_raw = load_trace(args.test_rankings)
    calibration = (
        json.loads(args.calibration_summary.read_text()) if args.calibration_summary else None
    )
    if calibration and calibration.get("selection_metric") != "validation overall mAP":
        raise ValueError("Calibration must have been selected on validation overall mAP")
    statistics = (
        calibration["validation_score_statistics"]
        if calibration
        else fit_statistics(validation_raw)
    )
    validation = normalize_with_statistics(validation_raw, statistics)
    test = normalize_with_statistics(test_raw, statistics)

    validation_rows = (
        [evaluate(validation, calibration["frozen_alpha"])] if calibration else sweep(validation)
    )
    validation_best = max(validation_rows, key=lambda row: row["overall_mAP"])
    frozen_alpha = validation_best["alpha"]

    with (args.output_dir / "validation_alpha_sweep.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(validation_rows[0]))
        writer.writeheader()
        writer.writerows(validation_rows)

    summary = {
        "protocol": (
            "Fit global z-score means/stds and alpha on the validation query subset only; apply all frozen "
            "hyperparameters unchanged to the deduplicated full test trace."
        ),
        "formula": (
            "alpha * ((SigLIP2_score - val_mean) / val_std) + "
            "(1 - alpha) * ((CR3_score - val_mean) / val_std)"
        ),
        "selection_metric": "validation overall mAP",
        "validation_rankings": str(args.validation_rankings.resolve()),
        "test_rankings": str(args.test_rankings.resolve()),
        "validation_score_statistics": statistics,
        "frozen_alpha": frozen_alpha,
        "validation_at_selected_alpha": validation_best,
        "validation_cr3_only": evaluate(validation, 0.0),
        "validation_siglip2_only": evaluate(validation, 1.0),
        "test_at_frozen_validation_alpha": evaluate(test, frozen_alpha),
        "test_cr3_only": evaluate(test, 0.0),
        "test_siglip2_only": evaluate(test, 1.0),
    }
    if calibration:
        summary["protocol"] = (
            "Reuse the original validation-fitted alpha and score statistics unchanged; "
            "evaluate the new validation and test traces without refitting."
        )
        summary["calibration_source"] = str(args.calibration_summary.resolve())
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
