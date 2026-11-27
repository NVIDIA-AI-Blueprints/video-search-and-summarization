#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Patch Behavior Analytics app keys in a compose config without editing git."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping


def set_app_values(config: dict, values: Mapping[str, str]) -> dict[str, str]:
    """Set or add ``app[].name/value`` entries. Returns the applied map."""
    app = list(config.get("app") or [])
    index = {item.get("name"): item for item in app if isinstance(item, dict)}
    applied: dict[str, str] = {}
    for name, value in values.items():
        if name in index:
            index[name]["value"] = value
        else:
            item = {"name": name, "value": value}
            app.append(item)
            index[name] = item
        applied[name] = value
    config["app"] = app
    return applied


def overlay_config(path: Path, values: Mapping[str, str], stamp: Path | None = None) -> dict[str, str]:
    config = json.loads(path.read_text())
    applied = set_app_values(config, values)
    path.write_text(json.dumps(config, indent="\t") + "\n")
    payload = {"source": str(path), "app": applied}
    if stamp is not None:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(json.dumps(payload, indent=2) + "\n")
    return applied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--behavior-emit-once", default="false")
    parser.add_argument("--stamp", type=Path)
    args = parser.parse_args()
    applied = overlay_config(
        args.config,
        {"behaviorEmitOnce": args.behavior_emit_once},
        stamp=args.stamp,
    )
    print(json.dumps({"overlay": applied, "config": str(args.config)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
