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

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest

from vss.ingestion import (
    EmbeddingChunkResponse,
    IngestionBackend,
    RequestSource,
    RTEmbedBackend,
    RTEmbedBackendError,
)
from vss.ingestion.rtvi import _RTVIEmbedTransport, _RTVITransportError
from vss.video_processing import Frame
from vss.video_processing.cuda_ipc import CudaIpcFrameSource
from vss.video_processing.shared_decode import SharedFrameCapability

SOURCE_ID = "123e4567-e89b-12d3-a456-426614174000"


async def test_rtvi_embed_transport_uses_independent_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VLM_BASE_URL", "https://caption.example.test")
    monkeypatch.setenv("RTVI_EMBED_BASE_URL", "https://embed.example.test")
    monkeypatch.setenv("RTVI_EMBED_MODEL", "embed-model")
    monkeypatch.setenv("RTVI_EMBED_API_KEY", "embed-key")

    transport = _RTVIEmbedTransport()

    assert str(transport._client.base_url).rstrip("/") == "https://embed.example.test"
    assert (await transport.get_model_info()).id == "embed-model"
    assert transport._headers == {"Authorization": "Bearer embed-key"}

    await transport.aclose()


async def test_rtvi_embed_transport_uses_embed_health_endpoint() -> None:
    requested_paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(200, text="Service is healthy")

    client = httpx.AsyncClient(
        base_url="http://embed.test",
        transport=httpx.MockTransport(handler),
    )
    transport = _RTVIEmbedTransport(client=client)

    assert await transport.check_health()
    assert requested_paths == ["/v1/ready"]

    await client.aclose()


async def test_rtvi_embed_backend_returns_validated_embedding_chunks() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        events = (
            'data: {"id":"123e4567-e89b-12d3-a456-426614174000",'
            '"model":"cosmos-embed1-448p","created":1,'
            '"media_info":{"type":"offset"},'
            '"chunk_responses":[{"start_time":"10.0","end_time":"20.0",'
            '"embeddings":[0.3,0.4],"decode_latency_ms":100,'
            '"inference_latency_ms":200,"chunk_latency_ms":350,'
            '"queue_time_s":0.05,"processing_latency_s":0.3,'
            '"frame_count":8}]}\n\n'
            'data: {"id":"123e4567-e89b-12d3-a456-426614174000",'
            '"model":"cosmos-embed1-448p","created":1,'
            '"media_info":{"type":"offset"},'
            '"chunk_responses":[{"start_time":"0.0","end_time":"10.0",'
            '"embeddings":[0.1,0.2],"frame_count":8}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=events,
        )

    client = httpx.AsyncClient(
        base_url="http://embed.test",
        transport=httpx.MockTransport(handler),
    )
    backend = RTEmbedBackend(_RTVIEmbedTransport(client=client))

    responses = await backend.ingest(
        RequestSource(source_id=SOURCE_ID),
        prompt="unused",
        model="cosmos-embed1-448p",
        chunk_duration=10,
    )

    assert issubclass(RTEmbedBackend, IngestionBackend)
    assert len(responses) == 2
    assert all(isinstance(response, EmbeddingChunkResponse) for response in responses)
    assert responses[0].embeddings == [0.1, 0.2]
    assert responses[0].chunk.start_pts == 0
    assert responses[0].chunk.is_first
    assert responses[1].embeddings == [0.3, 0.4]
    assert responses[1].chunk.start_pts == 10_000_000_000
    assert responses[1].chunk.is_last
    assert responses[1].rtvi_chunk_latency_s == 0.35
    assert captured["path"] == "/v1/generate_video_embeddings"
    assert captured["body"] == {
        "id": [SOURCE_ID],
        "model": "cosmos-embed1-448p",
        "stream": True,
        "chunk_duration": 10,
        "chunk_overlap_duration": 0,
    }

    await client.aclose()


