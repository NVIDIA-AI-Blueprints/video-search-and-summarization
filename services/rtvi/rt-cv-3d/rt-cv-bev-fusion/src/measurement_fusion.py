# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
MV3DT Measurement Fusion Service

Consumes per-sensor Frame messages from mdx-mv3dt-raw and aggregates all frames
sharing the same timestamp bucket into a single fused BEV Frame, then publishes to mdx-bev.

Fusion logic:
  - Buffer messages per timestamp bucket (BUCKET_MS window); flush when all expected
    sensors arrive or on timeout. Timestamp bucketing is required for multi-container
    deployments where each DeepStream instance maintains an independent frame counter
    — frame IDs diverge across containers but wall-clock timestamps stay aligned.
  - For each unique object ID: views collapsed to one position per FUSION_METHOD.
    "first", "closest", "mean" and "median" are the single-idea baselines, each
    taking every view. "rays" (default) refuses views under VISIBILITY_MIN, then
    solves for the position that best fits the remaining lines of sight, which
    needs CALIBRATION_PATH. Without it that solve is unavailable and "rays" degrades
    to an area-weighted mean of the gated views. See README, Fusion Methods.
  - Object type resolved by majority vote across the fused sensors (ties broken by
    total confidence)
  - Fused Frame timestamp: arithmetic mean of all sensor timestamps
  - Per-sensor timestamps stored in Frame.info (key = sensorId, value = ISO timestamp)
  - Output sensorId = "bev-sensor-1"

Broker support:
  - BROKER_TYPE=kafka (default): Confluent Kafka
  - BROKER_TYPE=redis: Redis Streams (XADD/XREAD, payloadkey=sensor.id for mdx-bev, value for mdx-mv3dt-raw)
