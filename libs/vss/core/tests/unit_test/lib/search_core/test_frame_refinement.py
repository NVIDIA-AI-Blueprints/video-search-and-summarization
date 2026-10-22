# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Frame refinement compatibility, evidence identity and lifecycle contracts."""

import asyncio

import pytest

from vss_core.search_core._internal.frame_refinement import FrameRefinementContext
from vss_core.search_core._internal.frame_refinement import frame_lookup_body
from vss_core.search_core._internal.frame_refinement import parse_frame_response
from vss_core.search_core._internal.frame_refinement import refine_frames
from vss_core.search_core._internal.frame_refinement import resolve_frame_capabilities
from vss_core.search_core.errors import InvalidInputError


def mapping(uuid="one", dims=2):
    return {
        "frames": {
            "settings": {"index": {"uuid": uuid}},
            "mappings": {
                "properties": {
                    "objects": {
                        "type": "nested",
                        "properties": {"embedding": {"properties": {"vector": {"type": "dense_vector", "dims": dims}}}},
                    }
                }
            },
        }
    }


class Fake:
    def __init__(self):
        self.calls = 0
        self.active = 0
        self.maximum = 0
        self.inspections = 0

    async def frame_capabilities(self, *, index):
        self.inspections += 1
        return mapping()

    async def search(self, **kwargs):
        self.calls += 1
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        try:
            await asyncio.sleep(0.001)
            return {"hits": {"hits": []}}
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_mapping_identity_and_explicit_modes():
    fake = Fake()
    profile = await resolve_frame_capabilities(fake, "frames")
    assert profile["selected_scoring"] == "indexed"
    assert profile["selected_transport"] == "single"
    fake.frame_capabilities = lambda **_kwargs: asyncio.sleep(0, result=mapping(uuid="two"))
    changed = await resolve_frame_capabilities(fake, "frames")
    assert changed["fingerprint"] != profile["fingerprint"]
    with pytest.raises(InvalidInputError):
        await resolve_frame_capabilities(fake, "frames", transport="msearch")


@pytest.mark.asyncio
async def test_search_only_legacy_client_is_unverified():
    class SearchOnly:
        async def search(self, **kwargs):
            return {"hits": {"hits": []}}

    profile = await resolve_frame_capabilities(SearchOnly(), "frames")
    assert profile["selected_scoring"] == "source"
    assert profile["verified"] is False
    with pytest.raises(InvalidInputError):
        await resolve_frame_capabilities(SearchOnly(), "frames", scoring="indexed")


@pytest.mark.asyncio
async def test_shared_budget_dedup_and_profile_singleflight():
    fake = Fake()
    context = FrameRefinementContext()
    keys = [("sensor", str(i)) for i in range(40)]
    arguments = {
        "es": fake,
        "frames_index": "frames",
        "start_time": "a",
        "end_time": "b",
        "query_embedding": [1.0, 0.0],
        "context": context,
    }
    results = await asyncio.gather(refine_frames(keys=keys + keys, **arguments), refine_frames(keys=keys, **arguments))
    assert fake.maximum <= 16
    assert fake.calls == 80
    assert fake.inspections == 1
    assert len(results[0]) == 80
    assert context._available == 16
    assert all(row["status"] == "no_match" for row in context.diagnostics if row["kind"] == "lookup")


@pytest.mark.asyncio
async def test_cancellation_drains_workers_and_restores_budget():
    class Slow(Fake):
        async def search(self, **kwargs):
            await asyncio.sleep(100)

    context = FrameRefinementContext()
    task = asyncio.create_task(
        refine_frames(
            es=Slow(),
            frames_index="frames",
            keys=[("s", str(i)) for i in range(40)],
            start_time="a",
            end_time="b",
            query_embedding=[1.0, 0.0],
            context=context,
        )
    )
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert context._available == 16


@pytest.mark.asyncio
@pytest.mark.parametrize("vector", [[], [0.0, 0.0], [float("nan"), 1.0], [1.0], [float("inf"), 0.0]])
async def test_invalid_vectors_never_execute(vector):
    fake = Fake()
    with pytest.raises(InvalidInputError):
        await refine_frames(
            es=fake,
            frames_index="frames",
            keys=[("s", "o")],
            start_time="a",
            end_time="b",
            query_embedding=vector,
            context=FrameRefinementContext(),
        )
    assert fake.calls == 0


