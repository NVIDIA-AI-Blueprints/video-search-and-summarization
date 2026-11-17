#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Frame-aware VLM inspection with deterministic output-quality recovery."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

MAX_VLM_FRAMES = 60
MAX_DEGENERATE_RETRIES = 1
ATTEMPT_CONTEXT_ENV = "VSS_INTROSPECTION_ATTEMPT_CONTEXT"
DEFAULT_ATTEMPT_CONTEXT_PATH = Path(
    "/sandbox/.openclaw/workspace/.vss/introspection-attempt.json"
)
REMOTE_VLM_CONFIG_PATH = Path(
    os.environ.get("VSS_REMOTE_VLM_CONFIG", "/sandbox/.vss/remote-vlm.json")
)
QUALITY_REPAIR_SUFFIX = (
    "\n\nQuality repair: report only chronological visible facts from this "
    "window. Do not emit per-frame boilerplate, repeat a template, or include "
    "private reasoning. If the requested fact is not visible, state that once."
)
OUTPUT_MODE_LEGACY = "legacy"
OUTPUT_MODE_TIMESTAMPED = "timestamped-caption"
OUTPUT_MODES = (OUTPUT_MODE_LEGACY, OUTPUT_MODE_TIMESTAMPED)
TIMESTAMPED_QUALITY_REPAIR_SUFFIX = (
    "\n\nQuality repair: emit one [start_seconds:end_seconds] line per event "
    "or unchanged state, ordered by start time. Do not narrate the whole "
    "window in one interval or put a sequence of events inside one caption. "
    "Do not emit per-frame boilerplate, repeat a template, or include private "
    "reasoning. If no relevant event is visible, say so once for this sampled "
    "window; that is not proof the event never occurred."
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


def caption_contract(duration_seconds: float) -> str:
    """Fixed reporting contract for one submitted subwindow.

    ``duration_seconds`` is the duration of that subwindow, not the parent task.
    """
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("submitted duration must be a positive finite number")
    duration = f"{duration_seconds:.3f}"
    return (
        "The inspection focus above is the only requested observation. "
        "Report only what is visibly present in this submitted window. "
        f"This window lasts {duration} seconds. Timestamps are model-estimated "
        "offsets from the start of this submitted window. They are not "
        "camera-overlay clocks or verified frame-level annotations.\n"
        "Write one caption line per visible event or unchanged state, ordered by start time:\n"
        "[start_seconds:end_seconds] caption\n"
        "Do not put a sequence of events inside one caption. "
        f"Do not copy this window's full duration as [0:{duration}] unless the "
        "described fact is visibly unchanged for that entire span. "
        'If the scene changes, start a new line. If you need "then", "later", '
        '"around", or a second clock time, you needed a new line.\n'
        "Correct:\n"
        "[12.0:14.5] The middle worker lifts the yellow hard hat off his head.\n"
        "[14.5:20.0] He holds the hat in both hands in front of his chest.\n"
        "[20.0:22.0] He places the hat back on his head.\n"
        "Incorrect:\n"
        f"[0.0:{duration}] Workers talk, then the middle worker removes his hat "
        "around 14s, then puts it back, then a forklift passes around 2:36.\n"
        "Requirements:\n"
        f"- 0 <= start_seconds <= end_seconds <= {duration}.\n"
        "- Use overlapping intervals when events are simultaneous.\n"
        "- Use a point interval [t:t] when only a single sample supports an observation.\n"
        "- Keep descriptors for the same visible entity consistent.\n"
        "- Give each distinct repeated event its own caption.\n"
        "- Distinguish before, during, and after when those phases are visible.\n"
        "- Distinguish image-relative direction from object-relative direction.\n"
        "- Do not assert unsupported identity, intent, causality, or an answer selection.\n"
        "- Do not invent events or temporal precision between sampled frames.\n"
        "- If an event starts before this window or continues beyond it, say so.\n"
        "- Visibility limitations use the same shape: "
        "[start_seconds:end_seconds] LIMITATION: description.\n"
        "- If no relevant event is observed, report that as a limitation of this "
        "sampled window. That limitation is not proof the event never occurred."
    )


def effective_inspection_prompt(
    focus: str,
    *,
    output_mode: str = OUTPUT_MODE_LEGACY,
    duration_seconds: float | None = None,
    repair: bool = False,
) -> str:
    """Keep the agent focus and, in timestamped mode, append the caption contract."""
    if output_mode not in OUTPUT_MODES:
        raise ValueError(f"unknown output mode: {output_mode}")
    if output_mode == OUTPUT_MODE_LEGACY:
        return focus + (QUALITY_REPAIR_SUFFIX if repair else "")
    if duration_seconds is None:
        raise ValueError("timestamped mode requires the submitted window duration")
    prompt = focus.rstrip() + "\n\n" + caption_contract(duration_seconds)
    if repair:
        prompt += TIMESTAMPED_QUALITY_REPAIR_SUFFIX
    return prompt


def _prompt_digest(prompt: str) -> str:
    normalized = _SPACE.sub(" ", prompt).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _call_key(
    sensor: str,
    start: str,
    end: str,
    fps: float,
    prompt_sha256: str,
    claim_id: str = "",
) -> tuple[str, str, str, float, str, str]:
    return (sensor, start, end, float(fps), prompt_sha256, claim_id)


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
    return str(load_selected_task(path, task_id)["media_scope"]["sensor_id"])


def load_selected_task(path: str, task_id: str | None = None) -> dict[str, Any]:
    """Load one sensor inspection task and reject an ambiguous selection."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        if task_id is None:
            raise ValueError("task list requires exactly one task_id")
        matches = [
            item
            for item in payload
            if isinstance(item, dict) and item.get("task_id") == task_id
        ]
        if len(matches) != 1:
            raise ValueError("task list must contain exactly one matching task_id")
        task = matches[0]
    elif isinstance(payload, dict):
        if task_id is not None and payload.get("task_id") != task_id:
            raise ValueError("task_id does not match the inspection task")
        task = payload
    else:
        raise ValueError("inspection task must be an object")
    _validate_sensor_task(task)
    return task


def _validate_sensor_task(task: dict[str, Any]) -> None:
    """Reject a task that cannot bound a sensor VLM call."""
    if not isinstance(task, dict):
        raise ValueError("inspection task must be an object")
    scope = task.get("media_scope")
    if not isinstance(scope, dict):
        raise ValueError("inspection task must contain a media_scope object")
    if scope.get("type") != "sensor":
        raise ValueError("inspection task must contain a sensor media_scope")
    sensor = scope.get("sensor_id")
    if not isinstance(sensor, str) or not sensor.strip():
        raise ValueError("inspection task sensor_id must be a non-empty string")
    start = scope.get("start")
    end = scope.get("end")
    if not isinstance(start, str) or not isinstance(end, str):
        raise ValueError("inspection task sensor scope requires start and end")
    window_duration_seconds(start, end)
    allocation = task.get("max_vlm_calls")
    if isinstance(allocation, bool) or not isinstance(allocation, int) or allocation < 1:
        raise ValueError("inspection task max_vlm_calls must be a positive integer")


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


def _json_values(stdout: str) -> list[Any]:
    """Decode successive JSON values without treating a prefix as the document."""
    decoder = json.JSONDecoder()
    index = 0
    length = len(stdout)
    values: list[Any] = []
    while index < length:
        while index < length and stdout[index].isspace():
            index += 1
        if index >= length:
            break
        try:
            value, end = decoder.raw_decode(stdout, index)
        except json.JSONDecodeError:
            return []
        values.append(value)
        index = end
    return values


def parse_vlm_stdout(stdout: str) -> dict[str, Any]:
    """Return the answer-bearing VLM body, not a later completion marker.

    ``vss vlm run`` prints one JSON body and then one compact completion
    marker. ``--pretty`` makes the body multiline; ``--raw`` keeps it compact.
    A marker that only carries job identity is not an answer.
    """
    objects = [value for value in _json_values(stdout) if isinstance(value, dict)]
    answer_bearing = [item for item in objects if "answer" in item]
    markers = [
        item
        for item in objects
        if isinstance(item.get("event"), str) and item["event"].startswith("vss_job_")
    ]
    if not answer_bearing:
        if not markers:
            return {}
        preserved: dict[str, Any] = {}
        marker = markers[-1]
        for key in ("job_id", "status", "error"):
            if key in marker:
                preserved[key] = marker[key]
        return preserved
    chosen = dict(answer_bearing[0])
    if markers:
        marker = markers[-1]
        for key in ("job_id", "status", "error"):
            if chosen.get(key) in (None, "") and key in marker:
                chosen[key] = marker[key]
    return chosen


def remote_vlm_spec() -> dict[str, Any] | None:
    """Optional Inference Hub override. Unset config keeps local `vss vlm run`."""
    model = (os.environ.get("VSS_REMOTE_VLM_MODEL") or "").strip()
    api = (os.environ.get("VSS_REMOTE_VLM_API") or "").strip()
    base = (os.environ.get("VSS_REMOTE_VLM_BASE") or "").strip()
    spec: dict[str, Any] = {}
    if REMOTE_VLM_CONFIG_PATH.is_file():
        loaded = json.loads(REMOTE_VLM_CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            spec.update(loaded)
    if model:
        spec["model"] = model
    if api:
        spec["api"] = api
    if base:
        spec["base_url"] = base
    model_id = str(spec.get("model") or "").strip()
    if not model_id:
        return None
    lowered = model_id.lower()
    if "cosmos3-super-reasoner" in lowered or "nim_nvidia_cosmos3-super" in lowered:
        raise RuntimeError(f"refusing to invoke CR3 Super: {model_id}")
    spec.setdefault("api", "responses" if "gpt-6-astra" in lowered else "chat_completions")
    spec.setdefault("base_url", "https://inference-api.nvidia.com/v1")
    spec.setdefault("max_tokens", 4096)
    spec.setdefault("timeout_seconds", 1200)
    spec["model"] = model_id
    return spec


def _inference_api_key() -> str:
    key = (os.environ.get("NVIDIA_INFERENCE_API_KEY") or os.environ.get("VSS_VLM_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("NVIDIA_INFERENCE_API_KEY is not available")
    return key


def _ffmpeg_bin() -> str:
    configured = (os.environ.get("VSS_FFMPEG") or "").strip()
    if configured:
        return configured
    path = shutil.which("ffmpeg")
    if path:
        return path
    raise RuntimeError("ffmpeg is required for remote VLM frame reconstruction")


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("empty json")
    try:
        payload = json.loads(stripped)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    if start < 0:
        raise ValueError("no json object")
    decoder = json.JSONDecoder()
    parsed, _ = decoder.raw_decode(stripped, start)
    if not isinstance(parsed, dict):
        raise ValueError("json payload is not an object")
    return parsed


def _vss_command(vss_project: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "--project", vss_project, "vss", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _extract_clip(vss_project: str, sensor: str, start: str, end: str, dest: Path) -> None:
    completed = _vss_command(
        vss_project,
        "vios",
        "clip",
        "--sensor",
        sensor,
        "--start-time",
        start,
        "--end-time",
        end,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            (completed.stderr or completed.stdout or "vss vios clip failed").strip()[-2000:]
        )
    payload = _parse_json_object(completed.stdout)
    media_url = str(payload.get("media_url") or "").strip()
    if not media_url:
        raise RuntimeError("vss vios clip returned no media_url")
    request = urllib.request.Request(media_url, method="GET")
    with urllib.request.urlopen(request, timeout=120) as response:
        dest.write_bytes(response.read())
    if dest.stat().st_size < 32:
        raise RuntimeError("downloaded clip is empty")


def _sample_frames(clip: Path, dest: Path, fps: float, frames_needed: int) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    pattern = dest / "frame_%04d.jpg"
    completed = subprocess.run(
        [
            _ffmpeg_bin(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(clip),
            "-vf",
            f"fps={fps}",
            "-frames:v",
            str(frames_needed),
            "-q:v",
            "2",
            str(pattern),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or "ffmpeg failed").strip()[-1000:])
    frames = sorted(dest.glob("frame_*.jpg"))
    if not frames:
        raise RuntimeError("ffmpeg produced no frames")
    return frames


def _extract_remote_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return str(payload)
    choices = payload.get("choices") or []
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [
                item.get("text")
                for item in content
                if isinstance(item, dict) and item.get("text")
            ]
            return "\n".join(str(text) for text in texts)
        if choices[0].get("text"):
            return str(choices[0]["text"])
    if payload.get("output_text"):
        return str(payload["output_text"])
    output = payload.get("output")
    if isinstance(output, list):
        chunks: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            for content in item.get("content") or []:
                if isinstance(content, dict) and content.get("text"):
                    chunks.append(str(content["text"]))
        if chunks:
            return "\n".join(chunks)
    return json.dumps(payload)[:20000]


def _post_inference(url: str, payload: dict[str, Any], timeout: int) -> tuple[int, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {_inference_api_key()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                return response.status, json.loads(raw)
            except json.JSONDecodeError:
                return response.status, raw
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            parsed: Any = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        return error.code, parsed


def _call_remote_vlm(spec: dict[str, Any], prompt: str, frame_paths: list[Path]) -> dict[str, Any]:
    model_id = str(spec["model"])
    api = str(spec.get("api") or "chat_completions")
    base = str(spec.get("base_url") or "").rstrip("/")
    max_tokens = int(spec.get("max_tokens") or 4096)
    timeout = int(spec.get("timeout_seconds") or 1200)
    images = []
    for path in frame_paths:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        images.append(f"data:image/jpeg;base64,{encoded}")
    if api == "responses":
        url = f"{base}/responses"
        payload: dict[str, Any] = {
            "model": model_id,
            "max_output_tokens": max_tokens,
            "input": [
                {
                    "role": "user",
                    "content": [
                        *[{"type": "input_image", "image_url": url} for url in images],
                        {"type": "input_text", "text": prompt},
                    ],
                }
            ],
        }
    else:
        url = f"{base}/chat/completions"
        payload = {
            "model": model_id,
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        *[{"type": "image_url", "image_url": {"url": item}} for item in images],
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }
    last_status = 0
    last_payload: Any = {}
    errors: list[str] = []
    for attempt in range(4):
        status, response = _post_inference(url, payload, timeout)
        last_status, last_payload = status, response
        if status in {429, 500, 502, 503, 504} and attempt < 3:
            errors.append(f"transport {status}")
            continue
        break
    text = _extract_remote_text(last_payload)
    ok = 200 <= last_status < 300
    return {
        "http_status": last_status,
        "ok": ok,
        "text": text,
        "error": None if ok else (str(last_payload)[:1500] or "; ".join(errors)),
        "job_id": "vlm-remote-" + uuid4().hex.upper(),
    }


def _run_remote_vlm(
    *,
    spec: dict[str, Any],
    vss_project: str,
    sensor: str,
    start: str,
    end: str,
    fps: float,
    prompt: str,
) -> dict[str, Any]:
    duration = window_duration_seconds(start, end)
    frames_needed = min(MAX_VLM_FRAMES, frames_requested(fps, duration))
    with tempfile.TemporaryDirectory(prefix="vss-remote-vlm-") as raw:
        root = Path(raw)
        clip = root / "clip.mp4"
        try:
            _extract_clip(vss_project, sensor, start, end, clip)
            frames = _sample_frames(clip, root / "frames", fps, frames_needed)
            result = _call_remote_vlm(spec, prompt, frames)
        except Exception as error:
            return {
                "exit_code": 1,
                "job_id": None,
                "status": "failed",
                "raw_answer": "",
                "answer": "",
                "usable": False,
                "persistence_limited": False,
                "quality_reason": "tool_failure",
                "error": str(error)[-2000:],
                "remote_model": spec.get("model"),
                "remote_api": spec.get("api"),
                "frames_sent": 0,
            }
    raw_answer = result.get("text") if isinstance(result.get("text"), str) else ""
    verdict = classify_vlm_output(raw_answer)
    exit_code = 0 if result.get("ok") else 1
    return {
        "exit_code": exit_code,
        "job_id": result.get("job_id"),
        "status": "completed" if result.get("ok") else "failed",
        "raw_answer": raw_answer,
        "answer": verdict["normalized_text"],
        "usable": exit_code == 0 and verdict["usable"],
        "persistence_limited": False,
        "quality_reason": verdict["reason"],
        "error": result.get("error"),
        "remote_model": spec.get("model"),
        "remote_api": spec.get("api"),
        "http_status": result.get("http_status"),
        "frames_sent": frames_needed,
    }


def _run_vlm(
    *,
    vss_project: str,
    sensor: str,
    start: str,
    end: str,
    fps: float,
    prompt: str,
) -> dict[str, Any]:
    spec = remote_vlm_spec()
    if spec is not None:
        return _run_remote_vlm(
            spec=spec,
            vss_project=vss_project,
            sensor=sensor,
            start=start,
            end=end,
            fps=fps,
            prompt=prompt,
        )
    command = [
        "uv",
        "run",
        "--project",
        vss_project,
        "vss",
        "vlm",
        "run",
        "--raw",
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
    raw_answer = answer if isinstance(answer, str) else ""
    verdict = classify_vlm_output(raw_answer)
    return {
        "exit_code": completed.returncode,
        "job_id": payload.get("job_id"),
        "status": payload.get("status"),
        "raw_answer": raw_answer,
        "answer": verdict["normalized_text"],
        "usable": completed.returncode in (0, 6) and verdict["usable"],
        "persistence_limited": completed.returncode == 6,
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
    assigned_start: str | None = None,
    assigned_end: str | None = None,
    task_call_allocation: int | None = None,
    claim_id: str | None = None,
    output_mode: str = OUTPUT_MODE_LEGACY,
) -> dict[str, Any]:
    """Inspect every affordable subwindow before bounded quality retries."""
    if output_mode not in OUTPUT_MODES:
        raise ValueError(f"unknown output mode: {output_mode}")
    if calls_budget < 1:
        raise ValueError("calls_budget must be at least one")
    if (
        isinstance(task_call_allocation, bool)
        or (
            task_call_allocation is not None
            and (not isinstance(task_call_allocation, int) or task_call_allocation < 1)
        )
    ):
        raise ValueError("task allocation must be a positive integer")
    if task_call_allocation is not None and calls_budget > task_call_allocation:
        raise ValueError("calls_budget exceeds the task allocation")
    requested_start = _parse_instant(start)
    requested_end = _parse_instant(end)
    if requested_start >= requested_end:
        raise ValueError("window start must be before end")
    if (assigned_start is None) != (assigned_end is None):
        raise ValueError("assigned scope requires both start and end")
    if assigned_start is not None and assigned_end is not None:
        scope_start = _parse_instant(assigned_start)
        scope_end = _parse_instant(assigned_end)
        if scope_start >= scope_end:
            raise ValueError("assigned window start must be before end")
        if requested_start < scope_start or requested_end > scope_end:
            raise ValueError("requested window is outside the assigned media scope")
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
    reused_evidence: list[dict[str, Any]] = []
    bound_claim = claim_id or ""
    priors_by_key: dict[tuple[str, str, str, float, str, str], list[dict[str, Any]]] = {}
    for item in prior_attempts:
        if not (
            item.get("start")
            and item.get("end")
            and item.get("fps") is not None
            and item.get("prompt_sha256")
            and item.get("sensor_id")
        ):
            continue
        key = _call_key(
            str(item.get("sensor_id")),
            str(item.get("start")),
            str(item.get("end")),
            float(item.get("fps") or 0),
            str(item.get("prompt_sha256")),
            str(item.get("claim_id") or ""),
        )
        priors_by_key.setdefault(key, []).append(item)
    completed_windows: set[int] = set()
    retry_windows: list[tuple[int, dict[str, Any]]] = []

    def _remember_duplicate(
        window_index: int,
        window: dict[str, Any],
        prompt_sha256: str,
        retry: int,
        priors: Sequence[dict[str, Any]],
    ) -> None:
        usable_prior = next((item for item in priors if item.get("usable") is True), None)
        if usable_prior is not None:
            reused_evidence.append(
                {
                    "window_index": window_index,
                    "sensor_id": sensor,
                    "start": window["start"],
                    "end": window["end"],
                    "fps": fps,
                    "prompt_sha256": prompt_sha256,
                    "claim_id": usable_prior.get("claim_id") or claim_id,
                    "job_id": usable_prior.get("job_id"),
                    "status": usable_prior.get("status"),
                    "answer": usable_prior.get("answer")
                    if isinstance(usable_prior.get("answer"), str)
                    else "",
                    "observation_id": usable_prior.get("observation_id"),
                    "exit_code": usable_prior.get("exit_code"),
                    "output_mode": usable_prior.get("output_mode"),
                    "source": "prior_inspection",
                }
            )
            completed_windows.add(window_index)
            return
        rejected_duplicates.append(
            {
                "window_index": window_index,
                "sensor_id": sensor,
                "start": window["start"],
                "end": window["end"],
                "fps": fps,
                "prompt_sha256": prompt_sha256,
                "retry": retry,
                "reason": "duplicate_window_call",
            }
        )

    def _window_prompt(window: dict[str, Any], *, repair: bool) -> tuple[str, str, float]:
        duration = window_duration_seconds(window["start"], window["end"])
        effective = effective_inspection_prompt(
            prompt,
            output_mode=output_mode,
            duration_seconds=duration,
            repair=repair,
        )
        return effective, _prompt_digest(effective), duration

    for window_index, window in enumerate(windows):
        effective_prompt, prompt_sha256, duration = _window_prompt(window, repair=False)
        call_key = _call_key(
            sensor, window["start"], window["end"], fps, prompt_sha256, bound_claim
        )
        priors = priors_by_key.get(call_key, [])
        if priors:
            _remember_duplicate(window_index, window, prompt_sha256, 0, priors)
            continue
        if len(attempts) >= calls_budget:
            continue
        attempt = _run_vlm(
            vss_project=vss_project,
            sensor=sensor,
            start=window["start"],
            end=window["end"],
            fps=fps,
            prompt=effective_prompt,
        )
        attempt.update(
            {
                "window_index": window_index,
                "sensor_id": sensor,
                "start": window["start"],
                "end": window["end"],
                "fps": fps,
                "frames_requested": window["frames_requested"],
                "submitted_duration_seconds": duration,
                "output_mode": output_mode,
                "inspection_focus": prompt,
                "effective_prompt": effective_prompt,
                "prompt_sha256": prompt_sha256,
                "retry": 0,
            }
        )
        attempts.append(attempt)
        priors_by_key.setdefault(call_key, []).append(attempt)
        if attempt["usable"]:
            completed_windows.add(window_index)
        elif max_degenerate_retries:
            retry_windows.append((window_index, window))

    for window_index, window in retry_windows:
        if len(attempts) >= calls_budget:
            break
        effective_prompt, prompt_sha256, duration = _window_prompt(window, repair=True)
        call_key = _call_key(
            sensor, window["start"], window["end"], fps, prompt_sha256, bound_claim
        )
        priors = priors_by_key.get(call_key, [])
        if priors:
            _remember_duplicate(window_index, window, prompt_sha256, 1, priors)
            continue
        attempt = _run_vlm(
            vss_project=vss_project,
            sensor=sensor,
            start=window["start"],
            end=window["end"],
            fps=fps,
            prompt=effective_prompt,
        )
        attempt.update(
            {
                "window_index": window_index,
                "sensor_id": sensor,
                "start": window["start"],
                "end": window["end"],
                "fps": fps,
                "frames_requested": window["frames_requested"],
                "submitted_duration_seconds": duration,
                "output_mode": output_mode,
                "inspection_focus": prompt,
                "effective_prompt": effective_prompt,
                "prompt_sha256": prompt_sha256,
                "retry": 1,
            }
        )
        attempts.append(attempt)
        priors_by_key.setdefault(call_key, []).append(attempt)
        if attempt["usable"]:
            completed_windows.add(window_index)
    covered = len(completed_windows)
    total = len(windows)
    if covered == total:
        window_inspection = "complete"
    elif covered:
        window_inspection = "partial"
    else:
        window_inspection = "none"
    return {
        "sensor_id": sensor,
        "claim_id": claim_id,
        "output_mode": output_mode,
        "scope": {"start": start, "end": end},
        "fps": fps,
        "max_frames_per_call": max_frames,
        "planned_windows": windows,
        "attempts": attempts,
        "reused_evidence": reused_evidence,
        "rejected_duplicates": rejected_duplicates,
        "vlm_calls_used": len(attempts),
        "complete": covered == total,
        "window_inspection": window_inspection,
        "claim_sufficiency": None,
        "gap": (
            None
            if covered == total
            else f"{total - covered} of {total} planned windows lack usable output."
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
    inspect.add_argument(
        "--output-mode",
        choices=OUTPUT_MODES,
        default=OUTPUT_MODE_LEGACY,
        help=(
            "legacy keeps the agent prompt unchanged. "
            "timestamped-caption appends the caption reporting instructions to each subwindow."
        ),
    )
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
        selected = load_selected_task(args.task, args.task_id)
        scope = selected["media_scope"]
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
                expected_sensor=scope["sensor_id"],
                allowed_sensors=allowed_sensors_from_attempt_context(),
                assigned_start=scope["start"],
                assigned_end=scope["end"],
                task_call_allocation=selected["max_vlm_calls"],
                output_mode=args.output_mode,
                claim_id=(
                    selected["claim"]["claim_id"]
                    if isinstance(selected.get("claim"), dict)
                    and isinstance(selected["claim"].get("claim_id"), str)
                    else None
                ),
            ),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
