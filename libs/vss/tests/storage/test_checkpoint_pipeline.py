# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""S08–S11: prewritten public-ingestion checkpoint and replay contracts."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import httpx
import numpy as np
import pytest

import vss.ingestion.vlm as service
import vss.ingestion.vlm as vlm
from vss.ingestion import IngestionQueue, RequestSource, ingest
from vss.video_processing import Frame, MediaFacts


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"immutable source fixture")
    source = RequestSource(url=str(source_path), source_id="retained-source")
    requests: list[dict] = []
    state = {"fail_chunk": None, "fail_once": True, "duration": 3.0, "gap": False}

    def request_handler(request: httpx.Request):
        body = json.loads(request.content)
        text = body["messages"][-1]["content"][0]["text"]
        # Inspect the real public adapter's window metadata, not a pretend result.
        window = re.search(r"Chunk (\d+) spans ([0-9.]+) to", text)
        assert window is not None
        index, start = int(window[1]), float(window[2])
        requests.append(
            {
                "index": index,
                "body": body,
                "url": str(request.url),
                "authorization": request.headers.get("authorization"),
            }
        )
        if state["fail_chunk"] == index and state["fail_once"]:
            state["fail_once"] = False
            return httpx.Response(
                500, json={"error": {"message": "private-api-secret"}}
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "events": [
                                        {
                                            "start_time": start,
                                            "end_time": start + 0.5,
                                            "type": "motion",
                                            "description": f"Chunk {index} moves",
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 9},
            },
        )

    class HTTPBackend(vlm.OpenAICompatibleVLMBackend):
        def __init__(self, **kwargs):
            super().__init__(
                client=httpx.AsyncClient(
                    transport=httpx.MockTransport(request_handler)
                ),
                **kwargs,
            )

        async def aclose(self):
            await self._client.aclose()

    monkeypatch.setitem(vlm._VLM_BACKEND_REGISTRY, "vlm", HTTPBackend)
    monkeypatch.setenv("VLM_BASE_URL", "https://endpoint-a.test/v1")
    monkeypatch.setenv("VLM_API_KEY", "private-api-secret")
    monkeypatch.delenv("VSS_STORAGE_BACKEND", raising=False)

    async def probe(*args, **kwargs):
        return MediaFacts(source_id=source.source_id, duration_s=state["duration"])

    async def frames(policy) -> AsyncIterator[Frame]:
        for i, t in enumerate(policy.timestamps_ns):
            if state["gap"] and 1_000_000_000 <= t < 2_000_000_000:
                continue
            yield Frame(
                tensor=np.zeros((2, 2, 3), dtype=np.uint8),
                timestamp_ns=t,
                requested_timestamp_ns=t,
                source_id=source.source_id,
                index=i,
                timestamp_quality="pts",
                decoder="opencv",
                device="cpu",
            )

    # S08 tests isolate optional native encoders; S12 separately exercises JPEG.
    monkeypatch.setattr(Frame, "to_base64", lambda self, **kwargs: "AAECAwQF")
    monkeypatch.setattr(service, "probe_video", probe)
    monkeypatch.setattr(
        service, "subsample_frames", lambda _source, policy, **kwargs: frames(policy)
    )
    return source, requests, state


def submit(source, storage, **kwargs):
    return ingest(
        source,
        backends=["vlm"],
        decoder="opencv",
        model="pinned-model",
        chunk_duration=1,
        pipeline_revision="deployment-one",
        storage=storage,
        **kwargs,
    )


async def test_checkpointed_gpu_plans_from_decode_when_probe_duration_is_unknown(
    tmp_path, pipeline, monkeypatch
):
    from vss.storage import FileStorageAdapter

    source, requests, state = pipeline
    state["duration"] = None
    scanned_decoders: list[str] = []

    async def decoded_frames(*_args, **kwargs) -> AsyncIterator[Frame]:
        scanned_decoders.append(kwargs["options"].decoder)
        for index, timestamp_ns in enumerate((0, 1_000_000_000, 2_000_000_000)):
            yield Frame(
                tensor=np.zeros((2, 2, 3), dtype=np.uint8),
                timestamp_ns=timestamp_ns,
                source_id=source.source_id,
                index=index,
                timestamp_quality="pts",
                decoder="pynv",
                device="gpu",
            )

    monkeypatch.setattr(service, "decode_frames", decoded_frames)
    async with FileStorageAdapter(root=tmp_path / "durable") as storage:
        job = ingest(
            source,
            backends=["vlm"],
            decoder="pynv",
            model="pinned-model",
            chunk_duration=1,
            pipeline_revision="deployment-one",
            storage=storage,
        )
        responses = await job
        await job.wait_closed()

    assert scanned_decoders == ["pynv"]
    assert [request["index"] for request in requests] == [0, 1, 2]
    assert len(responses["vlm"]) == 3