def test_winning_nested_object_controls_bbox():
    response = {
        "hits": {
            "hits": [
                {
                    "_score": 0.25,
                    "_source": {
                        "id": "frame",
                        "timestamp": "time",
                        "objects": [{"id": "person", "bbox": {"leftX": 1}}],
                    },
                    "inner_hits": {
                        "winning_object": {"hits": {"hits": [{"_source": {"id": "person", "bbox": {"leftX": 7}}}]}}
                    },
                }
            ]
        }
    }
    assert parse_frame_response(response, "person", scoring="indexed") == ("frame", {"leftX": 7}, 0.25, "time")


@pytest.mark.parametrize("partial", [{"timed_out": True}, {"_shards": {"failed": 1}}, {"error": "bad"}])
def test_partial_response_rejected(partial):
    with pytest.raises(ValueError):
        parse_frame_response({"hits": {"hits": []}, **partial}, "person", scoring="source")


@pytest.mark.asyncio
async def test_bad_batch_reports_all_failures_without_retry():
    class Multi(Fake):
        async def msearch(self, **kwargs):
            return {"responses": []}

    fake = Multi()
    context = FrameRefinementContext(transport="msearch")
    values = await refine_frames(
        es=fake,
        frames_index="frames",
        keys=[("s", "a"), ("s", "b")],
        start_time="a",
        end_time="b",
        query_embedding=[1.0, 0.0],
        context=context,
    )
    assert values == [None, None]
    assert fake.calls == 0
    assert [row["status"] for row in context.diagnostics if row["kind"] == "lookup"] == ["failed", "failed"]


def test_source_query_rejects_dimension_mismatch_and_returns_winning_identity():
    body = frame_lookup_body("s", "p", "a", "b", [1.0, 0.0], scoring="source")
    script = body["query"]["function_score"]["script_score"]["script"]["source"]
    assert "Math.min" not in script
    assert "dimension mismatch" in script
    assert "winning_object" in body["script_fields"]


@pytest.mark.asyncio
async def test_many_candidates_batched_without_truncation_or_duplicate_requests():
    class Multi(Fake):
        def __init__(self):
            super().__init__()
            self.batch_sizes = []

        async def msearch(self, **kwargs):
            searches = kwargs["searches"]
            self.batch_sizes.append(len(searches) // 2)
            return {"responses": [{"hits": {"hits": []}} for _ in searches[::2]]}

    fake = Multi()
    context = FrameRefinementContext()
    keys = [("sensor", str(i)) for i in range(1620)]
    values = await refine_frames(
        es=fake,
        frames_index="frames",
        keys=keys + keys[:2],
        start_time="a",
        end_time="b",
        query_embedding=[1.0, 0.0],
        context=context,
    )
    assert len(values) == 1622
    assert sum(fake.batch_sizes) == 1620
    assert max(fake.batch_sizes) == 16
    assert context._available == 16


@pytest.mark.asyncio
async def test_mapping_auth_failure_is_not_compatibility_fallback():
    fake = Fake()

    async def denied(**_kwargs):
        raise PermissionError("forbidden")

    fake.frame_capabilities = denied
    with pytest.raises(PermissionError):
        await resolve_frame_capabilities(fake, "frames")
    assert fake.calls == 0


@pytest.mark.asyncio
async def test_skipped_bounds_do_not_inspect_or_execute():
    fake = Fake()
    context = FrameRefinementContext()
    values = await refine_frames(
        es=fake,
        frames_index="frames",
        keys=[("sensor", "person")],
        start_time=None,
        end_time=None,
        query_embedding=[1.0, 0.0],
        context=context,
    )
    assert values == [None]
    assert fake.calls == fake.inspections == 0
    assert context.diagnostics[0]["status"] == "skipped"


@pytest.mark.asyncio
@pytest.mark.parametrize("count, expected", [(1, "single"), (16, "msearch")])
async def test_auto_transport_adapts_to_measured_workload(count, expected):
    class Multi(Fake):
        async def msearch(self, **kwargs):
            return {"responses": [{"hits": {"hits": []}} for _ in kwargs["searches"][::2]]}

    context = FrameRefinementContext()
    await refine_frames(
        es=Multi(),
        frames_index="frames-*",
        keys=[("s", str(i)) for i in range(count)],
        start_time="a",
        end_time="b",
        query_embedding=[1.0, 0.0],
        context=context,
    )
    assert {row["transport"] for row in context.diagnostics if row["kind"] == "lookup"} == {expected}


@pytest.mark.asyncio
async def test_execution_pins_concrete_indices_and_profile_is_compact():
    class Pinned(Fake):
        async def search(self, **kwargs):
            assert kwargs["index"] == ["frames"]
            return {"hits": {"hits": []}}

    context = FrameRefinementContext()
    await refine_frames(
        es=Pinned(),
        frames_index="frames-*",
        keys=[("s", "o")],
        start_time="a",
        end_time="b",
        query_embedding=[1.0, 0.0],
        context=context,
    )
    index = context.diagnostics[0]["indices"][0]
    assert "mapping" not in index
    assert len(index["mapping_hash"]) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_config, settings",
    [
        ({"includes": ["id", "timestamp"]}, {}),
        ({"excludes": ["objects.emb*"]}, {}),
        ({}, {"mapping": {"exclude_source_vectors": "true"}}),
    ],
)
async def test_filtered_or_excluded_source_vectors_rejected(source_config, settings):
    raw = mapping()
    raw["frames"]["mappings"]["_source"] = source_config
    raw["frames"]["settings"]["index"].update(settings)
    fake = Fake()
    fake.frame_capabilities = lambda **_kwargs: asyncio.sleep(0, result=raw)
    with pytest.raises(InvalidInputError):
        await resolve_frame_capabilities(fake, "frames", scoring="source")


