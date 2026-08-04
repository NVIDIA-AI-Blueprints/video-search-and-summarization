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

"""
Unit tests for src/chunk_info.py

Tests timestamp formatting and ChunkInfo data model.
"""
import pytest

from chunk_info import ChunkInfo, get_timestamp_str


# Tests for get_timestamp_str function
@pytest.mark.unit
def test_get_timestamp_str_basic():
    """Test basic timestamp string formatting."""
    # Unix epoch: 1970-01-01 00:00:00
    result = get_timestamp_str(0)
    assert result == "1970-01-01T00:00:00.000Z"


@pytest.mark.unit
def test_get_timestamp_str_with_milliseconds():
    """Test timestamp with milliseconds."""
    # 1.5 seconds = 1500 milliseconds
    result = get_timestamp_str(1.5)
    assert result == "1970-01-01T00:00:01.500Z"


@pytest.mark.unit
def test_get_timestamp_str_with_microseconds():
    """Test timestamp with microseconds (should round to milliseconds)."""
    # 1.123456 seconds
    result = get_timestamp_str(1.123456)
    assert result == "1970-01-01T00:00:01.123Z"


@pytest.mark.unit
def test_get_timestamp_str_large_timestamp():
    """Test timestamp string for a realistic date."""
    # 2024-01-01 00:00:00 UTC = 1704067200
    result = get_timestamp_str(1704067200)
    assert result == "2024-01-01T00:00:00.000Z"


@pytest.mark.unit
def test_get_timestamp_str_with_fractional_seconds():
    """Test timestamp with fractional seconds."""
    # 2024-01-01 00:00:00.999 UTC
    result = get_timestamp_str(1704067200.999)
    assert result == "2024-01-01T00:00:00.999Z"


@pytest.mark.unit
def test_chunk_info_with_values():
    """Test ChunkInfo initialization with values."""
    chunk = ChunkInfo(
        sourceId="test-stream-123",
        chunkIdx=5,
        file="/path/to/video.mp4",
        start_pts=1000000000,
        end_pts=2000000000,
        is_first=True,
    )
    assert chunk.sourceId == "test-stream-123"
    assert chunk.chunkIdx == 5
    assert chunk.file == "/path/to/video.mp4"
    assert chunk.start_pts == 1000000000
    assert chunk.end_pts == 2000000000
    assert chunk.is_first is True


@pytest.mark.unit
def test_chunk_info_repr_for_file():
    """Test __repr__ for file-based chunks."""
    chunk = ChunkInfo(
        chunkIdx=3,
        file="/videos/sample.mp4",
        start_pts=5000000000,  # 5 seconds in nanoseconds
        end_pts=10000000000,  # 10 seconds in nanoseconds
    )
    repr_str = repr(chunk)
    assert "Chunk 3" in repr_str
    assert "start=5.0" in repr_str
    assert "end=10.0" in repr_str
    assert "/videos/sample.mp4" in repr_str


@pytest.mark.unit
def test_chunk_info_repr_for_rtsp():
    """Test __repr__ for RTSP stream chunks."""
    chunk = ChunkInfo(
        chunkIdx=1,
        file="rtsp://example.com/stream",
        start_pts=1000000000,
        end_pts=2000000000,
        start_ntp="2024-01-01T00:00:01.000Z",
        end_ntp="2024-01-01T00:00:02.000Z",
    )
    repr_str = repr(chunk)
    assert "Chunk 1" in repr_str
    assert "start=1.0" in repr_str
    assert "end=2.0" in repr_str
    assert "start_ntp=2024-01-01T00:00:01.000Z" in repr_str
    assert "end_ntp=2024-01-01T00:00:02.000Z" in repr_str
    assert "rtsp://example.com/stream" in repr_str


@pytest.mark.unit
def test_chunk_info_str():
    """Test __str__ delegates to __repr__."""
    chunk = ChunkInfo(chunkIdx=2, file="/test.mp4", start_pts=0, end_pts=1000000000)
    assert str(chunk) == repr(chunk)


@pytest.mark.unit
def test_chunk_info_get_timestamp_for_file():
    """Test get_timestamp for file-based chunks."""
    chunk = ChunkInfo(file="/videos/test.mp4", start_pts=1000000000, start_ntp_float=0.0)
    # For file-based chunks, timestamp is the frame_pts itself
    timestamp = chunk.get_timestamp(5000000000)
    assert timestamp == "5000000000"


