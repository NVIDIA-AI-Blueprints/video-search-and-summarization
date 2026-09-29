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
"""Resolve how many cameras a deployment expects, and which ids they are.

Used by scripts/add-streams.sh and by report_registration_progress.py, which must
agree: the progress display and the registered/required count are read side by
side by the operator, so resolving the expected set twice invites them to
disagree after a layout change.

NUM_CAMS wins when it is a positive integer. Otherwise the ids come from the
tracker config's cameraModelFilepath when it is readable, else from the generated
camInfo filenames.

Standard library only, except for an optional yaml used behind a try: add-streams.sh
runs on the host with the system python3, before any venv exists.

Usage:  STREAM_INFO_PAYLOAD=<json> [NUM_CAMS_VALUE=<n>] camera_counts.py COMPONENT_ROOT
        prints "<registered> <required>"
Exit:   always 0. This reports, it does not judge
"""
import glob, json, os, sys


def parse_int(value, minimum):
    """The int in value, or None when it is absent, unparsable or below minimum."""
    try:
        value = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= minimum else None


def unique(items):
    """items with duplicates dropped, first occurrence winning, order preserved."""
    result, seen = [], set()
    for item in items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result


def configured_camera_ids(root):
    """Camera ids this deployment is configured for, in source_id order."""
    generated = os.path.join(root, "generated")
    tracker = os.path.join(generated, "configs", "ds-mv3dt-tracker-config.yml")
    try:
        import yaml
        with open(tracker, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        models = data.get("ObjectModelProjection", {}).get("cameraModelFilepath", {})
        if isinstance(models, dict) and models:
            return unique(str(camera_id) for camera_id in models)
    except Exception:
        pass

    patterns = (
        os.path.join(generated, "camInfo", "*.yml"),
        os.path.join(generated, "camInfo", "*.yaml"),
    )
    return unique(
        os.path.splitext(os.path.basename(path))[0]
        for pattern in patterns
        for path in sorted(glob.glob(pattern))
    )


def resolve_expected(root, num_cams_value):
    """(required, expected_ids) for root, NUM_CAMS overriding the discovered set."""
    expected_ids = configured_camera_ids(root)
    required = parse_int(num_cams_value, 1) or len(expected_ids)
    return required, expected_ids[:required]


def registered_count(payload):
    """How many streams the API payload reports, 0 when it is unreadable."""
    try:
        info = json.loads(payload or "").get("stream-info", {})
    except (AttributeError, json.JSONDecodeError):
        return 0
    if not isinstance(info, dict):
        return 0
    count = parse_int(info.get("stream-count"), 0)
    if count is not None:
        return count
    streams = info.get("stream-info", [])
    return len(streams) if isinstance(streams, list) else 0


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    required, _ = resolve_expected(root, os.environ.get("NUM_CAMS_VALUE"))
    print(registered_count(os.environ.get("STREAM_INFO_PAYLOAD")), required)
