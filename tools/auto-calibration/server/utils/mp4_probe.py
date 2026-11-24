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

"""Read video resolution from MP4 (ISO-BMFF) metadata without decoding.

The microservice image ships an FFmpeg-free OpenCV build, and some valid MP4
files (for example H.264 High 4:4:4 / yuv444p) cannot be opened by the decode
path at all. Resolution is a container property, so it is read from the
``moov`` box directly instead of opening a decoder.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional, Tuple
import subprocess
import struct


_CONTAINER_BOXES = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts"}

# Sample entry types that carry VisualSampleEntry width/height fields.
_VIDEO_SAMPLE_ENTRIES = {
    b"avc1",
    b"avc3",
    b"hvc1",
    b"hev1",
    b"mp4v",
    b"encv",
    b"vp08",
    b"vp09",
    b"av01",
}

# Largest moov box we are willing to buffer (metadata only, not media data).
_MAX_MOOV_BYTES = 64 * 1024 * 1024


class UnsupportedDeepStreamVideoError(ValueError):
    """Raised when installed GStreamer/DeepStream plugins cannot decode a video."""


class DeepStreamValidationUnavailableError(RuntimeError):
    """Raised when deployed DeepStream validation runtime is unavailable."""


def require_deepstream_decode_support(path: Path, label: Optional[str] = None) -> None:
    """Require the deployed GStreamer plugin set to decode ``path``.

    Pipeline reads one frame through DeepStream's decoder path. Subprocess timeout
    bounds malformed media so validation cannot consume an executor thread forever.
    """
    name = label or path.name
    command = [
        "gst-launch-1.0", "-q", "uridecodebin", f"uri={path.resolve().as_uri()}",
        "!", "nvvideoconvert", "!", "fakesink", "num-buffers=1", "sync=false",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise DeepStreamValidationUnavailableError(
            "Video validation is unavailable. Contact system administrator."
        ) from exc

    output = f"{result.stdout}\n{result.stderr}".lower()
    if "no element" in output or "no such element or plugin" in output:
        raise DeepStreamValidationUnavailableError(
            "Video validation is unavailable: required NVIDIA DeepStream plugins are missing."
        )
    if result.returncode != 0:
        raise UnsupportedDeepStreamVideoError(
            f"Unsupported video format for AMC: '{name}' cannot be decoded by "
            "NVIDIA DeepStream/GStreamer on this deployment. Upload a video "
            "supported by installed DeepStream decoder plugins."
        )


def _read_moov(path: Path, label: Optional[str] = None) -> bytes:
    """Return the ``moov`` payload, seeking past media data instead of reading it."""
    name = label or path.name
    with path.open("rb") as stream:
        file_size = path.stat().st_size
        offset = 0
        while offset + 8 <= file_size:
            stream.seek(offset)
            header = stream.read(8)
            if len(header) < 8:
                break
            size, box_type = struct.unpack(">I4s", header)
            header_size = 8
            if size == 1:
                ext = stream.read(8)
                if len(ext) < 8:
                    break
                size = struct.unpack(">Q", ext)[0]
                header_size = 16
            elif size == 0:
                size = file_size - offset
            if size < header_size or offset + size > file_size:
                break
            if box_type == b"moov":
                payload_size = size - header_size
                if payload_size > _MAX_MOOV_BYTES:
                    raise ValueError(f"'{name}' has an unexpectedly large moov box")
                stream.seek(offset + header_size)
                return stream.read(payload_size)
            offset += size
    raise ValueError(f"No moov metadata box found in '{name}'")


def _iter_boxes(data: bytes, start: int = 0, end: Optional[int] = None) -> Iterator[Tuple[bytes, int, int]]:
    """Yield (box_type, payload_start, payload_end) for boxes in ``data``."""
    if end is None:
        end = len(data)
    offset = start
    while offset + 8 <= end:
        size, box_type = struct.unpack_from(">I4s", data, offset)
        header_size = 8
        if size == 1:
            if offset + 16 > end:
                break
            size = struct.unpack_from(">Q", data, offset + 8)[0]
            header_size = 16
        elif size == 0:
            size = end - offset
        if size < header_size or offset + size > end:
            break
        yield box_type, offset + header_size, offset + size
        offset += size


def _find_boxes(data: bytes, target: bytes, start: int = 0, end: Optional[int] = None) -> Iterator[Tuple[int, int]]:
    for box_type, payload_start, payload_end in _iter_boxes(data, start, end):
        if box_type == target:
            yield payload_start, payload_end
        elif box_type in _CONTAINER_BOXES:
            yield from _find_boxes(data, target, payload_start, payload_end)


def _resolution_from_stsd(moov: bytes) -> Optional[Tuple[int, int]]:
    for stsd_start, stsd_end in _find_boxes(moov, b"stsd"):
        # stsd payload: 1 byte version + 3 bytes flags + 4 bytes entry_count.
        if stsd_end - stsd_start < 8:
            continue
        entry_count = struct.unpack_from(">I", moov, stsd_start + 4)[0]
        offset = stsd_start + 8
        for _ in range(entry_count):
            if offset + 8 > stsd_end:
                break
            entry_size, entry_type = struct.unpack_from(">I4s", moov, offset)
            if entry_size < 8 or offset + entry_size > stsd_end:
                break
            # VisualSampleEntry: 6 reserved + 2 data_reference_index
            # + 16 predefined/reserved, then 16-bit width and height.
            if entry_type in _VIDEO_SAMPLE_ENTRIES and entry_size >= 8 + 28:
                width, height = struct.unpack_from(">HH", moov, offset + 8 + 24)
                if width > 0 and height > 0:
                    return int(width), int(height)
            offset += entry_size
    return None


def _resolution_from_tkhd(moov: bytes) -> Optional[Tuple[int, int]]:
    """Fallback to track display size (16.16 fixed point) when stsd is unusual."""
    for tkhd_start, tkhd_end in _find_boxes(moov, b"tkhd"):
        length = tkhd_end - tkhd_start
        if length < 1:
            continue
        version = moov[tkhd_start]
        # Fixed fields before width/height: 76 bytes for version 0; version 1
        # widens creation/modification/duration by 12 bytes in total.
        width_offset = 88 if version == 1 else 76
        if length < width_offset + 8:
            continue
        width = struct.unpack_from(">I", moov, tkhd_start + width_offset)[0] >> 16
        height = struct.unpack_from(">I", moov, tkhd_start + width_offset + 4)[0] >> 16
        if width > 0 and height > 0:
            return int(width), int(height)
    return None


def read_mp4_resolution(path: Path, label: Optional[str] = None) -> Tuple[int, int]:
    """Return (width, height) of the first video track in an MP4 file.

    ``label`` is an optional user-facing filename (e.g. the original upload name)
    used in error messages instead of the internal ``cam_XX.mp4`` path name.

    Raises:
        ValueError: if the file cannot be read or has no video track dimensions.
    """
    name = label or path.name
    try:
        moov = _read_moov(path, label=name)
    except OSError as exc:
        raise ValueError(f"Unable to read file '{name}': {exc}") from exc
    except struct.error as exc:
        raise ValueError(f"Malformed MP4 structure in '{name}': {exc}") from exc

    try:
        resolution = _resolution_from_stsd(moov) or _resolution_from_tkhd(moov)
    except struct.error as exc:
        raise ValueError(f"Malformed MP4 metadata in '{name}': {exc}") from exc

    if resolution is None:
        raise ValueError(f"Could not determine video resolution for '{name}'")
    return resolution
