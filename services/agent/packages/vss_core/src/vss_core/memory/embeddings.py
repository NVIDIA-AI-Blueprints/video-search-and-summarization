# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Canonical memory text and lightweight HTTP embedding providers."""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import math
import os
import re
from typing import Protocol
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

import httpx

from vss_core._foundation.time import datetime_to_iso8601

from .models import UnifiedMemoryRecord

DOCUMENT_INPUT_TYPE = "passage"
QUERY_INPUT_TYPE = "query"
SIMILARITY = "cosine"
_SEARCHABLE_CONTEXT_KEYS = ("description", "category", "kind", "original_query", "search_mode")
_ELIGIBLE_STATUSES = frozenset({"completed", "partial"})


class EmbeddingProviderError(RuntimeError):
    """An embedding request failed or returned an invalid response."""


class EmbeddingProvider(Protocol):
    """Dependency-injection boundary for text embedding services."""

    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    @property
    def resolved_model(self) -> str | None: ...

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...

    def close(self) -> None: ...


def _canonical_scalar(value: object) -> str | None:
    if isinstance(value, str):
        normalized = re.sub(r"\s+", " ", value.replace("\r\n", "\n").replace("\r", "\n")).strip()
        return normalized or None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return str(value)
    return None


def canonical_searchable_text(record: UnifiedMemoryRecord) -> str:
    """Build stable, deliberately bounded searchable text for one record."""
    lines = [
        f"Group: {record.job.group}",
        f"Record type: {record.job.record_type or 'parent_job'}",
    ]
    memory_input = record.input
    if memory_input is not None:
        for label, value in (("Intent", memory_input.intent), ("Query", memory_input.query)):
            scalar = _canonical_scalar(value)
            if scalar is not None:
                lines.append(f"{label}: {scalar}")
    if record.output is not None:
        answer = _canonical_scalar(record.output.answer)
        if answer is not None:
            lines.append(f"Answer: {answer}")
    if memory_input is not None:
        if memory_input.sensors:
            sensors = [_canonical_scalar(sensor.id) for sensor in memory_input.sensors]
            present = [sensor for sensor in sensors if sensor is not None]
            if present:
                lines.append(f"Sensors: {', '.join(present)}")
        if memory_input.window is not None:
            lines.append(f"Start: {datetime_to_iso8601(memory_input.window.start.timestamp)}")
            if memory_input.window.end is not None:
                lines.append(f"End: {datetime_to_iso8601(memory_input.window.end.timestamp)}")
    if record.output is not None and record.output.ext:
        context = [
            f"{key}={scalar}"
            for key in _SEARCHABLE_CONTEXT_KEYS
            if (scalar := _canonical_scalar(record.output.ext.get(key))) is not None
        ]
        if context:
            lines.append(f"Context: {', '.join(context)}")
    return "\n".join(lines).strip()


