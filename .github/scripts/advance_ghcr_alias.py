#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Retag every GHCR image in a release set under one or more aliases.

Two aliases are published post-merge on ``develop``:

* ``develop-latest`` — the moving developer convenience alias.
* ``develop-<sha12>`` — an **immutable per-commit** tag. Publishing this for
  *every* GHCR image at every develop push tip is what lets a consumer derive a
  complete, correct coordinate set from the commit SHA alone, with no
  release-set manifest to fetch. That is the point of this script.

Two behaviours differ from the original build-only alias mover:

1. **Every GHCR entry is retagged, not just ``strategy == "build"``.** A tag set
   that skipped ``mirror`` / ``reuse-pinned`` entries would be incomplete at the
   commit, so a SHA-derived coordinate would 404 for those images. Entries are
   selected on evidence — a ``ghcr.io/`` repository plus a resolvable source
   reference — rather than on strategy, so coverage tracks what is *actually*
   published rather than a hardcoded list. That is 5 images today (vss-agent,
   vss-agent-ui, vss-alert-ms, vss-video-analytics-api, vss-behavior-analytics)
   and grows on its own as ``ghcr_build`` is enabled for the staged build
   entries. Non-GHCR pins (nvcr.io) cannot be retagged and are skipped;
   they are pinned in git at this commit and stay derivable from the repo.

2. **Tree-SHA gate.** ``--verify-tree-sha`` refuses to retag an image whose
   recorded ``source_tree_sha`` does not equal ``git rev-parse <commit>:<source_path>``.
   This makes the retag *absolute*: it asserts "this image was built from this
   commit's source" against git and the registry, never against the previous
   commit's tags. Entries with no ``source_tree_sha`` (``mirror`` entries carry
   an ``upstream_digest`` instead — their content does not come from repo
   source) have nothing repo-side to compare and pass the gate unverified.

The gate fails **closed**: a mismatch raises rather than publishing a tag that
would misrepresent provenance. An incomplete tag set is visible and recoverable;
a wrong one is neither.
"""
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
TREE_RE = re.compile(r"^[0-9a-f]{40}$")
Runner = Callable[[list[str]], str]


@dataclass(frozen=True)
class AliasUpdate:
    name: str
    source: str
    target: str
    digest: str


def _retaggable(image: dict) -> bool:
    """True when the entry carries enough evidence to retag it in GHCR.

    Selection is on evidence, not strategy: a ``ghcr.io/`` repository plus
    *some* resolvable source reference. A digest is preferred, but a tag alone
    is enough — ``imagetools create`` resolves either.

    Entries with a null digest are the normal case on a no-change commit: every
    image comes back ``reuse-pinned`` at ``develop-latest`` with no digest,
    because nothing was built to produce one. Excluding those is what left the
    tag set with a hole at exactly the commits where build avoidance worked.
    """
    repository = str(image.get("image") or "")
    return repository.startswith("ghcr.io/") and bool(image.get("tag"))


def alias_plan(release_set: dict, alias: str) -> list[AliasUpdate]:
    if not ALIAS_RE.fullmatch(alias):
        raise ValueError(f"invalid alias {alias!r}")
    if release_set.get("source", {}).get("ref") != "develop":
        raise ValueError("validated developer alias may advance only from develop")

    updates: list[AliasUpdate] = []
    for image in release_set.get("images", []):
        if not _retaggable(image):
            continue
        repository = str(image.get("image") or "")
        tag = str(image.get("tag") or "")
        digest = str(image.get("digest") or "")
        name = str(image.get("name") or "")
        if not tag:
            raise ValueError(f"{name}: incomplete immutable coordinate")
        # Prefer the digest when the build resolved one; fall back to the tag,
        # which imagetools resolves at create time. On a no-change commit the
        # recorded tag is develop-latest, and since nothing changed at this
        # commit its content IS this commit's content.
        source = f"{repository}:{tag}@{digest}" if DIGEST_RE.fullmatch(digest) else f"{repository}:{tag}"
        updates.append(
            AliasUpdate(
                name=name,
                source=source,
                target=f"{repository}:{alias}",
                digest=digest,
            )
        )
    if not updates:
        raise ValueError("release set has no retaggable GHCR entries")
    return sorted(updates, key=lambda item: item.name)


def git_tree_sha(repo_root: Path, commit: str, source_path: str) -> str | None:
    """``git rev-parse <commit>:<source_path>``, or None when the path is absent."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", f"{commit}:{source_path}"],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value if TREE_RE.fullmatch(value) else None


def verify_tree_shas(
    release_set: dict,
    repo_root: Path,
    commit: str,
    tree_reader: Callable[[Path, str, str], str | None] = git_tree_sha,
) -> list[str]:
    """Return a report line per checked entry; raise on any mismatch.

    Only entries that both declare a ``source_path`` and record a
    ``source_tree_sha`` can be verified. Everything else is reported as
    unverified and allowed through — see the module docstring.
    """
    report: list[str] = []
    mismatches: list[str] = []
    for image in release_set.get("images", []):
        if not _retaggable(image):
            continue
        name = str(image.get("name") or "")
        recorded = image.get("source_tree_sha")
        source_path = image.get("source_path")
        if not recorded or not source_path:
            report.append(f"{name}: no source_tree_sha recorded; retag unverified")
            continue
        expected = tree_reader(repo_root, commit, str(source_path))
        if expected is None:
            mismatches.append(
                f"{name}: {source_path!r} does not resolve to a tree at {commit}"
            )
            continue
        if expected != str(recorded):
            mismatches.append(
                f"{name}: source_tree_sha {recorded} != {commit}:{source_path} "
                f"tree {expected} — image was not built from this commit's source"
            )
            continue
        report.append(f"{name}: source_tree_sha matches {commit}:{source_path}")
    if mismatches:
        raise RuntimeError(
            "tree-SHA gate refused the retag:\n  " + "\n  ".join(mismatches)
        )
    return report


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
    if not update.digest:
        # Tag-sourced retag: the release set recorded no digest to compare
        # against, so report what the alias resolved to instead of asserting.
        print(f"[ghcr-alias] {update.name}: {update.target} -> {digest}")
        return
    if digest != update.digest:
        raise RuntimeError(
            f"{update.name}: alias digest {digest!r} != release-set {update.digest!r}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-set", type=Path, required=True)
    parser.add_argument(
        "--alias",
        action="append",
        default=None,
        help="alias to publish; repeat for several (default: develop-validated)",
    )
    parser.add_argument(
        "--verify-tree-sha",
        action="store_true",
        help="refuse to retag when a recorded source_tree_sha does not match git",
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    aliases = args.alias or ["develop-validated"]
    release_set = json.loads(args.release_set.read_text())

    if args.verify_tree_sha:
        for line in verify_tree_shas(release_set, args.repo_root, args.commit):
            print(f"[ghcr-alias] gate {line}")

    for alias in aliases:
        for update in alias_plan(release_set, alias):
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
