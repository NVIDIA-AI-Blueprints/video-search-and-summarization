# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Admitted local media and bounded external-tool oracles; no frame arrays retained."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import time
from fractions import Fraction
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from .diagnostics import retain
from .results import Evidence

MAX_INPUT_BYTES = 256 * 1024**2
MAX_ORACLE_BYTES = 64 * 1024**2
MAX_FRAMES = 12000


class MediaError(RuntimeError):
    def __init__(self, reason: str, *, details: dict | None = None):
        self.reason = reason
        self.details = details or {}
        super().__init__(reason)


def bounded_tool(command: list[str], *, limit: int, timeout: float = 90) -> bytes:
    """Bound both streams while producing output; inherit the worker's group."""
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        child = subprocess.Popen(command, stdout=stdout, stderr=stderr)
        started = time.monotonic()
        reason = "ok"
        returned_bytes = None
        try:
            while True:
                if stdout.tell() > limit or stderr.tell() > 1024**2:
                    raise MediaError("tool_output_limit")
                if time.monotonic() - started > timeout:
                    raise MediaError("tool_timeout")
                try:
                    child.wait(timeout=0.02)
                    break
                except subprocess.TimeoutExpired:
                    pass
            if stdout.tell() > limit or stderr.tell() > 1024**2:
                raise MediaError("tool_output_limit")
            if child.returncode:
                raise MediaError("tool_failure")
            # Media commands use -v error. A zero exit status must not hide
            # decoder errors and qualify a silently incomplete oracle.
            if stderr.tell():
                raise MediaError("tool_reported_error")
            stdout.seek(0)
            raw = stdout.read(limit + 1)
            returned_bytes = len(raw)
            return raw
        except BaseException as error:
            reason = error.reason if isinstance(error, MediaError) else "interrupted"
            raise
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    child.kill()
            child.wait()
            retain(
                command,
                stderr,
                returncode=child.returncode,
                reason=reason,
                stdout=stdout,
                returned_bytes=returned_bytes,
            )


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024**2):
            digest.update(block)
    return digest.hexdigest()


class MediaManifest(Evidence):
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(gt=0, le=MAX_INPUT_BYTES)
    width: int = Field(gt=0, le=1920)
    height: int = Field(gt=0, le=1080)
    codec: str = Field(max_length=32, pattern=r"^[a-z0-9_]+$")
    duration_s: float = Field(gt=0, le=1802)
    pts_ns: tuple[int, ...] = Field(min_length=2, max_length=MAX_FRAMES)
    oracle_indices: tuple[int, ...] = Field(min_length=2, max_length=8)
    oracle_rgb_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    container_frame_count: int | None = Field(default=None, ge=2, le=MAX_FRAMES)
    oracle_method: Literal[
        "ffprobe_integer_pts_ffmpeg_rgb.v1",
        "ffprobe_count_checked_integer_pts_ffmpeg_rgb.v2",
    ] = "ffprobe_count_checked_integer_pts_ffmpeg_rgb.v2"

    @model_validator(mode="after")
    def coherent(self) -> MediaManifest:
        if self.container_frame_count is not None and self.container_frame_count != len(
            self.pts_ns
        ):
            raise ValueError("oracle frame count differs from reported container count")
        if self.pts_ns[0] != 0 or any(
            b <= a for a, b in zip(self.pts_ns, self.pts_ns[1:], strict=False)
        ):
            raise ValueError("PTS must be strictly ordered and normalized")
        if tuple(
            sorted(set(self.oracle_indices))
        ) != self.oracle_indices or self.oracle_indices[-1] >= len(self.pts_ns):
            raise ValueError("oracle frame indices are invalid")
        if self.pts_ns[-1] >= self.duration_s * 1e9:
            raise ValueError("duration must contain the final frame")
        return self