"""

import collections
import itertools
import json
import logging
import math
import os

import signal
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from google.protobuf import timestamp_pb2

import schema_pb2

# ---------------------------------------------------------------------------
# Configuration (all overridable via environment variables)
# ---------------------------------------------------------------------------

# --- Broker / topics --------------------------------------------------------
BROKER_TYPE          = os.environ.get("BROKER_TYPE",     "kafka")           # "kafka" or "redis"
KAFKA_BOOTSTRAP      = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")  # Kafka bootstrap servers
REDIS_HOST           = os.environ.get("REDIS_HOST",      "localhost")       # Redis host (BROKER_TYPE=redis)
REDIS_PORT           = int(os.environ.get("REDIS_PORT",  "6379"))           # Redis port
RAW_TOPIC            = os.environ.get("RAW_TOPIC",       "mdx-raw")         # Input: per-sensor Frames
FUSED_TOPIC          = os.environ.get("FUSED_TOPIC",     "mdx-bev")         # Output: fused BEV Frames
CONSUMER_POLL_MS     = float(os.environ.get("CONSUMER_POLL_MS", "10"))      # Broker poll/block timeout per iter

# --- Pipeline information ---------------------------------------------------
# Number of distinct sensors expected per timestamp bucket. Once a bucket has
# this many sensors, the fast-path "all here" flush fires immediately.
MAX_EXPECTED_SENSORS = int(os.environ.get("MAX_EXPECTED_SENSORS", "4"))
# sensorId stamped on every fused Frame published to FUSED_TOPIC.
FUSED_SENSOR_ID      = "bev-sensor-1"

# --- Measurement fusion -----------------------------------------------------
# How the views of one object become one position. See README, Fusion Methods.
FUSION_METHOD        = os.environ.get("FUSION_METHOD",  "rays").strip().lower()
FUSION_METHODS       = ("first", "closest", "mean", "median", "rays")
BASELINE_METHODS     = ("first", "closest", "mean", "median")   # ungated, one idea each
CALIBRATION_METHODS  = ("closest",)             # cannot run without camera geometry
CALIBRATION_PATH     = os.environ.get("CALIBRATION_PATH", "/calibration/calibration.json")
# Drop views further than this from their own camera; 0 disables.
MAX_DIST             = float(os.environ.get("MAX_DIST", "30"))
if MAX_DIST < 0:
    raise ValueError("MAX_DIST must be 0 (disabled) or a positive distance in metres")

# Constant-velocity filter over each fused track.
TEMPORAL_FILTER      = os.environ.get("TEMPORAL_FILTER", "1").strip().lower() in ("1", "true", "yes")
PIXEL_SIGMA          = float(os.environ.get("PIXEL_SIGMA", "3.0"))   # bottom-edge jitter, px
ACCEL_SIGMA          = float(os.environ.get("ACCEL_SIGMA", "3.0"))   # m/s^2
FILTER_RESET_BUCKETS = int(os.environ.get("FILTER_RESET_BUCKETS", "30"))  # silence before restart
if TEMPORAL_FILTER and not (PIXEL_SIGMA > 0 and ACCEL_SIGMA > 0 and FILTER_RESET_BUCKETS > 0):
    raise ValueError("PIXEL_SIGMA, ACCEL_SIGMA and FILTER_RESET_BUCKETS must all be greater than 0")
# Gate for "rays". Needs TargetManagement.outputVisibility upstream, or the
# value is pinned to 1.0 and the gate does nothing.
VISIBILITY_MIN       = float(os.environ.get("VISIBILITY_MIN",     "0.3"))
if not 0.0 <= VISIBILITY_MIN <= 1.0:
    raise ValueError("VISIBILITY_MIN must be a finite value between 0 and 1")

# --- Foot-point offset ------------------------------------------------------
# A contact point sitting d off the z=0 plane back-projects short by d/h of its
# range. "auto" measures the offset that makes overlapping cameras agree, so a
# site with no offset measures zero; "off", or a fixed distance in metres.
FOOT_OFFSET           = os.environ.get("FOOT_OFFSET", "auto").strip().lower()
FOOT_OFFSET_MIN, FOOT_OFFSET_MAX = -0.05, 0.10   # outside this, something else is wrong
FOOT_OFFSET_MIN_PAIRS = int(os.environ.get("FOOT_OFFSET_MIN_PAIRS", "500"))
FOOT_OFFSET_WINDOW    = int(os.environ.get("FOOT_OFFSET_WINDOW", "20000"))
# Sample every Nth bucket: consecutive ones hold the same people on the same
# cameras, so they carry less information than their count suggests.
FOOT_OFFSET_STRIDE    = int(os.environ.get("FOOT_OFFSET_STRIDE", "10"))
FOOT_OFFSET_EVERY     = int(os.environ.get("FOOT_OFFSET_EVERY", "600"))   # buckets
FOOT_OFFSET_ITERS     = 20                       # ternary steps; the cost is unimodal
if FOOT_OFFSET not in ("auto", "off"):
    try:
        _fixed = float(FOOT_OFFSET)
    except ValueError:
        raise ValueError(f"FOOT_OFFSET={FOOT_OFFSET!r} must be 'auto', 'off' or metres")
    if not FOOT_OFFSET_MIN <= _fixed <= FOOT_OFFSET_MAX:
        raise ValueError(f"FOOT_OFFSET={_fixed:g} outside [{FOOT_OFFSET_MIN:g}, {FOOT_OFFSET_MAX:g}] m")

# --- Mis-association --------------------------------------------------------
# Views of one id further apart than this are different people; 0 disables.
CONFLICT_RADIUS      = float(os.environ.get("CONFLICT_RADIUS", "2.0"))
if CONFLICT_RADIUS < 0:
    raise ValueError("CONFLICT_RADIUS must be 0 (disabled) or a positive distance in metres")
# Reject a fused position implying more than this, m/s; 0 disables.
MAX_SPEED            = float(os.environ.get("MAX_SPEED", "10"))
# Accept after this many refusals, else a real move or a reused id is stranded.
REACQUIRE            = int(os.environ.get("REACQUIRE", "10"))
if MAX_SPEED < 0 or REACQUIRE < 1:
    raise ValueError("MAX_SPEED must be >= 0 (0 disables) and REACQUIRE >= 1")
# Publish a re-acquisition under a new id rather than leaping the old one there.
# The id is the longest-unused below the highest in play, and is a number.
SPLIT_ON_REACQUIRE   = os.environ.get("SPLIT_ON_REACQUIRE", "1").strip().lower() in ("1", "true", "yes")
GATE_DEBUG           = float(os.environ.get("GATE_DEBUG", "0"))   # log branch for steps >= this
# How long the gate remembers a track. Must exceed the refusal sequence.
GATE_MEMORY          = int(os.environ.get("GATE_MEMORY", "300"))

# Buckets of publish delay allowing an RTS backward pass. Needs
# SPLIT_ON_REACQUIRE, or it smears an accepted leap across neighbouring frames.
SMOOTH_LAG           = int(os.environ.get("SMOOTH_LAG", "0"))
if SMOOTH_LAG < 0:
    raise ValueError("SMOOTH_LAG must be 0 (causal) or a positive number of buckets")

def _load_cameras(path: str) -> dict:
    """{sensorId: {x, y, h, f}} from calibration.json, or {} when unavailable.

    x, y is where the camera stands on the ground plane, h how high above it and
    f the focal length in pixels. The camera centre is -R^T t from the extrinsic
    matrix, so its third component is the height. A sensor missing any of these
    is skipped rather than failing the load: the methods that need geometry check
    for their own sensors, and the ones that do not should still start.
    """
    try:
        with open(path) as handle:
            sensors = json.load(handle).get("sensors", [])
    except (OSError, ValueError) as exc:
        logger.info("No camera calibration at %s (%s)", path, exc)
        return {}
    cameras = {}
    for sensor in sensors:
        try:
            if sensor.get("type") != "camera":
                continue
            rows = sensor["extrinsicMatrix"]
            # Camera centre is -R^T t. Taking x, y and h from one source keeps
            # them consistent; "coordinates" is placeholder in some calibrations.
            centre = [-sum(rows[k][:3][i] * rows[k][3] for k in range(3)) for i in range(3)]
            height = abs(centre[2])
            focal = float(sensor["intrinsicMatrix"][0][0])
            if not (height > 0 and focal > 0):
                continue
            cameras[str(sensor["id"])] = {
                "x": centre[0], "y": centre[1], "h": height, "f": focal,
            }
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return cameras

# --- Fusion timing ----------------------------------------------------------
# Max time a bucket waits for missing sensors before flushing with whatever it has.
# Applied to BOTH event-time lag (watermark trigger in _buffer_frame) and arrival-time
# age (sweep trigger in _sweep_loop) — whichever crosses the threshold first wins.
SENSOR_TIMEOUT_MS    = float(os.environ.get("SENSOR_TIMEOUT_MS",  "100"))
# Width of one timestamp bucket. Frames whose POSIX timestamps round to the same slot
# are fused together. Default 17 ms = half a 30-FPS frame: narrow enough to never lump
# adjacent frames, wide enough to absorb ~8 ms of inter-container clock skew. The empty
# "guard" bucket between consecutive frames also prevents cross-frame contamination
# if a sensor's clock drifts more than half a frame.
BUCKET_MS            = float(os.environ.get("BUCKET_MS",          "17"))

# --- Background sweep / memory bounds ---------------------------------------
# Sweep thread cadence. Bounds how stale a bucket can get before the arrival-time
# branch (flush or stale-drop) notices it.
SWEEP_INTERVAL_S     = float(os.environ.get("SWEEP_INTERVAL_S",   "0.02"))  # 20 ms
# Hard upper bound on bucket age. Older buckets are *dropped* (not flushed) as a
# safety net for pathological conditions (sweep starvation, broker back-pressure).
# In healthy operation no bucket comes close to this.
BUFFER_DURATION_S    = float(os.environ.get("BUFFER_DURATION_S",  "1.0"))
# How long to remember already-flushed bucket keys. Rejects late stragglers so the
# same fused Frame.id is never republished — without this the BEV display "blinks".
# This is purely a memory bound; the rejection check itself is mandatory.
CLOSED_BUCKET_RETENTION_MS = float(os.environ.get("CLOSED_BUCKET_RETENTION_MS", "1000"))

# --- Logging ----------------------------------------------------------------
# Set LOG_LEVEL=DEBUG to enable verbose per-frame tracing (RECV, TRIG-*, PUBLISH, ...).
LOG_LEVEL            = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Fail rather than default: a typo would silently change every published position.
if FUSION_METHOD not in FUSION_METHODS:
    raise ValueError(f"FUSION_METHOD={FUSION_METHOD!r} unknown; expected one of "
                     f"{', '.join(FUSION_METHODS)}")

CAMERAS = _load_cameras(CALIBRATION_PATH)
if FUSION_METHOD in CALIBRATION_METHODS and not CAMERAS:
    raise ValueError(f"FUSION_METHOD={FUSION_METHOD!r} needs camera geometry, but no usable "
                     f"calibration was read from {CALIBRATION_PATH!r}. Mount the deployment's "
                     f"calibration.json there, or set CALIBRATION_PATH.")
logger.info("Fusion method: %s%s", FUSION_METHOD,
            f" (VISIBILITY_MIN={VISIBILITY_MIN:g})" if FUSION_METHOD == "rays" else "")
if FUSION_METHOD == "rays" and not CAMERAS:
    # Degrade rather than refuse: this is the default, so an older deployment that
    # has not added the calibration mount yet still starts. It just fuses worse.
    logger.warning("No calibration at %s, so 'rays' has no sight lines to solve and falls back "
                   "to an area-weighted mean of the gated views. Mount the deployment's "
                   "calibration.json there, or set CALIBRATION_PATH.", CALIBRATION_PATH)
logger.info("Camera calibration: %d camera(s) from %s",
            len(CAMERAS), CALIBRATION_PATH if CAMERAS else "(none loaded)")
logger.info("Range gate: %s", f"MAX_DIST={MAX_DIST:g}m" if MAX_DIST > 0 else "disabled")
logger.info("Temporal filter: %s", f"on (PIXEL_SIGMA={PIXEL_SIGMA:g}px, "
            f"ACCEL_SIGMA={ACCEL_SIGMA:g}m/s^2)" if TEMPORAL_FILTER else "off")
logger.info("Smoothing: %s", f"fixed lag {SMOOTH_LAG} buckets (~{SMOOTH_LAG * BUCKET_MS:g}ms)"
            if SMOOTH_LAG else "off (causal)")
logger.info("Conflict radius: %s", f"{CONFLICT_RADIUS:g}m" if CONFLICT_RADIUS > 0 else "disabled")
logger.info("Speed gate: %s%s", f"MAX_SPEED={MAX_SPEED:g}m/s, accept after {REACQUIRE} rejections"
            if MAX_SPEED > 0 else "disabled",
            "; re-acquisition reuses the longest-unused id below the highest in play"
            if MAX_SPEED > 0 and SPLIT_ON_REACQUIRE else "")
logger.info("Foot offset: %s", "disabled" if FOOT_OFFSET == "off"
            else (f"auto, after {FOOT_OFFSET_MIN_PAIRS} view pairs, "
                  f"clamped to [{FOOT_OFFSET_MIN:g}, {FOOT_OFFSET_MAX:g}]m"
                  if FOOT_OFFSET == "auto" else f"fixed at {float(FOOT_OFFSET):g}m"))
if FOOT_OFFSET != "off" and not CAMERAS:
    logger.warning("Foot offset needs camera heights; none loaded, so none applied.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_proto_timestamp(ts_msg) -> float:
    """Convert google.protobuf.Timestamp to POSIX seconds (float)."""
    return ts_msg.seconds + ts_msg.nanos / 1e9


def _posix_to_proto_timestamp(posix: float) -> timestamp_pb2.Timestamp:
    ts = timestamp_pb2.Timestamp()
    ts.seconds = int(posix)
    ts.nanos   = int((posix - int(posix)) * 1e9)
    return ts


def _posix_to_rfc3339(posix: float) -> str:
    dt = datetime.fromtimestamp(posix, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _ts_bucket_key(posix_ts: float) -> int:
    """Quantize a POSIX timestamp into a BUCKET_MS-wide bucket index.

    This is the fusion key, used in place of frame_id. Frames from different
    DeepStream containers have unrelated frame_id counters, but their POSIX
    timestamps stay aligned — so rounding to a shared time grid groups same-
    instant frames across containers.
    """
    return round(posix_ts * 1000 / BUCKET_MS)


def _element_wise_mean(arrays: list[list[float]],
                       weights: list[float] | None = None) -> list[float]:
    """Per-coordinate mean across the sensors reporting one object.

    Weights are 2D box areas, a proxy for range. Absent or all-zero weights fall
    back to a plain mean.
    """
    if not arrays:
        return []
    if weights is None or sum(weights) <= 0:
        weights = [1.0] * len(arrays)
    total = sum(weights)
    n = len(arrays[0])
    result = [0.0] * n
    for w, arr in zip(weights, arrays):
        for i, v in enumerate(arr):
            result[i] += w * v
    return [x / total for x in result]


def _visibility(obj: schema_pb2.Object) -> float | None:
    """Tracker-reported visibility for one view, or None if not reported at all.

    None is not zero: an unreported view cannot be assessed and must be admitted,
    a reported 0.0 must be refused. Conflating them empties every bucket for a
    producer that omits the field.
    """
    raw = obj.info.get("visibility")
    if raw is None: return None
    try:
        value = float(raw)
        return value if 0.0 <= value <= 1.0 else None
    except ValueError:
        return None


def _admits(obj: schema_pb2.Object) -> bool:
    v = _visibility(obj)
    return v is None or v >= VISIBILITY_MIN


def _bbox_area(obj: schema_pb2.Object) -> float:
    """Area of the 2D detection box in pixels, 0.0 when no box is attached."""
    if not obj.HasField("bbox"): return 0.0
    b = obj.bbox
    return max(0.0, b.rightX - b.leftX) * max(0.0, b.bottomY - b.topY)


def _views_to_fuse(instances: list) -> tuple[list, bool]:
    """Views to fuse for one object, and whether to weight them by box area.

    Takes and returns (sensorId, Object) pairs. Returns a flag rather than the
    weights, since a view without a usable bbox3d is dropped downstream. Only
    "rays" gates on visibility and weights by area.
    """
    if FUSION_METHOD in BASELINE_METHODS:
        return instances, False
    return [(sid, obj) for sid, obj in instances if _admits(obj)], True


def _distance_sq(sensor_id: str, coords: list) -> float:
    """Squared ground distance from a camera to the position it reported.

    A sensor missing from the calibration sorts last rather than first, so an
    incomplete calibration cannot quietly win "closest".
    """
    camera = CAMERAS.get(sensor_id)
    if camera is None:
        return float("inf")
    return (coords[0] - camera["x"]) ** 2 + (coords[1] - camera["y"]) ** 2


def _ray_geometry(sensor_id: str, coords: list):
    """(ray, normal, sigma_along, sigma_across) for one view, or None.

    Bottom-edge pixel error slides range, not bearing: for focal f, height h and
    slant d that is PIXEL_SIGMA*d^2/(f*h) along the ray against PIXEL_SIGMA*d/f
    across it. Ratio d/h; the pixel term only sets scale and cancels in weighting.
    """
    camera = CAMERAS.get(sensor_id)
    if camera is None:
        return None
    ux, uy = coords[0] - camera["x"], coords[1] - camera["y"]
    ground = math.hypot(ux, uy)
    if ground < 1e-9:
        return None
    ux, uy = ux / ground, uy / ground
    slant = math.hypot(ground, camera["h"])
    return ((ux, uy), (-uy, ux),
            PIXEL_SIGMA * slant * slant / (camera["f"] * camera["h"]),
            PIXEL_SIGMA * slant / camera["f"])


def _within_max_dist(sensor_id: str, coords: list) -> bool:
    """Close enough to its camera to be worth fusing. Uncalibrated views are kept:
    range unknown, and dropping them would quietly cut coverage."""
    if MAX_DIST <= 0:
        return True
    camera = CAMERAS.get(sensor_id)
    if camera is None:
        return True
    return math.hypot(coords[0] - camera["x"], coords[1] - camera["y"]) <= MAX_DIST


def _measurement_covariance(views: list) -> list:
    """2x2 covariance of the fused position, m^2. One view gives a thin ellipse along
    its ray, so the filter keeps range from prediction and takes only bearing."""
    a11 = a12 = a22 = 0.0
    for sensor_id, _obj, coords, _area in views:
        geometry = _ray_geometry(sensor_id, coords)
        if geometry is None:
            continue
        (ux, uy), (vx, vy), s_along, s_across = geometry
        w_along, w_across = 1.0 / (s_along * s_along), 1.0 / (s_across * s_across)
        a11 += w_along * ux * ux + w_across * vx * vx
        a12 += w_along * ux * uy + w_across * vx * vy
        a22 += w_along * uy * uy + w_across * vy * vy
    det = a11 * a22 - a12 * a12
    if abs(det) < 1e-15:
        # Geometry unknown: isotropic half-metre, claiming no more than is justified.
        return [[0.25, 0.0], [0.0, 0.25]]
    return [[a22 / det, -a12 / det], [-a12 / det, a11 / det]]


# Reached from the consumer and sweep threads (fuse_frames runs outside the service lock).
_TRACKS: dict[str, dict] = {}
_TRACKS_LOCK = threading.Lock()

# Each correction moves a view linearly in the offset, so the separation of one
# pair is sqrt(a + b*d + c*d^2). Three floats per pair is all the search needs.
_FOOT_PAIRS = collections.deque(maxlen=FOOT_OFFSET_WINDOW)
_FOOT_LOCK  = threading.Lock()
_FOOT_STATE = {"value": 0.0, "pairs": 0, "next_bucket": 0}


def foot_offset() -> float:
    """Offset currently applied, in metres."""
    if FOOT_OFFSET == "off": return 0.0
    if FOOT_OFFSET != "auto": return float(FOOT_OFFSET)
    with _FOOT_LOCK:
        return _FOOT_STATE["value"]


def _observe_foot_pairs(positioned: list, bucket_key: int = 0) -> None:
    """Record overlapping views for the offset estimate, as sqrt(a + b*d + c*d^2).

    Pairs beyond CONFLICT_RADIUS are two different people and say nothing about
    range. Called on RAW positions: the estimate is absolute, so feeding it
    corrected ones would measure the residual and walk it down to nothing.
    """
    if FOOT_OFFSET != "auto" or len(positioned) < 2: return
    if FOOT_OFFSET_STRIDE > 1 and bucket_key % FOOT_OFFSET_STRIDE: return
    rows = []
    for (s1, _o1, c1, _a1), (s2, _o2, c2, _a2) in itertools.combinations(positioned, 2):
        k1, k2 = CAMERAS.get(s1), CAMERAS.get(s2)
        if k1 is None or k2 is None or k1["h"] <= 0 or k2["h"] <= 0: continue
        if math.dist(c1[:2], c2[:2]) >= (CONFLICT_RADIUS or 2.0): continue
        ex, ey = c1[0] - c2[0], c1[1] - c2[1]
        mx = (c1[0] - k1["x"]) / k1["h"] - (c2[0] - k2["x"]) / k2["h"]
        my = (c1[1] - k1["y"]) / k1["h"] - (c2[1] - k2["y"]) / k2["h"]
        rows.append((ex * ex + ey * ey, 2.0 * (ex * mx + ey * my), mx * mx + my * my))
    if rows:
        with _FOOT_LOCK:
            _FOOT_PAIRS.extend(rows)


def _foot_cost(rows: list, d: float) -> float:
    """Mean separation between overlapping views when the offset is d."""
    total = 0.0
    for a, b, c in rows:
        s = a + d * (b + d * c)
        if s > 0.0:
            total += math.sqrt(s)
    return total / len(rows)


def _estimate_foot_offset(bucket_key: int) -> None:
    """Re-fit the offset that makes overlapping cameras agree.

    Ternary search, since the cost is unimodal: no grid, so nothing to
    interpolate. Mean separation not mean square -- the squared version is pulled
    by the tail and its answer moves with CONFLICT_RADIUS, which it should not.
    """
    if FOOT_OFFSET != "auto": return
    with _FOOT_LOCK:
        if bucket_key < _FOOT_STATE["next_bucket"]: return
        _FOOT_STATE["next_bucket"] = bucket_key + FOOT_OFFSET_EVERY
        if len(_FOOT_PAIRS) < FOOT_OFFSET_MIN_PAIRS:
            _FOOT_STATE["pairs"] = len(_FOOT_PAIRS)
            return
        rows = list(_FOOT_PAIRS)
    lo, hi = FOOT_OFFSET_MIN, FOOT_OFFSET_MAX
    for _ in range(FOOT_OFFSET_ITERS):
        third = (hi - lo) / 3.0
        if _foot_cost(rows, lo + third) < _foot_cost(rows, hi - third):
            hi -= third
        else:
            lo += third
    best = min(max((lo + hi) / 2.0, FOOT_OFFSET_MIN), FOOT_OFFSET_MAX)
    with _FOOT_LOCK:
        prev = _FOOT_STATE["value"]
        _FOOT_STATE["value"] = best
        _FOOT_STATE["pairs"] = len(rows)
    if abs(best - prev) > 1e-9:
        logger.info("Foot offset: %.4f m from %d overlapping view pairs (was %.4f m); "
                    "mean view disagreement %.3f m", best, len(rows), prev,
                    _foot_cost(rows, best))


def _apply_foot_offset(sensor_id: str, coords: list, offset: float) -> None:
    """Push one view away from its own camera by offset/h of its range, in place.
    Only along that camera's sight line: the bearing was never the biased part."""
    cam = CAMERAS.get(sensor_id)
    if cam is None or cam["h"] <= 0: return
    k = offset / cam["h"]
    coords[0] = cam["x"] + (coords[0] - cam["x"]) * (1.0 + k)
    coords[1] = cam["y"] + (coords[1] - cam["y"]) * (1.0 + k)


