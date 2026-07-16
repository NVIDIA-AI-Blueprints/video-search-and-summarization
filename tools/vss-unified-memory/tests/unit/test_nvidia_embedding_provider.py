# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from io import BytesIO
from typing import Any

import pytest

from vss_unified_memory.adapters.embeddings import nvidia
from vss_unified_memory.adapters.embeddings.nvidia import NvidiaEmbeddingProvider
from vss_unified_memory.application.errors import EmbeddingError


def test_embedding_provider_uses_rt_embed_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict[str, Any]] = []

    def fake_urlopen(request: Any, timeout: float) -> BytesIO:
        requests.append(json.loads(request.data))
        return BytesIO(json.dumps({"data": [{"embeddings": [0.1, 0.2]}]}).encode())

    monkeypatch.setattr(nvidia, "urlopen", fake_urlopen)
    provider = NvidiaEmbeddingProvider("http://rt-embed:8000", "cosmos-embed1-448p", dimensions=2)
    assert provider.embed(("forklift near miss",)) == ((0.1, 0.2),)
    assert requests == [{"text_input": "forklift near miss", "model": "cosmos-embed1-448p"}]


def test_embedding_provider_rejects_wrong_dimensions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        nvidia,
        "urlopen",
        lambda request, timeout: BytesIO(json.dumps({"data": [{"embeddings": [0.1]}]}).encode()),
    )
    provider = NvidiaEmbeddingProvider("http://rt-embed:8000", "cosmos-embed1-448p", dimensions=2)
    with pytest.raises(EmbeddingError, match="dimension mismatch"):
        provider.embed(("forklift",))


def test_embedding_provider_rejects_oversized_text_before_request(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def fake_urlopen(request: Any, timeout: float) -> BytesIO:
        nonlocal called
        called = True
        return BytesIO()

    monkeypatch.setattr(nvidia, "urlopen", fake_urlopen)
    provider = NvidiaEmbeddingProvider("http://rt-embed:8000", "cosmos-embed1-448p", dimensions=2)
    with pytest.raises(EmbeddingError, match="maximum is 1000"):
        provider.embed(("a" * 1001,))
    assert called is False


def test_embedding_provider_preserves_service_vector_without_normalizing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        nvidia,
        "urlopen",
        lambda request, timeout: BytesIO(json.dumps({"data": [{"embeddings": [3.0, 4.0]}]}).encode()),
    )
    provider = NvidiaEmbeddingProvider("http://rt-embed:8000", "cosmos-embed1-448p", dimensions=2)
    assert provider.embed(("forklift",)) == ((3.0, 4.0),)
