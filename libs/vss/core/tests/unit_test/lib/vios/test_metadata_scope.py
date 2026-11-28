# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Request sharing must preserve freshness, cancellation, and timeline semantics."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from vss_core.vios._metadata_cache import metadata_scope
from vss_core.vios._metadata_cache import metadata_text

KEY = ("fixture", 30.0, 3)


@pytest.mark.asyncio
async def test_nested_scope_shares_but_next_request_refreshes():
    fetch = AsyncMock(side_effect=["first", "second", "third"])
    async with metadata_scope():
        assert await metadata_text(KEY, fetch) == "first"
        async with metadata_scope():
            assert await metadata_text(KEY, fetch) == "first"
    async with metadata_scope():
        assert await metadata_text(KEY, fetch) == "second"
    assert await metadata_text(KEY, fetch) == "third"
    assert fetch.await_count == 3


@pytest.mark.asyncio
async def test_failed_reads_are_not_retained():
    fetch = AsyncMock(side_effect=[ValueError("bad"), "recovered"])
    async with metadata_scope():
        with pytest.raises(ValueError, match="bad"):
            await metadata_text(KEY, fetch)
        assert await metadata_text(KEY, fetch) == "recovered"


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_read():
    entered = asyncio.Event()
    release = asyncio.Event()

    async def fetch():
        entered.set()
        await release.wait()
        return "done"

    async with metadata_scope():
        first = asyncio.create_task(metadata_text(KEY, fetch))
        await entered.wait()
        second = asyncio.create_task(metadata_text(KEY, fetch))
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert await second == "done"


@pytest.mark.asyncio
async def test_parallel_requests_do_not_share():
    fetch = AsyncMock(return_value="value")

    async def request():
        async with metadata_scope():
            return await metadata_text(KEY, fetch)

    assert await asyncio.gather(request(), request()) == ["value", "value"]
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_timeline_consumers_keep_their_different_segment_semantics(monkeypatch):
    import json

    from vss_core.vios import client

    calls = []
    payload = {
        "id": [
            {"startTime": "2025-01-01T00:00:00Z", "endTime": "2025-01-01T00:00:02Z"},
            {"startTime": "2025-01-01T00:00:04Z", "endTime": "2025-01-01T00:00:06Z"},
        ]
    }

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def text(self):
            return json.dumps(payload)

    class Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def get(self, url):
            calls.append(url)
            return Response()

    monkeypatch.setattr(client.aiohttp, "ClientSession", Session)
    async with metadata_scope():
        first = await client.get_timeline("id", "http://fixture")
        envelope = await client.get_timelines_map("http://fixture")
    assert first == ("2025-01-01T00:00:00Z", "2025-01-01T00:00:02Z")
    assert envelope["id"] == ("2025-01-01T00:00:00Z", "2025-01-01T00:00:06Z")
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("first_payload", ["not json", "{}"])
async def test_rejected_stream_metadata_is_not_cached(monkeypatch, first_payload):
    from vss_core.vios import client

    responses = iter([first_payload, '[{"id": [{"name": "camera"}]}]'])
    reads = []

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def text(self):
            return next(responses)

    class Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def get(self, url):
            reads.append(url)
            return Response()

    monkeypatch.setattr(client.aiohttp, "ClientSession", Session)
    async with metadata_scope():
        with pytest.raises(client.VSTError):
            await client.get_name_to_stream_id_map("http://fixture")
        assert await client.get_name_to_stream_id_map("http://fixture") == {"camera": "id"}
    assert len(reads) == 2


@pytest.mark.asyncio
async def test_parsed_catalogue_is_shared_without_sharing_mutable_results(monkeypatch):
    from vss_core.vios import client

    fetch = AsyncMock(side_effect=[{"camera": "first"}, {"camera": "second"}])
    monkeypatch.setattr(client, "_get_name_to_stream_id_map", fetch)
    async with metadata_scope():
        first = await client.get_name_to_stream_id_map("http://fixture")
        first.clear()
        assert await client.get_name_to_stream_id_map(vst_internal_url="http://fixture/", timeout_seconds=30) == {
            "camera": "first"
        }
    async with metadata_scope():
        assert await client.get_name_to_stream_id_map("http://fixture") == {"camera": "second"}
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_parsed_timeline_views_keep_identity_and_retry_errors(monkeypatch):
    from vss_core.vios import client

    fetch = AsyncMock(side_effect=[client.VSTError("transient"), ("first", "end"), ("other", "end")])
    monkeypatch.setattr(client, "_get_timeline", fetch)
    async with metadata_scope():
        with pytest.raises(client.VSTError):
            await client.get_timeline("camera", "http://fixture")
        assert await client.get_timeline("camera", "http://fixture") == ("first", "end")
        assert await client.get_timeline("camera", "http://fixture") == ("first", "end")
        assert await client.get_timeline("other", "http://fixture") == ("other", "end")
    assert fetch.await_count == 3
