#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Extract one group's answers, persist its score, and aggregate the final group."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from benchmark.multiple_choice import parse_choice_prediction
from benchmark.scoring import aggregate_overall_mean, score_logic, score_relevance


def _write_reward(value: float, judge: dict) -> None:
    output = Path("/logs/verifier")
    output.mkdir(parents=True, exist_ok=True)
    (output / "reward.txt").write_text(f"{value}\n", encoding="utf-8")
    passed = value == 1.0
    rationale = str(judge.get("rationale", ""))
    payload = {
        "passed": int(passed),
        "total": 1,
        "checks": [{"check": "benchmark verifier", "pass": passed, "rationale": rationale}],
    }
    (output / "judge.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", required=True, type=Path)
    args = parser.parse_args()
    group = json.loads(args.group.read_text(encoding="utf-8"))
    artifact = Path("/logs/artifacts/predictions.json")
    if not artifact.is_file():
        _write_reward(0.0, {"passed": False, "rationale": "predictions.json was not produced"})
        return
    raw = json.loads(artifact.read_text(encoding="utf-8"))
    answers = group["answers"]
    observed = raw.get("predictions", [])
    if len(observed) != 4 or {item.get("case_id") for item in observed} != set(answers):
        _write_reward(0.0, {"passed": False, "rationale": "the group did not produce four identified responses"})
        return
    parsed = [
        parse_choice_prediction(str(item["case_id"]), str(item.get("response", "")))
        for item in observed
    ]
    correctness = [
        prediction is not None and prediction.label == answers[prediction.case_id]
        for prediction in parsed
    ]
    if group["group_type"] == "relevance":
        score = score_relevance(correctness)
    else:
        score = score_logic(correctness, group["group_structure"])

    state = Path(os.environ["SKILL_EVAL_LEG_STATE_DIR"]) / "benchmark-unified-memory"
    state.mkdir(parents=True, exist_ok=True)
    result = {
        "group_id": group["group_id"],
        "group_type": group["group_type"],
        "score": score,
        "correctness": correctness,
        "predictions": [
            {
                "case_id": item["case_id"],
                "label": prediction.label if prediction else None,
                "response": item.get("response", ""),
            }
            for item, prediction in zip(observed, parsed, strict=True)
        ],
    }
    (state / f"{group['group_id']}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if not group["final_group"]:
        _write_reward(1.0, {"passed": True, "rationale": "group executed; final threshold is evaluated later"})
        return

    results = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(state.glob("*.json"))]
    observed_group_ids = {item["group_id"] for item in results}
    if observed_group_ids != set(group["expected_group_ids"]):
        _write_reward(0.0, {"passed": False, "rationale": "benchmark group-result set is incomplete"})
        return
    overall = aggregate_overall_mean([item["score"] for item in results])
    passed = overall >= float(group["minimum"])
    report = {
        "overall_group_score": overall,
        "minimum": group["minimum"],
        "passed": passed,
        "groups": results,
    }
    (Path("/logs/artifacts") / "benchmark-report.json").write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_reward(1.0 if passed else 0.0, {"passed": passed, "rationale": json.dumps(report)})


if __name__ == "__main__":
    main()
