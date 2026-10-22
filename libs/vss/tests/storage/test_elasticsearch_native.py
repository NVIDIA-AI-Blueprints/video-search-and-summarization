# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Opted-in ES server acceptance; only disposable test indices are created.

Set RUN_VSS_ES_TESTS=1, VSS_STORAGE_ES_URL, and optional
VSS_STORAGE_ES_TEST_INDEX_PREFIX/API_KEY/CA_CERTS. Credentials stay in the caller
environment. Every fixture uses a unique index and deletes only that index.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from test_checkpoint_pipeline import pipeline as pipeline
from test_checkpoint_pipeline import submit

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_VSS_ES_TESTS") != "1", reason="native ES acceptance is opt-in"
)


async def _initial_index_probe(client, index, *, timeout_s=30.0):
    """Wait at most timeout_s for startup, preserving nonconnection failures."""
    from elasticsearch import ConnectionError as ESConnectionError
    from elasticsearch import ConnectionTimeout

    try:
        async with asyncio.timeout(timeout_s):
            while True:
                try:
                    return bool(await client.indices.exists(index=index))
                except (ESConnectionError, ConnectionTimeout):
                    await asyncio.sleep(0.5)
    except TimeoutError:
        pytest.fail(
            f"Elasticsearch did not become reachable within {timeout_s:g}s",
            pytrace=False,
        )


@pytest.fixture
async def native_es(tmp_path):
    from elasticsearch import AsyncElasticsearch

    from vss.storage import ElasticsearchConfig, ElasticsearchStorageAdapter

    url = os.environ["VSS_STORAGE_ES_URL"]
    index = (
        os.getenv("VSS_STORAGE_ES_TEST_INDEX_PREFIX", "vss-storage-acceptance")
        + "-"
        + uuid4().hex
    )
    config = ElasticsearchConfig(
        url=url,
        index=index,
        claim_root=tmp_path / "claims",
        ca_certs=os.getenv("VSS_STORAGE_ES_CA_CERTS"),
    )
    kwargs = {"max_retries": 0, "retry_on_timeout": False}
    if os.getenv("VSS_STORAGE_ES_API_KEY"):
        kwargs["api_key"] = os.environ["VSS_STORAGE_ES_API_KEY"]
    if config.ca_certs:
        kwargs["ca_certs"] = str(config.ca_certs)
    async with AsyncElasticsearch(config.url, **kwargs) as client:
        assert not await _initial_index_probe(client, index), (
            "Refuse pre-existing test index"
        )
        try:
            yield config, client, ElasticsearchStorageAdapter
        finally:
            # UUID identity was absent before this fixture; do not use wildcards.
            if bool(await client.indices.exists(index=index)):
                await client.indices.delete(index=index)


async def test_native_realtime_cas_and_restart(native_es):
    from vss.storage import StorageConflictError, StorageNotFoundError

    config, client, adapter = native_es
    async with adapter(config=config, client=client) as first:
        with pytest.raises(StorageNotFoundError):
            await first.read("data", "missing")
        snapshot = await first.write(
            "data",
            "job",
            [{"caption": "café", "timestamp_ns": 2_000_000_000_000_000_001}],
            expected_revision=None,
        )
        assert await first.read("data", "job") == snapshot
        with pytest.raises(StorageConflictError):
            await first.write("data", "job", [], expected_revision=None)
    async with adapter(config=config) as reopened:
        assert await reopened.read("data", "job") == snapshot
        replaced = await reopened.write("data", "job", [], expected_revision=1)
        assert replaced.revision == 2
        with pytest.raises(StorageConflictError):
            await reopened.write("data", "job", [], expected_revision=1)


async def test_native_concurrent_cas_and_same_host_claim(native_es):
    from vss.storage import StorageBusyError, StorageConflictError

    config, client, adapter = native_es
    async with (
        adapter(config=config, client=client) as first,
        adapter(config=config) as second,
    ):
        await first.write("data", "job", [{"v": 0}], expected_revision=None)
        results = await asyncio.gather(
            first.write("data", "job", [{"v": 1}], expected_revision=1),
            second.write("data", "job", [{"v": 2}], expected_revision=1),
            return_exceptions=True,
        )
        assert sum(isinstance(result, StorageConflictError) for result in results) == 1
        async with first.exclusive("jobs", "job"):
            with pytest.raises(StorageBusyError):
                async with second.exclusive("jobs", "job"):
                    pytest.fail("duplicate same-host owner")


