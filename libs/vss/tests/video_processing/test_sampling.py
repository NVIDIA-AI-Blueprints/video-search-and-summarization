# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""RT-VLM target-generation parity and explicit half-open window semantics."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError


@pytest.mark.parametrize(
    "data",
    [
        {"start_ns": -1},
        {"start_ns": 10, "end_ns": 10},
        {"start_ns": 10, "end_ns": 9},
        {"start_ns": 0, "unexpected": True},
    ],
)
def test_time_window_rejects_invalid_bounds(
    processing: Any, data: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        processing.TimeWindow(**data)


def test_open_ended_window_and_empty_plan_are_distinct(processing: Any) -> None:
    window = processing.TimeWindow(start_ns=0)
    assert window.end_ns is None
    assert processing.DecodePlan(window=window).sample_timestamps_ns is None
    assert (
        processing.DecodePlan(
            window=window, sample_timestamps_ns=()
        ).sample_timestamps_ns
        == ()
    )


@pytest.mark.parametrize("targets", [(0, 1_000_000_000), (-1,), (300, 100)])
def test_decode_plan_rejects_outside_or_unordered_targets(
    processing: Any,
    targets: tuple[int, ...],
) -> None:
    with pytest.raises(ValidationError):
        processing.DecodePlan(
            window=processing.TimeWindow(start_ns=0, end_ns=1_000_000_000),
            sample_timestamps_ns=targets,
        )


@pytest.mark.parametrize(
    "data",
    [
        {"mode": "count", "count": 0},
        {"mode": "count", "count": 1.5},
        {"mode": "fps", "fps": 0},
        {"mode": "fps", "fps": float("inf")},
        {"mode": "fps", "fps": float("nan")},
        {"mode": "count", "count": 8, "fps": 1},
        {"mode": "all", "count": 8},
        {"mode": "fps", "fps": 1, "allow_missing_timestamps": True},
        {"mode": "other"},
    ],
)
def test_sampling_policy_is_unambiguous(
    processing: Any, data: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        processing.SamplingPolicy(**data)


@pytest.mark.parametrize(
    "policy,expected_requests,expected_actual",
    [
        (
            {"mode": "count", "count": 4},
            [0, 250_000_000, 500_000_000, 750_000_000],
            [0, 300_000_000, 500_000_000, 800_000_000],
        ),
        ({"mode": "fps", "fps": 2.5}, [0, 500_000_000], [0, 500_000_000]),
        ({"mode": "fps", "fps": 0.2}, [0], [0]),
    ],
)
async def test_sampling_matches_rtvlm_target_generation(
    processing: Any,
    register: Any,
    video_path: Any,
    policy: dict[str, object],
    expected_requests: list[int],
    expected_actual: list[int],
) -> None:
    register()
    frames = [
        frame
        async for frame in processing.subsample_frames(
            video_path,
            processing.SamplingPolicy(**policy),
            options=processing.DecodeOptions(decoder="memory"),
        )
    ]
    assert [frame.requested_timestamp_ns for frame in frames] == expected_requests
    assert [frame.timestamp_ns for frame in frames] == expected_actual


async def test_sampling_uses_window_start_not_zero(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    register()
    frames = [
        frame
        async for frame in processing.subsample_frames(
            video_path,
            processing.SamplingPolicy(mode="count", count=2),
            window=processing.TimeWindow(start_ns=200_000_000, end_ns=800_000_000),
            options=processing.DecodeOptions(decoder="memory"),
        )
    ]
    assert [frame.timestamp_ns for frame in frames] == [200_000_000, 500_000_000]


async def test_generated_timestamp_grid_can_allow_missing_final_target(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    register(timestamps_ns=(0, 400_000_000, 800_000_000), duration_s=1.0)
    options = processing.DecodeOptions(decoder="memory")
    targets = (800_000_000, 875_000_000)

    strict_stream = processing.subsample_frames(
        video_path,
        processing.SamplingPolicy(mode="timestamps", timestamps_ns=targets),
        options=options,
    )
    with pytest.raises(processing.DecodeError, match="875000000"):
        _ = [frame async for frame in strict_stream]

    frames = [
        frame
        async for frame in processing.subsample_frames(
            video_path,
            processing.SamplingPolicy(
                mode="timestamps",
                timestamps_ns=targets,
                allow_missing_timestamps=True,
            ),
            options=options,
        )
    ]
    assert [frame.timestamp_ns for frame in frames] == [800_000_000]


async def test_many_targets_select_each_physical_frame_once(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    register(timestamps_ns=(0, 500_000_000))
    frames = [
        frame
        async for frame in processing.subsample_frames(
            video_path,
            processing.SamplingPolicy(mode="count", count=10),
            options=processing.DecodeOptions(decoder="memory"),
        )
    ]
    assert [frame.timestamp_ns for frame in frames] == [0, 500_000_000]


async def test_count_requires_known_duration_but_all_frames_does_not(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    register(duration_s=None)
    options = processing.DecodeOptions(decoder="memory")
    with pytest.raises(processing.ProcessingError, match="duration"):
        _ = [
            frame
            async for frame in processing.subsample_frames(
                video_path,
                processing.SamplingPolicy(mode="count", count=2),
                options=options,
            )
        ]
    frames = [
        frame
        async for frame in processing.subsample_frames(
            video_path,
            processing.SamplingPolicy(mode="all"),
            options=options,
        )
    ]
    assert len(frames) == 10


async def test_unknown_duration_allows_online_fps_sampling(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    register(duration_s=None)
    frames = [
        frame
        async for frame in processing.subsample_frames(
            video_path,
            processing.SamplingPolicy(mode="fps", fps=2),
            options=processing.DecodeOptions(decoder="memory"),
        )
    ]
    assert [frame.timestamp_ns for frame in frames] == [0, 500_000_000]


async def test_caller_defined_sampling_uses_explicit_targets(
    processing: Any,
    register: Any,
    video_path: Any,
) -> None:
    register()
    frames = [
        frame
        async for frame in processing.subsample_frames(
            video_path,
            processing.SamplingPolicy(
                mode="timestamps",
                timestamps_ns=(150_000_000, 750_000_000),
            ),
            options=processing.DecodeOptions(decoder="memory"),
        )
    ]
    assert [frame.timestamp_ns for frame in frames] == [200_000_000, 800_000_000]


async def test_count_uses_explicit_window_when_source_duration_is_unknown(
    processing: Any, register: Any, video_path: Any
) -> None:
    register(duration_s=None)
    frames = [
        frame
        async for frame in processing.subsample_frames(
            video_path,
            processing.SamplingPolicy(mode="count", count=3),
            window=processing.TimeWindow(start_ns=200_000_000, end_ns=800_000_000),
            options=processing.DecodeOptions(decoder="memory"),
        )
    ]
    assert [frame.requested_timestamp_ns for frame in frames] == [
        200_000_000,
        400_000_000,
        600_000_000,
    ]
    assert [frame.timestamp_ns for frame in frames] == [
        200_000_000,
        400_000_000,
        600_000_000,
    ]


@pytest.mark.parametrize(
    "duration,targets",
    [(1.0, [0, 500_000_000]), (None, [0, 400_000_000, 800_000_000])],
)
async def test_known_duration_fps_spacing_differs_explicitly_from_online_grid(
    processing: Any,
    register: Any,
    video_path: Any,
    duration: float | None,
    targets: list[int],
) -> None:
    register(duration_s=duration)
    frames = [
        frame
        async for frame in processing.subsample_frames(
            video_path,
            processing.SamplingPolicy(mode="fps", fps=2.5),
            options=processing.DecodeOptions(decoder="memory"),
        )
    ]
    assert [frame.requested_timestamp_ns for frame in frames] == targets
    assert [frame.timestamp_ns for frame in frames] == targets


async def test_online_fps_grid_is_anchored_to_requested_window_start(
    processing: Any, register: Any, video_path: Any
) -> None:
    register(duration_s=None)
    frames = [
        frame
        async for frame in processing.subsample_frames(
            video_path,
            processing.SamplingPolicy(mode="fps", fps=2.5),
            window=processing.TimeWindow(start_ns=150_000_000),
            options=processing.DecodeOptions(decoder="memory"),
        )
    ]
    assert [frame.requested_timestamp_ns for frame in frames] == [
        150_000_000,
        550_000_000,
    ]
    assert [frame.timestamp_ns for frame in frames] == [200_000_000, 600_000_000]


async def test_sampling_never_returns_frame_at_or_after_exclusive_window_end(
    processing: Any, register: Any, video_path: Any
) -> None:
    register(timestamps_ns=(0, 600_000_000, 900_000_000))
    frames = [
        frame
        async for frame in processing.subsample_frames(
            video_path,
            processing.SamplingPolicy(mode="count", count=4),
            window=processing.TimeWindow(start_ns=0, end_ns=600_000_000),
            options=processing.DecodeOptions(decoder="memory"),
        )
    ]
    assert [frame.timestamp_ns for frame in frames] == [0]


@pytest.mark.parametrize("api", ["decode", "subsample"])
async def test_explicit_targets_fail_when_only_frame_is_outside_window(
    processing: Any, register: Any, video_path: Any, api: str
) -> None:
    backend = register(timestamps_ns=(0, 600_000_000, 900_000_000))
    window = processing.TimeWindow(start_ns=0, end_ns=600_000_000)
    options = processing.DecodeOptions(decoder="memory")
    if api == "decode":
        stream = processing.decode_frames(
            video_path,
            processing.DecodePlan(window=window, sample_timestamps_ns=(550_000_000,)),
            options=options,
        )
    else:
        stream = processing.subsample_frames(
            video_path,
            processing.SamplingPolicy(mode="timestamps", timestamps_ns=(550_000_000,)),
            window=window,
            options=options,
        )
    with pytest.raises(processing.DecodeError):
        _ = [frame async for frame in stream]
    assert backend.active_sessions == 0


async def test_target_deduplication_does_not_drop_distinct_frames_with_equal_pts(
    processing: Any, register: Any, video_path: Any
) -> None:
    register(timestamps_ns=(0, 0, 500_000_000))
    frames = [
        frame
        async for frame in processing.subsample_frames(
            video_path,
            processing.SamplingPolicy(mode="all"),
            options=processing.DecodeOptions(decoder="memory"),
        )
    ]
    assert [frame.index for frame in frames] == [0, 1, 2]
    assert [frame.timestamp_ns for frame in frames] == [0, 0, 500_000_000]


async def test_sampling_deduplicates_targets_when_source_indices_are_unknown(
    processing: Any, register: Any, video_path: Any
) -> None:
    register(timestamps_ns=(0, 500_000_000), indices=(None, None))
    frames = [
        frame
        async for frame in processing.subsample_frames(
            video_path,
            processing.SamplingPolicy(mode="count", count=10),
            options=processing.DecodeOptions(decoder="memory"),
        )
    ]
    assert [frame.timestamp_ns for frame in frames] == [0, 500_000_000]
    assert [frame.index for frame in frames] == [None, None]


async def test_generated_targets_use_integral_nanosecond_floor(
    processing: Any, register: Any, video_path: Any
) -> None:
    register()
    frames = [
        frame
        async for frame in processing.subsample_frames(
            video_path,
            processing.SamplingPolicy(mode="count", count=3),
            options=processing.DecodeOptions(decoder="memory"),
        )
    ]
    assert [frame.requested_timestamp_ns for frame in frames] == [
        0,
        333_333_333,
        666_666_666,
    ]
    assert [frame.timestamp_ns for frame in frames] == [0, 400_000_000, 700_000_000]