def generated(directory: Path, recipe: str, ffmpeg: str) -> Path:
    settings = {
        "720p": (1280, 720, 30, 4),
        "1080p": (1920, 1080, 30, 4),
        "timeline30m": (160, 96, 1, 1800),
        "tiny": (160, 96, 5, 2),
    }
    width, height, fps, duration = settings[recipe]
    path = directory / "generated.mp4"
    bounded_tool(
        [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={width}x{height}:rate={fps}:duration={duration}",
            "-an",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-preset",
            "fast",
            "-pix_fmt",
            "yuv420p",
            "-g",
            str(fps * 2),
            "-bf",
            "2",
            "-color_range",
            "tv",
            "-colorspace",
            "smpte170m",
            "-color_primaries",
            "smpte170m",
            "-color_trc",
            "smpte170m",
            str(path),
        ],
        limit=1024,
        timeout=120,
    )
    return path


def inspect(path: Path, tools: dict[str, str]) -> tuple[MediaManifest, np.ndarray]:
    size = path.stat().st_size
    if not 0 < size <= MAX_INPUT_BYTES:
        raise MediaError("input_size")
    before = file_hash(path)
    document = json.loads(
        bounded_tool(
            [
                tools["ffprobe"],
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_streams",
                "-show_frames",
                "-show_entries",
                "stream=codec_name,width,height,time_base,duration_ts,nb_frames:frame=best_effort_timestamp,duration",
                "-of",
                "json",
                str(path),
            ],
            limit=8 * 1024**2,
        )
    )
    stream = document["streams"][0]
    frames = document["frames"]
    if not 2 <= len(frames) <= MAX_FRAMES:
        raise MediaError("input_frames")
    reported = stream.get("nb_frames")
    container_count = None if reported in (None, "N/A") else int(reported)
    if container_count is not None and container_count != len(frames):
        raise MediaError("oracle_frame_count")
    base = Fraction(stream["time_base"])
    timestamps = [
        round(int(f["best_effort_timestamp"]) * base * 1_000_000_000) for f in frames
    ]
    pts = tuple(t - timestamps[0] for t in timestamps)
    if any(b <= a for a, b in zip(pts, pts[1:], strict=False)):
        raise MediaError("input_pts")
    last_duration = round(int(frames[-1].get("duration", 0)) * base * 1_000_000_000)
    if last_duration <= 0:
        last_duration = pts[-1] - pts[-2]
    duration_s = (pts[-1] + last_duration) / 1e9
    width, height = int(stream["width"]), int(stream["height"])
    indices = tuple(sorted({round(i * (len(pts) - 1) / 7) for i in range(8)}))
    # Validate geometry/timeline before requesting any RGB output allocation.
    if not (0 < width <= 1920 and 0 < height <= 1080 and 0 < duration_s <= 1802):
        raise MediaError("input_geometry_or_duration")
    expected_bytes = len(indices) * width * height * 3
    if expected_bytes > MAX_ORACLE_BYTES:
        raise MediaError("oracle_size")
    selection = "+".join(f"eq(n\\,{index})" for index in indices)
    raw = bounded_tool(
        [
            tools["ffmpeg"],
            "-v",
            "error",
            "-nostdin",
            "-i",
            str(path),
            "-an",
            "-vf",
            f"select={selection}",
            "-fps_mode",
            "passthrough",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-threads",
            "1",
            "pipe:1",
        ],
        limit=expected_bytes,
    )
    after = file_hash(path)
    if len(raw) != expected_bytes or after != before:
        raise MediaError(
            "oracle_or_input_changed",
            details={
                "expected_rgb_bytes": expected_bytes,
                "actual_rgb_bytes": len(raw),
                "source_before_sha256": before,
                "source_after_sha256": after,
            },
        )
    manifest = MediaManifest(
        sha256=before,
        bytes=size,
        width=width,
        height=height,
        codec=stream["codec_name"],
        container_frame_count=container_count,
        duration_s=duration_s,
        pts_ns=pts,
        oracle_indices=indices,
        oracle_rgb_sha256=hashlib.sha256(raw).hexdigest(),
    )
    return manifest, np.frombuffer(raw, np.uint8).reshape(
        len(indices), height, width, 3
    )
