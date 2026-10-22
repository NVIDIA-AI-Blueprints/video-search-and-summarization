# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import asyncio
import json
import threading
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx
import numpy as np
import pytest

import vss.ingestion.vlm as vlm_module
from vss.ingestion import (
    ChunkInfo,
    RequestSource,
    VLMBackendError,
    VlmChunkResponse,
)
from vss.ingestion.vlm import (
    OpenAICompatibleVLMBackend,
    VLMBackend,
    VLMFrameChunk,
    _process_video,
    available_vlm_backends,
)
from vss.video_processing import Frame, MediaFacts


@pytest.fixture(autouse=True)
def mock_frame_encoding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep VLM unit tests independent of the optional OpenCV codec."""

    monkeypatch.setattr(
        Frame,
        "to_base64",
        lambda _frame, *, format="jpeg": f"encoded-{format}",
    )


def _frames() -> tuple[Frame, ...]:
    return (
        Frame(
            tensor=np.zeros((4, 4, 3), dtype=np.uint8),
            timestamp_ns=5_000_000_000,
            source_id="video-1",
            index=0,
            timestamp_quality="pts",
            decoder="opencv",
            device="cpu",
        ),
        Frame(
            tensor=np.full((4, 4, 3), 255, dtype=np.uint8),
            timestamp_ns=15_000_000_000,
            source_id="video-1",
            index=1,
            timestamp_quality="pts",
            decoder="opencv",
            device="cpu",
        ),
    )


def _chunk() -> VLMFrameChunk:
    return VLMFrameChunk(
        source=RequestSource(source_id="video-1"),
        chunk_idx=0,
        start_s=0,
        end_s=30,
        frames=_frames(),
    )


async def _run_process_video(*args: Any, **kwargs: Any) -> list[VlmChunkResponse]:
    async with vlm_module._processing_limiter():
        return await _process_video(*args, **kwargs)


def test_frame_inputs_are_encoded_for_vlm() -> None:
    chunk = _chunk()

    assert chunk.duration_s == 30
    assert chunk.frames[0].to_base64() == "encoded-jpeg"


def test_video_processing_has_no_public_whole_video_vlm_api() -> None:
    assert not hasattr(vlm_module, "process_video")
    assert not hasattr(vlm_module, "process_video_sync")


def test_public_vlm_frame_chunk_validates_window_membership() -> None:
    source = RequestSource(source_id="video-1")
    with pytest.raises(ValueError, match="inside the chunk window"):
        VLMFrameChunk(
            source=source,
            chunk_idx=0,
            start_s=0,
            end_s=1,
            frames=(_frames()[0],),
        )
    with pytest.raises(ValueError, match="0 <= start < end"):
        VLMFrameChunk(
            source=source,
            chunk_idx=0,
            start_s=1,
            end_s=1,
            frames=(),
        )


def test_vlm_sampling_includes_partial_final_chunk() -> None:
    targets = vlm_module._chunk_sample_timestamps_ns(31, 30)

    assert targets[:8] == (
        0,
        3_750_000_000,
        7_500_000_000,
        11_250_000_000,
        15_000_000_000,
        18_750_000_000,
        22_500_000_000,
        26_250_000_000,
    )
    assert targets[8:] == (
        30_000_000_000,
        30_125_000_000,
        30_250_000_000,
        30_375_000_000,
        30_500_000_000,
        30_625_000_000,
        30_750_000_000,
        30_875_000_000,
    )


async def test_vlm_backend_sends_timestamped_multimodal_request() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "A person walks through the scene."}}
                ],
                "usage": {"prompt_tokens": 42, "completion_tokens": 8},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = OpenAICompatibleVLMBackend(
        base_url="https://vlm.test/v1",
        api_key="secret",
        model="provider/model",
        client=client,
    )

    response = await backend.infer(
        _chunk(),
        prompt="Describe visible events.",
        generation_config={"max_tokens": 128, "temperature": 0},
    )

    assert isinstance(backend, VLMBackend)
    assert captured["url"] == "https://vlm.test/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer secret"
    assert captured["body"]["model"] == "provider/model"
    assert captured["body"]["max_tokens"] == 128
    user_content = captured["body"]["messages"][1]["content"]
    assert "contains 2 sampled frames" in user_content[0]["text"]
    assert user_content[1]["text"] == "Frame timestamp: 5.000 seconds"
    assert user_content[2]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert response.vlm_response == "A person walks through the scene."
    assert response.chunk is not None
    assert response.chunk.start_pts == 0
    assert response.chunk.end_pts == 30_000_000_000
    assert response.rtvi_frame_count == 2
    assert response.vlm_stats == {"input_tokens": 42, "output_tokens": 8}
    await client.aclose()


async def test_vlm_backend_reports_provider_errors() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid key"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = OpenAICompatibleVLMBackend(
        model="provider/model",
        client=client,
    )

    with pytest.raises(VLMBackendError, match="HTTP 401: invalid key"):
        await backend.infer(_chunk(), prompt="Describe.")
    with pytest.raises(ValueError, match="reserved fields"):
        await backend.infer(
            _chunk(),
            prompt="Describe.",
            generation_config={"model": "override"},
        )
    with pytest.raises(ValueError, match="stream"):
        await backend.infer(
            _chunk(),
            prompt="Describe.",
            generation_config={"stream": True},
        )
    await client.aclose()


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(200, True), (204, True), (301, False), (401, False), (429, False), (500, False)],
)
async def test_vlm_backend_health_requires_success_response(
    status_code: int, expected: bool
) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status_code, json={"data": []})
        )
    )
    backend = OpenAICompatibleVLMBackend(client=client)

    assert await backend.check_health() is expected

    await client.aclose()


async def test_frame_processing_uses_registry_and_decoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[int, str | None]] = []
    selected_decoders: list[str] = []

    class FakeVLMBackend(VLMBackend):
        async def infer(
            self,
            chunk: VLMFrameChunk,
            *,
            prompt: str,
            model: str | None = None,
            generation_config: Mapping[str, Any] | None = None,
        ) -> VlmChunkResponse:
            requests.append((chunk.chunk_idx, model))
            return VlmChunkResponse(
                chunk=ChunkInfo(
                    sourceId=chunk.source.source_id,
                    chunkIdx=chunk.chunk_idx,
                ),
                vlm_response=prompt,
            )

        async def cancel(self, source_id: str) -> bool:
            return False

        async def check_health(self) -> bool:
            return True

        def capability_map(self) -> dict[str, Any]:
            return {"backend": "fake"}

    monkeypatch.setitem(vlm_module._VLM_BACKEND_REGISTRY, "fake", FakeVLMBackend)
    source = RequestSource(source_id="video-1")
    frames = (
        *_frames(),
        Frame(
            tensor=np.full((4, 4, 3), 127, dtype=np.uint8),
            timestamp_ns=30_500_000_000,
            source_id="video-1",
            index=2,
            timestamp_quality="pts",
            decoder="opencv",
            device="cpu",
            requested_timestamp_ns=30_500_000_000,
        ),
    )

    async def fake_probe(*_: Any, **kwargs: Any) -> MediaFacts:
        selected_decoders.append(kwargs["options"].decoder)
        return MediaFacts(source_id=source.source_id, duration_s=31)

    async def frame_stream() -> AsyncIterator[Frame]:
        for frame in frames:
            yield frame

    monkeypatch.setattr(vlm_module, "probe_video", fake_probe)

    def fake_subsample(*_args: Any, **kwargs: Any) -> AsyncIterator[Frame]:
        selected_decoders.append(kwargs["options"].decoder)
        return frame_stream()

    monkeypatch.setattr(vlm_module, "subsample_frames", fake_subsample)

    responses = await _run_process_video(
        source,
        chunk_duration=30,
        prompt="Describe.",
        model="test-model",
        decoder="memory",
        backend="fake",
    )

    assert available_vlm_backends() == ("fake", "vlm")
    assert selected_decoders == ["memory", "memory"]
    assert requests == [(0, "test-model"), (1, "test-model")]
    assert responses[-1].chunk is not None
    assert responses[-1].chunk.is_last


async def test_private_video_processing_bounds_concurrent_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    max_active = 0
    probe_count = 0
    inference_started = asyncio.Event()
    release = asyncio.Event()

    class BlockingVLMBackend(VLMBackend):
        async def infer(
            self,
            chunk: VLMFrameChunk,
            *,
            prompt: str,
            model: str | None = None,
            generation_config: Mapping[str, Any] | None = None,
        ) -> VlmChunkResponse:
            nonlocal active, max_active
            del prompt, model, generation_config
            active += 1
            max_active = max(max_active, active)
            inference_started.set()
            await release.wait()
            active -= 1
            return VlmChunkResponse(
                chunk=ChunkInfo(
                    sourceId=chunk.source.source_id,
                    chunkIdx=chunk.chunk_idx,
                ),
                vlm_response="done",
            )

        async def cancel(self, source_id: str) -> bool:
            del source_id
            return False

        async def check_health(self) -> bool:
            return True

        def capability_map(self) -> dict[str, Any]:
            return {"backend": "blocking"}

    async def fake_probe(source: RequestSource, **_: Any) -> MediaFacts:
        nonlocal probe_count
        probe_count += 1
        return MediaFacts(source_id=source.source_id, duration_s=1)

    def fake_subsample(
        source: RequestSource,
        *_: Any,
        **__: Any,
    ) -> AsyncIterator[Frame]:
        async def stream() -> AsyncIterator[Frame]:
            yield Frame(
                tensor=np.zeros((4, 4, 3), dtype=np.uint8),
                timestamp_ns=0,
                source_id=source.source_id,
                index=0,
                timestamp_quality="pts",
                decoder="memory",
                device="cpu",
            )

        return stream()

    monkeypatch.setenv("VSS_PROCESSING_MAX_CONCURRENCY", "1")
    monkeypatch.setitem(
        vlm_module._VLM_BACKEND_REGISTRY,
        "blocking",
        BlockingVLMBackend,
    )
    monkeypatch.setattr(vlm_module, "probe_video", fake_probe)
    monkeypatch.setattr(vlm_module, "subsample_frames", fake_subsample)

    first = asyncio.create_task(
        _run_process_video(
            RequestSource(source_id="video-0"),
            backend="blocking",
            decoder="memory",
        )
    )
    await inference_started.wait()
    monkeypatch.setenv("VSS_PROCESSING_MAX_CONCURRENCY", "2")
    second = asyncio.create_task(
        _run_process_video(
            RequestSource(source_id="video-1"),
            backend="blocking",
            decoder="memory",
        )
    )
    await asyncio.sleep(0)

    assert probe_count == 1
    assert max_active == 1

    release.set()
    await asyncio.gather(first, second)

    assert probe_count == 2
    assert max_active == 1


async def test_vlm_rejects_invalid_chunk_duration_before_decode() -> None:
    source = RequestSource(source_id="video-1")

    with pytest.raises(ValueError, match="between 1 and 3600"):
        await _run_process_video(source, chunk_duration=0)


async def test_vlm_backend_can_cancel_source() -> None:
    started = asyncio.Event()

    async def handler(_: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend = OpenAICompatibleVLMBackend(model="provider/model", client=client)
    task = asyncio.create_task(backend.infer(_chunk(), prompt="Describe."))
    await started.wait()

    assert await backend.cancel("video-1")
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not await backend.cancel("video-1")
    await client.aclose()


async def test_vlm_encoding_is_bounded_and_does_not_block_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoding_started = threading.Event()
    release_encoding = threading.Event()
    active_encoders = 0
    max_active_encoders = 0
    state_lock = threading.Lock()

    def blocking_encode(_frame: Frame, *, format: str = "jpeg") -> str:
        nonlocal active_encoders, max_active_encoders
        assert format == "jpeg"
        with state_lock:
            active_encoders += 1
            max_active_encoders = max(max_active_encoders, active_encoders)
        encoding_started.set()
        release_encoding.wait(timeout=2)
        with state_lock:
            active_encoders -= 1
        return "encoded"

    monkeypatch.setattr(Frame, "to_base64", blocking_encode)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200))
    )
    backend = OpenAICompatibleVLMBackend(
        model="provider/model",
        encoding_concurrency=1,
        client=client,
    )

    task = asyncio.create_task(backend.infer(_chunk(), prompt="Describe."))
    assert await asyncio.to_thread(encoding_started.wait, 1)
    heartbeat = asyncio.Event()
    asyncio.get_running_loop().call_soon(heartbeat.set)
    await asyncio.wait_for(heartbeat.wait(), timeout=0.1)
    assert await backend.cancel("video-1")
    release_encoding.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert max_active_encoders == 1
    await client.aclose()
