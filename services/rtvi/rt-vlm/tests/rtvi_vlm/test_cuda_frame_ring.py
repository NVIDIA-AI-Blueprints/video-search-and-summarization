# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import threading

import pytest

from vlm_pipeline.cuda_frame_ring import CudaFrameRing


class FakeFrame:
    def __init__(self, value, nbytes=1):
        self.value = value
        self.nbytes = nbytes


def test_ring_samples_first_frame_at_or_after_each_target_without_copying():
    ring = CudaFrameRing(max_bytes=16)
    frames = [FakeFrame("f0"), FakeFrame("f1"), FakeFrame("f2"), FakeFrame("f3")]
    ring.publish(
        source_id="video-a",
        epoch="v1",
        coverage_start_ns=0,
        coverage_end_ns=4_000,
        pts_ns=[0, 1_000, 2_000, 3_000],
        frames=frames,
    )

    acquired = ring.acquire(
        source_id="video-a",
        epoch="v1",
        start_ns=500,
        end_ns=3_500,
        target_pts_ns=[500, 2_500],
    )

    assert acquired is not None
    selected_frames, selected_pts = acquired
    assert selected_frames == [frames[1], frames[3]]
    assert selected_frames[0] is frames[1]
    assert selected_pts == [1_000, 3_000]


def test_ring_rejects_incomplete_window_and_stale_epoch():
    ring = CudaFrameRing(max_bytes=16)
    ring.publish(
        source_id="video-a",
        epoch="v1",
        coverage_start_ns=1_000,
        coverage_end_ns=4_000,
        pts_ns=[1_000, 2_000, 3_000],
        frames=[FakeFrame("f1"), FakeFrame("f2"), FakeFrame("f3")],
    )

    assert (
        ring.acquire(
            source_id="video-a",
            epoch="v1",
            start_ns=0,
            end_ns=3_000,
            target_pts_ns=[500],
        )
        is None
    )
    assert (
        ring.acquire(
            source_id="video-a",
            epoch="v2",
            start_ns=1_000,
            end_ns=3_000,
            target_pts_ns=[1_500],
        )
        is None
    )


