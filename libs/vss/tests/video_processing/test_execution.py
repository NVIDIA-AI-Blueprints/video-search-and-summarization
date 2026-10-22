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

"""Selection, extraction, timestamps, async responsiveness, and lifetime tests."""

from __future__ import annotations

import asyncio
import gc
import threading
import weakref
from contextlib import aclosing
from typing import Any

import numpy as np
import pytest

from .fakes import MemoryBackend, MemorySession


async def collect(processing: Any, source: Any, **options: Any) -> list[Any]:
    return [
        frame
        async for frame in processing.decode_frames(
            source,
            processing.DecodePlan(),
            options=processing.DecodeOptions(**options),
        )
    ]


async def test_decode_is_lazy_and_returns_async_iterator(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    backend = register()
    stream = processing.decode_frames(
        video_path,
        processing.DecodePlan(),
        options=processing.DecodeOptions(decoder="memory"),
    )
    assert hasattr(stream, "__aiter__")
    assert backend.sessions == []
    async with aclosing(stream):
        assert (await anext(stream)).index == 0


async def test_sequential_decode_preserves_frames_and_closes(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    backend = register()
    frames = await collect(processing, video_path, decoder="memory")
    assert [frame.index for frame in frames] == list(range(10))
    assert [frame.timestamp_ns for frame in frames] == list(backend.timestamps_ns)
    assert all(frame.tensor.dtype == np.uint8 for frame in frames)
    assert all(frame.tensor.shape == (2, 3, 3) for frame in frames)
    assert all(session.closed for session in backend.sessions)
    assert all(session.close_count == 1 for session in backend.sessions)
    assert len({frame.source_id for frame in frames}) == 1


async def test_decode_to_eof_does_not_trust_underestimated_duration(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    backend = register(duration_s=0.5)

    frames = await collect(processing, video_path, decoder="memory")

    assert [frame.timestamp_ns for frame in frames] == list(backend.timestamps_ns)


async def test_decode_window_is_half_open(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    register()
    frames = [
        frame
        async for frame in processing.decode_frames(
            video_path,
            processing.DecodePlan(
                window=processing.TimeWindow(
                    start_ns=200_000_000,
                    end_ns=500_000_000,
                )
            ),
            options=processing.DecodeOptions(decoder="memory"),
        )
    ]
    assert [frame.timestamp_ns for frame in frames] == [
        200_000_000,
        300_000_000,
        400_000_000,
    ]


async def test_empty_sampling_plan_does_not_decode(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    backend = register()
    frames = [
        frame
        async for frame in processing.decode_frames(
            video_path,
            processing.DecodePlan(sample_timestamps_ns=()),
            options=processing.DecodeOptions(decoder="memory"),
        )
    ]
    assert frames == []
    assert all(session.read_count == 0 for session in backend.sessions)


async def test_auto_prefers_gpu_without_opening_cpu(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    gpu = register("pynv", device="gpu")
    cpu = register("opencv")
    frames = await collect(processing, video_path)
    assert frames[0].decoder == "pynv"
    assert frames[0].device == "gpu"
    assert frames[0].fallback_reason is None
    assert gpu.sessions
    assert not cpu.sessions


@pytest.mark.parametrize(
    "reason",
    [
        "dependency missing",
        "no NVIDIA device",
        "driver initialization failed",
        "unsupported codec",
        "unsupported bit depth",
    ],
)
async def test_auto_cpu_fallback_is_reported(
    processing: Any,
    register: Any,
    video_path: Any,
    reason: str,
) -> None:
    register("pynv", device="gpu", unavailable=reason)
    cpu = register("opencv")
    frames = await collect(processing, video_path)
    assert frames[0].decoder == "opencv"
    assert frames[0].device == "cpu"
    assert reason in frames[0].fallback_reason
    assert cpu.sessions[0].closed


async def test_explicit_gpu_never_falls_back(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    register("pynv", device="gpu", unavailable="no NVIDIA device")
    cpu = register("opencv")
    with pytest.raises(processing.DecoderUnavailableError):
        await collect(processing, video_path, decoder="pynv")
    assert cpu.sessions == []


async def test_corrupt_source_is_not_an_availability_failure(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    register("pynv", open_error=processing.DecodeError("corrupt source"))
    cpu = register("opencv")
    with pytest.raises(processing.DecodeError, match="corrupt"):
        await collect(processing, video_path)
    assert cpu.sessions == []


async def test_midstream_failure_does_not_restart_or_switch_decoder(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    gpu = register("pynv", device="gpu", fail_at=1)
    cpu = register("opencv")
    stream = processing.decode_frames(
        video_path,
        processing.DecodePlan(),
        options=processing.DecodeOptions(batch_size=1),
    )
    async with aclosing(stream):
        assert (await anext(stream)).index == 0
        with pytest.raises(processing.DecodeError):
            await anext(stream)
    assert len(gpu.sessions) == 1
    assert gpu.sessions[0].closed
    assert not cpu.sessions


async def test_custom_decoder_and_device_options_are_used(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    backend = register("custom")
    await collect(processing, video_path, decoder="custom", device_id=2)
    assert backend.open_options[0].device_id == 2


def test_registration_rejects_empty_and_duplicate_names(
    processing: Any,
    register: Any,
) -> None:
    backend = register("custom")
    with pytest.raises(ValueError):
        processing.register_decoder("", lambda: backend)
    with pytest.raises(ValueError):
        processing.register_decoder("custom", lambda: backend)
    processing.register_decoder("custom", lambda: backend, replace=True)


async def test_unknown_decoder_and_no_available_decoder_fail_clearly(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    register("opencv", unavailable="OpenCV not installed")
    with pytest.raises(processing.DecoderUnavailableError):
        await collect(processing, video_path, decoder="missing")
    with pytest.raises(processing.DecoderUnavailableError):
        await collect(processing, video_path)


async def test_sparse_extraction_keeps_order_duplicates_and_actual_times(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    backend = register()
    frames = await processing.extract_frames(
        video_path,
        [0.75, 0.15, 0.15],
        source_id="stable-video",
        options=processing.DecodeOptions(decoder="memory"),
    )
    assert [frame.requested_timestamp_ns for frame in frames] == [
        750_000_000,
        150_000_000,
        150_000_000,
    ]
    assert [frame.timestamp_ns for frame in frames] == [
        800_000_000,
        200_000_000,
        200_000_000,
    ]
    assert all(frame.source_id == "stable-video" for frame in frames)
    assert all(session.closed for session in backend.sessions)
    assert not np.shares_memory(frames[1].tensor, frames[2].tensor)


@pytest.mark.parametrize(
    "targets", [[-0.1], [1.0], [1.5], [float("nan")], [float("inf")]]
)
async def test_sparse_extraction_rejects_invalid_or_eof_targets(
    processing: Any,
    register: Any,
    video_path: Any,
    targets: list[float],
) -> None:
    register()
    with pytest.raises(ValueError):
        await processing.extract_frames(
            video_path,
            targets,
            options=processing.DecodeOptions(decoder="memory"),
        )


async def test_sparse_target_after_last_decodable_frame_fails(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    register()
    with pytest.raises(processing.DecodeError):
        await processing.extract_frames(
            video_path,
            [0.95],
            options=processing.DecodeOptions(decoder="memory"),
        )


async def test_empty_extraction_returns_empty_without_decoder_read(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    backend = register()
    assert (
        await processing.extract_frames(
            video_path,
            [],
            options=processing.DecodeOptions(decoder="memory"),
        )
        == []
    )
    assert all(session.read_count == 0 for session in backend.sessions)


async def test_vfr_selection_uses_actual_pts_and_normalizes_origin(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    register(
        timestamps_ns=(0, 40_000_000, 190_000_000, 700_000_000),
        origin_ns=5_000_000_000,
    )
    frames = await processing.extract_frames(
        video_path,
        [0.05, 0.2],
        options=processing.DecodeOptions(decoder="memory"),
    )
    assert [frame.timestamp_ns for frame in frames] == [190_000_000, 700_000_000]
    assert all(frame.timestamp_quality == "pts" for frame in frames)


@pytest.mark.parametrize("mode,raises", [("best_effort", False), ("strict", True)])
async def test_estimated_timestamps_are_marked_or_rejected(
    processing: Any,
    register: Any,
    video_path: Any,
    mode: str,
    raises: bool,
) -> None:
    backend = register(timestamp_quality="estimated")
    if raises:
        with pytest.raises(processing.TimestampUnavailableError):
            await collect(processing, video_path, decoder="memory", timestamp_mode=mode)
    else:
        frames = await collect(
            processing, video_path, decoder="memory", timestamp_mode=mode
        )
        assert all(frame.timestamp_quality == "estimated" for frame in frames)
    assert all(session.closed for session in backend.sessions)


@pytest.mark.parametrize("batch_size", [1, 3])
async def test_recycled_native_buffers_do_not_change_returned_frames(
    processing: Any,
    register: Any,
    video_path: Any,
    batch_size: int,
) -> None:
    register(reuse_buffer=True)
    frames = await collect(
        processing, video_path, decoder="memory", batch_size=batch_size
    )
    for index, frame in enumerate(frames):
        assert np.all(frame.tensor == index)
        assert not frame.tensor.flags.writeable
        with pytest.raises(ValueError):
            frame.tensor[0, 0, 0] = 99
    assert not np.shares_memory(frames[0].tensor, frames[1].tensor)


async def test_slow_consumer_does_not_trigger_full_video_prefetch(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    backend = register(timestamps_ns=tuple(index * 1_000_000 for index in range(1000)))
    stream = processing.decode_frames(
        video_path,
        processing.DecodePlan(),
        options=processing.DecodeOptions(decoder="memory", batch_size=3),
    )
    async with aclosing(stream):
        await anext(stream)
        session = backend.sessions[-1]
        assert session.frames_read <= 3
        reads = session.read_count
        await asyncio.sleep(0.03)
        assert session.read_count == reads
    assert session.closed


async def test_blocking_decode_does_not_block_event_loop(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    backend = register(delay_s=0.2)
    task = asyncio.create_task(collect(processing, video_path, decoder="memory"))
    try:
        await asyncio.wait_for(asyncio.to_thread(backend.read_started.wait), timeout=2)
        # A timer must run while the synthetic native decoder is sleeping.
        await asyncio.wait_for(asyncio.sleep(0.01), timeout=0.1)
        assert not task.done(), "Decoder blocked the event loop until it finished"
    finally:
        await task


async def test_one_session_serializes_all_native_calls_on_one_worker(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    # Set up the isolated registry through the existing fixture, then register
    # a factory whose own thread can be observed independently of open().
    register("unused")
    backend = MemoryBackend(processing)
    factory_threads: list[int] = []

    def factory() -> MemoryBackend:
        factory_threads.append(threading.get_ident())
        return backend

    processing.register_decoder("memory", factory)
    loop_thread = threading.get_ident()
    await processing.extract_frames(
        video_path,
        [0.25],
        options=processing.DecodeOptions(decoder="memory", batch_size=1),
    )
    assert factory_threads
    for session in backend.sessions:
        assert session.thread_ids == {session.open_thread_id}
        assert session.open_thread_id in factory_threads
        assert loop_thread not in session.thread_ids
        assert session.seeks
    assert set(factory_threads) == backend.open_thread_ids
    assert loop_thread not in backend.open_thread_ids


async def test_concurrent_calls_share_the_configured_session_limit(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    backend = register(delay_s=0.02)

    async def run() -> None:
        _ = [
            frame
            async for frame in processing.decode_frames(
                video_path,
                processing.DecodePlan(),
                # Equivalent separately constructed options share one limiter.
                options=processing.DecodeOptions(decoder="memory", max_concurrency=2),
            )
        ]

    await asyncio.wait_for(asyncio.gather(*(run() for _ in range(6))), timeout=5)
    assert backend.max_active_sessions <= 2
    assert backend.active_sessions == 0


async def test_cancel_during_native_read_waits_for_safe_cleanup(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    backend = register(delay_s=0.1)
    task = asyncio.create_task(collect(processing, video_path, decoder="memory"))
    await asyncio.wait_for(asyncio.to_thread(backend.read_started.wait), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert backend.active_sessions == 0
    assert all(session.closed for session in backend.sessions)
    assert all(session.close_count == 1 for session in backend.sessions)


def test_sync_wrappers_work_without_event_loop(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    register("opencv")
    register("memory")
    assert processing.probe_video_sync(video_path).width == 3
    frames = processing.extract_frames_sync(
        video_path,
        [0.1],
        options=processing.DecodeOptions(decoder="memory"),
    )
    assert frames[0].timestamp_ns == 100_000_000


async def test_probe_uses_explicit_decoder_options(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    backend = register("memory")

    facts = await processing.probe_video(
        video_path,
        options=processing.DecodeOptions(decoder="memory"),
    )

    assert facts.width == 3
    assert len(backend.sessions) == 1
    assert backend.sessions[0].closed


async def test_sync_wrappers_reject_running_event_loop(
    processing: Any, video_path: Any
) -> None:
    with pytest.raises(RuntimeError, match="event loop"):
        processing.probe_video_sync(video_path)
    with pytest.raises(RuntimeError, match="event loop"):
        processing.extract_frames_sync(video_path, [0.1])


async def test_auto_reports_both_gpu_and_cpu_unavailability(
    processing: Any, register: Any, video_path: Any
) -> None:
    gpu = register("pynv", unavailable="NVIDIA device unavailable")
    cpu = register("opencv", unavailable="OpenCV dependency not installed")
    with pytest.raises(processing.DecoderUnavailableError) as raised:
        await collect(processing, video_path)
    diagnostic = str(raised.value)
    assert "NVIDIA device unavailable" in diagnostic
    assert "OpenCV dependency not installed" in diagnostic
    assert len(gpu.open_paths) == len(cpu.open_paths) == 1
    assert gpu.active_sessions == cpu.active_sessions == 0


@pytest.mark.parametrize("stage", ["probe", "read"])
@pytest.mark.parametrize("error_type", ["DecoderUnavailableError", "DecodeError"])
async def test_post_open_failures_do_not_trigger_fallback_before_first_frame(
    processing: Any, register: Any, video_path: Any, stage: str, error_type: str
) -> None:
    error = getattr(processing, error_type)(f"failure during {stage}")
    gpu = register("pynv", **{f"{stage}_error": error})
    cpu = register("opencv")
    with pytest.raises(type(error), match=f"failure during {stage}"):
        await collect(processing, video_path)
    assert len(gpu.sessions) == 1
    assert gpu.sessions[0].close_count == 1
    assert not cpu.open_paths


async def test_omitted_options_share_default_concurrency_limit(
    processing: Any, register: Any, video_path: Any
) -> None:
    register("pynv", unavailable="no GPU")
    cpu = register("opencv", delay_s=0.04)

    async def run() -> None:
        _ = [
            frame
            async for frame in processing.decode_frames(
                video_path, processing.DecodePlan()
            )
        ]

    await asyncio.wait_for(asyncio.gather(*(run() for _ in range(9))), timeout=5)
    assert 0 < cpu.max_active_sessions <= 4
    assert cpu.active_sessions == 0


@pytest.mark.parametrize("stage", ["open", "probe", "seek", "read"])
@pytest.mark.parametrize("cancel_count", [1, 2])
async def test_cancellation_waits_for_native_operation_before_closing(
    processing: Any,
    register: Any,
    video_path: Any,
    stage: str,
    cancel_count: int,
) -> None:
    backend = register(block_stage=stage)
    task = asyncio.create_task(
        processing.extract_frames(
            video_path,
            [0.25],
            options=processing.DecodeOptions(decoder="memory", max_concurrency=1),
        )
    )
    try:
        started = await asyncio.wait_for(
            asyncio.to_thread(backend.operation_started[stage].wait, 2), timeout=3
        )
        assert started, f"native {stage} was never reached"
        for _ in range(cancel_count):
            task.cancel()
            # Give cancellation a chance to run while the native operation is
            # held by an explicit event, rather than racing a short native sleep.
            await asyncio.sleep(0.01)
        assert not task.done(), "cancellation returned before the native call finished"
        assert not backend.closed.is_set()
    finally:
        backend.release_operation.set()
        if not task.done() and not task.cancelling():
            task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=3)
    assert task.cancelled()
    assert backend.active_sessions == 0
    assert all(session.close_count == 1 for session in backend.sessions)
    # The same limit must be usable after cancellation; no permit may leak.
    backend.block_stage = None
    frames = await asyncio.wait_for(
        collect(processing, video_path, decoder="memory", max_concurrency=1),
        timeout=2,
    )
    assert len(frames) == 10


async def test_cancelled_permit_waiter_never_opens_and_does_not_leak_capacity(
    processing: Any, register: Any, video_path: Any
) -> None:
    backend = register(block_stage="read")
    first = asyncio.create_task(
        collect(processing, video_path, decoder="memory", max_concurrency=1)
    )
    waiter: asyncio.Task[Any] | None = None
    try:
        assert await asyncio.wait_for(
            asyncio.to_thread(backend.read_started.wait, 2), timeout=3
        )
        waiter = asyncio.create_task(
            collect(processing, video_path, decoder="memory", max_concurrency=1)
        )
        await asyncio.sleep(0.02)
        assert len(backend.open_paths) == 1
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert len(backend.open_paths) == 1
    finally:
        backend.release_operation.set()
        if waiter is not None and not waiter.done():
            waiter.cancel()
        await asyncio.wait_for(
            asyncio.gather(
                first, *([waiter] if waiter is not None else []), return_exceptions=True
            ),
            timeout=3,
        )
    assert first.exception() is None
    backend.block_stage = None
    frames = await asyncio.wait_for(
        collect(processing, video_path, decoder="memory", max_concurrency=1),
        timeout=2,
    )
    assert len(frames) == 10
    assert backend.active_sessions == 0


async def test_probe_failure_releases_limiter_for_next_call(
    processing: Any, register: Any, video_path: Any
) -> None:
    backend = register(probe_error=processing.DecodeError("broken metadata"))
    with pytest.raises(processing.DecodeError):
        await collect(processing, video_path, decoder="memory", max_concurrency=1)
    assert backend.active_sessions == 0
    backend.probe_error = None
    frames = await asyncio.wait_for(
        collect(processing, video_path, decoder="memory", max_concurrency=1), timeout=2
    )
    assert len(frames) == 10


@pytest.mark.parametrize("origin", [-5_000_000_000, 7_000_000_000])
async def test_source_time_zero_is_video_presentation_origin(
    processing: Any, register: Any, video_path: Any, origin: int
) -> None:
    register(origin_ns=origin)
    frames = await collect(processing, video_path, decoder="memory")
    assert [frame.timestamp_ns for frame in frames] == [
        index * 100_000_000 for index in range(10)
    ]


@pytest.mark.parametrize(
    "timestamps", [(0, 200_000_000, 100_000_000), (0, -100_000_000)]
)
@pytest.mark.parametrize("mode", ["best_effort", "strict"])
async def test_backward_or_negative_presentation_times_fail_without_relabeling(
    processing: Any,
    register: Any,
    video_path: Any,
    timestamps: tuple[int, ...],
    mode: str,
) -> None:
    backend = register(timestamps_ns=timestamps)
    with pytest.raises(processing.TimestampUnavailableError):
        await collect(processing, video_path, decoder="memory", timestamp_mode=mode)
    assert backend.active_sessions == 0


async def test_missing_pts_is_estimated_only_with_usable_frame_index_and_fps(
    processing: Any, register: Any, video_path: Any
) -> None:
    register(timestamps_ns=(0, None, 200_000_000))
    frames = await collect(processing, video_path, decoder="memory")
    assert [frame.timestamp_ns for frame in frames] == [0, 100_000_000, 200_000_000]
    assert [frame.timestamp_quality for frame in frames] == ["pts", "estimated", "pts"]


@pytest.mark.parametrize(
    "options,backend_options",
    [
        ({"timestamp_mode": "strict"}, {}),
        ({}, {"fps": None}),
        ({}, {"indices": (0, None, 2)}),
    ],
)
async def test_missing_pts_without_permitted_estimate_raises(
    processing: Any,
    register: Any,
    video_path: Any,
    options: dict[str, Any],
    backend_options: dict[str, Any],
) -> None:
    backend = register(timestamps_ns=(0, None, 200_000_000), **backend_options)
    with pytest.raises(processing.TimestampUnavailableError):
        await collect(processing, video_path, decoder="memory", **options)
    assert backend.active_sessions == 0


async def test_all_extraction_targets_validate_before_first_decoder_read(
    processing: Any, register: Any, video_path: Any
) -> None:
    backend = register()
    with pytest.raises(ValueError):
        await processing.extract_frames(
            video_path,
            [0.2, float("nan")],
            options=processing.DecodeOptions(decoder="memory"),
        )
    assert all(session.read_count == 0 for session in backend.sessions)


async def test_sparse_extraction_stops_after_target_before_unrelated_decode_failure(
    processing: Any, register: Any, video_path: Any
) -> None:
    backend = register(fail_at=2)
    frames = await processing.extract_frames(
        video_path,
        [0.1],
        options=processing.DecodeOptions(decoder="memory", batch_size=1),
    )
    assert [frame.timestamp_ns for frame in frames] == [100_000_000]
    assert sum(session.frames_read for session in backend.sessions) <= 2
    assert backend.active_sessions == 0


async def test_sparse_extraction_seeks_to_first_sorted_target_on_raw_source_clock(
    processing: Any, register: Any, video_path: Any
) -> None:
    backend = register(origin_ns=5_000_000_000)
    frames = await processing.extract_frames(
        video_path,
        [0.85, 0.75],
        options=processing.DecodeOptions(decoder="memory", batch_size=1),
    )
    # The native fake deliberately lands early, so the facade must still read
    # forward and return observed PTS after issuing the correct advisory seek.
    assert any(5_750_000_000 in session.seeks for session in backend.sessions), (
        "sparse extraction should seek to the earliest target, not video time zero"
    )
    assert [frame.timestamp_ns for frame in frames] == [900_000_000, 800_000_000]
    assert [frame.requested_timestamp_ns for frame in frames] == [
        850_000_000,
        750_000_000,
    ]
    assert backend.active_sessions == 0


@pytest.mark.parametrize(
    "policy,duration",
    [
        ({"mode": "all"}, 1.0),
        ({"mode": "count", "count": 2}, 1.0),
        ({"mode": "timestamps", "timestamps_ns": (0, 500_000_000)}, 1.0),
        ({"mode": "fps", "fps": 2.0}, None),
    ],
)
async def test_early_subsampling_aclose_joins_nested_decoder_cleanup(
    processing: Any,
    register: Any,
    video_path: Any,
    policy: dict[str, Any],
    duration: float | None,
) -> None:
    backend = register(duration_s=duration)
    stream = processing.subsample_frames(
        video_path,
        processing.SamplingPolicy(**policy),
        options=processing.DecodeOptions(decoder="memory", batch_size=1),
    )
    async with aclosing(stream):
        assert (await anext(stream)).timestamp_ns == 0
        assert backend.active_sessions == 1
    # No sleep/finalizer turn: returning from aclose must establish closure.
    assert backend.active_sessions == 0
    assert all(session.close_count == 1 for session in backend.sessions)


async def test_early_close_releases_unreturned_batch_without_cyclic_gc(
    processing: Any, register: Any, video_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = register()
    references: list[weakref.ReferenceType[np.ndarray]] = []
    original_read = MemorySession.read_frames

    def tracked_read(session: MemorySession, size: int) -> Any:
        batch = original_read(session, size)
        references.extend(weakref.ref(raw.tensor) for raw in batch)
        return batch

    monkeypatch.setattr(MemorySession, "read_frames", tracked_read)
    gc.collect()
    previously_enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(6):
            async with aclosing(
                processing.decode_frames(
                    video_path,
                    processing.DecodePlan(),
                    options=processing.DecodeOptions(decoder="memory", batch_size=4),
                )
            ) as stream:
                returned = await anext(stream)
            assert backend.active_sessions == 0
            # Keeping the returned owned Frame must not retain hidden batches.
            assert np.all(returned.tensor == 0)
            assert references and all(ref() is None for ref in references)
    finally:
        if previously_enabled:
            gc.enable()
        gc.collect()


@pytest.mark.parametrize("primary_failure", [False, True])
async def test_cleanup_failure_preserves_primary_error_and_releases_capacity(
    processing: Any,
    register: Any,
    video_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    primary_failure: bool,
) -> None:
    backend = register(fail_at=1 if primary_failure else None)
    original_close = MemorySession.close
    fail_close = True

    def failing_close(session: MemorySession) -> None:
        original_close(session)
        if fail_close:
            raise RuntimeError("synthetic close failure")

    monkeypatch.setattr(MemorySession, "close", failing_close)
    message = (
        "synthetic decode failure" if primary_failure else "Decoder cleanup failed"
    )
    with pytest.raises(processing.DecodeError, match=message):
        await collect(
            processing, video_path, decoder="memory", batch_size=1, max_concurrency=1
        )
    assert backend.active_sessions == 0
    assert backend.sessions[0].close_count == 1
    fail_close = False
    backend.fail_at = None
    async with asyncio.timeout(5):
        frames = await collect(
            processing, video_path, decoder="memory", max_concurrency=1
        )
    assert len(frames) == 10
    assert backend.active_sessions == 0
