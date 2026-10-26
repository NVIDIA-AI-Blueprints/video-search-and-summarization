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

"""Opt-in functional test for an OpenAI-compatible multimodal VLM."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from vss.ingestion import RequestSource, ingest
from vss.summarization import aggregate_events

pytestmark = [
    pytest.mark.functional,
    pytest.mark.skipif(
        os.getenv("RUN_VLM_FUNCTIONAL") != "1",
        reason="set RUN_VLM_FUNCTIONAL=1 to test a live multimodal VLM",
    ),
]

MODEL = os.getenv("VLM_MODEL", "gcp/google/gemini-3.5-flash-lite")


def _write_test_video(path: Path) -> None:
    import cv2
    import numpy as np

    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter.fourcc(*"mp4v"),
        5.0,
        (320, 240),
    )
    if not writer.isOpened():
        raise RuntimeError("OpenCV could not create the functional-test video")
    try:
        for index in range(20):
            frame = np.zeros((240, 320, 3), dtype=np.uint8)
            cv2.rectangle(
                frame,
                (40 + index * 5, 80),
                (100 + index * 5, 140),
                (0, 0, 255),
                thickness=-1,
            )
            writer.write(frame)
    finally:
        writer.release()


def _transcode_for_gpu(source: Path, destination: Path) -> None:
    """Create the H.264 input expected by the PyNvVideoCodec test path."""

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("GPU VLM functional tests require FFmpeg")
    result = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-c:v",
            os.getenv("VSS_TEST_H264_ENCODER", "libx264"),
            "-profile:v",
            "baseline",
            "-level",
            "3.0",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "10",
            "-bf",
            "0",
            str(destination),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg could not create GPU test input: {result.stderr}")


async def _run_nvidia_multimodal_processing(
    tmp_path: Path,
    *,
    decoder: str,
) -> None:
    if not os.getenv("VLM_API_KEY"):
        pytest.skip("VLM_API_KEY is required")

    source_path = tmp_path / "moving-box-source.mp4"
    _write_test_video(source_path)
    video_path = source_path
    if decoder == "pynv":
        video_path = tmp_path / "moving-box-h264.mp4"
        _transcode_for_gpu(source_path, video_path)
    backend = "vlm"
    results = await ingest(
        RequestSource.for_file(str(video_path)),
        backends=[backend],
        decoder=decoder,
        chunk_duration=30,
        prompt=(
            "Describe the important visible events in this video chunk. "
            "Include timestamps from the supplied frames."
        ),
        model=MODEL,
        generation_config={"max_tokens": 512},
    )
    responses = results[backend]

    assert len(responses) == 1
    assert responses[0].vlm_response
    assert responses[0].rtvi_frame_count > 0
    events = aggregate_events(responses)
    assert events
    assert all(event["description"] for event in events)


async def test_nvidia_multimodal_chat_completions_cpu(tmp_path: Path) -> None:
    await _run_nvidia_multimodal_processing(tmp_path, decoder="opencv")


@pytest.mark.skipif(
    os.getenv("RUN_VSS_GPU_TESTS") != "1",
    reason="set RUN_VSS_GPU_TESTS=1 to test VLM processing with GPU decoding",
)
async def test_nvidia_multimodal_chat_completions_gpu(tmp_path: Path) -> None:
    await _run_nvidia_multimodal_processing(tmp_path, decoder="pynv")
