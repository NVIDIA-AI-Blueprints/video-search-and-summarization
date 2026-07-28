#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Decide which first-party images the GHCR build workflow must build.

Emits a GitHub Actions matrix (JSON on stdout) with one entry per
``ghcr_build: true`` image from deploy/docker/container-inventory.json whose
source folder changed in the pushed range.

Images marked ``shared_tag_set: true`` (agent/UI/alert) share one
``VSS_CONTAINER_TAG``: a change to any member rebuilds the whole set.
Other ``ghcr_build`` images build only when their own ``source_path`` changes.

Diff-range rules (the subtle part — get the PUSH event right):

* ``push`` to ``develop``          → diff ``<event.before>..HEAD``. The naive
  ``origin/develop...HEAD`` is ALWAYS empty on this event because the fetched
  branch head IS the pushed commit.
* ``push`` to ``pull-request/N``   → diff ``merge-base(origin/<base>, HEAD)..HEAD``
  so the matrix reflects the whole PR, not just its last push.
* Initial push (``before`` is the zero SHA), force-push that orphaned
  ``before``, or any range git cannot resolve → **build everything**. Building
  too much is safe; silently building nothing is the failure mode this
  replaces.
* A change to the build workflow itself or the build scripts also builds
  everything (the build contract changed, so every image must re-prove it).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_set import load_inventory  # noqa: E402

ZERO_SHA = "0" * 40

# A change to any of these rebuilds every image: they define how images are
# built and recorded, so a stale image could otherwise carry stale metadata.
BUILD_CONTRACT_PATHS = (
    ".github/workflows/build-dev-images.yml",
    ".github/scripts/detect_changed_images.py",
    ".github/scripts/ghcr_image_guard.py",
    ".github/scripts/release_set.py",
    "deploy/docker/container-inventory.json",
)


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def commit_exists(repo: Path, sha: str) -> bool:
    return run_git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def resolve_diff_base(
    repo: Path, event_name: str, ref_name: str, before: str, base_branch: str
) -> tuple[str | None, str]:
    """Return ``(base_commit, reason)``; ``None`` means build everything."""
    if event_name != "push":
        return None, f"unsupported event {event_name!r}; building everything"

    if ref_name == base_branch:
        if not before or before == ZERO_SHA:
            return None, "initial push (zero before SHA); building everything"
        if not commit_exists(repo, before):
            return (
                None,
                f"push before-SHA {before[:12]} unreachable (force-push?); "
                "building everything",
            )
        return before, f"push range {before[:12]}..HEAD"

    # pull-request/N (or any non-default branch): compare against the base
    # branch merge-base so the matrix covers the whole PR.
    for candidate in (f"origin/{base_branch}", base_branch):
        result = run_git(repo, "merge-base", candidate, "HEAD")
        if result.returncode == 0:
            base = result.stdout.strip()
            return base, f"merge-base with {candidate}: {base[:12]}"
    return None, f"no merge-base with {base_branch}; building everything"


def changed_paths(repo: Path, base: str) -> list[str] | None:
    result = run_git(repo, "diff", "--name-only", base, "HEAD")
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line]


def ghcr_buildable(inventory: dict) -> list[dict]:
    return [
        entry
        for entry in inventory["images"]
        if entry.get("strategy") == "build" and entry.get("ghcr_build")
    ]


def expand_shared_tag_set(
    buildable: list[dict], changed_images: list[dict]
) -> tuple[list[dict], str]:
    """Expand selection for the managed shared-tag set; leave independents alone.

    Images with ``shared_tag_set: true`` (agent/UI/alert) share one
    ``VSS_CONTAINER_TAG``. If any of them changed, publish the whole set.
    Other ``ghcr_build`` images (e.g. video-summarization) build only when
    their own ``source_path`` changed.
    """
    shared = [entry for entry in buildable if entry.get("shared_tag_set")]
    shared_names = {entry["name"] for entry in shared}
    selected_names: set[str] = set()
    reasons: list[str] = []
    for entry in changed_images:
        if entry.get("shared_tag_set"):
            selected_names.update(shared_names)
            reasons.append(entry["name"])
        else:
            selected_names.add(entry["name"])
    selected = [entry for entry in buildable if entry["name"] in selected_names]
    if reasons and selected_names & shared_names:
        shared_reason = (
            f"managed image(s) changed ({', '.join(reasons)}); "
            "building complete shared-tag set"
        )
        independent = sorted(selected_names - shared_names)
        if independent:
            return (
                selected,
                f"{shared_reason}; also building {', '.join(independent)}",
            )
        return selected, shared_reason
    names = ", ".join(entry["name"] for entry in selected)
    return selected, f"changed image(s): {names}"


def select_images(inventory: dict, changed: list[str] | None) -> tuple[list[dict], str]:
    """Matrix entries for the buildable images that need a build."""
    buildable = ghcr_buildable(inventory)
    if changed is None:
        return buildable, "building all GHCR images"
    if any(
        path == contract or path.startswith(contract.rstrip("/") + "/")
        for path in changed
        for contract in BUILD_CONTRACT_PATHS
    ):
        return buildable, "build contract changed; building all GHCR images"
    changed_images = [
        entry
        for entry in buildable
        if any(path.startswith(entry["source_path"] + "/") for path in changed)
    ]
    if not changed_images:
        return [], f"0 of {len(buildable)} images changed"
    return expand_shared_tag_set(buildable, changed_images)


def to_matrix(entries: list[dict]) -> dict:
    return {
        "include": [
            {
                "name": entry["name"],
                "context": entry["context"],
                "dockerfile": entry["dockerfile"],
                "lfs_include": entry.get("lfs_include", ""),
                "platforms": ",".join(entry["platforms"]),
                "source_path": entry["source_path"],
            }
            for entry in entries
        ]
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--ref-name", required=True)
    parser.add_argument("--before", default="")
    parser.add_argument("--base-branch", default="develop")
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()

    inventory = load_inventory(repo_root)
    base, reason = resolve_diff_base(
        repo_root, args.event_name, args.ref_name, args.before, args.base_branch
    )
    changed = changed_paths(repo_root, base) if base else None
    if base and changed is None:
        reason += "; diff failed, building everything"

    entries, selection_reason = select_images(inventory, changed)
    matrix = to_matrix(entries)
    print(
        json.dumps(
            {
                "reason": f"{reason}; {selection_reason}",
                "count": len(entries),
                "matrix": matrix,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
