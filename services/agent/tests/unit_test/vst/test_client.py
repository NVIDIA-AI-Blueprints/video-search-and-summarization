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
"""Tests for the VST client: URL quoting, timeouts, error wrapping, retries."""

from __future__ import annotations

import json
from typing import Any

import aiohttp
import pytest

from lib.search_core.errors import BackendUnreachableError
from lib.search_core.errors import SearchError
from lib.vst import VSTClient
from lib.vst import VSTError
from lib.vst import build_screenshot_url
from lib.vst import get_name_to_stream_id_map
from lib.vst import get_streams_info
from lib.vst import get_video_clip_url
from lib.vst.client import _VST_RETRYABLE_ERRORS

# --------------------------------------------------------------------- fakes


class _FakeVSTResponse:
    def __init__(self, status: int, text: str) -> None:
        self._status = status
        self._text = text

    async def __aenter__(self) -> _FakeVSTResponse:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    @property
    def status(self) -> int:
        return self._status

    async def text(self) -> str:
        return self._text


class _FakeVSTSession:
    def __init__(self, status: int, text: str, record: dict[str, Any]) -> None:
        self._status = status
        self._text = text
        self._record = record

    async def __aenter__(self) -> _FakeVSTSession:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    def get(self, url: str) -> _FakeVSTResponse:
        self._record["calls"] += 1
        self._record["urls"].append(url)
        return _FakeVSTResponse(self._status, self._text)


def _install_fake_session(monkeypatch: pytest.MonkeyPatch, status: int, text: str) -> dict[str, Any]:
    record: dict[str, Any] = {"calls": 0, "urls": [], "timeout": None}

    def factory(*, timeout: Any = None) -> _FakeVSTSession:
        record["timeout"] = timeout
        return _FakeVSTSession(status, text, record)

    monkeypatch.setattr(aiohttp, "ClientSession", factory)
    return record


# ------------------------------------------------------------ VSTError shape


def test_vst_error_is_backend_unreachable_with_backend() -> None:
    err = VSTError("boom")
    assert isinstance(err, BackendUnreachableError)
    assert isinstance(err, SearchError)
    assert err.backend == "vst"
    assert "vst: boom" in str(err)


# ------------------------------------------------------------ URL injection


def test_build_screenshot_url_quotes_path_and_query() -> None:
    url = build_screenshot_url("http://vst.example/", "cam/../evil", "2026-01-01T00:00:00Z")
    # '/' in the stream id must be percent-encoded so it cannot alter the path.
    assert "cam%2F..%2Fevil" in url
    assert "/cam/../evil/" not in url
    # ':' in the timestamp query value must be encoded.
    assert "%3A" in url
    assert "/picture?startTime=" in url


@pytest.mark.asyncio
async def test_get_video_clip_url_quotes_stream_id(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _install_fake_session(monkeypatch, 200, json.dumps({"videoUrl": "http://clip"}))

    result = await get_video_clip_url(
        stream_id="a/b",
        start_time="2026-01-01T00:00:00Z",
        end_time="2026-01-01T00:00:10Z",
        vst_internal_url="http://internal",
    )

    assert result == "http://clip"
    assert "storage/file/a%2Fb/url" in record["urls"][0]


# --------------------------------------------------------------- timeouts


@pytest.mark.asyncio
async def test_get_streams_info_sets_default_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _install_fake_session(monkeypatch, 200, json.dumps([]))
    await get_streams_info("http://internal")
    assert isinstance(record["timeout"], aiohttp.ClientTimeout)
    assert record["timeout"].total == 30


@pytest.mark.asyncio
async def test_vst_client_threads_runtime_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _install_fake_session(monkeypatch, 200, json.dumps([{"sid-1": [{"name": "cam"}]}]))
    client = VSTClient(internal_url="http://internal", external_url="http://external", timeout_seconds=5)

    stream_id = await client.resolve_stream_id("cam")

    assert stream_id == "sid-1"
    assert record["timeout"].total == 5


# ------------------------------------------------ raw-leak wrapping / shapes


@pytest.mark.asyncio
async def test_get_streams_info_wraps_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_session(monkeypatch, 200, "this is not json")
    with pytest.raises(VSTError):
        await get_streams_info("http://internal")


@pytest.mark.asyncio
async def test_get_name_to_stream_id_map_wraps_malformed_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    # A dict payload (expected: list) must map cleanly, not leak a raw error.
    _install_fake_session(monkeypatch, 200, json.dumps({"unexpected": "dict"}))
    with pytest.raises(VSTError, match="Unexpected VST streams response shape"):
        await get_name_to_stream_id_map("http://internal")


@pytest.mark.asyncio
async def test_get_name_to_stream_id_map_skips_malformed_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [{"good": [{"name": "cam-a"}]}, {"empty": []}, "junk"]
    _install_fake_session(monkeypatch, 200, json.dumps(payload))
    mapping = await get_name_to_stream_id_map("http://internal")
    assert mapping == {"cam-a": "good"}


# ------------------------------------------------------------- retry policy


def test_retryable_errors_are_narrowed() -> None:
    assert aiohttp.ClientConnectionError in _VST_RETRYABLE_ERRORS
    assert TimeoutError in _VST_RETRYABLE_ERRORS
    assert Exception not in _VST_RETRYABLE_ERRORS


@pytest.mark.asyncio
async def test_non_200_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _install_fake_session(monkeypatch, 500, "server error")
    with pytest.raises(VSTError):
        await get_streams_info("http://internal")
    # Deterministic HTTP failure must fail fast — a single attempt, no retries.
    assert record["calls"] == 1
