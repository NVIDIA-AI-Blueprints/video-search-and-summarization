# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
#
# MV3DT config init entrypoint (distroless — no shell available).
#
# Obtains calibration.json (from the VSS video-analytics API by default, or
# from the shared calibration PVC when CALIBRATION_API_URL is empty), validates
# it, then generates the MV3DT perception configs required by the DeepStream
# pipeline:
#
#   ${CAM_INFO_OUTPUT_DIR}/<sensor_id>.yml    (one per camera)
#   ${PUB_SUB_OUTPUT_DIR}/pub_sub_info_config.yml
#
# Environment variables:
#   CALIBRATION_API_URL      VSS video-analytics endpoint to fetch calibration from.
#                            Default: http://vss-video-analytics-api:8081/config/calibration?emptyIfNotFound=false
#                            Set to an empty string to fall back to polling
#                            CALIBRATION_JSON_PATH on the shared volume
#                            (Docker Compose / local test behaviour).
#   CALIBRATION_JSON_PATH    Shared-volume calibration.json, used only by the
#                            fallback path when CALIBRATION_API_URL is empty.
#                            Default: /calibration/calibration.json
#   CALIBRATION_FETCH_PATH   Where API-fetched calibration is written. Must be
#                            writable — the shared calibration volume is mounted
#                            read-only. Default: /tmp/calibration/calibration.json
#   CALIBRATION_POLL_INTERVAL Seconds between retries. Default: 10
#   CAM_INFO_OUTPUT_DIR      Destination directory for camInfo YAML files. Default: /tmp/camInfo
#   PUB_SUB_OUTPUT_DIR       Destination directory for pub_sub_info_config.yml. Default: /tmp/generated
#   CALIBRATION_WAIT_TIMEOUT Seconds to wait for calibration.json. Default: 3600
#   CLASS_SPECS              Object model dimensions as ";"-separated
#                            "classID,height,radius" entries (SI units: metres);
#                            whitespace around tokens is ignored. Example:
#                            "0, 1.60, 0.3; 1, 0.80, 0.4". Default covers classes
#                            0-5 (see DEFAULT_CLASS_SPECS below).
#   MQTT_HOST                MQTT broker hostname for pub/sub topology. Default: localhost
#   MQTT_PORT                MQTT broker port. Default: 1883
#   RANGE_OF_INTEREST        Optional world-plane ROI "x1,y1,x2,y2" in metres.
#                            Auto-computed from camera positions when unset.
#   NEIGHBOR_CRITERIA        Optional FOV overlap criteria passed to generate_pub_sub_configs.
#                            "top_N:<N>" or "overlap_threshold:<float>".
#   MINIMUM_OBJECT_SIZE      Optional minimum object height in pixels for FOV mask. Default: 50

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Generation scripts are installed alongside this file under /app.
sys.path.insert(0, "/app")
from generate_cam_info_configs import generate_cam_info_files, _parse_model_args  # noqa: E402
from generate_pub_sub_configs import (  # noqa: E402
    get_overlap_matrix,
    get_subscription_map,
    load_and_process_camera_matrices,
)

import numpy as np  # noqa: E402
import yaml  # noqa: E402

# Default object model dimensions. Each class is "classID,height,radius"
# (metres); classes are separated by ";". Whitespace around tokens is ignored.
# Covers MTMC object classes 0-5.
DEFAULT_CLASS_SPECS = (
    "0,1.60,0.3;"
    "1,1.60,0.3;"
    "2,1.60,0.3;"
    "3,0.48,0.3;"
    "4,0.2,0.52;"
    "5,2.2,0.9"
)


DEFAULT_CALIBRATION_API_URL = (
    "http://vss-video-analytics-api:8081/config/calibration?emptyIfNotFound=false"
)


def _log(msg: str) -> None:
    print(f"[mv3dt-config-init] {msg}", flush=True)


