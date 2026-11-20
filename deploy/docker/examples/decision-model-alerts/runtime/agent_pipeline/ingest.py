# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""RTVI-style REST control plane over the existing CUDA D1 model adapter.

The first capture backend reuses the verified Jetson H.264 GPU-decoding bridge.
It is not an nv.Frame object detector or a zero-copy model adapter.
"""
import base64
import io
import logging
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import JSONResponse, Response

from .bus import DECISIONS, drain_outbox
from .contracts import StreamConfig
from .store import Store

LOG = logging.getLogger(__name__)
VIDEO_MAX_EDGE = 448


def prepare_input(cap, config, now, wall_now):
    """Build model input from fresh real frames and retain their time provenance."""
    mode = config.get("input_mode", "image")
    if mode == "video":
        window, reason = cap.sample_window(config.get("video_frames", 8), config.get("video_fps", 1), now)
        if window is None:
            return None, reason
        from PIL import Image
        encoded = []
        for data in window["frames"]:
            with Image.open(io.BytesIO(data)) as frame:
                frame = frame.convert("RGB")
                frame.thumbnail((VIDEO_MAX_EDGE, VIDEO_MAX_EDGE), Image.Resampling.LANCZOS)
                buffer = io.BytesIO()
                frame.save(buffer, format="JPEG", quality=85)
                encoded.append(base64.b64encode(buffer.getvalue()).decode())
        input_body = {"video": {"frames_base64": encoded, "fps": window["fps"]}}
        times, sequences = window["times"], window["sequences"]
        metadata = {"mode": "video", "frame_count": len(times), "fps": window["fps"],
                    "requested_fps": config.get("video_fps", 1), "frame_max_edge": VIDEO_MAX_EDGE}
    else:
        with cap.lock:
            frame, seq, captured = cap.frame, cap.seq, cap.frame_time
        if frame is None or now - captured > 5:
            return None, "Waiting for a fresh image"
        input_body = {"image_base64": base64.b64encode(frame).decode()}
        times, sequences = [captured], [seq]
        metadata = {"mode": "image", "frame_count": 1}
    timestamps = [wall_now - (now - captured) for captured in times]
    metadata.update(window_start=timestamps[0], window_end=timestamps[-1],
                    frame_timestamps=timestamps, frame_sequences=sequences,
                    timestamp_source="host_frame_arrival")
    return {"body": input_body, "input": metadata, "seq": sequences[-1],
            "captured_at": timestamps[-1]}, None


def response_provenance(result, client, endpoint):
    """Use the loaded server's identity, never a stale local lock or model label."""
    model = result.get("model")
    if not isinstance(model, dict):
        # Compatibility with the original image-only adapter during migration.
        response = client.get(endpoint + "/health")
        response.raise_for_status()
        model = response.json().get("model")
    if not isinstance(model, dict) or any(not isinstance(model.get(field), str) or not model[field]
                                          for field in ("model_id", "revision")):
        raise ValueError("D1 runtime did not provide model provenance")
    return model["model_id"], model["revision"]


