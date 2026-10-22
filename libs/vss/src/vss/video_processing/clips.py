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

"""Verified stream-copy clip artifacts using caller-installed FFmpeg tools."""

from __future__ import annotations

import asyncio
import json
import math
import shutil
import tempfile
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from .contracts import SourceInput
from .exceptions import ClipError
from .sources import resolve_source


@dataclass(frozen=True, slots=True)
class ClipArtifact:
    """Local compressed clip plus measured source-clock mapping.

    Public construction is borrowed and cleanup is a no-op. Artifacts returned
    by :func:`materialize_clip` own their temporary file until ``cleanup()``.
    """

    path: Path
    source_id: str
    requested_start_ns: int
    requested_end_ns: int
    actual_start_ns: int
    actual_end_ns: int
    pts_offset_ns: int
    duration_s: float
    container: str | None
    audio: bool | None
    _owned: bool = field(default=False, init=False, repr=False, compare=False)
    _directory: Path | None = field(default=None, init=False, repr=False, compare=False)

    @classmethod
    def _from_owned_path(cls, **fields: Any) -> ClipArtifact:
        """Private ownership seam used only after successful materialization."""
        directory = fields.pop("_directory", None)
        artifact = cls(**fields)
        object.__setattr__(artifact, "_owned", True)
        object.__setattr__(artifact, "_directory", directory)
        return artifact

    def cleanup(self) -> None:
        """Delete this artifact's owned temporary output; safe to call repeatedly."""
        if not self._owned:
            return
        object.__setattr__(self, "_owned", False)
        try:
            self.path.unlink(missing_ok=True)
        finally:
            if self._directory is not None:
                self._directory.rmdir()

    def __enter__(self) -> ClipArtifact:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.cleanup()


async def _communicate(executable: str, *arguments: str) -> tuple[bytes, bytes]:
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise ClipError(
            f"Could not start external tool {Path(executable).name}"
        ) from exc
    try:
        stdout, stderr = await process.communicate()
    except asyncio.CancelledError:
        teardown = asyncio.create_task(_terminate_reap(process))
        try:
            await _await_task_through_cancellation(teardown)
        except BaseException:
            # Preserve the caller's cancellation as the primary outcome. The
            # teardown task still reaps the child before this function exits.
            pass
        raise
    if process.returncode != 0:
        diagnostic = stderr.decode("utf-8", errors="replace")[:1000].strip()
        raise ClipError(
            f"{Path(executable).name} failed (exit {process.returncode}): "
            f"{diagnostic or 'no diagnostic output'}"
        )
    return stdout, stderr


async def _await_task_through_cancellation(task: asyncio.Task[Any]) -> None:
    """Join cleanup even if the caller receives additional cancel requests."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    task.result()


async def _terminate_reap(process: Any) -> None:
    """Terminate, then kill if needed, reap, and drain a cancelled child."""
    try:
        if process.returncode is None:
            process.terminate()
    except ProcessLookupError:
        pass
    wait_task = asyncio.create_task(process.wait())
    try:
        await asyncio.wait_for(asyncio.shield(wait_task), timeout=0.25)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await wait_task
    try:
        await process.communicate()
    except (ProcessLookupError, RuntimeError):
        pass


async def _probe(ffprobe: str, path: Path) -> dict[str, Any]:
    stdout, _ = await _communicate(
        ffprobe,
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-show_packets",
        "-show_data_hash",
        "sha256",
        "-of",
        "json",
        str(path),
    )
    try:
        document = await asyncio.to_thread(json.loads, stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ClipError("ffprobe returned malformed JSON metadata") from exc
    if not isinstance(document, dict):
        raise ClipError("ffprobe returned malformed JSON metadata")
    return document


def _video_stream(document: dict[str, Any]) -> dict[str, Any]:
    streams = [
        stream
        for stream in document.get("streams", [])
        if stream.get("codec_type") == "video"
    ]
    if not streams:
        raise ClipError("Source contains no verifiable video stream")
    return streams[0]


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _video_packets(
    document: dict[str, Any], stream: dict[str, Any]
) -> list[dict[str, Any]]:
    stream_index = stream.get("index")
    packets = [
        packet
        for packet in document.get("packets", [])
        if packet.get("stream_index") == stream_index
        and _number(packet.get("pts_time")) is not None
    ]
    packets.sort(key=lambda packet: float(packet["pts_time"]))
    return packets


def _hash(packet: dict[str, Any]) -> str | None:
    value = packet.get("data_hash")
    if not isinstance(value, str):
        return None
    # ffprobe prefixes the digest with e.g. "SHA256:".
    return value.split(":", 1)[-1].lower()


def _packet_time(packet: dict[str, Any], stream: dict[str, Any]) -> Fraction:
    """Prefer exact stream ticks over ffprobe's rounded decimal seconds."""
    ticks = packet.get("pts")
    if isinstance(ticks, (int, str)) and not isinstance(ticks, bool):
        try:
            timebase = Fraction(str(stream["time_base"]))
            if timebase > 0:
                return int(ticks) * timebase
        except (KeyError, ValueError, ZeroDivisionError):
            pass
    return Fraction(str(packet["pts_time"]))


