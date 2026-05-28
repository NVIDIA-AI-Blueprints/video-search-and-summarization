# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Tests for vss_agents/utils/video_file.py."""

from unittest.mock import MagicMock
from unittest.mock import patch

from vss_agents.data_models.vss import MediaInfoOffset
from vss_agents.utils.video_file import get_video_duration
from vss_agents.utils.video_file import pad_media_info


class TestGetVideoDuration:
    """Tests for get_video_duration function."""

    def test_get_video_duration_file_not_exists(self, tmp_path):
        """Test getting duration for non-existent file."""
        result = get_video_duration(str(tmp_path / "nonexistent.mp4"))
        assert result == 0.0

    def test_get_video_duration_success(self):
        """Test getting video duration successfully."""
        import cv2

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True

        def mock_get(prop):
            if prop == cv2.CAP_PROP_FRAME_COUNT:
                return 1000.0
            elif prop == cv2.CAP_PROP_FPS:
                return 30.0
            return 0.0

        mock_cap.get.side_effect = mock_get

        with patch("os.path.exists", return_value=True):
            with patch("vss_agents.utils.video_file.cv2.VideoCapture", return_value=mock_cap):
                result = get_video_duration("/fake/path.mp4")

        # 1000 frames / 30 fps = 33.33 seconds
        assert abs(result - 33.33) < 0.1

    def test_get_video_duration_cannot_open(self):
        """Test getting duration when video cannot be opened."""
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = False

        with patch("os.path.exists", return_value=True):
            with patch("vss_agents.utils.video_file.cv2.VideoCapture", return_value=mock_cap):
                result = get_video_duration("/fake/path.mp4")

        assert result == 0.0

    def test_get_video_duration_invalid_fps(self):
        """Test getting duration with invalid FPS."""
        import cv2

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True

        def mock_get(prop):
            if prop == cv2.CAP_PROP_FRAME_COUNT:
                return 1000.0
            elif prop == cv2.CAP_PROP_FPS:
                return 0.0  # Invalid FPS
            return 0.0

        mock_cap.get.side_effect = mock_get

        with patch("os.path.exists", return_value=True):
            with patch("vss_agents.utils.video_file.cv2.VideoCapture", return_value=mock_cap):
                result = get_video_duration("/fake/path.mp4")

        assert result == 0.0

    def test_get_video_duration_invalid_frame_count(self):
        """Test getting duration with invalid frame count."""
        import cv2

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True

        def mock_get(prop):
            if prop == cv2.CAP_PROP_FRAME_COUNT:
                return -1.0  # Invalid frame count
            elif prop == cv2.CAP_PROP_FPS:
                return 30.0
            return 0.0

        mock_cap.get.side_effect = mock_get

        with patch("os.path.exists", return_value=True):
            with patch("vss_agents.utils.video_file.cv2.VideoCapture", return_value=mock_cap):
                result = get_video_duration("/fake/path.mp4")

        assert result == 0.0


class TestPadMediaInfo:
    """Tests for pad_media_info function."""

    def test_pad_media_info_basic(self):
        """Test basic padding of media info."""
        media_info = MediaInfoOffset(start_offset=10, end_offset=20)
        video_duration = 100.0

        result = pad_media_info(media_info, video_duration, min_chunk_duration=4)

        # With min_chunk_duration=4, left_padding=2, right_padding=2
        # start: 10 - 2 = 8, end: 20 + 2 = 22
        assert result.start_offset == 8
        assert result.end_offset == 22

    def test_pad_media_info_start_near_zero(self):
        """Test padding when start is near zero."""
        media_info = MediaInfoOffset(start_offset=1, end_offset=20)
        video_duration = 100.0

        result = pad_media_info(media_info, video_duration, min_chunk_duration=4)

        # Cannot subtract full left_padding (2), so use 1
        # left_padding = 1, right_padding = 3
        assert result.start_offset == 0
        assert result.end_offset >= 20

    def test_pad_media_info_end_exceeds_duration(self):
        """Test padding when end exceeds video duration."""
        media_info = MediaInfoOffset(start_offset=90, end_offset=98)
        video_duration = 100.0

        result = pad_media_info(media_info, video_duration, min_chunk_duration=4)

        # end + right_padding would exceed duration
        assert result.end_offset == 100

    def test_pad_media_info_end_clamped_to_duration(self):
        """Test padding when end_offset exceeds duration (covers line 57)."""
        media_info = MediaInfoOffset(start_offset=95, end_offset=99)
        video_duration = 100.0

        result = pad_media_info(media_info, video_duration, min_chunk_duration=10)

        # With min_chunk_duration=10, left_padding=5, right_padding=5
        # end_offset = 99 + 5 = 104 > 100, so clamped to 100
        assert result.end_offset == 100

    def test_pad_media_info_zero_start(self):
        """Test padding when start is at zero."""
        media_info = MediaInfoOffset(start_offset=0, end_offset=20)
        video_duration = 100.0

        result = pad_media_info(media_info, video_duration, min_chunk_duration=4)

        # Start stays at 0, right padding gets extra
        assert result.start_offset == 0

    def test_pad_media_info_default_chunk_duration(self):
        """Test padding with default min_chunk_duration."""
        media_info = MediaInfoOffset(start_offset=10, end_offset=20)
        video_duration = 100.0

        result = pad_media_info(media_info, video_duration)

        # Default min_chunk_duration=2, left_padding=1, right_padding=1
        assert result.start_offset == 9
        assert result.end_offset == 21