@pytest.mark.unit
def test_chunk_info_pydantic_validation():
    """Test that ChunkInfo enforces Pydantic field types."""
    # Test valid data
    chunk = ChunkInfo(chunkIdx=5, start_pts=1000)
    assert chunk.chunkIdx == 5
    assert chunk.start_pts == 1000

    # Test type coercion (Pydantic converts compatible types)
    chunk = ChunkInfo(chunkIdx="10", start_pts="2000")
    assert chunk.chunkIdx == 10
    assert chunk.start_pts == 2000


@pytest.mark.unit
def test_chunk_info_get_timestamp_for_rtsp():
    """Test get_timestamp for RTSP stream chunks with NTP timestamps."""
    chunk = ChunkInfo(
        file="rtsp://example.com/stream",
        start_pts=1000000000,  # 1.0 seconds in nanoseconds
        start_ntp_float=1704067200.0,  # 2024-01-01 00:00:00 UTC
    )
    # For RTSP, timestamp calculation involves NTP time
    timestamp = chunk.get_timestamp(3000000000)
    # Should return RFC3339 formatted timestamp
    assert "T" in timestamp and "Z" in timestamp


@pytest.mark.unit
def test_chunk_info_get_timestamp_rtsp_with_fractional():
    """Test get_timestamp for RTSP with fractional seconds."""
    chunk = ChunkInfo(
        file="rtsp://stream.local/cam1",
        start_pts=500000000,  # 0.5 seconds
        start_ntp_float=1704067200.5,  # 2024-01-01 00:00:00.500 UTC
    )
    timestamp = chunk.get_timestamp(1500000000)
    # Should return RFC3339 formatted timestamp
    assert "T" in timestamp and "Z" in timestamp


@pytest.mark.unit
def test_chunk_info_default_values():
    """Test ChunkInfo default field values."""
    chunk = ChunkInfo()
    assert chunk.sourceId == ""
    assert chunk.chunkIdx == 0
    assert chunk.file == ""
    assert chunk.pts_offset_ns == 0
    assert chunk.start_pts == 0
    assert chunk.end_pts == -1
    assert chunk.start_ntp == ""
    assert chunk.end_ntp == ""
    assert chunk.start_ntp_float == 0.0
    assert chunk.end_ntp_float == 0.0
    assert chunk.is_first is False
    assert chunk.is_last is False
    assert chunk.cv_metadata_json_file == ""
    assert chunk.osd_output_video_file == ""
    assert chunk.cached_frames_cv_meta == []


@pytest.mark.unit
def test_chunk_info_all_fields():
    """Test ChunkInfo with all fields populated."""
    chunk = ChunkInfo(
        sourceId="stream-789",
        chunkIdx=7,
        file="/videos/test.mp4",
        pts_offset_ns=1000000,
        start_pts=5000000000,
        end_pts=10000000000,
        start_ntp="2024-01-01T00:00:05.000Z",
        end_ntp="2024-01-01T00:00:10.000Z",
        start_ntp_float=1704067205.0,
        end_ntp_float=1704067210.0,
        is_first=False,
        is_last=True,
        cv_metadata_json_file="/path/to/cv_metadata.json",
        osd_output_video_file="/path/to/osd_output.mp4",
        cached_frames_cv_meta=[{"frame": 1}, {"frame": 2}],
    )
    assert chunk.sourceId == "stream-789"
    assert chunk.chunkIdx == 7
    assert chunk.file == "/videos/test.mp4"
    assert chunk.pts_offset_ns == 1000000
    assert chunk.start_pts == 5000000000
    assert chunk.end_pts == 10000000000
    assert chunk.start_ntp == "2024-01-01T00:00:05.000Z"
    assert chunk.end_ntp == "2024-01-01T00:00:10.000Z"
    assert chunk.start_ntp_float == 1704067205.0
    assert chunk.end_ntp_float == 1704067210.0
    assert chunk.is_first is False
    assert chunk.is_last is True
    assert chunk.cv_metadata_json_file == "/path/to/cv_metadata.json"
    assert chunk.osd_output_video_file == "/path/to/osd_output.mp4"
    assert len(chunk.cached_frames_cv_meta) == 2


