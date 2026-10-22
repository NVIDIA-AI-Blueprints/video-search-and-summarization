# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""S06/S07: freeze typed caption persistence before its implementation exists."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from vss.ingestion.contracts import ChunkInfo, VlmChunkResponse


def caption(index: int = 0, *, file: str = "video.mp4") -> VlmChunkResponse:
    return VlmChunkResponse(
        chunk=ChunkInfo(
            sourceId="original-source",
            chunkIdx=index,
            file=file,
            start_pts=index * 1_000_000_000,
            end_pts=(index + 1) * 1_000_000_000,
            pts_offset_ns=123,
        ),
        vlm_response=f"A person waves. café {index}",
        vlm_stats={"input_tokens": 10, "output_tokens": 20},
    )


def APIs():
    from vss.storage import FileStorageAdapter, StorageCorruptError
    from vss.summarization import AdapterChunkStore, FileChunkStore

    return FileStorageAdapter, StorageCorruptError, AdapterChunkStore, FileChunkStore


async def test_file_caption_roundtrip_detaches_and_preserves_caller_order(tmp_path):
    file_adapter, _, facade, _ = APIs()
    values = [caption(8), VlmChunkResponse(vlm_response="legacy"), caption(2)]
    async with file_adapter(root=tmp_path) as adapter:
        store = facade(adapter)
        await store.store_chunks("opaque/job", values)
        values[0].chunk.sourceId = "caller-mutated"
        first = await store.get_chunks("opaque/job")
        assert [item.vlm_response for item in first] == [
            "A person waves. café 8",
            "legacy",
            "A person waves. café 2",
        ]
        assert first[0].chunk.sourceId == "original-source"
        assert first[0].chunk.pts_offset_ns == 123
        first[0].vlm_stats["input_tokens"] = 999
        assert (await store.get_chunks("opaque/job"))[0].vlm_stats["input_tokens"] == 10
        await store.store_chunks("opaque/job", [])
        assert await store.get_chunks("opaque/job") == []
        with pytest.raises(KeyError, match="unknown summarization job"):
            await store.get_chunks("absent")


async def test_persisted_projection_excludes_credentials_and_artifacts(tmp_path):
    file_adapter, _, facade, _ = APIs()
    value = caption(
        file="https://user:private-password@example.org/video?token=signed-secret#secret"
    )
    value.chunk.cached_frames_cv_meta = [{"secret": "private-cache-secret"}]
    value.chunk.cv_metadata_json_file = "/tmp/private-metadata"
    value.chunk.osd_output_video_file = "/tmp/private-clip"
    value.vlm_stats["authorization"] = "private-stats-secret"
    async with file_adapter(root=tmp_path) as adapter:
        store = facade(adapter)
        await store.store_chunks("public-job", [value])
        loaded = (await store.get_chunks("public-job"))[0]
        assert loaded.chunk.file == "https://example.org/video"
        assert loaded.chunk.cached_frames_cv_meta == []
        assert loaded.chunk.cv_metadata_json_file == ""
        assert loaded.chunk.osd_output_video_file == ""
        assert loaded.vlm_stats == {"input_tokens": 10, "output_tokens": 20}
    disk = b"".join(p.read_bytes() for p in tmp_path.rglob("*.jsonl")).decode()
    for secret in (
        "private-password",
        "signed-secret",
        "private-cache-secret",
        "private-metadata",
        "private-clip",
        "private-stats-secret",
    ):
        assert secret not in disk
    assert value.chunk.file.endswith("#secret")  # persistence never mutates caller


async def test_domain_version_and_ordinal_corruption_fails_closed(tmp_path):
    file_adapter, corrupt, facade, _ = APIs()
    async with file_adapter(root=tmp_path) as adapter:
        store = facade(adapter)
        await store.store_chunks("job", [caption(), caption(1)])
        snapshot = await adapter.read("observations", "job")
        records = json.loads(json.dumps(snapshot.records))
        records[2]["ordinal"] = 0
        await adapter.write(
            "observations", "job", records, expected_revision=snapshot.revision
        )
        with pytest.raises(corrupt):
            await store.get_chunks("job")


async def test_standalone_writer_honors_ingestion_claim(tmp_path):
    file_adapter, _, facade, _ = APIs()
    from vss.storage import StorageBusyError

    async with (
        file_adapter(root=tmp_path) as first,
        file_adapter(root=tmp_path) as second,
    ):
        async with first.exclusive("jobs", "job"):
            with pytest.raises(StorageBusyError):
                await facade(second).store_chunks("job", [caption()])
        await facade(second).store_chunks("job", [caption()])


async def test_file_facade_owns_only_its_constructed_adapter(tmp_path):
    _, _, _, file_facade = APIs()
    async with file_facade(root=tmp_path) as store:
        await store.store_chunks("job", [caption()])
    async with file_facade(root=tmp_path) as reopened:
        assert (await reopened.get_chunks("job"))[0] == caption()


