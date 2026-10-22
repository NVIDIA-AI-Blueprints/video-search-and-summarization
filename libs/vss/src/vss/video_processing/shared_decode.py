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

"""Bounded single-decode fan-out for in-process frame consumers.

The current decoder contract produces owned CPU RGB :class:`Frame` objects,
including when PyNvVideoCodec performs the decode.  This module therefore
implements the lifecycle and backpressure contract using an ``owned_cpu``
transport.  It deliberately does not describe that transport as GPU zero-copy.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import aclosing
from dataclasses import dataclass
from typing import Literal

from .contracts import DecodeOptions, DecodePlan, MediaFacts, SourceInput
from .execution import _decode_stream
from .frames import Frame

_PROTOCOL_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class SharedFrameCapability:
    """A backend's accepted shared-frame input contract."""

    decoder: str
    transport: Literal["owned_cpu", "cuda_ipc"] = "owned_cpu"
    pixel_format: Literal["rgb24", "nv12"] = "rgb24"
    device_id: int | None = None
    max_buffer_depth: int = 8
    live_streams: bool = False
    decoder_agnostic: bool = False
    protocol_version: str = _PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if not self.decoder:
            raise ValueError("decoder must not be empty")
        if self.max_buffer_depth < 1:
            raise ValueError("max_buffer_depth must be positive")

    def compatible_with(self, other: SharedFrameCapability) -> bool:
        """Return whether two consumers can use the same decoded stream."""

        return (
            self.protocol_version == other.protocol_version
            and self.decoder == other.decoder
            and self.transport == other.transport
            and self.pixel_format == other.pixel_format
            and self.device_id == other.device_id
        )


@dataclass(frozen=True, slots=True)
class FrameDescriptor:
    """Ordering and lifetime metadata for a leased decoded frame.

    Handles are opaque tokens, never process pointers.  ``owned_cpu`` leases
    expose the corresponding immutable :class:`Frame` through ``FrameLease``.
    """

    stream_id: str
    sequence: int
    timestamp_ns: int
    width: int
    height: int
    pixel_format: Literal["rgb24", "nv12"]
    transport: Literal["owned_cpu", "cuda_ipc"]
    device: Literal["cpu", "gpu"]
    device_id: int | None
    buffer_slot: int
    slot_generation: int
    memory_handle: str
    ready_event_handle: str | None = None
    protocol_version: str = _PROTOCOL_VERSION


class _FrameState:
    def __init__(
        self,
        frame: Frame,
        descriptor: FrameDescriptor,
        lease_count: int,
    ) -> None:
        self.frame: Frame | None = frame
        self.descriptor = descriptor
        self._remaining = lease_count
        self._lock = asyncio.Lock()

    async def release(self) -> bool:
        async with self._lock:
            if self._remaining == 0:
                return False
            self._remaining -= 1
            if self._remaining == 0:
                self.frame = None
                return True
        return False


class FrameLease:
    """One consumer's idempotently releasable claim on a decoded frame."""

    __slots__ = ("_buffer", "_released", "_state", "descriptor")

    def __init__(self, state: _FrameState, buffer: FrameBuffer) -> None:
        self.descriptor = state.descriptor
        self._state = state
        self._buffer = buffer
        self._released = False

    @property
    def frame(self) -> Frame:
        """Return the immutable owned CPU frame while this lease is live."""

        frame = self._state.frame
        if self._released or frame is None:
            raise RuntimeError("frame lease has been released")
        return frame

    @property
    def released(self) -> bool:
        return self._released

    async def release(self) -> None:
        """Release this claim; repeated calls have no effect."""

        if self._released:
            return
        self._released = True
        self._buffer._forget(self)
        if await self._state.release():
            self._buffer._coordinator._release_slot(self.descriptor)

    async def __aenter__(self) -> FrameLease:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.release()


@dataclass(frozen=True, slots=True)
class _EndOfStream:
    pass


@dataclass(frozen=True, slots=True)
class _DecodeFailure:
    error: BaseException


_END_OF_STREAM = _EndOfStream()
_QueueItem = FrameLease | _EndOfStream | _DecodeFailure


