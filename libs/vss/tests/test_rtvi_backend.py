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
    IngestionBackend,
    RequestSource,
    RTVIBackend,
    VlmChunkResponse,
    VlmModelInfo,
)
from vss.ingestion.exceptions import RTVIBackendError
from vss.ingestion.rtvi import _RTVITransport, _RTVITransportError
from vss.video_processing import Frame
from vss.video_processing.cuda_ipc import CudaIpcFrameSource

SOURCE_ID = "123e4567-e89b-12d3-a456-426614174000"


def test_rtvi_shared_transport_can_override_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VSS_SHARED_FRAME_TRANSPORT", "cuda_ipc")
    backend = RTVIBackend()

    owned = backend.shared_frame_capability_for_transport("owned_cpu")
    cuda = backend.shared_frame_capability_for_transport("cuda_ipc")

    assert owned.transport == "owned_cpu"
    assert cuda.transport == "cuda_ipc"


async def test_rtvi_shared_frame_capability_and_caption_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    requests: list[str] = []
    monkeypatch.setattr(
        "vss.ingestion.rtvi._encode_rgb_frame_jpeg",
        lambda _frame: b"encoded-jpeg",
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(f"{request.method} {request.url.path}")
        if request.url.path == "/v1/files":
            return httpx.Response(200, json={"id": "frame-asset"})
        if request.url.path == "/v1/generate_captions":
            captured.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "chunk_responses": [
                        {
                            "content": "shared caption",
                            "input_tokens": 10,
                            "output_tokens": 2,
                        }
                    ]
                },
            )
        if request.method == "DELETE":
            return httpx.Response(200)
        return httpx.Response(
            404,
        )

    client = httpx.AsyncClient(
        base_url="http://rtvi.test",
        transport=httpx.MockTransport(handler),
    )
    transport = _RTVITransport(client=client)
    backend = RTVIBackend(transport)
    frame = Frame(
        tensor=np.zeros((2, 3, 3), dtype=np.uint8),
        source_id=SOURCE_ID,
        timestamp_ns=0,
        index=0,
        timestamp_quality="pts",
        decoder="memory",
        device="cpu",
    )

    result = await transport.generate_frame_captions(
        frames=[frame],
        chunk_start_s=0.0,
        chunk_end_s=1.0,
        prompt="Describe.",
        model="test-vlm",
    )

    assert backend.capability_map()["shared_frames"]["supported"] is True
    assert backend.shared_frame_capability().decoder == "pynv"
    assert result == {
        "content": "shared caption",
        "input_tokens": 10,
        "output_tokens": 2,
    }
    assert captured["model"] == "test-vlm"
    assert captured["id"] == ["frame-asset"]
    assert captured["prompt"] == "Describe."
    assert captured["media_type"] == "image"
    assert captured["frame_times"] == [0.0]
    assert captured["frame_chunk_start"] == 0.0
    assert captured["frame_chunk_end"] == 1.0
    assert requests == [
        "POST /v1/files",
        "POST /v1/generate_captions",
        "DELETE /v1/files/frame-asset",
    ]
    await client.aclose()


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "prompt",
        "model",
        "stream",
        "chunk_duration",
        "chunk_overlap_duration",
        "media_type",
        "frame_times",
        "frame_chunk_start",
        "frame_chunk_end",
    ],
)
async def test_rtvi_frame_caption_rejects_owned_generation_fields(
    field: str,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        pytest.fail("invalid generation config must be rejected before frame upload")

    client = httpx.AsyncClient(
        base_url="http://rtvi.test",
        transport=httpx.MockTransport(handler),
    )
    transport = _RTVITransport(client=client)
    frame = Frame(
        tensor=np.zeros((2, 3, 3), dtype=np.uint8),
        source_id=SOURCE_ID,
        timestamp_ns=0,
        index=0,
        timestamp_quality="pts",
        decoder="memory",
        device="cpu",
    )

    with pytest.raises(ValueError, match=field):
        await transport.generate_frame_captions(
            frames=[frame],
            chunk_start_s=0.0,
            chunk_end_s=1.0,
            prompt="Describe.",
            model="test-vlm",
            generation_config={field: "override"},
        )

    await client.aclose()


async def test_rtvi_transport_health_and_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert issubclass(RTVIBackend, IngestionBackend)
    monkeypatch.delenv("RTVI_MODEL", raising=False)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/health/ready":
            return httpx.Response(200, json={"healthy": True})
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "test-vlm",
                            "created": 1,
                            "owned_by": "custom",
                            "api_type": "internal",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    http_client = httpx.AsyncClient(
        base_url="http://rtvi.test",
        transport=httpx.MockTransport(handler),
    )
    transport = _RTVITransport(client=http_client)

    assert await transport.check_health()
    assert await transport.list_models() == ("test-vlm",)
    model = await transport.get_model_info()
    assert model.id == "test-vlm"
    assert model.api_type == "internal"

    await http_client.aclose()


async def test_rtvi_shared_stream_uses_documented_api_routes() -> None:
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/add"):
            return httpx.Response(200, json={"asset_id": "asset-1"})
        return httpx.Response(200)

    client = httpx.AsyncClient(
        base_url="http://rtvi.test",
        transport=httpx.MockTransport(handler),
    )
    transport = _RTVITransport(client=client)

    asset_id = await transport.add_shared_stream(
        source_id=SOURCE_ID,
        camera_id=SOURCE_ID,
        frame_source={"type": "shared_decode"},
    )
    await transport.remove_shared_stream(camera_id=SOURCE_ID)

    assert asset_id == "asset-1"
    assert requests == [
        "POST /api/v1/stream/add",
        "POST /api/v1/stream/remove",
    ]
    await client.aclose()


async def test_rtvi_shared_stream_rejects_malformed_success_body() -> None:
    client = httpx.AsyncClient(
        base_url="http://rtvi.test",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200)),
    )
    transport = _RTVITransport(client=client)

    with pytest.raises(_RTVITransportError, match="malformed JSON"):
        await transport.add_shared_stream(
            source_id=SOURCE_ID,
            camera_id=SOURCE_ID,
            frame_source={"type": "shared_decode"},
        )

    await client.aclose()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json=["not", "an", "object"]),
    ],
)
async def test_rtvi_frame_upload_validates_success_body(
    monkeypatch: pytest.MonkeyPatch,
    response: httpx.Response,
) -> None:
    monkeypatch.setattr(
        "vss.ingestion.rtvi._encode_rgb_frame_jpeg",
        lambda _frame: b"encoded-jpeg",
    )
    client = httpx.AsyncClient(
        base_url="http://rtvi.test",
        transport=httpx.MockTransport(lambda _request: response),
    )
    transport = _RTVITransport(client=client)
    frame = Frame(
        tensor=np.zeros((2, 3, 3), dtype=np.uint8),
        source_id=SOURCE_ID,
        timestamp_ns=0,
        index=0,
        timestamp_quality="pts",
        decoder="memory",
        device="cpu",
    )

    with pytest.raises(_RTVITransportError, match="frame upload response"):
        await transport.generate_frame_captions(
            frames=[frame],
            chunk_start_s=0,
            chunk_end_s=1,
            prompt="Describe.",
            model="test-vlm",
        )

    await client.aclose()


