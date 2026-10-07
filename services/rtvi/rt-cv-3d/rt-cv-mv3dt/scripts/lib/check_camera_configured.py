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
"""Check that a camera id is present in every staged config that must name it.

Used by scripts/add-streams.sh. Standard library only: add-streams.sh runs on the
host with the system python3, before any venv exists.

Registering a camera the deployment was not staged for gives STREAM_ADD_SUCCESS
and then no frames, because the tracker has no model for it and the broker has no
topic (bug 6557851). The three staged files that must agree are generated/camInfo,
the tracker config's cameraModelFilepath, and pub_sub_info_config.

A deployment with no generated/ directory is not judged: some ad hoc setups stage
elsewhere, and refusing them would be worse than not checking.

Usage:  check_camera_configured.py COMPONENT_ROOT CAMERA_ID
Exit:   0 configured, or no staged config to judge against
        2 staged config does not name it, and the reason is printed to stderr

The caller turns a non-zero exit into `exit 2` of add-streams.sh, so 2 is used
here rather than 1 to keep the two the same number when read in a log.
"""
import os
import sys

root, camera_id = sys.argv[1], sys.argv[2]
generated_dir = os.path.join(root, "generated")
cam_info_dir = os.path.join(generated_dir, "camInfo")
tracker_config = os.path.join(generated_dir, "configs", "ds-mv3dt-tracker-config.yml")
pub_sub_config = os.path.join(generated_dir, "configs", "pub_sub_info_config.yml")

# Some ad hoc deployments do not stage generated configs beside this helper.
# In that case there is no local source of truth to check.
if not any(os.path.exists(path) for path in (cam_info_dir, tracker_config, pub_sub_config)):
    sys.exit(0)

missing = []
if not any(
    os.path.isfile(os.path.join(cam_info_dir, f"{camera_id}.{ext}"))
    for ext in ("yml", "yaml")
):
    missing.append(f"generated/camInfo/{camera_id}.yml")

try:
    import yaml
except ImportError:
    if missing:
        print(
            f"ERROR: camera_id {camera_id} is not configured in camInfo/tracker/pub-sub config",
            file=sys.stderr,
        )
        for item in missing:
            print(f"  missing: {item}", file=sys.stderr)
        sys.exit(2)
    # camInfo exists, but without pyyaml the tracker and pub/sub membership checks
    # cannot run. Say so rather than reporting a pass the check did not make: an
    # id present in camInfo but absent from pub_sub_info_config.yml still crashes
    # the tracker, which is the failure this validation exists to prevent.
    print(
        f"   ⚠ pyyaml unavailable: checked only generated/camInfo/{camera_id}.yml,",
        file=sys.stderr,
    )
    print(
        "     not the tracker cameraModelFilepath or pub/sub topic entries.",
        file=sys.stderr,
    )
    sys.exit(0)


def load_yaml(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        rel = os.path.relpath(path, root)
        print(f"ERROR: cannot parse {rel}: {exc}", file=sys.stderr)
        sys.exit(2)


tracker = load_yaml(tracker_config)
if tracker is not None:
    object_model = (
        tracker.get("ObjectModelProjection", {}) if isinstance(tracker, dict) else {}
    )
    camera_models = (
        object_model.get("cameraModelFilepath", {})
        if isinstance(object_model, dict)
        else {}
    )
    if not isinstance(camera_models, dict) or camera_id not in camera_models:
        missing.append(
            "generated/configs/ds-mv3dt-tracker-config.yml "
            "ObjectModelProjection.cameraModelFilepath"
        )

pub_sub = load_yaml(pub_sub_config)
if pub_sub is not None:
    if not isinstance(pub_sub, dict):
        pub_sub = {}
    pub_topics = pub_sub.get("pubBrokerTopicStr", {})
    sub_topics = pub_sub.get("subPeerBrokerTopicStrs", {})
    if not isinstance(pub_topics, dict) or camera_id not in pub_topics:
        missing.append("generated/configs/pub_sub_info_config.yml pubBrokerTopicStr")
    if not isinstance(sub_topics, dict) or camera_id not in sub_topics:
        missing.append("generated/configs/pub_sub_info_config.yml subPeerBrokerTopicStrs")

if missing:
    print(
        f"ERROR: camera_id {camera_id} is not configured in camInfo/tracker/pub-sub config",
        file=sys.stderr,
    )
    for item in missing:
        print(f"  missing: {item}", file=sys.stderr)
    sys.exit(2)
