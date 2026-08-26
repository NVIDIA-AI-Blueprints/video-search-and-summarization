#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify that every benchmark video was downloaded and registered in VIOS."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

import pyarrow.parquet as pq

VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".mkv", ".webm"})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    args = parser.parse_args()
    required = {str(value) for value in pq.read_table(args.dataset, columns=["video_id"])["video_id"].to_pylist()}
    video_dir = Path(os.environ["TMPDIR"]) / "videos"
    downloaded = {
        path.stem
        for path in video_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    }
    missing_downloads = required - downloaded
    if missing_downloads:
        raise SystemExit(f"missing downloaded benchmark videos: {sorted(missing_downloads)}")

    repo = Path.home() / "video-search-and-summarization"
    command = [
        "uv", "run", "--project", str(repo / "services/agent"), "--no-dev", "--extra", "cli",
        "vss", "vios", "list", "--type", "video",
    ]
    payload = json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)
    sensors = {str(sensor.get("name")) for sensor in payload.get("sensors", []) if sensor.get("name")}
    missing_sensors = required - sensors
    if missing_sensors:
        raise SystemExit(f"missing VIOS benchmark sensors: {sorted(missing_sensors)}")


if __name__ == "__main__":
    main()
