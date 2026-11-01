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

"""Remote text embedding client for incident evidence search.

Evidence descriptions (loader) and search queries (agent route) must be embedded by the same
model so cosine distance over ``evidence_crops.embedding vector(1024)`` is meaningful. This calls an
OpenAI-compatible ``POST {base_url}/embeddings`` endpoint (NVIDIA-hosted by default) and never runs a
local model.
"""

import asyncio
import logging
import os
import time
from typing import Literal

import httpx

from vss_agents.utils import run_provenance

logger = logging.getLogger(__name__)

EMBEDDING_DIMENSIONS = 1024
DEFAULT_EMBEDDING_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_EMBEDDING_MODEL = "nvidia/llama-nemotron-embed-vl-1b-v2"
DEFAULT_EMBEDDING_TIMEOUT_SECONDS = 15.0
EMBEDDING_BATCH_SIZE = 32
EMBEDDING_MAX_ATTEMPTS = 3
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
# The hosted endpoint answers HTTP 400 with this message when a batch (possibly other callers' inputs) is too long.
_TOKEN_LIMIT_ERROR = "exceeds maximum allowed token size"

InputType = Literal["query", "passage"]


def _api_key_from_env() -> str:
    for name in ("EVIDENCE_EMBEDDING_API_KEY", "NVIDIA_API_KEY", "NGC_CLI_API_KEY"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _is_retryable(resp: httpx.Response) -> bool:
    if resp.status_code in _RETRYABLE_STATUS_CODES:
        return True
    return resp.status_code == 400 and _TOKEN_LIMIT_ERROR in resp.text


class RemoteTextEmbedder:
    """Async client producing ``EMBEDDING_DIMENSIONS``-dimensional vectors from a remote endpoint.

    Settings (constructor argument, then environment, then default):
    ``EVIDENCE_EMBEDDING_BASE_URL``, ``EVIDENCE_EMBEDDING_MODEL`` and
    ``EVIDENCE_EMBEDDING_API_KEY`` (falls back to ``NVIDIA_API_KEY`` then ``NGC_CLI_API_KEY``).
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        dimensions: int = EMBEDDING_DIMENSIONS,
        timeout_seconds: float = DEFAULT_EMBEDDING_TIMEOUT_SECONDS,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("EVIDENCE_EMBEDDING_BASE_URL") or DEFAULT_EMBEDDING_BASE_URL).rstrip("/")
        self.model = model or os.getenv("EVIDENCE_EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL
        self.api_key = api_key if api_key is not None else _api_key_from_env()
        self.dimensions = dimensions
        self.timeout_seconds = timeout_seconds
        self._http_client = http_client

    def is_configured(self) -> bool:
        """Return True when an API key is available for the embedding endpoint."""
        return bool(self.api_key)

    async def embed(self, texts: list[str], *, input_type: InputType) -> list[list[float]]:
        """Embed texts, preserving order. Raises on endpoint failure or unexpected vector size."""
        if not texts:
            return []
        if not self.is_configured():
            raise ValueError(
                "Embedding API key is not configured (EVIDENCE_EMBEDDING_API_KEY, NVIDIA_API_KEY or NGC_CLI_API_KEY)"
            )
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            vectors.extend(await self._embed_batch(texts[start : start + EMBEDDING_BATCH_SIZE], input_type))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        """Embed one search query."""
        return (await self.embed([text], input_type="query"))[0]

    async def embed_passage(self, text: str) -> list[float]:
        """Embed one document passage (an evidence description)."""
        return (await self.embed([text], input_type="passage"))[0]

    async def _embed_batch(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        body = {
            "model": self.model,
            "input": texts,
            "input_type": input_type,
            "dimensions": self.dimensions,
            "encoding_format": "float",
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url}/embeddings"

        for attempt in range(1, EMBEDDING_MAX_ATTEMPTS + 1):
            started = time.perf_counter()
            try:
                resp = await self._post(url, body, headers)
            except Exception as exc:
                # Every attempt inside an analysis is counted (the revision's summary embedding); a no-op outside.
                run_provenance.record_call(
                    "embedding",
                    ok=False,
                    usage=None,
                    elapsed_ms=(time.perf_counter() - started) * 1000.0,
                    model=self.model,
                )
                if not isinstance(exc, httpx.ReadTimeout) or attempt == EMBEDDING_MAX_ATTEMPTS:
                    raise
                logger.warning("Embedding endpoint timed out; retrying (%d)", attempt)
                await asyncio.sleep(2.0 * attempt)
                continue
            run_provenance.record_call(
                "embedding",
                ok=resp.is_success,
                usage=run_provenance.usage_of(resp),
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                model=self.model,
            )
            if _is_retryable(resp) and attempt < EMBEDDING_MAX_ATTEMPTS:
                logger.warning("Embedding endpoint returned HTTP %d; retrying (%d)", resp.status_code, attempt)
                await asyncio.sleep(2.0 * attempt)
                continue
            if not resp.is_success:
                raise httpx.HTTPStatusError(
                    f"Embedding endpoint returned HTTP {resp.status_code}: {resp.text[:300]}",
                    request=resp.request,
                    response=resp,
                )
            break

        rows = sorted(resp.json()["data"], key=lambda row: row.get("index", 0))
        vectors = [[float(x) for x in row["embedding"]] for row in rows]
        if len(vectors) != len(texts) or any(len(v) != self.dimensions for v in vectors):
            raise ValueError(
                f"Embedding endpoint returned {len(vectors)} vectors of sizes "
                f"{sorted({len(v) for v in vectors})}; expected {len(texts)} x {self.dimensions}"
            )
        return vectors

    async def _post(self, url: str, body: dict[str, object], headers: dict[str, str]) -> httpx.Response:
        if self._http_client is not None:
            return await self._http_client.post(url, json=body, headers=headers, timeout=self.timeout_seconds)
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            return await client.post(url, json=body, headers=headers)
