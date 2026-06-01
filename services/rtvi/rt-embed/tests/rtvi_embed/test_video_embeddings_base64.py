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
"""Unit tests for base64 data: URI support in generate_video_embeddings.

Tests cover:
- VideoEmbeddingsQuery accepts valid data: URIs
- VideoEmbeddingsQuery rejects malformed data: URIs
- SSRF validation is NOT triggered for data: URIs
- AssetManager.save_from_base64 decodes and registers assets correctly
- Size limit enforcement in save_from_base64
- Server endpoint dispatches to save_from_base64 for data: URIs
"""

import argparse
import base64
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.requests import Request

from api_models.embeddings import VideoEmbeddingsQuery
from api_models.file import MediaType

# Minimal valid base64-encoded 1x1 JPEG (smallest valid JPEG)
_JPEG_1X1_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8U"
    "HRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgN"
    "DRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAA"
    "AAAAAAAAAAAAAAAAAP/EABQBAQAAAAAAAAAAAAAAAAAAAAD/xAAUEQEAAAAAAAAAAAAAAAAA"
    "AAAA/9oADAMBAAIRAxEAPwCwABmX/9k="
)

# Minimal valid base64-encoded MP4 (ftyp box only — not a playable video but valid binary)
_MP4_FTYP_B64 = base64.b64encode(
    b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2avc1mp41"
).decode()


# ---------------------------------------------------------------------------
# VideoEmbeddingsQuery field validation
# ---------------------------------------------------------------------------


class TestVideoEmbeddingsQueryDataUrl:
    """Unit tests for VideoEmbeddingsQuery.url accepting data: URIs."""

    _model = "cosmos-embed1-448p"
    _id = str(uuid.uuid4())

    def _make(self, url: str, **kwargs) -> VideoEmbeddingsQuery:
        return VideoEmbeddingsQuery(id=self._id, url=url, model=self._model, **kwargs)

    def test_valid_jpeg_data_url_accepted(self):
        """data:image/jpeg;base64,<data> must be accepted."""
        url = f"data:image/jpeg;base64,{_JPEG_1X1_B64}"
        q = self._make(url, media_type="image")
        assert q.url == url

    def test_valid_mp4_data_url_accepted(self):
        """data:video/mp4;base64,<data> must be accepted."""
        url = f"data:video/mp4;base64,{_MP4_FTYP_B64}"
        q = self._make(url, media_type="video")
        assert q.url == url

    def test_valid_png_data_url_accepted(self):
        """data:image/png;base64,<data> must be accepted."""
        png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20).decode()
        url = f"data:image/png;base64,{png_b64}"
        q = self._make(url, media_type="image")
        assert q.url == url

    def test_valid_non_base64_data_url_with_parameters(self):
        """data:text/plain with ;parameters and percent-encoding must be accepted."""
        url = "data:text/plain;charset=utf-8,hello%20world"
        q = self._make(url, media_type="video")
        assert q.url == url

    def test_valid_uppercase_mime_base64_data_url(self):
        """Primary MIME type letters may be uppercase (case-insensitive type token)."""
        url = f"data:Video/mp4;base64,{_MP4_FTYP_B64}"
        q = self._make(url, media_type="video")
        assert q.url == url

    def test_invalid_data_url_missing_comma_rejected(self):
        """data: URL with no comma separator must be rejected."""
        with pytest.raises(ValidationError):
            self._make("data:video/mp4;base64")

    def test_invalid_data_url_bad_mime_rejected(self):
        """data: URL with malformed MIME type must be rejected."""
        with pytest.raises(ValidationError):
            self._make("data:NOTAMIME;base64,abc")

    def test_http_url_still_accepted(self):
        """HTTP/HTTPS URLs must still be accepted (no regression)."""
        q = self._make("https://example.com/video.mp4")
        assert q.url == "https://example.com/video.mp4"

    def test_s3_url_still_accepted(self):
        """S3 URLs must still be accepted (no regression)."""
        q = self._make("s3://my-bucket/video.mp4")
        assert q.url == "s3://my-bucket/video.mp4"

    def test_unsupported_scheme_rejected(self):
        """ftp:// URLs must be rejected."""
        with pytest.raises(ValidationError):
            self._make("ftp://example.com/video.mp4")

    def test_ssrf_not_triggered_for_data_url(self):
        """validate_url_against_ssrf must NOT be called for data: URLs."""
        url = f"data:video/mp4;base64,{_MP4_FTYP_B64}"
        with patch("api_models.embeddings.validate_url_against_ssrf") as mock_ssrf:
            self._make(url, media_type="video")
        mock_ssrf.assert_not_called()

    def test_url_field_rejects_oversized_data_uri(self):
        """data: URI exceeding MAX_DATA_URL_SERIALIZED_LENGTH must be rejected with ValidationError."""
        from api_models.common import MAX_DATA_URL_SERIALIZED_LENGTH

        oversized_url = "data:video/mp4;base64," + "A" * (MAX_DATA_URL_SERIALIZED_LENGTH + 1)
        with pytest.raises(ValidationError):
            VideoEmbeddingsQuery(url=oversized_url, media_type="video")