class Runtime:
    def __init__(self, store, capture_factory=None):
        self.store = store
        self.stop = threading.Event()
        self.captures = {}
        self.errors = {}
        self.last_seq = {}
        self.next_due = {}
        self.capture_factory = capture_factory

    def run(self):
        if self.capture_factory is None:
            from .capture import Capture
            self.capture_factory = Capture
        endpoint = os.getenv("D1_MODEL_URL", "http://127.0.0.1:8001")
        with httpx.Client(timeout=90) as client:
            while not self.stop.wait(0.1):
                configs = self.store.configs("stream")
                active = {c["id"] for c in configs if c["config"]["enabled"]}
                for key in list(self.captures):
                    if key not in active:
                        self.captures.pop(key).stop.set()
                        self.last_seq.pop(key, None)
                        self.next_due.pop(key, None)
                        self.errors.pop(key, None)
                for row in configs:
                    key, cfg = row["id"], row["config"]
                    if not cfg["enabled"]:
                        continue
                    cap = self.captures.get(key)
                    if cap is None or cap.uri != cfg["rtsp_url"]:
                        if cap:
                            cap.stop.set()
                        cap = self.capture_factory(cfg["rtsp_url"], endpoint)
                        self.captures[key] = cap
                        self.last_seq.pop(key, None)
                        self.next_due.pop(key, None)
                        threading.Thread(target=cap.capture, daemon=True).start()
                    now = time.monotonic()
                    if now < self.next_due.get(key, 0):
                        continue
                    # Bounded backlog: pause new inference while downstream is unavailable.
                    if self.store.rows("SELECT count(*) AS n FROM outbox")[0]["n"] >= 10000:
                        self.errors[key] = "Kafka outbox full; inference paused"
                        continue
                    prepared = None
                    try:
                        prepared, reason = prepare_input(cap, cfg, now, time.time())
                        if prepared is None:
                            self.errors[key] = reason
                            continue
                        seq = prepared["seq"]
                        if seq == self.last_seq.get(key):
                            continue
                        capture_epoch = prepared["captured_at"]
                        question = {k: v for k, v in cfg["question"].items() if v is not None}
                        response = client.post(endpoint + "/classify", json={"state": None, "camera_id": key,
                            "timestamp": capture_epoch, "questions": {"scene": question},
                            **prepared["body"]})
                        response.raise_for_status()
                        result = response.json()
                        answer = result["result"]["answers"]["scene"]
                        if not isinstance(answer, dict):
                            raise ValueError("invalid D1 answer")
                        model_id, model_revision = response_provenance(result, client, endpoint)
                        event = {"schema_version": 1, "kind": "d1.decision", "id": str(uuid.uuid4()),
                            "stream_id": key, "vios_sensor": cfg["vios_sensor"], "captured_at": capture_epoch,
                            "timestamp_source": "host_frame_arrival", "published_at": time.time(),
                            "config_revision": row["revision"], "frame_sequence": seq,
                            "question": question, "answer": answer,
                            "model": model_id, "model_revision": model_revision,
                            "input": prepared["input"],
                            "processing_ms": result.get("processing_ms"),
                            "response": result}
                        self.store.publish_later(DECISIONS, event)
                        self.errors.pop(key, None)
                    except Exception:
                        LOG.exception("D1 request failed for stream %s", key)
                        self.errors[key] = "D1 request failed; see service log"
                    if prepared is not None:
                        self.last_seq[key] = prepared["seq"]
                    self.next_due[key] = now + cfg["interval_seconds"]
        for cap in self.captures.values():
            cap.stop.set()


