# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Small observable decoder double; no processing policy is implemented here."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class RawFrame:
    tensor: np.ndarray
    timestamp_ns: int | None
    index: int | None
    timestamp_quality: str = "pts"


class MemorySession:
    def __init__(self, backend: MemoryBackend) -> None:
        self.backend = backend
        self.cursor = 0
        self.closed = False
        self.close_count = 0
        self.read_count = 0
        self.frames_read = 0
        self.requested_batches: list[int] = []
        self.seeks: list[int] = []
        self.thread_ids: set[int] = set()
        self.open_thread_id = threading.get_ident()
        self.in_native_call = False
        self._buffers: list[np.ndarray] = []

    def _record(self) -> None:
        self.thread_ids.add(threading.get_ident())
        if self.closed:
            raise AssertionError("decoder accessed after closure")

    @contextmanager
    def _operation(self, name: str) -> Iterator[None]:
        self._record()
        if self.in_native_call:
            raise AssertionError("concurrent native operations on one session")
        self.in_native_call = True
        try:
            self.backend.enter_operation(name)
            yield
        finally:
            self.in_native_call = False

    def probe(self) -> Any:
        with self._operation("probe"):
            if self.backend.probe_error is not None:
                raise self.backend.probe_error
            return self.backend.facts()

    def seek(self, timestamp_ns: int) -> None:
        with self._operation("seek"):
            self.seeks.append(timestamp_ns)
            # Selection belongs to the library, not to this decoder double.
            self.cursor = 0

    def read_frames(self, batch_size: int) -> list[RawFrame]:
        with self._operation("read"):
            self.read_count += 1
            self.requested_batches.append(batch_size)
            if self.backend.delay_s:
                time.sleep(self.backend.delay_s)
            if self.backend.read_error is not None:
                raise self.backend.read_error
            if self.backend.fail_at is not None and self.cursor >= self.backend.fail_at:
                raise self.backend.processing.DecodeError("synthetic decode failure")
            # Recycle only ACROSS calls. Every member of a returned batch must
            # retain distinct valid contents until the next decoder operation.
            for buffer in self._buffers:
                buffer.fill(255)
            while len(self._buffers) < batch_size:
                self._buffers.append(np.zeros((2, 3, 3), dtype=np.uint8))
            results = []
            for slot in range(batch_size):
                if self.cursor >= len(self.backend.timestamps_ns):
                    break
                index = self.cursor
                if self.backend.reuse_buffer:
                    pixels = self._buffers[slot]
                    pixels.fill(index % 256)
                else:
                    pixels = np.full((2, 3, 3), index % 256, dtype=np.uint8)
                timestamp = self.backend.timestamps_ns[index]
                results.append(
                    RawFrame(
                        tensor=pixels,
                        timestamp_ns=(
                            None
                            if timestamp is None
                            else self.backend.origin_ns + timestamp
                        ),
                        index=(
                            self.backend.indices[index]
                            if self.backend.indices is not None
                            else index
                        ),
                        timestamp_quality=self.backend.timestamp_quality,
                    )
                )
                self.cursor += 1
                self.frames_read += 1
            return results

    def close(self) -> None:
        if self.in_native_call:
            raise AssertionError("session closed during an active native operation")
        self.thread_ids.add(threading.get_ident())
        for buffer in self._buffers:
            buffer.fill(255)
        self.close_count += 1
        self.closed = True
        self.backend.closed.set()
        self.backend.active_sessions -= 1


class MemoryBackend:
    def __init__(
        self,
        processing: Any,
        *,
        name: str = "memory",
        device: str = "cpu",
        timestamps_ns: tuple[int | None, ...] = (
            0,
            100_000_000,
            200_000_000,
            300_000_000,
            400_000_000,
            500_000_000,
            600_000_000,
            700_000_000,
            800_000_000,
            900_000_000,
        ),
        duration_s: float | None = 1.0,
        origin_ns: int = 0,
        timestamp_quality: str = "pts",
        unavailable: str | None = None,
        open_error: Exception | None = None,
        fail_at: int | None = None,
        reuse_buffer: bool = False,
        delay_s: float = 0,
        probe_error: Exception | None = None,
        read_error: Exception | None = None,
        block_stage: str | None = None,
        indices: tuple[int | None, ...] | None = None,
        fps: float | None = 10.0,
    ) -> None:
        self.processing = processing
        self.name = name
        self.device = device
        self.timestamps_ns = timestamps_ns
        self.duration_s = duration_s
        self.origin_ns = origin_ns
        self.timestamp_quality = timestamp_quality
        self.unavailable = unavailable
        self.open_error = open_error
        self.fail_at = fail_at
        self.reuse_buffer = reuse_buffer
        self.delay_s = delay_s
        self.probe_error = probe_error
        self.read_error = read_error
        self.block_stage = block_stage
        self.indices = indices
        self.fps = fps
        self.sessions: list[MemorySession] = []
        self.open_paths: list[Path] = []
        self.open_options: list[Any] = []
        self.open_thread_ids: set[int] = set()
        self.active_sessions = 0
        self.max_active_sessions = 0
        self.operation_started = {
            name: threading.Event() for name in ("open", "probe", "seek", "read")
        }
        self.read_started = self.operation_started["read"]
        self.release_operation = threading.Event()
        self.closed = threading.Event()

    def enter_operation(self, name: str) -> None:
        self.operation_started[name].set()
        if self.block_stage == name:
            if not self.release_operation.wait(timeout=5):
                raise AssertionError(f"test did not release blocked native {name}")

    def capability_map(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "device": self.device,
            "seek": True,
            "timestamp_quality": self.timestamp_quality,
        }

    def open(self, path: Path, options: Any) -> MemorySession:
        self.open_thread_ids.add(threading.get_ident())
        self.open_paths.append(path)
        self.open_options.append(options)
        self.enter_operation("open")
        if self.unavailable:
            raise self.processing.DecoderUnavailableError(self.unavailable)
        if self.open_error:
            raise self.open_error
        self.active_sessions += 1
        self.max_active_sessions = max(self.max_active_sessions, self.active_sessions)
        session = MemorySession(self)
        self.sessions.append(session)
        return session

    def facts(self) -> Any:
        return self.processing.MediaFacts(
            source_id="decoder-local-identity",
            codec="synthetic",
            container="mp4",
            duration_s=self.duration_s,
            duration_quality="container" if self.duration_s else "unknown",
            fps=self.fps,
            frame_rate_mode="unknown",
            width=3,
            height=2,
            audio=None,
            frame_count=len(self.timestamps_ns),
            timestamp_origin_ns=self.origin_ns,
        )