# ---------------------------------------------------------------------------
# AssetManager.save_from_base64
# ---------------------------------------------------------------------------


class TestAssetManagerSaveFromBase64:
    """Unit tests for AssetManager.save_from_base64."""

    @pytest.fixture
    def asset_manager(self, tmp_path):
        """Return a minimal AssetManager backed by a temp directory."""
        from utils.asset_manager import AssetManager

        return AssetManager(str(tmp_path), max_storage_usage_gb=None)

    @pytest.mark.asyncio
    async def test_mp4_asset_created(self, asset_manager):
        """save_from_base64 creates an asset for a video/mp4 data URL."""
        data_url = f"data:video/mp4;base64,{_MP4_FTYP_B64}"
        asset_id = str(uuid.uuid4())

        returned_id = await asset_manager.save_from_base64(
            data_url=data_url,
            media_type="video",
            creation_time=None,
            asset_id=asset_id,
        )

        assert returned_id == asset_id
        asset = asset_manager.get_asset(asset_id)
        assert asset is not None
        assert asset.media_type == "video"
        assert asset.filename.endswith(".mp4")
        assert os.path.isfile(asset.path)

    @pytest.mark.asyncio
    async def test_jpeg_asset_created(self, asset_manager):
        """save_from_base64 creates an asset for an image/jpeg data URL."""
        data_url = f"data:image/jpeg;base64,{_JPEG_1X1_B64}"
        asset_id = str(uuid.uuid4())

        returned_id = await asset_manager.save_from_base64(
            data_url=data_url,
            media_type="image",
            creation_time=None,
            asset_id=asset_id,
        )

        assert returned_id == asset_id
        asset = asset_manager.get_asset(asset_id)
        assert asset.filename.endswith(".jpg")

    @pytest.mark.asyncio
    async def test_decoded_bytes_written_to_disk(self, asset_manager):
        """Decoded bytes match the original raw data."""
        raw = b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2avc1mp41"
        data_url = f"data:video/mp4;base64,{base64.b64encode(raw).decode()}"
        asset_id = str(uuid.uuid4())

        await asset_manager.save_from_base64(
            data_url=data_url,
            media_type="video",
            creation_time=None,
            asset_id=asset_id,
        )

        asset = asset_manager.get_asset(asset_id)
        with open(asset.path, "rb") as f:
            assert f.read() == raw

    @pytest.mark.asyncio
    async def test_creation_time_propagated(self, asset_manager):
        """creation_time is stored on the resulting asset."""
        data_url = f"data:video/mp4;base64,{_MP4_FTYP_B64}"
        asset_id = str(uuid.uuid4())
        ctime = "2024-06-09T18:32:11.123Z"

        await asset_manager.save_from_base64(
            data_url=data_url,
            media_type="video",
            creation_time=ctime,
            asset_id=asset_id,
        )

        asset = asset_manager.get_asset(asset_id)
        assert asset.creation_time == ctime

    @pytest.mark.asyncio
    async def test_size_limit_enforced(self, asset_manager):
        """Oversized payloads raise ServiceException with FileTooLarge."""
        from common.service_exception import ServiceException

        # 1 byte of raw data base64-encoded
        raw = b"x"
        data_url = f"data:video/mp4;base64,{base64.b64encode(raw).decode()}"
        asset_id = str(uuid.uuid4())

        # Patch MAX_DOWNLOAD_FILE_SIZE to 0 to force limit
        with patch("utils.asset_manager.MAX_DOWNLOAD_FILE_SIZE", 0):
            with pytest.raises(ServiceException) as exc_info:
                await asset_manager.save_from_base64(
                    data_url=data_url,
                    media_type="video",
                    creation_time=None,
                    asset_id=asset_id,
                )
        assert exc_info.value.status_code == 413

    @pytest.mark.asyncio
    async def test_invalid_base64_raises(self, asset_manager):
        """Corrupted base64 payload raises ServiceException."""
        from common.service_exception import ServiceException

        data_url = "data:video/mp4;base64,!!!not-valid-base64!!!"
        asset_id = str(uuid.uuid4())

        with pytest.raises(ServiceException):
            await asset_manager.save_from_base64(
                data_url=data_url,
                media_type="video",
                creation_time=None,
                asset_id=asset_id,
            )

    @pytest.mark.asyncio
    async def test_missing_comma_raises(self, asset_manager):
        """data: URL without comma separator raises ServiceException."""
        from common.service_exception import ServiceException

        data_url = "data:video/mp4;base64"
        asset_id = str(uuid.uuid4())

        with pytest.raises(ServiceException):
            await asset_manager.save_from_base64(
                data_url=data_url,
                media_type="video",
                creation_time=None,
                asset_id=asset_id,
            )

    @pytest.mark.asyncio
    async def test_padding_auto_fixed(self, asset_manager):
        """Base64 strings without padding are automatically fixed."""
        raw = b"hello-world-test"
        encoded = base64.b64encode(raw).decode().rstrip("=")  # strip padding
        data_url = f"data:video/mp4;base64,{encoded}"
        asset_id = str(uuid.uuid4())

        await asset_manager.save_from_base64(
            data_url=data_url,
            media_type="video",
            creation_time=None,
            asset_id=asset_id,
        )

        asset = asset_manager.get_asset(asset_id)
        with open(asset.path, "rb") as f:
            assert f.read() == raw