_LASTPUB: dict[str, tuple] = {}     # published id -> (bucket, x, y, consecutive rejects)
_LASTOUT: dict[str, tuple] = {}     # published id -> (bucket, x, y) actually emitted
_SPLITS:  dict[str, str]   = {}     # obj_id -> the id it is published under now
_ID_FREED: dict[str, int]  = {}     # id -> bucket it stopped being published in
_ID_MAX = 0                         # highest id seen in play


def published_id(obj_id: str) -> str:
    return _SPLITS.get(obj_id, obj_id)


def _free_id() -> str | None:
    """The id below the highest in play that has gone unused the longest.

    _LASTPUB holds every id still being published, the tracker's own as well as
    earlier splits, so anything absent from it is genuinely free and cannot
    collide with a track still on screen. Never-used ids count as the oldest.
    """
    best, oldest = None, None
    for i in range(1, _ID_MAX + 1):
        s = str(i)
        if s in _LASTPUB:
            continue
        freed = _ID_FREED.get(s, -1)
        if oldest is None or freed < oldest:
            best, oldest = s, freed
    return best


def _smoothing_relocates(pub: str, bucket_key: int, x: float, y: float) -> bool:
    """True if the smoothed position cannot follow what was last emitted.

    The backward pass runs after the gate, so it must refine a position rather
    than relocate it; when it would relocate, the causal estimate is kept.
    """
    if MAX_SPEED <= 0: return False
    with _TRACKS_LOCK:
        last = _LASTOUT.get(pub)
        if last is not None:
            gap = bucket_key - last[0]
            if 0 < gap <= GATE_MEMORY and \
               math.dist((x, y), last[1:3]) / (gap * BUCKET_MS / 1000.0) > MAX_SPEED:
                return True
        _LASTOUT[pub] = (bucket_key, x, y)
        return False


