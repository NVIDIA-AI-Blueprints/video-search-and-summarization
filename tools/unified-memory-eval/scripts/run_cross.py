#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run deterministic cross-conversation memory eval scenarios."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


# =============================================================================
# Section 0 - Constants And Small Utilities
# =============================================================================

DEFAULT_SUMMARY_DIR = Path("/home/ubuntu/frozen-summarization-endpoint/data")
DEFAULT_QUESTION_FILE = Path("questions/cross-incidents.tsv")

RAW_FIELDS = [
    "Scenario ID",
    "Turn ID",
    "Family",
    "Question",
    "Expected Answer Target",
    "Expected Video IDs",
    "Expected Event IDs",
    "Answer",
    "Predicted Video IDs",
    "Predicted Event IDs",
    "Confidence",
    "Not Stated",
    "Locator Accuracy",
    "Answer Accuracy",
    "Precision",
    "Recall",
    "F1 Score",
    "Judge Notes",
    "Latency MS",
    "Num Tool Calls",
]


def log(message: str) -> None:
    print(message, flush=True)


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def normalize_row(row: dict[str, str]) -> dict[str, str]:
    return {key.strip().lower().replace(" ", "_"): value.strip() for key, value in row.items()}


def extract_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    best: dict[str, Any] | None = None
    preferred_keys = {
        "answer",
        "video_ids",
        "saved",
        "ready",
        "answer_matches_expected",
    }
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            if preferred_keys & set(value):
                return value
            best = value
    if best is None:
        raise ValueError(f"No JSON object found in output:\n{text}")
    return best


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None, timeout: int = 120) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"POST {url} failed with HTTP {exc.code}: {detail}") from exc


def make_run_dir(results_root: Path, run_id: str | None) -> tuple[str, Path]:
    resolved_run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = results_root / "cross" / f"run_{resolved_run_id}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return resolved_run_id, run_dir


def openclaw_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{Path.home() / '.local/npm-global/bin'}:{env.get('PATH', '')}"
    return env


