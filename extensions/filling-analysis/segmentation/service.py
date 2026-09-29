# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""GPU bottle/liquid inference on every recorded frame, with mask measurements."""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import subprocess
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from segmentation.measurements import measure_masks

DATA = Path(os.getenv("SEGMENTATION_DATA_DIR", "/data")).resolve()
RESULTS = Path(os.getenv("SEGMENTATION_RESULTS_DIR", "/results"))
BOTTLE_CHECKPOINT = Path(os.getenv("BOTTLE_CHECKPOINT", "/models/bottle.pth"))
LIQUID_CHECKPOINT = Path(os.getenv("LIQUID_CHECKPOINT", "/models/liquid.pth"))
SAMPLING_MODE = "every-source-frame-v2"


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def polygons(mask, offset=(0, 0), size=None):
    height, width = size or mask.shape
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    rings = []
    for contour in contours:
        if cv2.contourArea(contour) < 4:
            continue
        points = cv2.approxPolyDP(contour, 0.75, True).reshape(-1, 2)
        if len(points) >= 3:
            rings.append([[round((float(x) + offset[0]) / width, 6),
                           round((float(y) + offset[1]) / height, 6)] for x, y in points])
    return rings


def clip_liquid(mask, bottle_mask):
    if mask.shape != bottle_mask.shape:
        mask = cv2.resize(mask.astype(np.uint8), (bottle_mask.shape[1], bottle_mask.shape[0]),
                          interpolation=cv2.INTER_NEAREST).astype(bool)
    return mask.astype(bool) & bottle_mask.astype(bool)


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1, max_length=500)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_file: str = Field(min_length=1, max_length=500)
    duration: float = Field(gt=0, le=3600, allow_inf_nan=False)
    width: int = Field(gt=0, le=8192)
    height: int = Field(gt=0, le=8192)
    fps: float = Field(gt=0, le=120, allow_inf_nan=False)
    source_clock_origin: str | None = None
    stream_id: str | None = None
    force: bool = False


def source_path(job):
    path = (DATA / job.source_file).resolve()
    if not path.is_relative_to(DATA) or not path.is_file() or path.suffix != ".mp4":
        raise HTTPException(422, "Source must be an existing MP4 under the shared VIOS data directory")
    if path.name != job.source_sha256 + ".mp4":
        raise HTTPException(422, "Source filename does not match its requested content hash")
    return path