async def test_rtvi_embed_backend_rejects_empty_stream() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="data: [DONE]\n\n",
        )

    client = httpx.AsyncClient(
        base_url="http://embed.test",
        transport=httpx.MockTransport(handler),
    )
    backend = RTEmbedBackend(_RTVIEmbedTransport(client=client))

    with pytest.raises(
        RTEmbedBackendError,
        match="without producing an embedding chunk",
    ):
        await backend.ingest(
            RequestSource(source_id=SOURCE_ID),
            prompt="unused",
            model="cosmos-embed1-448p",
        )

    await client.aclose()


async def test_uploaded_chunk_error_preserves_caller_source_identity(
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")
    original = RequestSource.for_file(str(video_path))
    upload_id = "uploaded-asset-id"
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(f"{request.method} {request.url.path}")
        if request.url.path == "/v1/files":
            return httpx.Response(200, json={"id": upload_id})
        if request.url.path == "/v1/generate_video_embeddings":
            body = json.loads(request.content)
            assert body["id"] == [upload_id]
            assert body["stream"] is False
            return httpx.Response(
                200,
                json={
                    "chunk_responses": [
                        {
                            "start_time": "0.0",
                            "end_time": "1.0",
                            "error": "chunk decoding failed",
                        }
                    ]
                },
            )
        if request.method == "DELETE":
            return httpx.Response(200)
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    client = httpx.AsyncClient(
        base_url="http://embed.test",
        transport=httpx.MockTransport(handler),
    )
    backend = RTEmbedBackend(_RTVIEmbedTransport(client=client))

    responses = await backend.ingest(
        original,
        prompt="unused",
        model="cosmos-embed1-448p",
    )

    assert len(responses) == 1
    assert responses[0].chunk.sourceId == original.source_id
    assert responses[0].chunk.file == str(video_path)
    assert responses[0].embeddings == []
    assert responses[0].error == "chunk decoding failed"
    assert requests == [
        "POST /v1/files",
        "POST /v1/generate_video_embeddings",
        f"DELETE /v1/files/{upload_id}",
    ]
    await client.aclose()


async def test_url_video_uses_finite_response_without_upload_cleanup() -> None:
    source = RequestSource.for_file("https://videos.example.test/video.mp4")
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(f"{request.method} {request.url.path}")
        body = json.loads(request.content)
        assert body["id"] == [source.source_id]
        assert body["url"] == source.url
        assert body["media_type"] == "video"
        assert body["stream"] is False
        return httpx.Response(
            200,
            json={
                "chunk_responses": [
                    {
                        "start_time": "0.0",
                        "end_time": "1.0",
                        "embeddings": [0.1, 0.2],
                    }
                ]
            },
        )

    client = httpx.AsyncClient(
        base_url="http://embed.test",
        transport=httpx.MockTransport(handler),
    )
    backend = RTEmbedBackend(_RTVIEmbedTransport(client=client))

    responses = await backend.ingest(
        source,
        prompt="unused",
        model="cosmos-embed1-448p",
    )

    assert responses[0].embeddings == [0.1, 0.2]
    assert requests == ["POST /v1/generate_video_embeddings"]
    await client.aclose()


@pytest.mark.parametrize("inference_fails", [False, True])
async def test_uploaded_cleanup_failure_preserves_primary_error(
    tmp_path: Path,
    inference_fails: bool,
) -> None:
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/files":
            return httpx.Response(200, json={"id": "uploaded-asset-id"})
        if request.url.path == "/v1/generate_video_embeddings":
            if inference_fails:
                return httpx.Response(503, json={"detail": "inference failed"})
            return httpx.Response(
                200,
                json={
                    "chunk_responses": [
                        {
                            "start_time": "0.0",
                            "end_time": "1.0",
                            "embeddings": [0.1],
                        }
                    ]
                },
            )
        if request.method == "DELETE":
            return httpx.Response(500, json={"detail": "cleanup failed"})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    client = httpx.AsyncClient(
        base_url="http://embed.test",
        transport=httpx.MockTransport(handler),
    )
    backend = RTEmbedBackend(_RTVIEmbedTransport(client=client))
    expected = (
        "request failed; check the provider" if inference_fails else "cleanup failed"
    )

    with pytest.raises(RTEmbedBackendError, match=expected) as caught:
        await backend.ingest(
            RequestSource.for_file(str(video_path)),
            prompt="unused",
            model="cosmos-embed1-448p",
        )

    assert caught.value.__cause__ is None
    await client.aclose()


async def test_uploaded_cleanup_failure_does_not_mask_cancellation(
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")
    inference_started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/files":
            return httpx.Response(200, json={"id": "uploaded-asset-id"})
        if request.url.path == "/v1/generate_video_embeddings":
            inference_started.set()
            await asyncio.Event().wait()
        if request.method == "DELETE":
            return httpx.Response(500, json={"detail": "cleanup failed"})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    client = httpx.AsyncClient(
        base_url="http://embed.test",
        transport=httpx.MockTransport(handler),
    )
    backend = RTEmbedBackend(_RTVIEmbedTransport(client=client))
    ingestion = asyncio.create_task(
        backend.ingest(
            RequestSource.for_file(str(video_path)),
            prompt="unused",
            model="cosmos-embed1-448p",
        )
    )

    await inference_started.wait()
    ingestion.cancel()
    with pytest.raises(asyncio.CancelledError):
        await ingestion

    await client.aclose()


async def test_cancellation_does_not_interrupt_uploaded_video_deletion(
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")
    upload_id = "uploaded-asset-id"
    delete_started = asyncio.Event()
    allow_delete = asyncio.Event()
    delete_cancelled = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal delete_cancelled
        if request.url.path == "/v1/files":
            return httpx.Response(200, json={"id": upload_id})
        if request.method == "DELETE":
            delete_started.set()
            try:
                await allow_delete.wait()
            except asyncio.CancelledError:
                delete_cancelled = True
                raise
            return httpx.Response(200)
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    client = httpx.AsyncClient(
        base_url="http://embed.test",
        transport=httpx.MockTransport(handler),
    )
    transport = _RTVIEmbedTransport(client=client)
    prepared = await transport.prepare_source(RequestSource.for_file(str(video_path)))
    deletion = asyncio.create_task(transport.release_source(prepared))

    await delete_started.wait()
    deletion.cancel()
    await asyncio.sleep(0)
    assert not delete_cancelled

    deletion.cancel()
    await asyncio.sleep(0)
    assert not delete_cancelled

    allow_delete.set()
    with pytest.raises(asyncio.CancelledError):
        await deletion

    assert upload_id not in transport._prepared_uploads
    assert not delete_cancelled
    await client.aclose()


async def test_rtvi_embed_capabilities() -> None:
    transport = _RTVIEmbedTransport()
    capabilities = RTEmbedBackend(transport).capability_map()

    assert capabilities["pipeline"] == "rt_embed"
    assert capabilities["outputs"] == ("embedding",)
    assert capabilities["shared_frames"]["supported"] is True
    assert capabilities["shared_frames"]["transport"] == "owned_cpu"
    assert RTEmbedBackend(transport).shared_frame_capability() == SharedFrameCapability(
        decoder="pynv",
        decoder_agnostic=True,
    )
    await transport.aclose()


async def test_rtvi_embed_consumes_shared_frames_without_video_decode() -> None:
    embedded: list[list[int]] = []
    chunk_windows: list[tuple[float, float]] = []

    class FakeTransport:
        async def get_model_info(self):
            return type("Model", (), {"id": "embed-model"})()

        async def generate_frame_embeddings(
            self, *, frames, chunk_start_s, chunk_end_s, model
        ):
            assert model == "embed-model"
            chunk_windows.append((chunk_start_s, chunk_end_s))
            embedded.append([frame.index for frame in frames])
            return {"embeddings": [float(len(frames)), 0.5]}

    class FakeLease:
        def __init__(self, frame):
            self.frame = frame
            self.released = False

        async def release(self):
            self.released = True

    class FakeBuffer:
        duration_s = 2.0

        def __init__(self):
            self.leases = [
                FakeLease(
                    Frame(
                        tensor=np.full((2, 3, 3), index, dtype=np.uint8),
                        source_id=SOURCE_ID,
                        timestamp_ns=index * 250_000_000,
                        index=index,
                        timestamp_quality="pts",
                        decoder="memory",
                        device="cpu",
                    )
                )
                for index in range(8)
            ]
            self.closed = False

        def __aiter__(self):
            async def iterate():
                for lease in self.leases:
                    yield lease

            return iterate()

        async def aclose(self):
            self.closed = True

    buffer = FakeBuffer()
    backend = RTEmbedBackend(FakeTransport())
    responses = await backend.ingest_shared(
        RequestSource(source_id=SOURCE_ID, url="/video.mp4"),
        buffer,
        prompt="unused",
        chunk_duration=1,
    )

    assert embedded == [[0, 1, 2, 3], [4, 5, 6, 7]]
    assert chunk_windows == [(0, 1), (1, 2)]
    assert [response.embeddings for response in responses] == [[4.0, 0.5], [4.0, 0.5]]
    assert [response.chunk.start_pts for response in responses] == [0, 1_000_000_000]
    assert responses[-1].chunk.is_last
    assert all(lease.released for lease in buffer.leases)
    assert buffer.closed


async def test_rtvi_embed_cuda_ipc_uses_unique_key_and_reports_cleanup_failure() -> (
    None
):
    class CleanupFailureTransport:
        camera_id: str | None = None

        async def add_shared_stream(self, **kwargs: Any) -> str:
            self.camera_id = kwargs["camera_id"]
            return "asset-1"

        def generate_video_embeddings_stream(
            self, **_: Any
        ) -> AsyncIterator[dict[str, Any]]:
            async def stream() -> AsyncIterator[dict[str, Any]]:
                yield {
                    "chunk_responses": [
                        {
                            "start_time": "0",
                            "end_time": "1",
                            "embeddings": [0.1, 0.2],
                        }
                    ]
                }

            return stream()

        async def remove_shared_stream(self, *, camera_id: str) -> None:
            assert camera_id == self.camera_id
            raise _RTVITransportError("remove failed")

    transport = CleanupFailureTransport()
    backend = RTEmbedBackend(transport)  # type: ignore[arg-type]

    with pytest.raises(RTEmbedBackendError, match="stream cleanup failed"):
        await backend.ingest_cuda_ipc(
            RequestSource(source_id=SOURCE_ID),
            CudaIpcFrameSource(
                control_socket="/run/vss/frame-broker/source.sock",
                session_token="token",
                consumer_id="rtvi-embed",
                device_id=0,
            ),
            asyncio.Event(),
            prompt="",
            model="embed-model",
            chunk_duration=1,
        )

    assert transport.camera_id is not None
    assert transport.camera_id.startswith("vss-")
    assert transport.camera_id != SOURCE_ID


async def test_rtvi_embed_cuda_ipc_removes_stream_after_ambiguous_add_failure() -> None:
    class AmbiguousAddTransport:
        camera_id: str | None = None
        removed: str | None = None

        async def add_shared_stream(self, **kwargs: Any) -> str:
            self.camera_id = kwargs["camera_id"]
            raise _RTVITransportError("response was malformed")

        async def remove_shared_stream(self, *, camera_id: str) -> None:
            self.removed = camera_id

    transport = AmbiguousAddTransport()
    backend = RTEmbedBackend(transport)  # type: ignore[arg-type]

    with pytest.raises(RTEmbedBackendError, match="request failed"):
        await backend.ingest_cuda_ipc(
            RequestSource(source_id=SOURCE_ID),
            CudaIpcFrameSource(
                control_socket="/run/vss/frame-broker/source.sock",
                session_token="token",
                consumer_id="rtvi-embed",
                device_id=0,
            ),
            asyncio.Event(),
            prompt="",
            model="embed-model",
            chunk_duration=1,
        )

    assert transport.removed == transport.camera_id
