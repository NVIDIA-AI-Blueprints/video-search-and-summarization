#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Read API request counts and warehouse query constants from config.yaml."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Mapping

DEFAULTS = {
    "duration_seconds": 60,
    "warmup_seconds": 2,
    "request_count": 20,
    "warmup_requests": 2,
    "sensor_id": "Camera",
    "from_ts": "2026-02-14T10:16:00.000Z",
    "to_ts": "2026-02-14T10:17:00.000Z",
    "place": "building=Warehouse/room=Room-1",
    "lookup_x": "0",
    "lookup_y": "0",
    "endpoints": ["spec_gets"],
}


def scenario_settings(config_path: Path, scenario: str) -> dict[str, Any]:
    settings = dict(DEFAULTS)
    if not config_path.is_file():
        return settings
    try:
        import yaml  # type: ignore
    except ImportError:
        return settings
    data = yaml.safe_load(config_path.read_text()) or {}
    raw = data.get("test_scenarios") or data.get("scenarios") or {}
    candidate = raw.get(scenario) if isinstance(raw, Mapping) else None
    if not isinstance(candidate, Mapping):
        return settings
    for key in (
        "duration_seconds",
        "warmup_seconds",
        "request_count",
        "warmup_requests",
        "sensor_id",
        "place",
        "from_ts",
        "to_ts",
        "lookup_x",
        "lookup_y",
    ):
        if candidate.get(key) is not None:
            settings[key] = candidate[key]
    if candidate.get("endpoints"):
        settings["endpoints"] = [str(item) for item in candidate["endpoints"]]
    return settings


def bash_exports(config_path: Path, scenario: str) -> str:
    settings = scenario_settings(config_path, scenario)
    endpoints = " ".join(settings["endpoints"])
    return "\n".join(
        [
            f"CONFIG_DURATION_SECONDS={int(settings['duration_seconds'])}",
            f"CONFIG_WARMUP_SECONDS={int(settings['warmup_seconds'])}",
            f"CONFIG_REQUEST_COUNT={int(settings['request_count'])}",
            f"CONFIG_WARMUP_REQUESTS={int(settings['warmup_requests'])}",
            f"CONFIG_SENSOR_ID='{settings['sensor_id']}'",
            f"CONFIG_PLACE='{settings['place']}'",
            f"CONFIG_FROM_TS='{settings['from_ts']}'",
            f"CONFIG_TO_TS='{settings['to_ts']}'",
            f"CONFIG_LOOKUP_X='{settings['lookup_x']}'",
            f"CONFIG_LOOKUP_Y='{settings['lookup_y']}'",
            f"CONFIG_ENDPOINTS='{endpoints}'",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--scenario", default="quick_test")
    args = parser.parse_args()
    print(bash_exports(args.config, args.scenario))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
