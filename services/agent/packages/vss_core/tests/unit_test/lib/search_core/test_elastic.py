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
"""Tests for ElasticClient error mapping and loop-affine registry."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from elasticsearch import ConnectionError as ESConnectionError
from elasticsearch import NotFoundError as ESNotFoundError
import pytest

import vss_core.search_core.clients.elastic as elastic_module
from vss_core.search_core.clients.elastic import ElasticClient
from vss_core.search_core.clients.elastic import _redact_endpoint
from vss_core.search_core.errors import BackendUnreachableError
from vss_core.search_core.errors import IndexNotFoundError


class _RaisingES:
    """Stand-in AsyncElasticsearch whose .search() raises a preset exception."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def search(self, **_kwargs: Any) -> Any:
        raise self._exc


@pytest.mark.asyncio
async def test_search_maps_not_found_to_index_not_found_error() -> None:
    raw = ESNotFoundError("index_not_found_exception", SimpleNamespace(status=404), {})
    client = ElasticClient(endpoint="http://es", client=_RaisingES(raw))  # type: ignore[arg-type]

    with pytest.raises(IndexNotFoundError) as excinfo:
        await client.search(index="videos", body={"query": {}})

    err = excinfo.value
    assert isinstance(err, BackendUnreachableError)
    assert err.backend == "elasticsearch"
    assert err.index == "videos"
    assert err.__cause__ is raw


@pytest.mark.asyncio
async def test_search_maps_connection_error_to_backend_unreachable() -> None:
    raw = ESConnectionError("boom")
    client = ElasticClient(endpoint="http://es", client=_RaisingES(raw))  # type: ignore[arg-type]

    with pytest.raises(BackendUnreachableError) as excinfo:
        await client.search(index="videos", body=None)

    err = excinfo.value
    assert not isinstance(err, IndexNotFoundError)
    assert err.backend == "elasticsearch"
    assert err.__cause__ is raw


@pytest.mark.asyncio
async def test_registry_race_schedules_awaitable_loser_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    """A race loser is closed through the public async API, never abandoned."""

    closed = asyncio.Event()

    class _FakeAsyncElasticsearch:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def close(self) -> None:
            closed.set()

    class _LosingRegistry(dict[Any, Any]):
        def setdefault(self, key: Any, value: Any) -> Any:
            winner = _FakeAsyncElasticsearch()
            super().__setitem__(key, winner)
            return winner

    monkeypatch.setattr(elastic_module, "AsyncElasticsearch", _FakeAsyncElasticsearch)
    monkeypatch.setattr(ElasticClient, "_clients", _LosingRegistry())
    monkeypatch.setattr(ElasticClient, "_ref_counts", {})

    client = ElasticClient.from_endpoint("http://es")

    assert isinstance(client.raw, _FakeAsyncElasticsearch)
    await asyncio.wait_for(closed.wait(), timeout=1)


def test_registry_is_loop_affine() -> None:
    ElasticClient._clients.clear()
    ElasticClient._ref_counts.clear()
    endpoint = "http://localhost:19299"
    # Hold both loops for the whole test so they stay distinct live objects
    # (avoids the id/object reuse that a closed-then-freed loop would allow).
    loop_a = asyncio.new_event_loop()
    loop_b = asyncio.new_event_loop()

    async def make() -> Any:
        first = ElasticClient.from_endpoint(endpoint)
        second = ElasticClient.from_endpoint(endpoint)
        # Same endpoint on the same loop shares one underlying client.
        assert first.raw is second.raw
        return first.raw

    try:
        raw_a = loop_a.run_until_complete(make())
        raw_b = loop_b.run_until_complete(make())

        # Distinct event loops must NOT share a client (reuse across loops would
        # raise "event loop is closed"): one registry entry per (endpoint, loop).
        assert raw_a is not raw_b
        endpoint_keys = [key for key in ElasticClient._clients if key[0] == endpoint]
        assert len(endpoint_keys) == 2
    finally:
        loop_a.run_until_complete(ElasticClient.close_all())
        assert len(ElasticClient._clients) == 0
        loop_a.close()
        loop_b.close()


