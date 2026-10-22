# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Versioned source-snapshot checks against an explicitly isolated ES 9.4.4."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest

from vss_core.search_core._internal.source_selection import source_selection_context
from vss_core.search_core.clients.elastic import ElasticClient
from vss_core.search_core.errors import BackendUnreachableError

pytestmark = pytest.mark.integration


@pytest.fixture
def snapshot_backend():
    origin = os.environ.get("VSS_FRAME_TEST_URL")
    if not origin:
        pytest.skip("VSS_FRAME_TEST_URL must name the disposable local ES fixture")
    parsed = urlsplit(origin)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or not parsed.port:
        pytest.fail("Snapshot fixture accepts an explicit loopback origin only")
    name = "vss-snapshot-test-" + uuid4().hex
    mapping = {
        "mappings": {
            "properties": {
                "embeddings": {"type": "nested", "properties": {"vector": {"type": "dense_vector", "dims": 2}}},
                "sensor": {"properties": {"id": {"type": "keyword"}}},
            }
        }
    }
    with httpx.Client(base_url=origin, trust_env=False, timeout=60) as setup:
        response = setup.get("/")
        response.raise_for_status()
        assert response.json()["version"]["number"] == "9.4.4"
        setup.put("/" + name, json=mapping).raise_for_status()
        try:
            yield origin, setup, name, mapping
        finally:
            setup.delete("/" + name).raise_for_status()


async def real_scan(es, name, minimum=0):
    context = SimpleNamespace(scans=[])
    token = source_selection_context.set(context)
    try:
        result = await es.exhaustive_vector_search(
            index=name,
            filters=[{"term": {"sensor.id": "camera"}}],
            vector=[1.0, 0.0],
            field="embeddings.vector",
            nested_path="embeddings",
            candidate_limit=1000,
            min_score=minimum,
        )
        return result["candidate_coverage"]
    finally:
        source_selection_context.reset(token)


@pytest.mark.asyncio
async def test_real_versioned_scroll_digest_threshold_and_same_count_update(snapshot_backend):
    origin, setup, name, _ = snapshot_backend
    lines = []
    for i in range(600):
        lines.extend(
            [
                json.dumps({"index": {"_index": name, "_id": str(i)}}),
                json.dumps(
                    {"sensor": {"id": "camera"}, "embeddings": [{"vector": [1.0, 0.0] if i % 2 else [-1.0, 0.0]}]}
                ),
            ]
        )
    # The scoped but vector-less document must not be misreported as scored.
    lines.extend([json.dumps({"index": {"_index": name, "_id": "missing"}}), json.dumps({"sensor": {"id": "camera"}})])
    response = setup.post(
        "/_bulk?refresh=true", content="\n".join(lines) + "\n", headers={"Content-Type": "application/x-ndjson"}
    )
    response.raise_for_status()
    assert not response.json()["errors"]
    es = ElasticClient.from_endpoint(origin)
    try:
        full = await real_scan(es, name)
        threshold = await real_scan(es, name, minimum=0.5)
        assert full["snapshot_verified"]
        assert full["eligible_documents"] == full["retained_documents"] == 600
        assert threshold["retained_documents"] == threshold["score_filtered_documents"] == 300
        assert full["snapshot_digest"] == threshold["snapshot_digest"]
        assert full["scoped_documents"] is full["missing_vector_documents"] is None
        setup.put(
            "/" + name + "/_doc/1?refresh=true",
            json={"sensor": {"id": "camera"}, "embeddings": [{"vector": [0.0, 1.0]}]},
        ).raise_for_status()
        changed = await real_scan(es, name)
        assert changed["eligible_documents"] == full["eligible_documents"]
        assert changed["snapshot_digest"] != full["snapshot_digest"]
        assert changed["snapshot_scope_digest"] == full["snapshot_scope_digest"]
    finally:
        await es.aclose()


@pytest.mark.asyncio
async def test_real_same_name_index_recreation_during_scan_rejected(snapshot_backend):
    origin, setup, name, mapping = snapshot_backend
    setup.put(
        "/" + name + "/_doc/one?refresh=true", json={"sensor": {"id": "camera"}, "embeddings": [{"vector": [1.0, 0.0]}]}
    ).raise_for_status()
    es = ElasticClient.from_endpoint(origin)
    original = es._snapshot_index_identities
    calls = 0

    async def recreate_before_validation(client, index):
        nonlocal calls
        calls += 1
        if calls == 2:
            setup.delete("/" + name).raise_for_status()
            setup.put("/" + name, json=mapping).raise_for_status()
        return await original(client, index)

    es._snapshot_index_identities = recreate_before_validation
    try:
        with pytest.raises(BackendUnreachableError, match="identities changed"):
            await real_scan(es, name)
    finally:
        await es.aclose()
