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
import asyncio
from abc import ABC
from abc import abstractmethod
from collections import OrderedDict


class EmbedClient(ABC):
    """Abstract base class for embedding clients."""

    @abstractmethod
    async def get_image_embedding(self, image_url: str) -> list[float]:
        """Generate embedding for image input."""
        pass

    @abstractmethod
    async def get_text_embedding(self, text: str) -> list[float]:
        """Generate embedding for text input."""
        pass

    @abstractmethod
    async def get_video_embedding(self, video_url: str) -> list[float]:
        """Generate embedding for video input."""
        pass

    async def aclose(self) -> None:
        """Release any resources held by this client.

        Default implementation is a no-op. Subclasses with persistent HTTP clients
        or caches should override to close/clear them.
        """
        return None


class LRUEmbeddingCache:
    """Bounded LRU cache for text embeddings with per-key async locks.

    Both the cache and the lock dictionary are bounded by ``maxsize``. When full,
    the oldest (least-recently-used) entry is evicted along with its matching lock.
    """

    def __init__(self, maxsize: int = 1024):
        self._maxsize = maxsize
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._locks: OrderedDict[str, asyncio.Lock] = OrderedDict()

    def get(self, key: str) -> list[float] | None:
        value = self._cache.get(key)
        if value is not None:
            self._cache.move_to_end(key)  # LRU bump
        return value

    def put(self, key: str, value: list[float]) -> None:
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > self._maxsize:
            evicted, _ = self._cache.popitem(last=False)
            self._locks.pop(evicted, None)  # prune matching lock

    def get_lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        self._locks.move_to_end(key)
        # Bound locks dict independently; keys with cache entries will tend to stay
        while len(self._locks) > self._maxsize:
            self._locks.popitem(last=False)
        return lock

    def clear(self) -> None:
        self._cache.clear()
        self._locks.clear()

    def __len__(self) -> int:
        return len(self._cache)
