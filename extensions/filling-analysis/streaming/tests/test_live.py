# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
import json
import queue
import sys
import threading
import time
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from streaming.config import source_config
from streaming.decoder import put_latest, read_stream
from streaming.online import OnlineCycles
from streaming.service import LiveManager, create_app
from streaming.storage import EventStore


CALIBRATION = json.loads((Path(__file__).parents[2] / "backend/neural_measurement_calibration.json").read_text())
STREAM = "93f23506-b10f-4475-95f1-45a5aaf532f3"


def instance(t, final=.72803):
    level = .1 if t < 2 else min(final, .1 + (final-.1)*(t-2)/3)
    center = .5 if t < 8 else .5 + (t-8)*.2
    return {"id": "test-instance", "box": [center-.08, .2, .16, .6], "bottle_confidence": .95,
            "bottle_mask": [[[.1,.1],[.2,.1],[.2,.2]]], "liquid_mask": [],
            "measurement": {"level": level, "confidence": .95, "geometry": {"center_x": center},
                            "measurement_state": "measured_rf_mask_boundary"}}


def complete_cycle(final=.72803):
    tracker = OnlineCycles(CALIBRATION, .72803, 24)
    events = []
    for i in range(253):
        t = i/24
        events.extend(tracker.update(t, [instance(t, final)] if t <= 9 else [], 0, i))
    return tracker, events


@pytest.mark.parametrize(("level", "verdict"), [(.72803, "normal"), (.45, "underfill")])
def test_causal_final_verdict_requires_departure(level, verdict):
    tracker, events = complete_cycle(level)
    assert len(events) == 1
    assert events[0]["completed"] and events[0]["status"] == verdict
    assert events[0]["departure_time"] > 8
    assert events[0]["final_level"] == pytest.approx(level, abs=.002)
    assert tracker.current()["level"] is None


def test_low_current_bottle_is_not_a_final_underfill():
    tracker = OnlineCycles(CALIBRATION, .72803, 24)
    for i in range(7*24):
        assert tracker.update(i/24, [instance(i/24, .45)], 0, i) == []
    unfinished = tracker.interrupt("network disconnected")
    assert len(unfinished) == 1 and not unfinished[0]["completed"]
    assert unfinished[0]["status"] == "uncertain" and unfinished[0]["final_level"] is None


def test_frame_gap_does_not_establish_departure_or_zero_height():
    tracker = OnlineCycles(CALIBRATION, .72803, 24)
    for i in range(7*24):
        tracker.update(i/24, [instance(i/24, .45)], 0, i)
    events = tracker.update(8., [], 0, 999)
    assert events and not events[0]["completed"]
    assert tracker.current(8.)["level"] is None


def test_queue_keeps_latest_complete_packet_and_reports_drop():
    frames = queue.Queue(maxsize=2)
    assert put_latest(frames, {"frame": 1}) == 0
    assert put_latest(frames, {"frame": 2}) == 0
    assert put_latest(frames, {"frame": 3}) == 1
    assert frames.get()["frame"] == 2
    assert frames.get()["frame"] == 3


def test_decoder_preserves_actual_pts_and_creates_reset_epoch(monkeypatch):
    stop, frames = threading.Event(), queue.Queue(maxsize=8)
    notifications = []
    class Decoded:
        width, height, time_base = 16, 16, Fraction(1, 90)
        def __init__(self, pts): self.pts = pts
        def to_ndarray(self, format): return np.zeros((16,16,3), np.uint8)
    class Container:
        streams = [SimpleNamespace(type="video")]
        def decode(self, stream):
            for pts in [90, 93, 93, 87]: yield Decoded(pts)
            stop.set()
        def close(self): pass
    calls = []
    def open_input(url, **kwargs):
        calls.append(kwargs); return Container()
    monkeypatch.setitem(sys.modules, "av", SimpleNamespace(open=open_input))
    read_stream({"url":"rtsp://allowed/source","width":16,"height":16}, frames, stop,
                lambda kind, detail: notifications.append((kind, detail)))
    packets = [frames.get() for _ in range(frames.qsize())]
    assert [p["pts"] for p in packets] == [90,93,87]
    assert packets[1]["t"] == pytest.approx(3/90)
    assert packets[2]["connection_epoch"] == 2 and packets[2]["t"] == 0
    assert any(kind == "duplicate_pts" for kind, _ in notifications)
    assert calls[0]["options"]["rtsp_transport"] == "tcp"


