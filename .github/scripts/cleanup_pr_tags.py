#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Delete a merged/closed PR's ``pr-<N>-*`` candidate images from GHCR.

Candidate tags are build scaffolding: ``pr-<N>-<sha12>`` immutable candidates and
the ``pr-<N>-latest`` alias. Once the PR closes they can never be rebuilt or
referenced, so retaining them only grows the package indefinitely. ``develop-*``
tags are the opposite — they are the derivable coordinate set and are kept
forever.

SAFETY — the rule that matters here
-----------------------------------
A GHCR *version* is a manifest digest, and one digest can carry several tags.
Content-addressed reuse makes that routine: a PR whose tree matches develop's
resolves to the **same digest**, so one version can be tagged
``pr-1234-abc123abc123`` *and* ``develop-def456def456`` *and* ``tree-<sha>``
simultaneously.

Deleting a version deletes every tag on it. So a version is deletable only when
**every** tag on it belongs to the PR being cleaned up. A single foreign tag —
another PR's, any ``develop-*``, any ``tree-*`` content tag — disqualifies the
whole version. The cost of being wrong in that direction is deleting a live
develop image, which is unrecoverable without a rebuild; the cost of being
conservative is a retained tag. Untagged versions are left alone entirely: they
are not ours to reason about here.

DETACH — why the rule above deleted nothing
-------------------------------------------
Every publishing build pushes the content tag ``tree-<tree_sha>`` alongside the
candidate tag, so in practice *almost every* PR digest carries a foreign tag and
the rule above keeps it. Measured on PR #1623: ``0 version(s) deleted, 14 kept``.
The Packages API has no untag operation — only whole-version deletion — so the
tags cannot be separated by deleting.

They can be separated by *retagging*. GHCR tags are mutable (that is how
``develop-latest`` moves), so pointing this PR's tags at a throwaway manifest
leaves the real digest holding only ``tree-``/``develop-`` and puts the PR tags
on a scratch digest whose every tag belongs to the PR — which the unchanged rule
above then deletes.

``--emit-detach-plan`` prints the ``<package> <tag>`` pairs to retag. The
workflow performs the retag (it needs a registry client, this script deliberately
speaks only to the REST API) and then runs the normal delete pass. Detaching is
best-effort: if it fails, the tags stay where they are and nothing is lost.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

API_ROOT = "https://api.github.com"
Requester = Callable[[str, str], object]


def pr_tag_pattern(pr_number: int) -> re.Pattern[str]:
    """Tags owned by this PR: ``pr-<N>-<sha12>`` and ``pr-<N>-latest``."""
    return re.compile(rf"^pr-{pr_number}-(?:[0-9a-f]{{12}}|latest)$")


def is_deletable(tags: list[str], pattern: re.Pattern[str]) -> tuple[bool, str]:
    """Return ``(deletable, reason)`` for one package version's tag list."""
    if not tags:
        return False, "untagged version; not ours to delete"
    owned = [tag for tag in tags if pattern.fullmatch(tag)]
    if not owned:
        return False, "carries no tag for this PR"
    foreign = [tag for tag in tags if not pattern.fullmatch(tag)]
    if foreign:
        return False, f"shared digest also tagged {', '.join(sorted(foreign))}; keeping"
    return True, f"only this PR's tags ({', '.join(sorted(owned))})"


def detachable_tags(tags: list[str], pattern: re.Pattern[str]) -> list[str]:
    """This PR's tags on a version that :func:`is_deletable` refuses to delete.

    Empty when the version has no foreign tag (the delete pass handles it) or no
    tag of this PR's (not ours to touch).
    """
    owned = sorted(tag for tag in tags if pattern.fullmatch(tag))
    if not owned:
        return []
    if len(owned) == len(tags):
        return []  # deletable outright — no need to detach first
    return owned


def ghcr_packages(inventory: dict) -> list[str]:
    """GHCR package names (``vss/<image>``) from the container inventory."""
    images = inventory.get("images") or []
    return sorted(
        f"vss/{item['name']}"
        for item in images
        if item.get("ghcr_build") and item.get("name")
    )


