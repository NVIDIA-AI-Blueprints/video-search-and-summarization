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
"""RTVI CV embedding client (text-only).

Ported from services/agent/src/agent/embed/rtvi_cv_embed.py with no
behavior changes. The original had no env reads.
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


class RTVICVEmbedClient(EmbedClient):
    """RTVI CV embedding client for text embeddings."""

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.text_embeddings_url = f"{self.endpoint}/api/v1/generate_text_embeddings"
        self._text_cache = LRUEmbeddingCache(maxsize=_TEXT_EMBEDDING_CACHE_MAXSIZE)
        self._client: httpx.AsyncClient | None = None

    @classmethod
    def from_runtime(cls, rt: SearchRuntime) -> RTVICVEmbedClient:
        return cls(rt.require("rtvi_cv_endpoint"))

    @override
    async def aclose(self) -> None:
        """Close the shared HTTP client and clear cached embeddings and locks."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._text_cache.clear()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            timeout = httpx.Timeout(connect=30.0, read=120.0, write=120.0, pool=30.0)
            self._client = httpx.AsyncClient(timeout=timeout)
        return self._client

    @override
    async def get_text_embedding(self, text: str) -> list[float]:
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
        payload = {"text_input": text, "model": ""}
        try:
            response = await self._get_client().post(self.text_embeddings_url, json=payload)
            response.raise_for_status()
            result = response.json()

            # Format 1: {"data": [{"embedding": [...]}]}
            # Format 2: {"data": [[...]]}
            if not isinstance(result, dict):
                raise ValueError(f"Unexpected RTVI CV response shape: {type(result).__name__}")
            if not result.get("data") or not isinstance(result["data"], list) or len(result["data"]) == 0:
                raise ValueError("RTVI CV response missing or empty 'data' field")

            embedding_data = result["data"][0]
            if isinstance(embedding_data, list):
                return validate_embedding(embedding_data, backend="rtvi_cv")
            if isinstance(embedding_data, dict) and "embedding" in embedding_data:
                return validate_embedding(embedding_data["embedding"], backend="rtvi_cv")
            raise ValueError(f"Unexpected embedding data format: {type(embedding_data).__name__}")

        except httpx.HTTPError as e:
            logger.error(f"Failed to get text embedding from RTVI CV: {e}")
            raise BackendUnreachableError("rtvi_cv", str(e), e) from e
        except (KeyError, IndexError, TypeError, ValueError) as e:
            logger.error(f"Failed to parse RTVI CV response: {e}")
            raise BackendUnreachableError("rtvi_cv", f"Invalid RTVI CV response format: {e}", e) from e

    @override
    async def get_image_embedding(self, image_url: str) -> list[float]:
        raise NotImplementedError("Image embeddings not supported by RTVI CV client")

    @override
    async def get_video_embedding(self, video_url: str) -> list[float]:
        raise NotImplementedError("Video embeddings not supported by RTVI CV client")
