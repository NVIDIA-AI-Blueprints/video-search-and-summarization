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

"""Multi-backend shared-decode selection and cleanup tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import numpy as np
import pytest

import vss.ingestion.service as ingestion_service
import vss.ingestion.vlm as vlm_module
import vss.video_processing.shared_decode as shared_decode
from vss.ingestion import RequestSource, VlmChunkResponse, ingest
from vss.video_processing import Frame, MediaFacts
from vss.video_processing.shared_decode import SharedFrameCapability


def _frames(source_id: str) -> AsyncIterator[Frame]:
    async def generate() -> AsyncIterator[Frame]:
        for index in range(4):
            yield Frame(
                tensor=np.full((2, 3, 3), index, dtype=np.uint8),
                source_id=source_id,
                timestamp_ns=index * 100_000_000,
                index=index,
                timestamp_quality="pts",
                decoder="memory",
                device="cpu",
            )

    return generate()


async def test_compatible_backends_use_separate_buffers_from_one_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = RequestSource(source_id="shared-source")
    decode_calls = 0
    consumed: dict[str, list[tuple[int, int]]] = {}
    closed: set[str] = set()

    class FakeBackend:
        def __init__(self, name: str) -> None:
            self.name = name

        async def __aenter__(self) -> FakeBackend:
            return self

        async def __aexit__(self, *_: object) -> None:
            closed.add(self.name)

        def shared_frame_capability(self) -> SharedFrameCapability:
            return SharedFrameCapability(decoder="memory", max_buffer_depth=2)

        async def ingest_shared(
            self,
            _source: RequestSource,
            frame_buffer: Any,
            **_kwargs: object,
        ) -> list[VlmChunkResponse]:
            values: list[tuple[int, int]] = []
            async for lease in frame_buffer:
                values.append((lease.descriptor.sequence, id(lease.frame)))
                await lease.release()
            consumed[self.name] = values
            return [VlmChunkResponse()]

        async def ingest(self, *_: object, **__: object) -> Any:
            raise AssertionError("independent ingest must not run")

    def fake_decode(
        *_: object,
        facts_callback: Any = None,
        **__: object,
    ) -> AsyncIterator[Frame]:
        nonlocal decode_calls
        decode_calls += 1
        facts_callback(MediaFacts(source_id=source.source_id, duration_s=0.4))
        return _frames(source.source_id)

    monkeypatch.setenv("VSS_SHARED_DECODE_BUFFER_DEPTH", "2")
    vlm_type = vlm_module._VLM_BACKEND_REGISTRY["vlm"]
    monkeypatch.setitem(vlm_module._VLM_BACKEND_REGISTRY, "vlm_a", vlm_type)
    monkeypatch.setitem(vlm_module._VLM_BACKEND_REGISTRY, "vlm_b", vlm_type)
    monkeypatch.setattr(
        ingestion_service,
        "get_backend",
        lambda name, **_kwargs: FakeBackend(name),
    )
    monkeypatch.setattr(shared_decode, "_decode_stream", fake_decode)

    results = await ingest(
        source,
        backends=["vlm_a", "vlm_b"],
        decoder="memory",
        single_decode=True,
    )

    assert set(results) == {"vlm_a", "vlm_b"}
    assert decode_calls == 1
    assert [item[0] for item in consumed["vlm_a"]] == list(range(4))
    assert [item[0] for item in consumed["vlm_b"]] == list(range(4))
    assert [item[1] for item in consumed["vlm_a"]] == [
        item[1] for item in consumed["vlm_b"]
    ]
    assert closed == {"vlm_a", "vlm_b"}


async def test_required_shared_decode_rejects_incompatible_backends_before_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ingest_calls = 0
    closed: set[str] = set()

    class FakeBackend:
        def __init__(self, name: str) -> None:
            self.name = name

        async def __aenter__(self) -> FakeBackend:
            return self

        async def __aexit__(self, *_: object) -> None:
            closed.add(self.name)

        def shared_frame_capability(self) -> SharedFrameCapability:
            return SharedFrameCapability(decoder=self.name)

        async def ingest(self, *_: object, **__: object) -> Any:
            nonlocal ingest_calls
            ingest_calls += 1
            return []

    vlm_type = vlm_module._VLM_BACKEND_REGISTRY["vlm"]
    monkeypatch.setitem(vlm_module._VLM_BACKEND_REGISTRY, "vlm_a", vlm_type)
    monkeypatch.setitem(vlm_module._VLM_BACKEND_REGISTRY, "vlm_b", vlm_type)
    monkeypatch.setattr(
        ingestion_service,
        "get_backend",
        lambda name, **_kwargs: FakeBackend(name),
    )

    job = ingest(
        RequestSource(source_id="incompatible"),
        backends=["vlm_a", "vlm_b"],
        decoder="memory",
        single_decode=True,
    )
    with pytest.raises(
        ingestion_service.IngestionBackendError,
        match="compatible shared-frame",
    ):
        await job
    await job.wait_closed()

    assert ingest_calls == 0
    assert closed == {"vlm_a", "vlm_b"}


async def test_decoder_without_single_decode_runs_normal_backend_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []

    class FakeBackend:
        def __init__(self, name: str) -> None:
            self.name = name

        async def __aenter__(self) -> FakeBackend:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def shared_frame_capability(self) -> SharedFrameCapability:
            raise AssertionError(
                "ordinary ingestion must not resolve shared capability"
            )

        async def ingest(
            self,
            _source: RequestSource,
            **_kwargs: object,
        ) -> list[VlmChunkResponse]:
            called.append(self.name)
            return []

        async def ingest_shared(self, *_args: object, **_kwargs: object) -> Any:
            raise AssertionError("shared ingestion must be explicitly enabled")

    vlm_type = vlm_module._VLM_BACKEND_REGISTRY["vlm"]
    monkeypatch.setitem(vlm_module._VLM_BACKEND_REGISTRY, "vlm_a", vlm_type)
    monkeypatch.setitem(vlm_module._VLM_BACKEND_REGISTRY, "vlm_b", vlm_type)
    monkeypatch.setattr(
        ingestion_service,
        "get_backend",
        lambda name, **_kwargs: FakeBackend(name),
    )
    monkeypatch.setenv("VSS_SHARED_FRAME_TRANSPORT", "invalid")

    results = await ingest(
        RequestSource(source_id="independent-source"),
        backends=["vlm_a", "vlm_b"],
        decoder="memory",
    )

    assert set(results) == {"vlm_a", "vlm_b"}
    assert set(called) == {"vlm_a", "vlm_b"}


async def test_owned_cpu_consumer_honors_requested_decoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_decoder: str | None = None

    class FakeBackend:
        name = "vlm_a"

        async def __aenter__(self) -> FakeBackend:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def shared_frame_capability(self) -> SharedFrameCapability:
            return SharedFrameCapability(
                decoder="pynv",
                decoder_agnostic=True,
            )

        async def ingest_shared(
            self,
            _source: RequestSource,
            frame_buffer: Any,
            **_kwargs: object,
        ) -> list[VlmChunkResponse]:
            async for lease in frame_buffer:
                await lease.release()
            return [VlmChunkResponse()]

    def fake_decode(
        *_args: object,
        options: Any,
        facts_callback: Any,
        **_kwargs: object,
    ) -> AsyncIterator[Frame]:
        nonlocal observed_decoder
        observed_decoder = options.decoder
        facts_callback(MediaFacts(source_id="opencv-source"))
        return _frames("opencv-source")

    vlm_type = vlm_module._VLM_BACKEND_REGISTRY["vlm"]
    monkeypatch.setitem(vlm_module._VLM_BACKEND_REGISTRY, "vlm_a", vlm_type)
    monkeypatch.setattr(
        ingestion_service,
        "get_backend",
        lambda _name, **_kwargs: FakeBackend(),
    )
    monkeypatch.setattr(shared_decode, "_decode_stream", fake_decode)

    await ingest(
        RequestSource(source_id="opencv-source"),
        backends=["vlm_a"],
        decoder="opencv",
        single_decode=True,
        cuda_ipc=False,
    )

    assert observed_decoder == "opencv"


async def test_cuda_ipc_cancellation_stops_broker_before_joining_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vss.video_processing.cuda_ipc as cuda_ipc

    running = asyncio.Event()
    stopped = asyncio.Event()

    class FakeSession:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.closed = False

        async def prepare(self) -> None:
            return None

        def frame_source(self, consumer_id: str) -> str:
            return consumer_id

        async def run(self) -> None:
            running.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                # Model a native publication that cannot settle until stop()
                # releases its blocked broker slot.
                await stopped.wait()

        def stop(self) -> None:
            stopped.set()

        async def aclose(self, *, failed: bool) -> None:
            assert failed
            self.closed = True

    class FakeBackend:
        async def ingest_cuda_ipc(
            self,
            _source: RequestSource,
            _frame_source: object,
            ready: asyncio.Event,
            **_kwargs: object,
        ) -> list[VlmChunkResponse]:
            ready.set()
            await asyncio.Future()

    monkeypatch.setattr(cuda_ipc, "CudaIpcDecodeSession", FakeSession)
    task = asyncio.create_task(
        ingestion_service._run_cuda_ipc_ingestion(
            RequestSource(source_id="cancel-source"),
            ["rtvi"],
            [FakeBackend()],
            SharedFrameCapability(
                decoder="pynv",
                transport="cuda_ipc",
                pixel_format="nv12",
                device_id=0,
            ),
            prompt="Describe.",
            model=None,
            chunk_duration=60,
            chunk_overlap_duration=0,
            generation_config=None,
            buffer_depth=2,
        )
    )
    await asyncio.wait_for(running.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert stopped.is_set()


async def test_shared_consumer_failure_cancels_decode_and_sibling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = RequestSource(source_id="failure-source")
    sibling_cancelled = asyncio.Event()
    generator_closed = asyncio.Event()

    async def endless_frames() -> AsyncIterator[Frame]:
        index = 0
        try:
            while True:
                yield Frame(
                    tensor=np.zeros((2, 3, 3), dtype=np.uint8),
                    source_id=source.source_id,
                    timestamp_ns=index,
                    index=index,
                    timestamp_quality="pts",
                    decoder="memory",
                    device="cpu",
                )
                index += 1
                await asyncio.sleep(0)
        finally:
            generator_closed.set()

    class FakeBackend:
        def __init__(self, name: str) -> None:
            self.name = name

        async def __aenter__(self) -> FakeBackend:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def shared_frame_capability(self) -> SharedFrameCapability:
            return SharedFrameCapability(decoder="memory")

        async def ingest_shared(
            self,
            _source: RequestSource,
            frame_buffer: Any,
            **_kwargs: object,
        ) -> list[VlmChunkResponse]:
            if self.name == "vlm_a":
                lease = await frame_buffer.receive()
                await lease.release()
                raise RuntimeError("consumer failed")
            try:
                async for lease in frame_buffer:
                    await lease.release()
                return []
            finally:
                sibling_cancelled.set()

    vlm_type = vlm_module._VLM_BACKEND_REGISTRY["vlm"]
    monkeypatch.setitem(vlm_module._VLM_BACKEND_REGISTRY, "vlm_a", vlm_type)
    monkeypatch.setitem(vlm_module._VLM_BACKEND_REGISTRY, "vlm_b", vlm_type)
    monkeypatch.setattr(
        ingestion_service,
        "get_backend",
        lambda name, **_kwargs: FakeBackend(name),
    )

    def fake_decode(
        *_args: object,
        facts_callback: Any = None,
        **_kwargs: object,
    ) -> AsyncIterator[Frame]:
        facts_callback(MediaFacts(source_id=source.source_id))
        return endless_frames()

    monkeypatch.setattr(shared_decode, "_decode_stream", fake_decode)

    job = ingest(
        source,
        backends=["vlm_a", "vlm_b"],
        decoder="memory",
        single_decode=True,
    )
    with pytest.raises(RuntimeError, match="consumer failed"):
        await job
    await job.wait_closed()

    assert sibling_cancelled.is_set()
    assert generator_closed.is_set()
