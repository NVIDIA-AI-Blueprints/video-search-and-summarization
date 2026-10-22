# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Small decoder factory registry with lazy native backend imports."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_DECODER_REGISTRY: dict[str, Callable[[], Any]] = {
    "opencv": lambda: _make_opencv(),
    "pynv": lambda: _make_pynv(),
}


def _make_opencv() -> Any:
    from .opencv import OpenCVBackend

    return OpenCVBackend()


def _make_pynv() -> Any:
    from .pynv import PyNvBackend

    return PyNvBackend()


def register_decoder(
    name: str, factory: Callable[[], Any], *, replace: bool = False
) -> None:
    """Register a zero-argument factory for a blocking decoder backend.

    A backend provides ``capability_map()`` and ``open(path, options)``. Its
    session provides blocking ``probe()``, ``seek(raw_timestamp_ns)``,
    ``read_frames(batch_size)`` and ``close()`` methods. Returned raw frames
    expose ``tensor``, ``timestamp_ns``, ``index`` and ``timestamp_quality``.
    The library serializes factory/open/probe/seek/read/close on one worker
    thread and copies each read batch before the next native operation. Duplicate
    names raise ``ValueError`` unless ``replace=True``.

    Example::

        register_decoder("my_cpu", lambda: MyBackend())

    Then use ``DecodeOptions(decoder="my_cpu")``. Backend native libraries
    should be imported inside the factory/open path, not at package import.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("decoder name must not be empty")
    if not callable(factory):
        raise TypeError("factory must be callable")
    if name in _DECODER_REGISTRY and not replace:
        raise ValueError(f"decoder {name!r} is already registered")
    _DECODER_REGISTRY[name] = factory
