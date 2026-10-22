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

"""S07–S11 adversarial contracts added before checkpoint orchestration.

Inference uses the real public backend and HTTP request schema. Small deterministic
frame streams isolate persistence; these tests do not claim native decode or live
provider acceptance. Fresh-process tests also replace image encoding explicitly.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from test_checkpoint_pipeline import pipeline as pipeline
from test_checkpoint_pipeline import submit

import vss.ingestion.vlm as service
import vss.ingestion.vlm as vlm
from vss.ingestion import IngestionQueue
from vss.video_processing import Frame, MediaFacts


@pytest.fixture(autouse=True)
def deterministic_transfer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock the optional encoder, not inference or caption response construction."""
    monkeypatch.setattr(
        Frame,
        "to_base64",
        lambda self, *, format="jpeg": base64.b64encode(self.tensor.tobytes()).decode(),
    )


@pytest.mark.parametrize(
    "damage", ["success-and-empty", "wrong-source", "null-response"]
)
async def test_logically_invalid_checkpoints_fail_before_republication_or_http(
    tmp_path: Path, pipeline: Any, damage: str
) -> None:
    from vss.storage import FileStorageAdapter, StorageCorruptError

    source, requests, state = pipeline
    state["gap"] = True
    async with FileStorageAdapter(root=tmp_path / "durable") as storage:
        job = submit(source, storage)
        await job
        await job.wait_closed()
        snapshot = await storage.read("checkpoints", str(job.id))
        records = copy.deepcopy(list(snapshot.records))
        success = next(record for record in records[1:] if record["state"] == "success")
        if damage == "success-and-empty":
            empty = next(record for record in records[1:] if record["state"] == "empty")
            # Reuse a real valid empty record shape. Its index/bounds now identify
            # an already-successful window; all original coverage remains present.
            duplicate = {
                key: success.get(key, value)
                if key not in {"state", "record_type"}
                else value
                for key, value in empty.items()
            }
            records.append(duplicate)
        elif damage == "wrong-source":
            success["response"]["chunk"]["sourceId"] = "unrelated-source"
        else:
            success["response"] = None
        await storage.write(
            "checkpoints", str(job.id), records, expected_revision=snapshot.revision
        )
        before = await storage.read("observations", str(job.id))
        count = len(requests)
        resumed = submit(source, storage, job_id=job.id, resume=True)
        with pytest.raises(StorageCorruptError):
            await resumed
        await resumed.wait_closed()
        assert len(requests) == count
        assert await storage.read("observations", str(job.id)) == before


