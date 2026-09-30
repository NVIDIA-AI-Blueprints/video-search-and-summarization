# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Frame sampling for media-based rerankers."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .base import Candidate

_FRAME_FACTOR = 2
_FPS_MIN_FRAMES = 4


@dataclass(frozen=True)
class SampledVideoArray:
    """Explicit RGB frame sample plus metadata for offline vLLM video inputs."""

    frames: np.ndarray
    frame_indices: list[int]
    source_fps: float
    sample_fps: float
    duration: float


def _ceil_by_factor(value: float, factor: int) -> int:
    return int(math.ceil(value / factor) * factor)


def _floor_by_factor(value: float, factor: int) -> int:
    return int(math.floor(value / factor) * factor)


def controlled_frame_indices(
    candidate: Candidate,
    *,
    fps: float,
    max_frames: int,
    total_frames: int,
    video_fps: float,
) -> list[int]:
    """Frame indices for vLLM video rerankers that need explicit sampling.

    For controlled segment sampling: convert the candidate time window
    to source frame bounds, take roughly ``duration * fps`` frames, clamp to a
    small even minimum and ``max_frames``, then spread them evenly with linspace.
    """
    if video_fps <= 0.0:
        video_fps = max(1.0, float(fps))
    if total_frames <= 0:
        raise ValueError("Cannot sample video with no frames")

    seg = candidate.segment
    if seg.start is None or seg.end is None:
        start_frame = 0
        end_frame = total_frames - 1
    else:
        max_duration = total_frames / video_fps
        start = max(0.0, min(float(seg.start), max_duration))
        end = max(0.0, min(float(seg.end), max_duration))
        start_frame = max(0, int(math.ceil(start * video_fps)))
        end_frame = min(total_frames - 1, int(math.floor(end * video_fps)))
        if end_frame < start_frame:
            end_frame = start_frame

    segment_frames = end_frame - start_frame + 1
    if segment_frames <= 1:
        return [start_frame]

    min_frames = _ceil_by_factor(_FPS_MIN_FRAMES, _FRAME_FACTOR)
    capped_max = max(_FRAME_FACTOR, _floor_by_factor(max_frames, _FRAME_FACTOR))
    nframes = segment_frames / video_fps * float(fps)
    nframes = min(min(max(nframes, min_frames), capped_max), segment_frames)
    nframes = max(1, min(segment_frames, _floor_by_factor(nframes, _FRAME_FACTOR)))
    return np.linspace(start_frame, end_frame, nframes).round().astype(int).tolist()


def sampled_video_array(candidate: Candidate, fps: float, max_frames: int) -> SampledVideoArray:
    """Decode a controlled RGB frame sample for vLLM offline video inputs."""
    if candidate.video_path is None:
        raise ValueError("Candidate has no video_path; cannot sample video frames")

    import cv2

    source = candidate.video_path.resolve()
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise ValueError(f"Could not open video for frame sampling: {source}")

    try:
        video_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        indices = controlled_frame_indices(
            candidate,
            fps=fps,
            max_frames=max_frames,
            total_frames=total_frames,
            video_fps=video_fps,
        )
        frames = []
        for frame_idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
            ok, frame = cap.read()
            if not ok or frame is None:
                raise ValueError(f"Could not read frame {frame_idx} from source: {source}")
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    finally:
        cap.release()

    if video_fps <= 0.0:
        video_fps = max(1.0, float(fps))
    if len(indices) <= 1:
        duration = 1.0 / video_fps
    else:
        duration = max(1e-6, (indices[-1] - indices[0] + 1) / video_fps)
    return SampledVideoArray(
        frames=np.stack(frames, axis=0),
        frame_indices=indices,
        source_fps=video_fps,
        sample_fps=len(frames) / duration,
        duration=duration,
    )


def parallel_map(fn, items: list, workers: int = 8) -> list:
    """Thread-pool map (frame decode / image ops release the GIL), preserving order.

    Used to build per-candidate inputs concurrently so CPU preprocessing overlaps
    with the GPU instead of starving it.
    """
    if len(items) <= 1:
        return [fn(x) for x in items]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))
