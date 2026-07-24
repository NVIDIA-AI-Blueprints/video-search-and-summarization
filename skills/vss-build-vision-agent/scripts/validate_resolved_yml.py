#!/usr/bin/env -S uv run --quiet --script
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# /// script
# requires-python = ">=3.9"
# dependencies = ["pyyaml"]
# ///
"""Reject resolved Compose files that are unsafe to deploy."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Iterator

import yaml


SENTINELS = (
    "/path/to/deploy/docker",
    "<HOST_IP>",
)
UNRESOLVED_INTERPOLATION = re.compile(r"(?<!\$)\$\{[^}]+\}")
FILE_TARGET_SUFFIXES = {
    ".conf",
    ".cfg",
    ".env",
    ".ini",
    ".json",
    ".py",
    ".sh",
    ".toml",
    ".xml",
    ".yaml",
    ".yml",
}
GENERATED_BIND_NAMES = {".wdm-env"}


def walk_strings(value: Any, location: str = "$") -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield location, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from walk_strings(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_strings(child, f"{location}[{index}]")


def bind_mounts(
    document: dict[str, Any],
) -> Iterator[tuple[str, str, str, bool]]:
    for service_name, service in (document.get("services") or {}).items():
        if not isinstance(service, dict):
            continue
        for volume in service.get("volumes") or []:
            if isinstance(volume, dict) and volume.get("type") == "bind":
                source = volume.get("source")
                target = volume.get("target")
                if isinstance(source, str) and isinstance(target, str):
                    yield service_name, source, target, volume.get("read_only") is True


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
    except ValueError:
        return False
    return True


def validate_document(
    document: dict[str, Any],
    repo_root: Path,
) -> list[str]:
    errors: list[str] = []
    services = document.get("services")

    if not isinstance(services, dict) or not services:
        errors.append("resolved Compose model has no services")

    for location, value in walk_strings(document):
        for sentinel in SENTINELS:
            if sentinel in value:
                errors.append(f"{location} contains placeholder {sentinel!r}")
        if UNRESOLVED_INTERPOLATION.search(value):
            errors.append(f"{location} contains unresolved Compose interpolation")

    for service_name, source_text, target_text, read_only in bind_mounts(document):
        source = Path(source_text)
        if not source.is_absolute() or not is_within(source, repo_root):
            continue
        checked_in_source = read_only or "developer-profiles" in source.parts
        if not checked_in_source:
            continue
        if not source.exists():
            template_source = source.with_name(f"{source.name}.tmpl")
            if source.name in GENERATED_BIND_NAMES or template_source.is_file():
                continue
            errors.append(
                f"service {service_name!r} bind source does not exist: {source}"
            )
            continue
        if source.is_dir() and Path(target_text).suffix in FILE_TARGET_SUFFIXES:
            errors.append(
                f"service {service_name!r} mounts directory {source} "
                f"onto file target {target_text}"
            )

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("resolved_yml", type=Path)
    parser.add_argument("--repo-root", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        with args.resolved_yml.open() as resolved_file:
            document = yaml.safe_load(resolved_file) or {}
    except FileNotFoundError:
        print(f"ERROR: {args.resolved_yml} not found", file=sys.stderr)
        raise SystemExit(1)
    except yaml.YAMLError as error:
        print(
            f"ERROR: failed to parse {args.resolved_yml}: {error}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    errors = validate_document(document, args.repo_root)
    if errors:
        print(
            f"ERROR: {args.resolved_yml} failed pre-deployment validation:",
            file=sys.stderr,
        )
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        raise SystemExit(2)

    print(
        f"Validated {args.resolved_yml}: no stale placeholders or invalid "
        "checked-in bind sources"
    )


if __name__ == "__main__":
    main()