class Worker:
    def __init__(self):
        self.lock = threading.RLock()
        self.states = {}
        self.results = {}
        self.models = None
        self.bottle = None
        self.liquid = None
        self.ready = False
        self.loading_error = None
        self.active = None
        self.code_hash = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(Path(__file__).parent.rglob("*.py")))).hexdigest()

    def load(self):
        try:
            import torch
            torch.set_num_threads(4)
            cv2.setNumThreads(4)
            from segmentation.bottle.segmenter import BottleSegmenter
            module, klass = os.getenv("LIQUID_SEGMENTER_CLASS", "segmentation.liquid.inference.LiquidSegmenter").rsplit(".", 1)
            LiquidSegmenter = getattr(importlib.import_module(module), klass)
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA is required; no CPU or color-threshold fallback is enabled")
            self.bottle = BottleSegmenter(checkpoint=BOTTLE_CHECKPOINT, device="cuda", threshold=0.40,
                                          max_side=1280, memory_fraction=0.20)
            self.liquid = LiquidSegmenter(checkpoint_path=LIQUID_CHECKPOINT, device="cuda:0")
            self.models = {
                "bottle": {"name": "RF-DETR-Seg 2XLarge", "checkpoint_sha256": sha(BOTTLE_CHECKPOINT),
                           "training": "COCO-pretrained instance segmentation", "class": "bottle"},
                "liquid": {"name": "RF-DETR-Seg Nano · liquid", "checkpoint_sha256": sha(LIQUID_CHECKPOINT),
                           "training": "Source-adapted fine-tuning with weak labels; see model validation", "class": "visible_liquid"},
            }
            self.ready = True
        except Exception as exc:
            self.loading_error = f"{type(exc).__name__}: {str(exc)[:300]}"

    def key(self, source_id, source_sha256, stream_id=None, source_clock_origin=None):
        return hashlib.sha256(json.dumps({"source_id": source_id, "source_sha256": source_sha256,
            "models": self.models, "pipeline_sha256": self.code_hash, "sampling_mode": SAMPLING_MODE,
            "stream_id": stream_id, "source_clock_origin": source_clock_origin}, sort_keys=True).encode()).hexdigest()

    def state(self, source_id, source_sha256, stream_id=None, source_clock_origin=None):
        key = self.key(source_id, source_sha256, stream_id, source_clock_origin)
        with self.lock:
            if key not in self.states and self.ready:
                path = RESULTS / (key + ".json")
                if path.is_file():
                    try:
                        result = json.loads(path.read_text())
                        if (result.get("schema_version") != 2 or result["source_id"] != source_id or result["source_sha256"] != source_sha256
                                or result["models"] != self.models or result["provenance"]["cache_key"] != key):
                            raise ValueError("Stale segmentation cache")
                        self.results[key] = result
                        self.states[key] = {"status": "complete", "progress": 1, "cached": True}
                    except (ValueError, KeyError, OSError):
                        pass
            status = dict(self.states.get(key, {"status": "idle" if self.ready else "unavailable", "progress": 0}))
            if not self.ready:
                status["error"] = self.loading_error or "GPU segmentation models are loading"
            return {**status, "source_id": source_id, "source_sha256": source_sha256, "models": self.models,
                    "pipeline_sha256": self.code_hash, "sampling_mode": SAMPLING_MODE,
                    "stream_id": stream_id, "source_clock_origin": source_clock_origin}

    def start(self, job):
        path = source_path(job)
        with self.lock:
            if not self.ready:
                raise HTTPException(503, self.loading_error or "GPU segmentation models are loading")
            key = self.key(job.source_id, job.source_sha256, job.stream_id, job.source_clock_origin)
            state = self.state(job.source_id, job.source_sha256, job.stream_id, job.source_clock_origin)
            if self.active:
                if self.active == key and not job.force:
                    return state
                raise HTTPException(409, "A segmentation job is already running")
            if state["status"] == "complete" and not job.force:
                return state
            self.active = key
            self.states[key] = {"status": "running", "progress": 0, "cached": False}
            threading.Thread(target=self.run, args=(key, job, path), daemon=True).start()
            return self.state(job.source_id, job.source_sha256, job.stream_id, job.source_clock_origin)

    def run(self, key, job, path):
        started = time.monotonic()
        process = None
        try:
            if sha(path) != job.source_sha256:
                raise ValueError("Source SHA-256 differs from the requested recording")
            probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate,nb_frames:format=duration", "-of", "json", str(path)]))
            stream = probe["streams"][0]
            from fractions import Fraction
            fps = float(Fraction(stream["avg_frame_rate"]))
            if (stream["width"] != job.width or stream["height"] != job.height or abs(fps - job.fps) > .001
                    or abs(float(probe["format"]["duration"]) - job.duration) > .15):
                raise ValueError("Source dimensions, clock or duration changed")
            if abs(float(Fraction(stream.get("r_frame_rate", stream["avg_frame_rate"]))) - fps) > .001:
                raise ValueError("Frame-aligned segmentation requires a constant-frame-rate recording")
            expected_frames = int(stream["nb_frames"]) if str(stream.get("nb_frames", "")).isdigit() else None
            process = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-threads", "2", "-i", str(path),
                "-vsync", "0", "-threads", "2", "-f", "rawvideo",
                "-pix_fmt", "bgr24", "pipe:1"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            samples = []
            frame_bytes = job.width * job.height * 3
            while True:
                raw = process.stdout.read(frame_bytes)
                if not raw:
                    break
                if len(raw) != frame_bytes:
                    raise ValueError("Incomplete decoded source frame")
                frame = np.frombuffer(raw, np.uint8).reshape(job.height, job.width, 3)
                frame_index = len(samples)
                t = frame_index / fps
                detections = self.bottle.predict(frame, color_order="BGR")
                instances = []
                for i, detected in enumerate(sorted(detections, key=lambda d: d.box[0])):
                    bottle_mask = detected.raw_mask.astype(bool)
                    ys, xs = np.where(bottle_mask)
                    if not len(xs):
                        continue
                    bx0, bx1 = int(xs.min()), int(xs.max()) + 1
                    by0, by1 = int(ys.min()), int(ys.max()) + 1
                    pad_x, pad_y = int((bx1-bx0)*.15), int((by1-by0)*.05)
                    x0, x1 = max(0, bx0-pad_x), min(job.width, bx1+pad_x)
                    y0, y1 = max(0, by0-pad_y), min(job.height, by1+pad_y)
                    crop_rgb = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2RGB)
                    prediction = (self.liquid.predict(crop_rgb) if min(crop_rgb.shape[:2]) >= 8 else
                                  {"mask": np.zeros(crop_rgb.shape[:2], bool), "confidence": 0., "status": "insufficient_pixels"})
                    liquid = clip_liquid(prediction["mask"], bottle_mask[y0:y1, x0:x1])
                    full_liquid = np.zeros_like(bottle_mask)
                    full_liquid[y0:y1, x0:x1] = liquid
                    measurement = measure_masks(bottle_mask, full_liquid, float(detected.confidence),
                                                float(prediction["confidence"]))
                    instances.append({"id": f"frame-{len(samples)}-instance-{i}", "box": list(detected.box),
                        "bottle_mask": detected.bottle_mask,
                        "liquid_mask": polygons(liquid, (x0, y0), (job.height, job.width)),
                        "bottle_confidence": float(detected.confidence), "liquid_confidence": float(prediction["confidence"]),
                        "liquid_status": prediction.get("status", "predicted"),
                        "liquid_area_fraction": round(float(liquid.sum() / bottle_mask.sum()), 5),
                        "measurement_kind": "RF-mask-derived visible height; not volume",
                        "measurement": measurement})
                samples.append({"frame_index": frame_index, "t": t, "instances": instances})
                with self.lock:
                    self.states[key]["progress"] = min(.99, t / job.duration)
            process.wait()
            if process.returncode:
                raise RuntimeError("Source video decoding failed")
            if (expected_frames is not None and len(samples) != expected_frames) or len(samples) < int(job.duration * fps) - 1:
                raise RuntimeError("Decoder did not cover the full source recording")
            if sha(path) != job.source_sha256:
                raise ValueError("Source changed during segmentation")
            result = {"schema_version": 2, "source_id": job.source_id, "source_sha256": job.source_sha256,
                "source_clock_origin": job.source_clock_origin, "stream_id": job.stream_id,
                "sample_fps": fps, "source_fps": fps, "source_frame_count": len(samples),
                "width": job.width, "height": job.height, "duration": job.duration,
                "models": self.models, "device": "cuda", "runtime_seconds": round(time.monotonic()-started, 3),
                "samples": samples, "provenance": {"mode": "analyzed-replay", "cache_key": key,
                    "pipeline_sha256": self.code_hash, "source_clock_origin": job.source_clock_origin, "stream_id": job.stream_id,
                    "liquid_training": "source-adapted weak supervision", "containment": "liquid mask intersected with predicted bottle mask",
                    "sampling_mode": SAMPLING_MODE, "identity": "per-frame instances; inspection associates stationary cycles",
                    "fill_measurements": "derived from predicted bottle and liquid masks"}}
            RESULTS.mkdir(parents=True, exist_ok=True)
            target = RESULTS / (key + ".json")
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(result, allow_nan=False, separators=(",", ":")))
            os.replace(temporary, target)
            with self.lock:
                self.results[key] = result
                self.states[key].update(status="complete", progress=1, runtime_seconds=result["runtime_seconds"])
        except Exception as exc:
            with self.lock:
                self.states[key].update(status="error", error=f"{type(exc).__name__}: {str(exc)[:300]}")
        finally:
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            with self.lock:
                self.active = None


