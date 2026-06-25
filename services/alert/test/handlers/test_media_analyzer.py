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
Unit tests for handlers.direct_media.media_analyzer module.

Run with: pytest test/handlers/test_media_analyzer.py -v
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from handlers.direct_media.media_analyzer import (
    analyze_single_media,
    analyze_multiple_images,
    _is_url,
    _infer_media_type,
)


# ── helpers ───────────────────────────────────────────────────────────

def _make_vlm(content: str = "vlm response") -> MagicMock:
    vlm = MagicMock()
    msg = MagicMock(content=content)
    vlm.analyze_video_url.return_value = msg
    vlm.analyze_image_url.return_value = msg
    vlm.analyze_local_video.return_value = msg
    vlm.upload_media_file.return_value = msg
    vlm.analyze_multiple_image_urls.return_value = msg
    vlm.analyze_multiple_images.return_value = msg
    return vlm


def _make_downloader(local_path: str = "/tmp/downloaded.mp4") -> MagicMock:
    """Return a mock MediaDownloader with validate_url returning (True, '')."""
    dl = MagicMock()
    dl.download.return_value = local_path
    dl.validate_url.return_value = (True, "")
    return dl


# ── _is_url / _infer_media_type ──────────────────────────────────────

class TestIsUrl:
    def test_http(self):
        assert _is_url("http://example.com/video.mp4") is True

    def test_https(self):
        assert _is_url("https://example.com/video.mp4") is True

    def test_rtsp(self):
        assert _is_url("rtsp://cam/stream") is True

    def test_local_path(self):
        assert _is_url("/data/videos/test.mp4") is False

    def test_relative_path(self):
        assert _is_url("videos/test.mp4") is False


class TestInferMediaType:
    def test_mp4_is_video(self):
        assert _infer_media_type("clip.mp4") == "video"

    def test_avi_is_video(self):
        assert _infer_media_type("clip.avi") == "video"

    def test_jpg_is_image(self):
        assert _infer_media_type("photo.jpg") == "image"

    def test_png_is_image(self):
        assert _infer_media_type("photo.png") == "image"

    def test_unknown_defaults_to_video(self):
        assert _infer_media_type("file.xyz") == "video"


# ── analyze_single_media — local files ───────────────────────────────

class TestAnalyzeSingleMediaLocal:
    def test_local_video(self):
        vlm = _make_vlm()
        dl = _make_downloader()

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"\x00")
            path = f.name

        try:
            result = analyze_single_media(vlm, dl, path, "prompt", None)
            vlm.analyze_local_video.assert_called_once()
            dl.download.assert_not_called()
            assert result.content == "vlm response"
        finally:
            os.unlink(path)

    def test_local_image(self):
        vlm = _make_vlm()
        dl = _make_downloader()

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8")
            path = f.name

        try:
            result = analyze_single_media(vlm, dl, path, "prompt", None)
            vlm.upload_media_file.assert_called_once()
            assert result.content == "vlm response"
        finally:
            os.unlink(path)

    def test_local_file_not_found(self):
        vlm = _make_vlm()
        dl = _make_downloader()

        with pytest.raises(FileNotFoundError):
            analyze_single_media(vlm, dl, "/nonexistent/video.mp4", "prompt", None)


# ── analyze_single_media — URL direct (use_base64=False) ─────────────

class TestAnalyzeSingleMediaUrlDirect:
    def test_video_url_direct(self):
        vlm = _make_vlm()
        dl = _make_downloader()

        result = analyze_single_media(
            vlm, dl, "http://example.com/video.mp4", "prompt", None, use_base64=False,
        )
        vlm.analyze_video_url.assert_called_once()
        dl.download.assert_not_called()
        dl.validate_url.assert_called_once_with("http://example.com/video.mp4")
        assert result.content == "vlm response"

    def test_image_url_direct(self):
        vlm = _make_vlm()
        dl = _make_downloader()

        result = analyze_single_media(
            vlm, dl, "http://example.com/photo.jpg", "prompt", None, use_base64=False,
        )
        vlm.analyze_image_url.assert_called_once()
        dl.download.assert_not_called()


# ── analyze_single_media — URL base64 (use_base64=True) ──────────────

class TestAnalyzeSingleMediaUrlBase64:
    def test_video_url_base64(self):
        vlm = _make_vlm()
        dl = _make_downloader("/tmp/dl.mp4")

        with patch("handlers.direct_media.media_analyzer.os.path.isfile", return_value=True):
            result = analyze_single_media(
                vlm, dl, "http://example.com/video.mp4", "prompt", None, use_base64=True,
            )

        dl.download.assert_called_once()
        vlm.analyze_local_video.assert_called_once()
        assert result.content == "vlm response"

    def test_image_url_base64(self):
        vlm = _make_vlm()
        dl = _make_downloader("/tmp/dl.jpg")

        with patch("handlers.direct_media.media_analyzer.os.path.isfile", return_value=True), \
             patch("handlers.direct_media.media_analyzer._infer_media_type", return_value="image"):
            result = analyze_single_media(
                vlm, dl, "https://example.com/photo.jpg", "prompt", None, use_base64=True,
            )

        dl.download.assert_called_once()
        vlm.upload_media_file.assert_called_once()

    def test_download_failure_raises(self):
        vlm = _make_vlm()
        dl = _make_downloader()
        dl.download.return_value = None

        with pytest.raises(ValueError, match="Failed to download"):
            analyze_single_media(
                vlm, dl, "http://example.com/video.mp4", "prompt", None, use_base64=True,
            )

    def test_cleanup_called_on_success(self):
        vlm = _make_vlm()
        dl = _make_downloader("/tmp/dl.mp4")

        with patch("handlers.direct_media.media_analyzer.os.path.isfile", return_value=True), \
             patch("handlers.direct_media.media_analyzer.MediaDownloader.cleanup") as mock_cleanup:
            analyze_single_media(
                vlm, dl, "http://example.com/video.mp4", "prompt", None, use_base64=True,
            )
            mock_cleanup.assert_called_once_with("/tmp/dl.mp4")


