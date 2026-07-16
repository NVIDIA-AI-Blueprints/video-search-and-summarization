#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run deterministic cross-conversation memory eval scenarios."""

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
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__:
    from .context_pressure import (
        CONTEXT_PRESSURE_PLACEMENTS,
        apply_context_pressure,
        context_pressure_settings,
        should_apply_context_pressure,
    )
else:
    from context_pressure import (
        CONTEXT_PRESSURE_PLACEMENTS,
        apply_context_pressure,
        context_pressure_settings,
        should_apply_context_pressure,
    )


# =============================================================================
# Section 0 - Constants And Small Utilities
# =============================================================================

DEFAULT_SUMMARY_DIR = Path(__file__).resolve().parents[1] / "frozen_summarization_server" / "data"
DEFAULT_QUESTION_DIR = Path("questions")
DEFAULT_RESULTS_DIR = Path("results")
MANIFEST_SCHEMA_VERSION = 1
CANONICAL_CATEGORIES = ("within_event", "entity_relational", "temporal")
CANONICAL_QUESTIONS_PER_CATEGORY = 5


def log(message: str) -> None:
    print(message, flush=True)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def is_cross_question_document(value: Any) -> bool:
    required_legacy_keys = {"scenario_id", "turn_id", "family", "question"}
    if isinstance(value, list):
        return bool(value) and all(
            isinstance(row, dict) and required_legacy_keys <= set(row) for row in value
        )
    return (
        isinstance(value, dict)
        and value.get("schema_version") == MANIFEST_SCHEMA_VERSION
        and isinstance(value.get("scenarios"), list)
        and bool(value["scenarios"])
    )


def discover_question_files(
    question_dir: Path | None = None,
    question_file: Path | None = None,
) -> list[Path]:
    if question_dir is not None and question_file is not None:
        raise ValueError("--question-file and --question-dir are mutually exclusive")

    if question_file is not None:
        candidate = question_file.expanduser()
        if not candidate.is_absolute() and not candidate.is_file():
            candidate = DEFAULT_QUESTION_DIR / candidate
        if not candidate.is_file():
            raise FileNotFoundError(f"Question file not found: {candidate}")
        if candidate.suffix != ".json":
            raise ValueError(f"Cross-video question file must be JSON: {candidate}")
        return [candidate]

    resolved_dir = (question_dir or DEFAULT_QUESTION_DIR).expanduser()
    if not resolved_dir.is_dir():
        raise FileNotFoundError(f"Question directory not found: {resolved_dir}")

    files: list[Path] = []
    for candidate in sorted(resolved_dir.glob("*.json")):
        try:
            value = read_json(candidate)
        except json.JSONDecodeError:
            continue
        if is_cross_question_document(value):
            files.append(candidate)
    if not files:
        raise FileNotFoundError(f"No cross-video question files found in {resolved_dir}")
    return files


def require_mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be a JSON object")
    return value


def require_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} must be a non-empty string")
    return value.strip()


def load_canonical_questions(path: Path, focal_video_id: str) -> list[dict[str, Any]]:
    try:
        document = require_mapping(read_json(path), str(path))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Canonical single-video question file not found: {path}") from exc

    video_id = require_string(document.get("video_id"), f"{path}: video_id")
    if video_id != focal_video_id:
        raise ValueError(
            f"{path}: video_id {video_id!r} does not match focal_video_id {focal_video_id!r}"
        )
    questions = document.get("questions")
    if not isinstance(questions, list) or not all(isinstance(row, dict) for row in questions):
        raise ValueError(f"{path}: questions must be a JSON array of objects")

    expected_total = len(CANONICAL_CATEGORIES) * CANONICAL_QUESTIONS_PER_CATEGORY
    if len(questions) != expected_total:
        raise ValueError(f"{path}: expected {expected_total} canonical questions, got {len(questions)}")
    category_counts = {
        category: sum(row.get("category") == category for row in questions)
        for category in CANONICAL_CATEGORIES
    }
    expected_counts = {
        category: CANONICAL_QUESTIONS_PER_CATEGORY for category in CANONICAL_CATEGORIES
    }
    if category_counts != expected_counts:
        raise ValueError(
            f"{path}: expected canonical category counts {expected_counts}, got {category_counts}"
        )

    qids = [row.get("qid") for row in questions]
    if qids != list(range(1, expected_total + 1)):
        raise ValueError(f"{path}: qids must be consecutive integers 1..{expected_total}")
    for row in questions:
        location = f"{path}: qid {row['qid']}"
        require_string(row.get("question"), f"{location} question")
        require_string(row.get("expected_answer_target"), f"{location} expected_answer_target")
        event_ids = row.get("expected_event_ids")
        if not isinstance(event_ids, list) or not all(type(event_id) is int for event_id in event_ids):
            raise ValueError(f"{location} expected_event_ids must be an integer array")
    return questions


