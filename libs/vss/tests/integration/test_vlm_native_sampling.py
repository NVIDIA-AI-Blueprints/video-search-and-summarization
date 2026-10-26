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

"""Real OpenCV regressions for generated VLM sampling grids."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

import vss.ingestion.vlm as vlm_module
from vss.ingestion import (
    ChunkInfo,
    RequestSource,
    VlmChunkResponse,
    ingest,
)
from vss.ingestion.vlm import VLMBackend, VLMFrameChunk

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_VSS_CPU_TESTS") != "1",
    reason="set RUN_VSS_CPU_TESTS=1 to run real OpenCV VLM sampling regressions",
)


def _write_video(path: Path, *, duration_s: int) -> None:
    import cv2
    import numpy as np

    fps = 5
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter.fourcc(*"mp4v"),
        fps,
        (160, 90),
    )
    if not writer.isOpened():
        raise RuntimeError("OpenCV could not create the sampling fixture")
    try:
        for index in range(duration_s * fps):
            frame = np.zeros((90, 160, 3), dtype=np.uint8)
            frame[:, :, index % 3] = 255
            writer.write(frame)
    finally:
        writer.release()


async def _ingest_with_recording_backend(
    path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> list[VLMFrameChunk]:
    chunks: list[VLMFrameChunk] = []

    class RecordingBackend(VLMBackend):
        async def infer(
            self,
            chunk: VLMFrameChunk,
            *,
            prompt: str,
            model: str | None = None,
            generation_config: Mapping[str, Any] | None = None,
        ) -> VlmChunkResponse:
            del prompt, model, generation_config
            chunks.append(chunk)
            return VlmChunkResponse(
                chunk=ChunkInfo(
                    sourceId=chunk.source.source_id,
                    chunkIdx=chunk.chunk_idx,
                ),
                vlm_response="recorded",
            )

        async def cancel(self, source_id: str) -> bool:
            del source_id
            return False

        async def check_health(self) -> bool:
            return True

        def capability_map(self) -> Mapping[str, Any]:
            return {"backend": "vlm"}

    monkeypatch.setitem(
        vlm_module._VLM_BACKEND_REGISTRY,
        "vlm",
        RecordingBackend,
    )
    await ingest(
        RequestSource.for_file(str(path)),
        backends=["vlm"],
        decoder="opencv",
        chunk_duration=30,
    )
    return chunks


async def test_one_second_video_allows_unavailable_final_sample(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "one-second.mp4"
    _write_video(path, duration_s=1)

    chunks = await _ingest_with_recording_backend(path, monkeypatch)

    assert [chunk.chunk_idx for chunk in chunks] == [0]
    assert chunks[0].frames
    assert max(frame.timestamp_ns for frame in chunks[0].frames) == pytest.approx(
        800_000_000,
        abs=10_000_000,
    )


async def test_thirty_one_second_video_keeps_partial_final_chunk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "thirty-one-seconds.mp4"
    _write_video(path, duration_s=31)

    chunks = await _ingest_with_recording_backend(path, monkeypatch)

    assert [chunk.chunk_idx for chunk in chunks] == [0, 1]
    assert chunks[-1].start_s == 30
    assert chunks[-1].end_s == pytest.approx(31, abs=0.01)
    assert min(frame.timestamp_ns for frame in chunks[-1].frames) >= 30_000_000_000
    assert max(frame.timestamp_ns for frame in chunks[-1].frames) == pytest.approx(
        30_800_000_000,
        abs=10_000_000,
    )
