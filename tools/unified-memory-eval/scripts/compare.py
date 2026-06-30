#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Score one VSS JSON answer set against its ground-truth JSON questions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def parse_ids(value: Any) -> set[int]:
    if value is None:
        return set()
    if not isinstance(value, list) or not all(type(event_id) is int for event_id in value):
        raise ValueError("Event IDs must be a JSON array of integers")
    return set(value)


def norm_value(row: dict[str, Any], *names: str) -> Any:
    lowered = {key.strip().lower().replace(" ", "_"): value for key, value in row.items()}
    for name in names:
        key = name.strip().lower().replace(" ", "_")
        if key in lowered:
            return lowered[key]
    return None


def norm_text(row: dict[str, Any], *names: str) -> str:
    value = norm_value(row, *names)
    return "" if value is None else str(value).strip()


def read_json_rows(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"Input file must contain a JSON array: {path}")
    if not all(isinstance(row, dict) for row in value):
        raise ValueError(f"Every input row must be a JSON object: {path}")
    return value


def boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    lowered = str(value).strip().lower()
    if lowered in {"true", "yes", "1", "y"}:
        return True
    if lowered in {"false", "no", "0", "n"}:
        return False
    return None


def fmt_ratio(num: int, den: int) -> str:
    if den == 0:
        return "n/a"
    value = num / den
    return f"{num}/{den} = {value:.3f}".rstrip("0").rstrip(".")


def pct(num: int, den: int) -> str:
    if den == 0:
        return "n/a"
    return f"{(num / den) * 100:.1f}%"


def make_report(metrics: dict, rows: list[dict], output: Path) -> None:
    lines = [
        f"# {metrics['video_name']} Eval Report",
        "",
        "| Metric | Result | Interpretation |",
        "|---|---:|---|",
        f"| Answer Accuracy | `{metrics['answer_accuracy_text']}` | Natural-language answers were correct for {metrics['correct_answers']} out of {metrics['total_questions']} questions. |",
        f"| Exact Evidence Match | `{metrics['exact_evidence_text']}` | The cited event-ID set exactly matched the expected set for {metrics['exact_evidence_matches']} out of {metrics['total_questions']} questions. |",
        f"| Citation Precision / Avg P | `{metrics['citation_precision_text']}` | Of all cited event IDs, {metrics['citation_precision_pct']} were expected. {metrics['extra_ids_count']} extra incorrect IDs were cited. |",
        f"| Evidence Recall | `{metrics['evidence_recall_text']}` | Found {metrics['correct_cited_ids']} out of {metrics['expected_ids_count']} expected event IDs. {metrics['missing_ids_count']} expected IDs were missing. |",
        f"| Citation F1 | `{metrics['citation_f1_text']}` | Combined citation precision and evidence recall. |",
        "",
        "| QID | Question | Expected Answer Target | Answer | Expected IDs | Cited IDs | Answer Match | Evidence Exact Match | Notes |",
        "|---:|---|---|---|---|---|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| {qid} | {question} | {target} | {answer} | {expected} | {cited} | {answer_match} | {evidence_match} | {notes} |".format(
                **{key: str(value).replace("|", "\\|").replace("\n", " ") for key, value in row.items()}
            )
        )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def compare_one(
    questions: Path,
    answers: Path,
    video_name: str,
    report: Path,
    metrics_json: Path,
) -> dict:
    question_rows = read_json_rows(questions)
    answer_rows = read_json_rows(answers)
    answers_by_qid = {norm_text(row, "qid"): row for row in answer_rows}

    scored_rows: list[dict] = []
    total_questions = len(question_rows)
    correct_answers = 0
    judged_answers = 0
    exact_evidence_matches = 0
    correct_cited_ids = 0
    total_cited_ids = 0
    expected_ids_count = 0
    extra_ids_count = 0
    missing_ids_count = 0

    for qrow in question_rows:
        qid = norm_text(qrow, "qid")
        question = norm_text(qrow, "question")
        target = norm_text(qrow, "expected_answer_target")
        expected_ids = parse_ids(norm_value(qrow, "expected_event_ids"))
        arow = answers_by_qid.get(qid, {})
        answer = norm_text(arow, "answer")
        cited_ids = parse_ids(norm_value(arow, "cited_ids", "cited_event_ids", "predicted_event_ids"))
        answer_match = boolish(norm_value(arow, "answer_match", "answer_matches_expected", "answer_accuracy"))
        notes = norm_text(arow, "notes", "answer_match_notes", "judge_notes")

        intersection = expected_ids & cited_ids
        extras = cited_ids - expected_ids
        missing = expected_ids - cited_ids
        evidence_exact = expected_ids == cited_ids

        if answer_match is not None:
            judged_answers += 1
            correct_answers += int(answer_match)
        exact_evidence_matches += int(evidence_exact)
        correct_cited_ids += len(intersection)
        total_cited_ids += len(cited_ids)
        expected_ids_count += len(expected_ids)
        extra_ids_count += len(extras)
        missing_ids_count += len(missing)

        scored_rows.append(
            {
                "qid": qid,
                "question": question,
                "target": target,
                "answer": answer,
                "expected": ",".join(map(str, sorted(expected_ids))),
                "cited": ",".join(map(str, sorted(cited_ids))),
                "answer_match": "n/a" if answer_match is None else str(answer_match).lower(),
                "evidence_match": str(evidence_exact).lower(),
                "notes": notes,
            }
        )

    precision = correct_cited_ids / total_cited_ids if total_cited_ids else 0.0
    recall = correct_cited_ids / expected_ids_count if expected_ids_count else 0.0
    f1 = 0.0 if precision + recall == 0 else (2 * precision * recall) / (precision + recall)

    metrics = {
        "video_name": video_name,
        "total_questions": total_questions,
        "judged_answers": judged_answers,
        "correct_answers": correct_answers,
        "exact_evidence_matches": exact_evidence_matches,
        "correct_cited_ids": correct_cited_ids,
        "total_cited_ids": total_cited_ids,
        "expected_ids_count": expected_ids_count,
        "extra_ids_count": extra_ids_count,
        "missing_ids_count": missing_ids_count,
        "citation_precision": precision,
        "evidence_recall": recall,
        "citation_f1": f1,
        "answer_accuracy_text": fmt_ratio(correct_answers, judged_answers),
        "exact_evidence_text": fmt_ratio(exact_evidence_matches, total_questions),
        "citation_precision_text": fmt_ratio(correct_cited_ids, total_cited_ids),
        "citation_precision_pct": pct(correct_cited_ids, total_cited_ids),
        "evidence_recall_text": fmt_ratio(correct_cited_ids, expected_ids_count),
        "citation_f1_text": f"{f1:.3f}".rstrip("0").rstrip("."),
    }

    report.parent.mkdir(parents=True, exist_ok=True)
    metrics_json.parent.mkdir(parents=True, exist_ok=True)
    make_report(metrics, scored_rows, report)
    metrics_json.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", required=True, type=Path)
    parser.add_argument("--answers", required=True, type=Path)
    parser.add_argument("--video-name", required=True)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--metrics-json", required=True, type=Path)
    args = parser.parse_args()

    metrics = compare_one(
        questions=args.questions,
        answers=args.answers,
        video_name=args.video_name,
        report=args.report,
        metrics_json=args.metrics_json,
    )
    print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
