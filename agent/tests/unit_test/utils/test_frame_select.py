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
"""Tests for frame_select module."""

from unittest.mock import MagicMock
from unittest.mock import patch

import numpy as np
import pytest
from vss_agents.utils.frame_select import frame_select
from vss_agents.utils.frame_select import has_nvidia_gpu


class TestFrameSelect:
    """Test frame_select function."""

    def test_invalid_video_path(self):
        with patch("vss_agents.utils.frame_select.cv2") as mock_cv2:
            mock_cap = MagicMock()
            mock_cap.isOpened.return_value = False
            mock_cv2.VideoCapture.return_value = mock_cap
            with pytest.raises(ValueError, match="Could not open video"):
                frame_select("/nonexistent/video.mp4", 0.0, 10.0, 1.0)

    def test_successful_frame_extraction(self):
        with patch("vss_agents.utils.frame_select.cv2") as mock_cv2:
            mock_cap = MagicMock()
            mock_cap.isOpened.return_value = True
            mock_cap.get.side_effect = lambda prop: {0: 30.0, 7: 300}[prop]  # FPS=30, frames=300
            mock_cap.read.return_value = (True, np.zeros((100, 100, 3), dtype=np.uint8))
            mock_cv2.VideoCapture.return_value = mock_cap
            mock_cv2.CAP_PROP_FPS = 0
            mock_cv2.CAP_PROP_FRAME_COUNT = 7
            mock_cv2.CAP_PROP_POS_FRAMES = 1
            mock_cv2.imencode.return_value = (True, np.array([1, 2, 3], dtype=np.uint8))

            result = frame_select("/path/video.mp4", 0.0, 2.0, 1.0)
            assert len(result) > 0
            assert isinstance(result[0], str)  # base64 string

    def test_no_frames_selected(self):
        with patch("vss_agents.utils.frame_select.cv2") as mock_cv2:
            mock_cap = MagicMock()
            mock_cap.isOpened.return_value = True
            mock_cap.get.side_effect = lambda prop: {0: 30.0, 7: 10}[prop]
            mock_cv2.VideoCapture.return_value = mock_cap
            mock_cv2.CAP_PROP_FPS = 0
            mock_cv2.CAP_PROP_FRAME_COUNT = 7

            # start_frame > end_frame → empty range
            result = frame_select("/path/video.mp4", 100.0, 100.0, 1.0)
            assert result == []

    def test_frame_read_failure(self):
        with patch("vss_agents.utils.frame_select.cv2") as mock_cv2:
            mock_cap = MagicMock()
            mock_cap.isOpened.return_value = True
            mock_cap.get.side_effect = lambda prop: {0: 30.0, 7: 300}[prop]
            mock_cap.read.return_value = (False, None)  # Read failure
            mock_cv2.VideoCapture.return_value = mock_cap
            mock_cv2.CAP_PROP_FPS = 0
            mock_cv2.CAP_PROP_FRAME_COUNT = 7
            mock_cv2.CAP_PROP_POS_FRAMES = 1

            with pytest.raises(RuntimeError, match="Error selecting frames"):
                frame_select("/path/video.mp4", 0.0, 2.0, 1.0)


class TestHasNvidiaGpu:
    """Test has_nvidia_gpu function."""

    def test_no_nvidia_smi(self):
        with patch("vss_agents.utils.frame_select.shutil.which", return_value=None):
            assert has_nvidia_gpu() is False

    def test_nvidia_smi_success(self):
        with patch("vss_agents.utils.frame_select.shutil.which", return_value="/usr/bin/nvidia-smi"):
            mock_result = MagicMock()
            mock_result.returncode = 0
            with patch("vss_agents.utils.frame_select.subprocess.run", return_value=mock_result):
                assert has_nvidia_gpu() is True

    def test_nvidia_smi_failure(self):
        with patch("vss_agents.utils.frame_select.shutil.which", return_value="/usr/bin/nvidia-smi"):
            mock_result = MagicMock()
            mock_result.returncode = 1
            with patch("vss_agents.utils.frame_select.subprocess.run", return_value=mock_result):
                assert has_nvidia_gpu() is False