# ── SSRF / URL policy enforcement ────────────────────────────────────

class TestUrlPolicyEnforcement:
    """_enforce_url_policy is called for every URL path; blocked URLs raise ValueError."""

    def test_blocked_url_single_media_raises(self):
        vlm = _make_vlm()
        dl = _make_downloader()
        dl.validate_url.return_value = (False, "Private IP addresses not allowed")

        with pytest.raises(ValueError, match="URL blocked by security policy"):
            analyze_single_media(
                vlm, dl, "http://169.254.169.254/metadata", "prompt", None,
            )
        vlm.analyze_video_url.assert_not_called()

    def test_blocked_url_single_media_base64_raises(self):
        vlm = _make_vlm()
        dl = _make_downloader()
        dl.validate_url.return_value = (False, "Localhost URLs not allowed")

        with pytest.raises(ValueError, match="URL blocked by security policy"):
            analyze_single_media(
                vlm, dl, "http://localhost/secret", "prompt", None, use_base64=True,
            )
        dl.download.assert_not_called()

    def test_blocked_url_multiple_images_raises(self):
        vlm = _make_vlm()
        dl = _make_downloader()
        dl.validate_url.side_effect = [
            (True, ""),
            (False, "Private IP addresses not allowed"),
        ]

        with pytest.raises(ValueError, match="URL blocked by security policy"):
            analyze_multiple_images(
                vlm, dl,
                ["http://public.com/a.jpg", "http://10.0.0.1/b.jpg"],
                "prompt", None, use_base64=False,
            )
        vlm.analyze_multiple_image_urls.assert_not_called()

    def test_validate_url_called_for_direct_url(self):
        vlm = _make_vlm()
        dl = _make_downloader()

        analyze_single_media(
            vlm, dl, "http://example.com/video.mp4", "prompt", None, use_base64=False,
        )
        dl.validate_url.assert_called_once_with("http://example.com/video.mp4")

    def test_validate_url_called_for_each_multi_image(self):
        vlm = _make_vlm()
        dl = _make_downloader()
        urls = ["http://a.com/1.jpg", "http://b.com/2.jpg", "http://c.com/3.jpg"]

        analyze_multiple_images(vlm, dl, urls, "prompt", None, use_base64=False)

        assert dl.validate_url.call_count == 3

    def test_allowed_url_proceeds_to_vlm(self):
        vlm = _make_vlm()
        dl = _make_downloader()

        result = analyze_single_media(
            vlm, dl, "https://safe.example.com/video.mp4", "prompt", None,
        )
        assert result.content == "vlm response"
        vlm.analyze_video_url.assert_called_once()


# ── analyze_multiple_images ──────────────────────────────────────────

class TestAnalyzeMultipleImages:
    def test_url_direct(self):
        vlm = _make_vlm()
        dl = _make_downloader()
        urls = ["http://example.com/a.jpg", "http://example.com/b.jpg"]

        result = analyze_multiple_images(vlm, dl, urls, "prompt", None, use_base64=False)

        vlm.analyze_multiple_image_urls.assert_called_once_with(
            urls, "prompt", None, config_overrides=None,
        )
        dl.download.assert_not_called()

    def test_base64_download(self):
        vlm = _make_vlm()
        dl = MagicMock()
        dl.download.side_effect = ["/tmp/a.jpg", "/tmp/b.jpg"]
        dl.validate_url.return_value = (True, "")

        with patch("handlers.direct_media.media_analyzer.MediaDownloader.cleanup"):
            result = analyze_multiple_images(
                vlm, dl,
                ["http://example.com/a.jpg", "http://example.com/b.jpg"],
                "prompt", None, use_base64=True,
            )

        assert dl.download.call_count == 2
        vlm.analyze_multiple_images.assert_called_once()

    def test_all_downloads_fail_raises(self):
        vlm = _make_vlm()
        dl = MagicMock()
        dl.download.return_value = None
        dl.validate_url.return_value = (True, "")

        with pytest.raises(ValueError, match="Failed to download"):
            analyze_multiple_images(
                vlm, dl, ["http://example.com/a.jpg"], "prompt", None, use_base64=True,
            )

    def test_partial_download_raises(self):
        vlm = _make_vlm()
        dl = MagicMock()
        dl.download.side_effect = ["/tmp/a.jpg", None]
        dl.validate_url.return_value = (True, "")

        with patch("handlers.direct_media.media_analyzer.MediaDownloader.cleanup"):
            with pytest.raises(ValueError, match="Failed to download"):
                analyze_multiple_images(
                    vlm, dl,
                    ["http://example.com/a.jpg", "http://example.com/b.jpg"],
                    "prompt", None, use_base64=True,
                )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
