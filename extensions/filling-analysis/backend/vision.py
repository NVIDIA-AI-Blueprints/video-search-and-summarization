# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Source-calibrated pixel measurements; no model inference or scripted curves.

The algorithm fits a transparent-above / orange-liquid-below row model to HSV
pixels in a human-selected interior strip. Green neck/handle pixels register the
strip to camera motion. Invalid/occluded or diffuse boundaries remain unknown.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

VERSION = "1.0.0"
ALGORITHM = "calibrated-hsv-row-boundary-v1"
CALIBRATION_PATH = Path(__file__).with_name("calibration.json")


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_calibration() -> dict:
    return json.loads(CALIBRATION_PATH.read_text())


def find_anchor(hsv: np.ndarray, scene: dict) -> tuple[float, float] | None:
    """Register against green plastic, independent of liquid appearance."""
    green = cv2.inRange(hsv, (38, 65, 45), (95, 255, 255))
    x, y, w, h = scene["anchor_search"]
    allowed = np.zeros_like(green)
    allowed[y:y + h, x:x + w] = green[y:y + h, x:x + w]
    count, labels, stats, _ = cv2.connectedComponentsWithStats(allowed)
    candidates = []
    for idx in range(1, count):
        sx, sy, sw, sh, area = map(int, stats[idx])
        if scene["anchor_kind"] == "handle":
            valid = area > 2500 and sh > 95 and sw > 70
        else:
            valid = area > 2800 and 25 < sh < 65 and sw > 95
        if not valid:
            continue
        top = (labels[sy:sy + max(12, int(sh * .13))] == idx)
        _, xs = np.where(top)
        if len(xs):
            candidates.append((area, float(np.median(xs)), float(sy)))
    if not candidates:
        return None
    # Front handle is the right-most substantial component in angle A.
    best = max(candidates, key=lambda c: c[1] if scene["anchor_kind"] == "handle" else c[0])
    return best[1], best[2]


def segment_strip(hsv: np.ndarray, scene: dict) -> dict:
    """Fit one physical surface; do not integrate color area as fake volume."""
    height, width = hsv.shape[:2]
    if height < 30 or width < 10:
        return {"level": None, "confidence": 0., "state": "invalid_region", "boundary": None}
    hue, sat, val = cv2.split(hsv)
    visible = (hue >= 5) & (hue <= 36) & (sat >= 70) & (val >= 95)
    orange = visible & (hue <= scene["liquid_hue_max"]) & (sat >= scene["liquid_saturation_min"])
    denominators = visible.sum(axis=1)
    usable = denominators > width * .55
    ys = np.flatnonzero(usable)
    if len(ys) < height * .55:
        return {"level": None, "confidence": 0., "state": "occluded", "boundary": None}
    fractions = orange.sum(axis=1) / np.maximum(denominators, 1)
    fractions = np.convolve(fractions, np.ones(9) / 9, mode="same")
    p = fractions[usable]
    # Per-row absolute error for empty above, orange below. Missing rail rows
    # are excluded, not filled with synthetic data.
    costs = np.r_[0., np.cumsum(p)] + np.r_[np.cumsum((1. - p)[::-1])[::-1], 0.]
    split = int(np.argmin(costs))
    boundary = int(ys[min(split, len(ys) - 1)])
    error = float(costs[split] / len(p))
    confidence = float(np.clip((1. - error * 2.5) * np.mean(usable), 0., 1.))
    raw_level = float(np.clip(1. - boundary / height, 0., 1.))
    if float(np.mean(p)) < .06 or split >= len(p) - 5:
        return {"level": None, "confidence": round(confidence, 3), "state": "below_visible_window", "boundary": None}
    if error > .22:
        return {"level": None, "confidence": round(confidence, 3), "state": "ambiguous_color", "boundary": None}
    lo, hi = max(0, boundary - 12), min(height, boundary + 13)
    if np.mean(usable[lo:hi]) < .65:
        return {"level": None, "confidence": round(confidence, 3), "state": "surface_occluded", "boundary": None}
    # A gradual light/color gradient is not a located liquid surface. The second
    # close-up is intentionally strict because its lower bottle is out of frame.
    before = fractions[max(0, boundary - 12):max(1, boundary - 3)]
    after = fractions[min(height - 1, boundary + 3):min(height, boundary + 12)]
    transition = float(np.mean(after) - np.mean(before)) if len(before) and len(after) else 0.
    if scene.get("requires_sharp_boundary") and transition < .48:
        return {"level": None, "confidence": round(min(confidence, .45), 3), "state": "diffuse_boundary", "boundary": None}
    state = "at_upper_visible_limit" if raw_level >= .975 else "measured_boundary"
    return {"level": round(raw_level, 4), "confidence": round(confidence, 3), "state": state, "boundary": boundary, "orange": orange}


