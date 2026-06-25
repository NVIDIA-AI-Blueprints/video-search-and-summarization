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
"""TwelveLabs Marengo embedding client.

Opt-in alternative to the default ``CosmosEmbedClient``. Marengo is a
multimodal embedding model that maps text, images and video into a shared
512-dimensional space, so the same client can embed a search query and the
indexed media it is compared against.

Requires the optional ``twelvelabs`` dependency::

    uv sync --extra twelvelabs

and an API key (grab a free one at https://twelvelabs.io). The key is read from
``TWELVELABS_API_KEY`` by default, or passed explicitly to the constructor.

The official SDK is synchronous, so blocking calls are dispatched to a worker
thread via :func:`asyncio.to_thread` to satisfy the async ``EmbedClient``
contract without blocking the event loop.
"""

import asyncio
import logging
import os
import time
from typing import TYPE_CHECKING
from typing import Any

from typing_extensions import override  # noqa: UP035  # mypy targets 3.11

if TYPE_CHECKING:
    from twelvelabs.types.image_embedding_result import ImageEmbeddingResult
    from twelvelabs.types.text_embedding_result import TextEmbeddingResult

from vss_agents.embed.embed import EmbedClient
from vss_agents.embed.embed import LRUEmbeddingCache

logger = logging.getLogger(__name__)

# Marengo embeddings are fixed at 512 dimensions across all modalities.
TWELVELABS_EMBED_DIM = 512

_DEFAULT_MODEL = os.getenv("TWELVELABS_EMBED_MODEL", "marengo3.0")
_TEXT_EMBEDDING_CACHE_MAXSIZE = 1024
# Polling cadence for the asynchronous video-embedding task.
_VIDEO_POLL_INTERVAL_SEC = 5.0
_VIDEO_POLL_TIMEOUT_SEC = 600.0


class TwelveLabsEmbedClient(EmbedClient):
    """Embedding client backed by the TwelveLabs Marengo model.

    Text and image embeddings are synchronous round-trips. Video embeddings are
    produced by an asynchronous TwelveLabs task; this client submits the task,
    waits for completion and returns the whole-video (``"video"`` scope) vector.
    """

    def __init__(self, api_key: str | None = None, model_name: str = _DEFAULT_MODEL):
        """Initialize the TwelveLabs embedding client.

        Args:
            api_key: TwelveLabs API key. Defaults to the ``TWELVELABS_API_KEY``
                environment variable.
            model_name: Marengo model name. Defaults to ``TWELVELABS_EMBED_MODEL``
                or ``"marengo3.0"``.
        """
        key = api_key or os.getenv("TWELVELABS_API_KEY")
        if not key:
            raise ValueError(
                "TwelveLabs API key not provided. Set TWELVELABS_API_KEY or pass api_key=. "
                "Get a free key at https://twelvelabs.io."
            )
        try:
            from twelvelabs import TwelveLabs
        except ImportError as e:  # pragma: no cover - exercised only without the extra installed
            raise ImportError(
                "The 'twelvelabs' package is required for TwelveLabsEmbedClient. "
                "Install it with: uv sync --extra twelvelabs"
            ) from e

        self.model_name = model_name
        self._client = TwelveLabs(api_key=key)
        # Bounded LRU cache for text embeddings (with per-key async locks)
        self._text_cache = LRUEmbeddingCache(maxsize=_TEXT_EMBEDDING_CACHE_MAXSIZE)

    @override
    async def aclose(self) -> None:
        """Clear cached embeddings and locks."""
        self._text_cache.clear()

    @override
    async def get_text_embedding(self, text: str) -> list[float]:
        """Generate a 512-dim embedding for text input.

        Results are cached (bounded LRU) so concurrent callers with the same
        query share a single network round-trip.
        """
        cached = self._text_cache.get(text)
        if cached is not None:
            logger.debug(f"Text embedding cache hit for: {text[:80]}")
            return cached

        # Per-key lock so only one caller fetches a given text
        lock = self._text_cache.get_lock(text)

        async with lock:
            # Double-check after acquiring lock
            cached = self._text_cache.get(text)
            if cached is not None:
                return cached

            embedding = await asyncio.to_thread(self._fetch_text_embedding, text)
            self._text_cache.put(text, embedding)
            return embedding

    def _fetch_text_embedding(self, text: str) -> list[float]:
        """Fetch a text embedding from TwelveLabs (blocking; run in a thread)."""
        res = self._client.embed.create(model_name=self.model_name, text=text)
        return _extract_segment(res.text_embedding, "text")

    @override
    async def get_image_embedding(self, image_url: str) -> list[float]:
        """Generate a 512-dim embedding for an image URL."""
        return await asyncio.to_thread(self._fetch_image_embedding, image_url)

    def _fetch_image_embedding(self, image_url: str) -> list[float]:
        res = self._client.embed.create(model_name=self.model_name, image_url=image_url)
        return _extract_segment(res.image_embedding, "image")

    @override
    async def get_video_embedding(self, video_url: str) -> list[float]:
        """Generate a 512-dim whole-video embedding for a video URL.

        Submits an asynchronous TwelveLabs embedding task, waits for it to
        finish, then returns the ``"video"``-scope vector. May take tens of
        seconds depending on clip length.
        """
        return await asyncio.to_thread(self._fetch_video_embedding, video_url)

    def _fetch_video_embedding(self, video_url: str) -> list[float]:
        task = self._client.embed.tasks.create(
            model_name=self.model_name,
            video_url=video_url,
            video_embedding_scope=["video"],
        )
        task_id = task.id
        if not task_id:
            raise ValueError("TwelveLabs embedding task creation returned no task id")

        deadline = time.monotonic() + _VIDEO_POLL_TIMEOUT_SEC
        while True:
            status = self._client.embed.tasks.status(task_id=task_id)
            if status.status == "ready":
                break
            if status.status == "failed":
                raise ValueError(f"TwelveLabs video embedding task {task_id} failed")
            if time.monotonic() > deadline:
                raise TimeoutError(f"TwelveLabs video embedding task {task_id} did not finish in time")
            time.sleep(_VIDEO_POLL_INTERVAL_SEC)

        res = self._client.embed.tasks.retrieve(task_id=task_id)
        return _extract_segment(res.video_embedding, "video")


def _extract_segment(embedding: "TextEmbeddingResult | ImageEmbeddingResult | Any", kind: str) -> list[float]:
    """Pull the first segment's float vector out of an embedding response."""
    segments = getattr(embedding, "segments", None) if embedding else None
    if not segments:
        raise ValueError(f"TwelveLabs returned no {kind} embedding segments")
    floats = segments[0].float_
    if floats is None:
        raise ValueError(f"TwelveLabs {kind} embedding segment missing float values")
    return list(floats)
