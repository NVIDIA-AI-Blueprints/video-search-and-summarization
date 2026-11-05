# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier-Identifier: Apache-2.0

"""Protect the sequential sampler and the warm frame store's parity contract."""

import base64
from pathlib import Path

import cv2
import numpy as np
import pytest

from cosmos_reranker.reranking.base import Segment
from cosmos_reranker.reranking.data import Segment as DataSegment
from cosmos_reranker.reranking.frames import controlled_frame_indices, sampled_video_array
from cosmos_reranker.reranking.frame_store import FrameStore


def _candidate(path: Path, start=None, end=None):
    class C:
        video_path = path
        segment = DataSegment(0, path.stem, start, end)

    return C()


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    path = tmp_path_factory.mktemp("clips") / "sample.mp4"
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (64, 48)
    )
    for index in range(90):
        frame = np.full((48, 64, 3), index * 2, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    return path


def test_sequential_sampler_matches_index_plan(clip):
    sample = sampled_video_array(_candidate(clip), 1.0, 32)
    plan = controlled_frame_indices(
        _candidate(clip), fps=1.0, max_frames=32,
        total_frames=90, video_fps=30.0,
    )
    assert [int(i) for i in sample.frame_indices] == [int(i) for i in plan]
    assert len(sample.frames) == len(sample.frame_indices)
    assert sample.frames.dtype == np.uint8
    # gray ramp: sampled frames preserve the source frame order
    brightness = [int(frame[0, 0, 0]) for frame in sample.frames]
    assert brightness == sorted(brightness)


def test_frame_store_round_trip_is_payload_identical(clip):
    store = FrameStore(clip.parent.parent / "store")
    candidate = _candidate(clip)
    sample = sampled_video_array(candidate, 1.0, 32)
    store.put_sample(clip, sample)

    assert store.covers(clip, candidate, 1.0, 32)
    url, arrays, sample_fps = store.serve(clip, candidate, 1.0, 32)
    assert sample_fps == sample.sample_fps

    # the base64 payload is exactly what the live encode produced
    parts = []
    for frame in sample.frames:
        ok, buffer = cv2.imencode(
            ".jpg",
            cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
            [cv2.IMWRITE_JPEG_QUALITY, 95],
        )
        assert ok
        parts.append(base64.b64encode(buffer).decode("ascii"))
    assert url == "data:video/jpeg;base64," + ",".join(parts)

    # arrays are the PIL decode of those same bytes (what vLLM would produce)
    from io import BytesIO

    from PIL import Image

    for part, array in zip(parts, arrays):
        expected = np.asarray(Image.open(BytesIO(base64.b64decode(part))))
        assert np.array_equal(array, expected)

    # a fresh store instance (cold RAM) reads the same payload from disk
    cold = FrameStore(clip.parent.parent / "store")
    url2, _, _ = cold.serve(clip, candidate, 1.0, 32)
    assert url2 == url


def test_frame_store_partial_window_misses_are_live(clip):
    store = FrameStore(clip.parent.parent / "store-partial")
    whole = _candidate(clip)
    store.put_sample(clip, sampled_video_array(whole, 1.0, 32))
    window = _candidate(clip, 0.5, 2.5)
    assert not store.covers(clip, window, 1.0, 32)
