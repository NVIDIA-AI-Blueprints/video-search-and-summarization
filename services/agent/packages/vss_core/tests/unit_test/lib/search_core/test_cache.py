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
"""Tests for LRUEmbeddingCache: copy-on-get, lock retention, single-flight."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from vss_core.search_core.clients._cache import LRUEmbeddingCache
from vss_core.search_core.clients.cosmos_embed import CosmosEmbedClient


def test_get_returns_a_copy() -> None:
    cache = LRUEmbeddingCache(maxsize=4)
    cache.put("k", [1.0, 2.0, 3.0])

    first = cache.get("k")
    assert first == [1.0, 2.0, 3.0]
    assert first is not None
    first.append(999.0)

    second = cache.get("k")
    assert second == [1.0, 2.0, 3.0]


def test_put_copies_the_producer_value() -> None:
    cache = LRUEmbeddingCache(maxsize=4)
    produced = [1.0, 2.0]
    cache.put("k", produced)
    produced.append(999.0)
    assert cache.get("k") == [1.0, 2.0]


def test_non_positive_maxsize_is_rejected() -> None:
    with pytest.raises(ValueError, match="maxsize"):
        LRUEmbeddingCache(maxsize=0)


@pytest.mark.asyncio
async def test_held_lock_survives_get_lock_eviction() -> None:
    cache = LRUEmbeddingCache(maxsize=1)
    held = cache.get_lock("a")
    await held.acquire()
    try:
        # Requesting another lock overflows maxsize and triggers eviction.
        cache.get_lock("b")
        # The in-flight (held) lock must NOT be evicted, or a second coroutine
        # would create a fresh lock for "a" and issue a duplicate fetch.
        assert "a" in cache._locks
        assert cache._locks["a"] is held
    finally:
        held.release()


@pytest.mark.asyncio
async def test_put_does_not_prune_held_lock() -> None:
    cache = LRUEmbeddingCache(maxsize=1)
    cache.put("a", [1.0])
    held = cache.get_lock("a")
    await held.acquire()
    try:
        cache.put("b", [2.0])  # evicts "a" from the value cache
        assert "a" in cache._locks
    finally:
        held.release()


class _CountingResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _CountingClient:
    def __init__(self) -> None:
        self.calls = 0

    async def post(self, *_args: Any, **_kwargs: Any) -> _CountingResponse:
        self.calls += 1
        await asyncio.sleep(0.05)
        return _CountingResponse({"data": [{"embeddings": [0.1, 0.2]}]})

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_single_flight_dedup_shares_one_fetch() -> None:
    client = CosmosEmbedClient("http://embed")
    counting = _CountingClient()
    client._client = counting  # type: ignore[assignment]

    first, second = await asyncio.gather(
        client.get_text_embedding("red forklift"),
        client.get_text_embedding("red forklift"),
    )

    # Both concurrent callers share a single network round-trip.
    assert counting.calls == 1
    assert first == [0.1, 0.2]
    assert second == [0.1, 0.2]
