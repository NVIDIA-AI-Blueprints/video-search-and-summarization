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
"""Cosmos embed client: text + image + video embeddings.

Ported from services/agent/src/agent/embed/cosmos_embed.py with ONE
behavior change: the embed model name no longer comes from
`os.getenv("RTVI_EMBED_MODEL", ...)` at module import time — it is passed in
via the constructor, and SearchRuntime.cosmos_embed_model carries it.
All other logic (HTTP shapes, LRU cache, lazy httpx client) is unchanged.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from typing import override

import httpx

from ..errors import BackendUnreachableError
from ._cache import LRUEmbeddingCache
from .embed_base import EmbedClient
from .embed_base import validate_embedding

if TYPE_CHECKING:
    from ..runtime import SearchRuntime

logger = logging.getLogger(__name__)

_TEXT_EMBEDDING_CACHE_MAXSIZE = 1024


class CosmosEmbedClient(EmbedClient):
    def __init__(self, endpoint: str, *, model: str = "cosmos-embed1-448p") -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.text_embeddings_url = f"{self.endpoint}/v1/generate_text_embeddings"
        self.image_embeddings_url = f"{self.endpoint}/v1/generate_image_embeddings"
        self.video_embeddings_url = f"{self.endpoint}/v1/generate_video_embeddings"
        # Connection pooling: lazily created, reused across requests.
        self._client: httpx.AsyncClient | None = None
        # Bounded LRU cache for text embeddings (with per-key async locks).
        self._text_cache = LRUEmbeddingCache(maxsize=_TEXT_EMBEDDING_CACHE_MAXSIZE)

    @classmethod
    def from_runtime(cls, rt: SearchRuntime) -> CosmosEmbedClient:
        return cls(rt.require("cosmos_embed_endpoint"), model=rt.cosmos_embed_model)

    def _get_client(self) -> httpx.AsyncClient:
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
        """Generate embedding for image input."""
        # Handles base64 data URI and presigned_url format.
        if image_url.startswith("data:image/"):
            formatted_input = image_url
        else:
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
            return validate_embedding(result["data"][0]["embedding"], backend="cosmos_embed")
        except httpx.HTTPError as e:
            logger.error(f"Failed to get image embedding: {e}")
            raise BackendUnreachableError("cosmos_embed", str(e), e) from e
        except (KeyError, IndexError, TypeError, ValueError) as e:
            logger.error(f"Failed to parse image embedding response: {e}")
            raise BackendUnreachableError("cosmos_embed", f"Invalid Cosmos Embed response format: {e}", e) from e

    @override
    async def get_text_embedding(self, text: str) -> list[float]:
        """Generate embedding for text input.

        Results are cached (bounded LRU) so concurrent callers with the same
        query share a single network round-trip.
        """
        cached = self._text_cache.get(text)
        if cached is not None:
            logger.debug(f"Text embedding cache hit for: {text[:80]}")
            return cached

        lock = self._text_cache.get_lock(text)
        async with lock:
            cached = self._text_cache.get(text)
            if cached is not None:
                return cached
            embedding = await self._fetch_text_embedding(text)
            self._text_cache.put(text, embedding)
            return embedding

    async def _fetch_text_embedding(self, text: str) -> list[float]:
        payload = {"text_input": [text], "model": self.model}
        try:
            response = await self._get_client().post(self.text_embeddings_url, json=payload)
            response.raise_for_status()
            result = response.json()
            return validate_embedding(result["data"][0]["embeddings"], backend="cosmos_embed")
        except httpx.HTTPError as e:
            logger.error(f"Failed to get text embedding: {e}")
            raise BackendUnreachableError("cosmos_embed", str(e), e) from e
        except (KeyError, IndexError, TypeError, ValueError) as e:
            logger.error(f"Failed to parse text embedding response: {e}")
            raise BackendUnreachableError("cosmos_embed", f"Invalid Cosmos Embed response format: {e}", e) from e

    @override
    async def get_video_embedding(self, video_url: str) -> list[float]:
        embeddings = await self.get_video_embeddings_from_urls([video_url])
        if not embeddings:
            raise BackendUnreachableError("cosmos_embed", "empty embedding response")
        return embeddings[0]

    async def get_video_embeddings_from_urls(self, urls: list[str]) -> list[list[float]]:
        logger.info(f"Generating embeddings for {len(urls)} video chunks via URLs")
        formatted_urls = [f"data:video/mp4;presigned_url,{url}" for url in urls]
        payload = {
            "input": formatted_urls,
            "model": self.model,
            "encoding_format": "float",
            "request_type": "bulk_video",
        }
        # NOTE: never log ``payload`` — the formatted inputs embed presigned URLs
        # (short-lived credentials). Log counts only.
        try:
            response = await self._get_client().post(self.video_embeddings_url, json=payload)
            response.raise_for_status()
            result = response.json()
            embeddings = [validate_embedding(item["embedding"], backend="cosmos_embed") for item in result["data"]]
            logger.info(f"Successfully generated {len(embeddings)} embeddings")
            return embeddings
        except httpx.HTTPError as e:
            logger.error(f"Failed to get video embeddings: {e}")
            raise BackendUnreachableError("cosmos_embed", str(e), e) from e
        except (KeyError, IndexError, TypeError, ValueError) as e:
            logger.error(f"Failed to parse video embedding response: {e}")
            raise BackendUnreachableError("cosmos_embed", f"Invalid Cosmos Embed response format: {e}", e) from e