def _gate_log(branch: str, pub: str, gap: int, d: float) -> None:
    if GATE_DEBUG > 0 and d >= GATE_DEBUG:
        logger.info("[GATE] accepted %.2f m  branch=%s id=%s gap=%d", d, branch, pub, gap)


def _gate(obj_id: str, bucket_key: int, x: float, y: float) -> tuple[bool, str]:
    """(withhold this object?, id to publish it under).

    A position that cannot follow the last published one is withheld, until it
    has been withheld REACQUIRE times: then the move is real, or the id was
    reused, and refusing forever would strand the object.
    """
    pub = published_id(obj_id)
    if MAX_SPEED <= 0: return False, pub
    with _TRACKS_LOCK:
        last = _LASTPUB.get(pub)
        if last is None:
            _LASTPUB[pub] = (bucket_key, x, y, 0)
            return False, pub
        gap = bucket_key - last[0]
        d = math.dist((x, y), last[1:3])
        if gap <= 0:
            # Flushed out of order. Still judge it -- it gets published either way
            # -- but never let an older bucket move the reference forward.
            if d / (max(abs(gap), 1) * BUCKET_MS / 1000.0) > MAX_SPEED:
                return True, pub
            _gate_log("out-of-order", pub, gap, d)
            return False, pub
        if gap > GATE_MEMORY:               # absent long enough to be anywhere
            _gate_log("reacquire-gap", pub, gap, d)
            _LASTPUB[pub] = (bucket_key, x, y, 0)
            return False, pub
        if d / (gap * BUCKET_MS / 1000.0) <= MAX_SPEED:
            _gate_log("within-limit", pub, gap, d)
            _LASTPUB[pub] = (bucket_key, x, y, 0)
            return False, pub
        n = last[3] + 1
        if n < REACQUIRE:
            _LASTPUB[pub] = (last[0], last[1], last[2], n)
            return True, pub
        if SPLIT_ON_REACQUIRE:
            # Break the track rather than leap it across the map.
            fresh = _free_id()
            if fresh is None:
                return True, pub          # no id to give it: publish nothing
            _TRACKS.pop(pub, None); _LASTPUB.pop(pub, None); _LASTOUT.pop(pub, None)
            _SPLITS[obj_id] = pub = fresh
        _gate_log("after-refusals", pub, gap, d)
        _LASTPUB[pub] = (bucket_key, x, y, 0)
        return False, pub


