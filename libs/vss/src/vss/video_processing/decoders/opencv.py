# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Caller-installed OpenCV CPU decoder backend."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import numpy as np

from ..contracts import DecodeOptions, MediaFacts
from ..exceptions import DecodeError, DecoderUnavailableError


def _opencv() -> Any:
    try:
        cv2 = importlib.import_module("cv2")
    except ImportError as exc:
        raise DecoderUnavailableError(
            "OpenCV CPU decoding requires caller-installed opencv-python >=4.13"
        ) from exc
    try:
        version = tuple(int(part) for part in cv2.__version__.split(".")[:2])
    except (AttributeError, ValueError) as exc:
        raise DecoderUnavailableError(
            "Could not determine the installed OpenCV version"
        ) from exc
    if version < (4, 13):
        raise DecoderUnavailableError("OpenCV CPU decoding requires version >=4.13")
    return cv2


def _codec_tag(cv2: Any, capture: Any) -> str | None:
    """Read a printable FourCC tag; zero/unsupported values remain unknown."""
    property_id = getattr(cv2, "CAP_PROP_FOURCC", None)
    if property_id is None:
        return None
    value = float(capture.get(property_id))
    if not np.isfinite(value) or not 0 < value < 2**32 or not value.is_integer():
        return None
    code = int(value).to_bytes(4, "little")
    if not all(32 <= byte <= 126 for byte in code):
        return None
    return code.decode("ascii").strip() or None


class OpenCVBackend:
    """Factory backend; importing this module itself does not import cv2."""

    def capability_map(self) -> dict[str, object]:
        return {"backend": "opencv", "device": "cpu", "seek": True}

    def open(self, path: Path, options: DecodeOptions) -> OpenCVSession:
        cv2 = _opencv()
        capture = cv2.VideoCapture(str(path))
        try:
            if not capture.isOpened():
                raise DecodeError("OpenCV could not open the local video file")
            # MediaFacts describes coded geometry, as does the GPU backend.
            # FFmpeg-backed OpenCV otherwise applies display rotation implicitly.
            orientation_auto = getattr(cv2, "CAP_PROP_ORIENTATION_AUTO", None)
            if orientation_auto is not None:
                disabled = capture.set(orientation_auto, 0)
                orientation_meta = getattr(cv2, "CAP_PROP_ORIENTATION_META", None)
                if (
                    not disabled
                    and orientation_meta is not None
                    and capture.get(orientation_meta) != 0
                ):
                    raise DecodeError(
                        "OpenCV cannot disable automatic display rotation "
                        "for this video"
                    )
        except BaseException:
            capture.release()
            raise
        return OpenCVSession(cv2, capture, path)


class OpenCVSession:
    def __init__(self, cv2: Any, capture: Any, path: Path) -> None:
        self.cv2 = cv2
        self.capture = capture
        self.path = path
        self.closed = False
        self._facts: MediaFacts | None = None
        self._index = 0
        self._last_ms: float | None = None

    def probe(self) -> MediaFacts:
        cv2, cap = self.cv2, self.capture
        fps_value = float(cap.get(cv2.CAP_PROP_FPS))
        count_value = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width_value = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height_value = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = fps_value if np.isfinite(fps_value) and fps_value > 0 else None
        count = count_value if count_value > 0 else None
        duration = count / fps if count is not None and fps else None
        warnings: list[str] = []
        if duration is None:
            warnings.append("OpenCV did not report trustworthy duration and frame rate")
        self._facts = MediaFacts(
            source_id=str(self.path),
            codec=_codec_tag(cv2, cap),
            duration_s=duration,
            duration_quality="container" if duration is not None else "unknown",
            fps=fps,
            frame_rate_mode="unknown",
            width=width_value if width_value > 0 else None,
            height=height_value if height_value > 0 else None,
            audio=None,
            container=None,
            warnings=tuple(
                warnings
                + [
                    "Audio and container metadata were not established by OpenCV probe",
                    "Raw video PTS origin is unknown; OpenCV frame timestamps "
                    "are already source-relative",
                ]
            ),
            frame_count=count,
            timestamp_origin_ns=None,
        )
        return self._facts

    def seek(self, timestamp_ns: int) -> None:
        # OpenCV's time seek can translate seconds through average FPS. On VFR
        # media that may skip the first eligible frame and invent its ordinal.
        # Without a verified PTS index, rewind and let the facade select by
        # observed timestamps in one forward pass. This costs prefix decoding
        # but preserves both first-at-or-after selection and source indices.
        if not self.capture.set(self.cv2.CAP_PROP_POS_FRAMES, 0):
            raise DecodeError("OpenCV could not rewind for reliable frame selection")
        self._index = 0
        self._last_ms = None

    def read_frames(self, batch_size: int) -> list[Any]:
        frames = []
        for _ in range(batch_size):
            ok, bgr = self.capture.read()
            if not ok:
                break
            rgb = self.cv2.cvtColor(bgr, self.cv2.COLOR_BGR2RGB)
            after_ms = float(self.capture.get(self.cv2.CAP_PROP_POS_MSEC))
            position = after_ms if np.isfinite(after_ms) and after_ms >= 0 else None
            quality = "backend"
            timestamp_ns = round(position * 1_000_000) if position is not None else None
            frames.append(
                _RawFrame(
                    rgb,
                    timestamp_ns,
                    self._index,
                    quality if timestamp_ns is not None else "estimated",
                )
            )
            self._index += 1
        return frames

    def close(self) -> None:
        if not self.closed:
            self.capture.release()
            self.closed = True


class _RawFrame:
    __slots__ = ("tensor", "timestamp_ns", "index", "timestamp_quality")

    def __init__(
        self,
        tensor: np.ndarray,
        timestamp_ns: int | None,
        index: int,
        timestamp_quality: str,
    ) -> None:
        self.tensor = tensor
        self.timestamp_ns = timestamp_ns
        self.index = index
        self.timestamp_quality = timestamp_quality
