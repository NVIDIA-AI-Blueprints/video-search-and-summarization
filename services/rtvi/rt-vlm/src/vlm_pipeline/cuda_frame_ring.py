# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded, thread-safe frame retention indexed by source epoch and PTS."""

from __future__ import annotations

import bisect
import os
import threading
from contextlib import contextmanager
from collections import OrderedDict
from dataclasses import dataclass
from typing import Hashable, Optional, Sequence

import nvtx


@contextmanager
def _ring_nvtx_stage(message: str):
    if os.environ.get("RTVI_VLM_NVTX_STAGES", "false").lower() not in ("true", "1"):
        yield
        return
    range_id = nvtx.start_range(message=message, color="blue")
    try:
        yield
    finally:
        nvtx.end_range(range_id)


@dataclass(frozen=True)
class _FrameWindow:
    coverage_start_ns: int
    coverage_end_ns: int
    pts_ns: tuple[int, ...]
    frames: tuple[object, ...]
    size_bytes: int
    selections: tuple[tuple[Hashable, "_PackedSelection"], ...] = ()


@dataclass(frozen=True)
class _PackedSelection:
    frames: object
    pts_ns: tuple[int, ...]
    size_bytes: int


def _frame_nbytes(frame) -> int:
    nbytes = getattr(frame, "nbytes", None)
    if nbytes is not None:
        return int(nbytes)
    element_size = getattr(frame, "element_size", None)
    numel = getattr(frame, "numel", None)
    if callable(element_size) and callable(numel):
        return int(element_size() * numel())
    return 0