class FrameBuffer:
    """A bounded, ordered, independent cursor over one decoded stream."""

    def __init__(
        self,
        coordinator: SharedDecodeCoordinator,
        consumer_id: str,
        depth: int,
    ) -> None:
        self.consumer_id = consumer_id
        self._coordinator = coordinator
        self._queue: asyncio.Queue[_QueueItem] = asyncio.Queue(maxsize=depth)
        self._leases: set[FrameLease] = set()
        self._closed = False
        self._terminal: _EndOfStream | _DecodeFailure | None = None
        self._terminal_enqueued = False
        self._terminal_received = False
        self._close_lock = asyncio.Lock()
        self._closed_event = asyncio.Event()

    @property
    def depth(self) -> int:
        return self._queue.maxsize

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def duration_s(self) -> float | None:
        """Return the duration reported by the shared decoder session."""

        return self._coordinator.duration_s

    async def receive(self) -> FrameLease:
        """Receive the next lease, or raise at end-of-stream/decode failure."""

        if self._closed or self._terminal_received:
            raise StopAsyncIteration
        if (
            self._terminal is not None
            and not self._terminal_enqueued
            and self._queue.empty()
        ):
            self._terminal_received = True
            if isinstance(self._terminal, _DecodeFailure):
                raise self._terminal.error
            raise StopAsyncIteration
        get_task = asyncio.create_task(self._queue.get())
        close_task = asyncio.create_task(self._closed_event.wait())
        try:
            done, _ = await asyncio.wait(
                (get_task, close_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            get_task.cancel()
            close_task.cancel()
            await asyncio.gather(get_task, close_task, return_exceptions=True)
            if not get_task.cancelled() and get_task.exception() is None:
                cancelled_item = get_task.result()
                if isinstance(cancelled_item, FrameLease):
                    await cancelled_item.release()
            raise
        if close_task in done:
            close_task.result()
            if get_task.done():
                item = get_task.result()
                if isinstance(item, FrameLease):
                    await item.release()
            else:
                get_task.cancel()
                await asyncio.gather(get_task, return_exceptions=True)
            raise StopAsyncIteration
        close_task.cancel()
        await asyncio.gather(close_task, return_exceptions=True)
        item = get_task.result()
        if isinstance(item, FrameLease):
            if self._closed:
                await item.release()
                raise StopAsyncIteration
            self._leases.add(item)
            if (
                self._terminal is not None
                and not self._terminal_enqueued
                and not self._queue.full()
            ):
                self._queue.put_nowait(self._terminal)
                self._terminal_enqueued = True
            return item
        self._terminal_received = True
        if isinstance(item, _DecodeFailure):
            raise item.error
        raise StopAsyncIteration

    def __aiter__(self) -> FrameBuffer:
        return self

    async def __anext__(self) -> FrameLease:
        return await self.receive()

    async def aclose(self) -> None:
        """Detach the consumer and release queued and outstanding leases."""

        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self._closed_event.set()
            self._coordinator._detach(self.consumer_id)
            leases = set(self._leases)
            while True:
                try:
                    item = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if isinstance(item, FrameLease):
                    leases.add(item)
            for lease in leases:
                await lease.release()

    async def _put(self, item: FrameLease) -> bool:
        if self._closed or self._terminal is not None:
            return False
        await self._queue.put(item)
        if self._closed:
            return False
        return True

    def _finish(self, terminal: _EndOfStream | _DecodeFailure) -> None:
        if self._closed or self._terminal is not None:
            return
        self._terminal = terminal
        if not self._queue.full():
            self._queue.put_nowait(terminal)
            self._terminal_enqueued = True

    def _forget(self, lease: FrameLease) -> None:
        self._leases.discard(lease)


class SharedDecodeCoordinator:
    """Decode one source once and fan frames out through bounded buffers."""

    def __init__(
        self,
        source: SourceInput,
        *,
        source_id: str | None = None,
        options: DecodeOptions | None = None,
        plan: DecodePlan | None = None,
        buffer_depth: int = 8,
        slot_count: int | None = None,
    ) -> None:
        if buffer_depth < 1:
            raise ValueError("buffer_depth must be positive")
        resolved_slots = slot_count if slot_count is not None else buffer_depth + 1
        if resolved_slots < 1:
            raise ValueError("slot_count must be positive")
        self._source = source
        self._source_id = source_id
        self._options = options or DecodeOptions()
        self._plan = plan or DecodePlan()
        self._buffer_depth = buffer_depth
        self._buffers: dict[str, FrameBuffer] = {}
        self._state: Literal["new", "running", "finished", "closed"] = "new"
        self._run_task: asyncio.Task[None] | None = None
        self._free_slots: asyncio.Queue[int] = asyncio.Queue()
        for slot in range(resolved_slots):
            self._free_slots.put_nowait(slot)
        self._generations = [0] * resolved_slots
        self._outstanding: set[tuple[int, int]] = set()
        self._all_released = asyncio.Event()
        self._all_released.set()
        self._sequence = 0
        self._facts: MediaFacts | None = None
        self.stream_id = str(uuid.uuid4())

    @property
    def outstanding_lease_frames(self) -> int:
        """Number of decoded frames retained by at least one consumer."""

        return len(self._outstanding)

    @property
    def duration_s(self) -> float | None:
        """Return source duration from the single decoder session, if known."""

        return self._facts.duration_s if self._facts is not None else None

    async def register(
        self,
        consumer_id: str,
        *,
        buffer_depth: int | None = None,
    ) -> FrameBuffer:
        """Register a consumer before decoding starts."""

        if self._state != "new":
            raise RuntimeError("consumers must register before decoding starts")
        if not consumer_id:
            raise ValueError("consumer_id must not be empty")
        if consumer_id in self._buffers:
            raise ValueError(f"consumer {consumer_id!r} is already registered")
        depth = self._buffer_depth if buffer_depth is None else buffer_depth
        if depth < 1:
            raise ValueError("buffer_depth must be positive")
        buffer = FrameBuffer(self, consumer_id, depth)
        self._buffers[consumer_id] = buffer
        return buffer

    async def run(self) -> None:
        """Decode and publish until EOF, failure, cancellation, or no consumers."""

        if self._state != "new":
            raise RuntimeError("shared decode coordinator can only run once")
        if not self._buffers:
            raise RuntimeError("at least one consumer must be registered")
        self._state = "running"
        self._run_task = asyncio.current_task()
        stream = _decode_stream(
            self._source,
            self._plan,
            source_id=self._source_id,
            options=self._options,
            facts_callback=self._capture_facts,
        )
        try:
            async with aclosing(stream):
                async for frame in stream:
                    if not self._active_buffers():
                        break
                    await self._publish(frame)
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            await self._publish_terminal(_DecodeFailure(error))
            raise
        else:
            await self._publish_terminal(_END_OF_STREAM)
        finally:
            if self._state != "closed":
                self._state = "finished"

    async def aclose(self) -> None:
        """Stop decode, detach consumers, and release every retained frame."""

        if self._state == "closed":
            return
        self._state = "closed"
        run_task = self._run_task
        current = asyncio.current_task()
        if run_task is not None and run_task is not current and not run_task.done():
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)
        for buffer in tuple(self._buffers.values()):
            await buffer.aclose()
        await self._all_released.wait()

    async def _publish(self, frame: Frame) -> None:
        buffers = self._active_buffers()
        if not buffers:
            return
        slot = await self._free_slots.get()
        generation = self._generations[slot]
        self._generations[slot] += 1
        sequence = self._sequence
        self._sequence += 1
        descriptor = FrameDescriptor(
            stream_id=self.stream_id,
            sequence=sequence,
            timestamp_ns=frame.timestamp_ns,
            width=int(frame.tensor.shape[1]),
            height=int(frame.tensor.shape[0]),
            pixel_format="rgb24",
            transport="owned_cpu",
            device="cpu",
            device_id=None,
            buffer_slot=slot,
            slot_generation=generation,
            memory_handle=f"{self.stream_id}:{slot}:{generation}",
        )
        state = _FrameState(frame, descriptor, len(buffers))
        self._outstanding.add((slot, generation))
        self._all_released.clear()
        leases = [FrameLease(state, buffer) for buffer in buffers]
        _index = 0
        try:
            for _index, (buffer, lease) in enumerate(zip(buffers, leases, strict=True)):
                if not await buffer._put(lease):
                    await lease.release()
        except BaseException:
            for lease in leases[_index:]:
                await lease.release()
            raise

    async def _publish_terminal(self, terminal: _EndOfStream | _DecodeFailure) -> None:
        for buffer in self._active_buffers():
            buffer._finish(terminal)

    def _active_buffers(self) -> list[FrameBuffer]:
        return [buffer for buffer in self._buffers.values() if not buffer.closed]

    def _detach(self, consumer_id: str) -> None:
        self._buffers.pop(consumer_id, None)

    def _capture_facts(self, facts: MediaFacts) -> None:
        self._facts = facts

    def _release_slot(self, descriptor: FrameDescriptor) -> None:
        key = (descriptor.buffer_slot, descriptor.slot_generation)
        if key not in self._outstanding:
            return
        self._outstanding.remove(key)
        self._free_slots.put_nowait(descriptor.buffer_slot)
        if not self._outstanding:
            self._all_released.set()


def shared_capabilities_match(
    capabilities: list[SharedFrameCapability | None],
) -> SharedFrameCapability | None:
    """Return the common capability, or ``None`` when sharing is impossible."""

    if not capabilities or any(capability is None for capability in capabilities):
        return None
    common = capabilities[0]
    assert common is not None
    if all(
        capability is not None and common.compatible_with(capability)
        for capability in capabilities[1:]
    ):
        return common
    return None
