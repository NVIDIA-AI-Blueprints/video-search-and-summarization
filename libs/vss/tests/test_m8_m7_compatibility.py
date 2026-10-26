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

"""M8's public-ingestion boundary and fanout evidence against M7's API."""

import asyncio
import hashlib
from contextlib import AsyncExitStack
from types import SimpleNamespace

import numpy as np
import pytest

import vss.ingestion.backend as backend_module
import vss.video_processing.shared_decode as shared
from benchmarks.ingestion import caption_result
from benchmarks.workflow.sampling import prepare
from vss.ingestion import IngestionQueue, RequestSource, VlmChunkResponse, ingest
from vss.ingestion.backend import IngestionBackend, get_backend
from vss.observability import collect_vss_metrics
from vss.video_processing import Frame, MediaFacts


@pytest.mark.parametrize("suppress", [False, True])
async def test_backend_context_preserves_exit_suppression_and_exception_arguments(
    suppress,
):
    from vss.ingestion.service import _ObservedBackendContext

    exits = []
    failure = RuntimeError("cleanup failure")

    class Context:
        def __init__(self, name):
            self.name = name

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            exits.append((self.name, exc))
            if self.name == "B":
                raise failure
            return suppress

    async def run():
        async with AsyncExitStack() as stack:
            for name in ("A", "B"):
                context = Context(name)
                assert (
                    await stack.enter_async_context(
                        _ObservedBackendContext(
                            context, RequestSource(source_id="source"), name, None
                        )
                    )
                    is context
                )

    if suppress:
        await run()
    else:
        with pytest.raises(RuntimeError) as error:
            await run()
        assert error.value is failure
    assert [name for name, _ in exits] == ["B", "A"]
    assert exits[0][1] == (None, None, None)
    assert exits[1][1][0] is RuntimeError
    assert exits[1][1][1] is failure
    assert exits[1][1][2] is not None


@pytest.mark.parametrize(
    "result",
    [
        [],
        {},
        {"wrong": []},
        {"caption": [], "extra": []},
        {"caption": {}},
        {"caption": [object()]},
    ],
)
def test_caption_boundary_rejects_wrong_backend_or_output(result):
    with pytest.raises(ValueError, match="caption_backend_result"):
        caption_result(result, "caption")


def test_caption_boundary_preserves_selected_responses():
    chunks = [VlmChunkResponse()]
    assert caption_result({"caption": chunks}, "caption") is chunks


@pytest.mark.parametrize("captioning", ["cpu_vlm", "gpu_vlm"])
def test_sampling_adapter_selects_its_registered_decoder(
    tmp_path, monkeypatch, captioning
):
    import vss.video_processing.decoders.registry as registry

    monkeypatch.setattr(
        registry, "_DECODER_REGISTRY", registry._DECODER_REGISTRY.copy()
    )
    monkeypatch.setattr(
        backend_module, "_BACKEND_REGISTRY", backend_module._BACKEND_REGISTRY.copy()
    )
    video = tmp_path / "source.mp4"
    video.write_bytes(b"admitted source")
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    profile = SimpleNamespace(
        sampling_contract="shared_duration_grid.v1",
        captioning=captioning,
        video=video,
        video_sha256=digest,
        duration_s=1.0,
    )
    admission = {
        "source_sha256": digest,
        "selected_stream": "v:0",
        "duration_quality": "video",
        "oracle_sha256": "a" * 64,
        "duration_s": 1.0,
        "width": 4,
        "height": 2,
    }
    selected, _ = prepare(profile, admission)
    instance = get_backend(selected)
    assert instance.decoder == selected == "m8_shared_" + captioning
    assert get_backend(captioning).decoder == (
        "opencv" if captioning == "cpu_vlm" else "pynv"
    )


