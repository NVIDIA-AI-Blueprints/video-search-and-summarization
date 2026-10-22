# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Opt-in live ingest-to-summary validation for both VLM backends."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from vss.ingestion import (
    RequestSource,
    VlmChunkResponse,
    ingest,
)
from vss.summarization import CompletionResponse, summarize_video

pytestmark = pytest.mark.functional

NVIDIA_VLM_BASE_URL = "https://inference-api.nvidia.com/v1"
NVIDIA_VLM_MODEL = "gcp/google/gemini-3.5-flash-lite"
RTVI_MODEL = "nim_nvidia_cosmos3-nano-reasoner_bf16-final"


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
        raise RuntimeError("OpenCV could not create the integration-test video")
    try:
        for index in range(20):
            frame = np.zeros((240, 320, 3), dtype=np.uint8)
            color = (0, 0, 255) if index < 10 else (0, 255, 0)
            cv2.rectangle(
                frame,
                (40 + index * 5, 80),
                (100 + index * 5, 140),
                color,
                thickness=-1,
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


def _require_summary_configuration() -> None:
    required = (
        "VSS_SUMMARIZATION_BASE_URL",
        "VSS_SUMMARIZATION_MODEL",
        "VSS_SUMMARIZATION_API_KEY",
    )
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        pytest.skip(f"missing live summarization settings: {', '.join(missing)}")


async def _run_e2e(
    video_path: Path,
    pipeline: str,
    *,
    decoder: str | None = None,
    model: str | None = None,
) -> None:
    source = RequestSource.for_file(str(video_path))
    options: dict[str, Any] = {
        "model": model,
        "chunk_duration": 4,
        "prompt": (
            "Return JSON events describing the visible object's color and motion "
            "with start_time, end_time, type, and description."
        ),
        "generation_config": {"max_tokens": 512},
    }
    if decoder is not None:
        options["decoder"] = decoder
    chunks = (await ingest(source, backends=[pipeline], **options))[pipeline]

    assert chunks
    assert all(isinstance(chunk, VlmChunkResponse) for chunk in chunks)
    assert all(chunk.vlm_response for chunk in chunks)
    assert chunks[-1].chunk is not None
    assert chunks[-1].chunk.is_last

    completion = await summarize_video(chunks)
    assert isinstance(completion, CompletionResponse)
    payload = json.loads(completion.choices[0].message.content)
    assert payload["events"]
    assert payload["total_events"] == len(payload["events"])
    assert payload["video_summary"]
    assert completion.usage is not None
    assert completion.usage.total_chunks_processed == len(chunks)
    assert completion.usage.summary_requests == 1


@pytest.mark.skipif(
    os.getenv("RUN_VLM_E2E") != "1",
    reason="set RUN_VLM_E2E=1 to run the live generic VLM pipeline",
)
async def test_vlm_ingestion_and_summarization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_summary_configuration()
    summary_key = os.environ["VSS_SUMMARIZATION_API_KEY"]
    monkeypatch.setenv("VLM_BASE_URL", NVIDIA_VLM_BASE_URL)
    monkeypatch.setenv("VLM_MODEL", NVIDIA_VLM_MODEL)
    monkeypatch.setenv("VLM_API_KEY", summary_key)

    video_path = tmp_path / "vlm-e2e.mp4"
    _write_test_video(video_path)
    await _run_e2e(video_path, "vlm", decoder="opencv")


@pytest.mark.skipif(
    os.getenv("RUN_RTVI_E2E") != "1",
    reason="set RUN_RTVI_E2E=1 to run the live RTVI pipeline",
)
async def test_rtvi_ingestion_and_summarization(tmp_path: Path) -> None:
    _require_summary_configuration()
    video_path = tmp_path / "rtvi-e2e.mp4"
    _write_test_video(video_path)
    await _run_e2e(video_path, "rtvi", model=RTVI_MODEL)
