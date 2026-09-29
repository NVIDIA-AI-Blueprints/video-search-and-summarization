# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Live RF-DETR sessions; no prerecorded video, analysis cache, or seeded events."""
from __future__ import annotations

import base64
import copy
import os
import queue
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import cv2
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from .config import public_source, source_config
from .decoder import read_stream, utc_now
from .exterior import ExteriorHistory
from .evidence import snapshots, public_snapshots
from .inference import FrameEngine
from .online import OnlineCycles
from .storage import EventStore


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stream_id: UUID
    request_id: str | None = Field(default=None, min_length=1, max_length=100)


class StopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID


def estimated_utc(epoch_origin, seconds):
    return (datetime.fromisoformat(epoch_origin) + timedelta(seconds=seconds)).isoformat()


class LiveManager:
    def __init__(self, engine, allowlist, store, reader=read_stream):
        self.engine, self.allowlist, self.store, self.reader = engine, Path(allowlist), store, reader
        self.lock = threading.RLock()
        self.ready, self.load_error = False, None
        self.session = self.thread = self.stop_event = None
        self.latest = self.encoded = None
        self.frames_queue = None
        self.exterior_history = None
        self.discontinuity = 0
        self.queue_size = max(1, min(16, int(os.getenv("LIVE_FRAME_QUEUE_SIZE", "4"))))
        self.maximum_age = max(.1, min(5., float(os.getenv("LIVE_MAX_QUEUE_AGE_SECONDS", ".5"))))

    def load(self):
        try:
            self.engine.load()
            self.ready = True
        except Exception as exc:
            self.load_error = type(exc).__name__ + ": CUDA models could not be loaded or validated"

    def start(self, stream_id, request_id=None):
        with self.lock:
            prior = self.store.find_request(request_id)
            if prior:
                if prior["stream_id"] != stream_id:
                    raise HTTPException(409, "Request ID already belongs to another stream")
                return self.status(prior["session_id"])
            if not self.ready:
                raise HTTPException(503, self.load_error or "Live CUDA models are loading")
            if self.thread and self.thread.is_alive():
                if self.session["stream_id"] == stream_id and (not request_id or request_id == self.session.get("request_id")):
                    return self.status(self.session["session_id"])
                raise HTTPException(409, "One live inspection session is already using the resident GPU models")
            try:
                source = source_config(self.allowlist, stream_id, self.engine.calibration)
            except (ValueError, OSError, KeyError) as exc:
                raise HTTPException(422, "Registered stream/profile is unavailable or invalid") from exc
            session_id = str(uuid4())
            self.session = {"session_id": session_id, "stream_id": stream_id, "request_id": request_id,
                "mode": "live", "status": "connecting", "started_at": utc_now(), "ended_at": None,
                "source": public_source(source), "identity": self.engine.identity(), "connection_epoch": 0,
                "clock": {"method": "receiver-utc-estimate", "verified": False, "capture_utc_verified": False,
                          "qualification": "PTS mapped to first decoded frame receipt; network/camera delay is not measured"},
                "summary": {"total": 0, "normal": 0, "underfill": 0, "overflow": 0, "uncertain": 0, "incomplete": 0},
                "stats": {"decoded_frames": 0, "processed_frames": 0, "dropped_queue_frames": 0,
                          "skipped_stale_frames": 0, "missing_pts_frames": 0, "duplicate_pts_frames": 0,
                          "discarded_discontinuity_frames": 0,
                          "reconnects": 0, "pts_resets": 0, "timestamp_gaps": 0,
                          "queue_capacity": self.queue_size, "queue_depth": 0, "maximum_queue_age_seconds": self.maximum_age},
                "last_event_seq": 0, "measurement_history": [], "cycle_number": 0}
            self.latest = self.encoded = None
            self.discontinuity = 0
            self.stop_event = threading.Event()
            self.store.save_session(self.session)
            self.thread = threading.Thread(target=self._run, args=(source, self.stop_event), daemon=True)
            self.thread.start()
            return self.status(session_id)

    def resume(self, session_id):
        """Explicit continuation after a controlled stop; old evidence remains immutable."""
        with self.lock:
            if self.thread and self.thread.is_alive():
                if self.session['session_id'] == session_id:
                    return self.status(session_id)
                raise HTTPException(409, 'Another live session is running')
            if not self.ready:
                raise HTTPException(503, 'Live CUDA models are not ready')
            state = self.store.get_session(session_id)
            if state is None:
                raise HTTPException(404, 'Unknown live inspection session')
            if state['status'] not in {'stopped', 'interrupted', 'error'}:
                raise HTTPException(409, 'Session must be explicitly stopped or interrupted before resume')
            try:
                source = source_config(self.allowlist, state['stream_id'], self.engine.calibration)
            except (ValueError, OSError, KeyError) as exc:
                raise HTTPException(422, 'Registered stream/profile is unavailable or invalid') from exc
            history, maximum_cycle = self.store.history(session_id)
            epochs = {entry['epoch']: entry for entry in history}
            for entry in state.get('measurement_history', []):
                if entry['epoch'] in epochs and epochs[entry['epoch']] != entry:
                    raise HTTPException(409, 'Historical epoch identity differs from persisted events')
                epochs[entry['epoch']] = entry
            old_epoch = state.get('connection_epoch', 0)
            if old_epoch > 0 and old_epoch not in epochs:
                epochs[old_epoch] = {'epoch': old_epoch, 'measurement': self._measurement(state), 'clock': state['clock']}
            source['epoch_offset'] = max(epochs, default=old_epoch)
            state.update(status='connecting', ended_at=None, error=None, current={},
                         identity=self.engine.identity(), source=public_source(source),
                         measurement_history=[epochs[x] for x in sorted(epochs)],
                         cycle_number=max(maximum_cycle, state.get('cycle_number', 0)), resumed_at=utc_now(),
                         clock={'method':'receiver-utc-estimate', 'verified':False, 'capture_utc_verified':False,
                                'qualification':'New connection epoch after resume; PTS mapped to first frame receipt, not capture UTC'})
            # Reserve the new epoch before exposing the new identity in status.
            # Its decoder starts empty, and no frame/event is claimed yet.
            state['connection_epoch'] = source['epoch_offset']+1
            for name in ('last_frame_id', 'last_frame_received_at'):
                state.pop(name, None)
            self.session = state
            self._remember_epoch(state['connection_epoch'])
            self.latest = self.encoded = None
            self.discontinuity = 0
            self.stop_event = threading.Event()
            self.store.save_session(state)
            self.thread = threading.Thread(target=self._run, args=(source, self.stop_event), daemon=True)
            self.thread.start()
            return self.status(session_id)

    @staticmethod
    def _measurement(state):
        identity, source = state.get("identity") or {}, state.get("source") or {}
        return {"engine": "rfdetr", "algorithm": "rfdetr-live-cycle-v1",
                "model_hashes": {name: item["checkpoint_sha256"] for name, item in (identity.get("models") or {}).items()},
                "pipeline_sha256": identity.get("live_pipeline_sha256"),
                "recorded_pipeline_sha256": identity.get("recorded_pipeline_sha256"),
                "calibration_sha256": source.get("calibration_sha256"),
                "reference_level": source.get("reference_level"), "tolerance": source.get("tolerance"),
                "overflow_engine": "calibrated-exterior-color-signal-v1",
                "level_units": "fraction of detected visible bottle height; not volume or fraction of reference"}

    @classmethod
    def _status_envelope(cls, state):
        state = copy.deepcopy(state)
        stats = state.get("stats", {})
        stats.update(received_frames=stats.get("decoded_frames", 0),
                     dropped_frames=stats.get("dropped_queue_frames", 0)+stats.get("skipped_stale_frames", 0)+stats.get("discarded_discontinuity_frames", 0),
                     inference_fps=stats.get("recent_processed_fps"), source_fps=state.get("source", {}).get("fps"),
                     queue_age_ms=stats.get("queue_age_ms"), processing_ms=stats.get("inference_ms"),
                     last_frame_at_utc=state.get("last_frame_received_at"))
        state.update(type="status", epoch=state.get("connection_epoch", 0), stats=stats,
                     measurement=cls._measurement(state), error=state.get("error"), current=state.get("current", {}),
                     measurement_history=state.get('measurement_history', []))
        if state["status"] in {"starting", "loading"}:
            state["status"] = "connecting"
        elif state["status"] == "stopping":
            state.update(status="running", stop_requested=True)
        elif state["status"] == "interrupted":
            state["status"] = "error"
        return state

    def _notify(self, kind, values):
        if kind == "exterior_frame":
            self.exterior_history.add(values)
            return
        with self.lock:
            stats = self.session["stats"]
            if kind == "decoded":
                stats["decoded_frames"] += 1
                stats["dropped_queue_frames"] += values["dropped"]
            elif kind == "connected":
                self.session.update(status="running", connection_epoch=values["connection_epoch"])
                stats["reconnects"] = max(stats["reconnects"], values["connection_epoch"] - 1)
                self._remember_epoch(values['connection_epoch'])
            elif kind == "disconnected":
                self.session.update(status="reconnecting", connection_error=values["reason"])
                self.discontinuity += 1
            elif kind in {"missing_pts", "duplicate_pts"}:
                stats[kind + "_frames"] += 1
            elif kind == "pts_reset":
                stats["pts_resets"] += 1
                self.discontinuity += 1
                self._remember_epoch(values['connection_epoch'])
            if kind in {"disconnected", "pts_reset"}:
                if self.exterior_history is not None:
                    self.exterior_history.clear()
                self.latest = self.encoded = None
                if self.frames_queue is not None:
                    while True:
                        try:
                            self.frames_queue.get_nowait()
                            stats["discarded_discontinuity_frames"] += 1
                        except queue.Empty:
                            break

    def _remember_epoch(self, epoch):
        history = self.session.setdefault('measurement_history', [])
        if not any(entry['epoch'] == epoch for entry in history):
            history.append({'epoch': epoch, 'measurement': self._measurement(self.session),
                            'clock': copy.deepcopy(self.session['clock'])})

    def _append_cycles(self, cycles, epoch):
        if epoch is None:
            return
        for cycle in cycles:
            with self.lock:
                sid, stream = self.session["session_id"], self.session["stream_id"]
                cycle["id"] = f"{sid}:epoch-{epoch['connection_epoch']}:{cycle['id']}"
                kind = "cycle.finalized" if cycle["completed"] else "cycle.incomplete"
                verdict = cycle["status"] if cycle["completed"] else "incomplete"
                instant = cycle.get("overflow_time") if cycle.get("overflow_time") is not None else cycle.get("measurement_time")
                if instant is None:
                    instant = cycle["end_time"]
                event = {"id": cycle["id"] + ":inspection", "event_id": cycle["id"] + ":inspection",
                    "type": "cycle_inspection", "mode": "live", "kind": kind, "status": verdict,
                    "session_id": sid, "stream_id": stream, "connection_epoch": epoch["connection_epoch"], "epoch": epoch["connection_epoch"],
                    "track_id": cycle["id"], "source_pts_seconds": epoch["epoch_pts_origin"] + instant,
                    "start_pts_seconds": epoch["epoch_pts_origin"] + cycle["start_time"],
                    "end_pts_seconds": epoch["epoch_pts_origin"] + cycle["end_time"],
                    "occurred_at_utc": estimated_utc(epoch["epoch_receiver_utc"], instant),
                    "final_level": cycle["final_level"], "reference_level": cycle["reference_level"], "reason": cycle["reason"],
                    "measurement": self._measurement(self.session), "clock": copy.deepcopy(self.session["clock"]),
                    "evidence_status": "clock_verification_required", "video_evidences": [],
                    "created_at": utc_now(), "cycle": cycle,
                    "time": {"start_pts_seconds": epoch["epoch_pts_origin"] + cycle["start_time"],
                             "end_pts_seconds": epoch["epoch_pts_origin"] + cycle["end_time"],
                             "estimated_start_utc": estimated_utc(epoch["epoch_receiver_utc"], cycle["start_time"]),
                             "estimated_end_utc": estimated_utc(epoch["epoch_receiver_utc"], cycle["end_time"]),
                             "method": "receiver-utc-estimate", "capture_utc_verified": False},
                    "identity": self.session["identity"], "calibration_sha256": self.session["source"]["calibration_sha256"],
                    "evidence": {"available": False, "status": "clock_verification_required",
                                 "reason": "Receiver-estimated UTC has not been verified against VIOS recording time"}}
                if cycle.get("overflow_evidence", {}).get("sampling") == "every-decoded-frame-before-GPU-queue":
                    diagnostic_root = Path(os.getenv("LIVE_DIAGNOSTICS_DIR", "/state/diagnostics"))
                    event["diagnostic"] = self.exterior_history.save_diagnostic(
                        cycle["overflow_evidence"], diagnostic_root / sid / f"epoch-{epoch['connection_epoch']}" / cycle["id"].rsplit(":",1)[-1])
                saved = self.store.append(event)
                self.session["last_event_seq"] = saved["seq"]
                self.session["summary"][verdict] += 1
                self.session["summary"]["total"] += int(cycle["completed"])
                self.store.save_session(self.session)

    def _run(self, source, stop):
        frames = queue.Queue(maxsize=self.queue_size)
        self.frames_queue = frames
        self.exterior_history = ExteriorHistory(source["fps"], source["calibration"]["exterior"])
        reader = threading.Thread(target=self.reader, args=(source, frames, stop, self._notify), daemon=True)
        tracker = OnlineCycles(source["calibration"], source["reference_level"], source["fps"],
                               cycle_number=self.session.get('cycle_number', 0))
        epoch = None
        observed_discontinuity = 0
        last_pts = None
        processed_times = deque(maxlen=240)
        persisted = time.monotonic()
        reader.start()
        try:
            while not stop.is_set():
                with self.lock:
                    discontinuity = self.discontinuity
                if discontinuity != observed_discontinuity:
                    self._append_cycles(tracker.interrupt("decoder disconnected or PTS reset"), epoch)
                    observed_discontinuity = discontinuity
                    epoch, last_pts = None, None
                try:
                    packet = frames.get(timeout=.25)
                except queue.Empty:
                    continue
                age = time.monotonic() - packet["received_monotonic"]
                if age > self.maximum_age:
                    with self.lock:
                        self.session["stats"]["skipped_stale_frames"] += 1
                    continue
                if epoch is None or epoch["connection_epoch"] != packet["connection_epoch"]:
                    self._append_cycles(tracker.interrupt("connection epoch changed"), epoch)
                    tracker = OnlineCycles(source["calibration"], source["reference_level"], source["fps"],
                                           exterior_history=self.exterior_history, epoch=packet["connection_epoch"],
                                           cycle_number=tracker.cycle_number)
                    epoch, last_pts = packet, None
                if last_pts is not None and packet["pts_seconds"] - last_pts > .5:
                    with self.lock:
                        self.session["stats"]["timestamp_gaps"] += 1
                last_pts = packet["pts_seconds"]
                started = time.monotonic()
                predicted = self.engine.predict(packet["image"], source["calibration"]["exterior"])
                done = time.monotonic()
                with self.lock:
                    if self.discontinuity != observed_discontinuity:
                        # Do not republish a frame invalidated by a reset while CUDA was busy.
                        self.session["stats"]["discarded_discontinuity_frames"] += 1
                        continue
                frame_id = packet["frame_seq"]
                cycles = tracker.update(packet["t"], predicted["instances"], packet.get("exterior_pixels", 0), frame_id)
                self._append_cycles(cycles, epoch)
                processed_times.append(done)
                with self.lock:
                    stats = self.session["stats"]
                    stats.update(processed_frames=stats["processed_frames"] + 1, queue_depth=frames.qsize(),
                                 inference_ms=round((done-started)*1000, 3), queue_age_ms=round(age*1000, 3),
                                 receiver_to_result_ms=round((done-packet["received_monotonic"])*1000, 3),
                                 recent_processed_fps=round((len(processed_times)-1)/(processed_times[-1]-processed_times[0]), 3)
                                 if len(processed_times)>1 else 0.)
                    self.session.update(last_frame_id=frame_id, connection_epoch=packet["connection_epoch"],
                                        last_frame_received_at=packet["received_at"], current=tracker.current(packet["t"]),
                                        cycle_number=tracker.cycle_number)
                    self.latest = {**{k: v for k, v in packet.items() if k != "image"},
                        "type": "frame", "mode": "live", "epoch": packet["connection_epoch"],
                        "source_pts_seconds": packet["pts_seconds"], "received_at_utc": packet["received_at"],
                        "inference_done_at_utc": utc_now(),
                        "session_id": self.session["session_id"], "stream_id": source["stream_id"],
                        "frame_id": frame_id, "width": source["width"], "height": source["height"],
                        "instances": predicted["instances"], "exterior_pixels": packet.get("exterior_pixels", 0),
                        "current": tracker.current(packet["t"]), "identity": self.session["identity"], "measurement": self._measurement(self.session),
                        "summary": copy.deepcopy(self.session["summary"]),
                        "measurement_history": copy.deepcopy(self.session.get('measurement_history', [])),
                        "clock": {**self.session["clock"], "estimated_frame_utc": estimated_utc(packet["epoch_receiver_utc"], packet["t"])},
                        "stats": self._status_envelope(self.session)["stats"], "image": packet["image"]}
                    self.encoded = None
                    if done-persisted >= 2.:
                        self.store.save_session(self.session)
                        persisted = done
        except Exception as exc:
            with self.lock:
                self.session.update(status="error", error=type(exc).__name__ + ": live frame processing failed")
            stop.set()
        finally:
            self._append_cycles(tracker.interrupt("session stopped"), epoch)
            stop.set()
            reader.join(timeout=9.)
            with self.lock:
                if reader.is_alive():
                    self.session.update(status="error", error="RTSP decoder did not terminate within the bounded stop interval")
                elif self.session["status"] != "error":
                    self.session["status"] = "stopped"
                self.session["ended_at"] = utc_now()
                self.session['cycle_number'] = tracker.cycle_number
                self.store.save_session(self.session)

    def status(self, session_id=None):
        with self.lock:
            if self.session and (session_id is None or session_id == self.session["session_id"]):
                state = copy.deepcopy(self.session)
                state["frame_available"] = self.latest is not None
                state["frame_age_ms"] = round((time.monotonic()-self.latest["received_monotonic"])*1000, 3) if self.latest else None
                return self._status_envelope(state)
        saved = self.store.get_session(session_id) if session_id else None
        if saved:
            return self._status_envelope(saved)
        if session_id:
            raise HTTPException(404, "Unknown live inspection session")
        return self._status_envelope({"status": "idle" if self.ready else "loading", "mode": "live", "ready": self.ready,
                "session_id": None, "stream_id": None, "error": self.load_error, "identity": self.engine.identity(),
                "clock": {"method": "receiver-utc-estimate", "verified": False},
                "summary": {"total": 0, "normal": 0, "underfill": 0, "overflow": 0, "uncertain": 0, "incomplete": 0}})

    def stop(self, session_id):
        with self.lock:
            if not self.session or self.session["session_id"] != session_id:
                return self.status(session_id)
            if self.thread and self.thread.is_alive():
                self.session["status"] = "stopping"
                self.stop_event.set()
            thread = self.thread
        if thread:
            thread.join(timeout=10.)
        return self.status(session_id)

    def frame(self, session_id):
        self.status(session_id)
        with self.lock:
            packet = self.latest if self.latest and self.latest["session_id"] == session_id else None
            encoded = self.encoded if packet and self.encoded and self.encoded["frame_id"] == packet["frame_id"] else None
        if packet is None:
            return {**self.status(session_id), "available": False, "reason": "No measured live frame is available"}
        if encoded is None:
            # Snapshot image and geometry are from one immutable packet, even if a new frame arrives while encoding.
            preview_width = min(960, packet["width"])
            preview_height = round(packet["height"]*preview_width/packet["width"])
            preview = cv2.resize(packet["image"], (preview_width, preview_height), interpolation=cv2.INTER_AREA)
            ok, jpeg = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 78])
            if not ok:
                raise HTTPException(503, "Live frame encoding failed")
            encoded = {k: v for k, v in packet.items() if k not in {"image", "received_monotonic"}}
            encoded.update(available=True, jpeg_base64=base64.b64encode(jpeg).decode(), media_type="image/jpeg",
                           preview_width=preview_width, preview_height=preview_height)
            with self.lock:
                if self.latest and self.latest["frame_id"] == encoded["frame_id"]:
                    self.encoded = encoded
        age = round((time.monotonic()-packet["received_monotonic"])*1000, 3)
        return {**encoded, "frame_age_ms": age, "stale": age > 1000}


