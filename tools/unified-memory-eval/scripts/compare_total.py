#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Aggregate per-video VSS eval metric JSON files into total.md."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def fmt_ratio(num: int, den: int) -> str:
    if den == 0:
        return "n/a"
    value = num / den
    return f"{num}/{den} = {value:.3f}".rstrip("0").rstrip(".")


def pct(num: int, den: int) -> str:
    if den == 0:
        return "n/a"
    return f"{(num / den) * 100:.1f}%"


def compare_total(run_dir: Path, output: Path | None = None) -> Path:
    files = sorted(run_dir.glob("*_metrics.json"))
    if not files:
        raise FileNotFoundError(f"No *_metrics.json files found in {run_dir}")

    totals = {
        "videos": len(files),
        "total_questions": 0,
        "judged_answers": 0,
        "correct_answers": 0,
        "exact_evidence_matches": 0,
        "correct_cited_ids": 0,
        "total_cited_ids": 0,
        "expected_ids_count": 0,
        "extra_ids_count": 0,
        "missing_ids_count": 0,
    }
    video_names: list[str] = []

    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        video_names.append(data["video_name"])
        for key in totals:
            if key != "videos":
                totals[key] += int(data.get(key, 0))

    precision = (
        totals["correct_cited_ids"] / totals["total_cited_ids"]
        if totals["total_cited_ids"]
        else 0.0
    )
    recall = (
        totals["correct_cited_ids"] / totals["expected_ids_count"]
        if totals["expected_ids_count"]
        else 0.0
    )
    f1 = 0.0 if precision + recall == 0 else (2 * precision * recall) / (precision + recall)

    resolved_output = output or run_dir / "total.md"
    lines = [
        "# Total Eval Report",
        "",
        f"Videos evaluated: {totals['videos']}",
        "",
        "| Metric | Result | Interpretation |",
        "|---|---:|---|",
        f"| Answer Accuracy | `{fmt_ratio(totals['correct_answers'], totals['judged_answers'])}` | Natural-language answers were correct for {totals['correct_answers']} out of {totals['judged_answers']} judged questions. |",
        f"| Exact Evidence Match | `{fmt_ratio(totals['exact_evidence_matches'], totals['total_questions'])}` | The cited event-ID set exactly matched the expected set for {totals['exact_evidence_matches']} out of {totals['total_questions']} questions. |",
        f"| Citation Precision / Avg P | `{fmt_ratio(totals['correct_cited_ids'], totals['total_cited_ids'])}` | Of all cited event IDs, {pct(totals['correct_cited_ids'], totals['total_cited_ids'])} were expected. {totals['extra_ids_count']} extra incorrect IDs were cited. |",
        f"| Evidence Recall | `{fmt_ratio(totals['correct_cited_ids'], totals['expected_ids_count'])}` | Found {totals['correct_cited_ids']} out of {totals['expected_ids_count']} expected event IDs. {totals['missing_ids_count']} expected IDs were missing. |",
        f"| Citation F1 | `{f1:.3f}` | Combined citation precision and evidence recall. |",
        "",
        "## Videos",
        "",
    ]
    lines.extend(f"- {name}" for name in video_names)
    resolved_output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return resolved_output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    output = compare_total(args.run_dir, args.output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
