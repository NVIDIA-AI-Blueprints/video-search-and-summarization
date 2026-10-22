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

"""Optional same-host CUDA IPC frame publisher.

The native bridge is intentionally loaded lazily.  Installing or importing VSS
does not require CUDA, GStreamer, or DeepStream unless this transport is
selected.
"""

from __future__ import annotations

import ctypes
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .contracts import DecodeOptions, MediaFacts, SourceInput
from .exceptions import DecoderUnavailableError, ProcessingError

_DEFAULT_LIBRARY = "libvss_cuda_ipc_broker.so"


@dataclass(frozen=True, slots=True)
class CudaDeviceFrame:
    """A borrowed NV12 CUDA frame retained by ``owner``."""

    pointer: int
    width: int
    height: int
    pitch: int
    timestamp_ns: int
    index: int | None
    owner: Any
    producer_stream: int | None

    @classmethod
    def from_cuda_array(
        cls,
        value: Any,
        *,
        width: int,
        height: int,
        timestamp_ns: int,
        index: int | None,
    ) -> CudaDeviceFrame:
        interface = getattr(value, "__cuda_array_interface__", None)
        if not isinstance(interface, dict):
            raise ProcessingError(
                "PyNvVideoCodec device frame has no CUDA array interface"
            )
        data = interface.get("data")
        shape = interface.get("shape")
        typestr = interface.get("typestr")
        if (
            not isinstance(data, tuple)
            or not data
            or not isinstance(data[0], int)
            or data[0] <= 0
            or typestr not in {"|u1", "<u1", ">u1"}
            or not isinstance(shape, tuple | list)
            or tuple(shape) != (height * 3 // 2, width)
        ):
            raise ProcessingError("unsupported CUDA frame geometry")
        strides = interface.get("strides")
        pitch = width
        if isinstance(strides, tuple | list) and strides:
            pitch = int(strides[0])
        if pitch < width:
            raise ProcessingError("CUDA frame pitch is smaller than its width")
        producer_stream = interface.get("stream")
        if producer_stream is not None and (
            not isinstance(producer_stream, int)
            or isinstance(producer_stream, bool)
            or producer_stream <= 0
        ):
            raise ProcessingError("CUDA frame producer stream is invalid")
        return cls(
            data[0],
            width,
            height,
            pitch,
            timestamp_ns,
            index,
            value,
            producer_stream,
        )


@dataclass(frozen=True, slots=True)
class CudaIpcFrameSource:
    """JSON payload accepted by RTVI shared-decode stream APIs."""

    control_socket: str
    session_token: str
    consumer_id: str
    device_id: int
    timestamp_origin_ns: int | None = None
    duration_ns: int | None = None
    color_matrix: str | None = None
    color_range: str | None = None
    type: str = "shared_decode"
    protocol_version: str = "1.0"
    transport: str = "cuda_ipc"

    def as_dict(self) -> dict[str, str | int | None]:
        payload: dict[str, str | int | None] = {
            "type": self.type,
            "protocol_version": self.protocol_version,
            "control_socket": self.control_socket,
            "session_token": self.session_token,
            "consumer_id": self.consumer_id,
            "transport": self.transport,
            "device_id": self.device_id,
            "timestamp_origin_ns": self.timestamp_origin_ns,
            "duration_ns": self.duration_ns,
        }
        if self.color_matrix is not None:
            payload["color_matrix"] = self.color_matrix
        if self.color_range is not None:
            payload["color_range"] = self.color_range
        return payload


class _NativeBridge:
    def __init__(self) -> None:
        library_name = os.getenv(
            "VSS_CUDA_IPC_BROKER_LIBRARY", _DEFAULT_LIBRARY
        ).strip()
        try:
            self.library = ctypes.CDLL(library_name)
        except OSError as error:
            raise DecoderUnavailableError(
                "CUDA IPC transport requires libvss_cuda_ipc_broker.so from "
                "the VSS DeepStream optional package"
            ) from error
        self.library.vss_cuda_ipc_broker_create.argtypes = [
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_char),
            ctypes.c_size_t,
        ]
        self.library.vss_cuda_ipc_broker_create.restype = ctypes.c_void_p
        self.library.vss_cuda_ipc_broker_publish.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.c_size_t,
            ctypes.c_uint64,
            ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_char),
            ctypes.c_size_t,
        ]
        self.library.vss_cuda_ipc_broker_publish.restype = ctypes.c_int
        self.library.vss_cuda_ipc_broker_eos.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_char),
            ctypes.c_size_t,
        ]
        self.library.vss_cuda_ipc_broker_eos.restype = ctypes.c_int
        self.library.vss_cuda_ipc_broker_stop.argtypes = [ctypes.c_void_p]
        self.library.vss_cuda_ipc_broker_stop.restype = None
        self.library.vss_cuda_ipc_broker_destroy.argtypes = [ctypes.c_void_p]
        self.library.vss_cuda_ipc_broker_destroy.restype = None


