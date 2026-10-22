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

"""Storage integrates with main's separated processing and ingestion APIs."""

from uuid import uuid4

import pytest
from test_checkpoint_pipeline import pipeline as pipeline

import vss.ingestion.backend as backends
import vss.ingestion.vlm as processing
from vss.ingestion import IngestionQueue, RequestSource, ingest
from vss.storage import InMemoryStorageAdapter


async def _run_private_processing(*args, **kwargs):
    async with processing._processing_limiter():
        return await processing._process_video(*args, **kwargs)


async def test_stored_ingestion_uses_the_same_environment_selector_as_main(monkeypatch):
    calls = []

    class Backend(backends.IngestionBackend):
        async def ingest(self, source, **kwargs):
            calls.append(source.source_id)
            return []

        async def cancel(self, source_id):
            return False

        async def check_health(self):
            return True

        def capability_map(self):
            return {}

    monkeypatch.setitem(backends._BACKEND_REGISTRY, "stored-backend", Backend)
    monkeypatch.setenv("VSS_INGESTION_BACKEND", "stored-backend")
    monkeypatch.setenv("VLM_BACKEND", "unsupported-old-selector")
    queue = IngestionQueue()
    async with InMemoryStorageAdapter() as storage:
        job = ingest(RequestSource(source_id="source"), storage=storage, queue=queue)
        assert await job == {"stored-backend": []}
        await job.wait_closed()
    assert calls == ["source"]


def test_vlm_backend_is_available_to_unstored_ingestion():
    assert "vlm" in backends.available_backends()


def test_vlm_contracts_and_errors_are_owned_by_ingestion():
    import vss.video_processing as processing_api
    from vss.ingestion import VLMBackendError
    from vss.ingestion.contracts import VlmChunkResponse

    assert VLMBackendError is processing.VLMBackendError
    assert VlmChunkResponse.__module__ == "vss.ingestion.vlm_contracts"
    assert not hasattr(processing_api, "VLMBackendError")
    assert not hasattr(processing_api, "VlmChunkResponse")


async def test_checkpointed_jobs_obey_main_processing_concurrency_limit(
    tmp_path, pipeline, monkeypatch
):
    from test_checkpoint_pipeline import submit

    from vss.storage import FileStorageAdapter

    source, requests, _state = pipeline
    limiter = processing._processing_limiter()
    await limiter.acquire()
    # Hold every permit without using timing as evidence of contention.
    permits = 1
    while not limiter.locked():
        await limiter.acquire()
        permits += 1
    try:
        async with FileStorageAdapter(root=tmp_path / "store") as storage:
            queue = IngestionQueue()
            job = submit(source, storage, queue=queue, job_id=uuid4())
            import asyncio

            reached_limit = asyncio.Event()
            original = limiter.acquire

            async def acquire():
                reached_limit.set()
                return await original()

            monkeypatch.setattr(limiter, "acquire", acquire)
            await asyncio.wait_for(reached_limit.wait(), 2)
            assert not requests
            assert not job.done()
            limiter.release()
            permits -= 1
            assert await asyncio.wait_for(job, 5)
            await job.wait_closed()
    finally:
        for _ in range(permits):
            limiter.release()


async def test_waiting_checkpoint_cancel_releases_claim_without_processing(
    tmp_path, pipeline, monkeypatch
):
    import asyncio

    from test_checkpoint_pipeline import submit

    from vss.storage import FileStorageAdapter

    source, requests, _ = pipeline
    monkeypatch.setenv("VSS_PROCESSING_MAX_CONCURRENCY", "1")
    limiter = processing._processing_limiter()
    await limiter.acquire()
    arrived = asyncio.Event()
    original_acquire = limiter.acquire
    original_probe = processing.probe_video
    probe_calls = []

    async def acquire():
        arrived.set()
        return await original_acquire()

    async def probe(*args, **kwargs):
        probe_calls.append(True)
        return await original_probe(*args, **kwargs)

    monkeypatch.setattr(limiter, "acquire", acquire)
    monkeypatch.setattr(processing, "probe_video", probe)
    queue = IngestionQueue()
    async with FileStorageAdapter(root=tmp_path / "store") as storage:
        job = submit(source, storage, queue=queue)
        try:
            await asyncio.wait_for(arrived.wait(), 2)
            assert not probe_calls
            assert not requests
            job.cancel()
            with pytest.raises(asyncio.CancelledError):
                await job
            await asyncio.wait_for(job.wait_closed(), 2)
            assert not probe_calls
            assert not requests
        finally:
            limiter.release()
        retried = submit(source, storage, queue=queue, job_id=job.id)
        assert await asyncio.wait_for(retried, 5)
        await retried.wait_closed()


async def test_completed_replay_never_acquires_processing_permit(
    tmp_path, pipeline, monkeypatch
):
    from test_checkpoint_pipeline import submit

    from vss.storage import FileStorageAdapter

    source, requests, _ = pipeline
    async with FileStorageAdapter(root=tmp_path / "store") as storage:
        job = submit(source, storage)
        expected = (await job)["vlm"]
        await job.wait_closed()
        count = len(requests)

        def forbidden():
            raise AssertionError("completed replay must not start processing")

        monkeypatch.setattr(processing, "_processing_limiter", forbidden)
        resumed = submit(source, storage, job_id=job.id, resume=True)
        assert (await resumed)["vlm"] == expected
        await resumed.wait_closed()
        assert len(requests) == count