class FakeEngine:
    calibration = CALIBRATION
    def load(self): pass
    def identity(self):
        return {"engine":"rfdetr", "algorithm":"rfdetr-live-cycle-v1",
                "models": {k:{"checkpoint_sha256":v} for k,v in CALIBRATION["model_hashes"].items()},
                "recorded_pipeline_sha256":"a"*64, "live_pipeline_sha256":"b"*64}


@pytest.fixture
def manager(tmp_path):
    source = {"url":"rtsp://server/private","name":"Approved source","width":1280,"height":720,"fps":24,
              "profile_id":"sammy-single-station","reference_level":.72803,"tolerance":.05,
              "calibration_basis":"Reviewed fixed-camera demo"}
    path = tmp_path/"streams.json"
    path.write_text(json.dumps({"schema_version":1,"streams":{STREAM:source}}))
    live = LiveManager(FakeEngine(), path, EventStore(tmp_path/"state.sqlite"),
                       reader=lambda source, frames, stop, notify: stop.wait())
    live.load()
    yield live
    if live.session: live.stop(live.session["session_id"])


def test_request_id_is_idempotent_and_different_session_conflicts(manager):
    first = manager.start(STREAM, "test-request")
    second = manager.start(STREAM, "test-request")
    assert first["session_id"] == second["session_id"]
    with pytest.raises(HTTPException) as error: manager.start(STREAM, "different-request")
    assert error.value.status_code == 409
    stopped = manager.stop(first["session_id"])
    assert stopped["status"] == "stopped"
    assert manager.start(STREAM, "test-request")["session_id"] == first["session_id"]
    assert manager.status(first["session_id"])["clock"]["verified"] is False


def test_persistent_events_idempotent_pagination_and_restart(tmp_path):
    path = tmp_path/"state.sqlite"
    store = EventStore(path)
    store.save_session({"session_id":"s","stream_id":"v","status":"running"})
    for n in range(3):
        event={"id":str(n),"session_id":"s","stream_id":"v"}
        store.append(event); store.append(event)
    first = store.events("s", 0, 2)
    rest = store.events("s", first[-1]["seq"], 2)
    assert [row["id"] for row in first+rest] == ["0","1","2"]
    assert EventStore(path).get_session("s")["status"] == "interrupted"


def test_allowlist_and_client_schema_reject_arbitrary_input(manager):
    with pytest.raises(ValueError): source_config(manager.allowlist, str(uuid4()), CALIBRATION)
    with TestClient(create_app(manager)) as client:
        result = client.post("/live/start", json={"stream_id":STREAM,"url":"file:///recording.mp4"})
        assert result.status_code == 422
        idle = client.get("/live/status").json()
        assert idle["type"] == "status" and idle["session_id"] is None
        assert idle["clock"]["verified"] is False


def test_atomic_preview_does_not_mix_new_geometry(manager, monkeypatch):
    session = manager.start(STREAM, "preview")
    packet = {"session_id":session["session_id"],"stream_id":STREAM,"frame_id":1,
              "width":1280,"height":720,"received_monotonic":time.monotonic(),
              "instances":[{"id":"old-geometry"}],"image":np.zeros((720,1280,3),np.uint8)}
    manager.latest = packet
    original = __import__("cv2").imencode
    def changing_encoder(*args, **kwargs):
        manager.latest = {**packet,"frame_id":2,"instances":[{"id":"new-geometry"}]}
        return original(*args, **kwargs)
    monkeypatch.setattr("streaming.service.cv2.imencode", changing_encoder)
    response = manager.frame(session["session_id"])
    assert response["frame_id"] == 1 and response["instances"][0]["id"] == "old-geometry"
    assert response["jpeg_base64"] and response["preview_width"] == 960
    assert manager.encoded is None


def test_stopping_unknown_session_never_stops_current(manager):
    session = manager.start(STREAM, "stop-test")
    with pytest.raises(HTTPException) as error: manager.stop(str(uuid4()))
    assert error.value.status_code == 404
    assert not manager.stop_event.is_set()
    assert manager.status(session["session_id"])["mode"] == "live"


def test_disconnect_invalidates_queued_and_displayed_frames(manager):
    session = manager.start(STREAM, "reset-test")
    manager.frames_queue = queue.Queue(maxsize=2)
    manager.frames_queue.put({"frame_id": 1})
    manager.latest = {"session_id": session["session_id"], "frame_id": 1}
    manager._notify("disconnected", {"reason": "test"})
    assert manager.latest is None and manager.frames_queue.empty()
    status = manager.status(session["session_id"])
    assert status["status"] == "reconnecting"
    assert status["stats"]["dropped_frames"] == 1
