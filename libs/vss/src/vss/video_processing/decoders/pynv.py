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

"""PyNvVideoCodec 2.2+ GPU decoder.

The normal path returns owned host RGB.  ``frame_transport="cuda_ipc"`` keeps
the native NV12 output on-device for immediate publication by the shared-frame
broker.
"""

from __future__ import annotations

import ctypes
import importlib
import platform
import threading
import time
from collections import deque
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, Literal

import numpy as np

from vss.observability import metric_scope, record
from vss.observability.context import current_scope, defer_observations
from vss.observability.instrumentation import stage

from ..contracts import DecodeOptions, MediaFacts
from ..exceptions import DecodeError, DecoderUnavailableError, TimestampUnavailableError

_NATIVE_LOCK = threading.RLock()
_LOCK_DEPTH = threading.local()


def _serialized[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Keep creation, decode/copy and release indivisible across sessions.

    PyNv 2.2.3 RGB kernels share CUDA color coefficients across sessions.
    Sessions may coexist; native work is serialized to prevent pixel corruption.
    """

    @wraps(fn)
    def invoke(*args: P.args, **kwargs: P.kwargs) -> R:
        if current_scope() is None or getattr(_LOCK_DEPTH, "value", 0):
            with _NATIVE_LOCK:
                return fn(*args, **kwargs)
        with metric_scope(backend="pynv", device="gpu"), defer_observations():
            started = time.perf_counter_ns()
            with _NATIVE_LOCK:
                waited = (time.perf_counter_ns() - started) / 1e9
                _LOCK_DEPTH.value = 1
                try:
                    return fn(*args, **kwargs)
                finally:
                    _LOCK_DEPTH.value = 0
                    record("processing.native.lock_wait", waited, library="processing")

    return invoke


class PyNvBackend:
    """Lazily initialized PyNvVideoCodec backend."""

    def capability_map(self) -> dict[str, object]:
        return {"backend": "pynv", "device": "gpu", "seek": True}

    @_serialized
    def open(self, path: Path, options: DecodeOptions) -> PyNvSession:
        if platform.system() not in {"Linux", "Windows"}:
            raise DecoderUnavailableError(
                "PyNvVideoCodec is supported here only on Linux/Windows"
            )
        try:
            nvc = importlib.import_module("PyNvVideoCodec")
        except ImportError as exc:
            raise DecoderUnavailableError(
                "PyNvVideoCodec >=2.2 is not installed; install a build compatible "
                "with this GPU and driver"
            ) from exc
        version = getattr(nvc, "__version__", "0")
        try:
            if tuple(int(part) for part in version.split(".")[:2]) < (2, 2):
                raise DecoderUnavailableError(
                    f"PyNvVideoCodec {version} is too old; install version >=2.2"
                )
        except ValueError as exc:
            raise DecoderUnavailableError(
                "Could not determine the PyNvVideoCodec version"
            ) from exc
        decoder = session = None
        demuxer: Any = None
        failure: DecodeError | DecoderUnavailableError | None = None
        phase = "demux"
        try:
            create_demuxer = getattr(nvc, "CreateDemuxer", None)
            if not callable(create_demuxer):
                raise DecoderUnavailableError(
                    "PyNvVideoCodec demuxer API is unavailable"
                )
            demuxer = create_demuxer(filename=str(path))
            if not all(
                callable(getattr(demuxer, name, None))
                for name in (
                    "GetTimebaseNum",
                    "GetTimebaseDen",
                    "GetNvCodecId",
                )
            ):
                raise DecoderUnavailableError(
                    "PyNvVideoCodec packet timing API is unavailable"
                )
            time_num, time_den = (
                int(demuxer.GetTimebaseNum()),
                int(demuxer.GetTimebaseDen()),
            )
            if time_num <= 0 or time_den <= 0:
                raise DecodeError("PyNvVideoCodec reported an invalid video timebase")
            _check_cuda_device(options.device_id)
            codec_id = demuxer.GetNvCodecId()
            codec_name = getattr(codec_id, "name", codec_id)
            if (
                version == "2.2.3"
                and platform.system() == "Linux"
                and platform.machine() == "x86_64"
                and str(codec_name).upper() == "VP9"
                and demuxer.ColorSpace() == nvc.ColorSpace.BT_709
                and demuxer.ColorRange() == nvc.ColorRange.MPEG
            ):
                raise DecoderUnavailableError(
                    "PyNvVideoCodec 2.2.3 Linux RGB color conversion is inaccurate "
                    "for limited-range BT.709 VP9; select OpenCV or validate "
                    "another native build"
                )
            phase = "decoder"
            create_decoder = getattr(nvc, "CreateDecoder", None)
            if not callable(create_decoder):
                raise DecoderUnavailableError(
                    "PyNvVideoCodec packet decoder API is unavailable"
                )
            decoder = create_decoder(
                **_decoder_arguments(nvc, demuxer.GetNvCodecId(), options)
            )
            phase = "metadata"
            record("processing.native.sessions", 1, library="processing")
            session = PyNvSession(
                nvc, decoder, demuxer, time_num, time_den, path, options
            )
            first = session.read_frames(1)
            if not first:
                raise DecodeError("PyNvVideoCodec found no decoded video frames")
            session.initializing = False
            session.pending.appendleft(first[0])
            return session
        except Exception as exc:
            if isinstance(exc, DecoderUnavailableError):
                failure = DecoderUnavailableError(str(exc))
            elif isinstance(exc, TimestampUnavailableError):
                failure = TimestampUnavailableError(str(exc))
            elif phase == "decoder" and _device_unavailable(nvc, exc):
                failure = DecoderUnavailableError(
                    "PyNvVideoCodec GPU/driver or codec support "
                    "is unavailable; "
                    "check the selected GPU, driver, codec and bit depth"
                )
            else:
                failure = DecodeError(
                    f"PyNvVideoCodec failed during {phase} initialization; "
                    "verify the source and native installation"
                )
            _clear_tracebacks(exc)
            if session is not None:
                session.close()
            decoder = demuxer = session = None
        assert failure is not None
        raise failure


def _clear_tracebacks(error: BaseException) -> None:
    """Detach all native error frames, including suppressed cleanup failures."""
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        current.__traceback__ = None
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                pending.append(linked)
        current.__cause__ = None
        current.__context__ = None


def _check_cuda_device(device_id: int) -> None:
    """Check driver, ordinal and primary context without fatal native logging.

    PyNvVideoCodec 2.2.3's native initialization can exit the process when CUDA
    initialization fails. Direct driver calls return status codes instead. No
    context is left retained by this check, which runs on the session worker.
    """
    try:
        if platform.system() == "Windows":
            # This attribute is absent from ctypes on non-Windows hosts.
            loader = getattr(ctypes, "WinDLL")  # noqa: B009
            driver = loader("nvcuda.dll")
        else:
            driver = ctypes.CDLL("libcuda.so.1")
        initialize = driver.cuInit
        count_devices = driver.cuDeviceGetCount
        get_device = driver.cuDeviceGet
        retain = driver.cuDevicePrimaryCtxRetain
        release = getattr(driver, "cuDevicePrimaryCtxRelease_v2", None)
        if release is None:
            release = driver.cuDevicePrimaryCtxRelease
        initialize.argtypes = [ctypes.c_uint]
        count_devices.argtypes = [ctypes.POINTER(ctypes.c_int)]
        get_device.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int]
        retain.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_int]
        release.argtypes = [ctypes.c_int]
        for function in (initialize, count_devices, get_device, retain, release):
            function.restype = ctypes.c_int
        count = ctypes.c_int()
        device = ctypes.c_int()
        context = ctypes.c_void_p()
        if initialize(0) != 0 or count_devices(ctypes.byref(count)) != 0:
            raise DecoderUnavailableError("CUDA driver initialization is unavailable")
        if not 0 <= device_id < count.value:
            raise DecoderUnavailableError("Selected CUDA device is unavailable")
        if get_device(ctypes.byref(device), device_id) != 0:
            raise DecoderUnavailableError("Selected CUDA device could not be accessed")
        if retain(ctypes.byref(context), device.value) != 0:
            raise DecoderUnavailableError("CUDA primary context is unavailable")
        if release(device.value) != 0:
            raise DecoderUnavailableError("CUDA primary context release failed")
    except (OSError, AttributeError) as exc:
        raise DecoderUnavailableError(
            "CUDA driver API is unavailable; install a compatible NVIDIA driver"
        ) from exc


def _device_unavailable(nvc: Any, error: Exception) -> bool:
    """Recognize explicit native availability diagnostics, never generic errors."""
    unsupported = getattr(nvc, "PyNvVCExceptionUnsupported", None)
    if isinstance(unsupported, type) and isinstance(error, unsupported):
        return True
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "cuda_error_no_device",
            "cuda_error_invalid_device",
            "cuda_error_insufficient_driver",
            "cuda_error_system_driver_mismatch",
            "cuda_error_not_initialized",
            "no cuda-capable device",
            "gpu ordinal out of range",
        )
    )


class PyNvSession:
    """One forward packet stream, with at most one packet's pending outputs."""

    def __init__(
        self,
        nvc: Any,
        decoder: Any,
        demuxer: Any,
        time_num: int,
        time_den: int,
        path: Path,
        options: DecodeOptions,
    ) -> None:
        self.nvc, self.decoder, self.demuxer = nvc, decoder, demuxer
        self.time_num, self.time_den = time_num, time_den
        self.path, self.options = path, options
        self.width, self.height = int(demuxer.Width()), int(demuxer.Height())
        if self.width <= 0 or self.height <= 0:
            raise DecodeError("PyNvVideoCodec reported invalid video dimensions")
        self.codec_id = demuxer.GetNvCodecId()
        self.color_matrix: Literal["bt601", "bt709"] | None = _color_matrix(
            demuxer.ColorSpace()
        )
        self.color_range: Literal["limited", "full"] | None = _color_range(
            demuxer.ColorRange()
        )
        numerator = _positive(demuxer.AvgFrameRateNum())
        denominator = _positive(demuxer.AvgFrameRateDen())
        self._fps = numerator / denominator if numerator and denominator else None
        self.packets: Any = iter(demuxer)
        self.pending: deque[_RawFrame] = deque()
        self.origin_pts: int | None = None
        self.cursor = 0
        self.closed = False
        self.eof = False
        self.positioned = False
        self.initializing = True

    @_serialized
    def probe(self) -> MediaFacts:
        codec = getattr(self.codec_id, "name", self.codec_id)
        return MediaFacts(
            source_id=str(self.path),
            codec=codec.lower() if isinstance(codec, str) else None,
            duration_s=None,
            duration_quality="unknown",
            fps=self._fps,
            frame_rate_mode="unknown",
            width=self.width,
            height=self.height,
            audio=None,
            container=None,
            warnings=(
                "PyNvVideoCodec does not expose verified audio/container stream facts",
                "Exclusive video duration/frame count is unverified; "
                "use an explicit sampling window",
            ),
            frame_count=None,
            timestamp_origin_ns=self._ticks_to_ns(self.origin_pts)
            if self.origin_pts is not None
            else None,
            color_matrix=self.color_matrix,
            color_range=self.color_range,
        )

    @_serialized
    def seek(self, timestamp_ns: int) -> None:
        if self.closed:
            raise DecodeError("PyNvVideoCodec session is already closed")
        # Native time-to-index seeking and scanned metadata do not establish a
        # presentation-frame index. Read from the first PTS and let the facade
        # select a half-open window/targets from observed display-order output.
        if self.positioned:
            record("processing.native.resets", 1, library="processing")
            record(
                "processing.native.discarded_frames",
                len(self.pending),
                library="processing",
            )
            self.pending.clear()
            self.packets = self.decoder = self.demuxer = None
            self.demuxer = self.nvc.CreateDemuxer(filename=str(self.path))
            self.decoder = self.nvc.CreateDecoder(
                **_decoder_arguments(self.nvc, self.codec_id, self.options)
            )
            self.packets = iter(self.demuxer)
            record("processing.native.sessions", 1, library="processing")
            self.cursor, self.eof = 0, False
        self.positioned = True

    @_serialized
    @stage("processing.native.read", library="processing")
    def read_frames(self, batch_size: int) -> list[Any]:
        if self.closed or self.decoder is None:
            raise DecodeError("PyNvVideoCodec session is already closed")
        output: list[_RawFrame] = []
        measured = current_scope() is not None
        decode_ns = copy_ns = produced = flushes = 0
        complete = False
        try:
            while len(output) < batch_size:
                if self.pending:
                    output.append(self.pending.popleft())
                    continue
                if self.eof:
                    break
                try:
                    packet = next(self.packets)
                except StopIteration as exc:
                    raise DecodeError(
                        "PyNvVideoCodec demuxer omitted its EOF flush packet"
                    ) from exc
                self.eof = int(packet.bsl) == 0
                if measured and self.eof:
                    flushes += 1
                try:
                    if not self.eof:
                        pts = getattr(packet, "pts", None)
                        if pts is None or isinstance(pts, bool) or int(pts) == -(2**63):
                            raise TimestampUnavailableError(
                                "PyNvVideoCodec packet has no presentation PTS"
                            )
                    # The terminal zero-length packet flushes delayed display frames.
                    # Demux reuses packet storage: decode before advancing it again.
                    clock = time.perf_counter_ns() if measured else 0
                    try:
                        values = self.decoder.Decode(packet)
                    finally:
                        if measured:
                            decode_ns += time.perf_counter_ns() - clock
                    for value in values:
                        if measured:
                            produced += 1
                        pts = getattr(value, "timestamp", None)
                        if pts is None or isinstance(pts, bool) or int(pts) == -(2**63):
                            raise TimestampUnavailableError(
                                "PyNvVideoCodec supplied no presentation PTS"
                            )
                        pts = int(pts)
                        if self.options.frame_transport == "cuda_ipc":
                            interface = getattr(value, "__cuda_array_interface__", None)
                            shape = tuple(getattr(value, "shape", ()))
                            if not isinstance(interface, dict) or shape != (
                                self.height * 3 // 2,
                                self.width,
                            ):
                                raise DecodeError(
                                    "PyNvVideoCodec returned unsupported device "
                                    "NV12 geometry"
                                )
                            pixels = value
                        else:
                            pixels = np.from_dlpack(value).reshape(tuple(value.shape))
                            if pixels.dtype != np.uint8 or pixels.shape != (
                                self.height,
                                self.width,
                                3,
                            ):
                                raise DecodeError(
                                    "PyNvVideoCodec returned unsupported "
                                    "host RGB geometry"
                                )
                            # A contiguous native array is still a borrowed surface.
                            clock = time.perf_counter_ns() if measured else 0
                            try:
                                pixels = np.array(pixels, copy=True, order="C")
                            finally:
                                if measured:
                                    copy_ns += time.perf_counter_ns() - clock
                        if self.origin_pts is None:
                            self.origin_pts = pts
                        self.pending.append(
                            _RawFrame(
                                pixels, self._ticks_to_ns(pts), self.cursor, "pts"
                            )
                        )
                        self.cursor += 1
                    # Release borrowed frames before the next Decode can recycle them.
                    values = packet = value = None
                except (DecodeError, TimestampUnavailableError):
                    raise
                except Exception as exc:
                    if self.initializing and _device_unavailable(self.nvc, exc):
                        raise DecoderUnavailableError(
                            "PyNvVideoCodec GPU/driver or codec support "
                            "is unavailable; "
                            "check the selected GPU, driver, codec and bit depth"
                        ) from exc
                    raise DecodeError(
                        "PyNvVideoCodec failed while decoding a packet"
                    ) from exc
            complete = True
            return output
        finally:
            if measured:
                for name, value in (
                    ("decode", decode_ns / 1e9),
                    (
                        "copy",
                        None
                        if self.options.frame_transport == "cuda_ipc"
                        else copy_ns / 1e9,
                    ),
                    ("produced_frames", produced),
                    ("delivered_frames", len(output) if complete else 0),
                    ("pending_frames", len(self.pending)),
                    ("flushes", flushes),
                ):
                    record(
                        "processing.native." + name,
                        value,
                        library="processing",
                        status="ok" if complete else "error",
                        availability="not_applicable"
                        if name == "copy" and self.options.frame_transport == "cuda_ipc"
                        else "measured",
                    )

    @_serialized
    def close(self) -> None:
        # All fields owning native objects are cleared on the serialized worker.
        try:
            record(
                "processing.native.discarded_frames",
                len(self.pending),
                library="processing",
            )
            self.pending.clear()
        finally:
            self.packets = None
            self.decoder = None
            self.demuxer = None
            self.closed = True

    def _ticks_to_ns(self, ticks: int) -> int:
        numerator = ticks * self.time_num * 1_000_000_000
        if numerator >= 0:
            return (numerator + self.time_den // 2) // self.time_den
        return -((-numerator + self.time_den // 2) // self.time_den)


class _RawFrame:
    __slots__ = ("tensor", "timestamp_ns", "index", "timestamp_quality")

    def __init__(
        self,
        tensor: Any,
        timestamp_ns: int | None,
        index: int,
        timestamp_quality: str,
    ) -> None:
        self.tensor = tensor
        self.timestamp_ns = timestamp_ns
        self.index = index
        self.timestamp_quality = timestamp_quality


def _decoder_arguments(
    nvc: Any, codec_id: Any, options: DecodeOptions
) -> dict[str, Any]:
    device_output = options.frame_transport == "cuda_ipc"
    if device_output:
        output_type = getattr(nvc.OutputColorType, "NV12", None)
        if output_type is None:
            output_type = getattr(nvc.OutputColorType, "NATIVE", None)
        if output_type is None:
            raise DecoderUnavailableError(
                "PyNvVideoCodec does not expose NV12 device output"
            )
    else:
        output_type = nvc.OutputColorType.RGB
    return {
        "gpuid": options.device_id,
        "codec": codec_id,
        "usedevicememory": device_output,
        "outputColorType": output_type,
        "bWaitForSessionWarmUp": True,
        "latency": nvc.DisplayDecodeLatencyType.NATIVE,
    }


def _positive(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) and result > 0 else None


def _enum_label(value: object) -> str:
    return str(getattr(value, "name", value)).upper().replace("-", "_")


def _color_matrix(value: object) -> Literal["bt601", "bt709"] | None:
    label = _enum_label(value)
    if "709" in label:
        return "bt709"
    if "601" in label:
        return "bt601"
    return None


def _color_range(value: object) -> Literal["limited", "full"] | None:
    label = _enum_label(value)
    if "JPEG" in label or "FULL" in label:
        return "full"
    if "MPEG" in label or "LIMITED" in label:
        return "limited"
    return None
