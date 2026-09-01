#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Emit deterministic, non-secret evidence for build-artifact checks."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def _env_value(path: Path, key: str) -> str | None:
    if not path.is_file():
        return None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        if separator and name.strip() == key:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value
    return None


def _run(*args: str, cwd: Path) -> tuple[int, list[str]]:
    try:
        result = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        return 127, [str(exc)]
    output = result.stdout if result.returncode == 0 else result.stderr
    return result.returncode, [line for line in output.splitlines() if line]


def inspect(repo_root: Path, build_dir: Path) -> dict:
    override = build_dir / "override.env"
    profiles = _env_value(override, "COMPOSE_PROFILES")
    resolved = build_dir / "resolved.yml"
    compose_rc, services = (
        _run(
            "docker",
            "compose",
            "-f",
            str(resolved),
            "config",
            "--services",
            cwd=repo_root,
        )
        if resolved.is_file()
        else (None, [])
    )
    status_rc, protected_changes = _run(
        "git",
        "status",
        "--short",
        "--untracked-files=all",
        "--",
        "deploy/docker",
        cwd=repo_root,
    )
    return {
        "schema": 1,
        "build_dir": str(build_dir),
        "artifacts": {
            name: (build_dir / name).is_file()
            for name in ("override.env", "compose.yml", "resolved.yml")
        },
        "foundation": _env_value(override, "FOUNDATION"),
        "compose_profiles": sorted(
            token.strip() for token in (profiles or "").split(",") if token.strip()
        ),
        "resolved_config_valid": compose_rc == 0,
        "resolved_services": sorted(services) if compose_rc == 0 else [],
        "deploy_docker_status_valid": status_rc == 0,
        "deploy_docker_changes": protected_changes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    evidence = inspect(args.repo_root.resolve(), args.build_dir.resolve())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
