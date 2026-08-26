#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Temporary visual-introspection bridge; streams only the VLM answer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

import httpx
from vss_core.memory import UnifiedMemoryRecord


def _sensor(record: UnifiedMemoryRecord, video_id: str) -> str:
    ext = record.output.ext if record.output and record.output.ext else {}
    for video in ext.get("videos", []):
        if isinstance(video, dict) and str(video.get("video_id")) == video_id:
            return str(video["vios_sensor"])
    raise ValueError(f"authoritative record has no mapping for video {video_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--vlm-endpoint", required=True)
    parser.add_argument("--vlm-model", required=True)
    args = parser.parse_args()
    record = UnifiedMemoryRecord.model_validate_json(sys.stdin.read())
    repo = Path.home() / "video-search-and-summarization"
    command = [
        "uv", "run", "--project", str(repo / "services/agent"), "--no-dev", "--extra", "cli",
        "vss", "vios", "clip", "--sensor", _sensor(record, args.video_id),
    ]
    clip = json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)
    payload = {
        "model": args.vlm_model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": args.query},
                {"type": "video_url", "video_url": {"url": clip["media_url"]}},
            ],
        }],
        "max_tokens": 1024,
        "temperature": 0.0,
    }
    model = args.vlm_model.lower()
    if "cosmos-reason2" in model:
        payload["mm_processor_kwargs"] = {
            "size": {"shortest_edge": 3136, "longest_edge": 8388608}
        }
        payload["media_io_kwargs"] = {"video": {"num_frames": 30}}
    elif "cosmos" in model:
        payload["mm_processor_kwargs"] = {
            "videos_kwargs": {"min_pixels": 3136, "max_pixels": 8388608}
        }
        payload["media_io_kwargs"] = {"video": {"num_frames": 30}}
    elif ":8018" in args.vlm_endpoint or "rtvlm" in model:
        payload.update({
            "num_frames_per_second_or_fixed_frames_chunk": 20,
            "use_fps_for_chunking": False,
            "vlm_input_width": 1280,
            "vlm_input_height": 720,
        })
    response = httpx.post(
        f"{args.vlm_endpoint.rstrip('/')}/chat/completions",
        json=payload,
        timeout=600.0,
    )
    response.raise_for_status()
    print(response.json()["choices"][0]["message"]["content"])


if __name__ == "__main__":
    main()
