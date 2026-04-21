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
import logging
import os

import httpx
from typing_extensions import override  # noqa: UP035  # mypy targets 3.11

from vss_agents.embed.embed import EmbedClient
from vss_agents.embed.embed import LRUEmbeddingCache

logger = logging.getLogger(__name__)

_EMBED_MODEL = os.getenv("RTVI_EMBED_MODEL", "cosmos-embed1-448p")
_TEXT_EMBEDDING_CACHE_MAXSIZE = 1024


class CosmosEmbedClient(EmbedClient):
    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self.model = _EMBED_MODEL
        self.text_embeddings_url = f"{endpoint}/v1/generate_text_embeddings"
        self.image_embeddings_url = f"{endpoint}/v1/generate_image_embeddings"
        self.video_embeddings_url = f"{endpoint}/v1/generate_video_embeddings"
        # Connection pooling: lazily created, reused across requests
        self._client: httpx.AsyncClient | None = None
        # Bounded LRU cache for text embeddings (with per-key async locks)
        self._text_cache = LRUEmbeddingCache(maxsize=_TEXT_EMBEDDING_CACHE_MAXSIZE)

    def _get_client(self) -> httpx.AsyncClient:
        """Get or create the shared httpx client (lazy initialization for connection pooling)."""
        if self._client is None:
            timeout = httpx.Timeout(connect=30.0, read=120.0, write=120.0, pool=30.0)
            self._client = httpx.AsyncClient(timeout=timeout)
        return self._client

    @override
    async def aclose(self) -> None:
        """Close the shared httpx client and clear caches."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._text_cache.clear()

    @override
    async def get_image_embedding(self, image_url: str) -> list[float]:
        """Generate embedding for image input"""
        # Handles base64 data URI and presigned_url format
        if image_url.startswith("data:image/"):
            # base64 URI ("data:image/jpeg;base64,...")
            formatted_input = image_url
        else:
            # presigned_url format
            formatted_input = f"data:image/jpeg;presigned_url,{image_url}"

        payload = {
            "input": [formatted_input],
            "request_type": "query",
            "encoding_format": "float",
            "model": self.model,
        }
        try:
            response = await self._get_client().post(self.image_embeddings_url, json=payload)
            response.raise_for_status()
            result = response.json()
            embedding: list[float] = result["data"][0]["embedding"]
            return embedding
        except httpx.HTTPError as e:
            logger.error(f"Failed to get image embedding: {e}")
            raise

    @override
    async def get_text_embedding(self, text: str) -> list[float]:
        """Generate embedding for text input.

        Results are cached (bounded LRU) so concurrent callers with the same
        query share a single network round-trip and avoid redundant work.
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
        """Fetch text embedding from Cosmos Embed API."""
        payload = {
            "text_input": [text],
            "model": self.model,
        }

        try:
            response = await self._get_client().post(self.text_embeddings_url, json=payload)
            response.raise_for_status()
            result = response.json()
            embeddings: list[float] = result["data"][0]["embeddings"]
            return embeddings
        except httpx.HTTPError as e:
            logger.error(f"Failed to get text embedding: {e}")
            raise

    @override
    async def get_video_embedding(self, video_url: str) -> list[float]:
        """Generate embedding for video input"""
        return (await self.get_video_embeddings_from_urls([video_url]))[0]

    async def get_video_embeddings_from_urls(self, urls: list[str]) -> list[list[float]]:
        """Generate embeddings for videos from URLs (public or presigned)"""
        logger.info(f"Generating embeddings for {len(urls)} video chunks via URLs")

        # Format URLs according to the required format
        formatted_urls = [f"data:video/mp4;presigned_url,{url}" for url in urls]

        payload = {
            "input": formatted_urls,
            "model": self.model,
            "encoding_format": "float",
            "request_type": "bulk_video",
        }
        logger.info(f"Payload: {payload}")

        response = await self._get_client().post(self.video_embeddings_url, json=payload)
        response.raise_for_status()
        result = response.json()

        # Extract embeddings from response
        embeddings = [item["embedding"] for item in result["data"]]
        logger.info(f"Successfully generated {len(embeddings)} embeddings")
        return embeddings
