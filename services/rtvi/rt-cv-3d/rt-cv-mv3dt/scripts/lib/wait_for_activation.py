#!/usr/bin/env python3
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
"""Wait for registered MV3DT sources to produce frames.

Used by scripts/add-streams.sh. Standard library only: add-streams.sh runs on the
host with the system python3, before any venv exists, so this must not grow a
dependency on utils/requirements.txt.

Liveness is frame_number advancing, not membership of stream-stats. That list is a
rolling buffer of recent per-source samples, not one row per stream: a single
payload can carry the same sensor_id twice with consecutive frame numbers, and
stream-count can exceed the number of distinct sensors in it (bug 6558487). A
source that is decoding therefore shows up repeatedly across a few seconds of
polling with a rising frame_number, while one that is not either never appears or
stays frozen, observed live at frame_number 296 while DeepStream retried its RTSP
connect. Both count as not producing.

Usage:  BASE=<url> ACTIVATION_TIMEOUT=<seconds> wait_for_activation.py CAM [CAM...]

Exit codes, which add-streams.sh distinguishes:
  0  every requested camera advanced its frame_number
  1  at least one did not, with STATIC/UNSEEN lines and OBS samples on stdout
  3  no metrics endpoint at all, an older perception build
  4  the endpoint answered but never named a single stream, so the check is blind
"""
import json
import os
import sys
import time
import urllib.request

base = os.environ["BASE"]
timeout = int(os.environ["ACTIVATION_TIMEOUT"])
wanted = [c for c in sys.argv[1:] if c]

# Bypass any http_proxy in the environment: this endpoint is local.
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def sample():
    """{sensor_id: frame_number}, or None when the endpoint is not there."""
    try:
        with opener.open(base + "/api/v1/metrics", timeout=5) as resp:
            payload = json.load(resp)
    except Exception:
        return None
    stats = payload.get("metrics-info", {}).get("stream-stats")
    if not isinstance(stats, list):
        return {}
    seen = {}
    for entry in stats:
        if not isinstance(entry, dict):
            continue
        sensor = entry.get("sensor_id")
        try:
            frames = int(entry.get("frame_number"))
        except (TypeError, ValueError):
            continue
        if sensor is not None:
            seen[str(sensor)] = frames
    return seen


if sample() is None:
    sys.exit(3)          # older perception build, no metrics endpoint

baseline, producing, last = {}, set(), {}
deadline = time.monotonic() + timeout
while True:
    current = sample()
    if current is None:
        sys.exit(3)
    for sensor, frames in current.items():
        if sensor not in baseline:
            baseline[sensor] = frames
        elif frames > baseline[sensor]:
            producing.add(sensor)
    if all(cam in producing for cam in wanted):
        sys.exit(0)
    last = current
    if time.monotonic() >= deadline:
        break
    time.sleep(2)

# The endpoint answered every time but never named a single stream. Two states
# look identical from here and this cannot separate them: statistics are not being
# collected (nvdslogger off in the enabled sink), or nothing decoded. The caller
# says so rather than guessing.
if not baseline:
    sys.exit(4)

for cam in wanted:
    if cam in producing:
        continue
    print(("STATIC " if cam in baseline else "UNSEEN ") + cam)
for sensor in sorted(last or {}):
    print("OBS %s frame_number=%s" % (sensor, last[sensor]))
sys.exit(1)
