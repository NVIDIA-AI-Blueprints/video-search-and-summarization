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

"""Opt-in GPU RGB and packet-PTS association against independent native oracles."""

from __future__ import annotations

import json
import os
import platform
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from .test_native_inputs import run_tool


@pytest.mark.parametrize(
    "encoder,codec,container,matrix,range_name",
    [
        ("libx264", "h264", "mp4", "smpte170m", "tv"),
        ("libx264", "h264", "mp4", "bt709", "tv"),
        ("libx265", "hevc", "mp4", "bt709", "tv"),
        ("libvpx-vp9", "vp9", "webm", "bt709", "tv"),
        ("libvpx-vp9", "vp9", "webm", "bt709", "pc"),
        ("libaom-av1", "av1", "mkv", "bt709", "tv"),
    ],
)
async def test_gpu_codec_color_identity_or_hardware_availability(
    processing: Any,
    tmp_path: Path,
    encoder: str,
    codec: str,
    container: str,
    matrix: str,
    range_name: str,
) -> None:
    if os.getenv("RUN_VSS_GPU_TESTS") != "1" or os.getenv("RUN_VSS_INPUT_TESTS") != "1":
        pytest.skip("set RUN_VSS_GPU_TESTS=1 RUN_VSS_INPUT_TESTS=1 for GPU RGB oracles")
    path = tmp_path / f"{codec}-{matrix}-{range_name}.{container}"
    extra = ["-deadline", "realtime"] if encoder == "libvpx-vp9" else []
    if encoder == "libaom-av1":
        extra = ["-cpu-used", "8"]
    if encoder == "libx265":
        extra = ["-x265-params", "pools=2:frame-threads=2:log-level=error"]
    run_tool(
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x192:rate=12",
        "-t",
        "1",
        "-vf",
        f"scale=out_color_matrix={matrix}:out_range={range_name}",
        "-c:v",
        encoder,
        *extra,
        "-pix_fmt",
        "yuv420p",
        "-color_range",
        range_name,
        "-colorspace",
        matrix,
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        str(path),
    )
    facts = json.loads(
        run_tool(
            "ffprobe",
            "-select_streams",
            "v:0",
            "-show_frames",
            "-show_streams",
            "-of",
            "json",
            str(path),
        )
    )
    stream = facts["streams"][0]
    assert stream["codec_name"] == codec
    assert stream["color_space"] == matrix and stream["color_range"] == range_name
    pts = [int(row["best_effort_timestamp"]) for row in facts["frames"]]
    timebase = Fraction(stream["time_base"])
    expected = [round((tick - pts[0]) * timebase * 1_000_000_000) for tick in pts]
    # AV1 is absent on H100. Determine that from hardware capabilities before
    # assertions, preserving ordinary RGB assertions on a capable runner.
    import PyNvVideoCodec as nvc

    demux = nvc.CreateDemuxer(filename=str(path))
    caps = nvc.GetDecoderCaps(gpuid=0, codec=demux.GetNvCodecId(), bitdepth=8)
    demux = None
    if not caps["supported"]:
        with pytest.raises(processing.DecoderUnavailableError):
            await processing.extract_frames(
                path, [0], options=processing.DecodeOptions(decoder="pynv")
            )
        # Hardware rejection alone does not certify the CPU codec path.
        return
    guarded = (
        nvc.__version__ == "2.2.3"
        and platform.system() == "Linux"
        and platform.machine() == "x86_64"
        and codec == "vp9"
        and matrix == "bt709"
        and range_name == "tv"
    )
    if guarded:
        with pytest.raises(processing.DecoderUnavailableError, match="color"):
            await processing.extract_frames(
                path, [0], options=processing.DecodeOptions(decoder="pynv")
            )
    selected = "auto" if guarded else "pynv"
    frames = [
        f
        async for f in processing.decode_frames(
            path,
            processing.DecodePlan(),
            options=processing.DecodeOptions(decoder=selected, batch_size=3),
        )
    ]
    assert len(frames) == len(pts) == 12
    assert [f.index for f in frames] == list(range(12))
    assert [f.timestamp_ns for f in frames] == pytest.approx(expected, abs=1)
    expected_decoder, expected_device = (
        ("opencv", "cpu") if guarded else ("pynv", "gpu")
    )
    assert all(
        f.decoder == expected_decoder and f.device == expected_device for f in frames
    )
    if guarded:
        assert all(f.fallback_reason and "color" in f.fallback_reason for f in frames)
    rgb = np.frombuffer(
        run_tool(
            "ffmpeg",
            "-xerror",
            "-noautorotate",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-fps_mode",
            "passthrough",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ),
        np.uint8,
    ).reshape((12, 192, 320, 3))
    for frame, oracle in zip(frames, rgb, strict=True):
        assert frame.tensor.shape == oracle.shape
        assert not frame.tensor.flags.writeable
        mae = np.abs(frame.tensor.astype(float) - oracle.astype(float)).mean()
        assert mae < 5.0, (codec, matrix, range_name, frame.index, mae)