def expand_cross_manifest(path: Path, document: dict[str, Any]) -> list[dict[str, Any]]:
    if document.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            f"{path}: schema_version must be {MANIFEST_SCHEMA_VERSION}"
        )
    scenarios = document.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError(f"{path}: scenarios must be a non-empty JSON array")

    expanded: list[dict[str, Any]] = []
    seen_scenario_ids: set[str] = set()
    for scenario_index, raw_scenario in enumerate(scenarios, start=1):
        scenario = require_mapping(raw_scenario, f"{path}: scenario {scenario_index}")
        scenario_id = require_string(
            scenario.get("scenario_id"), f"{path}: scenario {scenario_index} scenario_id"
        )
        if scenario_id in seen_scenario_ids:
            raise ValueError(f"{path}: duplicate scenario_id {scenario_id!r}")
        seen_scenario_ids.add(scenario_id)
        location = f"{path}: scenario {scenario_id}"
        incident_id = require_string(scenario.get("incident_id"), f"{location} incident_id")
        focal_video_id = require_string(
            scenario.get("focal_video_id"), f"{location} focal_video_id"
        )
        source_value = require_string(
            scenario.get("single_question_source"), f"{location} single_question_source"
        )
        source_path = Path(source_value).expanduser()
        if not source_path.is_absolute():
            source_path = path.parent / source_path
        source_path = source_path.resolve()
        canonical_questions = load_canonical_questions(source_path, focal_video_id)

        locator = require_mapping(scenario.get("locator"), f"{location} locator")
        locator_videos = parse_expected_video_ids(locator.get("expected_video_ids"))
        if locator_videos != [focal_video_id]:
            raise ValueError(
                f"{location} locator expected_video_ids must contain only focal_video_id"
            )
        require_complete_expected_evidence(
            locator.get("expected_event_ids"), locator_videos, f"{location} locator"
        )
        expanded.append(
            {
                "scenario_id": scenario_id,
                "incident_id": incident_id,
                "focal_video_id": focal_video_id,
                "turn_id": 1,
                "turn_kind": "locator",
                "family": locator.get("family", "cross_video_locator"),
                "question": require_string(locator.get("question"), f"{location} locator question"),
                "expected_answer_target": require_string(
                    locator.get("expected_answer_target"),
                    f"{location} locator expected_answer_target",
                ),
                "expected_video_ids": locator_videos,
                "expected_event_ids": locator.get("expected_event_ids"),
            }
        )

        scenario_turn_id = 1
        for question in canonical_questions:
            scenario_turn_id += 1
            expanded.append(
                {
                    "scenario_id": scenario_id,
                    "incident_id": incident_id,
                    "focal_video_id": focal_video_id,
                    "turn_id": scenario_turn_id,
                    "turn_kind": "canonical_followup",
                    "family": question["category"],
                    "source_qid": question["qid"],
                    "question": f"For that same video: {question['question']}",
                    "expected_answer_target": question["expected_answer_target"],
                    "expected_video_ids": [focal_video_id],
                    "expected_event_ids": {
                        focal_video_id: question["expected_event_ids"]
                    },
                }
            )

        cross_questions = scenario.get("cross_video_questions", [])
        if not isinstance(cross_questions, list) or not all(
            isinstance(row, dict) for row in cross_questions
        ):
            raise ValueError(f"{location} cross_video_questions must be a JSON array of objects")
        cqids = [row.get("cqid") for row in cross_questions]
        if cqids != list(range(1, len(cross_questions) + 1)):
            raise ValueError(f"{location} cross-video cqids must be consecutive integers from 1")
        for cross_question in cross_questions:
            scenario_turn_id += 1
            cross_location = f"{location} cqid {cross_question['cqid']}"
            expected_videos = parse_expected_video_ids(
                cross_question.get("expected_video_ids")
            )
            if len(expected_videos) < 2:
                raise ValueError(f"{cross_location} must require at least two expected videos")
            if focal_video_id not in expected_videos:
                raise ValueError(f"{cross_location} must include focal_video_id in expected_video_ids")
            require_complete_expected_evidence(
                cross_question.get("expected_event_ids"), expected_videos, cross_location
            )
            expanded.append(
                {
                    "scenario_id": scenario_id,
                    "incident_id": incident_id,
                    "focal_video_id": focal_video_id,
                    "turn_id": scenario_turn_id,
                    "turn_kind": "cross_video_evidence_join",
                    "family": cross_question.get("family", "cross_video_evidence_join"),
                    "reasoning_axis": cross_question.get("reasoning_axis", ""),
                    "source_qid": cross_question["cqid"],
                    "question": require_string(
                        cross_question.get("question"), f"{cross_location} question"
                    ),
                    "expected_answer_target": require_string(
                        cross_question.get("expected_answer_target"),
                        f"{cross_location} expected_answer_target",
                    ),
                    "expected_video_ids": expected_videos,
                    "expected_event_ids": cross_question.get("expected_event_ids"),
                }
            )
    return expanded


