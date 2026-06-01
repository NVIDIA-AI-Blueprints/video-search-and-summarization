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

"""
Unit tests for VLMClient prompt validation.

Tests:
1. user_prompt is required - all methods must raise ValueError when prompt is empty/None
2. analyze_video_with_base64 - MediaDownloader integration for base64 upload mode

Run with: pytest test/vlm/test_vlm_client_prompt_validation.py -v

Note: Due to circular imports in the codebase, these tests use sys.modules patching
to isolate VLMClient from handlers module.
"""

import os
import sys
import tempfile
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Module isolation to avoid circular import
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def isolate_vlm_client():
    """
    Isolate vlm.vlm_client from handlers module to avoid circular import.
    This patches handlers module before vlm_client is imported.
    """
    mock_handlers = MagicMock()
    mock_media_downloader = MagicMock()
    
    with patch.dict(sys.modules, {
        'handlers': mock_handlers,
        'handlers.direct_media': mock_media_downloader,
        'handlers.direct_media.media_downloader': mock_media_downloader,
        'handlers.enrichment': MagicMock(),
    }):
        mock_media_downloader.MediaDownloader = MagicMock()
        mock_media_downloader.DownloadConfig = MagicMock()
        yield


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vlm_client():
    """Create a VLMClient with mocked dependencies."""
    with patch("openai.OpenAI"):
        # Import inside fixture after module isolation
        from vlm.vlm_client import VLMClient
        client = VLMClient({
            "base_url": "http://localhost:8080/v1",
            "model": "nvidia/cosmos-reason2-8b",
            "max_tokens": 256,
        })
        yield client


@pytest.fixture
def temp_video_file():
    """Create a temporary video file for testing."""
    fd, path = tempfile.mkstemp(suffix=".mp4")
    os.write(fd, b"\x00\x00\x00\x1cftypisom")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


# ---------------------------------------------------------------------------
# user_prompt validation tests
# ---------------------------------------------------------------------------

class TestUserPromptRequired:
    """Tests verifying that user_prompt is required for all VLM methods."""

    def test_analyze_image_url_requires_prompt_empty_string(self, vlm_client):
        """analyze_image_url must raise ValueError when user_prompt is empty string."""
        with pytest.raises(ValueError, match="user_prompt is required"):
            vlm_client.analyze_image_url("http://example.com/image.jpg", "")

    def test_analyze_image_url_requires_prompt_none(self, vlm_client):
        """analyze_image_url must raise ValueError when user_prompt is None."""
        with pytest.raises(ValueError, match="user_prompt is required"):
            vlm_client.analyze_image_url("http://example.com/image.jpg", None)

    def test_analyze_video_url_requires_prompt_empty_string(self, vlm_client):
        """analyze_video_url must raise ValueError when user_prompt is empty string."""
        with pytest.raises(ValueError, match="user_prompt is required"):
            vlm_client.analyze_video_url("http://example.com/video.mp4", "")

    def test_analyze_video_url_requires_prompt_none(self, vlm_client):
        """analyze_video_url must raise ValueError when user_prompt is None."""
        with pytest.raises(ValueError, match="user_prompt is required"):
            vlm_client.analyze_video_url("http://example.com/video.mp4", None)

    def test_analyze_video_with_base64_requires_prompt_empty_string(self, vlm_client):
        """analyze_video_with_base64 must raise ValueError when user_prompt is empty string."""
        with pytest.raises(ValueError, match="user_prompt is required"):
            vlm_client.analyze_video_with_base64("http://example.com/video.mp4", "")

    def test_analyze_video_with_base64_requires_prompt_none(self, vlm_client):
        """analyze_video_with_base64 must raise ValueError when user_prompt is None."""
        with pytest.raises(ValueError, match="user_prompt is required"):
            vlm_client.analyze_video_with_base64("http://example.com/video.mp4", None)

    def test_analyze_local_video_requires_prompt_empty_string(self, vlm_client, temp_video_file):
        """analyze_local_video must raise ValueError when user_prompt is empty string."""
        with pytest.raises(ValueError, match="user_prompt is required"):
            vlm_client.analyze_local_video(temp_video_file, "")

    def test_analyze_local_video_requires_prompt_none(self, vlm_client, temp_video_file):
        """analyze_local_video must raise ValueError when user_prompt is None."""
        with pytest.raises(ValueError, match="user_prompt is required"):
            vlm_client.analyze_local_video(temp_video_file, None)

    def test_upload_media_file_requires_prompt_empty_string(self, vlm_client, temp_video_file):
        """upload_media_file must raise ValueError when user_prompt is empty string."""
        with pytest.raises(ValueError, match="user_prompt is required"):
            vlm_client.upload_media_file(temp_video_file, "")

    def test_upload_media_file_requires_prompt_none(self, vlm_client, temp_video_file):
        """upload_media_file must raise ValueError when user_prompt is None."""
        with pytest.raises(ValueError, match="user_prompt is required"):
            vlm_client.upload_media_file(temp_video_file, None)


class TestUserPromptAccepted:
    """Tests verifying that valid prompts work correctly."""

    def test_analyze_image_url_with_valid_prompt(self, vlm_client):
        """analyze_image_url accepts non-empty prompt."""
        with patch.object(vlm_client, '_create_chat') as mock_create:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock(message=MagicMock(content="test response"))]
            mock_create.return_value = mock_response
            
            result = vlm_client.analyze_image_url(
                "http://example.com/image.jpg",
                "What is in this image?"
            )
            
            assert result.content == "test response"
            mock_create.assert_called_once()

    def test_analyze_video_url_with_valid_prompt(self, vlm_client):
        """analyze_video_url accepts non-empty prompt."""
        with patch.object(vlm_client, '_create_chat') as mock_create:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock(message=MagicMock(content="test response"))]
            mock_create.return_value = mock_response
            
            result = vlm_client.analyze_video_url(
                "http://example.com/video.mp4",
                "What is happening in this video?"
            )
            
            assert result.content == "test response"
            mock_create.assert_called_once()

    def test_upload_media_file_with_valid_prompt(self, vlm_client, temp_video_file):
        """upload_media_file accepts non-empty prompt."""
        with patch.object(vlm_client, '_create_chat') as mock_create, \
             patch.object(vlm_client, '_prepare_local_media') as mock_prepare:
            mock_prepare.return_value = ("video", "data:video/mp4;base64,...")
            mock_response = MagicMock()
            mock_response.choices = [MagicMock(message=MagicMock(content="test response"))]
            mock_create.return_value = mock_response
            
            result = vlm_client.upload_media_file(
                temp_video_file,
                "Describe this video",
                media_type="video"
            )
            
            assert result.content == "test response"


# ---------------------------------------------------------------------------
# analyze_video_with_base64 tests (MediaDownloader integration)
# ---------------------------------------------------------------------------

class TestAnalyzeVideoFromUrl:
    """Tests for analyze_video_with_base64 method with MediaDownloader."""

    def test_requires_valid_prompt_before_download(self, vlm_client):
        """Validation runs before download attempt."""
        with pytest.raises(ValueError, match="user_prompt is required"):
            vlm_client.analyze_video_with_base64("http://example.com/video.mp4", "")

    def test_requires_non_none_prompt(self, vlm_client):
        """None prompt raises ValueError."""
        with pytest.raises(ValueError, match="user_prompt is required"):
            vlm_client.analyze_video_with_base64("http://example.com/video.mp4", None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
