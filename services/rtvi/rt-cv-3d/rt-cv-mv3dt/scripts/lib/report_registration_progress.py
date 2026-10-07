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
"""Report how many streams are registered and which are still missing.

Used by scripts/add-streams.sh. Standard library only: add-streams.sh runs on the
host with the system python3, before any venv exists.

MV3DT batches its sources, so a partially registered set produces no frames at
all. Without this the operator sees a successful add and a silent pipeline with no
way to tell the deployment is still waiting for cameras (bug 6564472).

Expected camera ids are resolved by camera_counts.py, which add-streams.sh also
uses for its registered/required count so the two agree. Streams the API reports with
no camera_id are matched back by source_id position, which is how it reports
sources registered before this run.

Usage:  STREAM_INFO_PAYLOAD=<json> [NUM_CAMS_VALUE=<n>]
        report_registration_progress.py COMPONENT_ROOT [CAMERA_ID...]
Exit:   always 0. This reports, it does not judge
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from camera_counts import parse_int, registered_count, resolve_expected  # noqa: E402

root, known_registered = sys.argv[1], sys.argv[2:]


payload = os.environ.get("STREAM_INFO_PAYLOAD", "")
try:
    info = json.loads(payload or "").get("stream-info", {})
except (AttributeError, json.JSONDecodeError):
    sys.exit(0)
if not isinstance(info, dict):
    sys.exit(0)

streams = info.get("stream-info", [])
streams = streams if isinstance(streams, list) else []
registered = registered_count(payload)
required, expected_ids = resolve_expected(root, os.environ.get("NUM_CAMS_VALUE"))
if not required:
    sys.exit(0)

registered_ids, unnamed_sources = set(), []
for stream in streams:
    if not isinstance(stream, dict):
        continue
    camera_id = stream.get("camera_id")
    if isinstance(camera_id, str) and camera_id:
        registered_ids.add(camera_id)
    else:
        unnamed_sources.append(parse_int(stream.get("source_id"), 0))

for camera_id in known_registered:
    if len(registered_ids) >= registered:
        break
    registered_ids.add(camera_id)
for source_id in unnamed_sources:
    if len(registered_ids) >= registered:
        break
    if source_id is not None and source_id < len(expected_ids):
        registered_ids.add(expected_ids[source_id])

missing = [camera_id for camera_id in expected_ids if camera_id not in registered_ids]
remaining = max(required - registered, 0)

print(f"Registered streams: {registered}/{required}")
if registered < required:
    print(f"INFO: MV3DT requires {required} streams.")
    if missing:
        print("Waiting for: " + ", ".join(missing))
    elif remaining:
        suffix = "stream" if remaining == 1 else "streams"
        print(f"Waiting for {remaining} additional {suffix}.")
