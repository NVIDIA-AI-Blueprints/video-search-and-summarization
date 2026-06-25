#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run one deterministic VSS frozen-summary eval batch."""

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
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from compare import compare_one
from compare_total import compare_total


# =============================================================================
# Section 0 - Constants And Small Utilities
# =============================================================================

DEFAULT_LVS_BACKEND_URL = "http://127.0.0.1:38112"
DEFAULT_VIDEO_URL_TEMPLATE = "http://172.17.0.1:39080/{video_name}.mp4"
DEFAULT_VLM_MODEL = "nim_nvidia_cosmos-reason2-8b_hf-1208"
RAW_FIELDS = [
    "QID",
    "Question",
    "Answer",
    "Cited IDs",
    "Citation Reason",
    "Answer Match",
    "Notes",
]


def log(message: str) -> None:
    print(message, flush=True)


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_raw_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RAW_FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def normalize_row(row: dict[str, str]) -> dict[str, str]:
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


def discover_question_files(questions_dir: Path) -> list[Path]:
    files = sorted(questions_dir.glob("*_eval.tsv"))
    if not files:
        raise FileNotFoundError(f"No *_eval.tsv files found in {questions_dir}")
    return files


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
    output = run_openclaw(message, session_key, model, timeout, log_path)
    parsed = extract_json_object(output)
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


def run_openclaw(message: str, session_key: str, model: str, timeout: int, log_path: Path) -> str:
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
    result = subprocess.run(command, text=True, capture_output=True, env=openclaw_env(), timeout=timeout + 60)
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
    return result.stdout


def seed_video_context(events_doc: dict[str, Any], video_name: str, session_key: str, model: str, timeout: int, log_path: Path) -> None:
    message = (
        "We are starting one VSS eval conversation for a single video.\n"
        f"Video name: {video_name}\n"
        "Use only the frozen summary/events JSON below when answering the upcoming TSV questions.\n"
        "Do not use outside knowledge. Preserve event IDs exactly.\n\n"
        "Frozen summary/events JSON:\n"
        f"{json.dumps(events_doc, indent=2)}\n\n"
        "Reply with only this JSON: {\"ready\": true}"
    )
    output = run_openclaw(message, session_key, model, timeout, log_path)
    parsed = extract_json_object(output)
    if parsed.get("ready") is not True:
        raise RuntimeError(f"OpenClaw seed response did not confirm readiness: {parsed}")


def answer_question(
    qid: str,
    question: str,
    session_key: str,
    model: str,
    timeout: int,
    log_path: Path,
) -> tuple[str, list[int], str]:
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
    output = run_openclaw(message, session_key, model, timeout, log_path)
    parsed = extract_json_object(output)
    answer = str(parsed.get("answer", "")).strip()
    cited = parsed.get("cited_event_ids", [])
    if not isinstance(cited, list):
        cited = []
    cited_ids = [int(value) for value in cited if isinstance(value, int) or str(value).isdigit()]
    citation_reason = str(parsed.get("citation_reason", "")).strip()
    return answer, cited_ids, citation_reason


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
# Section 5 - Write Raw TSV, Run compare.py, And Run compare_total.py
# =============================================================================


def run_compare(script_dir: Path, question_file: Path, raw_tsv: Path, video_name: str, report: Path, metrics_json: Path) -> dict:
    return compare_one(
        questions=question_file,
        answers=raw_tsv,
        video_name=video_name,
        report=report,
        metrics_json=metrics_json,
    )


def run_compare_total(script_dir: Path, run_dir: Path) -> None:
    compare_total(run_dir=run_dir, output=run_dir / "total.md")


# =============================================================================
# Section 6 - Main Orchestration Loop
# =============================================================================


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-root", type=Path, default=Path.home() / "eval")
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
    scripts_dir = args.eval_root / "scripts"
    results_root = args.eval_root / "results"
    question_files = discover_question_files(questions_dir)
    run_id, run_dir = make_run_dir(results_root, args.run_id)
    log(f"Run directory: {run_dir}")
    if args.save_memory:
        log("Resetting OpenClaw durable memory for eval")
        reset_memory(run_dir / "memory_reset.log")

    reports: list[Path] = []
    for question_file in question_files:
        video_name = question_file.name.removesuffix("_eval.tsv")
        video_url = args.video_url_template.format(video_name=video_name)
        session_key = f"vss-eval-single-{run_id}-{video_name}"
        log_path = run_dir / f"{video_name}_openclaw.log"
        raw_tsv = run_dir / f"{video_name}_raw.tsv"
        report = run_dir / f"{video_name}_report.md"
        metrics_json = run_dir / f"{video_name}_metrics.json"
        summary_json = run_dir / f"{video_name}_summary.json"
        events_json = run_dir / f"{video_name}_frozen_summary_events.json"

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

        raw_rows: list[dict[str, str]] = []
        for row in read_tsv(question_file):
            norm = normalize_row(row)
            qid = norm.get("qid", "")
            question = norm.get("question", "")
            expected_target = norm.get("expected_answer_target", "")
            log(f"  Q{qid}: answering")
            answer, cited_ids, citation_reason = answer_question(
                qid,
                question,
                session_key,
                args.openclaw_model,
                args.openclaw_timeout,
                log_path,
            )
            log(f"  Q{qid}: judging")
            answer_match, notes = judge_answer(question, expected_target, answer, args.judge_model, args.judge_timeout)
            raw_rows.append(
                {
                    "QID": qid,
                    "Question": question,
                    "Answer": answer,
                    "Cited IDs": ",".join(map(str, cited_ids)),
                    "Citation Reason": citation_reason,
                    "Answer Match": str(answer_match).lower(),
                    "Notes": notes,
                }
            )
            write_raw_tsv(raw_tsv, raw_rows)

        run_compare(scripts_dir, question_file, raw_tsv, video_name, report, metrics_json)
        reports.append(report)

    run_compare_total(scripts_dir, run_dir)
    log("")
    log(f"Final output directory: {run_dir}")
    log("Video report files written:")
    for report in reports:
        log(str(report))
    log(f"Aggregate report: {run_dir / 'total.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
