#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Aggregate persisted benchmark groups and apply the final threshold."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from benchmark.domain import AggregateEvaluationSpec, BenchmarkReport, GroupScore
from benchmark.evaluation import aggregate_benchmark


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


def _write_report(path: Path, report: BenchmarkReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _load_group_scores(state: Path) -> tuple[GroupScore, ...]:
    return tuple(
        GroupScore.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(state.glob("*.json"))
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aggregate", required=True, type=Path)
    args = parser.parse_args()

    try:
        spec = AggregateEvaluationSpec.model_validate_json(
            args.aggregate.read_text(encoding="utf-8")
        )
        state = (
            Path(os.environ["SKILL_EVAL_LEG_STATE_DIR"])
            / "benchmark-unified-memory"
            / "groups"
        )
        report = aggregate_benchmark(
            _load_group_scores(state),
            expected_group_ids=spec.expected_group_ids,
            minimum=spec.minimum,
        )
    except (KeyError, OSError, ValueError) as exc:
        _write_reward(0.0, str(exc))
        return

    _write_report(Path("/logs/verifier") / "benchmark-report.json", report)
    _write_reward(1.0 if report.passed else 0.0, report.model_dump_json())


if __name__ == "__main__":
    main()
