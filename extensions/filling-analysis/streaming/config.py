# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Server-owned stream registration; client requests never supply media URLs."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID


def digest_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def reviewed_exterior(source: dict, default: dict) -> dict:
    """Accept camera-specific evidence regions only from the server-owned profile."""
    candidate = source.get("exterior_calibration")
    if candidate is None:
        return copy.deepcopy(default)
    if not isinstance(candidate, dict):
        raise ValueError("Reviewed exterior calibration must be an object")
    asset = source.get("source_asset_sha256", "")
    if (len(asset) != 64 or any(c not in "0123456789abcdef" for c in asset)
            or candidate.get("reviewed_source_sha256") != asset):
        raise ValueError("Exterior calibration must match the reviewed source asset")
    if candidate.get("engine") != "calibrated-exterior-color-signal-v1" or not candidate.get("basis"):
        raise ValueError("Unsupported or unreviewed exterior evidence method")
    size = candidate.get("reference_size")
    if (not isinstance(size, list) or len(size) != 2
            or any(type(x) is not int or not 16 <= x <= 4096 for x in size)):
        raise ValueError("Exterior reference dimensions are invalid")
    boxes = candidate.get("boxes")
    if not isinstance(boxes, list) or not 1 <= len(boxes) <= 16:
        raise ValueError("Exterior evidence regions are required")
    for box in boxes:
        if not isinstance(box, list) or len(box) != 4 or any(type(x) is not int for x in box):
            raise ValueError("Exterior evidence region is invalid")
        x, y, width, height = box
        if x < 0 or y < 0 or width <= 0 or height <= 0 or x+width > size[0] or y+height > size[1]:
            raise ValueError("Exterior evidence region exceeds its reference frame")
    lower, upper = candidate.get("orange_hsv_lower"), candidate.get("orange_hsv_upper")
    if any(not isinstance(value, list) or len(value) != 3 for value in (lower, upper)):
        raise ValueError("Exterior color limits are invalid")
    for lo, hi, maximum in zip(lower, upper, (179, 255, 255)):
        if type(lo) is not int or type(hi) is not int or not 0 <= lo <= hi <= maximum:
            raise ValueError("Exterior color limits are invalid")
    pixels = candidate.get("pixels_min")
    if type(pixels) is not int or not 0 < pixels <= sum(box[2]*box[3] for box in boxes):
        raise ValueError("Exterior pixel threshold is invalid")
    seconds = candidate.get("persistence_seconds")
    if (type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 10):
        raise ValueError("Exterior persistence duration is invalid")
    return copy.deepcopy(candidate)


def source_config(path: Path, stream_id: str, calibration: dict) -> dict:
    stream_id = str(UUID(stream_id))
    document = json.loads(path.read_text())
    if document.get("schema_version") != 1:
        raise ValueError("Unsupported live stream allowlist schema")
    source = copy.deepcopy(document.get("streams", {}).get(stream_id))
    if not isinstance(source, dict):
        raise ValueError("Stream is not registered in the live inspection allowlist")
    url = urlsplit(source.get("url", ""))
    if url.scheme not in {"rtsp", "rtsps"} or not url.hostname:
        raise ValueError("Allowlisted input must be an RTSP stream")
    for key in ("width", "height"):
        if not isinstance(source.get(key), int) or not 16 <= source[key] <= 4096:
            raise ValueError("Allowlisted dimensions are missing or invalid")
    fps = source.get("fps")
    if not isinstance(fps, (int, float)) or not math.isfinite(fps) or not 1 <= fps <= 60:
        raise ValueError("Allowlisted frame rate is invalid")
    for key in ("reference_level", "tolerance"):
        number = source.get(key)
        if not isinstance(number, (int, float)) or not math.isfinite(number) or not 0 < number < 1:
            raise ValueError("An explicit reviewed reference and tolerance are required")
    if not source.get("profile_id") or not source.get("calibration_basis"):
        raise ValueError("A reviewed camera profile and calibration basis are required")
    if source.get("model_hashes", calibration["model_hashes"]) != calibration["model_hashes"]:
        raise ValueError("Stream calibration model identities differ from the reviewed pair")
    source["stream_id"] = stream_id
    source["calibration"] = copy.deepcopy(calibration)
    source["calibration"]["tolerance"] = source["tolerance"]
    source["calibration"]["exterior"] = reviewed_exterior(source, calibration["exterior"])
    # This is a reviewed threshold, not a prerecorded event schedule or a frame lookup.
    source["calibration_sha256"] = digest_json({
        "calibration": source["calibration"], "profile_id": source["profile_id"],
        "reference_level": source["reference_level"], "basis": source["calibration_basis"],
    })
    return source


def public_source(source):
    return {key: source[key] for key in (
        "stream_id", "name", "width", "height", "fps", "profile_id",
        "reference_level", "tolerance", "calibration_basis", "calibration_sha256",
    ) if key in source}