def load_question_files(question_files: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scenario_sources: dict[str, Path] = {}
    for question_file in question_files:
        document = read_json(question_file)
        if isinstance(document, list):
            if not all(isinstance(row, dict) for row in document):
                raise ValueError(f"Every question must be a JSON object: {question_file}")
            file_rows = document
        elif isinstance(document, dict):
            file_rows = expand_cross_manifest(question_file, document)
        else:
            raise ValueError(f"Unsupported cross-question document: {question_file}")
        for scenario_id in {str(row.get("scenario_id", "")) for row in file_rows}:
            previous = scenario_sources.get(scenario_id)
            if previous is not None:
                raise ValueError(
                    f"Scenario {scenario_id!r} appears in both {previous} and {question_file}"
                )
            scenario_sources[scenario_id] = question_file
        rows.extend(file_rows)
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key.strip().lower().replace(" ", "_"): value.strip() if isinstance(value, str) else value
        for key, value in row.items()
    }


def extract_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    best: dict[str, Any] | None = None
    preferred_keys = {
        "answer",
        "video_ids",
        "saved",
        "ready",
        "pressure_ack",
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


def parse_expected_video_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(video_id, str) for video_id in value):
        raise ValueError("expected_video_ids must be a JSON array of strings")
    return [video_id.strip() for video_id in value if video_id.strip()]


def parse_id_list(value: str) -> list[int]:
    return [int(match.group(0)) for match in re.finditer(r"\d+", value)]


def parse_expected_evidence(value: Any, expected_video_ids: list[str]) -> set[tuple[str, int]]:
    """Parse the {video_id: [event_id]} expected-evidence JSON object."""
    if value is None:
        return set()
    if not isinstance(value, dict):
        raise ValueError("expected_event_ids must be a JSON object mapping video IDs to integer arrays")
    unexpected_videos = set(value) - set(expected_video_ids)
    if unexpected_videos:
        raise ValueError(f"expected_event_ids contains videos not listed in expected_video_ids: {unexpected_videos}")
    evidence: set[tuple[str, int]] = set()
    for video_id, event_ids in value.items():
        if not isinstance(event_ids, list) or not all(type(event_id) is int for event_id in event_ids):
            raise ValueError("expected_event_ids values must be JSON arrays of integers")
        evidence.update((str(video_id), event_id) for event_id in event_ids)
    return evidence