def _request(method: str, url: str) -> object:
    token = os.environ.get("GITHUB_TOKEN", "")
    request = urllib.request.Request(url, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def plan_deletions(
    org: str,
    package: str,
    pr_number: int,
    requester: Requester = _request,
) -> tuple[list[tuple[int, str]], list[tuple[str, str]]]:
    """Return ``(to_delete, skipped)`` for one package.

    ``to_delete`` is ``(version_id, reason)``; ``skipped`` is ``(tags, reason)``.
    """
    encoded = urllib.parse.quote(package, safe="")
    url = f"{API_ROOT}/orgs/{org}/packages/container/{encoded}/versions?per_page=100"
    versions = requester("GET", url)
    if not versions:
        return [], []
    pattern = pr_tag_pattern(pr_number)
    to_delete: list[tuple[int, str]] = []
    skipped: list[tuple[str, str]] = []
    for version in versions:
        tags = (
            ((version.get("metadata") or {}).get("container") or {}).get("tags") or []
        )
        deletable, reason = is_deletable(list(tags), pattern)
        label = ",".join(sorted(tags)) or "<untagged>"
        if deletable:
            to_delete.append((int(version["id"]), f"{label}: {reason}"))
        elif any(pattern.fullmatch(tag) for tag in tags):
            # only report skips that actually involve this PR
            skipped.append((label, reason))
    return to_delete, skipped


def plan_detach(
    org: str,
    package: str,
    pr_number: int,
    requester: Requester = _request,
) -> list[str]:
    """Return this PR's tags that sit on versions the delete pass must keep."""
    encoded = urllib.parse.quote(package, safe="")
    url = f"{API_ROOT}/orgs/{org}/packages/container/{encoded}/versions?per_page=100"
    versions = requester("GET", url)
    if not versions:
        return []
    pattern = pr_tag_pattern(pr_number)
    tags: list[str] = []
    for version in versions:
        version_tags = (
            ((version.get("metadata") or {}).get("container") or {}).get("tags") or []
        )
        tags.extend(detachable_tags(list(version_tags), pattern))
    return sorted(set(tags))


def emit_detach_plan(org: str, packages: list[str], pr_number: int) -> int:
    """Print ``<package> <tag>`` pairs for the workflow to retag. Never fails."""
    pairs = 0
    for package in packages:
        try:
            tags = plan_detach(org, package, pr_number)
        except urllib.error.HTTPError as exc:
            print(f"[pr-tags] {package}: SKIP (HTTP {exc.code})", file=sys.stderr)
            continue
        for tag in tags:
            print(f"{package} {tag}")
            pairs += 1
    print(f"[pr-tags] detach plan: {pairs} tag(s) to move", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("deploy/docker/container-inventory.json"),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--emit-detach-plan",
        action="store_true",
        help=(
            "Print '<package> <tag>' for this PR's tags that share a digest with a "
            "foreign tag, then exit. Deletes nothing."
        ),
    )
    args = parser.parse_args()

    inventory = json.loads(args.inventory.read_text())
    packages = ghcr_packages(inventory)

    if args.emit_detach_plan:
        return emit_detach_plan(args.org, packages, args.pr)

    print(f"[pr-tags] PR #{args.pr}: scanning {len(packages)} GHCR packages")

    deleted = kept = 0
    for package in packages:
        try:
            to_delete, skipped = plan_deletions(args.org, package, args.pr)
        except urllib.error.HTTPError as exc:
            # Never fail the workflow over cleanup: a retained tag is harmless.
            print(f"[pr-tags] {package}: SKIP (HTTP {exc.code})")
            continue
        for tags, reason in skipped:
            print(f"[pr-tags] {package}: keep {tags} — {reason}")
            kept += 1
        for version_id, reason in to_delete:
            print(f"[pr-tags] {package}: delete version {version_id} — {reason}")
            if not args.dry_run:
                encoded = urllib.parse.quote(package, safe="")
                _request(
                    "DELETE",
                    f"{API_ROOT}/orgs/{args.org}/packages/container/"
                    f"{encoded}/versions/{version_id}",
                )
            deleted += 1

    print(f"[pr-tags] done: {deleted} version(s) deleted, {kept} kept as shared")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[pr-tags] ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