def _die(msg: str) -> None:
    print(f"[mv3dt-config-init][ERROR] {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Phase 1 — obtain calibration.json
#
# Preferred source is the VSS video-analytics API. Setting CALIBRATION_API_URL
# to an empty string restores the original behaviour of polling the shared
# calibration volume, which keeps Docker Compose and tests/test_config_init.sh
# working unchanged.
# ---------------------------------------------------------------------------

def _is_populated(data: object) -> bool:
    """Guard against the API's empty-but-200 response.

    /config/calibration answers 200 with an empty payload while calibration is
    still unconfigured, so a status-code check alone yields a false positive.
    Mirrors the check Chirag runs in the Helm chart's inline init step.
    """
    return (
        isinstance(data, dict)
        and bool(data.get("sensors"))
        and bool(data.get("calibrationType"))
    )


def fetch_calibration(api_url: str, calib_path: Path, timeout_s: int, poll_s: int) -> None:
    """Poll the calibration API until it returns a populated payload, then persist it.

    The destination must be writable. The shared calibration volume is mounted
    read-only in the Helm chart (the container only ever read from it before),
    so CALIBRATION_FETCH_PATH defaults to scratch space instead.
    """
    _log(f"Fetching calibration from {api_url} (timeout {timeout_s}s)...")

    # Fail fast on an unwritable destination rather than discovering it after a
    # successful fetch — and never treat it as a retryable condition.
    try:
        calib_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _die(
            f"Cannot create {calib_path.parent}: {exc}. "
            f"Set CALIBRATION_FETCH_PATH to a writable location."
        )

    deadline = time.monotonic() + timeout_s

    while True:
        data = None
        try:
            with urllib.request.urlopen(api_url, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (OSError, ValueError) as exc:
            # URLError subclasses OSError and JSONDecodeError subclasses
            # ValueError, so these two cover every transient fetch failure.
            # NOTE: keep the file write outside this block — OSError also covers
            # read-only mounts, which are permanent and must not be retried.
            _log(f"Fetch error: {exc}")

        if data is not None:
            if _is_populated(data):
                # Write failures are permanent (read-only mount, no permission).
                # Retrying would just spin until the timeout, so exit loudly.
                try:
                    calib_path.write_text(json.dumps(data), encoding="utf-8")
                except OSError as exc:
                    _die(
                        f"Cannot write calibration to {calib_path}: {exc}. "
                        f"Set CALIBRATION_FETCH_PATH to a writable location."
                    )
                _log(f"Fetched calibration -> {calib_path}")
                return
            _log("Calibration empty/unconfigured; retrying")

        if time.monotonic() >= deadline:
            _die(f"Timeout after {timeout_s}s fetching calibration from {api_url}")
        time.sleep(poll_s)


def wait_for_calibration(calib_path: Path, timeout_s: int, poll_s: int) -> None:
    _log(f"Waiting for calibration.json at {calib_path} (timeout {timeout_s}s)...")
    elapsed = 0
    while not calib_path.exists():
        if elapsed >= timeout_s:
            _die(f"Timeout after {timeout_s}s: {calib_path} not found")
        _log(f"... still waiting ({elapsed}s elapsed)")
        time.sleep(poll_s)
        elapsed += poll_s
    _log(f"Found: {calib_path}")


# ---------------------------------------------------------------------------
# Phase 2 — validate calibration.json
# ---------------------------------------------------------------------------

def validate_calibration(calib_path: Path) -> None:
    _log("Validating calibration.json...")
    try:
        data = json.loads(calib_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _die(f"calibration.json is not valid JSON: {exc}")

    sensors = data.get("sensors")
    if not isinstance(sensors, list):
        _die("calibration.json missing 'sensors' list")

    cameras = [s for s in sensors if isinstance(s, dict) and s.get("type") == "camera"]
    if not cameras:
        _die("No camera-type sensors found in calibration.json")

    missing_matrix = [s.get("id", "<unknown>") for s in cameras if "cameraMatrix" not in s]
    if missing_matrix:
        _die(f"Camera(s) missing 'cameraMatrix': {missing_matrix}")

    invalid_matrix = [
        s.get("id", "<unknown>")
        for s in cameras
        if not (
            isinstance(s["cameraMatrix"], list)
            and len(s["cameraMatrix"]) == 3
            and all(isinstance(r, list) and len(r) == 4 for r in s["cameraMatrix"])
        )
    ]
    if invalid_matrix:
        _die(f"Camera(s) have invalid cameraMatrix shape (expected 3×4): {invalid_matrix}")

    _log(f"Validation passed: {len(cameras)} camera(s) found")


# ---------------------------------------------------------------------------
# Phase 3 — generate camInfo YAML files
# ---------------------------------------------------------------------------

def generate_cam_info(calib_path: Path, cam_info_dir: Path, class_specs_str: str) -> None:
    _log(f"Generating camInfo files -> {cam_info_dir}/")
    cam_info_dir.mkdir(parents=True, exist_ok=True)

    # Parse CLASS_SPECS: ";"-separated "classID,height,radius" entries.
    # Whitespace around any token is ignored.
    raw_triples = []
    for spec in class_specs_str.split(";"):
        spec = spec.strip()
        if not spec:
            continue
        fields = [tok.strip() for tok in spec.split(",")]
        if len(fields) != 3:
            _die(f"CLASS_SPECS entry must be 'classID,height,radius', got: {spec!r}")
        raw_triples.append(fields)
    if not raw_triples:
        _die(f"CLASS_SPECS is empty or invalid: {class_specs_str!r}")
    model_entries = _parse_model_args(raw_triples)

    count = generate_cam_info_files(calib_path, cam_info_dir, model_entries)
    _log(f"Generated {count} camInfo file(s)")


# ---------------------------------------------------------------------------
# Phase 4 — generate pub_sub_info_config.yml
# ---------------------------------------------------------------------------

def generate_pub_sub(
    cam_info_dir: Path,
    generated_dir: Path,
    mqtt_brokers: str,
    range_of_interest: str,
    neighbor_criteria: str,
    min_object_size: str,
) -> None:
    _log(f"Generating pub_sub_info_config.yml -> {generated_dir}/")
    generated_dir.mkdir(parents=True, exist_ok=True)

    cam_matrices, cam_names = load_and_process_camera_matrices(str(cam_info_dir))
    cam_ids = sorted(cam_matrices.keys())
    n = len(cam_ids)
    _log(f"Loaded {n} cameras: {[cam_names[c] for c in cam_ids]}")

    brokers = [b.strip() for b in mqtt_brokers.split(",")]
    num_instances = len(brokers)
    block_size = (n + num_instances - 1) // num_instances
    cam2instance = {cam: min((cam - 1) // block_size, num_instances - 1) for cam in cam_ids}
    _log(f"Distributing {n} cameras across {num_instances} broker(s), ~{block_size} per broker")

    if range_of_interest:
        x1, y1, x2, y2 = map(float, range_of_interest.split(","))
        roi = np.array([x1, y1, x2, y2], dtype=float)
    else:
        range_padding = 20
        poses = [cam_matrices[c][5] for c in cam_ids]
        roi = np.array(
            [
                min(p[0][0] for p in poses) - range_padding,
                min(p[1][0] for p in poses) - range_padding,
                max(p[0][0] for p in poses) + range_padding,
                max(p[1][0] for p in poses) + range_padding,
            ],
            dtype=float,
        )

    min_size = int(min_object_size) if min_object_size else 50
    criteria = neighbor_criteria or "overlap_threshold:1e-6"

    overlap_matrix = get_overlap_matrix(cam_matrices, min_size, roi, cam_names=cam_names)
    subscription_map = get_subscription_map(overlap_matrix, criteria)

    config: dict = {"pubBrokerTopicStr": {}, "subPeerBrokerTopicStrs": {}}
    for cam in cam_ids:
        name = cam_names[cam]
        broker = brokers[cam2instance[cam]]
        config["pubBrokerTopicStr"][name] = f"{broker};/trck/{name}"
        config["subPeerBrokerTopicStrs"][name] = [
            f"{brokers[cam2instance[nei]]};/trck/{cam_names[nei]}"
            for nei in subscription_map[cam]
        ]

    out = generated_dir / "pub_sub_info_config.yml"
    out.write_text(yaml.dump(config, default_flow_style=False), encoding="utf-8")
    _log(f"Written: {out}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    calib_path    = Path(os.environ.get("CALIBRATION_JSON_PATH", "/calibration/calibration.json"))
    cam_info_dir  = Path(os.environ.get("CAM_INFO_OUTPUT_DIR",   "/tmp/camInfo"))
    generated_dir = Path(os.environ.get("PUB_SUB_OUTPUT_DIR",  "/tmp/generated"))
    timeout_s     = int(os.environ.get("CALIBRATION_WAIT_TIMEOUT", "3600"))
    fetch_path    = Path(os.environ.get("CALIBRATION_FETCH_PATH", "/tmp/calibration/calibration.json"))
    api_url       = os.environ.get("CALIBRATION_API_URL", DEFAULT_CALIBRATION_API_URL).strip()
    poll_s        = int(os.environ.get("CALIBRATION_POLL_INTERVAL", "10"))
    class_specs   = os.environ.get("CLASS_SPECS", DEFAULT_CLASS_SPECS)
    mqtt_host     = os.environ.get("MQTT_HOST", "localhost")
    mqtt_port     = os.environ.get("MQTT_PORT", "1883")
    mqtt_brokers  = f"{mqtt_host}:{mqtt_port}"
    roi           = os.environ.get("RANGE_OF_INTEREST", "")
    criteria      = os.environ.get("NEIGHBOR_CRITERIA", "")
    min_obj_size  = os.environ.get("MINIMUM_OBJECT_SIZE", "")

    if api_url:
        # CALIBRATION_JSON_PATH points at the read-only shared volume; fetched
        # calibration has to land somewhere writable instead.
        calib_path = fetch_path
        fetch_calibration(api_url, calib_path, timeout_s, poll_s)
    else:
        wait_for_calibration(calib_path, timeout_s, poll_s)
    validate_calibration(calib_path)
    generate_cam_info(calib_path, cam_info_dir, class_specs)
    generate_pub_sub(cam_info_dir, generated_dir, mqtt_brokers, roi, criteria, min_obj_size)

    _log("Config generation complete.")
    _log(f"  camInfo files ({cam_info_dir}/):")
    for f in sorted(cam_info_dir.iterdir()):
        _log(f"    {f.name}")
    _log(f"  Generated files ({generated_dir}/):")
    for f in sorted(generated_dir.iterdir()):
        _log(f"    {f.name}")


if __name__ == "__main__":
    main()
