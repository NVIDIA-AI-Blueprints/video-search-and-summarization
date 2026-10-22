# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Explicitly enabled acceptance tests against actual installed decoders.

RUN_VSS_CPU_TESTS=1 enables OpenCV tests. RUN_VSS_GPU_TESTS=1 enables
PyNvVideoCodec tests and requires OpenCV plus external FFmpeg for generating a
known H.264 fixture. Opted-in dependencies must exist; missing dependencies fail
rather than quietly skipping validation. Neither mode calls a VLM endpoint.
"""

from __future__ import annotations

import base64
import importlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest


@pytest.fixture(params=["opencv", "pynv"])
def native_case(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    decoder = request.param
    flag = "RUN_VSS_CPU_TESTS" if decoder == "opencv" else "RUN_VSS_GPU_TESTS"
    if os.getenv(flag) != "1":
        pytest.skip(f"set {flag}=1 to validate the installed {decoder} decoder")
    cv2 = importlib.import_module("cv2")
    path = tmp_path / "known_colors.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"MJPG"), 5.0, (64, 48))
    assert writer.isOpened(), "OpenCV fixture writer could not open MJPEG/AVI"
    try:
        for index in range(10):
            # Input is BGR. Public processing output must be RGB.
            color = (10 + index * 2, 30, 180 + index * 5)
            pixels = np.empty((48, 64, 3), dtype=np.uint8)
            pixels[:] = color
            writer.write(pixels)
    finally:
        writer.release()
    if decoder == "pynv":
        importlib.import_module("PyNvVideoCodec")
        ffmpeg = shutil.which("ffmpeg")
        assert ffmpeg, "GPU fixture generation requires caller-installed FFmpeg"
        gpu_path = tmp_path / "known_colors.mp4"
        result = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-nostdin",
                "-y",
                "-i",
                str(path),
                "-c:v",
                os.getenv("VSS_TEST_H264_ENCODER", "libx264"),
                "-pix_fmt",
                "yuv420p",
                "-g",
                "5",
                "-bf",
                "2",
                str(gpu_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        path = gpu_path
    return decoder, path, cv2


async def test_native_probe_matches_known_fixture(
    processing: Any, native_case: Any
) -> None:
    _, path, _ = native_case
    facts = await processing.probe_video(path, source_id="known-video")
    assert facts.source_id == "known-video"
    assert facts.width == 64
    assert facts.height == 48
    assert facts.fps == pytest.approx(5.0, abs=0.01)
    assert facts.duration_s == pytest.approx(2.0, abs=0.2)


async def test_native_sequential_decode_rgb_pts_and_ownership(
    processing: Any,
    native_case: Any,
) -> None:
    decoder, path, _ = native_case
    frames = [
        frame
        async for frame in processing.decode_frames(
            path,
            processing.DecodePlan(),
            source_id="known-video",
            options=processing.DecodeOptions(decoder=decoder, batch_size=2),
        )
    ]
    assert len(frames) == 10
    assert [frame.index for frame in frames] == list(range(10))
    assert [frame.timestamp_ns for frame in frames] == pytest.approx(
        [index * 200_000_000 for index in range(10)],
        abs=2_000_000,
    )
    assert all(frame.source_id == "known-video" for frame in frames)
    first = frames[0].tensor
    assert first.shape == (48, 64, 3)
    assert first.dtype == np.uint8
    assert first.flags.c_contiguous
    red, green, blue = first.mean(axis=(0, 1))
    assert red > green + 100
    assert green > blue
    assert not np.shares_memory(first, frames[-1].tensor)
    assert frames[-1].tensor[..., 0].mean() > first[..., 0].mean() + 20


async def test_native_sparse_extraction_reads_forward_after_seek(
    processing: Any,
    native_case: Any,
) -> None:
    decoder, path, _ = native_case
    frames = await processing.extract_frames(
        path,
        [1.05, 0.25],
        options=processing.DecodeOptions(decoder=decoder),
    )
    assert [frame.requested_timestamp_ns for frame in frames] == [
        1_050_000_000,
        250_000_000,
    ]
    assert [frame.timestamp_ns for frame in frames] == pytest.approx(
        [1_200_000_000, 400_000_000],
        abs=2_000_000,
    )


@pytest.mark.parametrize(
    "format,mime,signature",
    [
        ("png", "image/png", b"\x89PNG\r\n\x1a\n"),
        ("jpeg", "image/jpeg", b"\xff\xd8"),
    ],
)
async def test_native_frame_encoding_preserves_color_and_dimensions(
    processing: Any,
    native_case: Any,
    format: str,
    mime: str,
    signature: bytes,
) -> None:
    decoder, path, cv2 = native_case
    frame = (
        await processing.extract_frames(
            path,
            [0.2],
            options=processing.DecodeOptions(decoder=decoder),
        )
    )[0]
    original = frame.tensor.copy()
    envelope = frame.to_encoded(format=format)
    data = base64.b64decode(envelope.data_base64, validate=True)
    assert data.startswith(signature)
    assert envelope.mime_type == mime
    assert envelope.metadata == frame.metadata
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    assert rgb.shape == frame.tensor.shape
    if format == "png":
        np.testing.assert_array_equal(rgb, original)
    else:
        assert np.abs(rgb.astype(float) - original.astype(float)).mean() < 10
    np.testing.assert_array_equal(frame.tensor, original)
    assert base64.b64decode(frame.to_base64(format=format), validate=True).startswith(
        signature
    )


@pytest.fixture
def native_clip_source(tmp_path: Path) -> Path:
    if os.getenv("RUN_VSS_CLIP_TESTS") != "1":
        pytest.skip("set RUN_VSS_CLIP_TESTS=1 to validate external FFmpeg/ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg and shutil.which("ffprobe"), (
        "Install FFmpeg and ffprobe before opting in"
    )
    source = tmp_path / "video_and_audio.mp4"
    result = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x48:rate=5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100",
            "-t",
            "10",
            "-c:v",
            os.getenv("VSS_TEST_H264_ENCODER", "libx264"),
            "-pix_fmt",
            "yuv420p",
            "-g",
            "10",
            "-keyint_min",
            "10",
            "-sc_threshold",
            "0",
            "-bf",
            "0",
            "-c:a",
            "aac",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return source


async def test_native_clip_is_playable_preserves_audio_and_maps_source_time(
    processing: Any,
    native_clip_source: Path,
) -> None:
    clip = await processing.materialize_clip(
        native_clip_source, 3, 5, source_id="original-video"
    )
    try:
        assert clip.actual_start_ns == pytest.approx(2_000_000_000, abs=200_000_000)
        assert clip.actual_end_ns >= 5_000_000_000
        assert clip.pts_offset_ns == clip.actual_start_ns
        assert clip.source_id == "original-video"
        assert clip.audio is True
        result = subprocess.run(
            [
                str(shutil.which("ffprobe")),
                "-v",
                "error",
                "-show_streams",
                "-of",
                "json",
                str(clip.path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        streams = json.loads(result.stdout)["streams"]
        assert {stream["codec_type"] for stream in streams} >= {"video", "audio"}
        assert (
            next(
                stream["codec_name"]
                for stream in streams
                if stream["codec_type"] == "video"
            )
            == "h264"
        )
    finally:
        clip.cleanup()
    assert not clip.path.exists()
    assert native_clip_source.exists()
