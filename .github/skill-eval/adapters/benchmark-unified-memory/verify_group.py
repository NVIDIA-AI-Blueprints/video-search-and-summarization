#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Evaluate and persist one typed benchmark group."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from benchmark.domain import GroupEvaluationSpec
from benchmark.evaluation import evaluate_group
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
    _write_reward(1.0, group_score.model_dump_json())


if __name__ == "__main__":
    main()