def _predicted_position(obj_id: str, bucket_key: int):
    """Where the filter expects this track to be now, or None."""
    with _TRACKS_LOCK:
        t = _TRACKS.get(obj_id)
        if t is None: return None
        gap = bucket_key - t["bucket"]
        if not 0 < gap <= FILTER_RESET_BUCKETS: return None
        dt = gap * BUCKET_MS / 1000.0
        st = t["state"]
        return st[0] + dt * st[2], st[1] + dt * st[3]


def _resolve_conflict(obj_id: str, bucket_key: int, positioned: list):
    """Views of one id that cannot all be the same object, or None to decline.

    Keeps the subset consistent with where the track was heading. With no history
    there is no basis to choose, and the mean of two people is worse than nothing.
    """
    if CONFLICT_RADIUS <= 0 or len(positioned) < 2: return positioned
    if all(math.dist(a[2][:2], b[2][:2]) <= CONFLICT_RADIUS
           for a, b in itertools.combinations(positioned, 2)):
        return positioned
    pred = _predicted_position(obj_id, bucket_key)
    if pred is None: return None
    keep = [v for v in positioned if math.dist(v[2][:2], pred) <= CONFLICT_RADIUS]
    return keep or [min(positioned, key=lambda v: math.dist(v[2][:2], pred))]



def _filter_position(obj_id: str, bucket_key: int, x: float, y: float, cov: list):
    """Constant-velocity filter over one object's positions, returning (x, y).

    A track unseen for FILTER_RESET_BUCKETS restarts rather than being predicted
    across the gap. With SMOOTH_LAG set each step is kept for the backward pass.
    """
    with _TRACKS_LOCK:
        for stale in [k for k, t in _TRACKS.items()
                      if bucket_key - t["bucket"] > FILTER_RESET_BUCKETS]:
            del _TRACKS[stale]
        for stale in [k for k, v in _LASTPUB.items() if bucket_key - v[0] > GATE_MEMORY]:
            del _LASTPUB[stale]; _ID_FREED[stale] = bucket_key
        for stale in [k for k, v in _LASTOUT.items() if bucket_key - v[0] > GATE_MEMORY]:
            del _LASTOUT[stale]


        track = _TRACKS.get(obj_id)
        if track is None or bucket_key <= track["bucket"]:
            state = [x, y, 0.0, 0.0]
            P = [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 4.0, 0], [0, 0, 0, 4.0]]
            _TRACKS[obj_id] = track = {"bucket": bucket_key, "state": state, "cov": P,
                                       "hist": collections.deque(maxlen=SMOOTH_LAG + 2)}
            if SMOOTH_LAG:
                track["hist"].append({"bucket": bucket_key, "xf": state, "Pf": P,
                                      "xp": state, "Pp": P, "dt": 0.0})
            return x, y

        dt = (bucket_key - track["bucket"]) * BUCKET_MS / 1000.0
        state = track["state"]
        state = [state[0] + dt * state[2], state[1] + dt * state[3], state[2], state[3]]
        P = [row[:] for row in track["cov"]]
        for l in range(4):                       # rows: F P
            P[0][l] += dt * P[2][l]; P[1][l] += dt * P[3][l]
        for i in range(4):                       # columns: (F P) F^T
            P[i][0] += dt * P[i][2]; P[i][1] += dt * P[i][3]
        q = ACCEL_SIGMA ** 2
        d4, d3, d2 = dt ** 4 / 4 * q, dt ** 3 / 2 * q, dt * dt * q
        P[0][0] += d4; P[1][1] += d4; P[2][2] += d2; P[3][3] += d2
        P[0][2] += d3; P[2][0] += d3; P[1][3] += d3; P[3][1] += d3
        xp, Pp = state, [row[:] for row in P]

        S = [[P[0][0] + cov[0][0], P[0][1] + cov[0][1]],
             [P[1][0] + cov[1][0], P[1][1] + cov[1][1]]]
        det = S[0][0] * S[1][1] - S[0][1] * S[1][0]
        if abs(det) < 1e-15:
            track.update(bucket=bucket_key, state=state, cov=P)
            return state[0], state[1]
        Si = [[S[1][1] / det, -S[0][1] / det], [-S[1][0] / det, S[0][0] / det]]
        K = [[P[i][0] * Si[0][j] + P[i][1] * Si[1][j] for j in range(2)] for i in range(4)]
        dx, dy = x - state[0], y - state[1]
        state = [state[i] + K[i][0] * dx + K[i][1] * dy for i in range(4)]
        P = [[P[i][j] - (K[i][0] * P[0][j] + K[i][1] * P[1][j]) for j in range(4)]
             for i in range(4)]
        track.update(bucket=bucket_key, state=state, cov=P)
        if SMOOTH_LAG:
            track["hist"].append({"bucket": bucket_key, "xf": state, "Pf": P,
                                  "xp": xp, "Pp": Pp, "dt": dt})
        return state[0], state[1]


def _cholesky(A: list):
    """Lower-triangular L with L Lt = A, or None when A is not positive definite.
    A is a predicted covariance, so SPD by construction; None covers degeneracy."""
    n = len(A)
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            t = A[i][j] - sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                if t <= 1e-15: return None
                L[i][i] = math.sqrt(t)
            else:
                L[i][j] = t / L[j][j]
    return L


def _cholesky_solve(L: list, b: list) -> list:
    """x with L Lt x = b, by forward then back substitution."""
    n = len(L)
    y = [0.0] * n
    for i in range(n):
        y[i] = (b[i] - sum(L[i][k] * y[k] for k in range(i))) / L[i][i]
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (y[i] - sum(L[k][i] * x[k] for k in range(i + 1, n))) / L[i][i]
    return x


def _smoothed_position(obj_id: str, bucket_key: int):
    """RTS backward pass from the newest retained step down to bucket_key, or None.

    Reaches back at most SMOOTH_LAG steps. Only the state is recursed; the gain
    C = Pf Ft Pp^-1 is a Cholesky solve against the symmetric Pp.
    """
    with _TRACKS_LOCK:
        track = _TRACKS.get(obj_id)
        hist = list(track["hist"]) if track else []
    idx = next((i for i, h in enumerate(hist) if h["bucket"] == bucket_key), None)
    if idx is None or idx == len(hist) - 1:
        return None
    xs = list(hist[-1]["xf"])
    for i in range(len(hist) - 2, idx - 1, -1):
        nxt, Pf = hist[i + 1], hist[i]["Pf"]
        L = _cholesky(nxt["Pp"])
        if L is None: return None
        dt = nxt["dt"]
        # M = Pf Ft, with F the identity plus a velocity block: two columns move.
        M = [[Pf[r][0] + dt * Pf[r][2], Pf[r][1] + dt * Pf[r][3], Pf[r][2], Pf[r][3]]
             for r in range(4)]
        # C row r solves Pp x = M row r, since C Pp = M and Pp is symmetric.
        C = [_cholesky_solve(L, M[r]) for r in range(4)]
        d = [xs[l] - nxt["xp"][l] for l in range(4)]
        xf = hist[i]["xf"]
        xs = [xf[r] + sum(C[r][l] * d[l] for l in range(4)) for r in range(4)]
    return xs[0], xs[1]


def _element_wise_median(arrays: list[list[float]]) -> list[float]:
    """Per-coordinate median across the sensors reporting one object.

    Unweighted like the mean, but a view that puts the object somewhere else has
    to outnumber the rest to move the answer rather than merely outweigh them.
    """
    n = len(arrays)
    mid = n // 2
    return [(lambda c: c[mid] if n % 2 else (c[mid - 1] + c[mid]) / 2)(
                sorted(a[i] for a in arrays))
            for i in range(len(arrays[0]))]


