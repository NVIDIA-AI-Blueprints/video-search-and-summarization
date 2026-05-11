#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Manually exercise the *VLM-only* path used for base-profile report generation:

  1) Download a clip from an HTTP(S) URL (same idea as the agent after vst_video_clip).
  2) Sample JPEG frames via ``frame_select`` (same as ``video_understanding`` with ``openai_*`` VLM).
  3) POST OpenAI-compatible ``/v1/chat/completions`` with system + multimodal user content.

This skips VST tools, report markdown/PDF, and HITL — only the vision LLM call.

Usage:
  export VLM_BASE_URL=http://127.0.0.1:30082
  export VLM_NAME=nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4
  python3 scripts/manual_vlm_report_prompt_test.py --video-url 'http://.../clip.mp4'

Optional API key (remote endpoints):
  export OPENAI_API_KEY=...
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

# Repo: agent/src on PYTHONPATH
_ROOT = Path(__file__).resolve().parents[1]
_AGENT_SRC = _ROOT / "agent" / "src"
if _AGENT_SRC.is_dir():
    sys.path.insert(0, str(_AGENT_SRC))

from vss_agents.utils.frame_select import frame_select  # noqa: E402

import cv2  # noqa: E402

# --- dev-profile-base: video_report_gen.vlm_prompt (lines 106–113) ---
REPORT_VLM_PROMPT = """Describe in detail what is happening in this video,
including all visible people, vehicles, equipments, objects,
actions, and environmental conditions.
OUTPUT REQUIREMENTS:
[timestamp-timestamp] Description of what is happening.
EXAMPLE:
[0.0s-4.0s] <description of the first event>
[4.0s-12.0s] <description of the second event>"""

# --- Same suffix as video_report_gen.CHUNK_TIMESTAMP_PROMPT (single full clip: 0 → duration) ---
CHUNK_TIMESTAMP_PROMPT = """
    All events from the video should fall within the time range:
    START_TIME: {start_time}s
    END_TIME: {end_time}s
"""

# --- dev-profile-base: video_understanding.system_prompt ---
SYSTEM_PROMPT = """You are a monitoring system analyzing video footage.
Your task is to describe the events in the video in detail or answer the user's question about the video.
  IMPORTANT:
  - You must respond only in English and in plain text.
  - You must respond only in the format specified in the OUTPUT REQUIREMENTS section.
  - Timestamp must be in pts format, seconds since the start of the video.
  - Always provide a direct answer to the question asked.
  - Never return an empty response. If you cannot find what the user is asking about, acknowledge it to the user.
"""


def _video_duration_seconds(path: str) -> float:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError(f"Could not open video: {path}")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS)) or 25.0
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        return n / fps if fps > 0 else 0.0
    finally:
        cap.release()


def _download(url: str, dest: Path, timeout: int) -> None:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        dest.write_bytes(resp.read())


def _build_user_prompt(duration: float, report_prompt: str) -> str:
    chunk = CHUNK_TIMESTAMP_PROMPT.format(start_time=0, end_time=duration)
    return f"{report_prompt.strip()}\n\n{chunk.strip()}"


def _build_openai_messages(
    *,
    system_prompt: str | None,
    user_prompt: str,
    jpeg_base64_list: list[str],
) -> list[dict]:
    # Mirrors video_understanding._build_vlm_messages (use_frame_images=True)
    text = (
        "The following images are a sequence of frames from a video. "
        f"Answer the user's question based on the video: {user_prompt}"
    )
    user_content: list[dict] = [{"type": "text", "text": text}]
    for b64 in jpeg_base64_list:
        user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_content})
    return messages


def main() -> int:
    p = argparse.ArgumentParser(description="Isolated VLM test (report-style prompt + frame sampling).")
    p.add_argument(
        "--video-url",
        default=os.environ.get("VIDEO_URL", "http://127.0.0.1:30888/REPLACE_WITH_YOUR_CLIP.mp4"),
        help="HTTP(S) URL to an MP4 the machine can download (edit default or pass flag).",
    )
    p.add_argument(
        "--vlm-base-url",
        default=os.environ.get("VLM_BASE_URL", "http://127.0.0.1:30082").rstrip("/"),
        help="OpenAI-compatible server base (no /v1 suffix).",
    )
    p.add_argument(
        "--model",
        default=os.environ.get("VLM_NAME", "nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4"),
        help="Model id for chat/completions.",
    )
    p.add_argument("--max-frames", type=int, default=7, help="Match video_understanding max_frames (<=7 for 8-image cap).")
    p.add_argument("--max-fps", type=int, default=2, help="Match video_understanding max_fps.")
    p.add_argument("--max-tokens", type=int, default=4096)
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--timeout", type=int, default=600, help="Download and HTTP timeout (seconds).")
    p.add_argument(
        "--skip-system",
        action="store_true",
        help="Do not send the video_understanding system prompt (minimal test).",
    )
    p.add_argument(
        "--prompt",
        default=REPORT_VLM_PROMPT,
        help="Override report VLM prompt (default: dev-profile-base video_report_gen.vlm_prompt).",
    )
    args = p.parse_args()

    if args.max_frames > 7:
        print("Warning: OpenAI-style APIs often allow at most 8 images per request; keeping <=7 is safest.", file=sys.stderr)

    endpoint = f"{args.vlm_base_url}/v1/chat/completions"

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        print(f"Downloading: {args.video_url}", file=sys.stderr)
        try:
            _download(args.video_url, tmp_path, timeout=args.timeout)
        except urllib.error.HTTPError as e:
            print(f"HTTP error downloading video: {e}", file=sys.stderr)
            return 1
        except urllib.error.URLError as e:
            print(f"URL error downloading video: {e}", file=sys.stderr)
            return 1

        duration = _video_duration_seconds(str(tmp_path))
        if duration <= 0:
            print("Could not determine video duration.", file=sys.stderr)
            return 1

        # Same formula as video_understanding (approx)
        num_frames = min(int(duration) * args.max_fps, args.max_frames)
        if num_frames < 1:
            num_frames = 1
        step_size = max(duration / num_frames, 1.0 / args.max_fps)

        print(
            f"Video length: {duration:.1f}s, num_frames target: {num_frames}, step_size: {step_size:.3f}s",
            file=sys.stderr,
        )
        frames = frame_select(str(tmp_path), 0.0, duration, step_size)
        if len(frames) > args.max_frames:
            frames = frames[: args.max_frames]

        if not frames:
            print("No frames extracted.", file=sys.stderr)
            return 1

        user_prompt = _build_user_prompt(duration, args.prompt)

        system = None if args.skip_system else SYSTEM_PROMPT
        messages = _build_openai_messages(system_prompt=system, user_prompt=user_prompt, jpeg_base64_list=frames)

        body = {
            "model": args.model,
            "messages": messages,
            "max_tokens": args.max_tokens,
            "temperature": args.temperature,
        }

        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        key = os.environ.get("OPENAI_API_KEY") or os.environ.get("NVIDIA_API_KEY")
        if key:
            headers["Authorization"] = f"Bearer {key}"

        print(f"POST {endpoint} ({len(frames)} frames)", file=sys.stderr)
        req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=args.timeout) as resp:
                out = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            print(f"HTTP {e.code}: {err_body}", file=sys.stderr)
            return 1

    finally:
        tmp_path.unlink(missing_ok=True)

    # Print assistant text (OpenAI-style)
    try:
        choice = out["choices"][0]
        msg = choice.get("message", {})
        content = msg.get("content")
        print(content if isinstance(content, str) else json.dumps(msg, indent=2))
    except (KeyError, IndexError, TypeError):
        print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