def _normal_box(box: list[float], width: int, height: int) -> list[float]:
    """Clip display geometry; callers retain original pixel bounds to measure."""
    left = round(float(np.clip(box[0] / width, 0., 1.)), 6)
    top = round(float(np.clip(box[1] / height, 0., 1.)), 6)
    right = round(float(np.clip((box[0] + box[2]) / width, 0., 1.)), 6)
    bottom = round(float(np.clip((box[1] + box[3]) / height, 0., 1.)), 6)
    return [left, top, round(max(0., right - left), 6), round(max(0., bottom - top), 6)]


def measure_bottle(hsv: np.ndarray, scene: dict, bottle: dict, offset: tuple[float, float]) -> dict:
    h, w = hsv.shape[:2]
    dx, dy = offset
    box = [bottle["box"][0] + dx, bottle["box"][1] + dy, *bottle["box"][2:]]
    strip = [bottle["measurement_box"][0] + dx, bottle["measurement_box"][1] + dy, *bottle["measurement_box"][2:]]
    x, y, sw, sh = map(lambda v: int(round(v)), strip)
    result = {"id": bottle["id"], "label": bottle["label"], "box": _normal_box(box, w, h), "measurement_box": _normal_box(strip, w, h), "level_kind": "visible-height", "level": None, "confidence": 0., "surface": [], "mask": []}
    if x < 0 or y < 0 or x + sw > w or y + sh > h:
        result["measurement_state"] = "out_of_frame"
        result["measurement_box_clipped"] = True
        return result
    if not scene.get("quantitative_enabled", True):
        result["measurement_state"] = "partial_view_not_validated"
        return result
    measurement = segment_strip(hsv[y:y + sh, x:x + sw], scene)
    result.update(level=measurement["level"], confidence=measurement["confidence"], measurement_state=measurement["state"])
    if measurement["boundary"] is None:
        return result
    boundary = measurement["boundary"]
    # The surface only spans the calibrated measured strip, not the entire
    # bottle silhouette. Orange mask is actual classified pixels in that strip.
    result["surface"] = [[round(x / w, 6), round((y + boundary) / h, 6)], [round((x + sw) / w, 6), round((y + boundary) / h, 6)]]
    binary = measurement["orange"].astype(np.uint8) * 255
    binary[:max(0, boundary - 5)] = 0
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        contour = max(contours, key=cv2.contourArea)
        simplified = cv2.approxPolyDP(contour, 1.5, True).reshape(-1, 2)
        result["mask"] = [[round((x + float(px)) / w, 6), round((y + float(py)) / h, 6)] for px, py in simplified]
    return result


def _decode_command(path: str, width: int, height: int, fps: float, source_fps: float) -> list[str]:
    stride = int(source_fps / fps)
    if stride < 1 or stride * fps != source_fps:
        raise ValueError("Sample cadence must divide the calibrated source FPS exactly")
    # Select actual source frames. The fps filter rounds/resamples and may
    # silently move imagery relative to output timestamps. This source is CFR.
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-threads", "4", "-i", path, "-map", "0:v:0", "-an", "-vf", f"select=not(mod(n\\,{stride})),scale={width}:{height}", "-vsync", "0", "-threads", "4", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]


def _frames(path: str, width: int, height: int, fps: float, source_fps: float):
    command = _decode_command(path, width, height, fps, source_fps)
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    size = width * height * 3
    try:
        assert proc.stdout is not None
        index = 0
        while True:
            data = proc.stdout.read(size)
            if not data:
                break
            if len(data) != size:
                raise RuntimeError("Decoder returned an incomplete frame")
            yield index / fps, np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)
            index += 1
        proc.wait()
        if proc.returncode:
            raise RuntimeError("Video decoding failed: " + (proc.stderr.read().decode() if proc.stderr else ""))
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait()


