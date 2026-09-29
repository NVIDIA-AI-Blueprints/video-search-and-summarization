# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pixel-based filling-cycle inspection for the reviewed single-camera source.

Cycle boundaries come from collar movement; completion requires observed nozzle
flow followed by its absence. No expected event counts or event times are used.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .vision import _frames, file_sha256

VERSION = "1.0.0"
ALGORITHM = "single-station-pixel-cycle-v1"
CALIBRATION_PATH = Path(__file__).with_name("calibration_5min.json")


def load_calibration() -> dict:
    return json.loads(CALIBRATION_PATH.read_text())


def collar_center(hsv: np.ndarray, calibration: dict) -> float | None:
    lo, hi = calibration["collar_y_range"]
    green = cv2.inRange(hsv[lo:hi], (38, 80, 50), (93, 255, 255))
    count, _, stats, _ = cv2.connectedComponentsWithStats(green)
    candidates = []
    for x, y, w, h, area in stats[1:count]:
        if area >= 100 and w >= 15 and h >= 5:
            candidates.append((int(area), float(x + w / 2)))
    return max(candidates)[1] if candidates else None


def measure_frame(frame: np.ndarray, calibration: dict) -> dict:
    """Measurements stay independent of time, known labels and cycle count."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    orange = cv2.inRange(hsv, tuple(calibration["orange_hsv_lower"]),
                         tuple(calibration["orange_hsv_upper"])) > 0
    center = collar_center(hsv, calibration)
    aligned = center is not None and abs(center - calibration["station_x"]) <= calibration["station_tolerance_px"]
    x, y, w, h = calibration["flow_box"]
    flow_score = float(orange[y:y+h, x:x+w].mean())
    overflow_pixels = sum(int(orange[y:y+h, x:x+w].sum())
                          for x, y, w, h in calibration["exterior_boxes"])
    result = {"center": center, "aligned": aligned, "flow": flow_score >= calibration["flow_fraction_min"],
              "flow_score": round(flow_score, 4), "overflow_pixels": overflow_pixels,
              "overflow_visible": bool(aligned and overflow_pixels >= calibration["overflow_pixels_min"]),
              "level": None, "confidence": 0.0, "surface": [], "mask": []}
    if not aligned:
        return result
    x0, x1 = [int(round(center + dx)) for dx in calibration["interior_x_offsets"]]
    y0, y1 = calibration["interior_y_range"]
    strip = orange[y0:y1, x0:x1]
    # A central filling jet is narrow. Require orange across >55% of each row
    # to identify a surface, instead of measuring the nozzle stream as fill.
    fractions = np.convolve(strip.mean(axis=1), np.ones(5)/5, mode="same")
    liquid_rows = fractions >= .55
    # Fit clear above / liquid below to suppress diagonal bottle highlights.
    costs = np.r_[0., np.cumsum(fractions)] + np.r_[np.cumsum((1.-fractions)[::-1])[::-1], 0.]
    split = int(np.argmin(costs))
    error = float(costs[split] / len(fractions))
    level = float(np.clip(1 - split / len(fractions), 0, 1))
    if error > .25:
        return result
    if not np.any(liquid_rows):
        level, split = 0.0, len(fractions)
    result.update(level=round(level, 4), confidence=round(max(0., 1.-error*2), 3))
    height, width = frame.shape[:2]
    result["surface"] = [[x0/width, (y0+split)/height], [x1/width, (y0+split)/height]]
    binary = strip.astype(np.uint8)*255
    binary[:max(0, split-2)] = 0
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        contour = max(contours, key=cv2.contourArea)
        contour = cv2.approxPolyDP(contour, 1.5, True).reshape(-1, 2)
        result["mask"] = [[(x0+float(px))/width, (y0+float(py))/height] for px, py in contour]
    return result


def inspect_cycle(observations: list[dict], cycle_number: int, calibration: dict) -> dict:
    """No underfill verdict until flow has stopped and a stable level exists."""
    cid = f"cycle-{cycle_number:02d}"
    reference, tolerance = calibration["reference_level"], calibration["tolerance"]
    flow = [o for o in observations if o["flow"]]
    stopped = [o for o in observations if flow and o["t"] > flow[-1]["t"] and not o["flow"]]
    start, end = observations[0]["t"], observations[-1]["t"]
    result = {"id": cid, "label": f"Bottle {cycle_number}", "start_time": start, "end_time": end,
              "fill_start_time": flow[0]["t"] if flow else None,
              "fill_end_time": stopped[0]["t"] if stopped else None,
              "measurement_time": None, "final_level": None, "status": "uncertain", "confidence": 0.,
              "reason": "No completed filling cycle could be established from visible nozzle flow and a settled level.",
              "evidence_start": start, "evidence_end": end,
              "reference_level": reference, "tolerance": tolerance, "overflow_time": None}
    if not flow:
        return result
    # Require consecutive exterior-liquid observations. A full bottle alone is
    # never an overflow; these pixels lie outside its calibrated silhouette.
    exterior_run = []
    overflow_time = None
    for o in observations:
        if o["overflow_visible"]:
            exterior_run.append(o)
            if len(exterior_run) / calibration["sample_fps"] >= calibration["overflow_persistence_seconds"]:
                overflow_time = exterior_run[0]["t"]
                break
        else:
            exterior_run = []
    completed = [o for o in observations if result["fill_end_time"] is not None and o["t"] >= result["fill_end_time"]
                 and not o["flow"] and o["level"] is not None and o["confidence"] >= .65]
    enough = len(completed) / calibration["sample_fps"] >= calibration["settled_seconds_min"]
    if enough:
        tail = completed[-max(3, int(calibration["sample_fps"])):]
        levels = [o["level"] for o in tail]
        if max(levels)-min(levels) <= .04:
            result.update(final_level=round(float(np.median(levels)), 4), measurement_time=tail[len(tail)//2]["t"],
                          confidence=round(float(np.mean([o["confidence"] for o in tail])), 3))
            result["status"] = "underfill" if result["final_level"] < reference-tolerance else "normal"
            result["reason"] = ("Flow stopped and the settled visible liquid height is below the reviewed reference minus tolerance."
                                if result["status"] == "underfill" else
                                "Flow stopped and the settled visible liquid height meets the reviewed reference minus tolerance.")
            result["evidence_start"] = max(start, result["fill_end_time"]-.5)
    if overflow_time is not None:
        result.update(status="overflow", overflow_time=overflow_time, confidence=.9,
                      reason="Persistent orange liquid was visible outside the bottle silhouette; this is independent of interior fill height.",
                      evidence_start=max(start, overflow_time-.75), evidence_end=min(end, overflow_time+3.))
    return result


def analyze_video(video_path: str, output_path: str | None = None, progress_callback: Callable | None = None,
                  approved_reference: dict | None = None) -> dict:
    started = time.monotonic()
    calibration = load_calibration()
    digest = file_sha256(video_path)
    from .media_identity import validate_approved_reference
    reviewed = set(calibration.get("reviewed_source_sha256", [calibration["source_sha256"]]))
    proof_reference = (approved_reference or {}).get("reference_sha256")
    reference = proof_reference if proof_reference in reviewed else calibration["source_sha256"]
    verified = validate_approved_reference(approved_reference, digest, reference)
    if digest not in reviewed and not verified:
        raise ValueError("This bottle-cycle profile is calibrated only for the reviewed five-minute recording.")
    metadata = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", video_path]))
    duration = float(metadata["format"]["duration"])
    width, height = calibration["reference_size"]
    fps = calibration["sample_fps"]
    samples, cycles, events, observations, indices = [], [], [], [], []
    colors = ["#76b900", "#61b7ff", "#ffb946", "#df8bea"]

    def close_cycle():
        nonlocal observations, indices
        if observations and len(observations)/fps >= calibration["cycle_seconds_min"]:
            cycle = inspect_cycle(observations, len(cycles)+1, calibration)
            cycles.append(cycle)
            for i in indices:
                for b in samples[i]["bottles"]:
                    b["id"], b["label"] = cycle["id"], cycle["label"]
            t = cycle["overflow_time"] if cycle["overflow_time"] is not None else cycle["measurement_time"]
            events.append({"id": cycle["id"]+"-inspection", "t": t if t is not None else cycle["end_time"],
                           "type": "cycle_inspection", "label": cycle["label"]+": "+cycle["status"],
                           "detail": cycle["reason"], "bottle_id": cycle["id"]})
        else:
            for i in indices:
                samples[i]["bottles"] = []
                samples[i]["phase"] = "bottle_moving"
        observations, indices = [], []

    for t, frame in _frames(video_path, width, height, fps, calibration["source_fps"]):
        features = measure_frame(frame, calibration)
        features["t"] = round(t, 3)
        sample = {"t": round(t, 3), "scene_id": "single-station", "phase": "station_empty", "bottles": []}
        if features["aligned"]:
            observations.append(features)
            indices.append(len(samples))
            n = len(cycles)+1
            bx, by, bw, bh = calibration["bottle_box_offsets"]
            x0, x1 = calibration["interior_x_offsets"]
            y0, y1 = calibration["interior_y_range"]
            center = features["center"]
            phase = "filling" if features["flow"] else "settled" if any(o["flow"] for o in observations) else "awaiting_fill"
            sample["phase"] = phase
            sample["bottles"] = [{"id": f"cycle-{n:02d}", "label": f"Bottle {n}",
                "box": [(center+bx)/width, by/height, bw/width, bh/height],
                "measurement_box": [(center+x0)/width, y0/height, (x1-x0)/width, (y1-y0)/height],
                "level": features["level"], "confidence": features["confidence"], "level_kind": "visible-height",
                "measurement_state": ("ambiguous_boundary" if features["level"] is None else
                                      "at_upper_visible_limit" if features["level"] >= .975 else "measured_boundary"),
                "surface": features["surface"], "mask": features["mask"], "phase": phase}]
        else:
            close_cycle()
            sample["phase"] = "bottle_moving" if features["center"] is not None else "station_empty"
        samples.append(sample)
        if progress_callback and len(samples)%8 == 0:
            progress_callback(min(.99, t/duration))
    close_cycle()
    summary = {"total": len(cycles), **{status: sum(c["status"] == status for c in cycles)
                 for status in ("normal", "underfill", "overflow", "uncertain")}}
    result = {"version": VERSION, "analysis_kind": "bottle-cycles", "source_sha256": digest,
        "algorithm": ALGORITHM, "calibration_sha256": file_sha256(CALIBRATION_PATH), "sample_fps": fps,
        "runtime_seconds": round(time.monotonic()-started, 3), "samples": samples, "events": events,
        "bottles": [{"id": c["id"], "label": c["label"], "color": colors[i%len(colors)], "scene_id": "single-station"}
                    for i, c in enumerate(cycles)], "cycles": cycles, "summary": summary,
        "quality": {"mode": "analyzed-replay", "source_specific_calibration": True,
            "measurement": "relative visible height; not volume", "reference_level": calibration["reference_level"],
            "tolerance": calibration["tolerance"], "reference_basis": calibration["reference_basis"],
            "tracking": "Green collar alignment and departure define individual stationary bottle cycles.",
            "completion": "Visible nozzle flow must stop before a settled final level can be assessed.",
            "mask": "HSV-classified liquid pixels in calibrated interior strip.",
            "confidence": "Heuristic pixel-fit quality, not a calibrated probability of correctness.",
            "overflow": "Persistent exterior orange pixels in calibrated regions outside bottle silhouette.",
            "limits": ["Calibrated to one fixed-camera recording; other footage requires reviewed calibration.",
                       "Reference tolerance is for this demonstration, not a production quality specification.",
                       "Recorded analysis; no continuous live input in this component.",
                       "Sampling resolution is 0.25 seconds; boundaries and timestamps are sampled estimates.",
                       "Bottle identity covers stationary filling cycles, not conveyor-wide tracking."]}}
    if verified:
        result.update(calibration_reference_sha256=reference, media_identity=approved_reference)
    if output_path:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix+".tmp")
        temporary.write_text(json.dumps(result, separators=(",", ":")))
        temporary.replace(target)
    if progress_callback:
        progress_callback(1.)
    return result
