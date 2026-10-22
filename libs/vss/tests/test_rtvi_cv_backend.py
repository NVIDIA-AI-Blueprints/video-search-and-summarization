# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
import time as stdlib_time
from collections import deque
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import vss.ingestion.backend as backend_module
from vss.ingestion.backend import RTVICvBackend, _cv_chunk_responses
from vss.ingestion.contracts import RequestSource
from vss.ingestion.exceptions import RTVICvBackendError
from vss.ingestion.rtvi_cv import (
    _parse_cv_detections,
    _RTVICvTransport,
    _RTVICvTransportError,
)
from vss.video_processing.cuda_ipc import CudaIpcFrameSource


class _FakeConsumer:
    def __init__(self, messages: list[dict[str, Any] | None]) -> None:
        self.messages = deque(messages)
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def receive(self, timeout_s: float) -> dict[str, Any] | None:
        del timeout_s
        return self.messages.popleft() if self.messages else None

    async def aclose(self) -> None:
        self.closed = True


class _FakeTransport:
    def __init__(
        self,
        *,
        addition_fails: bool = False,
        removal_fails: bool = False,
    ) -> None:
        self.consumer = _FakeConsumer(
            [
                {
                    "sensorId": "source-1",
                    "source_pts_ns": 3_000_000_000,
                    "frameId": 3,
                    "trackingId": 9,
                    "objectId": "person",
                    "confidence": 0.9,
                    "bbox": {"left": 1, "top": 2, "width": 3, "height": 4},
                },
                {
                    "sensorId": "another-camera",
                    "source_pts_ns": 100_000_000_000,
                    "objects": [],
                },
                {
                    "source_pts_ns": 3_000_000_000,
                    "objectId": "unidentified-object",
                },
                {
                    "sensorId": "source-1",
                    "source_pts_ns": 3_000_000_000,
                    "objects": [],
                },
            ]
        )
        self.statuses = deque(
            [
                {"status": "running", "last_pts_ns": 3_000_000_000},
                {
                    "status": "eos",
                    "last_pts_ns": 3_000_000_000,
                    "results_complete": True,
                    "final_results_count": 2,
                    "final_results_pts_ns": 3_000_000_000,
                },
                {
                    "status": "eos",
                    "last_pts_ns": 3_000_000_000,
                    "results_complete": True,
                    "final_results_count": 2,
                    "final_results_pts_ns": 3_000_000_000,
                },
                {
                    "status": "eos",
                    "last_pts_ns": 3_000_000_000,
                    "results_complete": True,
                    "final_results_count": 2,
                    "final_results_pts_ns": 3_000_000_000,
                },
            ]
        )
        self.added: dict[str, Any] | None = None
        self.removed: str | None = None
        self.consumer_id: str | None = None
        self.addition_fails = addition_fails
        self.removal_fails = removal_fails

    async def aclose(self) -> None:
        return None

    async def check_health(self) -> bool:
        return True

    def create_consumer(self, source_id: str) -> _FakeConsumer:
        self.consumer_id = source_id
        return self.consumer

    async def add_shared_stream(self, **kwargs: Any) -> None:
        self.added = kwargs
        if self.addition_fails:
            raise _RTVICvTransportError("response was lost")

    async def stream_status(self, camera_id: str) -> dict[str, Any]:
        assert self.added is not None
        assert camera_id == self.added["camera_id"]
        return self.statuses.popleft()

    async def remove_shared_stream(self, camera_id: str) -> None:
        self.removed = camera_id
        if self.removal_fails:
            raise _RTVICvTransportError("remove failed")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json=[]),
    ],
)
async def test_stream_status_wraps_invalid_json(
    response: httpx.Response,
) -> None:
    client = httpx.AsyncClient(
        base_url="http://rtvi-cv.test",
        transport=httpx.MockTransport(lambda _request: response),
    )
    transport = _RTVICvTransport(client=client)

    with pytest.raises(_RTVICvTransportError, match="stream status"):
        await transport.stream_status("camera")

    await client.aclose()


def test_parse_cv_detection_normalizes_bbox_and_tracking() -> None:
    detections = _parse_cv_detections(
        {
            "source_pts_ns": 42,
            "trackingId": 8,
            "objectId": "car",
            "bbox": [1, 2, 3, 4],
        }
    )

    assert len(detections) == 1
    assert detections[0].timestamp_ns == 42
    assert detections[0].tracking_id == "8"
    assert detections[0].bbox is not None
    assert detections[0].bbox.width == 3


@pytest.mark.asyncio
async def test_rtvi_cv_consumes_kafka_until_deterministic_eos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VSS_SHARED_FRAME_TRANSPORT", "cuda_ipc")
    transport = _FakeTransport()
    backend = RTVICvBackend(transport=transport)  # type: ignore[arg-type]
    ready = asyncio.Event()
    frame_source = CudaIpcFrameSource(
        control_socket="/run/vss/frame-broker/source-1.sock",
        session_token="token",
        consumer_id="rtvi-cv",
        device_id=0,
    )

    responses = await backend.ingest_cuda_ipc(
        RequestSource(
            source_id="source-1",
            camera_id="camera-1",
            url="/video.mp4",
        ),
        frame_source,
        ready,
        prompt="",
        chunk_duration=2,
    )

    assert ready.is_set()
    assert transport.consumer.started
    assert transport.consumer.closed
    assert not transport.consumer.messages
    assert transport.added is not None
    camera_id = transport.added["camera_id"]
    assert camera_id.startswith("vss-")
    assert camera_id != "source-1"
    assert transport.consumer_id == camera_id
    assert transport.removed == camera_id
    assert transport.added["frame_source"]["transport"] == "cuda_ipc"
    assert len(responses) == 2
    detections = [
        detection for response in responses for detection in response.detections
    ]
    assert [detection.object_id for detection in detections] == ["person"]
    assert responses[-1].chunk.is_last