def test_close_all_clears_registry_with_tuple_keys() -> None:
    ElasticClient._clients.clear()
    ElasticClient._ref_counts.clear()

    async def scenario() -> None:
        ElasticClient.from_endpoint("http://localhost:19298")
        assert len(ElasticClient._clients) == 1
        await ElasticClient.close_all()
        assert len(ElasticClient._clients) == 0

    asyncio.run(scenario())


def test_registry_prunes_clients_bound_to_closed_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = asyncio.Event()

    class _FakeAsyncElasticsearch:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def close(self) -> None:
            closed.set()

    monkeypatch.setattr(elastic_module, "AsyncElasticsearch", _FakeAsyncElasticsearch)
    stale_loop = asyncio.new_event_loop()
    stale_client = _FakeAsyncElasticsearch()
    stale_loop.close()
    ElasticClient._clients = {("http://stale", stale_loop): stale_client}  # type: ignore[assignment]

    async def scenario() -> None:
        ElasticClient.from_endpoint("http://live")
        assert ("http://stale", stale_loop) not in ElasticClient._clients
        await asyncio.wait_for(closed.wait(), timeout=1)
        await ElasticClient.close_all()

    asyncio.run(scenario())


def test_shared_transport_closes_after_final_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeAsyncElasticsearch:
        def __init__(self, **_kwargs: Any) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(elastic_module, "AsyncElasticsearch", _FakeAsyncElasticsearch)
    ElasticClient._clients.clear()
    ElasticClient._ref_counts.clear()

    async def scenario() -> None:
        first = ElasticClient.from_endpoint("http://es")
        second = ElasticClient.from_endpoint("http://es")
        raw = first.raw
        assert raw is second.raw
        await first.aclose()
        assert raw.closed is False
        await second.aclose()
        assert raw.closed is True
        assert ElasticClient._clients == {}
        assert ElasticClient._ref_counts == {}

    asyncio.run(scenario())


def test_synchronous_factory_rebinds_across_asyncio_run_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[Any] = []

    class _FakeAsyncElasticsearch:
        def __init__(self, **_kwargs: Any) -> None:
            self.loop: asyncio.AbstractEventLoop | None = None
            self.closed = False
            created.append(self)

        async def search(self, **_kwargs: Any) -> dict[str, Any]:
            loop = asyncio.get_running_loop()
            if self.loop is not None and self.loop is not loop:
                raise RuntimeError("client reused across loops")
            self.loop = loop
            return {"hits": {"hits": []}}

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(elastic_module, "AsyncElasticsearch", _FakeAsyncElasticsearch)
    monkeypatch.setattr(ElasticClient, "_clients", {})
    monkeypatch.setattr(ElasticClient, "_ref_counts", {})
    client = ElasticClient.from_endpoint("http://es")
    assert not created

    # Drive two independent loops explicitly instead of relying on
    # ``asyncio.run`` allocating a fresh loop per call: environments with
    # nest_asyncio-style patching (e.g. the nvidia-nat test plugin loaded in
    # the full agent test env) make ``asyncio.run`` reuse one persistent loop,
    # which would silently skip the cross-loop rebind path under test.
    loop_a = asyncio.new_event_loop()
    try:
        loop_a.run_until_complete(client.search(index="videos", body={"query": {}}))
    finally:
        loop_a.close()
    first = created[0]

    loop_b = asyncio.new_event_loop()
    try:
        loop_b.run_until_complete(client.search(index="videos", body={"query": {}}))
        # One extra tick lets the close task scheduled for the stale loop-A
        # client run to completion.
        loop_b.run_until_complete(asyncio.sleep(0))
        assert len(created) == 2
        assert first.closed is True
        loop_b.run_until_complete(ElasticClient.close_all())
    finally:
        loop_b.close()


def test_redact_endpoint_strips_userinfo() -> None:
    # Assemble the credentialed URL from parts so the committed source carries no
    # literal "user:pass@host" string (which trips secret scanners). The helper
    # under test must strip the userinfo component from the endpoint.
    userinfo_user = "u5er"
    userinfo_pass = "s3cret"
    redacted = _redact_endpoint(f"http://{userinfo_user}:{userinfo_pass}@es.example:9200/path")
    assert userinfo_user not in redacted
    assert userinfo_pass not in redacted
    assert "es.example:9200" in redacted


def test_redact_endpoint_scrubs_control_characters() -> None:
    assert "\n" not in _redact_endpoint("http://es\nINJECTED")