@pytest.mark.parametrize("shared_decode", [False, True])
@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
async def test_fanout_scopes_and_settlement(monkeypatch, shared_decode, outcome):
    closed, consumed, coordinators = [], {}, []
    entered = set()
    ready = asyncio.Event()
    buffers = []
    calls = 0

    class Backend(IngestionBackend):
        def __init__(self):
            self.name = type(self).__name__

        async def aclose(self):
            closed.append(self.name)
            await asyncio.sleep(0)

        async def invoke(self):
            entered.add(self.name)
            if len(entered) == 2:
                ready.set()
            await ready.wait()
            if outcome == "failure" and self.name == "ConsumerA":
                raise ValueError("peer_failure")
            if outcome != "success":
                await asyncio.Event().wait()
            return [VlmChunkResponse(vlm_response=self.name)]

        async def ingest(self, *args, **kwargs):
            return await self.invoke()

        async def ingest_shared(self, source, buffer, **kwargs):
            buffers.append(buffer)
            frames = []
            async for lease in buffer:
                frames.append((lease.descriptor.sequence, id(lease.frame)))
                await lease.release()
            consumed[self.name] = frames
            return await self.invoke()

        def shared_frame_capability(self):
            return shared.SharedFrameCapability(decoder="memory", max_buffer_depth=2)

        async def cancel(self, source_id):
            return False

        async def check_health(self):
            return True

        def capability_map(self):
            return {}

    for name in ("ConsumerA", "ConsumerB"):
        monkeypatch.setitem(
            backend_module._BACKEND_REGISTRY, name, type(name, (Backend,), {})
        )

    async def decode(*args, source_id=None, facts_callback=None, **kwargs):
        nonlocal calls
        calls += 1
        facts_callback(MediaFacts(source_id=source_id, duration_s=0.3))
        for index in range(3):
            yield Frame(
                tensor=np.zeros((2, 3, 3), dtype=np.uint8),
                source_id=source_id,
                timestamp_ns=index * 100_000_000,
                index=index,
                timestamp_quality="pts",
                decoder="memory",
                device="cpu",
            )

    from vss.ingestion import service

    original = service.SharedDecodeCoordinator

    def coordinator(*args, **kwargs):
        value = original(*args, **kwargs)
        coordinators.append(value)
        return value

    monkeypatch.setattr(shared, "_decode_stream", decode)
    monkeypatch.setattr(service, "SharedDecodeCoordinator", coordinator)
    queue = IngestionQueue(max_concurrency=1)
    with collect_vss_metrics() as metrics:
        job = ingest(
            RequestSource(source_id="private-source"),
            backends=["ConsumerA", "ConsumerB"],
            queue=queue,
            decoder="memory",
            single_decode=shared_decode,
            cuda_ipc=False,
        )
        if outcome == "success":
            assert set(await job) == {"ConsumerA", "ConsumerB"}
        elif outcome == "failure":
            with pytest.raises(ValueError, match="peer_failure"):
                await job
        else:
            await asyncio.wait_for(ready.wait(), 2)
            job.cancel()
            with pytest.raises(asyncio.CancelledError):
                await job
        await job.wait_closed()
    assert metrics.complete
    assert sorted(closed) == ["ConsumerA", "ConsumerB"]
    assert queue.running_count == queue.pending_count == 0
    observations = [e for e in metrics.observations if e.name == "ingestion.infer"]
    assert len(observations) == 2
    assert {e.backend for e in observations} == {
        metrics.identity("ConsumerA"),
        metrics.identity("ConsumerB"),
    }
    assert {e.request_id for e in observations} == {job.id.hex}
    assert {e.source_id for e in observations} == {
        metrics.source_identity("private-source")
    }
    assert len({e.span_id for e in observations}) == 2
    cleanup = [e for e in metrics.observations if e.name == "ingestion.cleanup"]
    assert len(cleanup) == 2
    assert [e.backend for e in cleanup] == [
        metrics.identity("ConsumerB"),
        metrics.identity("ConsumerA"),
    ]
    assert {e.pipeline for e in cleanup} == {e.pipeline for e in observations}
    assert {e.request_id for e in cleanup} == {job.id.hex}
    assert {e.source_id for e in cleanup} == {metrics.source_identity("private-source")}
    if outcome == "success":
        assert {e.status for e in observations} == {"ok"}
    else:
        assert any(
            e.status == ("error" if outcome == "failure" else "cancelled")
            for e in observations
        )
    if shared_decode:
        assert calls == 1 and consumed["ConsumerA"] == consumed["ConsumerB"]
        assert all(buffer.closed and buffer.duration_s == 0.3 for buffer in buffers)
        assert coordinators[0].outstanding_lease_frames == 0
    else:
        assert calls == 0


async def test_concurrent_public_jobs_preserve_request_and_upload_lineage(monkeypatch):
    from vss.ingestion import RTVIBackend

    released = []

    class Transport:
        async def prepare_source(self, source):
            await asyncio.sleep(0)
            return RequestSource(source_id=source.source_id + "-uploaded")

        async def release_source(self, source):
            released.append(source.source_id)

        async def generate_captions_stream(self, **kwargs):
            await asyncio.sleep(0)
            yield {
                "chunk_responses": [
                    {
                        "chunk_id": 0,
                        "start_time": "0",
                        "end_time": "1",
                        "content": "private caption",
                        "input_tokens": 1,
                        "output_tokens": 2,
                    }
                ]
            }

    class Registered(RTVIBackend):
        def __init__(self):
            super().__init__(transport=Transport())

    monkeypatch.setitem(backend_module._BACKEND_REGISTRY, "CaptionPlugin", Registered)
    queue = IngestionQueue(max_concurrency=2)
    with collect_vss_metrics() as metrics:
        jobs = [
            ingest(
                RequestSource(source_id=source),
                backends=["CaptionPlugin"],
                model="test-model",
                queue=queue,
            )
            for source in ("first", "second")
        ]
        results = await asyncio.gather(*jobs)
        await asyncio.gather(*(job.wait_closed() for job in jobs))
    assert metrics.complete
    assert sorted(released) == ["first-uploaded", "second-uploaded"]
    for source, job, result in zip(("first", "second"), jobs, results, strict=True):
        assert (
            caption_result(result, "CaptionPlugin")[0].chunk.sourceId
            == source + "-uploaded"
        )
        own = [e for e in metrics.observations if e.request_id == job.id.hex]
        assert own and {e.source_id for e in own} == {metrics.source_identity(source)}
        assert len([e for e in own if e.name == "ingestion.infer"]) == 1
        assert [e.value for e in own if e.name == "ingestion.total_input_tokens"] == [1]
