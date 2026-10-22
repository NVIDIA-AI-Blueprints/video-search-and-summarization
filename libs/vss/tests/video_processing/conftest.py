# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Fixtures for the processing contract suite, written before implementation.

The ordinary suite uses NumPy and synthetic decoder sessions, not native video
libraries. Native acceptance tests require explicit environment opt-in.

The decoder extension seam is deliberately narrow:
register_decoder(name, factory, replace=False); factory() returns a backend with
open(path: Path, options: DecodeOptions) -> session. A session implements probe(),
seek(timestamp_ns), read_frames(batch_size), and close(). These methods are
blocking and must run on one serialized worker per session. read_frames returns
objects exposing tensor, timestamp_ns, index, and timestamp_quality. Their
timestamps include the source origin; the processing facade normalizes them.

Unavailable backends raise DecoderUnavailableError from open(). Availability
errors may trigger automatic fallback before output, unlike decoding failures.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

from .fakes import MemoryBackend


@pytest.fixture
def processing() -> Any:
    # Import the existing, currently empty namespace. Tests still collect before
    # the proposed public exports exist; missing exports produce a red baseline.
    return importlib.import_module("vss.video_processing")


@pytest.fixture
def video_path(tmp_path: Path) -> Path:
    path = tmp_path / "video with spaces.mp4"
    path.write_bytes(b"synthetic source; only the fake decoder reads this file")
    return path


@pytest.fixture
def register(processing: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    registry = importlib.import_module("vss.video_processing.decoders.registry")
    # Each test gets its own registry. Overriding built-in names never leaks to
    # another test or a subsequent native acceptance run.
    monkeypatch.setattr(registry, "_DECODER_REGISTRY", {})

    def add(name: str = "memory", **kwargs: Any) -> MemoryBackend:
        backend = MemoryBackend(processing, name=name, **kwargs)
        processing.register_decoder(name, lambda: backend)
        return backend

    return add
