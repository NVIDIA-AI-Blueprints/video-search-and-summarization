#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Frame-aware VLM inspection with deterministic output-quality recovery."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

MAX_VLM_FRAMES = 60
MAX_DEGENERATE_RETRIES = 1
ATTEMPT_CONTEXT_ENV = "VSS_INTROSPECTION_ATTEMPT_CONTEXT"
DEFAULT_ATTEMPT_CONTEXT_PATH = Path(
    "/sandbox/.openclaw/workspace/.vss/introspection-attempt.json"
)
QUALITY_REPAIR_SUFFIX = (
    "\n\nQuality repair: report only chronological visible facts from this "
    "window. Do not emit per-frame boilerplate, repeat a template, or include "
    "private reasoning. If the requested fact is not visible, state that once."
)
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_UNCLOSED_THINK = re.compile(r"<think>", re.IGNORECASE)
_TIMESTAMP = re.compile(r"\b(?:(\d{1,2}):)?(\d{1,2}):(\d{2}(?:\.\d+)?)\b")
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
_SPACE = re.compile(r"\s+")


def _parse_instant(value: str) -> datetime:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _format_instant(value: datetime) -> str:
    value = value.astimezone(timezone.utc)
    if value.microsecond:
        rendered = value.isoformat(timespec="milliseconds")
    else:
        rendered = value.isoformat(timespec="seconds")
    return rendered.replace("+00:00", "Z")


def window_duration_seconds(start: str, end: str) -> float:
    duration = (_parse_instant(end) - _parse_instant(start)).total_seconds()
    if duration <= 0:
        raise ValueError("window start must be before end")
    return duration


def frames_requested(fps: float, duration_seconds: float) -> int:
    if fps <= 0:
        raise ValueError("fps must be greater than zero")
    if duration_seconds <= 0:
        raise ValueError("duration must be greater than zero")
    return max(1, math.ceil(fps * duration_seconds))


def split_sensor_window(
    start: str,
    end: str,
    fps: float,
    *,
    max_frames: int = MAX_VLM_FRAMES,
) -> list[dict[str, Any]]:
    """Split a sensor interval so every request stays within the frame cap."""
    if max_frames < 1:
        raise ValueError("max_frames must be at least one")
    start_at = _parse_instant(start)
    end_at = _parse_instant(end)
    duration = window_duration_seconds(start, end)
    count = max(1, math.ceil((fps * duration) / max_frames))
    segment_seconds = duration / count
    windows: list[dict[str, Any]] = []
    for index in range(count):
        segment_start = start_at + timedelta(seconds=segment_seconds * index)
        segment_end = end_at if index == count - 1 else start_at + timedelta(
            seconds=segment_seconds * (index + 1)
        )
        segment_duration = (segment_end - segment_start).total_seconds()
        windows.append(
            {
                "start": _format_instant(segment_start),
                "end": _format_instant(segment_end),
                "duration_seconds": round(segment_duration, 3),
                "frames_requested": frames_requested(fps, segment_duration),
            }
        )
    return windows


def normalize_vlm_text(raw: str) -> str:
    without_thinking = _THINK_BLOCK.sub("", raw)
    return without_thinking.strip()


def _line_material(line: str) -> str:
    return _SPACE.sub(" ", _NUMBER.sub("<n>", line.casefold())).strip()


def _prompt_digest(prompt: str) -> str:
    normalized = _SPACE.sub(" ", prompt).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _call_key(
    sensor: str,
    start: str,
    end: str,
    fps: float,
    prompt_sha256: str,
) -> tuple[str, str, str, float, str]:
    return (sensor, start, end, float(fps), prompt_sha256)


def load_attempt_history(paths: Sequence[str]) -> list[dict[str, Any]]:
    """Load persisted inspection attempts used for duplicate rejection."""
    attempts: list[dict[str, Any]] = []
    for raw_path in paths:
        value = json.loads(Path(raw_path).read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("attempts"), list):
            raise ValueError(f"history file lacks an attempts array: {raw_path}")
        attempts.extend(item for item in value["attempts"] if isinstance(item, dict))
    return attempts


def expected_sensor_from_task(path: str, task_id: str | None = None) -> str:
    """Load the immutable sensor UUID from a canonical inspection task."""
    task = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(task, list):
        matches = [
            item
            for item in task
            if isinstance(item, dict)
            and task_id is not None
            and item.get("task_id") == task_id
        ]
        if len(matches) != 1:
            raise ValueError("task list must contain exactly one matching task_id")
        task = matches[0]
    if not isinstance(task, dict):
        raise ValueError("inspection task must be an object")
    scope = task.get("media_scope")
    if not isinstance(scope, dict) or scope.get("type") != "sensor":
        raise ValueError("inspection task must contain a sensor media_scope")
    sensor = scope.get("sensor_id")
    if not isinstance(sensor, str) or not sensor.strip():
        raise ValueError("inspection task sensor_id must be a non-empty string")
    return sensor