# ---------------------------------------------------------------------------
# Server endpoint dispatch (no GPU required)
# ---------------------------------------------------------------------------


class TestGenerateVideoEmbeddingsBase64Dispatch:
    """Tests that the generate_video_embeddings handler routes data: URLs
    to save_from_base64 without attempting HTTP download or S3 download."""

    @pytest.fixture
    def mock_asset_manager(self):
        am = MagicMock()
        am.check_asset_exists.return_value = False
        am.save_from_base64 = AsyncMock(return_value=str(uuid.uuid4()))
        am.download_file = AsyncMock(return_value=str(uuid.uuid4()))
        am.download_file_from_s3 = AsyncMock(return_value=str(uuid.uuid4()))
        am.cleanup_asset = MagicMock()
        # get_asset returns a mock non-live asset
        fake_asset = MagicMock()
        fake_asset.is_live = False
        fake_asset.media_type = "video"
        am.get_asset.return_value = fake_asset
        return am

    @staticmethod
    def _minimal_rtvi_args(asset_dir: str) -> argparse.Namespace:
        ns = argparse.Namespace()
        ns.asset_dir = asset_dir
        ns.max_asset_storage_size = None
        ns.max_live_streams = 2
        ns.host = "127.0.0.1"
        ns.port = "8017"
        return ns

    @staticmethod
    async def _call_generate_video_embeddings_handler(
        mock_asset_manager, tmp_path, *, creation_time: str | None = None
    ) -> tuple[uuid.UUID, str]:
        """Build RTVIServer with mocked stream handler, inject mock_asset_manager, POST handler."""
        from server.rtvi_embed_server import API_PREFIX, RTVIServer
        from server.rtvi_stream_handler import RequestInfo

        args = TestGenerateVideoEmbeddingsBase64Dispatch._minimal_rtvi_args(str(tmp_path))
        mock_stream_handler = MagicMock()
        mock_stream_handler.get_models_info.return_value = MagicMock(id="cosmos-embed1-448p")
        mock_stream_handler.generate_vlm_captions.return_value = str(uuid.uuid4())
        mock_stream_handler.wait_for_request_done = MagicMock()
        req_info = RequestInfo()
        req_info.status = RequestInfo.Status.SUCCESSFUL
        req_info.queue_time = 1700000000.0
        req_info.chunk_count = 0
        req_info.start_time = 0.0
        req_info.end_time = 1.0
        req_info.start_timestamp = 0.0
        req_info.end_timestamp = 1.0
        req_info.assets = [MagicMock(creation_time=None)]
        req_info.is_live = False
        mock_stream_handler.get_response.return_value = (req_info, [])

        with patch("server.rtvi_embed_server.RTVIStreamHandler", return_value=mock_stream_handler):
            server = RTVIServer(args)
            server._asset_manager = mock_asset_manager

        file_id = uuid.uuid4()
        data_url = f"data:video/mp4;base64,{_MP4_FTYP_B64}"
        q_kwargs: dict = {
            "id": file_id,
            "url": data_url,
            "model": "cosmos-embed1-448p",
            "media_type": "video",
        }
        if creation_time is not None:
            q_kwargs["creation_time"] = creation_time
        query = VideoEmbeddingsQuery(**q_kwargs)

        path = f"{API_PREFIX}/generate_video_embeddings"
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "scheme": "http",
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("test", 80),
        }
        request = Request(scope)

        endpoint = None
        for route in server._app.routes:
            if (
                isinstance(route, APIRoute)
                and route.path == path
                and route.methods is not None
                and "POST" in route.methods
            ):
                endpoint = route.endpoint
                break
        assert endpoint is not None

        await endpoint(query, request)
        return file_id, data_url

    @pytest.mark.asyncio
    async def test_data_url_does_not_call_download_file(self, mock_asset_manager, tmp_path):
        """When url starts with data:, HTTP and S3 download helpers must NOT be called."""
        file_id, data_url = await self._call_generate_video_embeddings_handler(
            mock_asset_manager, tmp_path
        )

        mock_asset_manager.save_from_base64.assert_awaited_once_with(
            data_url,
            MediaType.VIDEO,
            None,
            str(file_id),
        )
        mock_asset_manager.download_file.assert_not_called()
        mock_asset_manager.download_file_from_s3.assert_not_called()

    @pytest.mark.asyncio
    async def test_data_url_dispatches_to_save_from_base64(self, mock_asset_manager, tmp_path):
        """Handler passes the full data: URL and metadata to save_from_base64."""
        ctime = "2024-06-09T18:32:11.123Z"
        file_id, data_url = await self._call_generate_video_embeddings_handler(
            mock_asset_manager, tmp_path, creation_time=ctime
        )

        mock_asset_manager.save_from_base64.assert_awaited_once_with(
            data_url,
            MediaType.VIDEO,
            ctime,
            str(file_id),
        )
        mock_asset_manager.download_file.assert_not_called()
        mock_asset_manager.download_file_from_s3.assert_not_called()