def _find_subsequences[Hash: object](
    sequence: list[Hash], pattern: list[Hash]
) -> list[int]:
    """Return every contiguous pattern offset using linear-time KMP matching."""
    if not pattern or len(pattern) > len(sequence):
        return []
    prefix = [0] * len(pattern)
    matched = 0
    for index in range(1, len(pattern)):
        while matched and pattern[index] != pattern[matched]:
            matched = prefix[matched - 1]
        if pattern[index] == pattern[matched]:
            matched += 1
        prefix[index] = matched

    offsets: list[int] = []
    matched = 0
    for index, value in enumerate(sequence):
        while matched and value != pattern[matched]:
            matched = prefix[matched - 1]
        if value == pattern[matched]:
            matched += 1
        if matched == len(pattern):
            offsets.append(index - len(pattern) + 1)
            matched = prefix[matched - 1]
    return offsets


def _audio_overlaps_segment(
    document: dict[str, Any], start_s: float, end_s: float
) -> bool | None:
    """Report source audio packet overlap, or None when its timing is unknown."""
    audio_indices = {
        stream.get("index")
        for stream in document.get("streams", [])
        if stream.get("codec_type") == "audio"
    }
    if not audio_indices:
        return False
    source_packets = document.get("packets", [])
    overlaps = False
    timing_unknown = False
    for stream_index in audio_indices:
        packets = [
            packet
            for packet in source_packets
            if packet.get("stream_index") == stream_index
        ]
        if not packets:
            timing_unknown = True
            continue
        for packet in packets:
            packet_start = _number(packet.get("pts_time"))
            if packet_start is None:
                timing_unknown = True
                continue
            if packet_start >= end_s:
                continue
            if packet_start >= start_s:
                overlaps = True
                continue
            packet_duration = _number(packet.get("duration_time"))
            if packet_duration is None or packet_duration <= 0:
                timing_unknown = True
                continue
            packet_end = packet_start + packet_duration
            overlaps = overlaps or (packet_start < end_s and packet_end > start_s)
    if overlaps:
        return True
    return None if timing_unknown else False


