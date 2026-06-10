# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Write-path tests (add/delete + event publish), isolated from any live shared data.

- SQLite round-trip: add -> list/get -> password decrypt -> delete, against a throwaway DB file.
- Redis round-trip: publish to a DEDICATED test stream (never the live vst_events), verify the exact
  serialized body landed, then clean up. Skipped if no redis on localhost:6379.
"""
from __future__ import annotations

import pytest

from sensor_ms.config import Config
from sensor_ms.core.sensor_management import SensorManagement
from sensor_ms.db.engine import make_engine
from sensor_ms.db.models import Base
from sensor_ms.events.publisher import ChangeEvent, EventPublisher, build_payload, serialize_event


def _sqlite_cfg(tmp_path) -> Config:
    db = tmp_path / "vst.db"
    return Config(
        use_centralize_db=False,
        sqlite_db_path=str(db),
        vst_data_path=str(tmp_path),     # no cert -> fallback key
        use_message_broker="",            # events disabled for the DB-only test
    )


async def test_sqlite_add_list_delete_roundtrip(tmp_path):
    cfg = _sqlite_cfg(tmp_path)
    Base.metadata.create_all(make_engine(cfg))   # create the lowercase schema in the temp DB
    mgmt = SensorManagement(cfg)
    try:
        sid = await mgmt.add_sensor({
            "sensorUrl": "rtsp://10.0.0.9:554/s1", "username": "admin",
            "password": "Pw!42", "name": "wtest", "tags": "t1",
        })
        listed = {s["sensorId"]: s for s in await mgmt.list_sensors()}
        assert sid in listed
        s = listed[sid]
        assert s["name"] == "wtest" and s["type"] == "sensor_rtsp" and s["state"] == "online"
        assert s["tags"] == "t1"
        # password persisted encrypted and decrypts back
        assert mgmt.repo.get_password(sid) == "Pw!42"
        # get_sensor (info endpoint shape: no state/type/isTimelinePresent)
        info = await mgmt.get_sensor(sid)
        assert "state" not in info and info["name"] == "wtest"
        # delete
        await mgmt.delete_sensor(sid)
        assert sid not in {x["sensorId"] for x in await mgmt.list_sensors()}
    finally:
        await mgmt.stop()


def _redis_up(cfg) -> bool:
    try:
        import redis
        redis.Redis(host="localhost", port=6379, socket_connect_timeout=3).ping()
        return True
    except Exception:
        return False


async def test_add_emits_add_then_proxy_and_creates_stream(tmp_path):
    import json

    cfg = Config(use_centralize_db=False, sqlite_db_path=str(tmp_path / "v.db"),
                 vst_data_path=str(tmp_path), use_message_broker="redis",
                 redis_server_env_var="localhost:6379",
                 message_broker_topic="vst_events_pytest_proxy", message_broker_payload_key="sensor.id")
    if not _redis_up(cfg):
        pytest.skip("no redis on localhost:6379")
    Base.metadata.create_all(make_engine(cfg))
    import redis
    r = redis.Redis(host="localhost", port=6379)
    r.delete("vst_events_pytest_proxy")
    mgmt = SensorManagement(cfg)
    try:
        sid = await mgmt.add_sensor({
            "sensorUrl": "rtsp://198.51.100.7:554/h264", "username": "cam", "password": "Secret9",
            "name": "proxy-probe", "encoding": "h265",
        })
        entries = r.xrange("vst_events_pytest_proxy")
        events = [json.loads(f[b"sensor.id"].decode())["event"] for _id, f in entries]
        # order: camera_add then camera_proxy
        assert [e["change"] for e in events] == ["camera_add", "camera_proxy"]
        add, proxy = events
        assert add["camera_url"] == "" and "metadata" not in add and add["camera_id"] == sid
        assert proxy["camera_id"] == sid
        # credentials embedded into the proxy url; codec from the request encoding
        assert proxy["camera_url"] == "rtsp://cam:Secret9@198.51.100.7:554/h264"
        assert proxy["metadata"] == {"codec": "h265", "framerate": "", "resolution": ""}
        # stream row created with stream_id == sensor_id, proxy url empty
        streams = await mgmt.list_streams(sid)
        assert len(streams) == 1 and streams[0]["streamId"] == sid
        assert streams[0]["url"] == ""  # proxy_url empty until RTSP-server MS fills it
        assert streams[0]["metadata"]["codec"] == "h265"
    finally:
        r.delete("vst_events_pytest_proxy")
        await mgmt.stop()


async def test_redis_publish_roundtrip(tmp_path):
    cfg = Config(use_message_broker="redis", redis_server_env_var="localhost:6379",
                 message_broker_topic="vst_events_pytest", message_broker_payload_key="sensor.id")
    if not _redis_up(cfg):
        pytest.skip("no redis on localhost:6379")
    import redis
    r = redis.Redis(host="localhost", port=6379)
    r.delete("vst_events_pytest")
    try:
        pub = EventPublisher(cfg)
        payload = build_payload(change=ChangeEvent.camera_add, camera_id="sid-x",
                                camera_name="n", camera_url="", tags="", created_at="2026-06-09T00:00:00Z")
        await pub.publish(payload)
        entries = r.xrange("vst_events_pytest")
        assert len(entries) == 1
        _id, fields = entries[0]
        assert fields[b"sensor.id"].decode() == serialize_event(payload)
    finally:
        r.delete("vst_events_pytest")