def run_shell(command: list[str], log_path: Path, timeout: int) -> str:
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        env=openclaw_env(),
        timeout=timeout,
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("\n\n===== SHELL CALL =====\n")
        handle.write(" ".join(command))
        handle.write("\n\n----- STDOUT -----\n")
        handle.write(result.stdout)
        handle.write("\n----- STDERR -----\n")
        handle.write(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit {result.returncode}: {' '.join(command)}; see {log_path}")
    return result.stdout


# =============================================================================
# Section 1 - Parse Cross-Evidence Targets
# =============================================================================


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_expected_video_ids(value: str | None) -> list[str]:
    return split_csv(value)


def parse_id_list(value: str) -> list[int]:
    return [int(match.group(0)) for match in re.finditer(r"\d+", value)]


def parse_expected_evidence(value: str | None, expected_video_ids: list[str]) -> set[tuple[str, int]]:
    """Parse either bare IDs or grouped video:id syntax from TSV expected_event_ids."""
    if not value:
        return set()
    text = value.strip()
    evidence: set[tuple[str, int]] = set()
    if ":" in text:
        for group in text.split(";"):
            if ":" not in group:
                continue
            video_id, ids = group.split(":", 1)
            video_id = video_id.strip()
            for event_id in parse_id_list(ids):
                evidence.add((video_id, event_id))
        return evidence
    if not expected_video_ids:
        return set()
    if len(expected_video_ids) == 1:
        return {(expected_video_ids[0], event_id) for event_id in parse_id_list(text)}
    # Ambiguous ungrouped multi-video evidence should not happen, but keep it scoreable.
    return {(video_id, event_id) for video_id in expected_video_ids for event_id in parse_id_list(text)}


def parse_predicted_video_ids(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return split_csv(value)
    return []


def parse_predicted_evidence(event_ids: Any, video_ids: list[str]) -> set[tuple[str, int]]:
    """Parse model event_ids from either [1,2] or {video: [1,2]} answer contract."""
    evidence: set[tuple[str, int]] = set()
    if isinstance(event_ids, dict):
        for video_id, ids in event_ids.items():
            if isinstance(ids, list):
                for value in ids:
                    try:
                        evidence.add((str(video_id), int(value)))
                    except (TypeError, ValueError):
                        continue
            else:
                for event_id in parse_id_list(str(ids)):
                    evidence.add((str(video_id), event_id))
        return evidence
    if isinstance(event_ids, list):
        if len(video_ids) != 1:
            return evidence
        video_id = video_ids[0]
        for value in event_ids:
            try:
                evidence.add((video_id, int(value)))
            except (TypeError, ValueError):
                continue
    elif isinstance(event_ids, str) and len(video_ids) == 1:
        evidence = {(video_ids[0], event_id) for event_id in parse_id_list(event_ids)}
    return evidence


def format_video_ids(video_ids: list[str]) -> str:
    return ",".join(video_ids)


def format_evidence(evidence: set[tuple[str, int]]) -> str:
    grouped: dict[str, list[int]] = defaultdict(list)
    for video_id, event_id in sorted(evidence):
        grouped[video_id].append(event_id)
    return ";".join(f"{video_id}:{','.join(map(str, ids))}" for video_id, ids in grouped.items())


def prf(expected: set[tuple[str, int]], predicted: set[tuple[str, int]]) -> tuple[float, float, float]:
    if not expected and not predicted:
        return 1.0, 1.0, 1.0
    correct = expected & predicted
    precision = len(correct) / len(predicted) if predicted else 0.0
    recall = len(correct) / len(expected) if expected else 0.0
    f1 = 0.0 if precision + recall == 0 else (2 * precision * recall) / (precision + recall)
    return precision, recall, f1


# =============================================================================
# Section 2 - Load Summaries And Validate Questions
# =============================================================================


def load_summaries(summary_dir: Path) -> dict[str, Any]:
    files = sorted(summary_dir.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"No JSON summaries found in {summary_dir}")
    summaries: dict[str, Any] = {}
    for path in files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        video_id = str(doc.get("video") or path.stem)
        summaries[video_id] = doc
    return summaries


def group_scenarios(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for raw in rows:
        row = normalize_row(raw)
        grouped[row["scenario_id"]].append(row)
    for scenario_id, scenario_rows in grouped.items():
        scenario_rows.sort(key=lambda item: int(item["turn_id"]))
        turn_ids = [int(row["turn_id"]) for row in scenario_rows]
        if turn_ids != list(range(1, 8)):
            raise ValueError(f"{scenario_id} must have turn IDs 1..7, got {turn_ids}")
    return dict(sorted(grouped.items()))


def validate_questions(rows: list[dict[str, str]], summaries: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    video_ids = set(summaries)
    for raw in rows:
        row = normalize_row(raw)
        location = f"{row.get('scenario_id')} turn {row.get('turn_id')}"
        expected_videos = parse_expected_video_ids(row.get("expected_video_ids"))
        for video_id in expected_videos:
            if video_id not in video_ids:
                warnings.append(f"{location}: expected video {video_id!r} has no summary JSON")
        expected_evidence = parse_expected_evidence(row.get("expected_event_ids"), expected_videos)
        for video_id, event_id in expected_evidence:
            events = summaries.get(video_id, {}).get("prediction", [])
            event_ids = {int(event.get("id")) for event in events if str(event.get("id", "")).isdigit()}
            if video_id not in video_ids:
                continue
            if event_id not in event_ids:
                warnings.append(f"{location}: expected event {video_id}:{event_id} not found in summary")
    return warnings


# =============================================================================
# Section 3 - OpenClaw Calls
# =============================================================================


def run_openclaw(message: str, session_key: str, model: str, timeout: int, log_path: Path) -> tuple[str, int, int]:
    command = [
        "openclaw",
        "agent",
        "--local",
        "--session-key",
        session_key,
        "--model",
        model,
        "--timeout",
        str(timeout),
        "--message",
        message,
    ]
    start = time.perf_counter()
    result = subprocess.run(command, text=True, capture_output=True, env=openclaw_env(), timeout=timeout + 60)
    latency_ms = int((time.perf_counter() - start) * 1000)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("\n\n===== OPENCLAW CALL =====\n")
        handle.write(message)
        handle.write("\n\n----- STDOUT -----\n")
        handle.write(result.stdout)
        handle.write("\n----- STDERR -----\n")
        handle.write(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"OpenClaw failed with exit {result.returncode}; see {log_path}")
    return result.stdout, latency_ms, 1


def assert_safe_memory_path(path: Path) -> None:
    resolved = path.resolve()
    expected_root = (Path.home() / ".openclaw" / "workspace").resolve()
    if resolved != expected_root and expected_root not in resolved.parents:
        raise RuntimeError(f"Refusing to reset unexpected memory path: {resolved}")


def reset_memory(log_path: Path) -> None:
    workspace = Path.home() / ".openclaw" / "workspace"
    memory_dir = workspace / "memory"
    memory_md = workspace / "MEMORY.md"

    assert_safe_memory_path(memory_dir)
    assert_safe_memory_path(memory_md)

    memory_md.write_text(
        "# MEMORY\n\n"
        "No durable memory is loaded. This file was reset for a VSS cross-memory eval run.\n",
        encoding="utf-8",
    )
    if memory_dir.exists():
        shutil.rmtree(memory_dir)
    memory_dir.mkdir(parents=True, exist_ok=True)

    run_shell(["openclaw", "memory", "index", "--force"], log_path=log_path, timeout=300)


def get_event_count(events_doc: dict[str, Any]) -> int:
    if isinstance(events_doc.get("events"), list):
        return len(events_doc["events"])
    if isinstance(events_doc.get("prediction"), list):
        return len(events_doc["prediction"])
    return 0


def normalize_summary_for_memory(summary_doc: dict[str, Any]) -> dict[str, Any]:
    """Preserve source shape while also adding an events alias for existing prompt wording."""
    if "events" in summary_doc or "prediction" not in summary_doc:
        return summary_doc
    normalized = dict(summary_doc)
    normalized["events"] = summary_doc["prediction"]
    return normalized


def save_memory(
    events_doc: dict[str, Any],
    video_id: str,
    session_key: str,
    model: str,
    timeout: int,
    log_path: Path,
) -> None:
    event_count = get_event_count(events_doc)
    message = (
        "Save the following BWC video summary/events as durable OpenClaw memory.\n"
        "\n"
        "Requirements:\n"
        "- Preserve the video_id exactly.\n"
        "- Preserve every event_id exactly.\n"
        "- Preserve start_time, end_time, event_type, and description.\n"
        "- Do not summarize away event IDs or timestamps.\n\n"
        "After saving, reply only with valid JSON:\n"
        "{"
        "\"saved\": true,"
        f"\"video_id\": \"{video_id}\","
        "\"memory_path\": \"...\","
        f"\"event_count\": {event_count}"
        "}\n\n"
        f"video_id: {video_id}\n"
        "summary/events JSON:\n"
        f"{json.dumps(events_doc, indent=2)}"
    )
    output, _, _ = run_openclaw(message, session_key, model, timeout, log_path)
    parsed = extract_json_object(output)
    if parsed.get("saved") is not True:
        raise RuntimeError(f"OpenClaw did not confirm memory save: {parsed}")
    if parsed.get("video_id") != video_id:
        raise RuntimeError(f"OpenClaw saved wrong video_id for {video_id}: {parsed}")
    if int(parsed.get("event_count", -1)) != event_count:
        raise RuntimeError(f"OpenClaw saved wrong event_count for {video_id}: {parsed}")


def seed_scenario_context(
    summaries: dict[str, Any],
    session_key: str,
    model: str,
    timeout: int,
    log_path: Path,
) -> None:
    """Optional control mode: place all summaries directly in scenario context."""
    message = (
        "We are starting a cross-video BWC eval scenario.\n"
        "Use only the summary/event memories below when answering the upcoming scenario questions.\n"
        "Preserve video IDs and event IDs exactly.\n\n"
        f"{json.dumps(summaries, indent=2)}\n\n"
        "Reply with only this JSON: {\"ready\": true}"
    )
    output, _, _ = run_openclaw(message, session_key, model, timeout, log_path)
    parsed = extract_json_object(output)
    if parsed.get("ready") is not True:
        raise RuntimeError(f"OpenClaw scenario seed response did not confirm readiness: {parsed}")


def answer_cross_question(
    row: dict[str, str],
    session_key: str,
    model: str,
    timeout: int,
    log_path: Path,
) -> tuple[dict[str, Any], int, int]:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["answer", "video_ids", "event_ids", "confidence", "not_stated"],
        "properties": {
            "answer": {
                "type": "string",
                "description": "Complete natural-language answer using only remembered BWC summary/event memories.",
            },
            "video_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Video IDs used to answer. Include all videos used for comparison turns.",
                "uniqueItems": True,
            },
            "event_ids": {
                "description": (
                    "Focused supporting event IDs. For normal one-video turns, use an array of integers. "
                    "For comparison turns, use an object mapping each video_id to an array of integer event IDs. "
                    "Use the smallest directly supporting evidence set."
                )
            },
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "not_stated": {
                "type": "boolean",
                "description": "true only if the answer is that the requested fact is not stated in the memories.",
            },
        },
    }
    message = (
        "Answer this cross-conversation BWC memory eval turn.\n"
        "Use only remembered BWC summary/event memories. Do not use outside knowledge.\n"
        "Maintain scenario context across follow-up turns such as 'that incident' or 'same incident'.\n"
        "Every answer must be grounded with focused video_ids and event_ids.\n"
        "Do not cite every related event; cite only directly necessary support.\n"
        "Return only valid JSON matching this schema:\n"
        f"{json.dumps(schema, indent=2)}\n\n"
        f"Scenario ID: {row['scenario_id']}\n"
        f"Turn ID: {row['turn_id']}\n"
        f"Family: {row['family']}\n"
        f"Question: {row['question']}\n"
    )
    output, latency_ms, tool_calls = run_openclaw(message, session_key, model, timeout, log_path)
    parsed = extract_json_object(output)
    return parsed, latency_ms, tool_calls


# =============================================================================
# Section 4 - Judge Natural-Language Answers
# =============================================================================


def judge_answer(question: str, expected_target: str, answer: str, judge_model: str, timeout: int) -> tuple[bool, str]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for the judging pass")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    payload = {
        "model": judge_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a strict evaluator for BWC memory evals. Judge whether the candidate answer "
                    "preserves the expected answer target meaning. Ignore small wording differences, but "
                    "mark false if the answer selects the wrong incident, omits the core fact, or contradicts "
                    "the expected target. Do not penalize missing event IDs or citations in the natural-language "
                    "answer, because evidence IDs are scored separately by precision/recall/F1. Return only JSON: "
                    "{\"answer_matches_expected\":true|false,\"notes\":\"brief reason\"}."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Question: {question}\n"
                    f"Expected answer target: {expected_target}\n"
                    f"Candidate answer: {answer}\n"
                ),
            },
        ],
    }
    response = post_json(
        f"{base_url}/chat/completions",
        payload,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )
    content = response["choices"][0]["message"]["content"]
    parsed = extract_json_object(content)
    return bool(parsed.get("answer_matches_expected")), str(parsed.get("notes", "")).strip()


