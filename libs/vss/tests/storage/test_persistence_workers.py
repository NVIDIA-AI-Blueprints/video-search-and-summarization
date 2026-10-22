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

"""Regression tests for domain codec isolation and checkpoint plan identity."""

from __future__ import annotations

import asyncio
import importlib
import threading
from typing import Any

import pytest


def _checkpoint_records():
    from vss.ingestion.persistence import (
        CheckpointManifest,
        CheckpointWindow,
        checkpoint_plan_fingerprint,
    )

    windows = (
        CheckpointWindow(chunk_idx=0, start_ns=0, end_ns=100),
        CheckpointWindow(chunk_idx=1, start_ns=100, end_ns=175),
    )
    samples = tuple(
        window.start_ns + (idx * (window.end_ns - window.start_ns)) // 8
        for window in windows
        for idx in range(8)
    )
    manifest = CheckpointManifest(
        job_id="job",
        original_source_id="source",
        source_revision_digest="source-digest",
        request_fingerprint="request-fingerprint",
        pipeline_revision="pipeline-one",
        backend="vlm",
        model="pinned",
        decoder="opencv",
        plan_fingerprint="0" * 64,
        windows=windows,
        sample_timestamps_ns=samples,
    )
    record = manifest.model_dump(mode="json")
    record["plan_fingerprint"] = checkpoint_plan_fingerprint(record)
    return record


@pytest.mark.parametrize(
    "field",
    [
        "windows",
        "sample_timestamps_ns",
        "source_revision_digest",
        "request_fingerprint",
        "pipeline_revision",
        "original_source_id",
        "backend",
        "model",
        "decoder",
    ],
)
def test_checkpoint_plan_digest_binds_grid_windows_and_manifest_metadata(field):
    from vss.ingestion.persistence import decode_checkpoint
    from vss.storage import StorageCorruptError, StorageSnapshot

    record = _checkpoint_records()
    if field == "windows":
        record["windows"][0]["end_ns"] = 101
        record["windows"][1]["start_ns"] = 101
    elif field == "sample_timestamps_ns":
        record[field][0] += 1
    elif field in {"source_revision_digest", "request_fingerprint"}:
        record[field] = "changed"
    elif field == "pipeline_revision":
        record[field] = "pipeline-two"
    elif field == "original_source_id":
        record[field] = "another-source"
    else:
        record[field] = "changed"
    snapshot = StorageSnapshot(revision=1, records=(record,))
    with pytest.raises(StorageCorruptError):
        decode_checkpoint("job", snapshot)


def test_checkpoint_rejects_noncanonical_sampling_grid_even_with_matching_digest():
    from vss.ingestion.persistence import (
        checkpoint_plan_fingerprint,
        decode_checkpoint,
    )
    from vss.storage import StorageCorruptError, StorageSnapshot

    record = _checkpoint_records()
    # A plan digest authenticates the stored plan, while the codec also checks
    # that the plan obeys the fixed eight samples per half-open window rule.
    record["sample_timestamps_ns"][2] += 1
    record["plan_fingerprint"] = checkpoint_plan_fingerprint(record)
    snapshot = StorageSnapshot(revision=1, records=(record,))
    with pytest.raises(StorageCorruptError):
        decode_checkpoint("job", snapshot)


def test_durable_response_drops_transient_cache_before_deepcopy() -> None:
    from vss.ingestion.contracts import ChunkInfo, VlmChunkResponse
    from vss.ingestion.persistence import durable_response

    class NonCopyable:
        def __deepcopy__(self, memo: dict[int, Any]) -> Any:
            raise AssertionError("transient cache must be omitted before copying")

    cache = NonCopyable()
    response = VlmChunkResponse(
        chunk=ChunkInfo(cached_frames_cv_meta=[cache]), vlm_response="caption"
    )
    result = durable_response(response)
    assert result.vlm_response == "caption"
    assert result.chunk is not None and result.chunk.cached_frames_cv_meta == []
    assert response.chunk is not None and response.chunk.cached_frames_cv_meta == [
        cache
    ]


async def test_observation_encoding_runs_off_event_loop(monkeypatch, worker_gate):
    persistence = importlib.import_module("vss.ingestion.persistence")
    from vss.ingestion.contracts import VlmChunkResponse
    from vss.storage import InMemoryStorageAdapter

    storage = InMemoryStorageAdapter(max_in_flight=1)
    original = persistence.observation_records

    def blocked(job_id: str, chunks: list[VlmChunkResponse]):
        worker_gate.block()
        return original(job_id, chunks)

    monkeypatch.setattr(persistence, "observation_records", blocked)
    async with storage:
        store = persistence.ObservationStore(storage)
        write = asyncio.create_task(store.store_chunks("job", [VlmChunkResponse()]))
        try:
            await worker_gate.wait_until_blocked(write)
            worker_gate.release.set()
            await asyncio.wait_for(write, 10)
            assert await store.get_chunks("job") == [VlmChunkResponse()]
        finally:
            worker_gate.release.set()
            await asyncio.gather(write, return_exceptions=True)


async def test_custom_domain_worker_fallback_bounds_admission_and_settles_cancel():
    from vss.ingestion.persistence import domain_work

    entered = threading.Event()
    release = threading.Event()
    state = {"active": 0, "started": 0}
    lock = threading.Lock()

    def blocked(value: int) -> int:
        with lock:
            state["active"] += 1
            state["started"] += 1
            if state["started"] == 4:
                entered.set()
        if not release.wait(2):
            raise TimeoutError("test did not release custom codec")
        with lock:
            state["active"] -= 1
        return value

    custom_storage = object()  # Public adapters need no private worker method.
    tasks = [
        asyncio.create_task(domain_work(custom_storage, blocked, index))
        for index in range(8)
    ]
    await asyncio.wait_for(asyncio.to_thread(entered.wait), timeout=2)
    await asyncio.sleep(0.03)
    assert state["started"] == 4
    for task in tasks[4:]:
        task.cancel()
    await asyncio.gather(*tasks[4:], return_exceptions=True)
    tasks[0].cancel()
    await asyncio.sleep(0.02)
    assert not tasks[0].done(), "cancellation returned before the codec settled"
    release.set()
    outcomes = await asyncio.gather(*tasks[:4], return_exceptions=True)
    assert isinstance(outcomes[0], asyncio.CancelledError)
    assert outcomes[1:] == [1, 2, 3]
    assert state["active"] == 0


async def test_custom_domain_worker_cancel_precedes_later_worker_failure():
    from vss.ingestion.persistence import domain_work

    entered, release = threading.Event(), threading.Event()

    def operation():
        entered.set()
        assert release.wait(2)
        raise ValueError("late worker failure")

    task = asyncio.create_task(domain_work(object(), operation))
    await asyncio.wait_for(asyncio.to_thread(entered.wait), 2)
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
