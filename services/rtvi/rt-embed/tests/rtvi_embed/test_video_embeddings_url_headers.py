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
"""Unit tests for url_headers in VideoEmbeddingsQuery and generate_video_embeddings.

Tests cover:
- VideoEmbeddingsQuery.url_headers defaults to None
- VideoEmbeddingsQuery.url_headers accepts standard auth headers
- generate_video_embeddings passes url_headers to download_file for http/https URLs
- generate_video_embeddings passes url_headers=None when not provided
- url_headers is NOT forwarded to save_from_base64 (data: URI path)
- url_headers is NOT forwarded to add_file (file:// path)

No GPU, no running service required. All external I/O is mocked.
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

_MODEL = "cosmos-embed1-448p"
_MP4_FTYP_B64 = base64.b64encode(
    b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2avc1mp41"
).decode()


# ---------------------------------------------------------------------------
# VideoEmbeddingsQuery field validation
# ---------------------------------------------------------------------------


class TestVideoEmbeddingsQueryUrlHeaders:
    """Unit tests for VideoEmbeddingsQuery.url_headers field."""

    _id = str(uuid.uuid4())

    def _make(self, **kwargs) -> VideoEmbeddingsQuery:
        return VideoEmbeddingsQuery(
            id=self._id,
            url="https://example.com/video.mp4",
            model=_MODEL,
            **kwargs,
        )

    def test_url_headers_defaults_to_none(self):
        """url_headers must default to None when not provided."""
        q = self._make()
        assert q.url_headers is None

    def test_url_headers_none_explicit(self):
        """Explicitly passing None must be accepted."""
        q = self._make(url_headers=None)
        assert q.url_headers is None

    def test_url_headers_authorization_accepted(self):
        """Authorization header must be accepted."""
        headers = {"Authorization": "Bearer some-token"}
        q = self._make(url_headers=headers)
        assert q.url_headers == headers

    def test_url_headers_basic_auth_accepted(self):
        """Basic auth header must be accepted."""
        headers = {"Authorization": "Basic dXNlcjp0b2tlbg=="}
        q = self._make(url_headers=headers)
        assert q.url_headers == headers

    def test_url_headers_x_api_key_accepted(self):
        """x-api-key header must be accepted."""
        headers = {"x-api-key": "tok"}
        q = self._make(url_headers=headers)
        assert q.url_headers == headers

    def test_url_headers_multiple_headers_accepted(self):
        """Multiple headers must be accepted."""
        headers = {"Authorization": "Bearer tok", "Accept": "application/json"}
        q = self._make(url_headers=headers)
        assert q.url_headers == headers

    def test_url_headers_empty_dict_accepted(self):
        """Empty dict must be accepted (treated as no headers)."""
        q = self._make(url_headers={})
        assert q.url_headers == {}

    def test_url_headers_present_without_url(self):
        """url_headers should be accepted even when url is not set (id-based lookup)."""
        q = VideoEmbeddingsQuery(
            id=self._id,
            model=_MODEL,
            url_headers={"Authorization": "Bearer tok"},
        )
        assert q.url_headers == {"Authorization": "Bearer tok"}


# ---------------------------------------------------------------------------
# Server dispatch: url_headers forwarded to download_file for http/https
# ---------------------------------------------------------------------------


def _make_mock_asset_manager(asset_id=None):
    am = MagicMock()
    fixed_id = asset_id or str(uuid.uuid4())
    am.check_asset_exists.return_value = False
    am.download_file = AsyncMock(return_value=fixed_id)
    am.download_file_from_s3 = AsyncMock(return_value=fixed_id)
    am.save_from_base64 = AsyncMock(return_value=fixed_id)
    am.add_file = MagicMock(return_value=fixed_id)
    am.cleanup_asset = MagicMock()
    fake_asset = MagicMock()
    fake_asset.is_live = False
    fake_asset.media_type = "video"
    am.get_asset.return_value = fake_asset
    return am


def _make_mock_stream_handler():
    from server.rtvi_stream_handler import RequestInfo

    sh = MagicMock()
    sh.get_models_info.return_value = MagicMock(id=_MODEL)
    sh.generate_vlm_captions.return_value = str(uuid.uuid4())
    sh.wait_for_request_done = MagicMock()
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
    sh.get_response.return_value = (req_info, [])
    return sh


async def _invoke_handler(server, query):
    """Find and call the generate_video_embeddings POST endpoint."""
    from server.rtvi_embed_server import API_PREFIX

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
    assert endpoint is not None, "generate_video_embeddings endpoint not found"
    await endpoint(query, request)


class TestGenerateVideoEmbeddingsUrlHeadersDispatch:
    """Server dispatch tests: url_headers forwarding to download_file."""

    @pytest.fixture
    def server_and_am(self, tmp_path):
        from server.rtvi_embed_server import RTVIServer

        args = argparse.Namespace(
            asset_dir=str(tmp_path),
            max_asset_storage_size=None,
            max_live_streams=2,
            host="127.0.0.1",
            port="8017",
        )
        sh = _make_mock_stream_handler()
        with patch("server.rtvi_embed_server.RTVIStreamHandler", return_value=sh):
            server = RTVIServer(args)
        am = _make_mock_asset_manager()
        server._asset_manager = am
        return server, am

    @pytest.mark.asyncio
    async def test_url_headers_forwarded_to_download_file(self, server_and_am):
        """download_file must be called with the provided url_headers."""
        server, am = server_and_am
        headers = {"Authorization": "Bearer test-token"}
        query = VideoEmbeddingsQuery(
            id=str(uuid.uuid4()),
            url="https://example.com/video.mp4",
            model=_MODEL,
            url_headers=headers,
        )

        with patch("utils.asset_manager.validate_url_ssrf_runtime_async", new_callable=AsyncMock):
            await _invoke_handler(server, query)

        am.download_file.assert_awaited_once()
        _, kwargs = am.download_file.call_args
        assert kwargs.get("url_headers") == headers

    @pytest.mark.asyncio
    async def test_url_headers_none_forwarded_to_download_file(self, server_and_am):
        """download_file must be called with url_headers=None when not provided."""
        server, am = server_and_am
        query = VideoEmbeddingsQuery(
            id=str(uuid.uuid4()),
            url="https://example.com/video.mp4",
            model=_MODEL,
        )

        with patch("utils.asset_manager.validate_url_ssrf_runtime_async", new_callable=AsyncMock):
            await _invoke_handler(server, query)

        am.download_file.assert_awaited_once()
        _, kwargs = am.download_file.call_args
        assert kwargs.get("url_headers") is None

    @pytest.mark.asyncio
    async def test_url_headers_not_forwarded_to_save_from_base64(self, server_and_am):
        """data: URI path must not receive url_headers (save_from_base64 has no such param)."""
        server, am = server_and_am
        query = VideoEmbeddingsQuery(
            id=str(uuid.uuid4()),
            url=f"data:video/mp4;base64,{_MP4_FTYP_B64}",
            model=_MODEL,
            url_headers={"Authorization": "Bearer tok"},
        )

        await _invoke_handler(server, query)

        am.save_from_base64.assert_awaited_once()
        am.download_file.assert_not_called()
        # Verify save_from_base64 was NOT called with url_headers
        _, kwargs = am.save_from_base64.call_args
        assert "url_headers" not in kwargs

    @pytest.mark.asyncio
    async def test_url_headers_not_forwarded_to_add_file(self, server_and_am, tmp_path):
        """file:// path must not receive url_headers (add_file has no such param)."""
        server, am = server_and_am
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")
        query = VideoEmbeddingsQuery(
            id=str(uuid.uuid4()),
            url=f"file://{video}",
            model=_MODEL,
            url_headers={"Authorization": "Bearer tok"},
        )

        with patch.dict(os.environ, {"FILE_URL_ALLOWED_DIRS": str(tmp_path)}):
            await _invoke_handler(server, query)

        am.add_file.assert_called_once()
        am.download_file.assert_not_called()
        _, kwargs = am.add_file.call_args
        assert "url_headers" not in kwargs