worker = Worker()
app = FastAPI(title="VSS RF-DETR bottle and liquid segmentation", version="1.0")


@app.on_event("startup")
def startup():
    threading.Thread(target=worker.load, daemon=True).start()


@app.get("/health")
def health():
    return {"status": "ready" if worker.ready else "error" if worker.loading_error else "loading",
            "device": "cuda", "models": worker.models, "pipeline_sha256": worker.code_hash,
            "sampling_mode": SAMPLING_MODE, "error": worker.loading_error}


@app.get("/jobs")
def status(source_id: str = Query(min_length=1, max_length=500), source_sha256: str = Query(pattern=r"^[0-9a-f]{64}$"),
           stream_id: str | None = Query(default=None, max_length=100), source_clock_origin: str | None = Query(default=None, max_length=100)):
    return worker.state(source_id, source_sha256, stream_id, source_clock_origin)


@app.post("/jobs")
def start(job: Job):
    return worker.start(job)


@app.get("/jobs/result")
def result(source_id: str = Query(min_length=1, max_length=500), source_sha256: str = Query(pattern=r"^[0-9a-f]{64}$"),
           stream_id: str | None = Query(default=None, max_length=100), source_clock_origin: str | None = Query(default=None, max_length=100)):
    state = worker.state(source_id, source_sha256, stream_id, source_clock_origin)
    if state["status"] != "complete":
        raise HTTPException(409, "Matching GPU segmentation is not complete")
    return worker.results[worker.key(source_id, source_sha256, stream_id, source_clock_origin)]