@pytest.mark.asyncio
async def test_empty_wildcard_is_disabled_but_fingerprinted():
    fake = Fake()
    fake.frame_capabilities = lambda **_kwargs: asyncio.sleep(0, result={})
    profile = await resolve_frame_capabilities(fake, "frames-*")
    assert profile["enabled"] is False
    assert profile["indices"] == []
    assert profile["fingerprint"]
    context = FrameRefinementContext()
    assert await refine_frames(
        es=fake,
        frames_index="frames-*",
        keys=[("s", "o")],
        start_time="a",
        end_time="b",
        query_embedding=[1.0, 0.0],
        context=context,
    ) == [None]
    assert fake.calls == 0
    assert context.diagnostics[-1]["status"] == "skipped"
    with pytest.raises(InvalidInputError):
        await resolve_frame_capabilities(fake, "frames-*", scoring="indexed")


@pytest.mark.asyncio
async def test_unknown_mapping_metadata_is_not_verified_compatibility():
    fake = Fake()
    fake.frame_capabilities = lambda **_kwargs: asyncio.sleep(0, result=None)
    with pytest.raises(InvalidInputError):
        await resolve_frame_capabilities(fake, "frames")


@pytest.mark.asyncio
@pytest.mark.parametrize("status, supported", [(405, False), (501, False), (403, None), (504, None)])
async def test_transport_probe_only_falls_back_for_unsupported_operation(status, supported):
    from types import SimpleNamespace

    from elasticsearch import ApiError

    from vss_core.search_core.clients.elastic import ElasticClient
    from vss_core.search_core.errors import BackendUnreachableError

    class Client:
        def options(self, **kwargs):
            assert kwargs == {"max_retries": 0}
            return self

        async def msearch(self, **kwargs):
            assert kwargs["searches"] == [{}, {"size": 0, "query": {"match_none": {}}}]
            raise ApiError("test", SimpleNamespace(status=status), {})

    wrapper = ElasticClient(endpoint="http://unused", client=Client())
    if supported is None:
        with pytest.raises(BackendUnreachableError):
            await wrapper.frame_transport_capability(index=["frames"])
    else:
        assert await wrapper.frame_transport_capability(index=["frames"]) is supported


@pytest.mark.asyncio
async def test_transport_probe_rejects_partial_results():
    from vss_core.search_core.clients.elastic import ElasticClient
    from vss_core.search_core.errors import BackendUnreachableError

    class Client:
        def options(self, **_kwargs):
            return self

        async def msearch(self, **_kwargs):
            return {"responses": [{"timed_out": True}]}

    wrapper = ElasticClient(endpoint="http://unused", client=Client())
    with pytest.raises(BackendUnreachableError):
        await wrapper.frame_transport_capability(index=["frames"])
