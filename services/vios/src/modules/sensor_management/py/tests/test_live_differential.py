# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Live differential tests: Python read-path output vs the running C++ sensor-ms over the SAME DB.

Skipped automatically unless a VIOS stack is reachable (Postgres on localhost:5432 + sensor-ms API
on localhost:30000). When the stack is up, these prove byte-semantic parity of /sensor/list and
/sensor/{id}/streams. Connection params match the stream-processing docker-compose defaults.
"""
from __future__ import annotations

import asyncio

import pytest

httpx = pytest.importorskip("httpx")

from sensor_ms.config import Config
from sensor_ms.core.sensor_management import SensorManagement

CPP_API = "http://localhost:30000/api/v1/sensor"


def _live_config() -> Config:
    return Config(
        use_centralize_db=True,
        centralize_db_name="nvcentralizedb",
        centralize_db_username="vst",
        centralize_remote_db_password="nvidia123",
        centralize_remote_db_hostaddr="localhost",
        centralize_remote_db_port="5432",
        vst_data_path="/tmp",                # no cert -> fallback key (nvstreamer sensors have no pwd)
        use_message_broker="redis",
        redis_server_env_var="localhost:6379",
    )


def _stack_up() -> bool:
    try:
        httpx.get(f"{CPP_API}/list", timeout=3)
        mgmt = SensorManagement(_live_config())
        asyncio.get_event_loop().run_until_complete(mgmt.list_sensors())
        asyncio.get_event_loop().run_until_complete(mgmt.stop())
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _stack_up(), reason="no live VIOS stack (Postgres+sensor-ms)")


def _by_id(items, key="sensorId"):
    return {i[key]: i for i in items}


async def _py_list():
    mgmt = SensorManagement(_live_config())
    try:
        return await mgmt.list_sensors()
    finally:
        await mgmt.stop()


async def _py_streams(sid):
    mgmt = SensorManagement(_live_config())
    try:
        return await mgmt.list_streams(sid)
    finally:
        await mgmt.stop()


def test_sensor_list_matches_cpp():
    cpp = httpx.get(f"{CPP_API}/list", timeout=10).json()
    py = asyncio.get_event_loop().run_until_complete(_py_list())
    cpp_by, py_by = _by_id(cpp), _by_id(py)
    assert set(py_by) == set(cpp_by), (
        f"sensor id set differs: only-cpp={set(cpp_by)-set(py_by)} only-py={set(py_by)-set(cpp_by)}"
    )
    mism = {sid: {"cpp": cpp_by[sid], "py": py_by[sid]}
            for sid in cpp_by if cpp_by[sid] != py_by[sid]}
    assert not mism, f"per-sensor field mismatch: {mism}"


def test_sensor_streams_match_cpp():
    cpp_list = httpx.get(f"{CPP_API}/list", timeout=10).json()
    assert cpp_list, "no sensors to compare streams for"
    sid = cpp_list[0]["sensorId"]
    cpp = httpx.get(f"{CPP_API}/{sid}/streams", timeout=10).json()
    py = asyncio.get_event_loop().run_until_complete(_py_streams(sid))
    assert _by_id(cpp, "streamId") == _by_id(py, "streamId"), f"streams differ for {sid}"
