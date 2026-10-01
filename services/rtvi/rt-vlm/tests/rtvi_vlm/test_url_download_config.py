# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CPU-only checks of URL safeguards in the production download path."""

import os
import socket
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.service_exception import ServiceException
from utils.asset_manager import (
    AssetManager,
    _parse_max_download_file_size_bytes,
    validate_url_ssrf_runtime_async,
)


async def _empty_chunks():
    yield b""


@pytest.mark.asyncio
async def test_redirect_revalidates_target_and_scopes_auth_and_ssl():
    origin = "https://media.example.com/video.mp4"
    target = "https://cdn.example.com/video.mp4"
    redirect = MagicMock(status=302, headers={"Location": target})
    redirect.release = AsyncMock()
    final = MagicMock(status=200)
    final.content.iter_chunked.return_value = _empty_chunks()
    final.release = AsyncMock()
    sessions = []

    def client_session(*, timeout, connector, headers):
        session = MagicMock(headers=headers)
        session.get = AsyncMock(return_value=[redirect, final][len(sessions)])
        session.close = AsyncMock()
        sessions.append(session)
        return session

    aiohttp = MagicMock()
    aiohttp.ClientSession.side_effect = client_session
    manager = object.__new__(AssetManager)
    manager.save_file = AsyncMock(return_value="asset-id")
    with (
        patch.dict(
            os.environ,
            {
                "ASSET_DOWNLOAD_MAX_REDIRECTS": "1",
                "ASSET_DOWNLOAD_SSL_SKIP_VERIFY_DOMAINS": "media.example.com",
                "ASSET_DOWNLOAD_AUTH_TOKENS": "cdn.example.com=Bearer cdn-token",
            },
        ),
        patch.dict(sys.modules, {"aiohttp": aiohttp}),
        patch(
            "utils.asset_manager.validate_url_ssrf_runtime_async", new_callable=AsyncMock
        ) as validate,
    ):
        result = await manager._download_file(
            origin,
            "video.mp4",
            "vision",
            "video",
            None,
            None,
            MagicMock(),
            {"Authorization": "Bearer origin-token", "Host": "evil.example.com"},
        )

    assert result == "asset-id"
    assert [s.get.await_args.args[0] for s in sessions] == [origin, target]
    assert [s.get.await_args.kwargs["allow_redirects"] for s in sessions] == [False, False]
    assert sessions[0].headers == {
        "User-Agent": "NVIDIA-RTVI/1.0",
        "Authorization": "Bearer origin-token",
    }
    assert sessions[1].headers == {
        "User-Agent": "NVIDIA-RTVI/1.0",
        "Authorization": "Bearer cdn-token",
    }
    assert [c.kwargs["ssl"] for c in aiohttp.TCPConnector.call_args_list] == [False, None]
    assert [c.args[0] for c in validate.await_args_list] == [origin, target]


@pytest.mark.asyncio
async def test_redirect_disabled_when_limit_is_zero():
    response = MagicMock(status=302, headers={"Location": "https://cdn.example.com/video.mp4"})
    response.release = AsyncMock()
    session = MagicMock()
    session.get = AsyncMock(return_value=response)
    session.close = AsyncMock()
    aiohttp = MagicMock()
    aiohttp.ClientSession.return_value = session
    manager = object.__new__(AssetManager)
    with (
        patch.dict(os.environ, {"ASSET_DOWNLOAD_MAX_REDIRECTS": "0"}),
        patch.dict(sys.modules, {"aiohttp": aiohttp}),
        patch(
            "utils.asset_manager.validate_url_ssrf_runtime_async", new_callable=AsyncMock
        ) as validate,
        pytest.raises(ServiceException, match="URL redirects are not allowed"),
    ):
        await manager._download_file(
            "https://media.example.com/video.mp4",
            "video.mp4",
            "vision",
            "video",
            None,
            None,
            MagicMock(),
        )
    validate.assert_awaited_once()
    session.close.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("ip", ["127.0.0.1", "169.254.169.254", "::1"])
async def test_ssrf_rejects_blocked_dns_results(ip):
    loop = MagicMock()
    loop.getaddrinfo = AsyncMock(return_value=[(socket.AF_INET, 0, 0, "", (ip, 0))])
    with (
        patch("utils.asset_manager.asyncio.get_event_loop", return_value=loop),
        pytest.raises(ServiceException, match="SSRF protection"),
    ):
        await validate_url_ssrf_runtime_async("https://media.example.com/video.mp4")


class TestMaxDownloadFileSizeConfig:
    def test_default_is_8_gib(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ASSET_DOWNLOAD_MAX_FILE_SIZE_GB", None)
            assert _parse_max_download_file_size_bytes() == 8 * 1024 * 1024 * 1024

    def test_env_override_allows_larger_downloads(self):
        with patch.dict(os.environ, {"ASSET_DOWNLOAD_MAX_FILE_SIZE_GB": "12"}):
            assert _parse_max_download_file_size_bytes() == 12 * 1024 * 1024 * 1024

    def test_fractional_gib_is_supported(self):
        with patch.dict(os.environ, {"ASSET_DOWNLOAD_MAX_FILE_SIZE_GB": "0.5"}):
            assert _parse_max_download_file_size_bytes() == 512 * 1024 * 1024

    @pytest.mark.parametrize("value", ["0", "-1", "abc"])
    def test_invalid_values_raise(self, value):
        with patch.dict(os.environ, {"ASSET_DOWNLOAD_MAX_FILE_SIZE_GB": value}):
            with pytest.raises(ValueError):
                _parse_max_download_file_size_bytes()