@pytest.mark.parametrize(
    "targets, expected",
    [([0, 50, 100], [0, 100]), ([0, 100, 150], [0, 100]), ([150], [])],
)
def test_ring_consumes_each_available_frame_once_at_eos(targets, expected):
    ring = CudaFrameRing(max_bytes=16)
    frames = [FakeFrame("first"), FakeFrame("last")]
    ring.publish("video", "epoch", 0, 200, [0, 100], frames)

    selected_frames, selected_pts = ring.acquire("video", "epoch", 0, 200, targets)

    assert selected_pts == expected
    assert selected_frames == [frames[index // 100] for index in expected]


def test_ring_preserves_decoded_coverage_before_first_frame():
    ring = CudaFrameRing(max_bytes=16)
    frame = FakeFrame("delayed-first-frame")
    ring.publish("video", "epoch", 0, 200, [70], [frame])

    assert ring.acquire("video", "epoch", 0, 200, [0, 25, 50, 75]) == ([frame], [70])


def test_ring_evicted_prefix_is_not_claimed_as_complete_coverage():
    ring = CudaFrameRing(max_bytes=2)
    ring.publish("video", "epoch", 0, 300, [0, 100, 200], [FakeFrame(i) for i in range(3)])

    assert ring.acquire("video", "epoch", 0, 300, [0]) is None
    assert ring.acquire("video", "epoch", 100, 300, [100, 200])[1] == [100, 200]


def test_ring_index_sampling_does_not_pad_short_clips():
    ring = CudaFrameRing(max_bytes=16)
    frames = [FakeFrame("first"), FakeFrame("last")]
    ring.publish("video", "epoch", 0, 200, [0, 100], frames)

    assert ring.acquire("video", "epoch", 0, 200, target_indices=[0, 0, 1, 1, 2]) == (
        frames,
        [0, 100],
    )


def test_ring_eviction_is_bounded_and_acquired_references_remain_valid():
    ring = CudaFrameRing(max_bytes=3)
    first = FakeFrame("first", nbytes=2)
    ring.publish("video-a", "v1", 0, 1_000, [0], [first])
    acquired = ring.acquire("video-a", "v1", 0, 1_000, [0])
    assert acquired is not None

    ring.publish("video-b", "v1", 0, 1_000, [0], [FakeFrame("second", nbytes=2)])

    assert ring.bytes_used <= ring.max_bytes
    assert ring.acquire("video-a", "v1", 0, 1_000, [0]) is None
    assert acquired[0][0].value == "first"


def test_ring_keeps_streams_isolated_when_pts_are_identical():
    ring = CudaFrameRing(max_bytes=16)
    frame_a = FakeFrame("a")
    frame_b = FakeFrame("b")
    ring.publish("video-a", "v1", 0, 1_000, [0], [frame_a])
    ring.publish("video-b", "v1", 0, 1_000, [0], [frame_b])

    assert ring.acquire("video-a", "v1", 0, 1_000, [0])[0] == [frame_a]
    assert ring.acquire("video-b", "v1", 0, 1_000, [0])[0] == [frame_b]


def test_ring_single_flight_waiter_observes_published_frames():
    ring = CudaFrameRing(max_bytes=16)
    assert ring.claim_fill("video-a", "v1")
    assert not ring.claim_fill("video-a", "v1")

    waiter_result = []

    def wait_for_fill():
        waiter_result.append(ring.wait_for_fill("video-a", "v1", timeout=1.0))

    waiter = threading.Thread(target=wait_for_fill)
    waiter.start()
    ring.publish("video-a", "v1", 0, 1_000, [0], [FakeFrame("ready")])
    waiter.join()

    assert waiter_result == [True]


def test_ring_aborted_fill_wakes_waiters_without_exposing_partial_data():
    ring = CudaFrameRing(max_bytes=16)
    assert ring.claim_fill("video-a", "v1")
    waiter_result = []

    def wait_for_fill():
        waiter_result.append(ring.wait_for_fill("video-a", "v1", timeout=1.0))

    waiter = threading.Thread(target=wait_for_fill)
    waiter.start()
    ring.abort_fill("video-a", "v1")
    waiter.join()

    assert waiter_result == [False]


def test_ring_merges_overlapping_dense_windows_for_rolling_file_chunks():
    ring = CudaFrameRing(max_bytes=16)
    frames = [FakeFrame(f"f{index}") for index in range(6)]
    ring.publish("video-a", "v1", 0, 4_000, [0, 1_000, 2_000, 3_000], frames[:4])
    ring.publish(
        "video-a",
        "v1",
        2_000,
        6_000,
        [2_000, 3_000, 4_000, 5_000],
        frames[2:],
    )

    acquired = ring.acquire("video-a", "v1", 1_000, 6_000, [1_500, 4_500])

    assert acquired is not None
    assert acquired[0] == [frames[2], frames[5]]
    assert acquired[1] == [2_000, 5_000]


def test_append_retains_bounded_live_pts_and_exact_acquisition():
    ring = CudaFrameRing(max_bytes=3)
    frames = [FakeFrame(f"f{index}") for index in range(4)]
    for index, frame in enumerate(frames):
        ring.append("live-a", "connection-1", index * 1_000, frame)

    assert ring.bytes_used == 3
    assert ring.acquire_exact("live-a", "connection-1", [1_000, 3_000]) == (
        [frames[1], frames[3]],
        [1_000, 3_000],
    )
    assert ring.acquire_exact("live-a", "connection-1", [0]) is None


def test_ring_reuses_packed_tensor_for_identical_sampling_contract():
    ring = CudaFrameRing(max_bytes=16)
    ring.publish(
        "video-a",
        "v1",
        0,
        4_000,
        [0, 1_000, 2_000, 3_000],
        [FakeFrame("f0"), FakeFrame("f1"), FakeFrame("f2"), FakeFrame("f3")],
    )
    packed = FakeFrame("packed", nbytes=4)

    assert ring.publish_selection("video-a", "v1", "sample-0-2", packed, [0, 2_000])
    acquired = ring.acquire_selection("video-a", "v1", "sample-0-2")

    assert acquired is not None
    assert acquired[0] is packed
    assert acquired[1] == [0, 2_000]


def test_dense_publish_can_keep_waiters_blocked_until_packed_selection_is_ready():
    ring = CudaFrameRing(max_bytes=16)
    assert ring.claim_fill("video-a", "v1")
    ring.publish(
        "video-a",
        "v1",
        0,
        2_000,
        [0, 1_000],
        [FakeFrame("f0"), FakeFrame("f1")],
        complete_fill=False,
    )

    assert not ring.claim_fill("video-a", "v1")
    packed = FakeFrame("packed")
    ring.publish_selection("video-a", "v1", "sample", packed, [0])

    assert ring.wait_for_fill("video-a", "v1", timeout=0.01)
    assert ring.acquire_selection("video-a", "v1", "sample")[0] is packed


def test_ring_declines_packed_tensor_that_would_exceed_source_budget():
    ring = CudaFrameRing(max_bytes=4)
    ring.publish("video-a", "v1", 0, 2_000, [0, 1_000], [FakeFrame("f0"), FakeFrame("f1")])

    assert not ring.publish_selection(
        "video-a", "v1", "oversized", FakeFrame("packed", nbytes=3), [0]
    )
    assert ring.acquire_selection("video-a", "v1", "oversized") is None
