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

from lib.search_core.clients.elastic import ElasticClient
from lib.search_core.clients.elastic import _redact_endpoint
from lib.search_core.errors import BackendUnreachableError
from lib.search_core.errors import IndexNotFoundError


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


def test_registry_is_loop_affine() -> None:
    ElasticClient._clients.clear()
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

    async def scenario() -> None:
        ElasticClient.from_endpoint("http://localhost:19298")
        assert len(ElasticClient._clients) == 1
        await ElasticClient.close_all()
        assert len(ElasticClient._clients) == 0

    asyncio.run(scenario())


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