def content_hash(text_or_record: str | UnifiedMemoryRecord) -> str:
    """Return lowercase SHA-256 for canonical text or a record."""
    text = (
        canonical_searchable_text(text_or_record) if isinstance(text_or_record, UnifiedMemoryRecord) else text_or_record
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_embedding_eligible(record: UnifiedMemoryRecord) -> bool:
    """Whether a persisted parent or supported child has searchable content."""
    return record.job.status in _ELIGIBLE_STATUSES and bool(canonical_searchable_text(record))


def _embeddings_url(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    path = parsed.path.rstrip("/")
    if not path.endswith("/embeddings"):
        path = f"{path}/embeddings"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


class OpenAICompatibleEmbeddingProvider:
    """OpenAI-compatible ``POST /embeddings`` provider using one HTTP client."""

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        dimensions: int | None,
        timeout_seconds: float = 30.0,
        batch_size: int = 16,
        api_key_env: str | None = None,
        query_input_type: str | None = None,
        document_input_type: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not endpoint:
            raise ValueError("embedding endpoint is required")
        if not model:
            raise ValueError("embedding model is required")
        if dimensions is not None and dimensions <= 0:
            raise ValueError("embedding dimensions must be positive")
        if not 1 <= batch_size <= 128:
            raise ValueError("embedding batch size must be between 1 and 128")
        self._url = _embeddings_url(endpoint)
        self._model = model
        self._dimensions = dimensions
        self._batch_size = batch_size
        self._api_key_env = api_key_env
        self._query_input_type = query_input_type
        self._document_input_type = document_input_type
        self._resolved_model: str | None = None
        self._owned_client = client is None
        self._client = client or httpx.Client(timeout=timeout_seconds, follow_redirects=True)

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        if self._dimensions is None:
            raise EmbeddingProviderError("embedding dimensions have not been discovered")
        return self._dimensions

    @property
    def resolved_model(self) -> str | None:
        return self._resolved_model

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for offset in range(0, len(texts), self._batch_size):
            vectors.extend(self._embed(list(texts[offset : offset + self._batch_size]), self._document_input_type))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], self._query_input_type)[0]

    def close(self) -> None:
        if self._owned_client:
            self._client.close()

    def _embed(self, texts: list[str], input_type: str | None) -> list[list[float]]:
        headers: dict[str, str] = {}
        if self._api_key_env:
            token = os.environ.get(self._api_key_env)
            if not token:
                raise EmbeddingProviderError(f"embedding API key environment variable {self._api_key_env!r} is not set")
            headers["Authorization"] = f"Bearer {token}"
        payload = {
            "model": self._model,
            "input": texts,
        }
        if input_type is not None:
            payload["input_type"] = input_type
        try:
            response = self._client.post(self._url, json=payload, headers=headers)
            response.raise_for_status()
            raw = response.json()
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 401:
                detail = "embedding endpoint authentication failed (HTTP 401)"
            elif error.response.status_code == 403:
                detail = "embedding endpoint authorization failed (HTTP 403)"
            else:
                detail = f"embedding endpoint returned HTTP {error.response.status_code}"
            raise EmbeddingProviderError(detail) from None
        except httpx.TimeoutException:
            raise EmbeddingProviderError("embedding endpoint request timed out") from None
        except httpx.ConnectError:
            raise EmbeddingProviderError("could not connect to the embedding endpoint") from None
        except httpx.HTTPError as error:
            raise EmbeddingProviderError(f"embedding endpoint request failed ({type(error).__name__})") from None
        except ValueError:
            raise EmbeddingProviderError("embedding endpoint returned invalid JSON") from None
        return self._validate_response(raw, expected_count=len(texts))

    def _validate_response(self, raw: object, *, expected_count: int) -> list[list[float]]:
        if not isinstance(raw, dict) or not isinstance(raw.get("data"), list):
            raise EmbeddingProviderError("embedding response must contain a data array")
        response_model = raw.get("model")
        if response_model is not None and not isinstance(response_model, str):
            raise EmbeddingProviderError("embedding response model must be a string when present")
        self._resolved_model = response_model
        data = raw["data"]
        if len(data) != expected_count:
            raise EmbeddingProviderError(f"embedding response returned {len(data)} vectors for {expected_count} inputs")
        indexed: dict[int, list[float]] = {}
        for item in data:
            if not isinstance(item, dict):
                raise EmbeddingProviderError("embedding response data items must be objects")
            index = item.get("index")
            vector = item.get("embedding")
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < expected_count:
                raise EmbeddingProviderError("embedding response contains an invalid index")
            if index in indexed:
                raise EmbeddingProviderError(f"embedding response contains duplicate index {index}")
            if not isinstance(vector, list) or not vector:
                raise EmbeddingProviderError(f"embedding response vector {index} must be a non-empty array")
            converted: list[float] = []
            for value in vector:
                if isinstance(value, bool) or not isinstance(value, int | float):
                    raise EmbeddingProviderError(f"embedding response vector {index} contains a non-numeric value")
                if not math.isfinite(value):
                    raise EmbeddingProviderError(f"embedding response vector {index} contains a non-finite number")
                converted.append(float(value))
            indexed[index] = converted
        if set(indexed) != set(range(expected_count)):
            raise EmbeddingProviderError("embedding response contains missing indices")
        ordered = [indexed[index] for index in range(expected_count)]
        returned_dimensions = len(ordered[0])
        if any(len(vector) != returned_dimensions for vector in ordered):
            raise EmbeddingProviderError("embedding response contains vectors with inconsistent dimensions")
        if self._dimensions is None:
            self._dimensions = returned_dimensions
        elif returned_dimensions != self._dimensions:
            raise EmbeddingProviderError(
                f"embedding response returned {returned_dimensions} dimensions; expected {self._dimensions}"
            )
        return ordered


__all__ = [
    "DOCUMENT_INPUT_TYPE",
    "QUERY_INPUT_TYPE",
    "SIMILARITY",
    "EmbeddingProvider",
    "EmbeddingProviderError",
    "OpenAICompatibleEmbeddingProvider",
    "canonical_searchable_text",
    "content_hash",
    "is_embedding_eligible",
]
