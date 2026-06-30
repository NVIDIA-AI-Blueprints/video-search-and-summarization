#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run one deterministic VSS frozen-summary eval batch."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

# =============================================================================
# Section 0 - Constants And Small Utilities
# =============================================================================

DEFAULT_LVS_BACKEND_URL = "http://127.0.0.1:38112"
DEFAULT_VIDEO_URL_TEMPLATE = "{video_name}.mp4"
DEFAULT_VLM_MODEL = "nim_nvidia_cosmos-reason2-8b_hf-1208"
QUESTION_CATEGORIES = ("within_event", "entity_relational", "temporal")


def log(message: str) -> None:
    print(message, flush=True)


def read_question_file(path: Path) -> tuple[str | None, list[dict[str, Any]]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    video_id: str | None = None
    if isinstance(value, dict):
        video_id_value = value.get("video_id")
        if not isinstance(video_id_value, str) or not video_id_value.strip():
            raise ValueError(f"Custom question file must contain a non-empty video_id: {path}")
        video_id = video_id_value.strip()
        value = value.get("questions")
    if not isinstance(value, list):
        raise ValueError(f"Question file must contain a JSON array or a questions array: {path}")
    if not all(isinstance(row, dict) for row in value):
        raise ValueError(f"Every question must be a JSON object: {path}")
    return video_id, value


def read_json_rows(path: Path) -> list[dict[str, Any]]:
    return read_question_file(path)[1]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key.strip().lower().replace(" ", "_"): value for key, value in row.items()}


def extract_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    best: dict[str, Any] | None = None
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
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
# Section 1 - Discover Inputs And Create Run Directory
# =============================================================================


def discover_question_files(questions_dir: Path, question_file: Path | None = None) -> list[Path]:
    if question_file is not None:
        candidate = question_file.expanduser()
        if not candidate.is_absolute() and not candidate.is_file():
            candidate = questions_dir / candidate
        if not candidate.is_file():
            raise FileNotFoundError(f"Question file not found: {candidate}")
        if candidate.suffix != ".json":
            raise ValueError(f"Single-video question file must be JSON: {candidate}")
        return [candidate]

    files = sorted(questions_dir.glob("*_eval.json"))
    if not files:
        raise FileNotFoundError(f"No *_eval.json files found in {questions_dir}")
    return files


def resolve_video_name(question_file: Path, embedded_video_id: str | None = None) -> str:
    if embedded_video_id:
        return embedded_video_id
    if question_file.name.endswith("_eval.json"):
        return question_file.name.removesuffix("_eval.json")
    raise ValueError(
        "Custom --question-file JSON must contain video_id and questions; "
        "legacy array files must end with _eval.json"
    )


def make_run_dir(results_root: Path, run_id: str | None) -> tuple[str, Path]:
    resolved_run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = results_root / "single" / f"run_{resolved_run_id}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return resolved_run_id, run_dir


# =============================================================================
# Section 1.5 - Optional OpenClaw Durable Memory Save
# =============================================================================


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
        "No durable memory is loaded. This file was reset for a VSS eval run.\n",
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


def save_memory(
    video_name: str,
    event_count: int,
    session_key: str,
    model: str,
    timeout: int,
    log_path: Path,
) -> None:
    message = (
        "Save the BWC video summary/events already provided in this conversation as durable OpenClaw memory.\n\n"
        "Requirements:\n"
        "- Preserve the video_id exactly.\n"
        "- Preserve every event_id exactly.\n"
        "- Preserve start_time, end_time, event_type, and description.\n"
        "- Do not summarize away event IDs or timestamps.\n\n"
        "After saving, reply only with valid JSON:\n"
        "{"
        "\"saved\": true,"
        f"\"video_id\": \"{video_name}\","
        "\"memory_path\": \"...\","
        f"\"event_count\": {event_count}"
        "}"
    )
    parsed, _, _ = run_openclaw_json(message, session_key, model, timeout, log_path)
    if parsed.get("saved") is not True:
        raise RuntimeError(f"OpenClaw did not confirm memory save: {parsed}")
    if parsed.get("video_id") != video_name:
        raise RuntimeError(f"OpenClaw saved wrong video_id for {video_name}: {parsed}")
    if int(parsed.get("event_count", -1)) != event_count:
        raise RuntimeError(f"OpenClaw saved wrong event_count for {video_name}: {parsed}")


