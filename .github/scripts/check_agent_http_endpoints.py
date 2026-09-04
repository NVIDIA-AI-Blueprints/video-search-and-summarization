#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reject Docker-only HTTP endpoints from agent-facing deployment defaults."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_SUFFIX = ".env"
YAML_SUFFIXES = {".yaml", ".yml"}
DOCKER_ONLY_HTTP_HOSTS = {
    "alert-bridge",
    "elasticsearch",
    "lvs-server",
    "vss-va-mcp",
    "vst-ingress",
}
HTTP_URL = re.compile(
    r"https?://(?P<host>"
    + "|".join(sorted(DOCKER_ONLY_HTTP_HOSTS))
    + r")(?=[:/\"'\s]|$)"
)


def is_env_file(path: Path) -> bool:
    """Match env files including a bare ``.env``, whose ``Path.suffix`` is empty."""
    return path.suffix == ENV_SUFFIX or path.name == ENV_SUFFIX


def default_paths() -> list[Path]:
    """Return the agent-facing files governed by the gateway contract."""
    roots = (
        ROOT / "deploy/docker/services/agent",
        ROOT / "deploy/docker/developer-profiles",
        ROOT / "deploy/docker/industry-profiles",
    )
    paths: list[Path] = []
    for root in roots:
        for path in root.rglob("*"):
            if not (is_env_file(path) or path.suffix in YAML_SUFFIXES):
                continue
            relative_parts = path.relative_to(root).parts
            is_profile_env = is_env_file(path) and len(relative_parts) == 2
            if root.name == "agent" or "vss-agent" in path.parts or is_profile_env:
                paths.append(path)
    return sorted(paths)


def scan_paths(paths: Iterable[Path]) -> list[str]:
    """Return actionable diagnostics for forbidden endpoint defaults."""
    failures: list[str] = []
    for path in paths:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            match = HTTP_URL.search(line)
            if match:
                try:
                    display_path = path.relative_to(ROOT)
                except ValueError:
                    display_path = path
                failures.append(
                    f"{display_path}:{line_number}: Docker-only HTTP host "
                    f"{match.group('host')!r}; use a gateway-derived environment variable"
                )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)

    paths = args.paths or default_paths()
    failures = scan_paths(paths)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1

    print(f"Agent HTTP endpoint lint passed ({len(paths)} files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
