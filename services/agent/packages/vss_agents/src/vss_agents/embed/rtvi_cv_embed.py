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
import logging
from typing import cast

import httpx
from typing_extensions import override  # noqa: UP035  # mypy targets 3.11

from vss_agents.embed.embed import EmbedClient
from vss_agents.embed.embed import LRUEmbeddingCache

logger = logging.getLogger(__name__)

_TEXT_EMBEDDING_CACHE_MAXSIZE = 1024

# RTVI CV serves HTTP before its DeepStream text-embedder TensorRT engine is
# loaded, and answers 200 with an empty `data` list until it is. That window is
# transient, so retry it rather than failing a search outright. Three attempts
# with linear backoff spans ~6s -- long enough for a service that is otherwise
# ready, short enough that a genuinely broken embedder still fails promptly.
_EMPTY_DATA_RETRIES = 3
_EMPTY_DATA_BACKOFF_S = 2.0


class _EmbedderWarmingUpError(RuntimeError):
    """RTVI CV answered 200 but has no embedding to give yet.

    Deliberately not a ValueError: the parse-failure handler below must not
    absorb it, because unlike a malformed payload this case is worth retrying.
    """


class RTVICVEmbedClient(EmbedClient):
    """RTVI CV embedding client for text embeddings."""

    def __init__(self, endpoint: str):
        """
        Initialize RTVI CV embedding client.

        Args:
            endpoint: RTVI CV base URL
        """
        self.endpoint = endpoint.rstrip("/")
        self.text_embeddings_url = f"{self.endpoint}/api/v1/generate_text_embeddings"
        # Bounded LRU cache for text embeddings (with per-key async locks)
        self._text_cache = LRUEmbeddingCache(maxsize=_TEXT_EMBEDDING_CACHE_MAXSIZE)

    @override
    async def aclose(self) -> None:
        """Clear cached embeddings and locks."""
        self._text_cache.clear()

    @override
    async def get_text_embedding(self, text: str) -> list[float]:
        """Generate embedding for text input using RTVI CV API.

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

            embedding = await self._fetch_text_embedding(text)
            self._text_cache.put(text, embedding)
            return embedding

    async def _fetch_text_embedding(self, text: str) -> list[float]:
        """Fetch a text embedding, retrying only while the embedder warms up.

        A transport failure or an unrecognised payload is a real fault and is
        raised on the first occurrence. An empty ``data`` list is not: it means
        the engine is still loading, and retrying there is the difference
        between a slow search and a failed one.
        """
        warming: _EmbedderWarmingUpError | None = None
        for attempt in range(1, _EMPTY_DATA_RETRIES + 1):
            try:
                return await self._fetch_text_embedding_once(text)
            except _EmbedderWarmingUpError as exc:
                warming = exc
                if attempt < _EMPTY_DATA_RETRIES:
                    delay = _EMPTY_DATA_BACKOFF_S * attempt
                    logger.warning(
                        f"RTVI CV has no text embedding yet ({exc}); attempt "
                        f"{attempt}/{_EMPTY_DATA_RETRIES}, retrying in {delay:.0f}s"
                    )
                    await asyncio.sleep(delay)

        logger.error(f"Failed to parse RTVI CV response: {warming}")
        raise ValueError(
            f"Invalid RTVI CV response format: {warming} (unchanged over "
            f"{_EMPTY_DATA_RETRIES} attempts, so the text-embedder engine is "
            f"not merely warming up)"
        ) from warming

    async def _fetch_text_embedding_once(self, text: str) -> list[float]:
        """Fetch text embedding from RTVI CV API."""
        payload = {
            "text_input": text,
            "model": "",
        }

        try:
            timeout = httpx.Timeout(connect=30.0, read=120.0, write=120.0, pool=30.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(self.text_embeddings_url, json=payload)
                response.raise_for_status()
                result = response.json()

            # Extract embedding from response
            # Format 1: {"data": [{"embedding": [...]}]}
            # Format 2: {"data": [[...]]}
            if not result.get("data") or not isinstance(result["data"], list) or len(result["data"]) == 0:
                raise _EmbedderWarmingUpError("RTVI CV response missing or empty 'data' field")

            embedding_data = result["data"][0]

            if isinstance(embedding_data, list):
                return embedding_data
            elif isinstance(embedding_data, dict) and "embedding" in embedding_data:
                return cast("list[float]", embedding_data["embedding"])
            else:
                raise ValueError(f"Unexpected embedding data format: {type(embedding_data).__name__}")

        except httpx.HTTPError as e:
            logger.error(f"Failed to get text embedding from RTVI CV: {e}")
            raise
        except (KeyError, IndexError, TypeError, ValueError) as e:
            logger.error(f"Failed to parse RTVI CV response: {e}")
            raise ValueError(f"Invalid RTVI CV response format: {e}") from e

    @override
    async def get_image_embedding(self, image_url: str) -> list[float]:
        """Image embeddings not supported by RTVI CV client."""
        raise NotImplementedError("Image embeddings not supported by RTVI CV client")

    @override
    async def get_video_embedding(self, video_url: str) -> list[float]:
        """Video embeddings not supported by RTVI CV client."""
        raise NotImplementedError("Video embeddings not supported by RTVI CV client")
