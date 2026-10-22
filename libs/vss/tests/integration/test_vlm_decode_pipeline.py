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

"""Frame-level VLM processing across caller-selected decoders."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Any

import numpy as np
import pytest

import vss.ingestion.vlm as vlm_module
from vss.ingestion import (
    ChunkInfo,
    RequestSource,
    VlmChunkResponse,
    VLMIngestionBackend,
    ingest,
)
from vss.ingestion.vlm import VLMBackend, VLMFrameChunk
from vss.video_processing import (
    DecodeOptions,
    Frame,
    MediaFacts,
    SamplingPolicy,
)
from vss.video_processing.shared_decode import SharedDecodeCoordinator


@pytest.mark.parametrize(
    ("decoder", "device", "duration_s", "expected_chunks"),
    (
        ("opencv", "cpu", 31, [0, 1]),
        ("pynv", "gpu", 31, [0, 1]),
        ("pynv", "gpu", None, [0]),
    ),
)
async def test_vlm_pipeline_accepts_cpu_or_gpu_decoded_frames(
    decoder: str,
    device: str,
    duration_s: float | None,
    expected_chunks: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_decoders: list[str] = []
    inferred_devices: list[str] = []
    inferred_timestamps: list[int] = []

    class RecordingVLMBackend(VLMBackend):
        async def infer(
            self,
            chunk: VLMFrameChunk,
            *,
            prompt: str,
            model: str | None = None,
            generation_config: Mapping[str, Any] | None = None,
        ) -> VlmChunkResponse:
            del prompt, model, generation_config
            inferred_devices.extend(frame.device for frame in chunk.frames)
            inferred_timestamps.extend(frame.timestamp_ns for frame in chunk.frames)
            return VlmChunkResponse(
                chunk=ChunkInfo(
                    sourceId=chunk.source.source_id,
                    chunkIdx=chunk.chunk_idx,
                    start_pts=int(chunk.start_s * 1_000_000_000),
                    end_pts=int(chunk.end_s * 1_000_000_000),
                ),
                vlm_response="decoded frame accepted",
            )

        async def cancel(self, source_id: str) -> bool:
            del source_id
            return False

        async def check_health(self) -> bool:
            return True

        def capability_map(self) -> Mapping[str, Any]:
            return {"backend": "vlm"}

    source = RequestSource(source_id=f"{decoder}-video")

    async def fake_probe(*_: Any, **kwargs: Any) -> MediaFacts:
        probe_decoder = kwargs["options"].decoder
        selected_decoders.append(probe_decoder)
        return MediaFacts(source_id=source.source_id, duration_s=duration_s)

    async def frame_stream(policy: SamplingPolicy) -> AsyncIterator[Frame]:
        if duration_s is None:
            assert policy.mode == "all"
            timestamps_ns = tuple(index * 100_000_000 for index in range(20))
        else:
            assert policy.mode == "timestamps"
            assert policy.timestamps_ns is not None
            timestamps_ns = (policy.timestamps_ns[0], policy.timestamps_ns[-1])
        for index, timestamp_ns in enumerate(timestamps_ns):
            yield Frame(
                tensor=np.zeros((4, 4, 3), dtype=np.uint8),
                timestamp_ns=timestamp_ns,
                requested_timestamp_ns=timestamp_ns,
                source_id=source.source_id,
                index=index,
                timestamp_quality="pts",
                decoder=decoder,
                device=device,  # pyright: ignore[reportArgumentType]
            )

    def fake_subsample(
        _source: RequestSource,
        policy: SamplingPolicy,
        **kwargs: Any,
    ) -> AsyncIterator[Frame]:
        selected_decoders.append(kwargs["options"].decoder)
        return frame_stream(policy)

    monkeypatch.setitem(
        vlm_module._VLM_BACKEND_REGISTRY,
        "vlm",
        RecordingVLMBackend,
    )
    monkeypatch.setattr(vlm_module, "probe_video", fake_probe)
    monkeypatch.setattr(vlm_module, "subsample_frames", fake_subsample)

    backend = "vlm"
    responses = (
        await ingest(
            source,
            backends=[backend],
            decoder=decoder,
            chunk_duration=30,
        )
    )[backend]

    assert selected_decoders == [decoder, decoder]
    assert inferred_devices == (
        [device, device] if duration_s is not None else [device] * 8
    )
    chunks = []
    for response in responses:
        assert response.chunk is not None
        chunks.append(response.chunk)
    assert [chunk.chunkIdx for chunk in chunks] == expected_chunks
    if duration_s is None:
        assert len(inferred_timestamps) == 8
        assert inferred_timestamps[0] == 0
        assert inferred_timestamps[-1] == 1_900_000_000
        assert (
            max(
                later - earlier
                for earlier, later in zip(
                    inferred_timestamps, inferred_timestamps[1:], strict=False
                )
            )
            <= 400_000_000
        )
    assert chunks[-1].is_last


async def test_vfr_gap_groups_selected_frame_by_actual_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = RequestSource(source_id="vfr-video")
    inferred_chunks: list[VLMFrameChunk] = []

    class RecordingVLMBackend(VLMBackend):
        async def infer(
            self,
            chunk: VLMFrameChunk,
            *,
            prompt: str,
            model: str | None = None,
            generation_config: Mapping[str, Any] | None = None,
        ) -> VlmChunkResponse:
            del prompt, model, generation_config
            inferred_chunks.append(chunk)
            return VlmChunkResponse(
                chunk=ChunkInfo(
                    sourceId=chunk.source.source_id,
                    chunkIdx=chunk.chunk_idx,
                ),
                vlm_response="recorded",
            )

        async def cancel(self, source_id: str) -> bool:
            del source_id
            return False

        async def check_health(self) -> bool:
            return True

        def capability_map(self) -> Mapping[str, Any]:
            return {"backend": "vlm"}

    async def fake_probe(*_: Any, **__: Any) -> MediaFacts:
        return MediaFacts(source_id=source.source_id, duration_s=2)

    async def frame_stream(policy: SamplingPolicy) -> AsyncIterator[Frame]:
        assert policy.allow_missing_timestamps
        yield Frame(
            tensor=np.zeros((4, 4, 3), dtype=np.uint8),
            timestamp_ns=1_200_000_000,
            requested_timestamp_ns=875_000_000,
            source_id=source.source_id,
            index=0,
            timestamp_quality="pts",
            decoder="opencv",
            device="cpu",
        )

    monkeypatch.setitem(
        vlm_module._VLM_BACKEND_REGISTRY,
        "vlm",
        RecordingVLMBackend,
    )
    monkeypatch.setattr(vlm_module, "probe_video", fake_probe)
    monkeypatch.setattr(
        vlm_module,
        "subsample_frames",
        lambda _source, policy, **_kwargs: frame_stream(policy),
    )

    await ingest(
        source,
        backends=["vlm"],
        chunk_duration=1,
    )

    assert len(inferred_chunks) == 1
    assert inferred_chunks[0].chunk_idx == 1
    assert inferred_chunks[0].start_s == 1
    assert inferred_chunks[0].end_s == 2
    assert inferred_chunks[0].frames[0].timestamp_ns == 1_200_000_000


async def test_local_vlm_consumes_shared_buffer_with_independent_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vss.video_processing.shared_decode as shared_decode

    source = RequestSource(source_id="shared-vlm")
    inferred_chunks: list[VLMFrameChunk] = []

    class RecordingVLMBackend(VLMBackend):
        async def infer(
            self,
            chunk: VLMFrameChunk,
            *,
            prompt: str,
            model: str | None = None,
            generation_config: Mapping[str, Any] | None = None,
        ) -> VlmChunkResponse:
            del prompt, model, generation_config
            inferred_chunks.append(chunk)
            return VlmChunkResponse(
                chunk=ChunkInfo(
                    sourceId=chunk.source.source_id,
                    chunkIdx=chunk.chunk_idx,
                    start_pts=round(chunk.start_s * 1_000_000_000),
                    end_pts=round(chunk.end_s * 1_000_000_000),
                ),
                vlm_response="shared frames accepted",
            )

        async def cancel(self, source_id: str) -> bool:
            del source_id
            return False

        async def check_health(self) -> bool:
            return True

        def capability_map(self) -> Mapping[str, Any]:
            return {"backend": "recording"}

    async def frames() -> AsyncIterator[Frame]:
        for index in range(20):
            yield Frame(
                tensor=np.zeros((4, 4, 3), dtype=np.uint8),
                timestamp_ns=index * 100_000_000,
                source_id=source.source_id,
                index=index,
                timestamp_quality="pts",
                decoder="opencv",
                device="cpu",
            )

    monkeypatch.setitem(
        vlm_module._VLM_BACKEND_REGISTRY,
        "vlm",
        RecordingVLMBackend,
    )

    def shared_stream(
        *_args: object,
        facts_callback: Any = None,
        **_kwargs: object,
    ) -> AsyncIterator[Frame]:
        facts_callback(MediaFacts(source_id=source.source_id, duration_s=2))
        return frames()

    monkeypatch.setattr(shared_decode, "_decode_stream", shared_stream)
    coordinator = SharedDecodeCoordinator(
        source,
        options=DecodeOptions(decoder="opencv"),
        buffer_depth=2,
    )
    buffer = await coordinator.register("vlm")

    async with VLMIngestionBackend(decoder="opencv") as backend:
        responses, _ = await asyncio.gather(
            backend.ingest_shared(
                source,
                buffer,
                prompt="Describe.",
                chunk_duration=1,
            ),
            coordinator.run(),
        )
    await coordinator.aclose()

    assert [chunk.chunk_idx for chunk in inferred_chunks] == [0, 1]
    assert [len(chunk.frames) for chunk in inferred_chunks] == [8, 8]
    assert inferred_chunks[-1].end_s == 2
    assert [response.chunk.chunkIdx for response in responses if response.chunk] == [
        0,
        1,
    ]
    assert responses[-1].chunk is not None
    assert responses[-1].chunk.is_last


async def test_multiple_registered_vlm_backends_share_one_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vss.video_processing.shared_decode as shared_decode

    source = RequestSource(source_id="multi-vlm")
    decoded = 0
    inferred: dict[str, list[int]] = {"primary_vlm": [], "secondary_vlm": []}

    class RecordingBackend(VLMBackend):
        provider = ""

        async def infer(
            self,
            chunk: VLMFrameChunk,
            *,
            prompt: str,
            model: str | None = None,
            generation_config: Mapping[str, Any] | None = None,
        ) -> VlmChunkResponse:
            del prompt, model, generation_config
            inferred[self.provider].extend(frame.timestamp_ns for frame in chunk.frames)
            return VlmChunkResponse(
                chunk=ChunkInfo(
                    sourceId=chunk.source.source_id,
                    chunkIdx=chunk.chunk_idx,
                ),
                vlm_response=self.provider,
            )

        async def cancel(self, source_id: str) -> bool:
            del source_id
            return False

        async def check_health(self) -> bool:
            return True

        def capability_map(self) -> Mapping[str, Any]:
            return {"backend": self.provider}

    class PrimaryBackend(RecordingBackend):
        provider = "primary_vlm"

    class SecondaryBackend(RecordingBackend):
        provider = "secondary_vlm"

    async def frames() -> AsyncIterator[Frame]:
        nonlocal decoded
        decoded += 1
        for index in range(20):
            yield Frame(
                tensor=np.zeros((4, 4, 3), dtype=np.uint8),
                timestamp_ns=index * 100_000_000,
                source_id=source.source_id,
                index=index,
                timestamp_quality="pts",
                decoder="opencv",
                device="cpu",
            )

    monkeypatch.setitem(
        vlm_module._VLM_BACKEND_REGISTRY,
        "primary_vlm",
        PrimaryBackend,
    )
    monkeypatch.setitem(
        vlm_module._VLM_BACKEND_REGISTRY,
        "secondary_vlm",
        SecondaryBackend,
    )
    monkeypatch.setenv("VSS_PROCESSING_MAX_CONCURRENCY", "1")

    def shared_stream(
        *_args: object,
        facts_callback: Any = None,
        **_kwargs: object,
    ) -> AsyncIterator[Frame]:
        facts_callback(MediaFacts(source_id=source.source_id, duration_s=2))
        return frames()

    monkeypatch.setattr(shared_decode, "_decode_stream", shared_stream)

    job = ingest(
        source,
        backends=["primary_vlm", "secondary_vlm"],
        decoder="opencv",
        single_decode=True,
        chunk_duration=1,
    )
    results = await asyncio.wait_for(job, timeout=1)
    await job.wait_closed()

    assert decoded == 1
    assert set(results) == {"primary_vlm", "secondary_vlm"}
    assert results["primary_vlm"][0].vlm_response == "primary_vlm"
    assert results["secondary_vlm"][0].vlm_response == "secondary_vlm"
    assert inferred["primary_vlm"] == inferred["secondary_vlm"]
