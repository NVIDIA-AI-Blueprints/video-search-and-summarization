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

"""Opt-in functional tests for a live RTVI service."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from vss.ingestion import (
    ChunkInfo,
    RequestSource,
    VlmChunkResponse,
    ingest,
    ingest_sync,
)
from vss.summarization import (
    GeneratedSummary,
    SummaryGenerator,
    summarize_video,
    summarize_video_sync,
)

pytestmark = [
    pytest.mark.functional,
    pytest.mark.skipif(
        os.getenv("RUN_RTVI_FUNCTIONAL") != "1",
        reason="set RUN_RTVI_FUNCTIONAL=1 to test a live RTVI service",
    ),
]

RTVI_MODEL = os.getenv(
    "RTVI_MODEL",
    "nim_nvidia_cosmos3-nano-reasoner_bf16-final",
)


class _DeterministicGenerator(SummaryGenerator):
    async def generate(
        self,
        events: list[dict[str, Any]],
        *,
        prompt: str | None = None,
        model: str | None = None,
    ) -> GeneratedSummary:
        return GeneratedSummary(
            text=f"RTVI produced {len(events)} summarized events.",
            model="functional-test",
        )


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
            color = (0, 0, 255) if index < 10 else (0, 255, 0)
            cv2.rectangle(
                frame,
                (40 + index * 5, 80),
                (100 + index * 5, 140),
                color,
                -1,
            )
            cv2.putText(
                frame,
                "MOVING BOX",
                (65, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2,
            )
            writer.write(frame)
    finally:
        writer.release()


async def test_uploaded_video_produces_existing_chunk_responses(
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "vss-functional-test.mp4"
    _write_test_video(video_path)

    results = await ingest(
        RequestSource.for_file(str(video_path)),
        backends=["rtvi"],
        model=RTVI_MODEL,
        prompt=("Briefly describe the visible object, its color, and motion."),
        chunk_duration=4,
    )
    responses = results["rtvi"]

    assert responses
    assert all(isinstance(response, VlmChunkResponse) for response in responses)
    assert all(response.vlm_response for response in responses)
    assert all(response.chunk is not None for response in responses)
    first_chunk = responses[0].chunk
    assert first_chunk is not None
    asset_id = first_chunk.sourceId
    assert all(
        response.chunk.sourceId == asset_id
        for response in responses
        if response.chunk is not None
    )
    assert ChunkInfo.model_validate_json(first_chunk.model_dump_json()) == first_chunk

    completion = await summarize_video(
        responses,
        generator=_DeterministicGenerator(),
    )
    payload = json.loads(completion.choices[0].message.content or "")
    assert payload["events"]
    assert payload["video_summary"].startswith("RTVI produced ")
    assert completion.video_id == UUID(asset_id)


def test_sync_ingest_and_summarize(tmp_path: Path) -> None:
    video_path = tmp_path / "vss-sync-functional-test.mp4"
    _write_test_video(video_path)

    results = ingest_sync(
        RequestSource.for_file(str(video_path)),
        backends=["rtvi"],
        model=RTVI_MODEL,
        prompt="Briefly describe the visible object, its color, and motion.",
        chunk_duration=4,
    )
    responses = results["rtvi"]
    completion = summarize_video_sync(
        responses,
        generator=_DeterministicGenerator(),
    )
    payload = json.loads(completion.choices[0].message.content)

    assert responses
    assert all(isinstance(response, VlmChunkResponse) for response in responses)
    assert payload["events"]
    assert payload["total_events"] == len(payload["events"])
    assert payload["video_summary"] == (
        f"RTVI produced {payload['total_events']} summarized events."
    )