@pytest.mark.parametrize("persistent", [False, True])
async def test_provider_error_is_ingestion_failure_before_confirmation(
    tmp_path, pipeline, monkeypatch, persistent
):
    from vss.ingestion import VLMBackendError
    from vss.ingestion._persistent import CheckpointRun
    from vss.storage import (
        FileStorageAdapter,
        InMemoryStorageAdapter,
        StorageNotFoundError,
    )

    source, requests, _ = pipeline
    original = vlm._VLM_BACKEND_REGISTRY["vlm"]
    confirmations = []
    original_confirm = CheckpointRun.confirm

    class FailedOutput(original):
        async def infer(self, *args, **kwargs):
            response = await super().infer(*args, **kwargs)
            response.error = "private-provider-secret"
            return response

    async def observed_confirm(self, idx, response):
        confirmations.append(idx)
        await original_confirm(self, idx, response)

    monkeypatch.setitem(vlm._VLM_BACKEND_REGISTRY, "vlm", FailedOutput)
    monkeypatch.setattr(CheckpointRun, "confirm", observed_confirm)
    async with FileStorageAdapter(root=tmp_path / "durable") as store:
        memory = InMemoryStorageAdapter()
        job = submit(source, store if persistent else memory)
        try:
            with pytest.raises(VLMBackendError) as caught:
                await job
            assert "private-provider-secret" not in str(caught.value)
            assert [request["index"] for request in requests] == [0]
            assert confirmations == []
            if persistent:
                snapshot = await store.read("checkpoints", str(job.id))
                assert len(snapshot.records) == 1
                with pytest.raises(StorageNotFoundError):
                    await store.read("observations", str(job.id))
        finally:
            await job.wait_closed()
            await memory.aclose()


async def test_failure_then_resume_never_dispatches_confirmed_chunks(
    tmp_path, pipeline
):
    from vss.ingestion import VLMBackendError
    from vss.storage import FileStorageAdapter
    from vss.summarization import AdapterChunkStore

    source, requests, state = pipeline
    state["fail_chunk"] = 2
    root = tmp_path / "durable"
    async with FileStorageAdapter(root=root) as storage:
        job = submit(source, storage)
        with pytest.raises(VLMBackendError) as error:
            await job
        await job.wait_closed()
        assert "private-api-secret" not in str(error.value)
        assert [r["index"] for r in requests] == [0, 1, 2]
        with pytest.raises(KeyError):
            await AdapterChunkStore(storage).get_chunks(str(job.id))
    async with FileStorageAdapter(root=root) as reopened:
        resumed = submit(source, reopened, job_id=job.id, resume=True)
        chunks = (await resumed)["vlm"]
        await resumed.wait_closed()
        assert resumed.id == job.id
        assert [r["index"] for r in requests] == [0, 1, 2, 2]
        assert [c.chunk.chunkIdx for c in chunks] == [0, 1, 2]
        assert [c.chunk.is_last for c in chunks] == [False, False, True]
        assert [c.vlm_stats for c in chunks] == [
            {"input_tokens": 7, "output_tokens": 9}
        ] * 3
        assert await AdapterChunkStore(reopened).get_chunks(str(job.id)) == chunks
        replay = (await submit(source, reopened, job_id=job.id, resume=True))["vlm"]
        assert replay == chunks
        assert len(requests) == 4
    disk = b"".join(p.read_bytes() for p in root.rglob("*.jsonl"))
    assert b"private-api-secret" not in disk


async def test_vlm_backend_resumes_confirmed_chunks(tmp_path, pipeline):
    from vss.ingestion import VLMBackendError
    from vss.storage import FileStorageAdapter

    source, requests, state = pipeline
    state["fail_chunk"] = 2
    options = {
        "backends": ["vlm"],
        "decoder": "opencv",
        "model": "pinned-model",
        "chunk_duration": 1,
        "pipeline_revision": "deployment-one",
    }
    root = tmp_path / "durable"

    async with FileStorageAdapter(root=root) as storage:
        job = ingest(source, storage=storage, **options)
        with pytest.raises(VLMBackendError):
            await job
        await job.wait_closed()
        assert [request["index"] for request in requests] == [0, 1, 2]

    async with FileStorageAdapter(root=root) as storage:
        resumed = ingest(
            source,
            storage=storage,
            job_id=job.id,
            resume=True,
            **options,
        )
        chunks = (await resumed)["vlm"]
        await resumed.wait_closed()

    assert [request["index"] for request in requests] == [0, 1, 2, 2]
    assert [chunk.chunk.chunkIdx for chunk in chunks] == [0, 1, 2]


