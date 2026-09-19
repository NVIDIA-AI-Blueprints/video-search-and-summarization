# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#!/usr/bin/env python3
"""
Guard the shape of the shipped VIOS notification configs.

A profile config carrying more than one stream-driven capability is a projection
superset: one item per capability per event, every item carrying an id, so a
build resolves its fan-out by setting `enabled` alone. This checks the item
inventory and the stock enable vector -- what a build that inherits the config
actually gets -- against the expectations in
skills/vss-build-vision-ai/references/services/vios.md.

Usage: check_notification_supersets.py <deploy/docker>
"""
import json
import sys
from pathlib import Path

# path relative to deploy/docker -> {item id: enabled as shipped}
EXPECTED = {
    "developer-profiles/dev-profile-search/vios/configs/notification_config.json": {
        "rtvi-cv-camera-streaming": True,
        "rtvi-cv-camera-remove": True,
        "rtvi-embed-camera-streaming": True,
        "rtvi-embed-camera-remove": True,
        "rtvi-vlm-tagging-camera-streaming": True,
        "rtvi-vlm-tagging-camera-remove": True,
        "alert-bridge-camera-streaming": False,
        "alert-bridge-camera-remove": False,
        "es-raw-camera-remove": True,
        "es-behavior-camera-remove": True,
        "es-embed-filtered-camera-remove": True,
        "dummy-camera-add": False,
    },
}

# The MODE-selected files a stock alerts deploy mounts carry one capability
# each, so there is no vector to resolve: check only their ids.
MODE_FILES = {
    "developer-profiles/dev-profile-alerts/vios/configs/notification_config_2d_cv.json": {
        "rtvi-cv-camera-streaming",
        "rtvi-cv-camera-remove",
    },
    "developer-profiles/dev-profile-alerts/vios/configs/notification_config_2d_vlm.json": {
        "alert-bridge-camera-streaming",
        "alert-bridge-camera-remove",
    },
}


def items(path: Path) -> list[dict]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    webhooks = doc.get("webhooks", {})
    if webhooks.get("enabled") is not True:
        raise ValueError(f"{path}: webhooks.enabled must be true")
    return webhooks.get("items", [])


def check(root: Path) -> list[str]:
    errors = []
    for relative, expected in EXPECTED.items():
        path = root / relative
        found = {}
        for item in items(path):
            item_id = item.get("id", "")
            if not item_id:
                errors.append(f"{relative}: an item has no id; every item must name its capability and event")
                continue
            if item_id in found:
                errors.append(f"{relative}: duplicate item id {item_id}")
            found[item_id] = item.get("enabled")
            if item_id != "dummy-camera-add" and not item.get("request"):
                errors.append(f"{relative}: item {item_id} declares no receiver")
        if found != expected:
            errors.append(f"{relative}: enable vector is {found}, expected {expected}")
    for relative, expected_ids in MODE_FILES.items():
        found_ids = {item.get("id", "") for item in items(root / relative)}
        if found_ids != expected_ids:
            errors.append(f"{relative}: item ids are {sorted(found_ids)}, expected {sorted(expected_ids)}")
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} <deploy/docker>", file=sys.stderr)
        return 2
    try:
        errors = check(Path(sys.argv[1]))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