class CudaFrameRing:
    """Retain complete dense frame windows under a strict byte budget.

    The ring is tensor-library agnostic: CUDA tensors remain on device because
    it stores references only. Eviction removes ring ownership but cannot
    invalidate references already acquired by a request.
    """

    def __init__(self, max_bytes: int):
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.max_bytes = int(max_bytes)
        self._bytes_used = 0
        self._windows: OrderedDict[tuple[Hashable, Hashable], _FrameWindow] = OrderedDict()
        self._fills: set[tuple[Hashable, Hashable]] = set()
        self._condition = threading.Condition()

    @property
    def bytes_used(self) -> int:
        with self._condition:
            return self._bytes_used

    def claim_fill(self, source_id: Hashable, epoch: Hashable) -> bool:
        key = (source_id, epoch)
        with self._condition:
            if key in self._fills:
                return False
            self._fills.add(key)
            return True

    def wait_for_fill(self, source_id: Hashable, epoch: Hashable, timeout: float) -> bool:
        key = (source_id, epoch)
        with _ring_nvtx_stage("rtvi.decode_ring_wait"):
            with self._condition:
                completed = self._condition.wait_for(
                    lambda: key not in self._fills,
                    timeout=timeout,
                )
                return bool(completed and key in self._windows)

    def abort_fill(self, source_id: Hashable, epoch: Hashable) -> None:
        key = (source_id, epoch)
        with self._condition:
            self._fills.discard(key)
            self._condition.notify_all()

    def discard(self, source_id: Hashable, epoch: Optional[Hashable] = None) -> None:
        """Release retained ownership for one source epoch or every source epoch."""
        with self._condition:
            keys = [
                key
                for key in self._windows
                if key[0] == source_id and (epoch is None or key[1] == epoch)
            ]
            for key in keys:
                self._bytes_used -= self._windows.pop(key).size_bytes
                self._fills.discard(key)
            self._condition.notify_all()

    def publish(
        self,
        source_id: Hashable,
        epoch: Hashable,
        coverage_start_ns: int,
        coverage_end_ns: int,
        pts_ns: Sequence[int],
        frames: Sequence[object],
        complete_fill: bool = True,
    ) -> bool:
        if len(pts_ns) != len(frames):
            raise ValueError("pts_ns and frames must have the same length")
        if any(left >= right for left, right in zip(pts_ns, pts_ns[1:])):
            raise ValueError("pts_ns must be strictly increasing")
        if coverage_start_ns > coverage_end_ns:
            raise ValueError("coverage_start_ns must not exceed coverage_end_ns")

        key = (source_id, epoch)
        window = _FrameWindow(
            coverage_start_ns=int(coverage_start_ns),
            coverage_end_ns=int(coverage_end_ns),
            pts_ns=tuple(int(pts) for pts in pts_ns),
            frames=tuple(frames),
            size_bytes=sum(_frame_nbytes(frame) for frame in frames),
        )
        with self._condition:
            previous = self._windows.pop(key, None)
            if previous is not None:
                self._bytes_used -= previous.size_bytes

            if previous is not None and (
                window.coverage_start_ns <= previous.coverage_end_ns
                and previous.coverage_start_ns <= window.coverage_end_ns
            ):
                merged = dict(zip(previous.pts_ns, previous.frames))
                merged.update(zip(window.pts_ns, window.frames))
                merged_pts = tuple(sorted(merged))
                merged_frames = tuple(merged[pts] for pts in merged_pts)
                window = _FrameWindow(
                    coverage_start_ns=min(
                        previous.coverage_start_ns,
                        window.coverage_start_ns,
                    ),
                    coverage_end_ns=max(previous.coverage_end_ns, window.coverage_end_ns),
                    pts_ns=merged_pts,
                    frames=merged_frames,
                    size_bytes=(
                        sum(_frame_nbytes(frame) for frame in merged_frames)
                        + sum(selection.size_bytes for _, selection in previous.selections)
                    ),
                    selections=previous.selections,
                )

            stored = self._store_window_locked(key, window)

            if complete_fill:
                self._fills.discard(key)
                self._condition.notify_all()
            return stored

    def append(
        self,
        source_id: Hashable,
        epoch: Hashable,
        pts_ns: int,
        frame: object,
    ) -> bool:
        """Append one live frame and evict the oldest retained PTS when full."""
        key = (source_id, epoch)
        with self._condition:
            previous = self._windows.pop(key, None)
            if previous is not None:
                self._bytes_used -= previous.size_bytes
                by_pts = dict(zip(previous.pts_ns, previous.frames))
                by_pts[int(pts_ns)] = frame
                ordered_pts = tuple(sorted(by_pts))
                frames = tuple(by_pts[pts] for pts in ordered_pts)
                coverage_start_ns = min(previous.coverage_start_ns, int(pts_ns))
                coverage_end_ns = max(previous.coverage_end_ns, int(pts_ns))
            else:
                ordered_pts = (int(pts_ns),)
                frames = (frame,)
                coverage_start_ns = coverage_end_ns = int(pts_ns)
            window = _FrameWindow(
                coverage_start_ns=coverage_start_ns,
                coverage_end_ns=coverage_end_ns,
                pts_ns=ordered_pts,
                frames=frames,
                size_bytes=sum(_frame_nbytes(item) for item in frames),
            )
            return self._store_window_locked(key, window)

    def _store_window_locked(
        self,
        key: tuple[Hashable, Hashable],
        window: _FrameWindow,
    ) -> bool:
        selections = list(window.selections)
        size_bytes = window.size_bytes
        while selections and size_bytes > self.max_bytes:
            _, selection = selections.pop(0)
            size_bytes -= selection.size_bytes

        pts_ns = list(window.pts_ns)
        frames = list(window.frames)
        while frames and size_bytes > self.max_bytes:
            size_bytes -= _frame_nbytes(frames.pop(0))
            pts_ns.pop(0)
        if not frames and window.frames:
            return False
        if pts_ns:
            window = _FrameWindow(
                coverage_start_ns=max(window.coverage_start_ns, pts_ns[0]),
                coverage_end_ns=window.coverage_end_ns,
                pts_ns=tuple(pts_ns),
                frames=tuple(frames),
                size_bytes=size_bytes,
                selections=tuple(selections),
            )
        self._windows[key] = window
        self._bytes_used += window.size_bytes
        while self._bytes_used > self.max_bytes and self._windows:
            _, evicted = self._windows.popitem(last=False)
            self._bytes_used -= evicted.size_bytes
        return key in self._windows

    def publish_selection(
        self,
        source_id: Hashable,
        epoch: Hashable,
        selection_key: Hashable,
        frames: object,
        pts_ns: Sequence[int],
    ) -> bool:
        """Retain one immutable packed tensor to avoid per-request D2D repacking."""
        key = (source_id, epoch)
        selection = _PackedSelection(
            frames=frames,
            pts_ns=tuple(int(pts) for pts in pts_ns),
            size_bytes=_frame_nbytes(frames),
        )
        with self._condition:
            window = self._windows.pop(key, None)
            if window is None:
                self._fills.discard(key)
                self._condition.notify_all()
                return False
            self._bytes_used -= window.size_bytes
            selections = OrderedDict(window.selections)
            previous = selections.pop(selection_key, None)
            candidate_size = window.size_bytes - (previous.size_bytes if previous else 0)
            candidate_size += selection.size_bytes
            if candidate_size > self.max_bytes:
                self._windows[key] = window
                self._bytes_used += window.size_bytes
                self._fills.discard(key)
                self._condition.notify_all()
                return False
            selections[selection_key] = selection
            updated = _FrameWindow(
                coverage_start_ns=window.coverage_start_ns,
                coverage_end_ns=window.coverage_end_ns,
                pts_ns=window.pts_ns,
                frames=window.frames,
                size_bytes=candidate_size,
                selections=tuple(selections.items()),
            )
            self._windows[key] = updated
            self._bytes_used += updated.size_bytes
            while self._bytes_used > self.max_bytes and self._windows:
                _, evicted = self._windows.popitem(last=False)
                self._bytes_used -= evicted.size_bytes
            self._fills.discard(key)
            self._condition.notify_all()
            return key in self._windows

    def acquire_selection(
        self,
        source_id: Hashable,
        epoch: Hashable,
        selection_key: Hashable,
    ) -> Optional[tuple[object, list[int]]]:
        key = (source_id, epoch)
        with _ring_nvtx_stage("rtvi.decode_ring_lookup_packed"):
            with self._condition:
                window = self._windows.get(key)
                if window is None:
                    return None
                selections = dict(window.selections)
                selection = selections.get(selection_key)
                if selection is None:
                    return None
                self._windows.move_to_end(key)
                return selection.frames, list(selection.pts_ns)

    def acquire(
        self,
        source_id: Hashable,
        epoch: Hashable,
        start_ns: int,
        end_ns: int,
        target_pts_ns: Optional[Sequence[int]] = None,
        target_indices: Optional[Sequence[int]] = None,
        select_all: bool = False,
    ) -> Optional[tuple[list[object], list[int]]]:
        key = (source_id, epoch)
        with _ring_nvtx_stage("rtvi.decode_ring_lookup"):
            with self._condition:
                window = self._windows.get(key)
                if (
                    window is None
                    or start_ns < window.coverage_start_ns
                    or end_ns > window.coverage_end_ns
                ):
                    return None
                self._windows.move_to_end(key)

                if select_all:
                    first = bisect.bisect_left(window.pts_ns, start_ns)
                    last = bisect.bisect_right(window.pts_ns, end_ns)
                    indices = range(first, last)
                elif target_indices is not None:
                    if any(index < 0 or index >= len(window.frames) for index in target_indices):
                        return None
                    indices = target_indices
                else:
                    indices = []
                    for target in target_pts_ns or ():
                        index = bisect.bisect_left(window.pts_ns, target)
                        if index >= len(window.pts_ns) or window.pts_ns[index] > end_ns:
                            return None
                        indices.append(index)

                selected_frames = [window.frames[index] for index in indices]
                selected_pts = [window.pts_ns[index] for index in indices]
                return selected_frames, selected_pts

    def acquire_exact(
        self,
        source_id: Hashable,
        epoch: Hashable,
        pts_ns: Sequence[int],
    ) -> Optional[tuple[list[object], list[int]]]:
        """Acquire exact live-frame PTS references without nearest-frame substitution."""
        key = (source_id, epoch)
        with self._condition:
            window = self._windows.get(key)
            if window is None:
                return None
            indices_by_pts = {pts: index for index, pts in enumerate(window.pts_ns)}
            if any(int(pts) not in indices_by_pts for pts in pts_ns):
                return None
            self._windows.move_to_end(key)
            indices = [indices_by_pts[int(pts)] for pts in pts_ns]
            return (
                [window.frames[index] for index in indices],
                [window.pts_ns[index] for index in indices],
            )
