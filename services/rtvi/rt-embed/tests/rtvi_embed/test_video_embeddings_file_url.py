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
"""Unit tests for file:// URL support in generate_video_embeddings.

Tests cover:
- VideoEmbeddingsQuery accepts valid file:// URLs
- VideoEmbeddingsQuery rejects unsupported schemes (no regression)
- SSRF validation is NOT triggered for file:// URLs
- RTVIServer._resolve_file_url enforces FILE_URL_ALLOWED_DIRS allowlist
- RTVIServer._resolve_file_url blocks path traversal via realpath
- RTVIServer._resolve_file_url returns 400 for non-existent files
- Server endpoint dispatches to add_file for file:// URLs
"""

import argparse
import os
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.routing import APIRoute
from pydantic import ValidationError

from api_models.embeddings import VideoEmbeddingsQuery

# ---------------------------------------------------------------------------
# VideoEmbeddingsQuery field validation
# ---------------------------------------------------------------------------


class TestVideoEmbeddingsQueryFileUrl:
    """Unit tests for VideoEmbeddingsQuery.url accepting file:// URLs."""

    _model = "cosmos-embed1-448p"
    _id = str(uuid.uuid4())

    def _make(self, url: str, **kwargs) -> VideoEmbeddingsQuery:
        return VideoEmbeddingsQuery(id=self._id, url=url, model=self._model, **kwargs)

    def test_valid_file_url_accepted(self):
        """file:// URL with a simple absolute path must be accepted."""
        q = self._make("file:///data/videos/video.mp4")
        assert q.url == "file:///data/videos/video.mp4"

    def test_file_url_with_subdirectory_accepted(self):
        """file:// URL with nested path must be accepted."""
        q = self._make("file:///mnt/storage/clips/clip01.mp4")
        assert q.url == "file:///mnt/storage/clips/clip01.mp4"

    def test_http_url_still_accepted(self):
        """HTTP/HTTPS URLs must still be accepted (no regression)."""
        q = self._make("https://example.com/video.mp4")
        assert q.url == "https://example.com/video.mp4"

    def test_s3_url_still_accepted(self):
        """S3 URLs must still be accepted (no regression)."""
        q = self._make("s3://my-bucket/video.mp4")
        assert q.url == "s3://my-bucket/video.mp4"

    def test_unsupported_scheme_rejected(self):
        """ftp:// URLs must still be rejected."""
        with pytest.raises(ValidationError):
            self._make("ftp://example.com/video.mp4")

    def test_ssrf_not_triggered_for_file_url(self):
        """validate_url_against_ssrf must NOT be called for file:// URLs."""
        with patch("api_models.embeddings.validate_url_against_ssrf") as mock_ssrf:
            self._make("file:///data/video.mp4")
        mock_ssrf.assert_not_called()


# ---------------------------------------------------------------------------
# RTVIServer._resolve_file_url
# ---------------------------------------------------------------------------


def _make_server(tmp_path):
    """Build a minimal RTVIServer with mocked stream handler."""
    from server.rtvi_embed_server import RTVIServer

    args = argparse.Namespace(
        asset_dir=str(tmp_path),
        max_asset_storage_size=None,
        max_live_streams=2,
        host="127.0.0.1",
        port="8017",
    )
    mock_stream_handler = MagicMock()
    mock_stream_handler.get_models_info.return_value = MagicMock(id="cosmos-embed1-448p")
    with patch("server.rtvi_embed_server.RTVIStreamHandler", return_value=mock_stream_handler):
        return RTVIServer(args)


def _find_route_handler(server, path, method="POST"):
    for route in server._app.routes:
        if isinstance(route, APIRoute) and route.path == path and method in (route.methods or []):
            return route.endpoint
    return None