async def test_decoder_error_does_not_turn_unvisited_tail_into_empty_success(
    tmp_path: Path, pipeline: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vss.storage import FileStorageAdapter
    from vss.summarization import AdapterChunkStore
    from vss.video_processing import DecodeError

    source, requests, state = pipeline
    state["duration"] = 4.0
    failing = True

    async def frames(*_: Any, **__: Any):
        for index, timestamp in enumerate((0, 2_000_000_000)):
            yield Frame(
                tensor=np.zeros((2, 2, 3), dtype=np.uint8),
                timestamp_ns=timestamp,
                requested_timestamp_ns=timestamp,
                source_id=source.source_id,
                index=index,
                timestamp_quality="pts",
                decoder="opencv",
                device="cpu",
            )
        if failing:
            raise DecodeError("decoder failed before verified EOF")

    monkeypatch.setattr(service, "subsample_frames", frames)
    async with FileStorageAdapter(root=tmp_path / "durable") as storage:
        job = submit(source, storage)
        with pytest.raises(DecodeError):
            await job
        await job.wait_closed()
        checkpoint = await storage.read("checkpoints", str(job.id))
        assert checkpoint.records[0]["completion_state"] != "complete"
        states = {
            record["chunk_idx"]: record["state"] for record in checkpoint.records[1:]
        }
        assert states.get(0) == "success"
        # Seeing frame 2 can prove window 1 empty. It cannot prove the tail empty
        # or complete window 2 while the decoder still might return more frames.
        assert 2 not in states
        assert 3 not in states
        with pytest.raises(KeyError):
            await AdapterChunkStore(storage).get_chunks(str(job.id))
        failing = False
        resumed = submit(source, storage, job_id=job.id, resume=True)
        result = (await resumed)["vlm"]
        await resumed.wait_closed()
        assert [request["index"] for request in requests] == [0, 2]
        assert [response.chunk.chunkIdx for response in result] == [0, 2]
        assert result[-1].chunk.is_last
        final = await storage.read("checkpoints", str(job.id))
        assert {
            record["chunk_idx"]: record["state"] for record in final.records[1:]
        } == {0: "success", 1: "empty", 2: "success", 3: "empty"}


@pytest.mark.parametrize(
    "damage",
    [
        "shortened-grid",
        "altered-grid",
        "extended-self-consistent-plan",
        "model",
        "backend",
        "decoder",
        "pipeline_revision",
        "source_revision_digest",
    ],
)
async def test_running_checkpoint_plan_and_identity_cannot_drift_behind_fingerprint(
    tmp_path: Path, pipeline: Any, damage: str
) -> None:
    from vss.ingestion import VLMBackendError
    from vss.storage import FileStorageAdapter, StorageCorruptError
    from vss.summarization import AdapterChunkStore

    source, requests, state = pipeline
    state["fail_chunk"] = 2
    async with FileStorageAdapter(root=tmp_path / "durable") as storage:
        initial = submit(source, storage)
        with pytest.raises(VLMBackendError):
            await initial
        await initial.wait_closed()
        saved = await storage.read("checkpoints", str(initial.id))
        records = copy.deepcopy(list(saved.records))
        manifest = records[0]
        original_fingerprint = manifest["request_fingerprint"]
        if damage == "shortened-grid":
            manifest["sample_timestamps_ns"] = [0]
        elif damage == "altered-grid":
            manifest["sample_timestamps_ns"][1] += 1
        elif damage == "extended-self-consistent-plan":
            extra = copy.deepcopy(manifest["windows"][-1])
            extra.update(chunk_idx=3, start_ns=3_000_000_000, end_ns=4_000_000_000)
            manifest["windows"].append(extra)
            manifest["sample_timestamps_ns"].extend(
                3_000_000_000 + sample * 125_000_000 for sample in range(8)
            )
        else:
            manifest[damage] = "changed-without-updating-the-bound-identity"
        assert manifest["request_fingerprint"] == original_fingerprint
        await storage.write(
            "checkpoints", str(initial.id), records, expected_revision=saved.revision
        )
        before_requests = len(requests)
        resumed = submit(source, storage, job_id=initial.id, resume=True)
        with pytest.raises(StorageCorruptError):
            await resumed
        await resumed.wait_closed()
        assert len(requests) == before_requests
        # In particular, an exhausted shortened grid cannot convert the pending
        # chunk into an empty success and publish an apparently complete result.
        with pytest.raises(KeyError):
            await AdapterChunkStore(storage).get_chunks(str(initial.id))


@pytest.mark.parametrize("damage", ["error", "nonfinite-stats", "missing-chunk"])
async def test_invalid_provider_output_is_unconfirmed_and_may_be_retried(
    tmp_path: Path,
    pipeline: Any,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    from vss.ingestion import VLMBackendError
    from vss.storage import FileStorageAdapter, StorageError
    from vss.summarization import AdapterChunkStore

    source, requests, _ = pipeline
    original = vlm._VLM_BACKEND_REGISTRY["vlm"]
    invalid = True

    class InvalidOutput(original):
        async def infer(self, *args: Any, **kwargs: Any):
            response = await super().infer(*args, **kwargs)
            if not invalid:
                return response
            if damage == "error":
                response.error = "private-api-secret"
            elif damage == "nonfinite-stats":
                response.vlm_stats["input_tokens"] = float("nan")
            else:
                response.chunk = None
            return response

    monkeypatch.setitem(vlm._VLM_BACKEND_REGISTRY, "vlm", InvalidOutput)
    async with FileStorageAdapter(root=tmp_path / "durable") as storage:
        job = submit(source, storage)
        with pytest.raises((StorageError, VLMBackendError)) as caught:
            await job
        await job.wait_closed()
        assert [request["index"] for request in requests] == [0]
        checkpoint = await storage.read("checkpoints", str(job.id))
        assert not any(
            record.get("state") == "success" for record in checkpoint.records[1:]
        )
        with pytest.raises(KeyError):
            await AdapterChunkStore(storage).get_chunks(str(job.id))
        assert "private-api-secret" not in "".join(
            traceback.format_exception(caught.value)
        )
        invalid = False
        # Keep the same declared pipeline revision: the injected failure only
        # invalidated output, and no inference response was durably confirmed.
        resumed = submit(source, storage, job_id=job.id, resume=True)
        await resumed
        await resumed.wait_closed()
        assert [request["index"] for request in requests] == [0, 0, 1, 2]


async def test_provider_failure_traceback_and_logs_do_not_expose_configured_secret(
    tmp_path: Path, pipeline: Any, caplog: pytest.LogCaptureFixture
) -> None:
    from vss.ingestion import VLMBackendError
    from vss.storage import FileStorageAdapter

    source, _, state = pipeline
    state["fail_chunk"] = 1
    async with FileStorageAdapter(root=tmp_path / "durable") as storage:
        job = submit(source, storage)
        with pytest.raises(VLMBackendError) as caught:
            await job
        await job.wait_closed()
        rendered = "".join(traceback.format_exception(caught.value))
        assert "private-api-secret" not in rendered
        assert "private-api-secret" not in caplog.text
    assert all(
        b"private-api-secret" not in path.read_bytes()
        for path in (tmp_path / "durable").rglob("*.jsonl")
    )


async def test_queued_source_and_nested_generation_mutation_do_not_change_inference(
    tmp_path: Path, pipeline: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vss.storage import FileStorageAdapter

    source, requests, _ = pipeline
    original_id = source.source_id
    original_path = source.url
    queue = IngestionQueue(max_concurrency=1)
    started, release = asyncio.Event(), asyncio.Event()

    async def blocker() -> None:
        started.set()
        await release.wait()

    async def frames(*_: Any, **__: Any):
        for index in range(3):
            yield Frame(
                tensor=np.zeros((2, 2, 3), dtype=np.uint8),
                timestamp_ns=index * 1_000_000_000,
                source_id=original_id,
                index=index,
                timestamp_quality="pts",
                decoder="opencv",
                device="cpu",
            )

    async def probe(*_: Any, **__: Any):
        return MediaFacts(source_id=original_id, duration_s=3)

    monkeypatch.setattr(service, "probe_video", probe)
    monkeypatch.setattr(service, "subsample_frames", frames)
    head = queue.submit(blocker)
    await started.wait()
    generation = {"metadata": {"request": {"label": "before"}}}
    async with FileStorageAdapter(root=tmp_path / "durable") as storage:
        job = submit(source, storage, queue=queue, generation_config=generation)
        try:
            generation["metadata"]["request"]["label"] = "after"
            source.source_id = "changed-by-caller"
            source.url = str(tmp_path / "does-not-exist.mp4")
            source.camera_id = "different-camera"
        finally:
            release.set()
        await head
        result = (await job)["vlm"]
        await job.wait_closed()
        assert all(
            request["body"]["metadata"]["request"]["label"] == "before"
            for request in requests
        )
        assert all(response.chunk.sourceId == original_id for response in result)
        assert all(response.chunk.file == original_path for response in result)


PROCESS_PIPELINE = r"""
import asyncio, base64, importlib.abc, json, pathlib, re, sys
from uuid import UUID
phase, root, source_path, job_id = sys.argv[1:]
if phase == "summary":
    class Block(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] in {"cv2", "PyNvVideoCodec"} or fullname in {
                "vss.ingestion.service", "vss.ingestion.vlm", "vss.ingestion.rtvi",
                "vss.ingestion.backend", "vss.ingestion.vlm",
            }:
                raise ImportError("summary must not import ingestion execution")
    sys.meta_path.insert(0, Block())
from vss.storage import FileStorageAdapter
if phase != "summary":
    import httpx, numpy as np
    import vss.ingestion.vlm as service
    import vss.ingestion.vlm as vlm
    from vss.ingestion import RequestSource, ingest, VLMBackendError
    from vss.video_processing import Frame, MediaFacts
    Frame.to_base64 = lambda self, *, format="jpeg": base64.b64encode(
        bytes([int(self.tensor[0, 0, 0])])
    ).decode()
    seen = []
    def handler(request):
        body = json.loads(request.content)
        content = body["messages"][-1]["content"]
        match = re.search(r"Chunk (\d+) spans ([0-9.]+) to", content[0]["text"])
        index, start = int(match[1]), float(match[2])
        pixels = [base64.b64decode(part["image_url"]["url"].split(",", 1)[1])[0]
                  for part in content if part["type"] == "image_url"]
        assert pixels == [index], (index, pixels)
        seen.append(index)
        if phase == "first" and index == 2:
            return httpx.Response(503, json={"error": {"message": "retry later"}})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps({"events": [{
                "start_time": start, "end_time": start + .5,
                "type": "motion", "description": "motion in chunk " + str(index)
            }]})}}], "usage": {"prompt_tokens": 3, "completion_tokens": 4}
        })
    class Backend(vlm.OpenAICompatibleVLMBackend):
        def __init__(self, **kwargs):
            super().__init__(client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler)), **kwargs)
        async def aclose(self):
            await self._client.aclose()
    vlm.register_vlm_backend("vlm", Backend)
    async def probe(*args, **kwargs):
        return MediaFacts(source_id="source-retained", duration_s=3)
    async def frames(*args, **kwargs):
        for index in range(3):
            yield Frame(tensor=np.full((2, 2, 3), index, dtype=np.uint8),
                        timestamp_ns=index*1_000_000_000, source_id="source-retained",
                        index=index, timestamp_quality="pts",
                        decoder="opencv", device="cpu")
    service.probe_video, service.subsample_frames = probe, frames
from vss.summarization import summarize_from_store, summarize_video
from vss.summarization.generators import GeneratedSummary, SummaryGenerator
class Generator(SummaryGenerator):
    async def generate(self, events, *, prompt=None, model=None):
        return GeneratedSummary(text="summary", model=model or "summary-model",
                                prompt_tokens=5, completion_tokens=7)
async def main():
    async with FileStorageAdapter(root=root) as storage:
        if phase == "summary":
            assert not pathlib.Path(source_path).exists()
            summary = await summarize_from_store(job_id, storage=storage,
                                                 generator=Generator())
            print(json.dumps({"content": json.loads(summary.choices[0].message.content),
                              "chunks": summary.usage.total_chunks_processed,
                              "tokens": summary.usage.aggregation_tokens}))
            return
        source = RequestSource(url=source_path, source_id="source-retained")
        options = {} if phase == "first" else {"job_id": UUID(job_id), "resume": True}
        job = ingest(source, backends=["vlm"], decoder="opencv", model="pinned-model",
                     pipeline_revision="process-test-v1", chunk_duration=1,
                     storage=storage, **options)
        try:
            chunks = (await job)["vlm"]
        except VLMBackendError:
            assert phase == "first"
            print(json.dumps({"job_id": str(job.id), "requests": seen}))
        else:
            assert phase == "resume"
            summary = await summarize_video(chunks, generator=Generator())
            print(json.dumps({"job_id": str(job.id), "requests": seen,
                              "source_ids": [c.chunk.sourceId for c in chunks],
                              "indices": [c.chunk.chunkIdx for c in chunks],
                              "content": json.loads(summary.choices[0].message.content),
                              "chunks": summary.usage.total_chunks_processed,
                              "tokens": summary.usage.aggregation_tokens}))
        finally:
            await job.wait_closed()
asyncio.run(main())
"""


async def test_three_process_resume_then_summary_without_media_or_ingestion_imports(
    tmp_path: Path,
) -> None:
    # Import first so this contract is visibly red until storage exists, before
    # creating child processes; no in-process state is shared with those children.
    import vss.storage  # noqa: F401

    source = tmp_path / "source.mp4"
    source.write_bytes(b"immutable bytes; decode is explicitly mocked")
    root = tmp_path / "durable"
    env = dict(os.environ)
    project_root = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = os.pathsep.join(
        str(path)
        for path in (
            project_root / "src",
            project_root / "packages/ingestion/src",
            project_root / "packages/summarization/src",
        )
    )
    env["VLM_BASE_URL"] = "https://mocked-vlm.test/v1"
    env.pop("VSS_STORAGE_BACKEND", None)

    async def run(phase: str, identity: str = "") -> dict[str, Any]:
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            PROCESS_PIPELINE,
            phase,
            str(root),
            str(source),
            identity,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=20)
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()
        assert child.returncode == 0, stderr.decode(errors="replace")
        return json.loads(stdout)

    first = await run("first")
    assert first["requests"] == [0, 1, 2]
    resumed = await run("resume", first["job_id"])
    assert resumed["job_id"] == first["job_id"]
    assert resumed["requests"] == [2]
    assert resumed["indices"] == [0, 1, 2]
    assert resumed["source_ids"] == ["source-retained"] * 3
    source.unlink()
    summary = await run("summary", first["job_id"])
    assert summary["content"] == resumed["content"]
    assert summary["chunks"] == resumed["chunks"] == 3
    assert summary["tokens"] == resumed["tokens"] == 12