def _ray_wls_xy(views: list) -> tuple[float, float] | None:
    """Ground position from views whose uncertainty is anisotropic, or None.

    A ground-plane projection is precise across the camera's line of sight and
    vague along it: a pixel of jitter on the box's bottom edge slides the range,
    not the bearing. For focal length f at height h and slant range d,
    sigma_along = delta*d^2/(f*h) against sigma_across = delta*d/f, so the ratio
    is d/h and the pixel term cancels: the weights are geometry, nothing fitted.

    One view alone solves to its own position, so fewer than two calibrated
    views return None and the caller falls back to the weighted mean.
    """
    a11 = a12 = a22 = b1 = b2 = 0.0
    used = 0
    for sensor_id, _obj, coords, _area in views:
        geometry = _ray_geometry(sensor_id, coords)
        if geometry is None:
            continue
        (ux, uy), (vx, vy), s_along, s_across = geometry
        w_along, w_across = 1.0 / (s_along * s_along), 1.0 / (s_across * s_across)
        m11 = w_along * ux * ux + w_across * vx * vx
        m12 = w_along * ux * uy + w_across * vx * vy
        m22 = w_along * uy * uy + w_across * vy * vy
        a11 += m11; a12 += m12; a22 += m22
        b1  += m11 * coords[0] + m12 * coords[1]
        b2  += m12 * coords[0] + m22 * coords[1]
        used += 1
    det = a11 * a22 - a12 * a12
    if used < 2 or abs(det) < 1e-12:
        return None
    return ((a22 * b1 - a12 * b2) / det, (a11 * b2 - a12 * b1) / det)


def _majority_type(instances: list) -> str:
    """Resolve type disagreement across sensors by majority vote.

    Each sensor instance casts one vote for its reported type. Ties are
    broken by total confidence (sum across the tied type's instances), and
    further ties fall back to first-seen order via Python's stable max().
    """
    type_counts: dict[str, int]   = defaultdict(int)
    type_conf:   dict[str, float] = defaultdict(float)
    for inst in instances:
        type_counts[inst.type] += 1
        type_conf[inst.type]   += inst.confidence
    return max(type_counts, key=lambda t: (type_counts[t], type_conf[t]))