def create_app(manager=None):
    app = FastAPI(title="VSS live RF-DETR filling inspection", version="1.0")

    @app.on_event("startup")
    def startup():
        app.state.manager = manager or LiveManager(FrameEngine(), os.getenv("LIVE_STREAMS_CONFIG", "/config/streams.json"),
                                                   EventStore(Path(os.getenv("LIVE_STATE_DB", "/state/live.sqlite3"))))
        threading.Thread(target=app.state.manager.load, daemon=True).start()

    @app.on_event("shutdown")
    def shutdown():
        live = app.state.manager
        if live.session:
            live.stop(live.session["session_id"])

    @app.get("/health")
    def health():
        live = app.state.manager
        return JSONResponse({"ready": live.ready, "error": live.load_error, "identity": live.engine.identity()},
                            status_code=200 if live.ready else 503)

    @app.post("/live/start")
    def start(request: StartRequest):
        return JSONResponse(app.state.manager.start(str(request.stream_id), request.request_id))

    @app.get("/live/status")
    def status(session_id: UUID | None = None):
        return JSONResponse(app.state.manager.status(str(session_id) if session_id else None))

    @app.post("/live/stop")
    def stop(request: StopRequest):
        return JSONResponse(app.state.manager.stop(str(request.session_id)))

    @app.post('/live/resume')
    def resume(request: StopRequest):
        return JSONResponse(app.state.manager.resume(str(request.session_id)))

    @app.get("/live/events")
    def events(session_id: UUID, after_seq: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=200), latest: bool = False):
        state = app.state.manager.status(str(session_id))
        rows = app.state.manager.store.events(str(session_id), after_seq, limit, latest=latest)
        for row in rows:
            row['snapshot_evidences'] = public_snapshots(row, os.getenv('LIVE_DIAGNOSTICS_DIR', '/state/diagnostics'))
        return JSONResponse({"session_id": str(session_id), "stream_id": state["stream_id"], "mode": "live",
                             "measurement": state["measurement"], "clock": state["clock"],
                             "measurement_history": state.get('measurement_history', []), "summary": state["summary"], "events": rows,
                             "next_seq": rows[-1]["seq"] if rows else after_seq})

    @app.get("/live/frame")
    def frame(session_id: UUID):
        return JSONResponse(app.state.manager.frame(str(session_id)))

    @app.get('/live/evidence')
    def evidence(session_id: UUID, event_id: str = Query(max_length=250), frame_id: int = Query(ge=0)):
        event = app.state.manager.store.event(str(session_id), event_id)
        candidates = snapshots(event, os.getenv('LIVE_DIAGNOSTICS_DIR', '/state/diagnostics'))
        selected = next((x for x in candidates if x['frame_id'] == frame_id), None)
        if selected is None:
            raise HTTPException(404, 'No stored diagnostic frame matches this session, event and frame')
        data = Path(selected['_path']).read_bytes()
        if not data.startswith(b'\xff\xd8'):
            raise HTTPException(404, 'Stored diagnostic is not a JPEG')
        return Response(data, media_type='image/jpeg', headers={
            'X-Live-Session-ID': str(session_id), 'X-Live-Stream-ID': selected['stream_id'],
            'X-Live-Event-ID': event_id, 'X-Live-Frame-ID': str(frame_id),
            'X-Live-Source-PTS': str(selected['source_pts_seconds']),
            'Cache-Control': 'private, max-age=3600'})

    return app


app = create_app()