class CudaIpcBroker:
    """Bounded NV12 publisher backed by DeepStream ``nvunixfdsink``."""

    def __init__(
        self,
        socket_path: str | Path,
        *,
        width: int,
        height: int,
        device_id: int,
        slot_count: int = 9,
        session_token: str | None = None,
        color_matrix: str = "bt601",
        color_range: str = "limited",
    ) -> None:
        path = Path(socket_path)
        if not path.is_absolute():
            raise ValueError("CUDA IPC control socket must be absolute")
        if width < 1 or height < 2 or height % 2:
            raise ValueError("NV12 dimensions must be positive with even height")
        if slot_count < 2:
            raise ValueError("CUDA IPC slot_count must be at least two")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.control_socket = str(path)
        self.session_token = session_token or secrets.token_urlsafe(32)
        self.device_id = device_id
        self.width = width
        self.height = height
        if color_matrix not in {"bt601", "bt709"}:
            raise ValueError("color_matrix must be bt601 or bt709")
        if color_range not in {"limited", "full"}:
            raise ValueError("color_range must be limited or full")
        self.color_matrix = color_matrix
        self.color_range = color_range
        self._native = _NativeBridge()
        error = ctypes.create_string_buffer(1024)
        handle = self._native.library.vss_cuda_ipc_broker_create(
            os.fsencode(path),
            device_id,
            width,
            height,
            slot_count,
            709 if color_matrix == "bt709" else 601,
            1 if color_range == "full" else 0,
            error,
            len(error),
        )
        if not handle:
            raise ProcessingError(
                error.value.decode(errors="replace")
                or "could not create CUDA IPC broker"
            )
        self._handle: int | None = handle

    def frame_source(self, consumer_id: str) -> CudaIpcFrameSource:
        if not consumer_id:
            raise ValueError("consumer_id must not be empty")
        return CudaIpcFrameSource(
            control_socket=self.control_socket,
            session_token=self.session_token,
            consumer_id=consumer_id,
            device_id=self.device_id,
            color_matrix=self.color_matrix,
            color_range=self.color_range,
        )

    def publish(self, frame: CudaDeviceFrame) -> None:
        handle = self._require_open()
        if frame.width != self.width or frame.height != self.height:
            raise ProcessingError("CUDA IPC frame geometry changed during a stream")
        error = ctypes.create_string_buffer(1024)
        result = self._native.library.vss_cuda_ipc_broker_publish(
            handle,
            frame.pointer,
            frame.pitch,
            frame.timestamp_ns,
            frame.producer_stream or 0,
            error,
            len(error),
        )
        if result:
            raise ProcessingError(
                error.value.decode(errors="replace") or "CUDA IPC frame publish failed"
            )

    def finish(self) -> None:
        handle = self._require_open()
        error = ctypes.create_string_buffer(1024)
        result = self._native.library.vss_cuda_ipc_broker_eos(handle, error, len(error))
        if result:
            raise ProcessingError(
                error.value.decode(errors="replace") or "CUDA IPC end-of-stream failed"
            )

    def close(self) -> None:
        if self._handle is None:
            return
        self.stop()
        self._native.library.vss_cuda_ipc_broker_destroy(self._handle)
        self._handle = None

    def stop(self) -> None:
        """Wake blocked publishers and stop accepting frames without freeing storage."""

        if self._handle is not None:
            self._native.library.vss_cuda_ipc_broker_stop(self._handle)

    def _require_open(self) -> int:
        if self._handle is None:
            raise RuntimeError("CUDA IPC broker is closed")
        return self._handle

    def __enter__(self) -> CudaIpcBroker:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class CudaIpcDecodeSession:
    """One PyNv device decode feeding one multi-client CUDA IPC publisher."""

    def __init__(
        self,
        source: SourceInput,
        *,
        source_id: str,
        socket_path: str | Path,
        device_id: int = 0,
        slot_count: int = 9,
        broker_factory: Callable[..., CudaIpcBroker] = CudaIpcBroker,
    ) -> None:
        self._source = source
        self._source_id = source_id
        self._socket_path = socket_path
        self._device_id = device_id
        self._slot_count = slot_count
        self._broker_factory = broker_factory
        self._opened: Any | None = None
        self._broker: CudaIpcBroker | None = None
        self.facts: MediaFacts | None = None

    async def prepare(self) -> None:
        if self._opened is not None:
            raise RuntimeError("CUDA IPC decode session is already prepared")
        from .execution import _open
        from .sources import resolve_source

        resolved = resolve_source(self._source, self._source_id)
        options = DecodeOptions(
            decoder="pynv",
            device_id=self._device_id,
            frame_transport="cuda_ipc",
            batch_size=1,
        )
        opened = await _open(resolved, options)
        try:
            if opened.facts.width is None or opened.facts.height is None:
                raise ProcessingError("decoder did not report CUDA frame dimensions")
            broker = self._broker_factory(
                self._socket_path,
                width=opened.facts.width,
                height=opened.facts.height,
                device_id=self._device_id,
                slot_count=self._slot_count,
                color_matrix=_required_color_fact(
                    opened.facts.color_matrix,
                    "matrix",
                ),
                color_range=_required_color_fact(
                    opened.facts.color_range,
                    "range",
                ),
            )
        except BaseException:
            from .execution import _close

            await _close(opened, True)
            raise
        self._opened = opened
        self._broker = broker
        self.facts = opened.facts

    def frame_source(self, consumer_id: str) -> CudaIpcFrameSource:
        if self._broker is None:
            raise RuntimeError("CUDA IPC decode session is not prepared")
        source = self._broker.frame_source(consumer_id)
        duration_ns = (
            None
            if self.facts is None or self.facts.duration_s is None
            else round(self.facts.duration_s * 1_000_000_000)
        )
        return replace(
            source,
            timestamp_origin_ns=(
                None if self.facts is None else self.facts.timestamp_origin_ns
            ),
            duration_ns=duration_ns,
            color_matrix=None if self.facts is None else self.facts.color_matrix,
            color_range=None if self.facts is None else self.facts.color_range,
        )

    def stop(self) -> None:
        """Stop publication so a blocked native worker can settle."""

        if self._broker is not None:
            self._broker.stop()

    async def run(self) -> None:
        if self._opened is None or self._broker is None or self.facts is None:
            raise RuntimeError("CUDA IPC decode session is not prepared")
        from .execution import _native

        opened = self._opened
        origin = self.facts.timestamp_origin_ns or 0
        await _native(opened.worker, opened.session.seek, origin)
        last_timestamp = -1
        try:
            while True:
                published = await _native(
                    opened.worker,
                    _read_and_publish_one,
                    opened.session,
                    self._broker,
                    origin,
                )
                if published is None:
                    break
                if published < last_timestamp:
                    raise ProcessingError("decoded CUDA timestamps moved backwards")
                last_timestamp = published
            await _native(opened.worker, self._broker.finish)
        except BaseException:
            raise

    async def aclose(self, *, failed: bool = False) -> None:
        opened, broker = self._opened, self._broker
        self._opened = None
        self._broker = None
        if broker is not None:
            broker.close()
        if opened is not None:
            from .execution import _close

            await _close(opened, failed)

    async def __aenter__(self) -> CudaIpcDecodeSession:
        await self.prepare()
        return self

    async def __aexit__(self, exc_type: object, *_: object) -> None:
        await self.aclose(failed=exc_type is not None)


def _read_and_publish_one(
    decoder_session: Any,
    broker: CudaIpcBroker,
    timestamp_origin_ns: int,
) -> int | None:
    frames = decoder_session.read_frames(1)
    if not frames:
        return None
    raw = frames[0]
    if raw.timestamp_ns is None:
        raise ProcessingError("CUDA IPC decode requires presentation timestamps")
    timestamp_ns = raw.timestamp_ns - timestamp_origin_ns
    frame = CudaDeviceFrame.from_cuda_array(
        raw.tensor,
        width=decoder_session.width,
        height=decoder_session.height,
        timestamp_ns=timestamp_ns,
        index=raw.index,
    )
    broker.publish(frame)
    return timestamp_ns


def _required_color_fact(value: str | None, name: str) -> str:
    if value is None:
        raise ProcessingError(f"CUDA IPC decode requires source color {name} metadata")
    return value
