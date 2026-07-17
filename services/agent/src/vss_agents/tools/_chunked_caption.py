# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Dense chunked captioning for long videos.

The base ``video_understanding`` tool samples a fixed frame budget
(``num_frames = min(video_length * max_fps, max_frames)``) across the WHOLE clip.
On a long video that saturates at ``max_frames`` and the effective frame rate drops
far below 1 fps (e.g. a 210s clip at ``max_frames=30`` is ~1 frame / 7s), which
starves the VLM and makes it confabulate events that never happen.

This module holds the pure, dependency-free orchestration for the fix: tile the
video into short fixed-length windows and caption each one densely (a ~30s window
at the same frame budget is ~1 fps), then aggregate. Each window's caption is
produced by a caller-supplied coroutine so this module never imports the VLM/VST
stack (keeps it unit-testable and free of import cycles). The caller passes a
``caption_window`` callable; this module never calls the ``video_understanding``
tool itself, so the tool cannot recurse into itself.
"""

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
import logging

logger = logging.getLogger(__name__)

# Appended to the user's prompt for each window so the VLM grounds its answer in the
# segment it can actually see and does not speculate about the rest of the video.
DENSE_WINDOW_INSTRUCTION = (
    "\n\nThis video is one short segment of a longer video. Describe in detail only what is "
    "visibly happening in this segment, grounded in what you can actually see. Do not describe or "
    "infer anything outside this segment, and do not invent events you cannot see."
)


def divide_into_windows(duration_seconds: float, window_seconds: int) -> list[tuple[float, float]]:
    """Tile ``[0, duration_seconds]`` into consecutive windows of ``window_seconds``.

    The final window may be shorter than ``window_seconds``. Always returns at least
    one window (a video shorter than one window yields a single ``[0, duration]`` window).
    """
    if window_seconds <= 0:
        raise ValueError(f"window_seconds must be > 0, got {window_seconds!r}")
    if duration_seconds <= 0:
        return [(0.0, float(duration_seconds))]
    windows: list[tuple[float, float]] = []
    start = 0.0
    while start < duration_seconds:
        end = min(start + window_seconds, duration_seconds)
        windows.append((start, end))
        start = end
    return windows


async def caption_in_windows(
    *,
    windows: list[tuple[float, float]],
    caption_window: Callable[[float, float], Awaitable[str]],
    max_concurrency: int = 3,
    join_with: str = "\n\n",
) -> str:
    """Caption a long video over the given windows, then aggregate.

    Args:
        windows: the ``[(start, end), ...]`` windows to caption (from ``divide_into_windows``).
            Passing them in rather than recomputing keeps the tiling decision single-sourced
            in the caller.
        caption_window: coroutine ``(window_start, window_end) -> caption`` supplied by the
            caller. It owns the actual VLM/VST call for one sub-clip. Windows are absolute
            offsets so the caller can fetch the matching sub-clip URL.
        max_concurrency: upper bound on concurrent ``caption_window`` calls. The video model
            is a shared, memory-bounded resource; an unbounded fan-out on a long video can
            thrash it. Defaults to a conservative 3.
        join_with: separator between window captions in the aggregated result.

    Returns:
        The window captions joined into one string, each prefixed with its ``[start-end s]`` time
        range (relative to the start of the captioned span; the caller only enables this for spans
        that start at 0, so the range doubles as the absolute video time) so the result stays
        chronologically legible without depending on the VLM emitting its own timestamps.

    Partial-failure policy: one window failing (after its own retries) must not discard the windows
    that succeeded, since the whole point is coverage. A failed or empty window is kept in place with
    a visible placeholder; the call raises only if EVERY window fails, so a total outage still fails
    atomically like the single-pass path and a mostly-failed result never masquerades as success.
    """
    semaphore = asyncio.Semaphore(max(1, max_concurrency))
    failures = 0

    async def _run(window_start: float, window_end: float) -> str:
        nonlocal failures
        header = f"[{round(window_start)}-{round(window_end)}s]"
        try:
            async with semaphore:
                caption = await caption_window(window_start, window_end)
        except Exception as exc:  # one window must not sink the batch
            failures += 1
            logger.warning("dense caption window %s failed: %s: %s", header, type(exc).__name__, exc)
            return f"{header} [caption unavailable]"
        caption = (caption or "").strip()
        return f"{header} {caption}" if caption else f"{header} [no caption returned]"

    parts = await asyncio.gather(*(_run(start, end) for start, end in windows))
    if failures == len(parts):
        raise RuntimeError(f"dense chunked captioning: all {failures} window(s) failed")
    return join_with.join(parts)
