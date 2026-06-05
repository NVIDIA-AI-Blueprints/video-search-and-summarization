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
Unit tests for vlm_media_source_using_base64 configuration.

Tests that the config flag correctly controls media routing through
VLMClient and analyze_single_media / analyze_multiple_images.

Run with: pytest test/vlm/test_vlm_media_source_config.py -v
"""

from unittest.mock import MagicMock, patch

import pytest

from handlers.direct_media.media_analyzer import (
    analyze_single_media,
    analyze_multiple_images,
)


def _make_vlm() -> MagicMock:
    vlm = MagicMock()
    msg = MagicMock(content="ok")
    vlm.analyze_video_url.return_value = msg
    vlm.analyze_image_url.return_value = msg
    vlm.analyze_local_video.return_value = msg
    vlm.upload_media_file.return_value = msg
    vlm.analyze_multiple_image_urls.return_value = msg
    vlm.analyze_multiple_images.return_value = msg
    return vlm


def _make_downloader(local_path="/tmp/dl.mp4") -> MagicMock:
    dl = MagicMock()
    dl.download.return_value = local_path
    dl.validate_url.return_value = (True, "")
    return dl


# ---------------------------------------------------------------------------
# use_base64=False → URL-direct path (VLM fetches the URL itself)
# ---------------------------------------------------------------------------

class TestUrlDirectMode:
    """When vlm_media_source_using_base64 is False, VLM receives the URL directly."""

    def test_video_url_sent_to_vlm(self):
        vlm = _make_vlm()
        dl = _make_downloader()

        analyze_single_media(
            vlm, dl, "http://host/video.mp4", "prompt", None, use_base64=False,
        )

        vlm.analyze_video_url.assert_called_once()
        dl.download.assert_not_called()

    def test_image_url_sent_to_vlm(self):
        vlm = _make_vlm()
        dl = _make_downloader()

        analyze_single_media(
            vlm, dl, "http://host/photo.jpg", "prompt", None, use_base64=False,
        )

        vlm.analyze_image_url.assert_called_once()
        dl.download.assert_not_called()

    def test_multi_image_urls_sent_directly(self):
        vlm = _make_vlm()
        dl = _make_downloader()
        urls = ["http://host/a.jpg", "http://host/b.jpg"]

        analyze_multiple_images(vlm, dl, urls, "prompt", None, use_base64=False)

        vlm.analyze_multiple_image_urls.assert_called_once_with(
            urls, "prompt", None, config_overrides=None,
        )
        dl.download.assert_not_called()


# ---------------------------------------------------------------------------
# use_base64=True → download + local analysis (VLM has no network access)
# ---------------------------------------------------------------------------

class TestBase64UploadMode:
    """When vlm_media_source_using_base64 is True, media is downloaded first."""

    def test_video_downloaded_then_local_analysis(self):
        vlm = _make_vlm()
        dl = _make_downloader("/tmp/dl.mp4")

        with patch("handlers.direct_media.media_analyzer.os.path.isfile", return_value=True):
            analyze_single_media(
                vlm, dl, "http://host/video.mp4", "prompt", None, use_base64=True,
            )

        dl.download.assert_called_once()
        vlm.analyze_local_video.assert_called_once()
        vlm.analyze_video_url.assert_not_called()

    def test_image_downloaded_then_uploaded(self):
        vlm = _make_vlm()
        dl = _make_downloader("/tmp/dl.jpg")

        with patch("handlers.direct_media.media_analyzer.os.path.isfile", return_value=True), \
             patch("handlers.direct_media.media_analyzer._infer_media_type", return_value="image"):
            analyze_single_media(
                vlm, dl, "https://host/photo.jpg", "prompt", None, use_base64=True,
            )

        dl.download.assert_called_once()
        vlm.upload_media_file.assert_called_once()
        vlm.analyze_image_url.assert_not_called()

    def test_multi_images_downloaded_then_local(self):
        vlm = _make_vlm()
        dl = MagicMock()
        dl.download.side_effect = ["/tmp/a.jpg", "/tmp/b.jpg"]
        dl.validate_url.return_value = (True, "")

        with patch("handlers.direct_media.media_analyzer.MediaDownloader.cleanup"):
            analyze_multiple_images(
                vlm, dl,
                ["http://host/a.jpg", "http://host/b.jpg"],
                "prompt", None, use_base64=True,
            )

        assert dl.download.call_count == 2
        vlm.analyze_multiple_images.assert_called_once()
        vlm.analyze_multiple_image_urls.assert_not_called()

    def test_rtsp_in_base64_mode_raises(self):
        vlm = _make_vlm()
        dl = _make_downloader()

        with pytest.raises(ValueError, match="base64 mode requires an http/https URL"):
            analyze_single_media(
                vlm, dl, "rtsp://cam/stream", "prompt", None, use_base64=True,
            )

    def test_cleanup_runs_after_base64_success(self):
        vlm = _make_vlm()
        dl = _make_downloader("/tmp/dl.mp4")

        with patch("handlers.direct_media.media_analyzer.os.path.isfile", return_value=True), \
             patch("handlers.direct_media.media_analyzer.MediaDownloader.cleanup") as mock_cleanup:
            analyze_single_media(
                vlm, dl, "http://host/video.mp4", "prompt", None, use_base64=True,
            )
            mock_cleanup.assert_called_once_with("/tmp/dl.mp4")


# ---------------------------------------------------------------------------
# DirectMediaHandler config integration
# ---------------------------------------------------------------------------

class TestDirectMediaHandlerConfig:
    """DirectMediaHandler reads vlm_media_source_using_base64 from config."""

    def test_default_is_false(self):
        from handlers.direct_media.direct_media_handler import DirectMediaHandler

        handler = DirectMediaHandler(
            vlm_client=MagicMock(),
            vlm_enhanced_event_sink=MagicMock(),
            config={"vlm": {}, "alert_agent": {"media_download": {}}, "vst_config": {}},
        )
        assert handler.vlm_media_source_using_base64 is False

    def test_explicit_true(self):
        from handlers.direct_media.direct_media_handler import DirectMediaHandler

        handler = DirectMediaHandler(
            vlm_client=MagicMock(),
            vlm_enhanced_event_sink=MagicMock(),
            config={
                "vlm": {"vlm_media_source_using_base64": True},
                "alert_agent": {"media_download": {}},
                "vst_config": {},
            },
        )
        assert handler.vlm_media_source_using_base64 is True

    def test_explicit_false(self):
        from handlers.direct_media.direct_media_handler import DirectMediaHandler

        handler = DirectMediaHandler(
            vlm_client=MagicMock(),
            vlm_enhanced_event_sink=MagicMock(),
            config={
                "vlm": {"vlm_media_source_using_base64": False},
                "alert_agent": {"media_download": {}},
                "vst_config": {},
            },
        )
        assert handler.vlm_media_source_using_base64 is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