def require_complete_expected_evidence(
    value: Any,
    expected_video_ids: list[str],
    location: str,
) -> set[tuple[str, int]]:
    evidence = parse_expected_evidence(value, expected_video_ids)
    if not isinstance(value, dict):
        raise ValueError(f"{location}: expected_event_ids must be a JSON object")
    missing_videos = set(expected_video_ids) - set(value)
    if missing_videos:
        raise ValueError(
            f"{location}: expected_event_ids is missing expected videos: {missing_videos}"
        )
    empty_videos = [video_id for video_id in expected_video_ids if not value[video_id]]
    if empty_videos:
        raise ValueError(
            f"{location}: expected_event_ids must cite representative evidence for: {empty_videos}"
        )
    return evidence


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


def evidence_to_json(evidence: set[tuple[str, int]]) -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for video_id, event_id in sorted(evidence):
        grouped[video_id].append(event_id)
    return dict(grouped)


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


def group_scenarios(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in rows:
        row = normalize_row(raw)
        row.setdefault(
            "turn_kind", "locator" if int(row["turn_id"]) == 1 else "legacy_followup"
        )
        grouped[row["scenario_id"]].append(row)
    for scenario_id, scenario_rows in grouped.items():
        scenario_rows.sort(key=lambda item: int(item["turn_id"]))
        turn_ids = [int(row["turn_id"]) for row in scenario_rows]
        expected_turn_ids = list(range(1, len(scenario_rows) + 1))
        if turn_ids != expected_turn_ids:
            raise ValueError(
                f"{scenario_id} must have consecutive turn IDs 1..{len(scenario_rows)}, "
                f"got {turn_ids}"
            )
        if scenario_rows[0]["turn_kind"] != "locator":
            raise ValueError(f"{scenario_id} turn 1 must be a locator")
    return dict(sorted(grouped.items()))


def validate_questions(rows: list[dict[str, Any]], summaries: dict[str, Any]) -> list[str]:
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
    row: dict[str, Any],
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
                "description": "Video IDs used to answer. Include every video used for cross-video joins.",
                "uniqueItems": True,
            },
            "event_ids": {
                "description": (
                    "Focused supporting event IDs. For normal one-video turns, use an array of integers. "
                    "For cross-video joins, use an object mapping each video_id to an array of integer "
                    "event IDs. Use the smallest directly supporting evidence set."
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
        "Maintain scenario context across follow-up turns such as 'that video' or 'same incident'.\n"
        "Retrieve additional video memories when a cross-video evidence join requires them.\n"
        "Every answer must be grounded with focused video_ids and event_ids.\n"
        "Do not cite every related event; cite only directly necessary support.\n"
        "Return only valid JSON matching this schema:\n"
        f"{json.dumps(schema, indent=2)}\n\n"
        f"Scenario ID: {row['scenario_id']}\n"
        f"Turn ID: {row['turn_id']}\n"
        f"Turn kind: {row.get('turn_kind', '')}\n"
        f"Family: {row['family']}\n"
        f"Reasoning axis: {row.get('reasoning_axis', '')}\n"
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
    locator_rows = [row for row in rows if row["turn_kind"] == "locator"]
    answer_rows = [
        row
        for row in rows
        if row["turn_kind"] != "locator" and row["answer_accuracy"] is not None
    ]
    return {
        "total_turns": len(rows),
        "locator_turns": len(locator_rows),
        "locator_correct": sum(int(row["locator_accuracy"]) for row in locator_rows if row["locator_accuracy"] is not None),
        "judged_answers": len(answer_rows),
        "correct_answers": sum(int(row["answer_accuracy"]) for row in answer_rows),
        "mean_precision": sum(float(row["precision"]) for row in rows) / len(rows) if rows else 0.0,
        "mean_recall": sum(float(row["recall"]) for row in rows) / len(rows) if rows else 0.0,
        "mean_f1_score": sum(float(row["f1_score"]) for row in rows) / len(rows) if rows else 0.0,
        "mean_latency_ms": int(sum(int(row["latency_ms"]) for row in rows) / len(rows)) if rows else 0,
        "mean_num_tool_calls": sum(int(row["num_tool_calls"]) for row in rows) / len(rows) if rows else 0.0,
    }


def summarize_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = summarize_rows(rows)
    return {
        "total_turns": metrics["total_turns"],
        "locator_accuracy": (
            metrics["locator_correct"] / metrics["locator_turns"]
            if metrics["locator_turns"]
            else None
        ),
        "answer_accuracy": (
            metrics["correct_answers"] / metrics["judged_answers"]
            if metrics["judged_answers"]
            else None
        ),
        "mean_evidence_precision": metrics["mean_precision"],
        "mean_evidence_recall": metrics["mean_recall"],
        "mean_evidence_f1_score": metrics["mean_f1_score"],
        "mean_latency_ms": metrics["mean_latency_ms"],
        "mean_num_tool_calls": metrics["mean_num_tool_calls"],
    }


def build_report_json(
    run_id: str,
    rows: list[dict[str, Any]],
    scenario_count: int,
    run_config: dict[str, Any],
) -> dict[str, Any]:
    metrics = summarize_rows(rows)
    by_scenario = []
    for scenario_id in sorted({row["scenario_id"] for row in rows}):
        summary = summarize_group([row for row in rows if row["scenario_id"] == scenario_id])
        summary["scenario_id"] = scenario_id
        by_scenario.append(summary)
    by_family = []
    for family in sorted({row["family"] for row in rows}):
        summary = summarize_group([row for row in rows if row["family"] == family])
        summary["family"] = family
        by_family.append(summary)
    by_turn_kind = []
    for turn_kind in sorted({row["turn_kind"] for row in rows}):
        summary = summarize_group([row for row in rows if row["turn_kind"] == turn_kind])
        summary["turn_kind"] = turn_kind
        by_turn_kind.append(summary)
    return {
        "run_id": run_id,
        "eval_type": "cross_conversation_memory",
        **run_config,
        "summary": {
            "total_turns": metrics["total_turns"],
            "scenario_count": scenario_count,
            "locator_accuracy": (
                metrics["locator_correct"] / metrics["locator_turns"]
                if metrics["locator_turns"]
                else 0.0
            ),
            "answer_accuracy": (
                metrics["correct_answers"] / metrics["judged_answers"]
                if metrics["judged_answers"]
                else 0.0
            ),
            "mean_evidence_precision": metrics["mean_precision"],
            "mean_evidence_recall": metrics["mean_recall"],
            "mean_evidence_f1_score": metrics["mean_f1_score"],
            "mean_latency_ms": metrics["mean_latency_ms"],
            "mean_num_tool_calls": metrics["mean_num_tool_calls"],
        },
        "counts": {
            "locator_turns": metrics["locator_turns"],
            "correct_locator_turns": metrics["locator_correct"],
            "judged_answer_turns": metrics["judged_answers"],
            "correct_answer_turns": metrics["correct_answers"],
        },
        "breakdown": {
            "by_scenario": by_scenario,
            "by_family": by_family,
            "by_turn_kind": by_turn_kind,
        },
        "artifacts": {
            "raw_json": "raw.json",
            "report_md": "report.md",
            "validation_warnings": "debug/validation_warnings.txt",
            "memory_save_log": "debug/memory_save_openclaw.log",
        },
    }


def write_report(path: Path, report_json: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    summary = report_json["summary"]
    counts = report_json["counts"]
    lines = [
        "# Cross-Conversation Memory Eval Report",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Locator Accuracy | `{counts['correct_locator_turns']}/{counts['locator_turns']} = {fmt_float(summary['locator_accuracy'])}` |",
        f"| Answer Accuracy | `{counts['correct_answer_turns']}/{counts['judged_answer_turns']} = {fmt_float(summary['answer_accuracy'])}` |",
        f"| Mean Evidence Precision | `{fmt_float(summary['mean_evidence_precision'])}` |",
        f"| Mean Evidence Recall | `{fmt_float(summary['mean_evidence_recall'])}` |",
        f"| Mean Evidence F1 Score | `{fmt_float(summary['mean_evidence_f1_score'])}` |",
        f"| Mean Latency MS | `{summary['mean_latency_ms']}` |",
        f"| Mean Tool Calls | `{fmt_float(summary['mean_num_tool_calls'])}` |",
        "",
        "| Scenario | Turn | Kind | Family | Expected Videos | Predicted Videos | Expected Evidence | Predicted Evidence | Locator Accuracy | Answer Accuracy | Precision | Recall | F1 |",
        "|---|---:|---|---|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        expected_evidence = ";".join(f"{video}:{','.join(map(str, ids))}" for video, ids in row["expected_event_ids"].items())
        predicted_evidence = ";".join(f"{video}:{','.join(map(str, ids))}" for video, ids in row["predicted_event_ids"].items())
        lines.append(
            f"| {row['scenario_id']} | {row['turn_id']} | {row['turn_kind']} | {row['family']} | {','.join(row['expected_video_ids'])} | "
            f"{','.join(row['predicted_video_ids'])} | {expected_evidence} | {predicted_evidence} | "
            f"{'' if row['locator_accuracy'] is None else row['locator_accuracy']} | "
            f"{'' if row['answer_accuracy'] is None else row['answer_accuracy']} | "
            f"{fmt_float(row['precision'])} | {fmt_float(row['recall'])} | {fmt_float(row['f1_score'])} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# =============================================================================
# Section 6 - Main Orchestration
# =============================================================================


def main() -> int:
    parser = argparse.ArgumentParser()
    question_source = parser.add_mutually_exclusive_group()
    question_source.add_argument(
        "--question-file",
        type=Path,
        help="Run one cross-video question JSON file.",
    )
    question_source.add_argument(
        "--question-dir",
        type=Path,
        help="Run every valid cross-video question JSON file in this directory.",
    )
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
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
    parser.add_argument(
        "--context-pressure-turns",
        type=int,
        default=0,
        help="Number of synthetic filler turns per context-pressure injection point.",
    )
    parser.add_argument(
        "--context-pressure-chars",
        type=int,
        default=0,
        help="Characters of irrelevant filler text per context-pressure turn.",
    )
    parser.add_argument(
        "--context-pressure-placement",
        choices=CONTEXT_PRESSURE_PLACEMENTS,
        default="none",
        help="When to inject context-pressure filler relative to scenario turns.",
    )
    args = parser.parse_args()

    question_files = discover_question_files(args.question_dir, args.question_file)
    run_id, run_dir = make_run_dir(args.results_dir, args.run_id)
    debug_dir = run_dir / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    log(f"Run directory: {run_dir}")
    run_config = {
        **context_pressure_settings(
            args.context_pressure_turns,
            args.context_pressure_chars,
            args.context_pressure_placement,
        ),
        "openclaw_model": args.openclaw_model,
        "judge_model": args.judge_model,
    }

    summaries = load_summaries(args.summary_dir)
    log(f"Loaded {len(summaries)} summaries from {args.summary_dir}")

    question_rows = load_question_files(question_files)
    grouped = group_scenarios(question_rows)
    warnings = validate_questions(question_rows, summaries)
    validation_path = debug_dir / "validation_warnings.txt"
    validation_path.write_text("\n".join(warnings) + ("\n" if warnings else ""), encoding="utf-8")
    if warnings:
        log("Validation warnings:")
        for warning in warnings:
            log(f"  - {warning}")
    else:
        log("Question validation passed with no missing expected videos/events.")

    save_log = debug_dir / "memory_save_openclaw.log"
    if args.reset_memory:
        log("Resetting OpenClaw durable memory for cross eval")
        reset_memory(debug_dir / "memory_reset.log")
    if args.skip_ingest or args.seed_each_scenario:
        reason = "--skip-ingest" if args.skip_ingest else "--seed-each-scenario"
        save_log.write_text(f"Memory save skipped because {reason} was used.\n", encoding="utf-8")
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
    raw_json = run_dir / "raw.json"

    for scenario_id, scenario_rows in grouped.items():
        log(f"Processing {scenario_id}")
        session_key = f"vss-eval-cross-{run_id}-{scenario_id}"
        log_path = debug_dir / f"{scenario_id}_openclaw.log"
        if args.seed_each_scenario:
            seed_scenario_context(summaries, session_key, args.openclaw_model, args.openclaw_timeout, log_path)

        for row in scenario_rows:
            turn_id = int(row["turn_id"])
            if should_apply_context_pressure(args.context_pressure_placement, turn_id):
                if args.context_pressure_turns > 0 and args.context_pressure_chars > 0:
                    log(
                        f"  {scenario_id} T{turn_id}: applying context pressure "
                        f"({args.context_pressure_turns} turns x {args.context_pressure_chars} chars)"
                    )
                    apply_context_pressure(
                        args.context_pressure_turns,
                        args.context_pressure_chars,
                        session_key,
                        args.openclaw_model,
                        args.openclaw_timeout,
                        log_path,
                        run_openclaw_json,
                    )

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

            turn_kind = str(row.get("turn_kind", ""))
            locator_accuracy: int | None = None
            if turn_kind == "locator":
                locator_accuracy = 1 if set(expected_videos).issubset(set(predicted_videos)) else 0

            answer_accuracy: int | None = None
            notes = ""
            if turn_kind != "locator" and not args.skip_judge:
                log(f"  {scenario_id} T{row['turn_id']}: judging")
                answer_match, notes = judge_answer(
                    row.get("question", ""),
                    row.get("expected_answer_target", ""),
                    answer,
                    args.judge_model,
                    args.judge_timeout,
                )
                answer_accuracy = 1 if answer_match else 0
            elif turn_kind != "locator" and args.skip_judge:
                notes = "judge skipped"

            raw_rows.append(
                {
                    "scenario_id": scenario_id,
                    "turn_id": turn_id,
                    "turn_kind": turn_kind,
                    "family": row.get("family", ""),
                    "incident_id": row.get("incident_id", ""),
                    "focal_video_id": row.get("focal_video_id", ""),
                    "source_qid": row.get("source_qid"),
                    "reasoning_axis": row.get("reasoning_axis", ""),
                    "question": row.get("question", ""),
                    "expected_answer_target": row.get("expected_answer_target", ""),
                    "answer": answer,
                    "expected_video_ids": expected_videos,
                    "predicted_video_ids": predicted_videos,
                    "expected_event_ids": evidence_to_json(expected_evidence),
                    "predicted_event_ids": evidence_to_json(predicted_evidence),
                    "locator_accuracy": locator_accuracy,
                    "answer_accuracy": answer_accuracy,
                    "precision": precision,
                    "recall": recall,
                    "f1_score": f1,
                    "confidence": str(parsed.get("confidence", "")).strip(),
                    "not_stated": bool(parsed.get("not_stated", False)),
                    "judge_notes": notes,
                    "latency_ms": latency_ms,
                    "num_tool_calls": tool_calls,
                }
            )
            write_json(raw_json, raw_rows)

    report_json = build_report_json(run_id, raw_rows, len(grouped), run_config)
    report_json_path = run_dir / "report.json"
    report_path = run_dir / "report.md"
    write_json(report_json_path, report_json)
    write_report(report_path, report_json, raw_rows)

    log("")
    log(f"Final output directory: {run_dir}")
    log(f"Raw results: {raw_json}")
    log(f"Report JSON: {report_json_path}")
    log(f"Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
