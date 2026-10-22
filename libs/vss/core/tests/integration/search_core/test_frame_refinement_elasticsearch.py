# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Opt-in checks against an isolated Elasticsearch 9.4.4 fixture, never VSS data.

Set VSS_FRAME_TEST_URL to the disposable container's loopback origin. Set
VSS_FRAME_TEST_PERF=1 to include measured 1/16/256/1617-candidate workloads.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import statistics
import time
import tracemalloc
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest

from vss_core.search_core._internal.frame_refinement import FrameRefinementContext
from vss_core.search_core._internal.frame_refinement import refine_frames
from vss_core.search_core._internal.frame_refinement import resolve_frame_capabilities
from vss_core.search_core.clients.elastic import ElasticClient
from vss_core.search_core.errors import InvalidInputError

pytestmark = pytest.mark.integration
START = "2025-01-01T00:00:00Z"
END = "2025-01-01T01:00:00Z"
BOX_A = {"leftX": 1, "rightX": 2, "topY": 3, "bottomY": 4}
BOX_B = {"leftX": 11, "rightX": 12, "topY": 13, "bottomY": 14}


def mapping(indexed, dimensions=3):
    text = {"type": "text", "fields": {"keyword": {"type": "keyword"}}}
    embedding = (
        {"properties": {"vector": {"type": "dense_vector", "dims": dimensions, "index": True}}}
        if indexed
        else {"enabled": False}
    )
    return {
        "settings": {"index.mapping.exclude_source_vectors": False},
        "mappings": {
            "properties": {
                "id": text,
                "sensorId": text,
                "timestamp": {"type": "date"},
                "objects": {
                    "type": "nested",
                    "properties": {
                        "id": text,
                        "bbox": {"enabled": False},
                        "embedding": embedding,
                    },
                },
            }
        },
    }


@pytest.fixture(scope="module")
def backend():
    origin = os.environ.get("VSS_FRAME_TEST_URL")
    if not origin:
        pytest.skip("VSS_FRAME_TEST_URL must name the disposable local ES fixture")
    parsed = urlsplit(origin)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or not parsed.port:
        pytest.fail("The integration fixture accepts an explicit loopback port only")
    with httpx.Client(base_url=origin, trust_env=False, timeout=60) as setup:
        info = setup.get("/")
        info.raise_for_status()
        assert info.json()["version"]["number"] == "9.4.4"
        owned = []

        def create(indexed, documents, dimensions=3):
            name = "vss-frame-test-" + uuid4().hex
            response = setup.put("/" + name, json=mapping(indexed, dimensions))
            response.raise_for_status()
            owned.append(name)
            lines = []
            for document in documents:
                lines.extend([json.dumps({"index": {"_index": name, "_id": document["id"]}}), json.dumps(document)])
            if lines:
                response = setup.post(
                    "/_bulk?refresh=true",
                    content="\n".join(lines) + "\n",
                    headers={"Content-Type": "application/x-ndjson"},
                )
                response.raise_for_status()
                assert not response.json()["errors"], response.text
            return name

        yield origin, create, setup
        for name in owned:
            assert name.startswith("vss-frame-test-")
            response = setup.delete("/" + name)
            assert response.status_code in {200, 404}, response.text


def obj(vector=None, box=BOX_A, object_id="a"):
    result = {"id": object_id, "bbox": box}
    if vector is not None:
        result["embedding"] = {"vector": vector}
    return result


@pytest.fixture(scope="module")
def indices(backend):
    _, create, _ = backend
    entries = [
        [obj([1, 0, 0])],
        [obj([-1, 0, 0])],
        [obj([0, 1, 0])],
        [obj([0, 1, 0]), obj([1, 0, 0], BOX_B, "b")],
        [obj([0, 1, 0]), obj([1, 0, 0], BOX_B)],
        [obj()],
    ]
    documents = [
        {"id": str(i), "sensorId": "cam", "timestamp": f"2025-01-01T00:00:0{i}Z", "objects": objects}
        for i, objects in enumerate(entries, 1)
    ]
    documents.append({"id": "outside", "sensorId": "another", "timestamp": START, "objects": [obj([1, 0, 0])]})
    return create(True, documents), create(False, documents)