@pytest.mark.asyncio
async def test_rtvi_cv_closes_consumer_when_stream_removal_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VSS_SHARED_FRAME_TRANSPORT", "cuda_ipc")
    transport = _FakeTransport(removal_fails=True)
    backend = RTVICvBackend(transport=transport)  # type: ignore[arg-type]

    with pytest.raises(RTVICvBackendError, match="cleanup failed"):
        await backend.ingest_cuda_ipc(
            RequestSource(source_id="source-1"),
            CudaIpcFrameSource(
                control_socket="/run/vss/frame-broker/source-1.sock",
                session_token="token",
                consumer_id="rtvi-cv",
                device_id=0,
            ),
            asyncio.Event(),
            prompt="",
            chunk_duration=2,
        )

    assert transport.consumer.closed


@pytest.mark.asyncio
async def test_rtvi_cv_removes_stream_after_ambiguous_add_failure() -> None:
    transport = _FakeTransport(addition_fails=True)
    backend = RTVICvBackend(transport=transport)  # type: ignore[arg-type]

    with pytest.raises(RTVICvBackendError, match="request failed"):
        await backend.ingest_cuda_ipc(
            RequestSource(source_id="source-1"),
            CudaIpcFrameSource(
                control_socket="/run/vss/frame-broker/source-1.sock",
                session_token="token",
                consumer_id="rtvi-cv",
                device_id=0,
            ),
            asyncio.Event(),
            prompt="",
            chunk_duration=2,
        )

    assert transport.added is not None
    assert transport.removed == transport.added["camera_id"]
    assert transport.consumer.closed


@pytest.mark.asyncio
async def test_rtvi_cv_normalizes_absolute_status_timestamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin_ns = 1_000_000_000_000_001
    final_pts_ns = origin_ns + 3_000_000_000
    transport = _FakeTransport()
    transport.consumer = _FakeConsumer(
        [
            {
                "sensorId": "source-1",
                "timestamp_ns": final_pts_ns,
                "objectId": "person",
            }
        ]
    )
    status = {
        "status": "eos",
        "timestamp_origin_ns": origin_ns,
        "last_pts_ns": final_pts_ns,
        "final_results_pts_ns": final_pts_ns,
    }
    transport.statuses = deque([status, status])
    monotonic_values = iter((0.0, 31.0))
    monkeypatch.setattr(
        backend_module,
        "time",
        SimpleNamespace(
            monotonic=lambda: next(monotonic_values),
            time=stdlib_time.time,
        ),
    )
    backend = RTVICvBackend(transport=transport)  # type: ignore[arg-type]

    responses = await backend.ingest_cuda_ipc(
        RequestSource(source_id="source-1"),
        CudaIpcFrameSource(
            control_socket="/run/vss/frame-broker/source-1.sock",
            session_token="token",
            consumer_id="rtvi-cv",
            device_id=0,
            timestamp_origin_ns=origin_ns,
            duration_ns=3_000_000_000,
        ),
        asyncio.Event(),
        prompt="",
        chunk_duration=3600,
    )

    assert len(responses) == 1
    assert responses[0].chunk.end_pts == 3_000_000_000
    assert responses[0].detections[0].timestamp_ns == 3_000_000_000


def test_cv_absolute_timestamps_use_source_origin_and_stream_end() -> None:
    origin_ns = 1_800_000_000_000_000_000
    detections = _parse_cv_detections(
        {
            "sensorId": "source-1",
            "timestamp_ns": origin_ns + 2_000_000_000,
            "objectId": "person",
        }
    )

    responses = _cv_chunk_responses(
        RequestSource(source_id="source-1", url="/video.mp4"),
        detections,
        chunk_duration=2,
        last_pts_ns=5_000_000_000,
        timestamp_origin_ns=origin_ns,
        processing_latency=0,
    )

    assert detections[0].timestamp_ns == 2_000_000_000
    assert [response.chunk.start_pts for response in responses] == [
        0,
        2_000_000_000,
        4_000_000_000,
    ]
    assert responses[-1].chunk.end_pts == 5_000_000_000


def test_cv_chunk_responses_preserve_reported_zero_duration() -> None:
    responses = _cv_chunk_responses(
        RequestSource(source_id="empty-source"),
        [],
        chunk_duration=2,
        last_pts_ns=0,
        timestamp_origin_ns=None,
        processing_latency=0,
    )

    assert len(responses) == 1
    assert responses[0].chunk.start_pts == 0
    assert responses[0].chunk.end_pts == 0