# =============================================================================
# Section 2 - Query LVS/Frozen Summarization Endpoint
# =============================================================================


def fetch_frozen_summary(
    lvs_backend_url: str,
    video_url: str,
    vlm_model: str,
    output_summary_json: Path,
    output_events_json: Path,
) -> dict[str, Any]:
    payload = {
        "url": video_url,
        "model": vlm_model,
        "scenario": "activity monitoring",
        "events": ["notable activity"],
        "chunk_duration": 10,
        "num_frames_per_second_or_fixed_frames_chunk": 20,
        "use_fps_for_chunking": False,
        "seed": 1,
    }
    response = post_json(f"{lvs_backend_url.rstrip('/')}/v1/summarize", payload, timeout=120)
    content = response["choices"][0]["message"]["content"]
    summary = json.loads(content)
    output_summary_json.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    events_doc = {
        "video_summary": summary.get("video_summary", ""),
        "events": summary.get("events", []),
    }
    output_events_json.write_text(json.dumps(events_doc, indent=2) + "\n", encoding="utf-8")
    return events_doc


# =============================================================================
# Section 3 - Drive One Shared OpenClaw Conversation Per Video
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


def run_openclaw_json(
    message: str,
    session_key: str,
    model: str,
    timeout: int,
    log_path: Path,
    attempts: int = 3,
) -> tuple[dict[str, Any], int, int]:
    total_latency_ms = 0
    total_tool_calls = 0
    for attempt in range(1, attempts + 1):
        output, latency_ms, tool_calls = run_openclaw(message, session_key, model, timeout, log_path)
        total_latency_ms += latency_ms
        total_tool_calls += tool_calls
        try:
            return extract_json_object(output), total_latency_ms, total_tool_calls
        except ValueError:
            if attempt == attempts:
                raise
            log(f"  OpenClaw returned malformed JSON; retrying ({attempt}/{attempts})")
    raise AssertionError("unreachable")


def seed_video_context(events_doc: dict[str, Any], video_name: str, session_key: str, model: str, timeout: int, log_path: Path) -> None:
    message = (
        "We are starting one VSS eval conversation for a single video.\n"
        f"Video name: {video_name}\n"
        "Use only the frozen summary/events JSON below when answering the upcoming eval questions.\n"
        "Do not use outside knowledge. Preserve event IDs exactly.\n\n"
        "Frozen summary/events JSON:\n"
        f"{json.dumps(events_doc, indent=2)}\n\n"
        "Reply with only this JSON: {\"ready\": true}"
    )
    parsed, _, _ = run_openclaw_json(message, session_key, model, timeout, log_path)
    if parsed.get("ready") is not True:
        raise RuntimeError(f"OpenClaw seed response did not confirm readiness: {parsed}")


def answer_question(
    qid: str,
    question: str,
    session_key: str,
    model: str,
    timeout: int,
    log_path: Path,
) -> tuple[str, list[int], str, int, int]:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["answer", "cited_event_ids", "citation_reason"],
        "properties": {
            "answer": {
                "type": "string",
                "description": (
                    "Complete natural-language answer to the question. Must directly answer "
                    "the question using the frozen summary/events. Do not return only event "
                    "IDs, labels, or terse fragments."
                ),
            },
            "cited_event_ids": {
                "type": "array",
                "description": (
                    "Smallest focused set of event IDs from the frozen summary that directly "
                    "supports the answer and matches the question scope. Do not cite every "
                    "event in the incident. Do not include broad background/context events "
                    "unless they are necessary to answer this specific question. For broad "
                    "overview questions, cite representative events for each major phase, not all events."
                    "For negative-control questions, use an empty array "
                    "unless a specific event directly supports the negative conclusion."
                ),
                "items": {"type": "integer"},
                "uniqueItems": True,
            },
            "citation_reason": {
                "type": "string",
                "description": (
                    "Brief explanation of why this is the smallest focused evidence set for "
                    "the answer. If broader context events exist but are not necessary, say "
                    "they were excluded because they are only background/context. For "
                    "negative-control questions with no cited IDs, explain that the answer "
                    "is supported by absence across the frozen event set."
                ),
            },
        },
    }
    message = (
        "Answer this eval question using only the frozen summary/events already provided in this conversation.\n"
        "Your answer must be complete natural language, and your citations must be minimal and focused.\n"
        "Prefer precision over recall for citations: cite the specific events needed to support the answer, not every related event.\n"
        "Return only valid JSON matching this schema:\n"
        f"{json.dumps(schema, indent=2)}\n\n"
        f"QID: {qid}\n"
        f"Question: {question}\n"
    )
    parsed, latency_ms, tool_calls = run_openclaw_json(message, session_key, model, timeout, log_path)
    answer = str(parsed.get("answer", "")).strip()
    cited = parsed.get("cited_event_ids", [])
    if not isinstance(cited, list):
        cited = []
    cited_ids = [int(value) for value in cited if isinstance(value, int) or str(value).isdigit()]
    citation_reason = str(parsed.get("citation_reason", "")).strip()
    return answer, cited_ids, citation_reason, latency_ms, tool_calls


