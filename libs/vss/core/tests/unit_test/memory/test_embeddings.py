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
from vss_core.memory.embeddings import embedding_endpoint_identity
from vss_core.memory.embeddings import is_embedding_eligible
from vss_core.memory.models import UnifiedMemoryRecord

MODEL = "openclaw/default"


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


def _provider(
    handler: object,
    *,
    endpoint: str = "http://127.0.0.1:18789/v1",
    dimensions: int | None = 3,
    batch_size: int = 16,
    api_key_env: str | None = None,
    query_input_type: str | None = None,
    document_input_type: str | None = None,
):
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    client = httpx.Client(transport=transport)
    provider = OpenAICompatibleEmbeddingProvider(
        endpoint=endpoint,
        model=MODEL,
        dimensions=dimensions,
        batch_size=batch_size,
        api_key_env=api_key_env,
        query_input_type=query_input_type,
        document_input_type=document_input_type,
        client=client,
    )
    return provider, client


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://127.0.0.1:18789/v1",
        "http://127.0.0.1:18789/v1/",
        "http://127.0.0.1:18789/v1/embeddings",
    ),
)
def test_openclaw_url_normalization_and_minimal_batched_requests(endpoint: str) -> None:
    requests: list[tuple[str, dict[str, object], str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append((str(request.url), payload, request.headers.get("Authorization")))
        data = [
            {"index": index, "embedding": [float(index), 1.0, 2.0]} for index in reversed(range(len(payload["input"])))
        ]
        return httpx.Response(200, json={"model": "resolved/backend-model", "data": data})

    provider, client = _provider(handler, endpoint=endpoint, batch_size=2, api_key_env="OPENCLAW_GATEWAY_TOKEN")
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("OPENCLAW_GATEWAY_TOKEN", "gateway-token")
            passages = provider.embed_passages(["a", "b", "c"])
            query = provider.embed_query("question")
        assert passages == [[0.0, 1.0, 2.0], [1.0, 1.0, 2.0], [0.0, 1.0, 2.0]]
        assert query == [0.0, 1.0, 2.0]
        assert provider.resolved_model == "resolved/backend-model"
    finally:
        client.close()
    assert [request[0] for request in requests] == ["http://127.0.0.1:18789/v1/embeddings"] * 3
    assert [request[1] for request in requests] == [
        {"model": MODEL, "input": ["a", "b"]},
        {"model": MODEL, "input": ["c"]},
        {"model": MODEL, "input": ["question"]},
    ]
    assert [request[2] for request in requests] == ["Bearer gateway-token"] * 3


def test_custom_input_types_are_sent_only_when_explicitly_configured() -> None:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        return httpx.Response(
            200,
            json={"data": [{"index": index, "embedding": [0.0, 1.0, 2.0]} for index, _ in enumerate(payload["input"])]},
        )

    provider, client = _provider(handler, query_input_type="query", document_input_type="passage")
    try:
        provider.embed_passages(["document"])
        provider.embed_query("question")
    finally:
        client.close()
    assert payloads == [
        {"model": MODEL, "input": ["document"], "input_type": "passage"},
        {"model": MODEL, "input": ["question"], "input_type": "query"},
    ]
    assert all("dimensions" not in payload and "encoding_format" not in payload for payload in payloads)


@pytest.mark.parametrize("response_model", (None, "resolved/backend-model"))
def test_response_model_is_optional_and_may_differ_from_target(response_model: str | None) -> None:
    response = {"data": [{"index": 0, "embedding": [0.0, 1.0, 2.0]}]}
    if response_model is not None:
        response["model"] = response_model
    provider, client = _provider(lambda _request: httpx.Response(200, json=response))
    try:
        assert provider.embed_query("question") == [0.0, 1.0, 2.0]
        assert provider.resolved_model == response_model
    finally:
        client.close()


def test_dimension_discovery_is_sticky_and_later_responses_are_validated() -> None:
    response_dimensions = iter((4, 4, 3))

    def handler(_request: httpx.Request) -> httpx.Response:
        dimensions = next(response_dimensions)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.0] * dimensions}]})

    provider, client = _provider(handler, dimensions=None)
    try:
        assert provider.embed_query("discover") == [0.0] * 4
        assert provider.dimensions == 4
        assert provider.embed_query("same") == [0.0] * 4
        with pytest.raises(EmbeddingProviderError, match="returned 3 dimensions; expected 4"):
            provider.embed_query("changed")
    finally:
        client.close()


