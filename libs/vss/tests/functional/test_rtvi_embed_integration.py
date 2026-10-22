# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Opt-in functional test for a live RTVI-Embed service."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from vss.ingestion import EmbeddingChunkResponse, RequestSource, ingest

pytestmark = [
    pytest.mark.functional,
    pytest.mark.skipif(
        os.getenv("RUN_RTVI_EMBED_FUNCTIONAL") != "1",
        reason="set RUN_RTVI_EMBED_FUNCTIONAL=1 to test a live RTVI-Embed service",
    ),
]


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
        raise RuntimeError("OpenCV could not create the RTVI-Embed test video")
    try:
        # Match the documented RT-Embed file profile: one ten-second chunk
        # with enough source frames for Cosmos-Embed's eight-frame sampler.
        for index in range(50):
            frame = np.zeros((240, 320, 3), dtype=np.uint8)
            cv2.rectangle(
                frame,
                (40 + index * 5, 80),
                (100 + index * 5, 140),
                (0, 255, 0),
                -1,
            )
            writer.write(frame)
    finally:
        writer.release()


async def test_uploaded_video_produces_embedding_chunks(tmp_path: Path) -> None:
    video_path = tmp_path / "vss-embed-functional-test.mp4"
    _write_test_video(video_path)

    results = await ingest(
        RequestSource.for_file(str(video_path)),
        backends=["rt_embed"],
        model=os.getenv("RTVI_EMBED_MODEL"),
        chunk_duration=10,
    )
    responses = results["rt_embed"]

    assert responses
    assert all(isinstance(response, EmbeddingChunkResponse) for response in responses)
    assert all(response.embeddings for response in responses)
    assert all(response.chunk.sourceId for response in responses)
    assert responses[0].chunk.is_first
    assert responses[-1].chunk.is_last
    assert len({len(response.embeddings) for response in responses}) == 1