async def test_rtvi_cuda_ipc_uses_unique_key_and_reports_cleanup_failure() -> None:
    class CleanupFailureTransport:
        camera_id: str | None = None

        async def get_model_info(self) -> VlmModelInfo:
            raise AssertionError("explicit model should avoid discovery")

        async def add_shared_stream(self, **kwargs: Any) -> str:
            self.camera_id = kwargs["camera_id"]
            return "asset-1"

        def generate_captions_stream(self, **_: Any) -> AsyncIterator[dict[str, Any]]:
            async def stream() -> AsyncIterator[dict[str, Any]]:
                yield {
                    "chunk_responses": [
                        {
                            "chunk_id": 0,
                            "start_time": "0",
                            "end_time": "1",
                            "content": "caption",
                        }
                    ]
                }

            return stream()

        async def remove_shared_stream(self, *, camera_id: str) -> None:
            assert camera_id == self.camera_id
            raise _RTVITransportError("remove failed")

    transport = CleanupFailureTransport()
    backend = RTVIBackend(transport)  # type: ignore[arg-type]

    with pytest.raises(RTVIBackendError, match="stream cleanup failed"):
        await backend.ingest_cuda_ipc(
            RequestSource(source_id=SOURCE_ID),
            CudaIpcFrameSource(
                control_socket="/run/vss/frame-broker/source.sock",
                session_token="token",
                consumer_id="rtvi",
                device_id=0,
            ),
            asyncio.Event(),
            prompt="Describe.",
            model="test-vlm",
            chunk_duration=1,
        )

    assert transport.camera_id is not None
    assert transport.camera_id.startswith("vss-")
    assert transport.camera_id != SOURCE_ID


async def test_rtvi_cuda_ipc_removes_stream_after_ambiguous_add_failure() -> None:
    class AmbiguousAddTransport:
        camera_id: str | None = None
        removed: str | None = None

        async def add_shared_stream(self, **kwargs: Any) -> str:
            self.camera_id = kwargs["camera_id"]
            raise _RTVITransportError("response was malformed")

        async def remove_shared_stream(self, *, camera_id: str) -> None:
            self.removed = camera_id

    transport = AmbiguousAddTransport()
    backend = RTVIBackend(transport)  # type: ignore[arg-type]

    with pytest.raises(RTVIBackendError, match="request failed"):
        await backend.ingest_cuda_ipc(
            RequestSource(source_id=SOURCE_ID),
            CudaIpcFrameSource(
                control_socket="/run/vss/frame-broker/source.sock",
                session_token="token",
                consumer_id="rtvi",
                device_id=0,
            ),
            asyncio.Event(),
            prompt="Describe.",
            model="test-vlm",
            chunk_duration=1,
        )

    assert transport.removed == transport.camera_id