@pytest.mark.asyncio
@pytest.mark.parametrize("scoring,index_position", [("indexed", 0), ("source", 1)])
@pytest.mark.parametrize(
    "frame,score,box",
    [(1, 1.0, BOX_A), (2, 0.0, BOX_A), (3, 0.5, BOX_A), (4, 0.5, BOX_A), (5, 1.0, BOX_B), (6, None, None)],
)
async def test_real_cosine_and_winning_subject(backend, indices, scoring, index_position, frame, score, box):
    context = FrameRefinementContext(scoring=scoring, transport="single")
    client = ElasticClient.from_endpoint(backend[0])
    try:
        timestamp = f"2025-01-01T00:00:0{frame}Z"
        result = await refine_frames(
            es=client,
            frames_index=indices[index_position],
            keys=[("cam", "a")],
            start_time=timestamp,
            end_time=timestamp,
            query_embedding=[1, 0, 0],
            context=context,
        )
        if score is None:
            assert result == [None]
            assert context.diagnostics[-1]["status"] == "no_match"
        else:
            assert result[0] is not None, context.diagnostics
            assert result[0][0] == str(frame)
            assert result[0][1] == box
            assert result[0][2] == pytest.approx(score, abs=1e-5)
            assert context.diagnostics[-1]["status"] == "succeeded"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_real_mixed_mapping_and_dimension_validation(backend, indices):
    client = ElasticClient.from_endpoint(backend[0])
    try:
        profile = await resolve_frame_capabilities(client, list(indices), scoring="auto", transport="single")
        assert profile["selected_scoring"] == "source"
        with pytest.raises(InvalidInputError):
            await resolve_frame_capabilities(client, list(indices), scoring="indexed", transport="single")
        for vector in ([1, 0], [0, 0, 0], [float("nan"), 0, 1]):
            with pytest.raises(InvalidInputError):
                await refine_frames(
                    es=client,
                    frames_index=indices[0],
                    keys=[("cam", "a")],
                    start_time=START,
                    end_time=END,
                    query_embedding=vector,
                    context=FrameRefinementContext(scoring="indexed", transport="single"),
                )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_real_multi_search_parity_and_index_identity(backend, indices):
    client = ElasticClient.from_endpoint(backend[0])
    try:
        outputs = []
        for transport in ("single", "msearch"):
            context = FrameRefinementContext(scoring="indexed", transport=transport)
            outputs.append(
                await refine_frames(
                    es=client,
                    frames_index=indices[0],
                    keys=[("cam", "a"), ("cam", "b"), ("cam", "absent")],
                    start_time=START,
                    end_time=END,
                    query_embedding=[1, 0, 0],
                    context=context,
                )
            )
            assert not any(row.get("status") == "failed" for row in context.diagnostics)
        assert outputs[0] == outputs[1]
        first = await resolve_frame_capabilities(client, indices[0], scoring="auto", transport="single")
        assert first["verified"] and first["indices"][0]["uuid"]
        assert (
            first["fingerprint"]
            == (await resolve_frame_capabilities(client, indices[0], scoring="auto", transport="single"))["fingerprint"]
        )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_real_index_recreation_changes_profile(backend):
    origin, create, setup = backend
    index = create(True, [])
    client = ElasticClient.from_endpoint(origin)
    try:
        before = await resolve_frame_capabilities(client, index, transport="single")
        setup.delete("/" + index).raise_for_status()
        setup.put("/" + index, json=mapping(True)).raise_for_status()
        after = await resolve_frame_capabilities(client, index, transport="single")
        assert before["indices"][0]["uuid"] != after["indices"][0]["uuid"]
        assert before["fingerprint"] != after["fingerprint"]
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("count,expected", [(1, "single"), (16, "msearch")])
async def test_real_auto_uses_batching_only_for_batchable_workloads(backend, indices, count, expected):
    client = ElasticClient.from_endpoint(backend[0])
    context = FrameRefinementContext()
    try:
        # Unique absent IDs are valid no-matches, not failed refinement.
        result = await refine_frames(
            es=client,
            frames_index=indices[0],
            keys=[("cam", "absent-" + str(i)) for i in range(count)],
            start_time=START,
            end_time=END,
            query_embedding=[1, 0, 0],
            context=context,
        )
        assert result == [None] * count
        lookups = [row for row in context.diagnostics if row.get("kind") == "lookup"]
        assert len(lookups) == count
        assert all(row["status"] == "no_match" and row["transport"] == expected for row in lookups)
    finally:
        await context.aclose()
        await client.aclose()


