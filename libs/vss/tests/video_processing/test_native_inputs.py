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

"""Opt-in input acceptance, independent of private demo media.

RUN_VSS_INPUT_TESTS=1 requires caller-installed OpenCV/FFmpeg/ffprobe.
VSS_INPUT_VIDEO optionally derives fixtures from a real video instead of testsrc2.
These CPU checks do not establish GPU, HDR, or every codec/container support.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest


class ColorConversionMismatch(AssertionError):
    """A decoded RGB frame differs materially from the independent oracle."""


def _known_broken_color_build(version: str, system: str, build: str) -> bool:
    """Match the evidenced Mac binary; another build uses ordinary assertions."""
    if version != "4.13.0" or system != "Darwin":
        return False
    expected = {
        "avcodec": "61.19.101",
        "avformat": "61.7.100",
        "avutil": "59.39.100",
        "swscale": "8.3.100",
    }
    actual = dict(
        re.findall(r"(avcodec|avformat|avutil|swscale):\s+YES\s+\(([^)]+)\)", build)
    )
    return actual == expected


@pytest.fixture
def known_color_build(request: pytest.FixtureRequest) -> None:
    # Native imports happen only during explicitly enabled execution.
    if (
        os.getenv("RUN_VSS_INPUT_TESTS") != "1"
        or os.getenv("RUN_VSS_COLOR_LIMIT_TESTS") != "1"
    ):
        return
    import cv2

    if _known_broken_color_build(
        cv2.__version__, platform.system(), cv2.getBuildInformation()
    ):
        request.applymarker(
            pytest.mark.xfail(
                strict=True,
                raises=ColorConversionMismatch,
                reason="Documented OpenCV 4.13.0 Mac FFmpeg build mis-converts SDR RGB",
            )
        )


def run_tool(name: str, *args: str) -> bytes:
    executable = shutil.which(name)
    assert executable, f"Input acceptance requires caller-installed {name}"
    result = subprocess.run(
        [executable, "-v", "error", *args],
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result.stdout


def inspect(path: Path) -> dict[str, Any]:
    return json.loads(
        run_tool("ffprobe", "-show_streams", "-show_format", "-of", "json", str(path))
    )


@pytest.fixture(scope="module")
def input_source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if os.getenv("RUN_VSS_INPUT_TESTS") != "1":
        pytest.skip("set RUN_VSS_INPUT_TESTS=1 for native input acceptance")
    source = tmp_path_factory.mktemp("native-inputs") / "base.mp4"
    real_source = os.getenv("VSS_INPUT_VIDEO")
    inputs = (
        ["-i", str(Path(real_source).resolve())]
        if real_source
        else ["-f", "lavfi", "-i", "testsrc2=size=160x90:rate=12"]
    )
    run_tool(
        "ffmpeg",
        "-nostdin",
        "-y",
        *inputs,
        "-t",
        "2",
        "-an",
        "-vf",
        "scale=160:90:in_range=auto:out_range=tv:out_color_matrix=bt601,fps=12",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-color_range",
        "tv",
        "-colorspace",
        "smpte170m",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-g",
        "12",
        "-bf",
        "2",
        str(source),
    )
    return source


async def check_video(processing: Any, path: Path, width: int, height: int) -> None:
    facts = await processing.probe_video(path, source_id="original")
    assert (facts.width, facts.height) == (width, height)
    frames = [
        frame
        async for frame in processing.decode_frames(
            path,
            processing.DecodePlan(),
            source_id="original",
            options=processing.DecodeOptions(decoder="opencv", batch_size=3),
        )
    ]
    assert len(frames) == 24
    assert all(frame.source_id == "original" for frame in frames)
    assert all(frame.tensor.shape == (height, width, 3) for frame in frames)
    assert all(not frame.tensor.flags.writeable for frame in frames)
    assert [frame.timestamp_ns for frame in frames] == pytest.approx(
        [index * 1e9 / 12 for index in range(24)], abs=1_000_000
    )
    # Independent decoder oracle; no display-matrix autorotation is requested.
    oracle = run_tool(
        "ffmpeg",
        "-xerror",
        "-noautorotate",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    )
    expected = np.frombuffer(oracle, dtype=np.uint8).reshape(height, width, 3)
    error = float(
        np.abs(frames[0].tensor.astype(float) - expected.astype(float)).mean()
    )
    if error >= 3:
        raise ColorConversionMismatch(f"mean absolute RGB error {error:.3f} >= 3")


@pytest.mark.parametrize(
    "codec,suffix,encoder_args",
    [
        ("h264", ".mp4", ["-c:v", "copy"]),
        ("hevc", ".mp4", ["-c:v", "libx265", "-preset", "ultrafast"]),
        ("vp9", ".webm", ["-c:v", "libvpx-vp9", "-deadline", "realtime"]),
    ],
)
async def test_native_alternate_codec_container(
    processing: Any,
    input_source: Path,
    tmp_path: Path,
    codec: str,
    suffix: str,
    encoder_args: list[str],
) -> None:
    output = tmp_path / f"variant{suffix}"
    run_tool("ffmpeg", "-y", "-i", str(input_source), *encoder_args, str(output))
    stream = inspect(output)["streams"][0]
    assert stream["codec_name"] == codec
    assert stream["pix_fmt"] == "yuv420p"
    assert stream["color_range"] == "tv"
    assert stream["color_space"] == "smpte170m"
    await check_video(processing, output, 160, 90)


@pytest.mark.usefixtures("known_color_build")
async def test_native_known_full_range_vp9_color_limitation(
    processing: Any,
    input_source: Path,
    tmp_path: Path,
) -> None:
    if os.getenv("RUN_VSS_COLOR_LIMIT_TESTS") != "1":
        pytest.skip(
            "set RUN_VSS_COLOR_LIMIT_TESTS=1 to reproduce the known color limitation"
        )
    output = tmp_path / "full-range.webm"
    run_tool(
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=0x505050:size=160x90:rate=12",
        "-t",
        "2",
        "-vf",
        "scale=in_range=tv:out_range=pc",
        "-c:v",
        "libvpx-vp9",
        "-deadline",
        "realtime",
        "-color_range",
        "pc",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        str(output),
    )
    stream = inspect(output)["streams"][0]
    assert stream["pix_fmt"] == "yuv420p" and stream["color_range"] == "pc"
    await check_video(processing, output, 160, 90)


@pytest.mark.usefixtures("known_color_build")
async def test_native_known_bt709_color_limitation(
    processing: Any,
    input_source: Path,
    tmp_path: Path,
) -> None:
    if os.getenv("RUN_VSS_COLOR_LIMIT_TESTS") != "1":
        pytest.skip("set RUN_VSS_COLOR_LIMIT_TESTS=1 for native color regressions")
    output = tmp_path / "bt709.mp4"
    run_tool(
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=160x90:rate=12",
        "-t",
        "2",
        "-vf",
        "scale=out_color_matrix=bt709:out_range=tv",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-color_range",
        "tv",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        str(output),
    )
    stream = inspect(output)["streams"][0]
    assert stream["color_space"] == "bt709" and stream["color_range"] == "tv"
    await check_video(processing, output, 160, 90)


async def test_native_portrait(
    processing: Any, input_source: Path, tmp_path: Path
) -> None:
    output = tmp_path / "portrait.mp4"
    run_tool(
        "ffmpeg",
        "-y",
        "-i",
        str(input_source),
        "-vf",
        "transpose=1",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(output),
    )
    await check_video(processing, output, 90, 160)


async def test_native_rotation_keeps_coded_geometry(
    processing: Any,
    input_source: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "rotation.mp4"
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg, "Input acceptance requires caller-installed ffmpeg"
    result = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-display_rotation:v:0",
            "90",
            "-i",
            str(input_source),
            "-c",
            "copy",
            str(output),
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    diagnostic = result.stderr.decode(errors="replace")
    if result.returncode and "Unrecognized option 'display_rotation" in diagnostic:
        result = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-y",
                "-i",
                str(input_source),
                "-c",
                "copy",
                "-metadata:s:v:0",
                "rotate=90",
                str(output),
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
        diagnostic = result.stderr.decode(errors="replace")
        if result.returncode:
            if os.getenv("VSS_REQUIRE_ROTATION_TESTS") == "1":
                pytest.fail(f"FFmpeg cannot create a rotated fixture: {diagnostic}")
            pytest.skip("installed FFmpeg cannot create a rotated fixture")
    assert result.returncode == 0, diagnostic
    video = inspect(output)["streams"][0]
    has_rotation = any(
        abs(item.get("rotation", 0)) == 90 for item in video.get("side_data_list", ())
    )
    if not has_rotation:
        if os.getenv("VSS_REQUIRE_ROTATION_TESTS") == "1":
            pytest.fail("FFmpeg fixture contains no 90-degree display rotation")
        pytest.skip("installed FFmpeg did not preserve display rotation metadata")
    await check_video(processing, output, 160, 90)


@pytest.mark.parametrize("damage", ["corrupt", "missing_mp4_index"])
async def test_native_unreadable_input_fails_and_session_limit_recovers(
    processing: Any,
    input_source: Path,
    tmp_path: Path,
    damage: str,
) -> None:
    output = tmp_path / "damaged.mp4"
    original = input_source.read_bytes()
    if damage == "corrupt":
        output.write_bytes(b"not a media stream" * 100)
    else:
        # The MP4 index is at EOF; removing it is a deterministic unreadable file.
        moov = original.rfind(b"moov")
        assert moov > 4
        output.write_bytes(original[: moov - 4])
    options = processing.DecodeOptions(decoder="opencv", max_concurrency=1)
    async with asyncio.timeout(10):
        with pytest.raises(processing.DecodeError):
            _ = [
                frame
                async for frame in processing.decode_frames(
                    output,
                    processing.DecodePlan(),
                    options=options,
                )
            ]
        frames = await processing.extract_frames(input_source, [0], options=options)
    assert len(frames) == 1
    assert input_source.read_bytes() == original


@pytest.mark.parametrize("delayed_stream", ["audio", "video"])
async def test_native_stream_start_offsets_clip_and_source_clock(
    processing: Any,
    input_source: Path,
    tmp_path: Path,
    delayed_stream: str,
) -> None:
    output = tmp_path / "offset.mp4"
    run_tool(
        "ffmpeg",
        "-y",
        *(["-itsoffset", "0.4"] if delayed_stream == "video" else []),
        "-i",
        str(input_source),
        *(["-itsoffset", "0.4"] if delayed_stream == "audio" else []),
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=2",
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        str(output),
    )
    streams = inspect(output)["streams"]
    video_start = float(
        next(s["start_time"] for s in streams if s["codec_type"] == "video")
    )
    audio_start = float(
        next(s["start_time"] for s in streams if s["codec_type"] == "audio")
    )
    assert abs(video_start - audio_start) > 0.3
    frame = (
        await processing.extract_frames(
            output, [0], options=processing.DecodeOptions(decoder="opencv")
        )
    )[0]
    assert frame.timestamp_ns == pytest.approx(0, abs=1_000_000)
    clip = await processing.materialize_clip(output, 0.2, 0.7, source_id="original")
    try:
        assert clip.source_id == "original" and clip.audio is True
        assert clip.actual_start_ns <= 200_000_000
        assert clip.actual_end_ns >= 700_000_000

        def frame_times(path: Path) -> list[int]:
            doc = json.loads(
                run_tool(
                    "ffprobe",
                    "-select_streams",
                    "v:0",
                    "-show_frames",
                    "-show_entries",
                    "frame=best_effort_timestamp_time",
                    "-of",
                    "json",
                    str(path),
                )
            )
            return [
                round(float(frame["best_effort_timestamp_time"]) * 1e9)
                for frame in doc["frames"]
            ]

        source_pts = frame_times(output)
        clip_pts = frame_times(clip.path)
        expected_pts = [
            pts - source_pts[0]
            for pts in source_pts
            if clip.actual_start_ns <= pts - source_pts[0] < clip.actual_end_ns
        ]
        assert [
            pts - clip_pts[0] + clip.pts_offset_ns for pts in clip_pts
        ] == pytest.approx(expected_pts, abs=2_000)
        clip_streams = inspect(clip.path)["streams"]
        if delayed_stream == "audio":
            copied_audio = next(s for s in clip_streams if s["codec_type"] == "audio")
            copied_video = next(s for s in clip_streams if s["codec_type"] == "video")
            assert float(copied_audio["start_time"]) - float(
                copied_video["start_time"]
            ) == pytest.approx(audio_start - video_start, abs=0.03)
        run_tool("ffmpeg", "-xerror", "-i", str(clip.path), "-f", "null", "-")
    finally:
        clip.cleanup()
    assert not clip.path.exists() and output.exists()