# =============================================================================
# Section 4 - Judge Answers With OpenAI API
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
                    "You are a strict evaluator. Judge whether an answer preserves the expected target meaning. "
                    "Return only JSON: {\"answer_matches_expected\":true|false,\"notes\":\"brief reason\"}."
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
# Section 5 - Score And Write JSON/Markdown Reports
# =============================================================================


def parse_event_ids(value: Any) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(type(event_id) is int for event_id in value):
        raise ValueError("expected_event_ids must be a JSON array of integers")
    return value


def parse_category(value: Any) -> str:
    category = str(value).strip().lower()
    if category not in QUESTION_CATEGORIES:
        allowed = ", ".join(QUESTION_CATEGORIES)
        raise ValueError(f"category must be one of: {allowed}")
    return category


def prf(expected: set[int], predicted: set[int]) -> tuple[float, float, float]:
    if not expected and not predicted:
        return 1.0, 1.0, 1.0
    correct = expected & predicted
    precision = len(correct) / len(predicted) if predicted else 0.0
    recall = len(correct) / len(expected) if expected else 0.0
    f1 = 0.0 if precision + recall == 0 else (2 * precision * recall) / (precision + recall)
    return precision, recall, f1


def summarize_single_raw(raw_rows: list[dict[str, Any]], include_categories: bool = True) -> dict[str, Any]:
    question_count = len(raw_rows)
    correct_answers = sum(int(row["answer_accuracy"]) for row in raw_rows)
    exact_evidence_matches = sum(int(row["exact_evidence_match"]) for row in raw_rows)
    correct_cited_ids = 0
    total_cited_ids = 0
    expected_ids_count = 0
    for row in raw_rows:
        expected = set(row["expected_event_ids"])
        predicted = set(row["predicted_event_ids"])
        correct_cited_ids += len(expected & predicted)
        total_cited_ids += len(predicted)
        expected_ids_count += len(expected)
    precision = correct_cited_ids / total_cited_ids if total_cited_ids else 0.0
    recall = correct_cited_ids / expected_ids_count if expected_ids_count else 0.0
    f1 = 0.0 if precision + recall == 0 else (2 * precision * recall) / (precision + recall)
    category_metrics = (
        {
            category: summarize_single_raw(
                [row for row in raw_rows if row["category"] == category],
                include_categories=False,
            )
            for category in QUESTION_CATEGORIES
            if any(row["category"] == category for row in raw_rows)
        }
        if include_categories
        else {}
    )
    metrics = {
        "question_count": question_count,
        "correct_answers": correct_answers,
        "exact_evidence_matches": exact_evidence_matches,
        "correct_cited_ids": correct_cited_ids,
        "total_cited_ids": total_cited_ids,
        "expected_ids_count": expected_ids_count,
        "answer_accuracy": correct_answers / question_count if question_count else 0.0,
        "exact_evidence_match": exact_evidence_matches / question_count if question_count else 0.0,
        "mean_evidence_precision": precision,
        "mean_evidence_recall": recall,
        "mean_evidence_f1_score": f1,
    }
    if include_categories:
        metrics["categories"] = category_metrics
    return metrics