async def test_rtvi_transport_uses_backend_specific_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VLM_BASE_URL", "https://generic-vlm.example.test/api")
    monkeypatch.setenv("RTVI_BASE_URL", "https://rtvi.example.test/api")
    monkeypatch.setenv("RTVI_MODEL", "configured-rtvi")
    monkeypatch.setenv("RTVI_API_KEY", "test-key")

    configured_transport = _RTVITransport()
    assert (
        str(configured_transport._client.base_url) == "https://rtvi.example.test/api/"
    )
    await configured_transport.aclose()

    captured_headers: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.update(request.headers)
        return httpx.Response(200, json={"data": []})

    http_client = httpx.AsyncClient(
        base_url="http://rtvi.test",
        transport=httpx.MockTransport(handler),
    )
    transport = _RTVITransport(client=http_client)

    model = await transport.get_model_info()
    assert model.id == "configured-rtvi"
    assert await transport.list_models() == ()
    assert captured_headers["authorization"] == "Bearer test-key"

    await http_client.aclose()


async def test_rtvi_transport_manages_uploaded_video(
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "sample.mp4"
    video_path.write_bytes(b"video")
    requests: list[str] = []
    delete_attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal delete_attempts
        requests.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            assert b"sample.mp4" in request.content
            return httpx.Response(200, json={"id": SOURCE_ID})
        delete_attempts += 1
        return httpx.Response(204 if delete_attempts > 1 else 409)

    http_client = httpx.AsyncClient(
        base_url="http://rtvi.test",
        transport=httpx.MockTransport(handler),
    )
    transport = _RTVITransport(client=http_client)

    async with transport.uploaded_video(video_path) as source:
        assert source.source_id == SOURCE_ID
        assert requests == ["POST /v1/files"]

    assert requests == [
        "POST /v1/files",
        f"DELETE /v1/files/{SOURCE_ID}",
        f"DELETE /v1/files/{SOURCE_ID}",
    ]
    await http_client.aclose()


async def test_rtvi_backend_returns_existing_chunk_responses() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        events = (
            'data: {"id":"123e4567-e89b-12d3-a456-426614174000",'
            '"model":"test-vlm","created":1,"media_info":'
            '{"type":"offset"},"chunk_responses":[{"chunk_id":0,'
            '"start_time":"0.0","end_time":"60.0",'
            '"content":"A person enters the room.",'
            '"decode_latency_ms":100,"vlm_latency_ms":500,'
            '"chunk_latency_ms":650,"queue_time_s":0.05,'
            '"processing_latency_s":0.6,"frame_count":16,'
            '"input_tokens":10,"output_tokens":6}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=events,
        )

    http_client = httpx.AsyncClient(
        base_url="http://rtvi.test",
        transport=httpx.MockTransport(handler),
    )
    backend = RTVIBackend(_RTVITransport(client=http_client))
    source = RequestSource(url=None, source_id=SOURCE_ID)

    responses = await backend.ingest(
        source,
        prompt="Describe the video.",
        model="test-vlm",
        chunk_duration=60,
    )

    assert len(responses) == 1
    response = responses[0]
    assert isinstance(response, VlmChunkResponse)
    assert response.vlm_response == "A person enters the room."
    assert response.chunk is not None
    assert response.chunk.sourceId == SOURCE_ID
    assert response.chunk.start_pts == 0
    assert response.chunk.end_pts == 60_000_000_000
    assert response.chunk.is_first
    assert response.chunk.is_last
    assert response.rtvi_frame_count == 16
    assert response.vlm_stats == {
        "input_tokens": 10,
        "output_tokens": 6,
    }
    assert captured["path"] == "/v1/generate_captions"
    assert captured["body"]["id"] == SOURCE_ID
    assert captured["body"]["chunk_duration"] == 60
    assert "add_doc" not in json.dumps(captured["body"])
    assert captured["headers"]["x-stream-id"] == SOURCE_ID

    await http_client.aclose()


async def test_rtvi_backend_can_cancel_active_ingestion() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingTransport:
        def generate_captions_stream(self, **_: Any) -> AsyncIterator[dict[str, Any]]:
            async def stream() -> AsyncIterator[dict[str, Any]]:
                started.set()
                await release.wait()
                if False:
                    yield {}

            return stream()

        async def check_health(self) -> bool:
            return True

        async def prepare_source(self, source: RequestSource) -> RequestSource:
            return source

        async def release_source(self, source: RequestSource) -> None:
            return None

        async def get_model_info(self) -> VlmModelInfo:
            model = VlmModelInfo()
            model.id = "test-vlm"
            return model

        async def aclose(self) -> None:
            return None

    backend = RTVIBackend(BlockingTransport())
    source = RequestSource(source_id=SOURCE_ID)
    task = asyncio.create_task(
        backend.ingest(source, prompt="Describe.", model="test-vlm")
    )
    await started.wait()

    assert await backend.cancel(SOURCE_ID)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not await backend.cancel(SOURCE_ID)