# =============================================================================
# Section 5 - Reports
# =============================================================================


def fmt_float(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    locator_rows = [row for row in rows if row["Turn ID"] == "1"]
    answer_rows = [row for row in rows if row["Turn ID"] != "1" and row["Answer Accuracy"] != ""]
    return {
        "total_turns": len(rows),
        "locator_turns": len(locator_rows),
        "locator_correct": sum(int(row["Locator Accuracy"]) for row in locator_rows if row["Locator Accuracy"] != ""),
        "judged_answers": len(answer_rows),
        "correct_answers": sum(int(row["Answer Accuracy"]) for row in answer_rows),
        "mean_precision": sum(float(row["Precision"]) for row in rows) / len(rows) if rows else 0.0,
        "mean_recall": sum(float(row["Recall"]) for row in rows) / len(rows) if rows else 0.0,
        "mean_f1_score": sum(float(row["F1 Score"]) for row in rows) / len(rows) if rows else 0.0,
        "mean_latency_ms": int(sum(int(row["Latency MS"]) for row in rows) / len(rows)) if rows else 0,
        "mean_num_tool_calls": sum(int(row["Num Tool Calls"]) for row in rows) / len(rows) if rows else 0.0,
    }


def write_report(path: Path, metrics: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Cross-Conversation Memory Eval Report",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Locator Accuracy | `{metrics['locator_correct']}/{metrics['locator_turns']} = {fmt_float(metrics['locator_correct'] / metrics['locator_turns'] if metrics['locator_turns'] else 0.0)}` |",
        f"| Answer Accuracy | `{metrics['correct_answers']}/{metrics['judged_answers']} = {fmt_float(metrics['correct_answers'] / metrics['judged_answers'] if metrics['judged_answers'] else 0.0)}` |",
        f"| Mean Evidence Precision | `{fmt_float(metrics['mean_precision'])}` |",
        f"| Mean Evidence Recall | `{fmt_float(metrics['mean_recall'])}` |",
        f"| Mean Evidence F1 Score | `{fmt_float(metrics['mean_f1_score'])}` |",
        f"| Mean Latency MS | `{metrics['mean_latency_ms']}` |",
        f"| Mean Tool Calls | `{fmt_float(metrics['mean_num_tool_calls'])}` |",
        "",
        "| Scenario | Turn | Family | Expected Videos | Predicted Videos | Expected Evidence | Predicted Evidence | Locator Accuracy | Answer Accuracy | Precision | Recall | F1 |",
        "|---|---:|---|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        safe = {key: str(value).replace("|", "\\|").replace("\n", " ") for key, value in row.items()}
        lines.append(
            "| {Scenario ID} | {Turn ID} | {Family} | {Expected Video IDs} | {Predicted Video IDs} | {Expected Event IDs} | {Predicted Event IDs} | {Locator Accuracy} | {Answer Accuracy} | {Precision} | {Recall} | {F1 Score} |".format(
                **safe
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# =============================================================================
# Section 6 - Main Orchestration
# =============================================================================


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-root", type=Path, default=Path.home() / "eval")
    parser.add_argument("--question-file", type=Path, default=None)
    parser.add_argument("--summary-dir", type=Path, default=DEFAULT_SUMMARY_DIR)
    parser.add_argument("--run-id")
    parser.add_argument("--openclaw-model", default=os.environ.get("OPENCLAW_MODEL", "openai/gpt-5.5"))
    parser.add_argument("--judge-model", default=os.environ.get("JUDGE_MODEL", "gpt-5.5"))
    parser.add_argument("--openclaw-timeout", type=int, default=300)
    parser.add_argument("--judge-timeout", type=int, default=120)
    parser.add_argument("--skip-judge", action="store_true", help="Skip LLM answer-accuracy judging.")
    parser.add_argument("--skip-ingest", action="store_true", help="Do not save memories now; assume OpenClaw memory already contains the summaries.")
    parser.add_argument("--reset-memory", action="store_true", help="Reset OpenClaw durable memory before saving summaries.")
    parser.add_argument(
        "--seed-each-scenario",
        action="store_true",
        help="Control mode: insert all summaries into each scenario context instead of relying only on memory retrieval.",
    )
    args = parser.parse_args()

    question_file = args.question_file or args.eval_root / DEFAULT_QUESTION_FILE
    results_root = args.eval_root / "results"
    run_id, run_dir = make_run_dir(results_root, args.run_id)
    log(f"Run directory: {run_dir}")

    summaries = load_summaries(args.summary_dir)
    log(f"Loaded {len(summaries)} summaries from {args.summary_dir}")

    question_rows = read_tsv(question_file)
    grouped = group_scenarios(question_rows)
    warnings = validate_questions(question_rows, summaries)
    validation_path = run_dir / "validation_warnings.txt"
    validation_path.write_text("\n".join(warnings) + ("\n" if warnings else ""), encoding="utf-8")
    if warnings:
        log("Validation warnings:")
        for warning in warnings:
            log(f"  - {warning}")
    else:
        log("Question validation passed with no missing expected videos/events.")

    save_log = run_dir / "memory_save_openclaw.log"
    if args.reset_memory:
        log("Resetting OpenClaw durable memory for cross eval")
        reset_memory(run_dir / "memory_reset.log")
    if not args.skip_ingest and not args.seed_each_scenario:
        log("Saving summaries into OpenClaw durable memory")
        for video_id, summary_doc in summaries.items():
            log(f"  {video_id}: saving durable OpenClaw memory")
            save_memory(
                normalize_summary_for_memory(summary_doc),
                video_id,
                f"vss-eval-cross-save-{run_id}-{video_id}",
                args.openclaw_model,
                args.openclaw_timeout,
                save_log,
            )

    raw_rows: list[dict[str, Any]] = []
    raw_tsv = run_dir / "cross_raw.tsv"

    for scenario_id, scenario_rows in grouped.items():
        log(f"Processing {scenario_id}")
        session_key = f"vss-eval-cross-{run_id}-{scenario_id}"
        log_path = run_dir / f"{scenario_id}_openclaw.log"
        if args.seed_each_scenario:
            seed_scenario_context(summaries, session_key, args.openclaw_model, args.openclaw_timeout, log_path)

        for row in scenario_rows:
            expected_videos = parse_expected_video_ids(row.get("expected_video_ids"))
            expected_evidence = parse_expected_evidence(row.get("expected_event_ids"), expected_videos)
            log(f"  {scenario_id} T{row['turn_id']} {row['family']}: answering")
            parsed, latency_ms, tool_calls = answer_cross_question(
                row,
                session_key,
                args.openclaw_model,
                args.openclaw_timeout,
                log_path,
            )
            answer = str(parsed.get("answer", "")).strip()
            predicted_videos = parse_predicted_video_ids(parsed.get("video_ids"))
            predicted_evidence = parse_predicted_evidence(parsed.get("event_ids"), predicted_videos)
            precision, recall, f1 = prf(expected_evidence, predicted_evidence)

            locator_accuracy = ""
            if row["turn_id"] == "1":
                locator_accuracy = "1" if set(expected_videos).issubset(set(predicted_videos)) else "0"

            answer_accuracy = ""
            notes = ""
            if row["turn_id"] != "1" and not args.skip_judge:
                log(f"  {scenario_id} T{row['turn_id']}: judging")
                answer_match, notes = judge_answer(
                    row.get("question", ""),
                    row.get("expected_answer_target", ""),
                    answer,
                    args.judge_model,
                    args.judge_timeout,
                )
                answer_accuracy = "1" if answer_match else "0"
            elif row["turn_id"] != "1" and args.skip_judge:
                notes = "judge skipped"

            raw_rows.append(
                {
                    "Scenario ID": scenario_id,
                    "Turn ID": row["turn_id"],
                    "Family": row.get("family", ""),
                    "Question": row.get("question", ""),
                    "Expected Answer Target": row.get("expected_answer_target", ""),
                    "Expected Video IDs": ",".join(expected_videos),
                    "Expected Event IDs": format_evidence(expected_evidence),
                    "Answer": answer,
                    "Predicted Video IDs": format_video_ids(predicted_videos),
                    "Predicted Event IDs": format_evidence(predicted_evidence),
                    "Confidence": str(parsed.get("confidence", "")).strip(),
                    "Not Stated": str(bool(parsed.get("not_stated", False))).lower(),
                    "Locator Accuracy": locator_accuracy,
                    "Answer Accuracy": answer_accuracy,
                    "Precision": fmt_float(precision),
                    "Recall": fmt_float(recall),
                    "F1 Score": fmt_float(f1),
                    "Judge Notes": notes,
                    "Latency MS": str(latency_ms),
                    "Num Tool Calls": str(tool_calls),
                }
            )
            write_tsv(raw_tsv, raw_rows, RAW_FIELDS)

    metrics = summarize_rows(raw_rows)
    metrics_path = run_dir / "cross_metrics.json"
    report_path = run_dir / "cross_report.md"
    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    write_report(report_path, metrics, raw_rows)

    log("")
    log(f"Final output directory: {run_dir}")
    log(f"Raw results: {raw_tsv}")
    log(f"Metrics: {metrics_path}")
    log(f"Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
