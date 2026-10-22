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

"""Real VFR and nonzero-origin PTS acceptance, using ffprobe as the oracle.

Enable RUN_VSS_TIMING_TESTS=1 plus RUN_VSS_CPU_TESTS=1 or RUN_VSS_GPU_TESTS=1.
These fixtures also require external FFmpeg/ffprobe and an H.264 encoder. They
compare returned times with the file's actual decoded-frame PTS, not N / FPS.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(params=["opencv", "pynv"])
def timing_decoder(request: pytest.FixtureRequest) -> str:
    flag = "RUN_VSS_CPU_TESTS" if request.param == "opencv" else "RUN_VSS_GPU_TESTS"
    if os.getenv("RUN_VSS_TIMING_TESTS") != "1" or os.getenv(flag) != "1":
        pytest.skip(
            f"set RUN_VSS_TIMING_TESTS=1 and {flag}=1 for native PTS acceptance"
        )
    return request.param


@pytest.fixture(params=["vfr", "nonzero_origin", "bframes"])
def timing_video(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    assert ffmpeg and ffprobe, "Native timing acceptance requires FFmpeg/ffprobe"
    path = tmp_path / f"{request.param}.mp4"
    command = [
        ffmpeg,
        "-v",
        "error",
        "-nostdin",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=64x48:rate=5:duration=2",
        "-an",
    ]
    if request.param == "vfr":
        command += [
            "-vf",
            r"setpts=if(lt(N\,3)\,N/(5*TB)\,(N+2)/(5*TB))",
            "-fps_mode",
            "vfr",
        ]
    command += [
        "-c:v",
        os.getenv("VSS_TEST_H264_ENCODER", "libx264"),
        "-pix_fmt",
        "yuv420p",
        "-g",
        "5",
        "-bf",
        "2",
    ]
    if request.param == "nonzero_origin":
        command += ["-output_ts_offset", "5"]
    command.append(str(path))
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_frames",
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    timestamps = [
        round(float(frame["best_effort_timestamp_time"]) * 1_000_000_000)
        for frame in json.loads(result.stdout)["frames"]
    ]
    assert len(timestamps) == 10
    assert timestamps == sorted(timestamps)
    if request.param == "vfr":
        deltas = {
            end - start for start, end in zip(timestamps, timestamps[1:], strict=False)
        }
        assert len(deltas) > 1, "Fixture generation unexpectedly produced CFR video"
    if request.param == "nonzero_origin":
        assert timestamps[0] >= 5_000_000_000
    return path, timestamps


async def test_native_pts_and_origin_match_independent_oracle(
    processing: Any,
    timing_decoder: str,
    timing_video: Any,
) -> None:
    path, raw_timestamps = timing_video
    normalized = [timestamp - raw_timestamps[0] for timestamp in raw_timestamps]
    frames = [
        frame
        async for frame in processing.decode_frames(
            path,
            processing.DecodePlan(),
            options=processing.DecodeOptions(decoder=timing_decoder),
        )
    ]
    assert len(frames) == len(normalized)
    assert [frame.timestamp_ns for frame in frames] == pytest.approx(
        normalized, abs=2_000_000
    )
    assert all(frame.timestamp_quality != "estimated" for frame in frames)


async def test_native_time_based_extraction_uses_pts_not_average_fps(
    processing: Any,
    timing_decoder: str,
    timing_video: Any,
) -> None:
    path, raw_timestamps = timing_video
    normalized = [timestamp - raw_timestamps[0] for timestamp in raw_timestamps]
    targets = [50_000_000, 550_000_000, 1_050_000_000]
    expected = [
        next(timestamp for timestamp in normalized if timestamp >= target)
        for target in targets
    ]
    frames = await processing.extract_frames(
        path,
        [target / 1_000_000_000 for target in targets],
        options=processing.DecodeOptions(decoder=timing_decoder),
    )
    assert [frame.timestamp_ns for frame in frames] == pytest.approx(
        expected, abs=2_000_000
    )
    assert [frame.requested_timestamp_ns for frame in frames] == targets


async def test_cpu_probe_does_not_invent_raw_pts_origin(
    processing: Any, timing_decoder: str, timing_video: Any
) -> None:
    if timing_decoder != "opencv":
        pytest.skip("OpenCV metadata contract")
    path, _ = timing_video
    facts = await processing.probe_video(path)
    assert facts.timestamp_origin_ns is None
    assert any("origin" in warning.lower() for warning in facts.warnings)


@pytest.fixture
def long_gap_video(tmp_path: Path) -> Any:
    path = tmp_path / "late-vfr-target.mp4"
    result = subprocess.run(
        [
            str(shutil.which("ffmpeg")),
            "-v",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x48:rate=10:duration=4",
            "-vf",
            r"setpts=if(lt(N\,20)\,N/(10*TB)\,(N+30)/(10*TB))",
            "-fps_mode",
            "vfr",
            "-c:v",
            os.getenv("VSS_TEST_H264_ENCODER", "libx264"),
            "-g",
            "10",
            "-bf",
            "2",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run(
        [
            str(shutil.which("ffprobe")),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_frames",
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    pts = [
        round(float(f["best_effort_timestamp_time"]) * 1e9)
        for f in json.loads(result.stdout)["frames"]
    ]
    assert len(pts) == 40 and pts[20] - pts[19] > 3_000_000_000
    return path, [timestamp - pts[0] for timestamp in pts]


@pytest.mark.parametrize("target_ns", [4_500_000_000, 5_050_000_000, 6_500_000_000])
async def test_native_late_first_vfr_target_preserves_first_eligible_frame_and_index(
    processing: Any,
    timing_decoder: str,
    long_gap_video: Any,
    target_ns: int,
) -> None:
    path, pts = long_gap_video
    expected_index = next(
        index for index, timestamp in enumerate(pts) if timestamp >= target_ns
    )
    frame = (
        await processing.extract_frames(
            path,
            [target_ns / 1e9],
            options=processing.DecodeOptions(decoder=timing_decoder),
        )
    )[0]
    assert frame.timestamp_ns == pytest.approx(pts[expected_index], abs=2_000_000)
    assert frame.index == expected_index


async def test_native_late_vfr_window_preserves_all_in_window_frames_and_indices(
    processing: Any,
    timing_decoder: str,
    long_gap_video: Any,
) -> None:
    path, pts = long_gap_video
    start, end = 4_500_000_000, 5_150_000_000
    expected = [
        (index, timestamp)
        for index, timestamp in enumerate(pts)
        if start <= timestamp < end
    ]
    frames = [
        frame
        async for frame in processing.decode_frames(
            path,
            processing.DecodePlan(
                window=processing.TimeWindow(start_ns=start, end_ns=end)
            ),
            options=processing.DecodeOptions(decoder=timing_decoder),
        )
    ]
    assert [frame.timestamp_ns for frame in frames] == pytest.approx(
        [timestamp for _, timestamp in expected], abs=2_000_000
    )
    assert [frame.index for frame in frames] == [index for index, _ in expected]


@pytest.fixture
def clip_timing_enabled() -> None:
    if (
        os.getenv("RUN_VSS_CLIP_TESTS") != "1"
        or os.getenv("RUN_VSS_TIMING_TESTS") != "1"
    ):
        pytest.skip("set RUN_VSS_CLIP_TESTS=1 and RUN_VSS_TIMING_TESTS=1")


async def test_native_clip_decoded_times_map_back_to_source_oracle(
    processing: Any,
    clip_timing_enabled: None,
    timing_video: Any,
) -> None:
    """Verify playable clip contents, not only copied packet metadata."""
    path, raw_timestamps = timing_video
    normalized = [timestamp - raw_timestamps[0] for timestamp in raw_timestamps]
    # Start between frames after the second keyframe, and end before source EOF.
    start = (normalized[6] + 50_000_000) / 1_000_000_000
    end = (normalized[8] + 50_000_000) / 1_000_000_000
    artifact = await processing.materialize_clip(path, start, end, source_id="original")
    try:
        assert artifact.source_id == "original"
        assert 0 < artifact.actual_start_ns <= round(start * 1_000_000_000)
        assert artifact.actual_end_ns >= round(end * 1_000_000_000)
        result = subprocess.run(
            [
                str(shutil.which("ffprobe")),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_frames",
                "-show_entries",
                "frame=best_effort_timestamp_time",
                "-of",
                "json",
                str(artifact.path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        clip_pts = [
            round(float(frame["best_effort_timestamp_time"]) * 1_000_000_000)
            for frame in json.loads(result.stdout)["frames"]
        ]
        assert clip_pts, "Verified clip must also decode into presentation frames"
        mapped_pts = [
            timestamp - clip_pts[0] + artifact.pts_offset_ns for timestamp in clip_pts
        ]
        expected = [
            timestamp
            for timestamp in normalized
            if artifact.actual_start_ns - 2_000_000
            <= timestamp
            < artifact.actual_end_ns - 2_000_000
        ]
        assert mapped_pts == pytest.approx(expected, abs=2_000_000)
    finally:
        artifact.cleanup()
    assert not artifact.path.exists()
    assert path.exists()


@pytest.mark.parametrize("origin_s", [0, 5])
@pytest.mark.parametrize("start,end,key_index", [(3, 5, 0), (12, 14, 250)])
async def test_native_long_gop_bframes_clip_keeps_contiguous_video_and_audio(
    processing: Any,
    clip_timing_enabled: None,
    tmp_path: Path,
    origin_s: int,
    start: int,
    end: int,
    key_index: int,
) -> None:
    """A DTS-limited copy must not leak a future P-frame past the GOP end."""
    source = tmp_path / "long-gop-audio.mp4"
    result = subprocess.run(
        [
            str(shutil.which("ffmpeg")),
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x48:rate=30000/1001:duration=20",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=20",
            "-c:v",
            os.getenv("VSS_TEST_H264_ENCODER", "libx264"),
            "-g",
            "250",
            "-keyint_min",
            "250",
            "-sc_threshold",
            "0",
            "-bf",
            "2",
            "-c:a",
            "aac",
            "-output_ts_offset",
            str(origin_s),
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    artifact = await processing.materialize_clip(
        source, start, end, source_id="original"
    )
    try:
        assert artifact.audio is True
        assert artifact.actual_start_ns == pytest.approx(
            key_index * 1001 / 30000 * 1e9, abs=2000
        )
        assert artifact.actual_end_ns == pytest.approx(
            (key_index + 250) * 1001 / 30000 * 1e9, abs=2000
        )
        result = subprocess.run(
            [
                str(shutil.which("ffmpeg")),
                "-nostdin",
                "-v",
                "error",
                "-xerror",
                "-i",
                str(artifact.path),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
    finally:
        artifact.cleanup()
    assert not artifact.path.exists()
    assert source.exists()
