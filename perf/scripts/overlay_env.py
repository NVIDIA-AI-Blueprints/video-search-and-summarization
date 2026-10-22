#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Copy warehouse overrides.env and upsert perf-host values."""

from __future__ import annotations

import argparse
from pathlib import Path


def upsert_env(text: str, updates: dict[str, str]) -> str:
    """Replace KEY=value lines; append keys that are not already present."""
    remaining = dict(updates)
    lines: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw:
            lines.append(raw)
            continue
        key, _sep, _rest = raw.partition("=")
        name = key.strip()
        if name in remaining:
            quote = '"' if any(ch.isspace() for ch in remaining[name]) else ""
            lines.append(f"{name}={quote}{remaining.pop(name)}{quote}")
        else:
            lines.append(raw)
    if remaining:
        lines.append("")
        lines.append("# warehouse-bp perf overlays")
        for name, value in remaining.items():
            quote = '"' if any(ch.isspace() for ch in value) else ""
            lines.append(f"{name}={quote}{value}{quote}")
    return "\n".join(lines) + "\n"


def parse_assignments(items: list[str]) -> dict[str, str]:
    updates: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"expected KEY=value, got {item}")
        key, _sep, value = item.partition("=")
        updates[key] = value
    return updates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("assignments", nargs="+")
    args = parser.parse_args()
    text = args.source.read_text()
    args.dest.parent.mkdir(parents=True, exist_ok=True)
    args.dest.write_text(upsert_env(text, parse_assignments(args.assignments)))
    print(args.dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
