######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

from unittest.mock import patch

from common.chunk_info import ChunkInfo
from utils.media_file_info import MediaFileInfo
from vlm_pipeline.video_file_frame_getter import DefaultFrameSelector


def test_fixed_file_sampling_selects_qwen_endpoint_indices():
    selector = DefaultFrameSelector(40)
    chunk = ChunkInfo(file="video.mp4", start_pts=0, end_pts=30_100_000_000)
    info = MediaFileInfo(
        video_duration_nsec=30_100_000_000,
        video_fps=10.0,
        video_frame_count=301,
    )

    with patch.dict("os.environ", {"RTVI_QWEN_REFERENCE_RESIZE": "true"}), patch.object(
        MediaFileInfo, "get_info", return_value=info
    ):
        selector.set_chunk(chunk)

    assert selector.selects_by_frame_index
    assert list(selector._selected_frame_indices_array) == [
        0,
        8,
        15,
        23,
        31,
        38,
        46,
        54,
        62,
        69,
        77,
        85,
        92,
        100,
        108,
        115,
        123,
        131,
        138,
        146,
        154,
        162,
        169,
        177,
        185,
        192,
        200,
        208,
        215,
        223,
        231,
        238,
        246,
        254,
        262,
        269,
        277,
        285,
        292,
        300,
    ]


def test_fixed_file_subrange_keeps_pts_selection():
    selector = DefaultFrameSelector(4)
    chunk = ChunkInfo(
        file="video.mp4",
        start_pts=10_000_000_000,
        end_pts=20_000_000_000,
    )
    info = MediaFileInfo(
        video_duration_nsec=30_100_000_000,
        video_frame_count=301,
    )

    with patch.dict("os.environ", {"RTVI_QWEN_REFERENCE_RESIZE": "true"}), patch.object(
        MediaFileInfo, "get_info", return_value=info
    ):
        selector.set_chunk(chunk)

    assert not selector.selects_by_frame_index
    assert list(selector._selected_pts_array) == [
        10_000_000_000,
        12_500_000_000,
        15_000_000_000,
        17_500_000_000,
    ]
