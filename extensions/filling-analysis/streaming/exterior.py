# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Independent full-decoder-rate exterior evidence, never substitute model masks."""
from __future__ import annotations

import json
import threading
from collections import deque
from fractions import Fraction
from pathlib import Path

import cv2


def exterior_pixels(frame, config):
    resized = cv2.resize(frame, tuple(config["reference_size"]), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
    orange = cv2.inRange(hsv, tuple(config["orange_hsv_lower"]), tuple(config["orange_hsv_upper"])) > 0
    return sum(int(orange[y:y+h, x:x+w].sum()) for x, y, w, h in config["boxes"])


class ExteriorHistory:
    def __init__(self, fps, config, maximum_seconds=120, maximum_previews=256):
        self.fps, self.config = fps, config
        self.lock = threading.RLock()
        self.rows = deque(maxlen=int(maximum_seconds*fps)+1)
        self.previews = {}
        self.preview_order = deque()
        self.maximum_previews = maximum_previews

    def clear(self):
        with self.lock:
            self.rows.clear()
            self.previews.clear()
            self.preview_order.clear()

    def add(self, packet):
        row = {key: packet[key] for key in (
            "connection_epoch", "frame_seq", "pts", "time_base", "pts_seconds", "t", "exterior_pixels", "received_at"
        )}
        preview = None
        if row["exterior_pixels"] >= self.config["pixels_min"] and packet.get("image") is not None:
            ok, encoded = cv2.imencode(".jpg", packet["image"], [cv2.IMWRITE_JPEG_QUALITY, 90])
            if ok:
                preview = encoded.tobytes()
        key = (row["connection_epoch"], row["frame_seq"])
        with self.lock:
            self.rows.append(row)
            if preview is not None:
                self.previews[key] = preview
                self.preview_order.append(key)
                while len(self.preview_order) > self.maximum_previews:
                    self.previews.pop(self.preview_order.popleft(), None)

    def burst(self, epoch, start, end):
        with self.lock:
            rows = [row.copy() for row in self.rows if row["connection_epoch"] == epoch and start <= row["t"] <= end]
        run = []
        threshold = Fraction(str(self.config["persistence_seconds"]))
        maximum_gap = Fraction(8, 5) / Fraction(str(self.fps))
        for row in rows:
            tick = Fraction(row["time_base"])
            instant = row["pts"] * tick
            # Coarse timestamps cannot establish this subsecond evidence gate.
            if row["exterior_pixels"] < self.config["pixels_min"] or not 0 < tick <= 1 / Fraction(str(self.fps)):
                run = []
                continue
            if run:
                previous = run[-1]
                delta = instant - previous["pts"] * Fraction(previous["time_base"])
                if delta <= 0 or delta > maximum_gap:
                    run = []
            run.append(row)
            first = run[0]
            span = instant - first["pts"] * Fraction(first["time_base"])
            tolerance = max(tick, Fraction(first["time_base"]))
            # Endpoint quantization tolerance is at most one actual PTS clock tick.
            if span + tolerance >= threshold:
                return {"engine": "calibrated-exterior-color-signal-v1", "start": first["t"], "end": row["t"],
                        "start_pts_seconds": first["pts_seconds"], "end_pts_seconds": row["pts_seconds"],
                        "observed_span_seconds": float(span), "required_span_seconds": float(threshold),
                        "timestamp_tolerance_seconds": float(tolerance), "time_base": row["time_base"],
                        "observation_count": len(run), "minimum_exterior_pixels": min(x["exterior_pixels"] for x in run),
                        "sampling": "every-decoded-frame-before-GPU-queue", "connection_epoch": epoch,
                        "frames": [{key: x[key] for key in ("frame_seq", "pts", "time_base", "pts_seconds", "t", "exterior_pixels", "received_at")} for x in run]}
        return None

    def save_diagnostic(self, burst, directory: Path):
        """Save only the actual qualified burst, if its bounded diagnostic cache still has it."""
        with self.lock:
            frames = [(row, self.previews.get((burst["connection_epoch"], row["frame_seq"]))) for row in burst["frames"]]
        if not frames or any(data is None for _, data in frames):
            return {"available": False, "reason": "Diagnostic frames aged out of the bounded preview cache"}
        directory.mkdir(parents=True, exist_ok=True)
        for row, data in frames:
            (directory / f"frame-{row['frame_seq']}.jpg").write_bytes(data)
        (directory / "provenance.json").write_text(json.dumps(burst, indent=2) + "\n")
        return {"available": True, "directory": str(directory), "frame_count": len(frames),
                "purpose": "Actual arriving exterior-evidence frames; diagnostic only"}
