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

"""Focused regressions for large clip timelines and delayed source audio."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest


class _CountedHash(str):
    comparisons = 0

    def __eq__(self, other: object) -> bool:
        type(self).comparisons += 1
        return super().__eq__(other)

    def __ne__(self, other: object) -> bool:
        type(self).comparisons += 1
        return super().__ne__(other)


def test_packet_subsequence_matching_is_linear(processing: Any) -> None:
    from vss.video_processing.clips import _find_subsequences

    source = [_CountedHash("repeated") for _ in range(40_000)]
    pattern = [_CountedHash("repeated") for _ in range(2_000)] + [
        _CountedHash("absent")
    ]
    _CountedHash.comparisons = 0

    assert _find_subsequences(source, pattern) == []
    assert _CountedHash.comparisons <= 4 * (len(source) + len(pattern))


def test_missing_audio_packet_timing_is_unknown_not_nonoverlap(processing: Any) -> None:
    from vss.video_processing.clips import _audio_overlaps_segment

    document = {
        "streams": [
            {"index": 1, "codec_type": "audio"},
            {"index": 2, "codec_type": "audio"},
        ],
        "packets": [{"stream_index": 1, "pts_time": "0", "duration_time": "1"}],
    }
    assert _audio_overlaps_segment(document, 3, 4) is None


def test_missing_audio_duration_is_evaluated_against_requested_interval(
    processing: Any,
) -> None:
    from vss.video_processing.clips import _audio_overlaps_segment

    document = {
        "streams": [{"index": 1, "codec_type": "audio"}],
        "packets": [
            {"stream_index": 1, "pts_time": "0", "duration_time": "0.25"},
            {"stream_index": 1, "pts_time": "10"},
        ],
    }
    assert _audio_overlaps_segment(document, 1, 2) is False
    assert _audio_overlaps_segment(document, 9, 11) is True


@pytest.mark.skipif(
    os.getenv("RUN_VSS_CLIP_TESTS") != "1",
    reason="set RUN_VSS_CLIP_TESTS=1 to validate delayed audio with FFmpeg",
)
async def test_delayed_audio_is_only_required_when_retained_interval_overlaps(
    processing: Any, tmp_path: Path
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg and shutil.which("ffprobe"), "FFmpeg and ffprobe are required"
    source = tmp_path / "delayed-audio.mp4"
    result = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x48:rate=10:duration=12",
            "-itsoffset",
            "8",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=4",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            os.getenv("VSS_TEST_H264_ENCODER", "libx264"),
            "-g",
            "10",
            "-c:a",
            "aac",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    early = await processing.materialize_clip(source, 0.2, 0.7)
    try:
        assert early.audio is False
    finally:
        early.cleanup()

    overlapping = await processing.materialize_clip(source, 9.2, 9.7)
    try:
        assert overlapping.audio is True
    finally:
        overlapping.cleanup()