@pytest.mark.parametrize(
    "change",
    [
        "source",
        "camera",
        "prompt",
        "model",
        "backend",
        "revision",
        "generation",
        "endpoint",
    ],
)
async def test_resume_identity_change_fails_before_another_request(
    tmp_path, pipeline, change, monkeypatch
):
    from vss.storage import FileStorageAdapter, StorageConflictError

    source, requests, state = pipeline
    async with FileStorageAdapter(root=tmp_path / "durable") as storage:
        job = submit(source, storage)
        await job
        count = len(requests)
        options = dict(
            backends=["vlm"],
            decoder="opencv",
            model="pinned-model",
            chunk_duration=1,
            pipeline_revision="deployment-one",
            storage=storage,
            job_id=job.id,
            resume=True,
        )
        changed_source = source.model_copy(deep=True)
        if change == "source":
            changed_source.source_id = "new-source"
        if change == "camera":
            changed_source.camera_id = "another-camera"
        if change == "prompt":
            options["prompt"] = "different prompt"
        if change == "model":
            options["model"] = "new-model"
        if change == "backend":
            monkeypatch.setitem(
                service._VLM_BACKEND_REGISTRY,
                "alternate_vlm",
                service._VLM_BACKEND_REGISTRY["vlm"],
            )
            options["backends"] = ["alternate_vlm"]
        if change == "revision":
            options["pipeline_revision"] = "deployment-two"
        if change == "generation":
            options["generation_config"] = {"temperature": 0.8}
        if change == "endpoint":
            monkeypatch.setenv("VLM_BASE_URL", "https://endpoint-b.test/v1")
        with pytest.raises(StorageConflictError):
            await ingest(changed_source, **options)
        assert len(requests) == count


async def test_completed_checkpoint_upgrades_legacy_origin_without_decode_or_inference(
    tmp_path, pipeline, monkeypatch
):
    from vss.storage import FileStorageAdapter

    source, requests, _ = pipeline
    async with FileStorageAdapter(root=tmp_path / "durable") as storage:
        job = submit(source, storage)
        chunks = (await job)["vlm"]
        await job.wait_closed()
        snapshot = await storage.read("observations", str(job.id))
        assert snapshot.records[0]["original_source_id"] == source.source_id
        records = json.loads(json.dumps(snapshot.records))
        records[0].pop("original_source_id")
        await storage.write(
            "observations", str(job.id), records, expected_revision=snapshot.revision
        )
        request_count = len(requests)

        def forbidden(*args, **kwargs):
            raise AssertionError("Completed replay must not decode")

        monkeypatch.setattr(service, "probe_video", forbidden)
        monkeypatch.setattr(service, "subsample_frames", forbidden)
        resumed = submit(source, storage, job_id=job.id, resume=True)
        assert (await resumed)["vlm"] == chunks
        await resumed.wait_closed()
        assert len(requests) == request_count
        upgraded = await storage.read("observations", str(job.id))
        assert upgraded.records[0]["original_source_id"] == source.source_id
        assert upgraded.records[1:] == snapshot.records[1:]


async def test_changed_file_with_same_source_id_invalidates_checkpoint(
    tmp_path, pipeline
):
    from vss.storage import FileStorageAdapter, StorageConflictError

    source, requests, state = pipeline
    async with FileStorageAdapter(root=tmp_path / "durable") as storage:
        job = submit(source, storage)
        await job
        Path(source.url).write_bytes(b"changed video bytes")
        with pytest.raises(StorageConflictError):
            await submit(source, storage, job_id=job.id, resume=True)
        assert len(requests) == 3


async def test_empty_windows_are_explicit_and_not_inferred(tmp_path, pipeline):
    from vss.storage import FileStorageAdapter

    source, requests, state = pipeline
    state["gap"] = True
    async with FileStorageAdapter(root=tmp_path / "durable") as storage:
        job = submit(source, storage)
        chunks = (await job)["vlm"]
        assert [r["index"] for r in requests] == [0, 2]
        snap = await storage.read("checkpoints", str(job.id))
        statuses = {r["chunk_idx"]: r["state"] for r in snap.records[1:]}
        assert statuses == {0: "success", 1: "empty", 2: "success"}
        assert [c.chunk.chunkIdx for c in chunks] == [0, 2]
        assert chunks[-1].chunk.is_last


async def test_checksum_valid_incomplete_complete_state_fails_closed(
    tmp_path, pipeline
):
    from vss.storage import FileStorageAdapter, StorageCorruptError

    source, requests, state = pipeline
    async with FileStorageAdapter(root=tmp_path / "durable") as storage:
        job = submit(source, storage)
        await job
        snap = await storage.read("checkpoints", str(job.id))
        await storage.write(
            "checkpoints",
            str(job.id),
            snap.records[:-1],
            expected_revision=snap.revision,
        )
        with pytest.raises(StorageCorruptError):
            await submit(source, storage, job_id=job.id, resume=True)
        assert len(requests) == 3


