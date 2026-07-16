#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Advance a GHCR moving alias to each built digest in a release set."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ALIAS_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
Runner = Callable[[list[str]], str]


@dataclass(frozen=True)
class AliasUpdate:
    name: str
    source: str
    target: str
    digest: str


def alias_plan(release_set: dict, alias: str) -> list[AliasUpdate]:
    if not ALIAS_RE.fullmatch(alias):
        raise ValueError(f"invalid alias {alias!r}")
    if release_set.get("source", {}).get("ref") != "develop":
        raise ValueError("validated developer alias may advance only from develop")

    updates: list[AliasUpdate] = []
    for image in release_set.get("images", []):
        if image.get("strategy") != "build":
            continue
        repository = str(image.get("image") or "")
        tag = str(image.get("tag") or "")
        digest = str(image.get("digest") or "")
        name = str(image.get("name") or "")
        if not repository.startswith("ghcr.io/"):
            raise ValueError(f"{name}: build entry is not in GHCR")
        if not tag or not DIGEST_RE.fullmatch(digest):
            raise ValueError(f"{name}: incomplete immutable coordinate")
        updates.append(
            AliasUpdate(
                name=name,
                source=f"{repository}:{tag}@{digest}",
                target=f"{repository}:{alias}",
                digest=digest,
            )
        )
    if not updates:
        raise ValueError("release set has no GHCR build entries")
    return sorted(updates, key=lambda item: item.name)


def command_runner(command: list[str]) -> str:
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            f"{command[0]} failed with exit {result.returncode}: {detail[:500]}"
        )
    return result.stdout.strip()


def advance(update: AliasUpdate, runner: Runner = command_runner) -> None:
    runner(
        [
            "docker",
            "buildx",
            "imagetools",
            "create",
            "--tag",
            update.target,
            update.source,
        ]
    )
    observed = runner(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            update.target,
            "--format",
            "{{json .Manifest}}",
        ]
    )
    digest = str(json.loads(observed).get("digest") or "")
    if digest != update.digest:
        raise RuntimeError(
            f"{update.name}: alias digest {digest!r} != release-set {update.digest!r}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-set", type=Path, required=True)
    parser.add_argument("--alias", default="develop-validated")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    release_set = json.loads(args.release_set.read_text())
    for update in alias_plan(release_set, args.alias):
        print(f"[ghcr-alias] {update.source} -> {update.target}")
        if not args.dry_run:
            advance(update)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"[ghcr-alias] ERROR {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