async def test_fractional_final_window_keeps_exact_checkpoint_nanoseconds(
    tmp_path, pipeline
):
    from test_checkpoint_pipeline import submit

    from vss.storage import FileStorageAdapter

    source, _requests, state = pipeline
    state["duration"] = 1.000000007
    async with FileStorageAdapter(root=tmp_path / "store") as storage:
        job = submit(source, storage)
        try:
            result = (await job)["vlm"]
            assert result[-1].chunk.end_pts == 1_000_000_007
            saved = await storage.read("checkpoints", str(job.id))
            assert saved.records[0]["completion_state"] == "complete"
        finally:
            await job.wait_closed()


async def test_direct_processing_and_checkpoint_job_share_one_permit(
    tmp_path, pipeline, monkeypatch
):
    import asyncio

    from test_checkpoint_pipeline import submit

    from vss.storage import FileStorageAdapter

    source, _requests, _state = pipeline
    monkeypatch.setenv("VSS_PROCESSING_MAX_CONCURRENCY", "1")
    entered = asyncio.Event()
    release = asyncio.Event()
    contended = asyncio.Event()
    original_backend = processing._VLM_BACKEND_REGISTRY["vlm"]
    original_probe = processing.probe_video
    constructions = []
    probes = []

    class GatedBackend(original_backend):
        def __init__(self, **kwargs):
            constructions.append(True)
            super().__init__(**kwargs)

        async def infer(self, *args, **kwargs):
            entered.set()
            await release.wait()
            return await super().infer(*args, **kwargs)

    async def probe(*args, **kwargs):
        probes.append(True)
        return await original_probe(*args, **kwargs)

    monkeypatch.setitem(processing._VLM_BACKEND_REGISTRY, "vlm", GatedBackend)
    monkeypatch.setattr(processing, "probe_video", probe)
    direct = asyncio.create_task(
        _run_private_processing(
            source, decoder="opencv", model="pinned-model", chunk_duration=1
        )
    )
    job = None
    async with FileStorageAdapter(root=tmp_path / "store") as storage:
        try:
            await asyncio.wait_for(entered.wait(), 2)
            limiter = processing._processing_limiter()
            original_acquire = limiter.acquire

            async def acquire():
                contended.set()
                return await original_acquire()

            monkeypatch.setattr(limiter, "acquire", acquire)
            job = submit(source, storage)
            await asyncio.wait_for(contended.wait(), 2)
            assert len(probes) == 1
            assert len(constructions) == 1
            assert not job.done()
            release.set()
            assert await asyncio.wait_for(direct, 5)
            assert await asyncio.wait_for(job, 5)
            await job.wait_closed()
            assert len(probes) == 2
            assert len(constructions) == 2
        finally:
            release.set()
            direct.cancel()
            await asyncio.gather(direct, return_exceptions=True)
            if job is not None:
                if not job.done():
                    job.cancel()
                await job.wait_closed()


@pytest.mark.parametrize("failure", ["probe-error", "probe-cancel", "sampling-error"])
async def test_stored_backend_closes_once_on_pre_stream_failure(
    pipeline, monkeypatch, failure
):
    import asyncio

    from test_checkpoint_pipeline import submit

    source, _requests, _state = pipeline
    created = []
    started = asyncio.Event()
    original_backend = processing._VLM_BACKEND_REGISTRY["vlm"]

    class TrackingBackend(original_backend):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.close_calls = 0
            created.append(self)

        async def aclose(self):
            self.close_calls += 1
            await super().aclose()

    async def probe(*args, **kwargs):
        started.set()
        if failure == "probe-cancel":
            await asyncio.Event().wait()
        raise RuntimeError("probe failed")

    monkeypatch.setitem(processing._VLM_BACKEND_REGISTRY, "vlm", TrackingBackend)
    if failure != "sampling-error":
        monkeypatch.setattr(processing, "probe_video", probe)
    else:

        def invalid_policy(*args, **kwargs):
            raise ValueError("sampling policy setup failed")

        monkeypatch.setattr(processing, "SamplingPolicy", invalid_policy)
    async with InMemoryStorageAdapter() as storage:
        job = submit(source, storage)
        if failure == "probe-cancel":
            await asyncio.wait_for(started.wait(), 2)
            job.cancel()
            with pytest.raises(asyncio.CancelledError):
                await job
        else:
            with pytest.raises((RuntimeError, ValueError)):
                await job
        await asyncio.wait_for(job.wait_closed(), 2)
        assert len(created) == 1
        assert created[0].close_calls == 1
        assert created[0]._client.is_closed


async def test_direct_processing_preserves_custom_backend_error_responses(
    pipeline, monkeypatch
):
    source, requests, _state = pipeline
    original_backend = processing._VLM_BACKEND_REGISTRY["vlm"]

    class ErrorResponseBackend(original_backend):
        async def infer(self, *args, **kwargs):
            response = await super().infer(*args, **kwargs)
            response.error = "custom provider result"
            return response

    monkeypatch.setitem(processing._VLM_BACKEND_REGISTRY, "vlm", ErrorResponseBackend)
    responses = await _run_private_processing(
        source, model="pinned-model", decoder="opencv", chunk_duration=1
    )
    assert len(responses) == 3
    assert len(requests) == 3
    assert all(item.error == "custom provider result" for item in responses)
