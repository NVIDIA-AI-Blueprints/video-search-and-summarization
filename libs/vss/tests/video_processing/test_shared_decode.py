# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared decode ordering, ownership, backpressure, and cleanup tests."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import pytest

from vss.video_processing.shared_decode import SharedDecodeCoordinator


async def _consume(buffer: Any) -> tuple[list[int], list[int]]:
    sequences: list[int] = []
    frame_ids: list[int] = []
    async for lease in buffer:
        sequences.append(lease.descriptor.sequence)
        frame_ids.append(id(lease.frame))
        await lease.release()
    await buffer.aclose()
    return sequences, frame_ids


async def test_shared_decode_opens_one_session_and_fans_out_in_order(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    backend = register()
    coordinator = SharedDecodeCoordinator(
        video_path,
        options=processing.DecodeOptions(decoder="memory", batch_size=1),
        buffer_depth=2,
    )
    first = await coordinator.register("caption")
    second = await coordinator.register("embedding")

    first_result, second_result, _ = await asyncio.gather(
        _consume(first),
        _consume(second),
        coordinator.run(),
    )
    await coordinator.aclose()

    expected = list(range(10))
    assert first_result[0] == expected
    assert second_result[0] == expected
    assert first_result[1] == second_result[1]
    assert len(backend.sessions) == 1
    assert backend.sessions[0].closed
    assert coordinator.duration_s == 1.0
    assert coordinator.outstanding_lease_frames == 0


async def test_slow_consumer_applies_bounded_backpressure(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    backend = register(timestamps_ns=(0, 100_000_000, 200_000_000))
    coordinator = SharedDecodeCoordinator(
        video_path,
        options=processing.DecodeOptions(decoder="memory", batch_size=1),
        buffer_depth=1,
        slot_count=1,
    )
    fast = await coordinator.register("fast")
    slow = await coordinator.register("slow")
    run_task = asyncio.create_task(coordinator.run())

    fast_first = await fast.receive()
    slow_first = await slow.receive()
    await fast_first.release()
    await asyncio.sleep(0)

    assert not run_task.done()
    assert coordinator.outstanding_lease_frames == 1
    # The decoder may read one frame ahead before blocking on the occupied
    # slot. Thread scheduling differs across event loops, but it must not
    # consume the complete source while the slow lease is held.
    assert backend.sessions[0].frames_read < 3

    await slow_first.release()
    fast_second, slow_second = await asyncio.gather(
        fast.receive(),
        slow.receive(),
    )
    assert fast_second.descriptor.sequence == 1
    assert slow_second.descriptor.sequence == 1
    assert fast_second.descriptor.buffer_slot == fast_first.descriptor.buffer_slot
    assert (
        fast_second.descriptor.slot_generation
        == fast_first.descriptor.slot_generation + 1
    )
    await fast_first.release()
    assert coordinator.outstanding_lease_frames == 1
    await fast_second.release()
    await fast_second.release()
    await slow_second.release()
    await fast.aclose()
    await slow.aclose()
    await run_task
    await coordinator.aclose()
    assert coordinator.outstanding_lease_frames == 0


async def test_full_buffer_drains_frames_before_saved_end_of_stream(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    register(timestamps_ns=(0, 100_000_000), duration_s=0.2)
    coordinator = SharedDecodeCoordinator(
        video_path,
        options=processing.DecodeOptions(decoder="memory", batch_size=1),
        buffer_depth=1,
        slot_count=2,
    )
    buffer = await coordinator.register("slow")
    run_task = asyncio.create_task(coordinator.run())
    first = await buffer.receive()

    await run_task
    second = await buffer.receive()

    assert first.descriptor.sequence == 0
    assert second.descriptor.sequence == 1
    await first.release()
    await second.release()
    with pytest.raises(StopAsyncIteration):
        await buffer.receive()
    await buffer.aclose()
    await coordinator.aclose()


async def test_detach_releases_queued_and_outstanding_leases(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    backend = register(delay_s=0.001)
    coordinator = SharedDecodeCoordinator(
        video_path,
        options=processing.DecodeOptions(decoder="memory", batch_size=1),
        buffer_depth=2,
    )
    buffer = await coordinator.register("cancelled-consumer")
    run_task = asyncio.create_task(coordinator.run())
    lease = await buffer.receive()

    await buffer.aclose()
    await run_task
    await lease.release()
    await coordinator.aclose()

    assert backend.sessions[0].closed
    assert backend.sessions[0].frames_read < 10
    assert coordinator.outstanding_lease_frames == 0


async def test_close_wakes_receiver_waiting_on_empty_buffer(
    processing: Any,
    video_path: Any,
) -> None:
    coordinator = SharedDecodeCoordinator(
        video_path,
        options=processing.DecodeOptions(decoder="memory"),
    )
    buffer = await coordinator.register("waiting-consumer")
    receiver = asyncio.create_task(buffer.receive())
    await asyncio.sleep(0)

    await buffer.aclose()

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(receiver, timeout=1)
    await coordinator.aclose()


async def test_decode_failure_reaches_every_consumer(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    backend = register(fail_at=1)
    coordinator = SharedDecodeCoordinator(
        video_path,
        options=processing.DecodeOptions(decoder="memory", batch_size=1),
    )
    first = await coordinator.register("caption")
    second = await coordinator.register("embedding")

    async def consume_failure(buffer: Any) -> BaseException:
        lease = await buffer.receive()
        await lease.release()
        with pytest.raises(processing.DecodeError) as caught:
            await buffer.receive()
        await buffer.aclose()
        return caught.value

    results = await asyncio.gather(
        consume_failure(first),
        consume_failure(second),
        coordinator.run(),
        return_exceptions=True,
    )
    await coordinator.aclose()

    assert isinstance(results[2], processing.DecodeError)
    assert results[0] is results[2]
    assert results[1] is results[2]
    assert backend.sessions[0].closed
    assert coordinator.outstanding_lease_frames == 0


async def test_cancelling_coordinator_closes_decoder_and_releases_leases(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    backend = register(block_stage="read")
    coordinator = SharedDecodeCoordinator(
        video_path,
        options=processing.DecodeOptions(decoder="memory", batch_size=1),
    )
    buffer = await coordinator.register("consumer")
    run_task = asyncio.create_task(coordinator.run())
    await asyncio.to_thread(backend.read_started.wait, 2)

    run_task.cancel()
    backend.release_operation.set()
    with suppress(asyncio.CancelledError):
        await run_task
    await coordinator.aclose()

    assert buffer.closed
    assert backend.sessions[0].closed
    assert coordinator.outstanding_lease_frames == 0
