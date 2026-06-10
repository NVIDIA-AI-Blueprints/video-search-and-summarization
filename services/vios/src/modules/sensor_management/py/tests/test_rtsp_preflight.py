# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""RTSP DESCRIBE pre-flight tests + add_sensor verifyRtsp gating."""
from __future__ import annotations

import asyncio

import pytest

from sensor_ms.adaptors.rtsp_preflight import rtsp_describe
from sensor_ms.api.errors import VmsError, VmsErrorCode
from sensor_ms.config import Config
from sensor_ms.core.sensor_management import SensorManagement
from sensor_ms.db.engine import make_engine
from sensor_ms.db.models import Base

_SDP = (
    "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=test\r\n"
    "m=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
    "a=framerate:30\r\na=x-dimensions: 1920,1080\r\n"
)


async def _mock_rtsp_server(status_line: str):
    """Start a one-shot RTSP server that answers DESCRIBE with the given status + SDP. Returns port."""
    async def handle(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        body = _SDP if status_line.endswith("200 OK") else ""
        resp = (f"{status_line}\r\nCSeq: 1\r\nContent-Type: application/sdp\r\n"
                f"Content-Length: {len(body)}\r\n\r\n{body}")
        writer.write(resp.encode())
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


async def test_describe_ok_parses_sdp():
    server, port = await _mock_rtsp_server("RTSP/1.0 200 OK")
    async with server:
        p = await rtsp_describe(f"rtsp://127.0.0.1:{port}/s", timeout=3)
    assert p.ok and p.status == 200
    assert p.codec == "H264" and p.framerate == "30" and p.resolution == "1920x1080"


async def test_describe_404_rejected():
    server, port = await _mock_rtsp_server("RTSP/1.0 404 Not Found")
    async with server:
        p = await rtsp_describe(f"rtsp://127.0.0.1:{port}/s", timeout=3)
    assert not p.ok and p.status == 404


async def test_describe_connection_refused():
    p = await rtsp_describe("rtsp://127.0.0.1:9/x", timeout=2)
    assert not p.ok


def _sqlite_cfg(tmp_path) -> Config:
    return Config(use_centralize_db=False, sqlite_db_path=str(tmp_path / "v.db"),
                  vst_data_path=str(tmp_path), use_message_broker="")


async def test_add_rejects_unreachable_when_verify_true(tmp_path):
    cfg = _sqlite_cfg(tmp_path)
    Base.metadata.create_all(make_engine(cfg))
    mgmt = SensorManagement(cfg)
    try:
        with pytest.raises(VmsError) as ei:
            await mgmt.add_sensor({"sensorUrl": "rtsp://192.0.2.1:554/x", "username": "u",
                                   "password": "p", "verifyRtsp": True})
        assert ei.value.code == VmsErrorCode.InvalidParameterError
        # and nothing was persisted
        assert await mgmt.list_sensors() == []
    finally:
        await mgmt.stop()


async def test_add_skips_preflight_when_verify_false(tmp_path):
    cfg = _sqlite_cfg(tmp_path)
    Base.metadata.create_all(make_engine(cfg))
    mgmt = SensorManagement(cfg)
    try:
        sid = await mgmt.add_sensor({"sensorUrl": "rtsp://192.0.2.1:554/x", "username": "u",
                                     "password": "p", "verifyRtsp": False})
        assert sid in {s["sensorId"] for s in await mgmt.list_sensors()}
    finally:
        await mgmt.stop()
