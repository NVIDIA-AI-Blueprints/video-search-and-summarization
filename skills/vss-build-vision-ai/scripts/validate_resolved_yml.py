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
NGC_TRIGGER = re.compile(r"nvcr\.io/|(?<![\w.-])ngc:")
NGC_SECRET_KEYS = ("NGC_API_KEY", "NGC_CLI_API_KEY")


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


def iter_env(service: dict[str, Any]) -> Iterator[tuple[str, Any]]:
    env = service.get("environment")
    if isinstance(env, dict):
        yield from env.items()
    elif isinstance(env, list):
        for item in env:
            if isinstance(item, str):
                key, separator, value = item.partition("=")
                yield key, (value if separator else None)


def secret_errors(document: dict[str, Any], extra_required: set[str]) -> list[str]:
    errors: list[str] = []
    services = document.get("services") or {}

    ngc_required = any(NGC_TRIGGER.search(value) for _, value in walk_strings(document))

    monitored = set(extra_required)
    if ngc_required:
        monitored.update(NGC_SECRET_KEYS)
    if not monitored:
        return errors

    seen = {key: False for key in monitored}
    for service_name, service in services.items():
        if not isinstance(service, dict):
            continue
        for key, value in iter_env(service):
            if key not in monitored:
                continue
            seen[key] = True
            if value is None or (isinstance(value, str) and not value.strip()):
                errors.append(
                    f"service {service_name!r} environment {key!r} resolved empty; a "
                    "mode-required credential baked as '' cannot be supplied at deploy "
                    "time — set it and regenerate resolved.yml"
                )

    if ngc_required and not (seen["NGC_API_KEY"] or seen["NGC_CLI_API_KEY"]):
        errors.append(
            "resolved model references NGC-gated artifacts (nvcr.io/ or ngc:) but no "
            "NGC_API_KEY/NGC_CLI_API_KEY is wired into any service environment"
        )
    for key in extra_required:
        if not seen[key]:
            errors.append(
                f"required credential {key!r} is absent from every service environment"
            )

    return errors


def validate_document(
    document: dict[str, Any],
    repo_root: Path,
    extra_required: set[str] | None = None,
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
        checked_in_source = read_only or bool(
            {"developer-profiles", "industry-profiles"} & set(source.parts)
        )
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

    errors.extend(secret_errors(document, extra_required or set()))

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("resolved_yml", type=Path)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument(
        "--required-secret",
        action="append",
        default=[],
        metavar="KEY",
        help="env key that must resolve to a non-empty literal (repeatable)",
    )
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

    errors = validate_document(
        document, args.repo_root, set(args.required_secret)
    )
    if errors:
        print(
            f"ERROR: {args.resolved_yml} failed pre-deployment validation:",
            file=sys.stderr,
        )
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        raise SystemExit(2)

    print(
        f"Validated {args.resolved_yml}: no stale placeholders, invalid "
        "checked-in bind sources, or empty mode-required credentials"
    )


if __name__ == "__main__":
    main()