async def test_final_publication_failure_is_repaired_without_decode_or_inference(
    tmp_path, pipeline, monkeypatch
):
    from vss.storage import FileStorageAdapter, StorageError
    from vss.summarization import AdapterChunkStore

    source, requests, state = pipeline

    class OnceFailure(FileStorageAdapter):
        fail = True

        async def write(self, namespace, key, records, *, expected_revision):
            if namespace == "observations" and self.fail:
                self.fail = False
                raise StorageError("publication unavailable")
            return await super().write(
                namespace, key, records, expected_revision=expected_revision
            )

    async with OnceFailure(root=tmp_path / "durable") as storage:
        job = submit(source, storage)
        with pytest.raises(StorageError):
            await job
        await job.wait_closed()

        async def forbidden(*args, **kwargs):
            raise AssertionError("completed checkpoint should not probe/decode")

        monkeypatch.setattr(service, "probe_video", forbidden)
        monkeypatch.setattr(service, "subsample_frames", forbidden)
        result = (await submit(source, storage, job_id=job.id, resume=True))["vlm"]
        assert len(result) == 3
        assert len(requests) == 3
        assert await AdapterChunkStore(storage).get_chunks(str(job.id)) == result


async def test_no_next_inference_before_successful_checkpoint_write(tmp_path, pipeline):
    from vss.storage import FileStorageAdapter, StorageError

    source, requests, state = pipeline

    class Failure(FileStorageAdapter):
        async def write(self, namespace, key, records, *, expected_revision):
            if namespace == "checkpoints" and len(records) > 1:
                raise StorageError("disk failure")
            return await super().write(
                namespace, key, records, expected_revision=expected_revision
            )

    async with Failure(root=tmp_path / "durable") as storage:
        job = submit(source, storage)
        with pytest.raises(StorageError):
            await job
        await job.wait_closed()
        assert [r["index"] for r in requests] == [0]


async def test_frozen_request_controls_actual_endpoint_and_values(
    tmp_path, pipeline, monkeypatch
):
    from vss.storage import FileStorageAdapter

    source, requests, state = pipeline
    queue = IngestionQueue(max_concurrency=1)
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocker():
        entered.set()
        await release.wait()

    head = queue.submit(blocker)
    await entered.wait()
    config = {"max_tokens": 123, "temperature": 0.0}
    async with FileStorageAdapter(root=tmp_path / "durable") as storage:
        job = submit(source, storage, queue=queue, generation_config=config)
        monkeypatch.setenv("VLM_BASE_URL", "https://endpoint-b.test/v1")
        monkeypatch.setenv("VLM_MODEL", "wrong-model")
        config["max_tokens"] = 999

        class Forbidden(vlm.VLMBackend):
            def __init__(self):
                raise AssertionError("registry rebinding changed queued job")

        monkeypatch.setitem(vlm._VLM_BACKEND_REGISTRY, "vlm", Forbidden)
        release.set()
        await head
        await job
        assert all(r["url"].startswith("https://endpoint-a.test/") for r in requests)
        assert all(r["body"]["max_tokens"] == 123 for r in requests)
        assert all(r["body"]["model"] == "pinned-model" for r in requests)


async def test_absent_credentials_are_frozen_before_queueing(
    tmp_path, pipeline, monkeypatch
):
    from vss.storage import FileStorageAdapter

    source, requests, state = pipeline
    monkeypatch.delenv("VLM_API_KEY", raising=False)
    queue = IngestionQueue(max_concurrency=1)
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocker():
        entered.set()
        await release.wait()

    head = queue.submit(blocker)
    await entered.wait()
    async with FileStorageAdapter(root=tmp_path / "durable") as storage:
        job = submit(source, storage, queue=queue)
        monkeypatch.setenv("VLM_API_KEY", "late-credential")
        release.set()
        await head
        await job
        await job.wait_closed()
    assert requests and all(r["authorization"] is None for r in requests)


async def test_memory_resume_and_whole_request_resume_are_rejected(pipeline, tmp_path):
    from vss.storage import (
        FileStorageAdapter,
        InMemoryStorageAdapter,
        StorageConfigurationError,
    )

    source, requests, state = pipeline
    async with InMemoryStorageAdapter() as memory:
        with pytest.raises(StorageConfigurationError):
            await submit(source, memory, job_id=UUID(int=10), resume=True)
    async with FileStorageAdapter(root=tmp_path / "durable") as storage:
        with pytest.raises(StorageConfigurationError):
            await ingest(
                source,
                backends=["rtvi"],
                storage=storage,
                job_id=UUID(int=11),
                resume=True,
            )
    assert not requests
