# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Canonical text and OpenAI-compatible embedding provider tests."""

from __future__ import annotations

import json
import math

import httpx
import pytest

from vss_core.memory.embeddings import EmbeddingProviderError
from vss_core.memory.embeddings import OpenAICompatibleEmbeddingProvider
from vss_core.memory.embeddings import canonical_searchable_text
from vss_core.memory.embeddings import content_hash
from vss_core.memory.embeddings import is_embedding_eligible
from vss_core.memory.models import UnifiedMemoryRecord

MODEL = "nvidia/llama-nemotron-embed-300m-v2"


def _record(**overrides: object) -> UnifiedMemoryRecord:
    raw: dict[str, object] = {
        "job": {
            "job_id": "summary-secret-id",
            "group": "summary",
            "status": "completed",
            "created_at": "2026-08-31T12:00:00Z",
            "backend_ref": "private-backend",
        },
        "input": {
            "intent": "  inspect\r\n loading   bay ",
            "query": " What happened? ",
            "sensors": [{"id": "camera-1"}],
            "window": {
                "start": {"timestamp": "2026-08-31T11:00:00Z"},
                "end": {"timestamp": "2026-08-31T12:00:00Z"},
            },
            "params": {"secret": "excluded"},
        },
        "output": {
            "answer": " A forklift   arrived. ",
            "handles": {"media_urls": ["https://excluded.example/video"]},
            "embedding": [{"es_ref": "vectors/id", "doc_ids": ["id"]}],
            "ext": {
                "description": " loading\r\n activity ",
                "category": "logistics",
                "arbitrary": "excluded",
                "nested": {"description": "excluded"},
            },
        },
    }
    raw.update(overrides)
    return UnifiedMemoryRecord.model_validate(raw)


def test_canonical_parent_text_is_stable_bounded_and_ordered() -> None:
    record = _record()
    assert canonical_searchable_text(record) == "\n".join(
        [
            "Group: summary",
            "Record type: parent_job",
            "Intent: inspect loading bay",
            "Query: What happened?",
            "Answer: A forklift arrived.",
            "Sensors: camera-1",
            "Start: 2026-08-31T11:00:00Z",
            "End: 2026-08-31T12:00:00Z",
            "Context: description=loading activity, category=logistics",
        ]
    )
    text = canonical_searchable_text(record)
    for excluded in ("summary-secret-id", "private-backend", "secret", "https://", "arbitrary", "vectors/id"):
        assert excluded not in text


@pytest.mark.parametrize(
    ("record_type", "group"),
    [("event", "summary"), ("search_hit", "search"), ("incident", "alert")],
)
def test_canonical_child_types(record_type: str, group: str) -> None:
    record = _record(
        job={
            "job_id": "job",
            "record_id": "child",
            "record_type": record_type,
            "group": group,
            "status": "partial",
            "created_at": "2026-08-31T12:00:00Z",
        }
    )
    assert canonical_searchable_text(record).splitlines()[:2] == [
        f"Group: {group}",
        f"Record type: {record_type}",
    ]
    assert is_embedding_eligible(record)


def test_vlm_parent_with_missing_optional_blocks() -> None:
    record = _record(
        job={"job_id": "vlm", "group": "vlm", "status": "completed", "created_at": "2026-08-31T12:00:00Z"},
        input=None,
        output={"answer": "Door is closed."},
    )
    assert canonical_searchable_text(record) == "Group: vlm\nRecord type: parent_job\nAnswer: Door is closed."


def test_hash_ignores_references_and_changes_with_searchable_content() -> None:
    record = _record()
    without_reference = record.model_copy(
        update={"output": record.output.model_copy(update={"embedding": None}) if record.output else None}
    )
    assert content_hash(record) == content_hash(without_reference)
    changed = record.model_copy(
        update={"output": record.output.model_copy(update={"answer": "A truck arrived."}) if record.output else None}
    )
    assert content_hash(record) != content_hash(changed)
    assert content_hash(record) == content_hash(canonical_searchable_text(record))
    assert len(content_hash(record)) == 64


@pytest.mark.parametrize("status", ["submitted", "running", "failed", "timeout"])
def test_nonterminal_or_unsuccessful_records_are_ineligible(status: str) -> None:
    record = _record(job={"job_id": "job", "group": "summary", "status": status, "created_at": "2026-08-31T12:00:00Z"})
    assert not is_embedding_eligible(record)


def _provider(handler: object, *, batch_size: int = 16, api_key_env: str | None = None):
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    client = httpx.Client(transport=transport)
    provider = OpenAICompatibleEmbeddingProvider(
        endpoint="http://embedding.example/v1",
        model=MODEL,
        dimensions=3,
        batch_size=batch_size,
        api_key_env=api_key_env,
        client=client,
    )
    return provider, client


def test_provider_uses_input_types_normalized_url_batching_and_response_order() -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append((str(request.url), payload))
        data = [
            {"index": index, "embedding": [float(index), 1.0, 2.0]} for index in reversed(range(len(payload["input"])))
        ]
        return httpx.Response(200, json={"model": MODEL, "data": data})

    provider, client = _provider(handler, batch_size=2)
    try:
        assert provider.embed_passages(["a", "b", "c"]) == [[0.0, 1.0, 2.0], [1.0, 1.0, 2.0], [0.0, 1.0, 2.0]]
        assert provider.embed_query("question") == [0.0, 1.0, 2.0]
    finally:
        client.close()
    assert [request[0] for request in requests] == ["http://embedding.example/v1/embeddings"] * 3
    assert [request[1]["input_type"] for request in requests] == ["passage", "passage", "query"]
    assert [request[1]["dimensions"] for request in requests] == [3, 3, 3]


@pytest.mark.parametrize(
    "data",
    [
        [{"index": 0, "embedding": [0.0, 1.0, 2.0]}],
        [
            {"index": 0, "embedding": [0.0, 1.0, 2.0]},
            {"index": 0, "embedding": [0.0, 1.0, 2.0]},
        ],
        [
            {"index": 0, "embedding": [0.0, 1.0]},
            {"index": 1, "embedding": [0.0, 1.0, 2.0]},
        ],
        [
            {"index": 0, "embedding": [math.nan, 1.0, 2.0]},
            {"index": 1, "embedding": [0.0, 1.0, math.inf]},
        ],
    ],
)
def test_provider_rejects_missing_duplicate_wrong_size_and_nonfinite(data: list[dict[str, object]]) -> None:
    provider, client = _provider(lambda _request: httpx.Response(200, json={"model": MODEL, "data": data}))
    try:
        with pytest.raises(EmbeddingProviderError):
            provider.embed_passages(["a", "b"])
    finally:
        client.close()


def test_provider_uses_named_bearer_token_without_leaking_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "super-secret-token"
    monkeypatch.setenv("VSS_EMBED_KEY", secret)
    seen_authorization: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_authorization.append(request.headers.get("Authorization"))
        raise httpx.ReadTimeout(f"request carrying {secret}", request=request)

    provider, client = _provider(handler, api_key_env="VSS_EMBED_KEY")
    try:
        with pytest.raises(EmbeddingProviderError) as error:
            provider.embed_query("question")
    finally:
        client.close()
    assert seen_authorization == [f"Bearer {secret}"]
    assert secret not in str(error.value)
