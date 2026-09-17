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
# Polls CALIBRATION_API_URL until it returns a populated calibration payload,
# validates it, and writes it to CALIBRATION_OUTPUT_PATH.
#
# Environment variables:
#   CALIBRATION_API_URL       Required.
#   CALIBRATION_OUTPUT_PATH   Required.
#   CALIBRATION_WAIT_TIMEOUT  Seconds to wait before giving up. Default: 300
#   CALIBRATION_POLL_INTERVAL Seconds between retries. Default: 10

import json
import os
import sys
import time
import urllib.request

api_url = os.environ["CALIBRATION_API_URL"]
out_path = os.environ["CALIBRATION_OUTPUT_PATH"]
timeout_s = int(os.environ.get("CALIBRATION_WAIT_TIMEOUT", "300"))
poll_s = int(os.environ.get("CALIBRATION_POLL_INTERVAL", "10"))


def log(msg):
    print(f"[fetch-calibration] {msg}", flush=True)


def die(msg):
    print(f"[fetch-calibration][ERROR] {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def is_populated(data):
    # /config/calibration answers 200 with an empty payload while calibration
    # is still unconfigured; a status check alone is a false positive.
    return (
        isinstance(data, dict)
        and bool(data.get("sensors"))
        and bool(data.get("calibrationType"))
    )


def validate(data):
    sensors = data.get("sensors")
    if not isinstance(sensors, list):
        die("calibration.json missing 'sensors' list")
    cameras = [s for s in sensors if isinstance(s, dict) and s.get("type") == "camera"]
    if not cameras:
        die("No camera-type sensors found in calibration.json")
    missing = [s.get("id", "<unknown>") for s in cameras if "cameraMatrix" not in s]
    if missing:
        die(f"Camera(s) missing 'cameraMatrix': {missing}")
    invalid = [
        s.get("id", "<unknown>")
        for s in cameras
        if not (
            isinstance(s["cameraMatrix"], list)
            and len(s["cameraMatrix"]) == 3
            and all(isinstance(r, list) and len(r) == 4 for r in s["cameraMatrix"])
        )
    ]
    if invalid:
        die(f"Camera(s) have invalid cameraMatrix shape (expected 3x4): {invalid}")
    log(f"Validation passed: {len(cameras)} camera(s) found")


def main():
    log(f"Fetching calibration from {api_url} (timeout {timeout_s}s)...")
    deadline = time.monotonic() + timeout_s
    while True:
        data = None
        try:
            with urllib.request.urlopen(api_url, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (OSError, ValueError) as exc:
            log(f"Fetch error: {exc}")
        if data is not None and is_populated(data):
            validate(data)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            log(f"Fetched calibration -> {out_path}")
            return
        if data is not None:
            log("Calibration empty/unconfigured; retrying")
        if time.monotonic() >= deadline:
            die(f"Timeout after {timeout_s}s fetching calibration from {api_url}")
        time.sleep(poll_s)


if __name__ == "__main__":
    main()
