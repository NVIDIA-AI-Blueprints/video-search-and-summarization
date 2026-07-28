#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Decide which first-party images the GHCR build workflow must build.

Emits a GitHub Actions matrix (JSON on stdout) with one entry per
``ghcr_build: true`` image from deploy/docker/container-inventory.json whose
source folder changed in the pushed range.

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

# Agent, UI, and alert share VSS_CONTAINER_TAG and must move as one set.
# Analytics images have independent tag variables and build only when their
# own service source changes.
SHARED_TAG_IMAGE_NAMES = frozenset({"vss-agent", "vss-agent-ui", "vss-alert-ms"})

# These images compile architecture-sensitive native dependencies and must not
# build arm64 through QEMU on an amd64 runner. The build workflow expands each
# selected image into one job per platform, then combines the native results
# into the same multiarch candidate expected by the release-set flow.
NATIVE_PLATFORM_IMAGE_NAMES = frozenset(
    {"vss-video-analytics-api", "vss-behavior-analytics"}
)
RUNNER_BY_PLATFORM = {
    "linux/amd64": "ubuntu-24.04",
    "linux/arm64": "ubuntu-24.04-arm",
}
RUNNER_ARCH_BY_PLATFORM = {
    "linux/amd64": "X64",
    "linux/arm64": "ARM64",
}
KERNEL_ARCH_BY_PLATFORM = {
    "linux/amd64": "x86_64",
    "linux/arm64": "aarch64",
}


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


def select_images(inventory: dict, changed: list[str] | None) -> tuple[list[dict], str]:
    """Matrix entries for the buildable images that need a build."""
    buildable = [
        entry
        for entry in inventory["images"]
        if entry.get("strategy") == "build" and entry.get("ghcr_build")
    ]
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
    if changed_images:
        selected_names = {entry["name"] for entry in changed_images}
        if selected_names & SHARED_TAG_IMAGE_NAMES:
            selected_names.update(
                entry["name"]
                for entry in buildable
                if entry["name"] in SHARED_TAG_IMAGE_NAMES
            )
        selected = [
            entry for entry in buildable if entry["name"] in selected_names
        ]
        changed_names = ", ".join(entry["name"] for entry in changed_images)
        selected_names_text = ", ".join(entry["name"] for entry in selected)
        return (
            selected,
            f"managed image(s) changed ({changed_names}); building "
            f"{selected_names_text}",
        )
    return [], f"0 of {len(buildable)} images changed"


def matrix_entry(entry: dict) -> dict:
    return {
        "name": entry["name"],
        "context": entry["context"],
        "dockerfile": entry["dockerfile"],
        "lfs_include": entry.get("lfs_include", ""),
        "platforms": ",".join(entry["platforms"]),
        "source_path": entry["source_path"],
    }


def to_matrix(entries: list[dict]) -> dict:
    return {"include": [matrix_entry(entry) for entry in entries]}


def split_build_matrices(entries: list[dict]) -> dict[str, dict]:
    """Partition selected images and expand native builds by platform."""
    standard = [
        entry for entry in entries if entry["name"] not in NATIVE_PLATFORM_IMAGE_NAMES
    ]
    native = [
        entry for entry in entries if entry["name"] in NATIVE_PLATFORM_IMAGE_NAMES
    ]
    native_platforms: list[dict] = []
    for entry in native:
        base = matrix_entry(entry)
        for platform in entry["platforms"]:
            try:
                runner = RUNNER_BY_PLATFORM[platform]
            except KeyError as exc:
                raise ValueError(
                    f"{entry['name']}: no native runner configured for {platform}"
                ) from exc
            native_platforms.append(
                {
                    **base,
                    "platform": platform,
                    "arch": platform.rsplit("/", 1)[-1],
                    "runner": runner,
                    "runner_arch": RUNNER_ARCH_BY_PLATFORM[platform],
                    "kernel_arch": KERNEL_ARCH_BY_PLATFORM[platform],
                }
            )
    return {
        "standard_matrix": to_matrix(standard),
        "native_matrix": to_matrix(native),
        "native_platform_matrix": {"include": native_platforms},
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
    split_matrices = split_build_matrices(entries)
    print(
        json.dumps(
            {
                "reason": f"{reason}; {selection_reason}",
                "count": len(entries),
                "matrix": matrix,
                "standard_count": len(split_matrices["standard_matrix"]["include"]),
                "native_count": len(split_matrices["native_matrix"]["include"]),
                **split_matrices,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