async def test_native_concurrent_index_creation_validates_winning_mapping(native_es):
    config, client, adapter = native_es
    async with (
        adapter(config=config, client=client) as first,
        adapter(config=config) as second,
    ):
        results = await asyncio.gather(
            first.write("data", "first", [], expected_revision=None),
            second.write("data", "second", [], expected_revision=None),
        )
        assert [result.revision for result in results] == [1, 1]


async def test_native_es_snapshot_survives_fresh_python_process(native_es):
    import vss.storage as storage_module

    config, client, adapter = native_es
    async with adapter(config=config, client=client) as first:
        await first.write(
            "data",
            "job",
            [{"caption": "fresh-process", "timestamp_ns": 2_000_000_000_000_000_001}],
            expected_revision=None,
        )
    script = """
import asyncio,json,sys
from pathlib import Path
import vss.storage as storage_module
from vss.storage import ElasticsearchConfig,ElasticsearchStorageAdapter
assert Path(storage_module.__file__).resolve() == Path(sys.argv[2]).resolve(), (
 "Fresh process imported a different package than the parent: "
 + storage_module.__file__
)
async def main():
 config=ElasticsearchConfig.model_validate_json(sys.argv[1])
 async with ElasticsearchStorageAdapter(config=config) as store:
  print(json.dumps((await store.read('data','job')).records))
asyncio.run(main())
"""
    result = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            *(["-I"] if sys.flags.isolated else []),
            "-c",
            script,
            config.model_dump_json(),
            storage_module.__file__,
        ],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        {"caption": "fresh-process", "timestamp_ns": 2_000_000_000_000_000_001}
    ]


async def test_native_es_ingestion_checkpoint_resume_and_summary(
    native_es, pipeline, monkeypatch
):
    import vss.ingestion.vlm as service
    from vss.ingestion import VLMBackendError
    from vss.summarization import summarize_from_store
    from vss.summarization.generators import GeneratedSummary, SummaryGenerator

    class Generator(SummaryGenerator):
        async def generate(self, events, *, prompt=None, model=None):
            assert len(events) == 1
            assert events[0]["start_time"] == 0 and events[0]["end_time"] == 2.5
            return GeneratedSummary(text="Native ES deferred summary.", model="test")

    config, client, adapter = native_es
    source, requests, state = pipeline
    state["fail_chunk"] = 2
    async with adapter(config=config, client=client) as first:
        job = submit(source, first)
        with pytest.raises(VLMBackendError):
            await job
        await job.wait_closed()
    async with adapter(config=config) as restarted:
        resumed = submit(source, restarted, job_id=job.id, resume=True)
        chunks = (await resumed)["vlm"]
        await resumed.wait_closed()
        assert [request["index"] for request in requests] == [0, 1, 2, 2]
        assert (await restarted.read("observations", str(job.id))).records[0][
            "original_source_id"
        ] == source.source_id

        def forbidden(*args, **kwargs):
            raise AssertionError("Completed replay must not decode")

        monkeypatch.setattr(service, "probe_video", forbidden)
        monkeypatch.setattr(service, "subsample_frames", forbidden)
        replay = submit(source, restarted, job_id=job.id, resume=True)
        assert (await replay)["vlm"] == chunks
        await replay.wait_closed()
        await summarize_from_store(
            str(job.id), storage=restarted, generator=Generator()
        )
        assert len(requests) == 4


async def test_native_config_selected_es_is_checkpointed_and_resumable(
    native_es, pipeline, monkeypatch
):
    import vss.ingestion.vlm as service
    from vss.ingestion import ingest
    from vss.storage import StorageConfig

    config, client, adapter = native_es
    source, requests, _ = pipeline
    options = dict(
        backends=["vlm"],
        model="pinned-model",
        decoder="opencv",
        chunk_duration=1,
        pipeline_revision="deployment-one",
        storage_config=StorageConfig(backend="elasticsearch", elasticsearch=config),
    )
    job = ingest(source, **options)
    try:
        chunks = (await job)["vlm"]
    finally:
        await job.wait_closed()
    async with adapter(config=config, client=client) as reader:
        assert (await reader.read("checkpoints", str(job.id))).records[0][
            "completion_state"
        ] == "complete"

    def forbidden(*args, **kwargs):
        raise AssertionError("Config-selected completed replay must not decode")

    monkeypatch.setattr(service, "probe_video", forbidden)
    monkeypatch.setattr(service, "subsample_frames", forbidden)
    replay = ingest(source, **options, job_id=job.id, resume=True)
    try:
        assert (await replay)["vlm"] == chunks
    finally:
        await replay.wait_closed()
    assert len(requests) == 3