async def test_fresh_process_summary_after_source_deletion(tmp_path):
    file_adapter, _, facade, _ = APIs()
    from vss.summarization import summarize_from_store
    from vss.summarization.generators import GeneratedSummary, SummaryGenerator

    class Generator(SummaryGenerator):
        async def generate(self, events, *, prompt=None, model=None):
            return GeneratedSummary(text="Durable summary", model="test")

    async with file_adapter(root=tmp_path) as adapter:
        await facade(adapter).store_chunks("job", [caption()])
        expected = await summarize_from_store(
            "job", storage=adapter, generator=Generator()
        )
    script = """
import asyncio, importlib.abc, json, sys
from vss.storage import FileStorageAdapter
from vss.summarization import summarize_from_store
from vss.summarization.generators import GeneratedSummary, SummaryGenerator
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, name, path=None, target=None):
  if (name.split('.')[0] in {'cv2', 'PyNvVideoCodec'}
      or name.startswith('vss.ingestion.vlm')):
   raise ImportError('No video decoder or VLM access in summary process')
sys.meta_path.insert(0, Block())
class Generator(SummaryGenerator):
 async def generate(self, events, *, prompt=None, model=None):
  return GeneratedSummary(text='Durable summary', model='test')
async def main():
 async with FileStorageAdapter(root=sys.argv[1]) as storage:
  result=await summarize_from_store('job',storage=storage,generator=Generator())
  print(result.choices[0].message.content)
asyncio.run(main())
"""
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join(
            str(Path(path).resolve())
            for path in (
                "src",
                "packages/ingestion/src",
                "packages/summarization/src",
            )
        ),
        PYTHONIOENCODING="utf-8",
    )
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script, str(tmp_path)],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == json.loads(expected.choices[0].message.content)


async def test_store_storage_config_are_mutually_exclusive(tmp_path):
    from vss.storage import StorageConfig
    from vss.summarization import InMemoryChunkStore, summarize_from_store

    with pytest.raises(ValueError):
        await summarize_from_store(
            "job",
            store=InMemoryChunkStore(),
            storage_config=StorageConfig(backend="file", root=tmp_path),
        )


async def test_manual_bad_version_never_invokes_summary_generator(tmp_path):
    file_adapter, corrupt, facade, _ = APIs()
    from vss.summarization import summarize_from_store

    async with file_adapter(root=tmp_path) as adapter:
        await facade(adapter).store_chunks("job", [caption()])
        snap = await adapter.read("observations", "job")
        records = json.loads(json.dumps(snap.records))
        records[0]["schema_version"] = "9.0"
        await adapter.write(
            "observations", "job", records, expected_revision=snap.revision
        )
        with pytest.raises(corrupt):
            await summarize_from_store("job", storage=adapter)


async def test_legacy_observation_can_gain_verified_origin_without_changing_chunks(
    tmp_path,
):
    from vss.ingestion.persistence import ObservationStore

    file_adapter, _, facade, _ = APIs()
    values = [caption()]
    async with file_adapter(root=tmp_path) as adapter:
        await facade(adapter).store_chunks("job", values)
        old = await adapter.read("observations", "job")
        assert "original_source_id" not in old.records[0]
        assert await facade(adapter).get_chunks("job") == values
        async with adapter.exclusive("jobs", "job"):
            await ObservationStore(adapter).publish(
                "job", values, original_source_id="caller-source"
            )
        upgraded = await adapter.read("observations", "job")
        assert upgraded.revision == old.revision + 1
        assert upgraded.records[0]["original_source_id"] == "caller-source"
        assert upgraded.records[1:] == old.records[1:]
        assert await facade(adapter).get_chunks("job") == values


@pytest.mark.parametrize("change", ["origin", "caption", "omit-origin"])
async def test_origin_publication_conflicts_preserve_existing_snapshot(
    tmp_path, change
):
    from vss.ingestion.persistence import ObservationStore
    from vss.storage import StorageConflictError

    file_adapter, _, _, _ = APIs()
    values = [caption()]
    async with file_adapter(root=tmp_path) as adapter:
        store = ObservationStore(adapter)
        async with adapter.exclusive("jobs", "job"):
            await store.publish("job", values, original_source_id="caller-source")
            before = await adapter.read("observations", "job")
            if change == "caption":
                values[0].vlm_response = "Different caption"
            with pytest.raises(StorageConflictError):
                await store.publish(
                    "job",
                    values,
                    original_source_id=(
                        "different-source"
                        if change == "origin"
                        else None
                        if change == "omit-origin"
                        else "caller-source"
                    ),
                )
            assert await adapter.read("observations", "job") == before


async def test_ingestion_queue_identity_and_cleanup_waiters():
    from vss.ingestion.queue import IngestionQueue

    queue = IngestionQueue(max_concurrency=1)
    identity = uuid4()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def work():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await release.wait()

    job = queue.submit(work, job_id=identity)
    await entered.wait()
    assert job.id == identity
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    one = asyncio.create_task(job.wait_closed())
    two = asyncio.create_task(job.wait_closed())
    await asyncio.sleep(0)
    one.cancel()
    with pytest.raises(asyncio.CancelledError):
        await one
    assert not two.done()
    release.set()
    await asyncio.wait_for(two, 2)
    fresh = queue.submit(lambda: asyncio.sleep(0, result=3), job_id=identity)
    assert not job.cancel()
    assert await fresh == 3
    await fresh.wait_closed()


async def test_cancel_before_worker_starts_still_drains_and_releases_id():
    from vss.ingestion.queue import IngestionQueue

    queue = IngestionQueue(max_concurrency=1)
    identity = uuid4()
    job = queue.submit(lambda: asyncio.sleep(100), job_id=identity)
    with pytest.raises(ValueError):
        queue.submit(lambda: asyncio.sleep(0), job_id=identity)
    job.cancel()
    await asyncio.wait_for(job.wait_closed(), 2)
    assert queue.running_count == 0
    replacement = queue.submit(lambda: asyncio.sleep(0, result=4), job_id=identity)
    assert await replacement == 4
    await replacement.wait_closed()