@pytest.mark.unit
def test_chunk_info_get_timestamp_rtsp_precise():
    """Test get_timestamp for RTSP with precise calculation verification."""
    chunk = ChunkInfo(
        file="rtsp://cam.local/feed",
        start_pts=2000000000,
        start_ntp_float=1704067200.0,
    )
    result = chunk.get_timestamp(5.0)
    expected_float = 1704067200.0 + 5.0 - 2000000000 / 1000000000.0
    expected = get_timestamp_str(expected_float)
    assert result == expected


@pytest.mark.unit
def test_chunk_info_get_timestamp_file_returns_str():
    """Test get_timestamp for regular file returns frame_pts as string."""
    chunk = ChunkInfo(file="/path/video.mp4")
    result = chunk.get_timestamp(12345)
    assert result == "12345"
    assert isinstance(result, str)


@pytest.mark.unit
def test_get_timestamp_str_negative_timestamp():
    result = get_timestamp_str(-1.0)
    assert "1969" in result
    assert "Z" in result


@pytest.mark.unit
def test_get_timestamp_str_exact_millisecond_boundary():
    result = get_timestamp_str(1.0)
    assert result == "1970-01-01T00:00:01.000Z"


@pytest.mark.unit
def test_get_timestamp_str_sub_millisecond_truncation():
    result = get_timestamp_str(1.9999)
    assert result.endswith("Z")
    assert ".999Z" in result


@pytest.mark.unit
def test_chunk_info_repr_zero_pts():
    chunk = ChunkInfo(chunkIdx=0, file="/test.mp4", start_pts=0, end_pts=0)
    r = repr(chunk)
    assert "start=0.0" in r
    assert "end=0.0" in r


@pytest.mark.unit
def test_chunk_info_get_timestamp_rtsp_zero_frame_pts():
    chunk = ChunkInfo(
        file="rtsp://cam/feed",
        start_pts=0,
        start_ntp_float=1704067200.0,
    )
    result = chunk.get_timestamp(0.0)
    expected = get_timestamp_str(1704067200.0)
    assert result == expected


@pytest.mark.unit
def test_chunk_info_get_timestamp_file_zero():
    chunk = ChunkInfo(file="/video.mp4")
    assert chunk.get_timestamp(0) == "0"


@pytest.mark.unit
def test_chunk_info_get_timestamp_file_float():
    chunk = ChunkInfo(file="/video.mp4")
    assert chunk.get_timestamp(3.14) == "3.14"


@pytest.mark.unit
def test_chunk_info_setattr_with_reassignment():
    """Reassigning sourceId updates the field."""
    chunk = ChunkInfo()
    chunk.sourceId = "first"
    chunk.chunkIdx = 1
    chunk.sourceId = "second"
    assert chunk.sourceId == "second"
    assert chunk.chunkIdx == 1


@pytest.mark.unit
def test_chunk_info_str_equals_repr_rtsp():
    chunk = ChunkInfo(
        chunkIdx=0,
        file="rtsp://cam/feed",
        start_pts=0,
        end_pts=1000000000,
        start_ntp="2024-01-01T00:00:00.000Z",
        end_ntp="2024-01-01T00:00:01.000Z",
    )
    assert str(chunk) == repr(chunk)
    assert "start_ntp=" in str(chunk)


@pytest.mark.unit
def test_chunk_info_setattr_sets_other_field_normally():
    """Non-trigger fields are set normally without side effects."""
    chunk = ChunkInfo()
    chunk.sourceId = "s"
    chunk.chunkIdx = 1
    chunk.is_first = True
    chunk.is_last = True
    chunk.cv_metadata_json_file = "/cv.json"
    assert chunk.is_first is True
    assert chunk.is_last is True
    assert chunk.cv_metadata_json_file == "/cv.json"
    assert chunk.sourceId == "s"
    assert chunk.chunkIdx == 1


@pytest.mark.unit
def test_chunk_info_setattr_streamid_empty_after_set():
    """Setting sourceId to empty string after it was non-empty."""
    chunk = ChunkInfo()
    chunk.sourceId = "s"
    chunk.chunkIdx = 1
    chunk.sourceId = ""
    assert chunk.sourceId == ""
    assert chunk.chunkIdx == 1