def _verify_mapping(
    source_doc: dict[str, Any],
    output_doc: dict[str, Any],
    start_ns: int,
    end_ns: int,
) -> tuple[int, int, int, float, str | None, bool]:
    source_stream = _video_stream(source_doc)
    output_stream = _video_stream(output_doc)
    source_packets = _video_packets(source_doc, source_stream)
    output_packets = _video_packets(output_doc, output_stream)
    if not source_packets or not output_packets:
        raise ClipError("ffprobe did not provide video packet presentation timestamps")
    source_hashes = [_hash(packet) for packet in source_packets]
    output_hashes = [_hash(packet) for packet in output_packets]
    if any(value is None for value in source_hashes + output_hashes):
        raise ClipError(
            "ffprobe packet hashes are required to verify copied video identity"
        )
    source_pts = [
        round(float(packet["pts_time"]) * 1_000_000_000) for packet in source_packets
    ]
    output_pts = [
        round(float(packet["pts_time"]) * 1_000_000_000) for packet in output_packets
    ]
    source_origin = source_pts[0]
    output_origin = output_pts[0]
    output_relative = [value - output_origin for value in output_pts]
    candidates = _find_subsequences(source_hashes, output_hashes)
    matching_segments: list[list[int]] = []
    for offset in candidates:
        indices = list(range(offset, offset + len(output_packets)))
        source_relative = [source_pts[index] - source_pts[offset] for index in indices]
        source_durations = [
            _number(source_packets[index].get("duration_time")) for index in indices
        ]
        output_durations = [
            _number(packet.get("duration_time")) for packet in output_packets
        ]
        timing_matches = all(
            abs(left - right) <= 2_000_000
            for left, right in zip(source_relative, output_relative, strict=True)
        )
        durations_match = all(
            left is not None and right is not None and abs(left - right) <= 0.002
            for left, right in zip(source_durations, output_durations, strict=True)
        )
        if timing_matches and durations_match:
            matching_segments.append(indices)
            if len(matching_segments) > 1:
                raise ClipError(
                    "Copied packet identity maps to multiple source intervals; "
                    "source clock is ambiguous"
                )
    if not matching_segments:
        if not candidates:
            raise ClipError("Copied video packet identity does not match the source")
        raise ClipError(
            "Stream-copy changed relative video packet timing or packet durations"
        )
    if len(matching_segments) != 1:
        raise ClipError(
            "Copied packet identity maps to multiple source intervals; "
            "source clock is ambiguous"
        )
    mapped_indices = matching_segments[0]
    packet_durations = [
        _number(packet.get("duration_time")) for packet in output_packets
    ]
    last_duration = packet_durations[-1]
    if last_duration is None or last_duration <= 0:
        raise ClipError("ffprobe did not provide the final video packet duration")
    actual_start = source_pts[mapped_indices[0]] - source_origin
    actual_end = (
        source_pts[mapped_indices[-1]]
        + round(last_duration * 1_000_000_000)
        - source_origin
    )
    if actual_start > start_ns or actual_end < end_ns:
        raise ClipError(
            "Verified copied GOPs do not cover the requested half-open window"
        )
    if not str(source_packets[mapped_indices[0]].get("flags", "")).startswith("K"):
        raise ClipError("Copied clip does not begin at a verified video keyframe")
    audio = any(
        stream.get("codec_type") == "audio" for stream in output_doc.get("streams", [])
    )
    source_start_s = source_pts[mapped_indices[0]] / 1_000_000_000
    source_last_duration = _number(
        source_packets[mapped_indices[-1]].get("duration_time")
    )
    if source_last_duration is None:
        source_last_duration = last_duration
    source_end_s = source_pts[mapped_indices[-1]] / 1_000_000_000 + source_last_duration
    audio_expected = _audio_overlaps_segment(source_doc, source_start_s, source_end_s)
    if audio_expected is None:
        audio_expected = any(
            stream.get("codec_type") == "audio"
            for stream in source_doc.get("streams", [])
        )
    if audio_expected and not audio:
        raise ClipError("Stream-copy did not preserve the source audio stream")
    format_name = output_doc.get("format", {}).get("format_name")
    container = "mp4" if isinstance(format_name, str) and "mp4" in format_name else None
    duration_s = (actual_end - actual_start) / 1_000_000_000
    return actual_start, actual_end, actual_start, duration_s, container, audio