class TestResolveFileUrl:
    """Unit tests for RTVIServer._resolve_file_url."""

    def test_valid_path_returned(self, tmp_path):
        """Returns the real path when file exists inside an allowed dir."""
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")
        server = _make_server(tmp_path)

        with patch.dict(os.environ, {"FILE_URL_ALLOWED_DIRS": str(tmp_path)}):
            result = server._resolve_file_url(f"file://{video}")

        assert result == str(video.resolve())

    def test_disabled_when_env_unset(self, tmp_path):
        """Raises 403 when FILE_URL_ALLOWED_DIRS is not set."""
        from common.service_exception import ServiceException

        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")
        server = _make_server(tmp_path)

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("FILE_URL_ALLOWED_DIRS", None)
            with pytest.raises(ServiceException) as exc_info:
                server._resolve_file_url(f"file://{video}")
        assert exc_info.value.status_code == 403

    def test_path_outside_allowed_dir_rejected(self, tmp_path):
        """Raises 403 when the resolved path is outside the allowed directories."""
        from common.service_exception import ServiceException

        allowed = tmp_path / "allowed"
        allowed.mkdir()
        outside = tmp_path / "secret.txt"
        outside.write_text("secret")
        server = _make_server(tmp_path)

        with patch.dict(os.environ, {"FILE_URL_ALLOWED_DIRS": str(allowed)}):
            with pytest.raises(ServiceException) as exc_info:
                server._resolve_file_url(f"file://{outside}")
        assert exc_info.value.status_code == 403

    def test_traversal_blocked(self, tmp_path):
        """Raises 403 when a path traversal escapes the allowed directory."""
        from common.service_exception import ServiceException

        allowed = tmp_path / "allowed"
        allowed.mkdir()
        # This traversal attempts to escape 'allowed' via '..'; realpath resolves it.
        traversal_url = f"file://{allowed}/../secret.txt"
        server = _make_server(tmp_path)

        with patch.dict(os.environ, {"FILE_URL_ALLOWED_DIRS": str(allowed)}):
            with pytest.raises(ServiceException) as exc_info:
                server._resolve_file_url(traversal_url)
        assert exc_info.value.status_code == 403

    def test_nonexistent_file_returns_400(self, tmp_path):
        """Raises 400 when the file does not exist."""
        from common.service_exception import ServiceException

        server = _make_server(tmp_path)

        with patch.dict(os.environ, {"FILE_URL_ALLOWED_DIRS": str(tmp_path)}):
            with pytest.raises(ServiceException) as exc_info:
                server._resolve_file_url(f"file://{tmp_path}/nonexistent.mp4")
        assert exc_info.value.status_code == 400

    def test_symlink_outside_allowed_blocked(self, tmp_path):
        """Raises 403 when a symlink inside allowed dir points outside it."""
        from common.service_exception import ServiceException

        allowed = tmp_path / "allowed"
        allowed.mkdir()
        outside = tmp_path / "secret.txt"
        outside.write_text("secret")
        link = allowed / "link.mp4"
        link.symlink_to(outside)
        server = _make_server(tmp_path)

        with patch.dict(os.environ, {"FILE_URL_ALLOWED_DIRS": str(allowed)}):
            with pytest.raises(ServiceException) as exc_info:
                server._resolve_file_url(f"file://{link}")
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Server endpoint dispatch (no GPU required)
# ---------------------------------------------------------------------------


class TestGenerateVideoEmbeddingsFileUrlDispatch:
    """Tests that the generate_video_embeddings handler routes file:// URLs
    to _resolve_file_url + add_file without attempting HTTP/S3/base64 download."""

    @pytest.fixture
    def mock_asset_manager(self):
        am = MagicMock()
        am.check_asset_exists.return_value = False
        am.add_file = MagicMock(return_value=str(uuid.uuid4()))
        am.save_from_base64 = MagicMock(return_value=str(uuid.uuid4()))
        am.download_file = MagicMock(return_value=str(uuid.uuid4()))
        am.download_file_from_s3 = MagicMock(return_value=str(uuid.uuid4()))
        am.cleanup_asset = MagicMock()
        fake_asset = MagicMock()
        fake_asset.is_live = False
        fake_asset.media_type = "video"
        am.get_asset.return_value = fake_asset
        return am

    @pytest.mark.asyncio
    async def test_file_url_calls_add_file(self, tmp_path, mock_asset_manager):
        """Handler must call add_file (not download_file/save_from_base64) for file:// URLs."""
        from server.rtvi_embed_server import API_PREFIX, RTVIServer
        from server.rtvi_stream_handler import RequestInfo

        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")

        args = argparse.Namespace(
            asset_dir=str(tmp_path),
            max_asset_storage_size=None,
            max_live_streams=2,
            host="127.0.0.1",
            port="8017",
        )
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
        query = VideoEmbeddingsQuery(
            id=file_id,
            url=f"file://{video}",
            model="cosmos-embed1-448p",
            media_type="video",
        )

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
        }
        request = MagicMock()
        request.scope = scope

        route_handler = _find_route_handler(server, path)
        assert route_handler is not None, "generate_video_embeddings route not found"

        with patch.dict(os.environ, {"FILE_URL_ALLOWED_DIRS": str(tmp_path)}):
            await route_handler(query, request)

        mock_asset_manager.add_file.assert_called_once()
        mock_asset_manager.download_file.assert_not_called()
        mock_asset_manager.download_file_from_s3.assert_not_called()
        mock_asset_manager.save_from_base64.assert_not_called()

        # Verify correct args: local_path, purpose, media_type
        call_kwargs = mock_asset_manager.add_file.call_args
        assert call_kwargs[0][0] == str(video.resolve())  # local_path
        assert call_kwargs[0][1] == "vision"  # purpose
        assert call_kwargs[0][2] == "video"  # media_type
