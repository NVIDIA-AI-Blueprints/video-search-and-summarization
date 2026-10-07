######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
######################################################################################################

from threading import Lock
from unittest.mock import patch

import pytest

from common.chunk_info import ChunkInfo
from utils.media_file_info import MediaFileInfo
from vlm_pipeline.video_file_frame_getter import (
    DefaultFrameSelector,
    FrameSelectorData,
    VideoFileFrameGetter,
)


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


@pytest.mark.parametrize(
    "audio_mode, expected_end, expected_transcripts, expected_audio_frames",
    [
        (None, "2024-09-28T09:00:09.000Z", 0, 0),
        ("asr", "2024-09-28T09:00:10.000Z", 1, 0),
        ("vlm", "2024-09-28T09:00:10.000Z", 0, 1),
    ],
)
def test_live_chunk_ntp_covers_delivered_media(
    audio_mode, expected_end, expected_transcripts, expected_audio_frames
):
    chunk = ChunkInfo(
        file="rtsp://localhost/live",
        start_pts=0,
        end_pts=10_000_000_000,
    )
    selector = DefaultFrameSelector(10)
    selector.set_chunk(chunk)
    for second in range(10):
        assert selector.choose_frame(None, second * 1_000_000_000)

    getter = VideoFileFrameGetter.__new__(VideoFileFrameGetter)
    getter._live_stream_frame_selectors = {
        selector: FrameSelectorData(
            cached_pts=[float(second) for second in range(10)],
            cached_frames=[object() for _ in range(10)],
        )
    }
    getter._preprocess = lambda frames: frames
    getter._live_stream_ntp_epoch = 1_727_514_000_000_000_000
    getter._live_stream_ntp_pts = 0
    getter._sei_base_time = None
    getter._enable_audio = audio_mode is not None
    getter._use_vlm_audio = audio_mode == "vlm"
    getter._live_stream_audio_transcripts_lock = Lock()
    getter._audio_frames_lock = Lock()
    getter._live_stream_chunk_overlap_duration = 0
    getter._cached_transcripts = (
        [{"start": 9_500_000_000, "end": 9_700_000_000, "transcript": "speech"}]
        if audio_mode == "asr"
        else []
    )
    getter._cached_audio_frames = (
        [{"start": 9.5, "end": 9.7, "audio": object()}] if audio_mode == "vlm" else []
    )
    getter._err_msg_lock = Lock()
    getter._err_msg = None
    getter._timestamp_filter = None
    reported = []
    getter._live_stream_chunk_decoded_callback = (
        lambda chunk, _frames, _pts, transcripts, _err, _start, _end, audio: reported.append(
            (chunk, transcripts, audio)
        )
    )

    with patch.dict("os.environ", {"CHOOSE_FSELECT": "false"}):
        getter._process_finished_chunks(current_pts=9_000_000_000)

    assert len(reported) == 1
    assert chunk.end_pts == 10_000_000_000  # The next chunk still uses the same boundary.
    assert len(reported[0][1]) == expected_transcripts
    assert len(reported[0][2]) == expected_audio_frames
    assert chunk.end_ntp == expected_end
    assert chunk.end_ntp_float == (1_727_514_010.0 if audio_mode else 1_727_514_009.0)
