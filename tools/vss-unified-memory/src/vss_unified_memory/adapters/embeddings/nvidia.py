# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Client for the VSS RT-Embed text-embedding endpoint."""

import json
import math
from collections.abc import Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from vss_unified_memory.application.errors import EmbeddingError


class NvidiaEmbeddingProvider:
    def __init__(
        self,
        endpoint: str,
        model: str,
        *,
        dimensions: int = 768,
        max_characters: int = 1000,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._url = f"{endpoint.rstrip('/')}/v1/generate_text_embeddings"
        self._model = model
        self._dimensions = dimensions
        self._max_characters = max_characters
        self._timeout_seconds = timeout_seconds

    @property
    def model(self) -> str:
        return self._model

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._request_vector(text) for text in texts)

    def _request_vector(self, text: str) -> tuple[float, ...]:
        value = text.strip()
        if not value:
            raise EmbeddingError("text to embed cannot be empty", retryable=False)
        if len(value) > self._max_characters:
            raise EmbeddingError(
                f"text to embed has {len(value)} characters; maximum is {self._max_characters}",
                retryable=False,
            )
        request = Request(
            self._url,
            data=json.dumps({"text_input": value, "model": self._model}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310 - configured trusted endpoint
                payload: Any = json.load(response)
        except HTTPError as error:
            retryable = error.code == 429 or error.code >= 500
            raise EmbeddingError(f"embedding service returned HTTP {error.code}", retryable=retryable) from error
        except URLError as error:
            raise EmbeddingError("embedding service is unreachable") from error
        except (json.JSONDecodeError, ValueError, TypeError, KeyError, IndexError) as error:
            raise EmbeddingError("embedding service returned an invalid response", retryable=False) from error

        try:
            raw_vector = payload["data"][0]["embeddings"]
            vector = tuple(float(value) for value in raw_vector)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise EmbeddingError(
                "embedding service response did not contain a numeric vector", retryable=False
            ) from error
        if len(vector) != self._dimensions:
            raise EmbeddingError(
                f"embedding dimension mismatch: expected {self._dimensions}, received {len(vector)}",
                retryable=False,
            )
        if not all(math.isfinite(value) for value in vector):
            raise EmbeddingError("embedding service returned non-finite values", retryable=False)
        if not any(value != 0 for value in vector):
            raise EmbeddingError("embedding service returned a zero vector", retryable=False)
        return vector