async def materialize_clip(
    source: SourceInput,
    start: float,
    end: float,
    *,
    source_id: str | None = None,
) -> ClipArtifact:
    """Create and verify a keyframe-aligned stream-copy clip.

    ``source`` accepts a local path, local ``file://`` URI, or an object with
    ``url`` and ``source_id``. ``start`` and ``end`` are finite seconds on the
    source-relative video clock and define a half-open range. FFmpeg and ffprobe
    must be installed by the caller. The function copies a surrounding GOP and
    verifies video packet hashes, timestamps, durations, and expected audio
    presence before
    returning. Invalid or out-of-duration bounds raise ``ValueError``;
    missing tools or unverifiable identity/timing raise :class:`ClipError`.
    The artifact owns a temporary local MP4 file until ``cleanup()`` or
    context-manager exit. Its path is host-local, not a portable URL.
    """
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, (int, float))
        or not isinstance(end, (int, float))
        or not math.isfinite(start)
        or not math.isfinite(end)
        or start < 0
        or end <= start
    ):
        raise ValueError("clip start/end must be finite seconds with 0 <= start < end")
    resolved = resolve_source(source, source_id)
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg:
        raise ClipError(
            "External ffmpeg executable is required; install FFmpeg and add it to PATH"
        )
    if not ffprobe:
        raise ClipError(
            "External ffprobe executable is required; install FFmpeg and add it to PATH"
        )
    requested_start = round(start * 1_000_000_000)
    requested_end = round(end * 1_000_000_000)
    directory = Path(tempfile.mkdtemp(prefix="vss-clip-"))
    output = directory / "clip.mp4"
    try:
        source_doc = await _probe(ffprobe, resolved.path)
        source_stream = _video_stream(source_doc)
        packets = _video_packets(source_doc, source_stream)
        if not packets:
            raise ClipError("Source has no verifiable video packet timeline")
        source_start = _number(packets[0].get("pts_time"))
        source_end = _number(packets[-1].get("pts_time"))
        final_duration = _number(packets[-1].get("duration_time"))
        if source_start is None or source_end is None or final_duration is None:
            raise ClipError("Source video packet bounds could not be measured")
        source_duration_ns = round(
            (source_end + final_duration - source_start) * 1_000_000_000
        )
        if requested_end > source_duration_ns:
            raise ValueError("clip end exceeds the known video duration")
        source_pts = [
            round(float(packet["pts_time"]) * 1_000_000_000) for packet in packets
        ]
        keyframes = [
            index
            for index, packet in enumerate(packets)
            if str(packet.get("flags", "")).startswith("K")
        ]
        if not keyframes:
            raise ClipError("Source video packet timeline has no verifiable keyframes")
        start_index = next(
            (
                index
                for index in reversed(keyframes)
                if source_pts[index] - source_pts[0] <= requested_start
            ),
            None,
        )
        if start_index is None:
            raise ClipError("No video keyframe covers the requested clip start")
        end_index = next(
            (
                index
                for index in keyframes
                if source_pts[index] - source_pts[0] >= requested_end
            ),
            None,
        )
        end_boundary = (
            source_pts[end_index] - source_pts[0]
            if end_index is not None
            else source_duration_ns
        )
        aligned_start = source_pts[start_index] - source_pts[0]
        aligned_duration = max(0, end_boundary - aligned_start)
        video_filter: tuple[str, ...] = ()
        if end_index is not None:
            duration = _packet_time(packets[end_index], source_stream) - _packet_time(
                packets[start_index], source_stream
            )
            # -t limits copy by decode order: B-frame reference packets can
            # have later PTS and leak into the next GOP. Drop video packets at
            # the presentation boundary, without modifying retained payloads.
            # Rescale the exact duration to the BSF timebase; startpts cancels
            # seek/mux offsets. Audio retains the original duration limit.
            video_filter = (
                "-bsf:v",
                "noise=amount=0:drop=gte(pts-startpts\\,"
                f"round(({duration.numerator}/{duration.denominator})/tb))",
            )
        await _communicate(
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-seek_timestamp",
            "1",
            "-ss",
            f"{(source_pts[0] + aligned_start) / 1_000_000_000:.9f}",
            "-i",
            str(resolved.path),
            "-t",
            f"{aligned_duration / 1_000_000_000:.9f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c",
            "copy",
            *video_filter,
            "-avoid_negative_ts",
            "disabled",
            str(output),
        )
        output_doc = await _probe(ffprobe, output)
        (
            actual_start,
            actual_end,
            offset,
            duration_s,
            container,
            audio,
        ) = await asyncio.to_thread(
            _verify_mapping,
            source_doc,
            output_doc,
            requested_start,
            requested_end,
        )
        return ClipArtifact._from_owned_path(
            path=output,
            source_id=resolved.source_id,
            requested_start_ns=requested_start,
            requested_end_ns=requested_end,
            actual_start_ns=actual_start,
            actual_end_ns=actual_end,
            pts_offset_ns=offset,
            duration_s=duration_s,
            container=container,
            audio=audio,
            _directory=directory,
        )
    except asyncio.CancelledError:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except ValueError:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
