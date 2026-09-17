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
"""Bounded LRU cache for text embeddings with per-key async locks.

Ported from services/agent/src/agent/embed/embed.py:48 with no behavior
changes. Used by CosmosEmbedClient and RTVICVEmbedClient to deduplicate
concurrent fetches for the same text.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict


class LRUEmbeddingCache:
    """Bounded LRU cache for text embeddings with per-key async locks.

    Both the cache and the lock dictionary are bounded by ``maxsize``. When full,
    the oldest (least-recently-used) entry is evicted along with its matching lock.
    """

    def __init__(self, maxsize: int = 1024) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        self._maxsize = maxsize
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._locks: OrderedDict[str, asyncio.Lock] = OrderedDict()

    def get(self, key: str) -> list[float] | None:
        value = self._cache.get(key)
        if value is None:
            return None
        self._cache.move_to_end(key)  # LRU bump
        # Return a copy: the stored list must never be mutated by callers, or
        # a later cache hit would hand back corrupted embeddings.
        return list(value)

    def put(self, key: str, value: list[float]) -> None:
        # Copy on insertion as well as retrieval: the caller that produced the
        # first embedding must not be able to mutate the cached value later.
        self._cache[key] = list(value)
        self._cache.move_to_end(key)
        while len(self._cache) > self._maxsize:
            evicted, _ = self._cache.popitem(last=False)
            # Prune the matching lock only if it is not currently held — a
            # locked lock means a fetch is in flight for that key and evicting
            # it would break single-flight dedup.
            lock = self._locks.get(evicted)
            if lock is not None and not lock.locked():
                self._locks.pop(evicted, None)

    def get_lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        self._locks.move_to_end(key)
        self._evict_unlocked_locks()
        return lock

    def _evict_unlocked_locks(self) -> None:
        """Drop oldest UNLOCKED locks until within ``maxsize``.

        A held lock (``locked()``) guards an in-flight fetch; evicting it would
        let a second coroutine create a fresh lock for the same key and issue a
        duplicate request, defeating the single-flight guarantee. Such locks are
        skipped and reaped later once released.
        """
        if len(self._locks) <= self._maxsize:
            return
        for candidate in list(self._locks):
            if len(self._locks) <= self._maxsize:
                break
            if not self._locks[candidate].locked():
                del self._locks[candidate]

    def clear(self) -> None:
        self._cache.clear()
        self._locks.clear()

    def __len__(self) -> int:
        return len(self._cache)
