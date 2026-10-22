# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Owned CPU RGB frames and explicit image transfer encoding."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Literal

import numpy as np

from .contracts import EncodedFrame, FrameMetadata
from .exceptions import DecodeError, DecoderUnavailableError


@dataclass(frozen=True, slots=True)
class Frame:
    """An owned, read-only HxWx3 uint8 RGB frame.

    Pixels are copied into contiguous NumPy storage at construction, so a frame
    remains valid when the native decoder recycles its buffer or closes.
    """

    tensor: np.ndarray
    source_id: str
    timestamp_ns: int
    index: int | None
    timestamp_quality: Literal["pts", "backend", "estimated"]
    decoder: str
    device: Literal["cpu", "gpu"]
    requested_timestamp_ns: int | None = None
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        pixels = np.asarray(self.tensor)
        if pixels.dtype != np.uint8:
            raise TypeError("Frame tensor must have dtype uint8")
        if pixels.ndim != 3 or pixels.shape[2] != 3:
            raise ValueError("Frame tensor must have HxWx3 RGB shape")
        if pixels.shape[0] <= 0 or pixels.shape[1] <= 0:
            raise ValueError("Frame dimensions must be positive")
        owned = np.array(pixels, dtype=np.uint8, order="C", copy=True)
        owned.setflags(write=False)
        object.__setattr__(self, "tensor", owned)
        # Validate all JSON-facing fields through the frozen schema.
        FrameMetadata(
            source_id=self.source_id,
            timestamp_ns=self.timestamp_ns,
            requested_timestamp_ns=self.requested_timestamp_ns,
            index=self.index,
            timestamp_quality=self.timestamp_quality,
            decoder=self.decoder,
            device=self.device,
            fallback_reason=self.fallback_reason,
        )

    @property
    def metadata(self) -> FrameMetadata:
        """Return the JSON-safe provenance envelope, excluding pixels."""
        return FrameMetadata(
            source_id=self.source_id,
            timestamp_ns=self.timestamp_ns,
            requested_timestamp_ns=self.requested_timestamp_ns,
            index=self.index,
            timestamp_quality=self.timestamp_quality,
            decoder=self.decoder,
            device=self.device,
            fallback_reason=self.fallback_reason,
        )

    def to_base64(self, *, format: Literal["jpeg", "png"] = "jpeg") -> str:
        """Encode to plain base64 using caller-installed OpenCV.

        ``format`` is keyword-only and defaults to lossy JPEG; PNG is lossless.
        The input RGB pixels remain unchanged. Requires caller-installed OpenCV
        4.13 or newer. Missing/old OpenCV raises ``DecoderUnavailableError``;
        invalid formats raise ``ValueError`` and codec failures raise
        ``DecodeError``. The result is base64 text without a data-URI prefix.
        """
        return self.to_encoded(format=format).data_base64

    def to_encoded(self, *, format: Literal["jpeg", "png"] = "jpeg") -> EncodedFrame:
        """Return an explicit JSON-safe encoded-frame envelope.

        ``format`` is keyword-only and accepts ``"jpeg"`` (default) or
        ``"png"``. The envelope contains MIME type, base64 bytes, and frame
        provenance metadata; it does not mutate or replace the owned RGB array.
        Caller-installed OpenCV 4.13+ is required. Missing/old OpenCV raises
        ``DecoderUnavailableError``, unsupported formats raise ``ValueError``,
        and native encoding failures raise ``DecodeError``.
        """
        if format not in ("jpeg", "png"):
            raise ValueError("format must be 'jpeg' or 'png'")
        version: tuple[int, int]
        try:
            import cv2
        except ImportError as exc:
            raise DecoderUnavailableError(
                "Frame encoding requires caller-installed OpenCV >=4.13"
            ) from exc
        try:
            components = cv2.__version__.split(".")
            version = (int(components[0]), int(components[1]))
        except (AttributeError, ValueError):
            version = (0, 0)
        if version[0] < 4 or (version[0] == 4 and version[1] < 13):
            raise DecoderUnavailableError("Frame encoding requires OpenCV >=4.13")
        extension, mime = (
            (".jpg", "image/jpeg") if format == "jpeg" else (".png", "image/png")
        )
        try:
            bgr = cv2.cvtColor(self.tensor, cv2.COLOR_RGB2BGR)
            ok, encoded = cv2.imencode(extension, bgr)
        except Exception as exc:
            raise DecodeError(
                f"OpenCV failed while encoding this frame as {format}"
            ) from exc
        if not ok:
            raise DecodeError(f"OpenCV could not encode this frame as {format}")
        return EncodedFrame(
            metadata=self.metadata,
            mime_type=mime,
            data_base64=base64.b64encode(encoded.tobytes()).decode("ascii"),
        )