def analyze_video(video_path: str, output_path: str | None = None, progress_callback: Callable | None = None,
                  approved_reference: dict | None = None) -> dict:
    from .profiles import profile_for_identity

    profile = profile_for_identity(file_sha256(video_path), approved_reference)
    if profile and profile.analysis_kind == "bottle-cycles":
        from .cycle_vision import analyze_video as analyze_cycles
        return analyze_cycles(video_path, output_path=output_path, progress_callback=progress_callback,
                              approved_reference=approved_reference)
    started = time.monotonic()
    calibration = load_calibration()
    digest = file_sha256(video_path)
    from .media_identity import validate_approved_reference
    identity_verified = validate_approved_reference(approved_reference, digest, calibration["source_sha256"])
    if digest != calibration["source_sha256"] and not identity_verified:
        raise ValueError("This source has no reviewed calibration. Calibrate its camera shots before measuring; the orange-juice calibration cannot be applied to different footage.")
    metadata = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", video_path]))
    duration = float(metadata["format"]["duration"])
    width, height = calibration["reference_size"]
    fps = calibration["sample_fps"]
    samples, events, history = [], [], {}
    previous_anchor, scene_before, departed = None, None, False
    phase_before = None
    for t, frame in _frames(video_path, width, height, fps, calibration["source_fps"]):
        scene = next((s for s in calibration["scenes"] if s["start"] <= t < s["end"]), None)
        scene_id = scene["id"] if scene else None
        if scene_id != scene_before:
            previous_anchor, departed, phase_before = None, False, None
            scene_before = scene_id
        sample = {"t": round(t, 3), "scene_id": scene_id, "phase": "outside_calibrated_view", "bottles": []}
        if scene:
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            anchor = find_anchor(hsv, scene)
            if anchor and previous_anchor and not departed:
                elapsed = t - previous_anchor[0]
                vx = (anchor[0] - previous_anchor[1][0]) / elapsed
                vy = (anchor[1] - previous_anchor[1][1]) / elapsed
                if abs(vx) > 115 and abs(vy) < 35:
                    departed = True
                    events.append({"id": f"{scene_id}-tracking-end", "t": round(t, 3), "type": "tracking_ended", "label": "Bottle group moved — tracking reset", "detail": "Measured neck-anchor horizontal motion exceeded the stationary-station calibration. Bottle identities are not reused for the next group."})
            if anchor:
                previous_anchor = (t, anchor)
            if anchor and not departed:
                offset = (anchor[0] - scene["anchor_reference"][0], anchor[1] - scene["anchor_reference"][1])
                sample["bottles"] = [measure_bottle(hsv, scene, b, offset) for b in scene["bottles"]]
                states = []
                for b in sample["bottles"]:
                    past = history.setdefault(b["id"], [])
                    if b["level"] is None:
                        b["phase"] = "unreadable"
                    else:
                        recent = [p for p in past if p[0] >= t - 2.]
                        rise = b["level"] - recent[0][1] if recent else 0.
                        b["phase"] = "upper_visible_limit" if b["measurement_state"] == "at_upper_visible_limit" else "level_rising" if rise > .025 else "level_stable" if len(recent) >= 4 else "measuring"
                        past.append((t, b["level"]))
                        if b["level"] >= .90 and not any(e.get("bottle_id") == b["id"] and e["type"] == "reference_crossing" for e in events):
                            events.append({"id": f"{b['id']}-90", "t": round(t, 3), "type": "reference_crossing", "label": f"{b['label']} reached 90% of visible window", "detail": "First measured sample at or above the 90% visible-height reference. This is not 90% bottle volume or a quality verdict.", "bottle_id": b["id"]})
                    states.append(b["phase"])
                sample["phase"] = "level_rising" if "level_rising" in states else "upper_visible_limit" if "upper_visible_limit" in states else "level_stable" if "level_stable" in states else "unreadable"
                if sample["phase"] == "level_rising" and phase_before != "level_rising" and not any(e["id"] == scene_id + "-rising" for e in events):
                    events.append({"id": scene_id + "-rising", "t": round(t, 3), "type": "level_rising", "label": "Liquid boundary is rising", "detail": "Visible height increased by more than 2.5 percentage points over the recent measured samples."})
                phase_before = sample["phase"]
            else:
                sample["phase"] = "tracking_ended" if departed else "anchor_not_visible"
        samples.append(sample)
        if progress_callback and len(samples) % 8 == 0:
            progress_callback(min(.99, t / duration))
    result = {"version": VERSION, "source_sha256": digest, "algorithm": ALGORITHM, "calibration_sha256": file_sha256(CALIBRATION_PATH), "sample_fps": fps, "runtime_seconds": round(time.monotonic() - started, 3), "samples": samples, "events": sorted(events, key=lambda e: e["t"]), "bottles": [{"id": b["id"], "label": b["label"], "color": b["color"], "scene_id": s["id"]} for s in calibration["scenes"] for b in s["bottles"]], "quality": {"mode": "analyzed-replay", "source_specific_calibration": True, "measurement": "relative visible height; not volume", "mask": "HSV-classified liquid pixels within calibrated interior strips", "tracking": "measured green neck/handle anchors; reset between reviewed camera shots and on large horizontal group motion", "uncertainty": "Null for hidden, diffuse, ambiguous or out-of-frame boundaries. Upper-limit readings are censored at the measurement-window edge.", "limits": ["No certified fill-volume or underfill/overfill verdict", "No spill detector", "No bottle identity across camera cuts", "Second-angle bottle bottom lies outside the image; quantitative measurement is disabled pending calibration", "Process chapters are human-reviewed, separately from measured events", "Reference crossing resolution is 0.2 seconds; pixel/color calibration has not been validated on other footage"]}}
    if identity_verified:
        result["calibration_reference_sha256"] = calibration["source_sha256"]
        result["media_identity"] = approved_reference
    if output_path:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(result, separators=(",", ":")))
        temporary.replace(target)
    if progress_callback:
        progress_callback(1.)
    return result