# ---------------------------------------------------------------------------
# Fusion logic
# ---------------------------------------------------------------------------
def fuse_frames(bucket_key: int, sensor_frames: dict) -> schema_pb2.Frame:
    """
    Build a single fused Frame from a dict of {sensorId: Frame protobuf message}.
    """
    global _ID_MAX
    # --- average timestamps ---
    timestamps = [_parse_proto_timestamp(f.timestamp) for f in sensor_frames.values()]
    avg_ts_posix = sum(timestamps) / len(timestamps)

    # --- per-sensor timestamp info ---
    frame_info = {sid: _posix_to_rfc3339(_parse_proto_timestamp(f.timestamp))
                  for sid, f in sensor_frames.items()}

    # --- aggregate objects by ID ---
    # Sensor id travels with the object: the geometry-aware methods need to know
    # which camera reported a view, and buckets are filled in broker arrival
    # order, so sorting by it also makes every method independent of that order.
    objects_by_id: dict[str, list[tuple[str, schema_pb2.Object]]] = defaultdict(list)
    for sensor_id, frame in sorted(sensor_frames.items()):
        for obj in frame.objects:
            objects_by_id[obj.id].append((sensor_id, obj))

    _estimate_foot_offset(bucket_key)
    offset = foot_offset()

    fused_objects = []
    for obj_id, instances in objects_by_id.items():
        admitted, weighted = _views_to_fuse(instances)
        if not admitted:
            # No view is trustworthy
            continue

        positioned = [(sid, obj, list(obj.bbox3d.coordinates), _bbox_area(obj))
                      for sid, obj in admitted
                      if obj.HasField("bbox3d") and len(obj.bbox3d.coordinates) == 12]

        # Observe before correcting: the estimate is absolute, so feeding it
        # corrected positions would drive its own output to zero.
        _observe_foot_pairs(positioned, bucket_key)

        # Correct first, so the gates and every method see true ranges.
        if offset:
            for sid, _obj, coords, _area in positioned:
                _apply_foot_offset(sid, coords, offset)

        # Range gate before any method runs, so a distant view cannot pull the
        # position however the views are then combined.
        near = [v for v in positioned if _within_max_dist(v[0], v[2])]
        if near:
            positioned = near
            admitted = [(sid, obj) for sid, obj, _, _ in positioned]
        elif positioned:
            # Every view is beyond the gate: publishing the object from a view
            # this far out is what the gate exists to avoid.
            continue

        # Mis-associated ids: two views of different people wearing one id. Left
        # in, they drag the fused position to a point between two objects.
        positioned = _resolve_conflict(published_id(obj_id), bucket_key, positioned)
        if positioned is None:
            continue
        admitted = [(sid, obj) for sid, obj, _, _ in positioned]

        if FUSION_METHOD in ("first", "closest") and positioned:
            chosen = (positioned[0] if FUSION_METHOD == "first"
                      else min(positioned, key=lambda v: _distance_sq(v[0], v[2])))
            positioned = [chosen]
            admitted = [(chosen[0], chosen[1])]

        coord_arrays = [coords for _, _, coords, _ in positioned]
        weights      = [area   for _, _, _, area  in positioned]

        if not coord_arrays:
            avg_coords = [0.0] * 12
        elif FUSION_METHOD == "median":
            avg_coords = _element_wise_median(coord_arrays)
        else:
            avg_coords = _element_wise_mean(coord_arrays, weights if weighted else None)

        # The ray solution replaces only the ground position; the remaining ten
        # coordinates stay the weighted mean, since the anisotropy argument says
        # nothing about them. Fewer than two calibrated views leaves that mean in
        # place, which is also the whole no-calibration path.
        if FUSION_METHOD == "rays" and len(positioned) > 1:
            solved = _ray_wls_xy(positioned)
            if solved is not None:
                avg_coords[0], avg_coords[1] = solved

        if TEMPORAL_FILTER and coord_arrays:
            avg_coords[0], avg_coords[1] = _filter_position(
                published_id(obj_id), bucket_key, avg_coords[0], avg_coords[1],
                _measurement_covariance(positioned))

        if obj_id.isdigit(): _ID_MAX = max(_ID_MAX, int(obj_id))

        withhold, pub_id = ((False, published_id(obj_id)) if not coord_arrays
                            else _gate(obj_id, bucket_key, avg_coords[0], avg_coords[1]))
        if withhold:
            continue

        avg_conf = sum(obj.confidence for _, obj in admitted) / len(admitted)

        fused_obj = schema_pb2.Object()
        fused_obj.id         = pub_id
        fused_obj.type       = _majority_type([obj for _, obj in admitted])
        fused_obj.confidence = avg_conf
        fused_obj.bbox3d.coordinates[:] = avg_coords
        fused_obj.bbox3d.confidence     = avg_conf

        vis = [v for v in (_visibility(obj) for _, obj in admitted) if v is not None]
        if vis: fused_obj.info["visibility"] = f"{sum(vis) / len(vis):g}"

        fused_objects.append(fused_obj)

    # --- build fused Frame ---
    fused_frame = schema_pb2.Frame()
    fused_frame.version  = "4.0"
    fused_frame.id       = str(bucket_key)
    fused_frame.sensorId = FUSED_SENSOR_ID
    fused_frame.timestamp.CopyFrom(_posix_to_proto_timestamp(avg_ts_posix))
    fused_frame.objects.extend(fused_objects)
    fused_frame.info.update(frame_info)

    return fused_frame


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------
class MeasurementFusionService:
    def __init__(self):
        # Open buckets pending fusion. Keyed by bucket_key (= round(posix_ms / BUCKET_MS))
        # so frames from different DeepStream containers — which have independent frame
        # counters — are grouped by wall-clock time. Each entry:
        #   'frames':        {sensorId: Frame}   — sensors that have arrived so far
        #   'first_arrival': time.monotonic()    — wall-clock when the bucket was opened
        #   'first_posix':   earliest event-time seen in this bucket
        #   'last_posix':    latest   event-time seen in this bucket
        self._buffer: dict[int, dict] = {}
        # Recently-flushed bucket keys → monotonic close time. Rejects late stragglers
        # so the same fused Frame.id is never published twice (the "blinking" failure).
        self._closed_buckets: dict[int, float] = {}
        # Highest event-time ever observed, used as the watermark reference for the
        # event-time flush trigger. Monotonic; only ever moves forward.
        self._latest_seen_posix = 0.0

        self._lock = threading.Lock()
        self._shutdown = threading.Event()
        self._flush_timeout_ms = SENSOR_TIMEOUT_MS

        # Frames held back for the smoother, oldest first. Empty unless SMOOTH_LAG.
        self._pending: dict[int, schema_pb2.Frame] = {}

        # Counters surfaced in periodic INFO logs and final shutdown summary.
        self._published = 0
        self._received  = 0
        self._late_dropped = 0

    def _release_smoothed(self, up_to: int):
        """Publish every held frame at or before up_to, smoothed where possible.
        A track that has since ended keeps what the causal filter produced."""
        with self._lock:
            ready = [(b, self._pending.pop(b))
                     for b in sorted(b for b in self._pending if b <= up_to)]
        self._emit_smoothed(ready)

    def _emit_smoothed(self, ready):
        """Smooth and publish detached frames. Runs without the lock held."""
        for bucket, frame in ready:
            for obj in frame.objects:
                if len(obj.bbox3d.coordinates) != 12: continue
                pos = _smoothed_position(obj.id, bucket)
                if pos is not None and not _smoothing_relocates(obj.id, bucket, *pos):
                    obj.bbox3d.coordinates[0], obj.bbox3d.coordinates[1] = pos
                else:
                    # keep the causal position, and record what actually went out
                    _smoothing_relocates(obj.id, bucket,
                                         obj.bbox3d.coordinates[0], obj.bbox3d.coordinates[1])
            self._publish(frame.SerializeToString())
            self._published += 1

    def _mark_bucket_closed(self, bucket: int, now_mono: float | None = None):
        self._closed_buckets[bucket] = now_mono if now_mono is not None else time.monotonic()

    # --- Buffer management ---

    def _buffer_frame(self, frame: schema_pb2.Frame):
        posix = _parse_proto_timestamp(frame.timestamp)
        bucket = _ts_bucket_key(posix)
        sid = frame.sensorId
        now = time.monotonic()
        to_flush: set[int] = set()

        with self._lock:
            if bucket in self._closed_buckets:
                self._late_dropped += 1
                closed_age_ms = (now - self._closed_buckets[bucket]) * 1000.0
                logger.debug(
                    "[DBG] LATE        bucket=%d sensor=%s ts=%.3f "
                    "(closed %.0fms ago, total_late=%d)",
                    bucket, sid, posix, closed_age_ms, self._late_dropped,
                )
                if self._late_dropped % 100 == 0:
                    logger.info(
                        "Dropped %d late raw frames for already-closed buckets "
                        "(latest bucket=%d)",
                        self._late_dropped, bucket,
                    )
                return

            is_new_bucket = bucket not in self._buffer
            if is_new_bucket:
                self._buffer[bucket] = {
                    "frames": {},
                    "first_arrival": now,
                    "first_posix": posix,
                    "last_posix": posix,
                }
                logger.debug(
                    "[DBG] NEW_BUCKET  bucket=%d sensor=%s ts=%.3f "
                    "(open_buckets=%d)",
                    bucket, sid, posix, len(self._buffer),
                )
            entry = self._buffer[bucket]
            entry["frames"][sid] = frame
            entry["first_posix"] = min(entry["first_posix"], posix)
            entry["last_posix"] = max(entry["last_posix"], posix)
            self._latest_seen_posix = max(self._latest_seen_posix, posix)

            logger.debug(
                "[DBG] RECV        bucket=%d sensor=%s ts=%.3f "
                "→ %d/%d sensors %s",
                bucket, sid, posix,
                len(entry["frames"]), MAX_EXPECTED_SENSORS,
                sorted(entry["frames"].keys()),
            )

            ready = len(entry["frames"]) >= MAX_EXPECTED_SENSORS
            if ready:
                to_flush.add(bucket)
                logger.debug(
                    "[DBG] TRIG-ALL    bucket=%d sensors=%s",
                    bucket, sorted(entry["frames"].keys()),
                )

            # Event-time watermark flush. If the stream has already advanced
            # SENSOR_TIMEOUT_MS past a bucket's latest event-time, no more sensors
            # are realistically going to land in that bucket — flush it now.
            for queued_bucket, queued_entry in self._buffer.items():
                lag_ms = (self._latest_seen_posix - queued_entry["last_posix"]) * 1000.0
                if lag_ms >= self._flush_timeout_ms:
                    to_flush.add(queued_bucket)
                    if queued_bucket != bucket or not ready:
                        logger.debug(
                            "[DBG] TRIG-WMARK  bucket=%d lag_ms=%.1f "
                            "latest_seen=%.3f sensors=%s (missing %d)",
                            queued_bucket, lag_ms, self._latest_seen_posix,
                            sorted(queued_entry["frames"].keys()),
                            MAX_EXPECTED_SENSORS - len(queued_entry["frames"]),
                        )

        for flush_bucket in sorted(to_flush):
            self._flush_frame(flush_bucket)

    def _flush_frame(self, bucket: int):
        with self._lock:
            entry = self._buffer.pop(bucket, None)
            # Always mark closed (even on a no-op pop) so any late straggler for
            # this bucket key is rejected instead of resurrecting the bucket.
            self._mark_bucket_closed(bucket)
        if entry is None:
            logger.debug("[DBG] FLUSH-NOOP  bucket=%d (already popped)", bucket)
            return
        sensor_frames = entry["frames"]
        if not sensor_frames:
            logger.debug("[DBG] FLUSH-EMPTY bucket=%d (no sensors)", bucket)
            return
        try:
            age_ms = (time.monotonic() - entry["first_arrival"]) * 1000.0
            fused = fuse_frames(bucket, sensor_frames)
            if SMOOTH_LAG:
                # Hold this frame back: its positions improve once SMOOTH_LAG
                # later buckets let the filter look backwards at it.
                with self._lock:
                    self._pending[bucket] = fused
                self._release_smoothed(bucket - SMOOTH_LAG)
            else:
                self._publish(fused.SerializeToString())
                self._published += 1
            logger.debug(
                "[DBG] PUBLISH     bucket=%d sensors=%s (%d/%d) "
                "age=%.0fms event_ts=%.3f objects=%d",
                bucket, sorted(sensor_frames.keys()),
                len(sensor_frames), MAX_EXPECTED_SENSORS,
                age_ms,
                _parse_proto_timestamp(fused.timestamp),
                len(fused.objects),
            )
            if self._published % 100 == 0:
                logger.info(
                    "Published %d fused frames (received %d raw frames, "
                    "last bucket=%d, sensors=%s)",
                    self._published, self._received,
                    bucket, list(sensor_frames.keys()),
                )
        except Exception as exc:
            logger.error("Failed to fuse/publish bucket=%d: %s", bucket, exc)

    # --- Background sweep thread ---

    def _sweep_loop(self):
        stale_s = BUFFER_DURATION_S
        closed_retention_s = CLOSED_BUCKET_RETENTION_MS / 1000.0

        while not self._shutdown.is_set():
            now_mono = time.monotonic()
            to_flush = []
            to_drop  = []

            drop_info: list[tuple[int, float, list]] = []
            flush_info: list[tuple[int, float, list]] = []

            with self._lock:
                for bucket, entry in list(self._buffer.items()):
                    age_arrival = now_mono - entry["first_arrival"]

                    if age_arrival >= stale_s:
                        # Pathological case: bucket has somehow lived past
                        # BUFFER_DURATION_S. Drop to bound memory; the lost
                        # frame is preferable to unbounded growth.
                        to_drop.append(bucket)
                        drop_info.append((
                            bucket, age_arrival * 1000.0,
                            sorted(entry["frames"].keys()),
                        ))
                    # Arrival-time fallback for when event-time does not advance
                    # (entire stream silent, broker stall, sparse sensors). Without
                    # this, buckets in those cases would never flush at all.
                    elif age_arrival * 1000.0 >= self._flush_timeout_ms:
                        to_flush.append(bucket)
                        flush_info.append((
                            bucket, age_arrival * 1000.0,
                            sorted(entry["frames"].keys()),
                        ))

                # Only remove stale entries here; flush entries are popped below
                for bucket in to_drop:
                    self._buffer.pop(bucket, None)
                    self._mark_bucket_closed(bucket, now_mono)

                # Bound _closed_buckets memory: drop entries older than the
                # retention window. The bucket key itself is no longer reachable
                # in event time by that point, so further rejection is unneeded.
                if self._closed_buckets:
                    stale_closed = [
                        b for b, closed_at in self._closed_buckets.items()
                        if (now_mono - closed_at) >= closed_retention_s
                    ]
                    for b in stale_closed:
                        self._closed_buckets.pop(b, None)
                    if stale_closed:
                        logger.debug(
                            "[DBG] PRUNE       %d closed buckets pruned (retention=%.0fms)",
                            len(stale_closed), closed_retention_s * 1000,
                        )

            for b, age_ms, sensors in drop_info:
                logger.debug(
                    "[DBG] DROP-STALE  bucket=%d age=%.0fms sensors=%s "
                    "(exceeded BUFFER_DURATION_S=%.1fs)",
                    b, age_ms, sensors, stale_s,
                )

            for b, age_ms, sensors in flush_info:
                logger.debug(
                    "[DBG] TRIG-SWEEP  bucket=%d age=%.0fms sensors=%s "
                    "(missing %d)",
                    b, age_ms, sensors,
                    MAX_EXPECTED_SENSORS - len(sensors),
                )

            for bucket in to_flush:
                self._flush_frame(bucket)

            self._shutdown.wait(SWEEP_INTERVAL_S)

    # --- Broker abstractions ---

    def _publish(self, data: bytes):
        if BROKER_TYPE == "redis":
            self._redis.xadd(FUSED_TOPIC, {"key": FUSED_SENSOR_ID, "value": data, "headers": "{}"})
        else:
            self._producer.produce(FUSED_TOPIC, value=data)
            self._producer.poll(0)

    def _run_kafka(self):
        from confluent_kafka import Consumer, Producer, KafkaException

        self._producer = Producer({
            "bootstrap.servers": KAFKA_BOOTSTRAP,
            "linger.ms":         5,
            "acks":              "1",
        })
        group_id = f"mdx-measurement-fusion-{uuid.uuid4().hex[:8]}"
        consumer = Consumer({
            "bootstrap.servers":  KAFKA_BOOTSTRAP,
            "group.id":           group_id,
            "auto.offset.reset":  "latest",
            "enable.auto.commit": True,
        })

        logger.info(
            "Measurement fusion (kafka): %s → %s  "
            "(timeout=%.0fms, max_sensors=%d, buffer=%.1fs, bucket=%.0fms)",
            RAW_TOPIC, FUSED_TOPIC, self._flush_timeout_ms, MAX_EXPECTED_SENSORS, BUFFER_DURATION_S, BUCKET_MS,
        )

        try:
            consumer.subscribe([RAW_TOPIC])
            logger.info("Subscribed to %s @ %s", RAW_TOPIC, KAFKA_BOOTSTRAP)
            with open("/tmp/fusion_ready", "w") as _f:
                _f.write("ready\n")

            while not self._shutdown.is_set():
                msg = consumer.poll(timeout=CONSUMER_POLL_MS / 1000.0)
                if msg is None:
                    continue
                if msg.error():
                    logger.warning("Consumer error: %s", msg.error())
                    continue
                try:
                    frame = schema_pb2.Frame()
                    frame.ParseFromString(msg.value())
                    self._received += 1
                    self._buffer_frame(frame)
                except Exception as exc:
                    logger.error("Failed to parse message: %s", exc)

        except KafkaException as exc:
            logger.error("Kafka exception: %s", exc)
        finally:
            self._shutdown.set()
            # Nothing more is coming, so held frames are published as they stand
            # rather than being dropped on the floor.
            try:
                with self._lock:
                    up_to = max(self._pending) if self._pending else 0
                self._release_smoothed(up_to)
            except Exception:
                pass
            try:
                consumer.close()
            except Exception:
                pass
            try:
                self._producer.flush(timeout=5)
            except Exception:
                pass

    def _run_redis(self):
        import redis as redis_lib

        self._redis = redis_lib.Redis(host=REDIS_HOST, port=REDIS_PORT)
        self._redis.ping()

        logger.info(
            "Measurement fusion (redis): %s → %s  "
            "(timeout=%.0fms, max_sensors=%d, buffer=%.1fs, bucket=%.0fms)",
            RAW_TOPIC, FUSED_TOPIC, self._flush_timeout_ms, MAX_EXPECTED_SENSORS, BUFFER_DURATION_S, BUCKET_MS,
        )

        # Start from the tip of the stream (only new messages)
        last_id = "$"
        with open("/tmp/fusion_ready", "w") as _f:
            _f.write("ready\n")
        logger.info("Subscribed to Redis stream %s @ %s:%d", RAW_TOPIC, REDIS_HOST, REDIS_PORT)

        while not self._shutdown.is_set():
            try:
                results = self._redis.xread(
                    {RAW_TOPIC: last_id},
                    count=100,
                    block=int(CONSUMER_POLL_MS),
                )
                if not results:
                    continue
                for _stream, messages in results:
                    for msg_id, fields in messages:
                        last_id = msg_id
                        data = fields.get(b"value")
                        if data is None:
                            continue
                        try:
                            frame = schema_pb2.Frame()
                            frame.ParseFromString(data)
                            self._received += 1
                            self._buffer_frame(frame)
                        except Exception as exc:
                            logger.error("Failed to parse Redis message: %s", exc)
            except Exception as exc:
                if not self._shutdown.is_set():
                    logger.error("Redis read error: %s", exc)
                    time.sleep(1.0)

    # --- Main loop ---

    def run(self):
        def _on_signal(signum, _frame):
            logger.info("Received signal %d — shutting down", signum)
            self._shutdown.set()

        signal.signal(signal.SIGINT,  _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)

        sweep_thread = threading.Thread(target=self._sweep_loop, daemon=True, name="sweep")
        sweep_thread.start()

        try:
            if BROKER_TYPE == "redis":
                self._run_redis()
            else:
                self._run_kafka()
        finally:
            self._shutdown.set()
            sweep_thread.join(timeout=2.0)
            logger.info(
                "Shutdown complete. Received=%d  Published=%d  LateDropped=%d",
                self._received, self._published, self._late_dropped,
            )


if __name__ == "__main__":
    MeasurementFusionService().run()