def test_explicit_expected_dimension_is_validated() -> None:
    provider, client = _provider(
        lambda _request: httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.0, 1.0]}]}),
        dimensions=3,
    )
    try:
        with pytest.raises(EmbeddingProviderError, match="returned 2 dimensions; expected 3"):
            provider.embed_query("question")
    finally:
        client.close()


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([{"embedding": [0.0, 1.0, 2.0]}], "invalid index"),
        [
            [
                {"index": 0, "embedding": [0.0, 1.0, 2.0]},
                {"index": 0, "embedding": [0.0, 1.0, 2.0]},
            ],
            "duplicate index",
        ],
        [
            [
                {"index": 0, "embedding": [0.0, 1.0]},
                {"index": 1, "embedding": [0.0, 1.0, 2.0]},
            ],
            "inconsistent dimensions",
        ],
        [
            [
                {"index": 0, "embedding": [math.nan, 1.0, 2.0]},
                {"index": 1, "embedding": [0.0, 1.0, math.inf]},
            ],
            "non-finite",
        ],
        ([{"index": 0, "embedding": [True, 1.0, 2.0]}], "non-numeric"),
        ([{"index": 0, "embedding": ["0", 1.0, 2.0]}], "non-numeric"),
        ([{"index": 0, "embedding": []}], "non-empty array"),
    ],
)
def test_provider_rejects_malformed_vectors(data: list[dict[str, object]], message: str) -> None:
    expected_inputs = 2 if len(data) == 2 else 1
    provider, client = _provider(
        lambda _request: httpx.Response(
            200,
            content=json.dumps({"data": data}, allow_nan=True),
            headers={"Content-Type": "application/json"},
        ),
        dimensions=None if "inconsistent" in message else 3,
    )
    try:
        with pytest.raises(EmbeddingProviderError, match=message):
            provider.embed_passages(["text"] * expected_inputs)
    finally:
        client.close()


def test_provider_rejects_wrong_vector_count() -> None:
    provider, client = _provider(
        lambda _request: httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [0.0, 1.0, 2.0]}]},
        )
    )
    try:
        with pytest.raises(EmbeddingProviderError, match="returned 1 vectors for 2 inputs"):
            provider.embed_passages(["one", "two"])
    finally:
        client.close()


@pytest.mark.parametrize("status", (401, 403))
def test_authentication_failures_are_distinct(status: int) -> None:
    provider, client = _provider(lambda _request: httpx.Response(status))
    try:
        with pytest.raises(EmbeddingProviderError, match=f"HTTP {status}"):
            provider.embed_query("question")
    finally:
        client.close()


@pytest.mark.parametrize(
    ("failure", "message"),
    (
        (httpx.ReadTimeout("slow"), "timed out"),
        (httpx.ConnectError("refused"), "connect"),
    ),
)
def test_transport_failures_are_clear(failure: httpx.HTTPError, message: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        failure.request = request
        raise failure

    provider, client = _provider(handler)
    try:
        with pytest.raises(EmbeddingProviderError, match=message):
            provider.embed_query("question")
    finally:
        client.close()


def test_missing_named_credential_fails_before_request(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    monkeypatch.delenv("OPENCLAW_GATEWAY_TOKEN", raising=False)
    provider, client = _provider(handler, api_key_env="OPENCLAW_GATEWAY_TOKEN")
    try:
        with pytest.raises(EmbeddingProviderError, match=r"OPENCLAW_GATEWAY_TOKEN.*not set"):
            provider.embed_query("question")
    finally:
        client.close()
    assert not called


def test_provider_uses_named_bearer_token_without_leaking_it(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_endpoint_identity_normalizes_paths_and_drops_sensitive_query_values() -> None:
    expected = embedding_endpoint_identity("https://EMBEDDING.example:443/v1")
    assert embedding_endpoint_identity("https://embedding.example/v1/") == expected
    assert embedding_endpoint_identity("https://embedding.example/v1/embeddings") == expected
    assert embedding_endpoint_identity("https://embedding.example/v1?api_key=secret") == expected
    assert "secret" not in expected