def fmt_float(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def fmt_ratio(num: int, den: int) -> str:
    if den == 0:
        return "n/a"
    return f"{num}/{den} = {fmt_float(num / den)}"


def write_video_report_md(path: Path, video_id: str, metrics: dict[str, Any], raw_rows: list[dict[str, Any]]) -> None:
    lines = [
        f"# {video_id} Eval Report",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Answer Accuracy | `{fmt_ratio(metrics['correct_answers'], metrics['question_count'])}` |",
        f"| Exact Evidence Match | `{fmt_ratio(metrics['exact_evidence_matches'], metrics['question_count'])}` |",
        f"| Mean Evidence Precision | `{fmt_float(metrics['mean_evidence_precision'])}` |",
        f"| Mean Evidence Recall | `{fmt_float(metrics['mean_evidence_recall'])}` |",
        f"| Mean Evidence F1 Score | `{fmt_float(metrics['mean_evidence_f1_score'])}` |",
        "",
        "## Category Breakdown",
        "",
        "| Category | Questions | Answer Accuracy | Exact Evidence | Precision | Recall | F1 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for category, category_metric in metrics["categories"].items():
        lines.append(
            f"| {category} | {category_metric['question_count']} | "
            f"{fmt_float(category_metric['answer_accuracy'])} | "
            f"{fmt_float(category_metric['exact_evidence_match'])} | "
            f"{fmt_float(category_metric['mean_evidence_precision'])} | "
            f"{fmt_float(category_metric['mean_evidence_recall'])} | "
            f"{fmt_float(category_metric['mean_evidence_f1_score'])} |"
        )
    lines.extend(
        [
            "",
            "## Questions",
            "",
            "| QID | Category | Question | Expected IDs | Predicted IDs | Answer Accuracy | Exact Evidence | Precision | Recall | F1 |",
            "|---:|---|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in raw_rows:
        safe_question = str(row["question"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {row['qid']} | {row['category']} | {safe_question} | {','.join(map(str, row['expected_event_ids']))} | "
            f"{','.join(map(str, row['predicted_event_ids']))} | {row['answer_accuracy']} | "
            f"{row['exact_evidence_match']} | {fmt_float(row['precision'])} | "
            f"{fmt_float(row['recall'])} | {fmt_float(row['f1_score'])} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_video_report_json(run_id: str, video_id: str, metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "eval_type": "single_video_qa",
        "video_id": video_id,
        "summary": {
            "question_count": metrics["question_count"],
            "answer_accuracy": metrics["answer_accuracy"],
            "exact_evidence_match": metrics["exact_evidence_match"],
            "mean_evidence_precision": metrics["mean_evidence_precision"],
            "mean_evidence_recall": metrics["mean_evidence_recall"],
            "mean_evidence_f1_score": metrics["mean_evidence_f1_score"],
            "categories": metrics["categories"],
        },
        "counts": {
            "questions": metrics["question_count"],
            "correct_answers": metrics["correct_answers"],
            "exact_evidence_matches": metrics["exact_evidence_matches"],
        },
        "artifacts": {
            "raw_json": "raw.json",
            "report_md": "report.md",
            "summary": "debug/summary.json",
            "summary_events": "debug/summary_events.json",
            "openclaw_log": "debug/openclaw.log",
        },
    }


def build_total_report(run_id: str, video_reports: list[dict[str, Any]], video_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    videos = len(video_reports)
    questions = sum(item["question_count"] for item in video_metrics)
    correct_answers = sum(item["correct_answers"] for item in video_metrics)
    exact_evidence_matches = sum(item["exact_evidence_matches"] for item in video_metrics)
    correct_cited_ids = sum(item["correct_cited_ids"] for item in video_metrics)
    total_cited_ids = sum(item["total_cited_ids"] for item in video_metrics)
    expected_ids_count = sum(item["expected_ids_count"] for item in video_metrics)
    precision = correct_cited_ids / total_cited_ids if total_cited_ids else 0.0
    recall = correct_cited_ids / expected_ids_count if expected_ids_count else 0.0
    f1 = 0.0 if precision + recall == 0 else (2 * precision * recall) / (precision + recall)
    category_rows = {
        category: [
            row
            for metrics in video_metrics
            for row in metrics.get("raw_rows", [])
            if row["category"] == category
        ]
        for category in QUESTION_CATEGORIES
    }
    category_metrics = {
        category: summarize_single_raw(rows, include_categories=False)
        for category, rows in category_rows.items()
        if rows
    }
    return {
        "run_id": run_id,
        "eval_type": "single_video_qa_total",
        "summary": {
            "video_count": videos,
            "question_count": questions,
            "answer_accuracy": correct_answers / questions if questions else 0.0,
            "exact_evidence_match": exact_evidence_matches / questions if questions else 0.0,
            "mean_evidence_precision": precision,
            "mean_evidence_recall": recall,
            "mean_evidence_f1_score": f1,
            "categories": category_metrics,
        },
        "counts": {
            "videos": videos,
            "questions": questions,
            "correct_answers": correct_answers,
            "exact_evidence_matches": exact_evidence_matches,
        },
        "videos": [
            {
                "video_id": report["video_id"],
                "question_count": report["summary"]["question_count"],
                "answer_accuracy": report["summary"]["answer_accuracy"],
                "exact_evidence_match": report["summary"]["exact_evidence_match"],
                "mean_evidence_precision": report["summary"]["mean_evidence_precision"],
                "mean_evidence_recall": report["summary"]["mean_evidence_recall"],
                "mean_evidence_f1_score": report["summary"]["mean_evidence_f1_score"],
                "report_json": f"{report['video_id']}/report.json",
            }
            for report in video_reports
        ],
        "artifacts": {
            "report_md": "total.md",
            "videos_root": ".",
        },
    }


def write_total_report_md(path: Path, total: dict[str, Any]) -> None:
    summary = total["summary"]
    counts = total["counts"]
    lines = [
        "# Total Eval Report",
        "",
        f"Videos evaluated: {counts['videos']}",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Answer Accuracy | `{fmt_ratio(counts['correct_answers'], counts['questions'])}` |",
        f"| Exact Evidence Match | `{fmt_ratio(counts['exact_evidence_matches'], counts['questions'])}` |",
        f"| Mean Evidence Precision | `{fmt_float(summary['mean_evidence_precision'])}` |",
        f"| Mean Evidence Recall | `{fmt_float(summary['mean_evidence_recall'])}` |",
        f"| Mean Evidence F1 Score | `{fmt_float(summary['mean_evidence_f1_score'])}` |",
        "",
        "## Category Breakdown",
        "",
        "| Category | Questions | Answer Accuracy | Exact Evidence | Precision | Recall | F1 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for category, metrics in summary["categories"].items():
        lines.append(
            f"| {category} | {metrics['question_count']} | {fmt_float(metrics['answer_accuracy'])} | "
            f"{fmt_float(metrics['exact_evidence_match'])} | {fmt_float(metrics['mean_evidence_precision'])} | "
            f"{fmt_float(metrics['mean_evidence_recall'])} | {fmt_float(metrics['mean_evidence_f1_score'])} |"
        )
    lines.extend([
        "",
        "## Videos",
        "",
    ])
    lines.extend(f"- [{video['video_id']}]({video['video_id']}/report.md)" for video in total["videos"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# =============================================================================
# Section 6 - Main Orchestration Loop
# =============================================================================


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-root", type=Path, default=Path.home() / "eval")
    parser.add_argument(
        "--question-file",
        type=Path,
        help=(
            "Run only this *_eval.json file. Relative filenames are resolved from "
            "<eval-root>/questions unless they already exist relative to the current directory."
        ),
    )
    parser.add_argument("--run-id")
    parser.add_argument("--lvs-backend-url", default=os.environ.get("LVS_BACKEND_URL", DEFAULT_LVS_BACKEND_URL))
    parser.add_argument("--video-url-template", default=os.environ.get("VIDEO_URL_TEMPLATE", DEFAULT_VIDEO_URL_TEMPLATE))
    parser.add_argument("--vlm-model", default=os.environ.get("VLM_NAME", DEFAULT_VLM_MODEL))
    parser.add_argument("--openclaw-model", default=os.environ.get("OPENCLAW_MODEL", "openai/gpt-5.5"))
    parser.add_argument("--judge-model", default=os.environ.get("JUDGE_MODEL", "gpt-5.5"))
    parser.add_argument("--openclaw-timeout", type=int, default=300)
    parser.add_argument("--judge-timeout", type=int, default=120)
    parser.add_argument(
        "--save-memory",
        action="store_true",
        help="Reset OpenClaw memory and ask OpenClaw to save each frozen summary/events document as durable memory.",
    )
    args = parser.parse_args()

    questions_dir = args.eval_root / "questions"
    results_root = args.eval_root / "results"
    question_files = discover_question_files(questions_dir, args.question_file)
    run_id, run_dir = make_run_dir(results_root, args.run_id)
    debug_dir = run_dir / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    log(f"Run directory: {run_dir}")
    if args.save_memory:
        log("Resetting OpenClaw durable memory for eval")
        reset_memory(debug_dir / "memory_reset.log")

    video_reports: list[dict[str, Any]] = []
    video_metrics: list[dict[str, Any]] = []
    for question_file in question_files:
        embedded_video_id, question_rows = read_question_file(question_file)
        video_name = resolve_video_name(question_file, embedded_video_id)
        video_url = args.video_url_template.format(video_name=video_name)
        session_key = f"vss-eval-single-{run_id}-{video_name}"
        video_dir = run_dir / video_name
        video_debug_dir = video_dir / "debug"
        video_debug_dir.mkdir(parents=True, exist_ok=True)
        log_path = video_debug_dir / "openclaw.log"
        raw_json = video_dir / "raw.json"
        report_md = video_dir / "report.md"
        report_json = video_dir / "report.json"
        summary_json = video_debug_dir / "summary.json"
        events_json = video_debug_dir / "summary_events.json"

        log(f"Processing {video_name}")
        events_doc = fetch_frozen_summary(
            args.lvs_backend_url,
            video_url,
            args.vlm_model,
            summary_json,
            events_json,
        )
        seed_video_context(events_doc, video_name, session_key, args.openclaw_model, args.openclaw_timeout, log_path)
        if args.save_memory:
            log(f"  {video_name}: saving durable OpenClaw memory")
            save_memory(
                video_name,
                get_event_count(events_doc),
                session_key,
                args.openclaw_model,
                args.openclaw_timeout,
                log_path,
            )

        raw_rows: list[dict[str, Any]] = []
        for row in question_rows:
            norm = normalize_row(row)
            qid = str(norm.get("qid", ""))
            question = str(norm.get("question", ""))
            expected_target = str(norm.get("expected_answer_target", ""))
            expected_event_ids = parse_event_ids(norm.get("expected_event_ids", []))
            category = parse_category(norm.get("category", ""))
            log(f"  Q{qid}: answering")
            answer, cited_ids, citation_reason, latency_ms, tool_calls = answer_question(
                qid,
                question,
                session_key,
                args.openclaw_model,
                args.openclaw_timeout,
                log_path,
            )
            log(f"  Q{qid}: judging")
            answer_match, notes = judge_answer(question, expected_target, answer, args.judge_model, args.judge_timeout)
            precision, recall, f1 = prf(set(expected_event_ids), set(cited_ids))
            raw_rows.append(
                {
                    "qid": qid,
                    "category": category,
                    "question": question,
                    "expected_answer_target": expected_target,
                    "answer": answer,
                    "expected_event_ids": expected_event_ids,
                    "predicted_event_ids": sorted(cited_ids),
                    "answer_accuracy": int(answer_match),
                    "exact_evidence_match": int(set(expected_event_ids) == set(cited_ids)),
                    "precision": precision,
                    "recall": recall,
                    "f1_score": f1,
                    "judge_notes": notes,
                    "latency_ms": latency_ms,
                    "num_tool_calls": tool_calls,
                    "citation_reason": citation_reason,
                }
            )
            write_json(raw_json, raw_rows)

        metrics = summarize_single_raw(raw_rows)
        metrics["raw_rows"] = raw_rows
        video_report = build_video_report_json(run_id, video_name, metrics)
        write_video_report_md(report_md, video_name, metrics, raw_rows)
        write_json(report_json, video_report)
        video_reports.append(video_report)
        video_metrics.append(metrics)

    total_report = build_total_report(run_id, video_reports, video_metrics)
    write_total_report_md(run_dir / "total.md", total_report)
    write_json(run_dir / "total.json", total_report)
    log("")
    log(f"Final output directory: {run_dir}")
    log("Video report files written:")
    for report in video_reports:
        log(str(run_dir / report["video_id"] / "report.md"))
    log(f"Aggregate report: {run_dir / 'total.md'}")
    log(f"Aggregate JSON: {run_dir / 'total.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
