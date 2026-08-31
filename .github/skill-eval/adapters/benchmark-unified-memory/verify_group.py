#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Evaluate one typed benchmark group and aggregate the final result."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from benchmark.domain import GroupEvaluationSpec, GroupScore
from benchmark.evaluation import aggregate_benchmark, evaluate_group
from benchmark.prediction_artifacts import load_prediction_artifacts
from pydantic import BaseModel


def _write_reward(value: float, rationale: str) -> None:
    output = Path("/logs/verifier")
    output.mkdir(parents=True, exist_ok=True)
    (output / "reward.txt").write_text(f"{value}\n", encoding="utf-8")
    passed = value == 1.0
    payload = {
        "passed": int(passed),
        "total": 1,
        "checks": [
            {"check": "benchmark verifier", "pass": passed, "rationale": rationale}
        ],
    }
    (output / "judge.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def _write_model_atomic(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _load_group_scores(state: Path) -> tuple[GroupScore, ...]:
    return tuple(
        GroupScore.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(state.glob("*.json"))
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", required=True, type=Path)
    args = parser.parse_args()

    try:
        spec = GroupEvaluationSpec.model_validate_json(
            args.group.read_text(encoding="utf-8")
        )
        observed = load_prediction_artifacts(
            Path("/logs/artifacts"),
            tuple(item.case_id for item in spec.expected_answers),
        )
        group_score = evaluate_group(spec, observed)
    except (OSError, ValueError) as exc:
        _write_reward(0.0, str(exc))
        return

    state = (
        Path(os.environ["SKILL_EVAL_LEG_STATE_DIR"])
        / "benchmark-unified-memory"
        / "groups"
    )
    _write_model_atomic(state / f"{spec.group_id}.json", group_score)
    if not spec.final_group:
        _write_reward(1.0, "group executed; final threshold is evaluated later")
        return

    try:
        report = aggregate_benchmark(
            _load_group_scores(state),
            expected_group_ids=spec.expected_group_ids,
            minimum=spec.minimum,
        )
    except (OSError, ValueError) as exc:
        _write_reward(0.0, str(exc))
        return

    _write_model_atomic(Path("/logs/verifier") / "benchmark-report.json", report)
    _write_reward(1.0 if report.passed else 0.0, report.model_dump_json())


if __name__ == "__main__":
    main()
