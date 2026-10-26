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

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from vss.ingestion.contracts import RequestSource
from vss.ingestion.exceptions import IngestionBackendError
from vss.ingestion.service import _run_cuda_ipc_ingestion
from vss.video_processing.cuda_ipc import (
    CudaDeviceFrame,
    CudaIpcDecodeSession,
    CudaIpcFrameSource,
)
from vss.video_processing.exceptions import ProcessingError
from vss.video_processing.shared_decode import SharedFrameCapability


def test_cuda_device_frame_validates_nv12_interface() -> None:
    owner = SimpleNamespace(
        __cuda_array_interface__={
            "shape": (6, 4),
            "strides": (8, 1),
            "typestr": "|u1",
            "data": (1234, False),
            "version": 3,
            "stream": 9876,
        }
    )

    frame = CudaDeviceFrame.from_cuda_array(
        owner,
        width=4,
        height=4,
        timestamp_ns=99,
        index=2,
    )

    assert frame.pointer == 1234
    assert frame.pitch == 8
    assert frame.owner is owner
    assert frame.producer_stream == 9876


def test_cuda_device_frame_rejects_host_or_wrong_geometry() -> None:
    with pytest.raises(ProcessingError, match="CUDA array interface"):
        CudaDeviceFrame.from_cuda_array(
            object(), width=4, height=4, timestamp_ns=0, index=0
        )
    owner = SimpleNamespace(
        __cuda_array_interface__={
            "shape": (4, 4, 3),
            "strides": None,
            "typestr": "<f4",
            "data": (1234, False),
            "version": 3,
        }
    )
    with pytest.raises(ProcessingError, match="geometry"):
        CudaDeviceFrame.from_cuda_array(
            owner, width=4, height=4, timestamp_ns=0, index=0
        )
    rgb_owner = SimpleNamespace(
        __cuda_array_interface__={
            "shape": (4, 4, 3),
            "strides": (12, 3, 1),
            "typestr": "|u1",
            "data": (1234, False),
            "version": 3,
        }
    )
    with pytest.raises(ProcessingError, match="geometry"):
        CudaDeviceFrame.from_cuda_array(
            rgb_owner, width=4, height=4, timestamp_ns=0, index=0
        )


def test_frame_source_serializes_protocol_without_pointer() -> None:
    source = CudaIpcFrameSource(
        control_socket="/run/vss/frame-broker/job.sock",
        session_token="secret",
        consumer_id="rtvi-vlm",
        device_id=1,
        timestamp_origin_ns=123,
        duration_ns=0,
        color_matrix="bt709",
        color_range="full",
    )

    assert source.as_dict() == {
        "type": "shared_decode",
        "protocol_version": "1.0",
        "control_socket": "/run/vss/frame-broker/job.sock",
        "session_token": "secret",
        "consumer_id": "rtvi-vlm",
        "transport": "cuda_ipc",
        "device_id": 1,
        "timestamp_origin_ns": 123,
        "duration_ns": 0,
        "color_matrix": "bt709",
        "color_range": "full",
    }


@pytest.mark.asyncio
async def test_decode_session_closes_broker_before_decoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    session = CudaIpcDecodeSession(
        RequestSource(source_id="source"),
        source_id="source",
        socket_path="/run/vss/frame-broker/source.sock",
    )
    session._opened = SimpleNamespace()
    session._broker = SimpleNamespace(close=lambda: order.append("broker"))

    async def fake_close(opened: Any, failed: bool) -> None:
        del opened, failed
        order.append("decoder")

    monkeypatch.setattr("vss.video_processing.execution._close", fake_close)

    await session.aclose(failed=True)

    assert order == ["broker", "decoder"]


@pytest.mark.asyncio
async def test_cuda_ingestion_stops_broker_before_settling_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances: list[Any] = []

    class FakeSession:
        def __init__(self, *_: Any, **__: Any) -> None:
            self.stopped = asyncio.Event()
            self.closed = False
            instances.append(self)

        async def prepare(self) -> None:
            return None

        def frame_source(self, consumer_id: str) -> CudaIpcFrameSource:
            return CudaIpcFrameSource(
                control_socket="/run/vss/frame-broker/source.sock",
                session_token="token",
                consumer_id=consumer_id,
                device_id=0,
            )

        async def run(self) -> None:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                await self.stopped.wait()
                raise

        def stop(self) -> None:
            self.stopped.set()

        async def aclose(self, *, failed: bool) -> None:
            del failed
            self.closed = True

    class FailingBackend:
        name = "failing"

        async def ingest_cuda_ipc(
            self,
            source: RequestSource,
            frame_source: CudaIpcFrameSource,
            ready: asyncio.Event,
            **kwargs: Any,
        ) -> list[Any]:
            del source, frame_source, kwargs
            ready.set()
            await asyncio.sleep(0)
            raise IngestionBackendError("consumer failed")

    monkeypatch.setattr(
        "vss.video_processing.cuda_ipc.CudaIpcDecodeSession", FakeSession
    )

    with pytest.raises(IngestionBackendError, match="consumer failed"):
        await asyncio.wait_for(
            _run_cuda_ipc_ingestion(
                RequestSource(source_id="source"),
                ["failing"],
                [FailingBackend()],
                SharedFrameCapability(
                    decoder="pynv",
                    transport="cuda_ipc",
                    pixel_format="nv12",
                    device_id=0,
                ),
                prompt="",
                model=None,
                chunk_duration=1,
                chunk_overlap_duration=0,
                generation_config=None,
                buffer_depth=2,
            ),
            timeout=1,
        )

    assert instances[0].stopped.is_set()
    assert instances[0].closed
