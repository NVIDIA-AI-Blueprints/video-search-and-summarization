#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Read playback duration, warmup, topics, and percentiles from config.yaml."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Mapping


DEFAULTS = {
    "duration_seconds": 60,
    "warmup_seconds": 10,
    "topics": ["raw", "frames", "behavior", "events", "incidents"],
    "percentiles": [50, 95, 99],
}


def scenario_settings(config_path: Path, scenario: str) -> dict[str, Any]:
    """Return run settings for one scenario, falling back to DEFAULTS."""
    settings = dict(DEFAULTS)
    if not config_path.is_file():
        return settings
    text = config_path.read_text()
    block: Mapping[str, Any] = {}
    try:
        import yaml  # type: ignore
    except ImportError:
        block = _parse_scenario_block(text, scenario)
    else:
        data = yaml.safe_load(text) or {}
        raw = data.get("test_scenarios") or data.get("scenarios") or {}
        candidate = raw.get(scenario) if isinstance(raw, Mapping) else None
        if isinstance(candidate, Mapping):
            block = candidate
        elif not isinstance(candidate, Mapping):
            block = _parse_scenario_block(text, scenario)
    if block.get("duration_seconds") is not None:
        settings["duration_seconds"] = int(block["duration_seconds"])
    if block.get("warmup_seconds") is not None:
        settings["warmup_seconds"] = int(block["warmup_seconds"])
    if block.get("topics"):
        settings["topics"] = [str(item) for item in block["topics"]]
    if block.get("percentiles"):
        settings["percentiles"] = [int(item) for item in block["percentiles"]]
    return settings


def bash_exports(config_path: Path, scenario: str) -> str:
    """Print CONFIG_* assignments for eval from common.sh."""
    settings = scenario_settings(config_path, scenario)
    topics = " ".join(settings["topics"])
    percentiles = " ".join(str(item) for item in settings["percentiles"])
    return "\n".join(
        [
            f"CONFIG_DURATION_SECONDS={settings['duration_seconds']}",
            f"CONFIG_WARMUP_SECONDS={settings['warmup_seconds']}",
            f"CONFIG_TOPICS='{topics}'",
            f"CONFIG_PERCENTILES='{percentiles}'",
        ]
    )


def _parse_scenario_block(text: str, scenario: str) -> dict[str, Any]:
    """Read a mapping under ``scenario:`` when PyYAML is not installed."""
    lines = text.splitlines()
    start = None
    indent = None
    for index, line in enumerate(lines):
        stripped = line.split("#", 1)[0].rstrip()
        if not stripped.strip():
            continue
        if stripped.strip() == f"{scenario}:":
            start = index + 1
            indent = len(stripped) - len(stripped.lstrip(" "))
            break
    if start is None or indent is None:
        return {}
    child_indent = None
    block_lines: list[str] = []
    for line in lines[start:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        raw = line.split("#", 1)[0].rstrip()
        leading = len(raw) - len(raw.lstrip(" "))
        if child_indent is None:
            if leading <= indent:
                break
            child_indent = leading
        if leading < child_indent:
            break
        block_lines.append(raw)
    mapping: dict[str, Any] = {}
    current_list: str | None = None
    for raw in block_lines:
        content = raw.strip()
        leading = len(raw) - len(raw.lstrip(" "))
        if content.startswith("- ") and current_list:
            mapping.setdefault(current_list, []).append(_scalar(content[2:].strip()))
            continue
        if leading > child_indent and current_list:
            continue
        current_list = None
        key, sep, rest = content.partition(":")
        if not sep:
            continue
        key = key.strip()
        rest = rest.strip()
        if rest:
            mapping[key] = _scalar(rest)
            current_list = None
        else:
            mapping[key] = []
            current_list = key
    return mapping


def _scalar(text: str) -> Any:
    if text[:1] in "\"'" and text.endswith(text[:1]) and len(text) >= 2:
        return text[1:-1]
    try:
        return int(text)
    except ValueError:
        return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--scenario", default="quick_test")
    args = parser.parse_args()
    print(bash_exports(args.config, args.scenario))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