def allowed_sensors_from_attempt_context() -> tuple[str, ...]:
    """Return harness-bound sensor identifiers when an eval context exists."""
    path = Path(
        os.environ.get(ATTEMPT_CONTEXT_ENV, str(DEFAULT_ATTEMPT_CONTEXT_PATH))
    )
    if not path.is_file():
        return ()
    context = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(context, dict):
        raise ValueError("introspection attempt context must be an object")
    values = (context.get("video_id"), context.get("sensor_id"))
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("attempt context must contain video_id and sensor_id")
    return tuple(dict.fromkeys(str(value) for value in values))


def _timestamps_seconds(text: str) -> list[float]:
    values: list[float] = []
    for match in _TIMESTAMP.finditer(text):
        hours = float(match.group(1) or 0)
        minutes = float(match.group(2))
        seconds = float(match.group(3))
        values.append(hours * 3600 + minutes * 60 + seconds)
    return values


def classify_vlm_output(raw: str) -> dict[str, Any]:
    """Classify structural output quality without judging visual correctness."""
    normalized = normalize_vlm_text(raw)
    if not normalized:
        return {"usable": False, "reason": "empty", "normalized_text": ""}
    if _UNCLOSED_THINK.search(normalized):
        return {
            "usable": False,
            "reason": "unclosed_reasoning",
            "normalized_text": normalized,
        }
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    material = [_line_material(line) for line in lines]
    if len(material) >= 8 and len(set(material)) <= max(2, len(material) // 5):
        return {
            "usable": False,
            "reason": "repetitive_template",
            "normalized_text": normalized,
        }
    timestamps = _timestamps_seconds(normalized)
    if len(timestamps) >= 4:
        descending = sum(a > b for a, b in zip(timestamps, timestamps[1:]))
        ascending = sum(a < b for a, b in zip(timestamps, timestamps[1:]))
        if descending > ascending * 2 and descending >= 3:
            return {
                "usable": False,
                "reason": "reverse_chronology",
                "normalized_text": normalized,
            }
    return {"usable": True, "reason": "ok", "normalized_text": normalized}


def parse_vlm_stdout(stdout: str) -> dict[str, Any]:
    """Return the first JSON object carrying the VLM answer contract."""
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and (
            "answer" in value or "job_id" in value or "status" in value
        ):
            return value
    return {}


def _run_vlm(
    *,
    vss_project: str,
    sensor: str,
    start: str,
    end: str,
    fps: float,
    prompt: str,
) -> dict[str, Any]:
    command = [
        "uv",
        "run",
        "--project",
        vss_project,
        "vss",
        "vlm",
        "run",
        "--prompt",
        prompt,
        "--sensor",
        sensor,
        "--start-time",
        start,
        "--end-time",
        end,
        "--fps",
        str(fps),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    payload = parse_vlm_stdout(completed.stdout)
    answer = payload.get("answer")
    verdict = classify_vlm_output(answer if isinstance(answer, str) else "")
    return {
        "exit_code": completed.returncode,
        "job_id": payload.get("job_id"),
        "status": payload.get("status"),
        "answer": verdict["normalized_text"],
        "usable": completed.returncode in (0, 6) and verdict["usable"],
        "quality_reason": verdict["reason"],
        "error": completed.stderr.strip() or payload.get("error"),
    }


def inspect_sensor_scope(
    *,
    vss_project: str,
    sensor: str,
    start: str,
    end: str,
    fps: float,
    prompt: str,
    calls_budget: int,
    max_frames: int = MAX_VLM_FRAMES,
    max_degenerate_retries: int = MAX_DEGENERATE_RETRIES,
    prior_attempts: Sequence[dict[str, Any]] = (),
    expected_sensor: str | None = None,
    allowed_sensors: Sequence[str] = (),
) -> dict[str, Any]:
    """Inspect every affordable subwindow before bounded quality retries."""
    if calls_budget < 1:
        raise ValueError("calls_budget must be at least one")
    if expected_sensor is not None and sensor != expected_sensor:
        raise ValueError(
            f"sensor mismatch: requested {sensor!r}, task requires {expected_sensor!r}"
        )
    if allowed_sensors and (
        sensor not in allowed_sensors
        or (expected_sensor is not None and expected_sensor not in allowed_sensors)
    ):
        raise ValueError(
            f"sensor mismatch: {sensor!r} is outside the current case binding"
        )
    windows = split_sensor_window(start, end, fps, max_frames=max_frames)
    attempts: list[dict[str, Any]] = []
    rejected_duplicates: list[dict[str, Any]] = []
    seen = {
        _call_key(
            str(item.get("sensor_id") or sensor),
            str(item.get("start") or ""),
            str(item.get("end") or ""),
            float(item.get("fps") or 0),
            str(item.get("prompt_sha256") or ""),
        )
        for item in prior_attempts
        if item.get("start")
        and item.get("end")
        and item.get("fps") is not None
        and item.get("prompt_sha256")
    }
    completed_windows: set[int] = set()
    retry_windows: list[tuple[int, dict[str, Any]]] = []
    for window_index, window in enumerate(windows):
        if len(attempts) >= calls_budget:
            break
        prompt_sha256 = _prompt_digest(prompt)
        call_key = _call_key(
            sensor, window["start"], window["end"], fps, prompt_sha256
        )
        if call_key in seen:
            rejected_duplicates.append(
                {
                    "window_index": window_index,
                    "sensor_id": sensor,
                    "start": window["start"],
                    "end": window["end"],
                    "fps": fps,
                    "prompt_sha256": prompt_sha256,
                    "retry": 0,
                    "reason": "duplicate_window_call",
                }
            )
            continue
        attempt = _run_vlm(
            vss_project=vss_project,
            sensor=sensor,
            start=window["start"],
            end=window["end"],
            fps=fps,
            prompt=prompt,
        )
        attempt.update(
            {
                "window_index": window_index,
                "sensor_id": sensor,
                "start": window["start"],
                "end": window["end"],
                "fps": fps,
                "frames_requested": window["frames_requested"],
                "prompt_sha256": prompt_sha256,
                "retry": 0,
            }
        )
        attempts.append(attempt)
        seen.add(call_key)
        if attempt["usable"]:
            completed_windows.add(window_index)
        elif max_degenerate_retries:
            retry_windows.append((window_index, window))

    for window_index, window in retry_windows:
        if len(attempts) >= calls_budget:
            break
        attempt_prompt = prompt + QUALITY_REPAIR_SUFFIX
        prompt_sha256 = _prompt_digest(attempt_prompt)
        call_key = _call_key(
            sensor, window["start"], window["end"], fps, prompt_sha256
        )
        if call_key in seen:
            rejected_duplicates.append(
                {
                    "window_index": window_index,
                    "sensor_id": sensor,
                    "start": window["start"],
                    "end": window["end"],
                    "fps": fps,
                    "prompt_sha256": prompt_sha256,
                    "retry": 1,
                    "reason": "duplicate_window_call",
                }
            )
            continue
        attempt = _run_vlm(
            vss_project=vss_project,
            sensor=sensor,
            start=window["start"],
            end=window["end"],
            fps=fps,
            prompt=attempt_prompt,
        )
        attempt.update(
            {
                "window_index": window_index,
                "sensor_id": sensor,
                "start": window["start"],
                "end": window["end"],
                "fps": fps,
                "frames_requested": window["frames_requested"],
                "prompt_sha256": prompt_sha256,
                "retry": 1,
            }
        )
        attempts.append(attempt)
        seen.add(call_key)
        if attempt["usable"]:
            completed_windows.add(window_index)
    return {
        "sensor_id": sensor,
        "scope": {"start": start, "end": end},
        "fps": fps,
        "max_frames_per_call": max_frames,
        "planned_windows": windows,
        "attempts": attempts,
        "rejected_duplicates": rejected_duplicates,
        "vlm_calls_used": len(attempts),
        "complete": len(completed_windows) == len(windows),
        "coverage": (
            "sufficient"
            if len(completed_windows) == len(windows)
            else "partial"
            if completed_windows
            else "none"
        ),
        "gap": (
            None
            if len(completed_windows) == len(windows)
            else f"{len(windows) - len(completed_windows)} of {len(windows)} planned windows lack usable output."
        ),
    }


def _write_json(path: str | None, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan-windows")
    plan.add_argument("--start", required=True)
    plan.add_argument("--end", required=True)
    plan.add_argument("--fps", required=True, type=float)
    plan.add_argument("--max-frames", type=int, default=MAX_VLM_FRAMES)
    plan.add_argument("--output")
    classify = commands.add_parser("classify")
    classify.add_argument("--input", required=True)
    classify.add_argument("--output")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--vss-project", required=True)
    inspect.add_argument("--task", required=True)
    inspect.add_argument("--task-id")
    inspect.add_argument("--sensor", required=True)
    inspect.add_argument("--start", required=True)
    inspect.add_argument("--end", required=True)
    inspect.add_argument("--fps", required=True, type=float)
    inspect.add_argument("--prompt", required=True)
    inspect.add_argument("--calls-budget", required=True, type=int)
    inspect.add_argument("--max-frames", type=int, default=MAX_VLM_FRAMES)
    inspect.add_argument("--history", action="append", default=[])
    inspect.add_argument("--output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "plan-windows":
        windows = split_sensor_window(
            args.start, args.end, args.fps, max_frames=args.max_frames
        )
        _write_json(
            args.output,
            {
                "fps": args.fps,
                "max_frames": args.max_frames,
                "scope": {"start": args.start, "end": args.end},
                "windows": windows,
            },
        )
    elif args.command == "classify":
        _write_json(
            args.output,
            classify_vlm_output(Path(args.input).read_text(encoding="utf-8")),
        )
    else:
        _write_json(
            args.output,
            inspect_sensor_scope(
                vss_project=args.vss_project,
                sensor=args.sensor,
                start=args.start,
                end=args.end,
                fps=args.fps,
                prompt=args.prompt,
                calls_budget=args.calls_budget,
                max_frames=args.max_frames,
                prior_attempts=load_attempt_history(args.history),
                expected_sensor=expected_sensor_from_task(args.task, args.task_id),
                allowed_sensors=allowed_sensors_from_attempt_context(),
            ),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