def create_app(store=None, start_workers=True):
    store = store or Store(os.getenv("D1_INGEST_DB", "results/agent/ingest.sqlite"))
    runtime = Runtime(store)

    @asynccontextmanager
    async def lifespan(_app):
        threads = []
        if start_workers:
            for target, args in [(runtime.run, ()), (drain_outbox, (store, runtime.stop))]:
                t = threading.Thread(target=target, args=args, daemon=True)
                t.start()
                threads.append(t)
        yield
        runtime.stop.set()
        for t in threads:
            t.join(timeout=2)

    app = FastAPI(title="D1 RT Inference", version="0.1.0", lifespan=lifespan)
    app.state.runtime = runtime

    @app.get("/v1/health/live")
    def live():
        return {"alive": True}

    @app.get("/v1/health/ready")
    def ready():
        configs = {row['id']: row['config'] for row in store.configs('stream')}
        captures = {}
        for key, capture in list(runtime.captures.items()):
            state = capture.snapshot()
            cfg = configs.get(key, {})
            mode = cfg.get('input_mode', 'image')
            state['input_mode'] = mode
            if mode == 'video':
                window, reason = capture.sample_window(cfg.get('video_frames', 8), cfg.get('video_fps', 1))
                state.update(input_ready=window is not None, input_status=reason or 'Video input window ready',
                             required_frames=cfg.get('video_frames', 8), sampled_fps=window['fps'] if window else None)
            else:
                state['input_ready'] = state['frame_age_ms'] is not None and state['frame_age_ms'] < 5000
                state['input_status'] = 'Image ready' if state['input_ready'] else 'Waiting for a fresh image'
            captures[key] = state
        backlog = store.rows("SELECT count(*) AS n FROM outbox")[0]["n"]
        expected = {key for key, cfg in configs.items() if cfg['enabled']}
        healthy = not runtime.errors and backlog < 10000 and expected.issubset(captures)
        for c in captures.values():
            healthy = healthy and c['input_ready']
        return JSONResponse(status_code=200 if healthy else 503, content={"ready": healthy,
            "streams": captures, "errors": dict(runtime.errors), "outbox_pending": backlog})

    @app.get("/v1/streams")
    def streams():
        return store.configs("stream")

    @app.get("/v1/streams/{stream_id}/preview")
    def preview(stream_id: str, revision: int | None = None):
        """Return the monitored capture's latest JPEG without starting any work."""
        headers = {"Cache-Control": "no-store"}

        def current_stream():
            row = next((row for row in store.configs("stream") if row["id"] == stream_id), None)
            if row is None:
                raise HTTPException(404, "Stream not found", headers=headers)
            if not row["config"]["enabled"]:
                raise HTTPException(409, "Stream is disabled", headers=headers)
            if revision is not None and revision != row["revision"]:
                raise HTTPException(409, "Stream configuration changed", headers=headers)
            return row

        row = current_stream()
        cap = runtime.captures.get(stream_id)
        if cap is None:
            raise HTTPException(503, "Waiting for the monitored input", headers=headers)
        if cap.uri != row["config"]["rtsp_url"] or cap.stop.is_set():
            raise HTTPException(409, "Monitored input changed", headers=headers)
        with cap.lock:
            frame, sequence, captured = cap.frame, cap.seq, cap.frame_time

        # Configuration and capture may change while waiting for the frame lock.
        # Hold the configuration lock through the final check and response build.
        with store.lock:
            current = current_stream()
            if (current["revision"] != row["revision"]
                    or current["config"]["rtsp_url"] != cap.uri
                    or runtime.captures.get(stream_id) is not cap or cap.stop.is_set()):
                raise HTTPException(409, "Monitored input changed", headers=headers)
            age = time.monotonic() - captured
            if not frame or not 0 <= age <= 2:
                raise HTTPException(503, "Waiting for a fresh input frame", headers=headers)
            headers.update({"X-Frame-Sequence": str(sequence),
                            "X-Frame-Captured-At": str(time.time() - age),
                            "X-Stream-Revision": str(current["revision"])})
            return Response(content=frame, media_type="image/jpeg", headers=headers)

    @app.put("/v1/streams/{stream_id}")
    def configure(stream_id: str, config: StreamConfig, if_match: int | None = Header(default=None)):
        import re
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", stream_id):
            raise HTTPException(422, "Invalid stream ID")
        with store.lock:
            if if_match is not None:
                current = next((s for s in store.configs("stream") if s["id"] == stream_id), None)
                if not current or current["revision"] != if_match:
                    raise HTTPException(409, "Stream configuration changed")
            return {"id": stream_id, "revision": store.put_config("stream", stream_id, config.model_dump()), "config": config}

    @app.patch("/v1/streams/{stream_id}")
    def update(stream_id: str, changes: dict):
        current = next((r for r in store.configs("stream") if r["id"] == stream_id), None)
        if current is None:
            raise HTTPException(404, "Stream not found")
        from pydantic import ValidationError
        try:
            config = StreamConfig.model_validate({**current["config"], **changes})
        except ValidationError:
            raise HTTPException(422, "Invalid stream configuration")
        return configure(stream_id, config, None)

    @app.delete("/v1/streams/{stream_id}")
    def remove(stream_id: str):
        with store.transaction() as db:
            db.execute("DELETE FROM config WHERE kind='stream' AND id=?", (stream_id,))
        return {"removed": stream_id}

    return app