class CountingClient:
    def __init__(self, client):
        self.client = client
        self.requests = self.sent_bytes = self.received_bytes = 0

    def __getattr__(self, name):
        return getattr(self.client, name)

    async def _request(self, method, kwargs):
        self.requests += 1
        self.sent_bytes += len(json.dumps(kwargs).encode())
        response = await method(**kwargs)
        self.received_bytes += len(json.dumps(dict(response)).encode())
        return response

    async def search(self, **kwargs):
        return await self._request(self.client.search, kwargs)

    async def msearch(self, **kwargs):
        return await self._request(self.client.msearch, kwargs)


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("VSS_FRAME_TEST_PERF") != "1", reason="Opt-in measured backend workload")
async def test_measured_refinement_workloads(backend):
    origin, create, _ = backend
    dimensions = 1152
    vector = [1.0, 0.0] + [0.0] * (dimensions - 2)
    documents = []
    for i in range(1617):
        angle = (i % 101) / 100
        embedding = [math.cos(angle), math.sin(angle)] + [0.0] * (dimensions - 2)
        documents.append(
            {"id": str(i), "sensorId": "cam", "timestamp": START, "objects": [obj(embedding, object_id=str(i))]}
        )
    index = create(True, documents, dimensions)
    raw = ElasticClient.from_endpoint(origin)
    receipts = []
    try:
        for count in (1, 16, 256, 1617):
            reference = None
            for scoring, transport in (("source", "single"), ("indexed", "single"), ("indexed", "msearch")):
                client = CountingClient(raw)
                elapsed = []
                # Warm compatibility metadata and script compilation outside measurements.
                context = FrameRefinementContext(scoring=scoring, transport=transport)
                await context.profile(client, index)
                await refine_frames(
                    es=client,
                    frames_index=index,
                    keys=[("cam", "0")],
                    start_time=START,
                    end_time=END,
                    query_embedding=vector,
                    context=context,
                )
                client.requests = client.sent_bytes = client.received_bytes = 0
                for _ in range(5):
                    context.diagnostics.clear()
                    started = time.perf_counter()
                    result = await refine_frames(
                        es=client,
                        frames_index=index,
                        keys=[("cam", str(i)) for i in range(count)],
                        start_time=START,
                        end_time=END,
                        query_embedding=vector,
                        context=context,
                    )
                    elapsed.append(time.perf_counter() - started)
                    assert len(result) == count and all(item is not None for item in result), context.diagnostics
                    assert not any(row.get("status") == "failed" for row in context.diagnostics)
                counts = (client.requests, client.sent_bytes, client.received_bytes)
                # Sample allocation separately: tracing heavily distorts async
                # HTTP timings and must not become a claimed backend speedup.
                context.diagnostics.clear()
                tracemalloc.start()
                await refine_frames(
                    es=client,
                    frames_index=index,
                    keys=[("cam", str(i)) for i in range(count)],
                    start_time=START,
                    end_time=END,
                    query_embedding=vector,
                    context=context,
                )
                _, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                scores = [item[2] for item in result]
                if reference is None:
                    reference = scores
                else:
                    assert scores == pytest.approx(reference, abs=1e-5)
                receipts.append(
                    {
                        "candidates": count,
                        "scoring": scoring,
                        "transport": transport,
                        "median_s": statistics.median(elapsed),
                        "max_s": max(elapsed),
                        "samples_s": elapsed,
                        "requests_per_iteration": counts[0] / 5,
                        "json_request_bytes_per_iteration": counts[1] / 5,
                        "json_response_bytes_per_iteration": counts[2] / 5,
                        "python_peak_bytes": peak,
                        "successful": count,
                    }
                )
        output = Path(os.environ.get("VSS_FRAME_TEST_RECEIPT", "/tmp/vss-frame-refinement-measurements.json"))
        output.write_text(
            json.dumps(
                {
                    "server_version": "9.4.4",
                    "dimensions": dimensions,
                    "scope": "warm frame-refinement stage; JSON sizes exclude transport headers",
                    "measurements": receipts,
                },
                indent=2,
            )
        )
    finally:
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        await raw.aclose()
