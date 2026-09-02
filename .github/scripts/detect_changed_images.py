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
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

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

# Agent, gateway, UI, and alert share VSS_CONTAINER_TAG and must move as one set.
# Analytics/configurator images have independent tag variables and build only when
# their own service source changes.
SHARED_TAG_IMAGE_NAMES = frozenset(
    {"vss-agent", "vss-agent-gateway", "vss-agent-ui", "vss-alert-ms"}
)

# Behavior analytics native-runner routing predates the SDR/configurator GHCR
# onboarding. New native-build images should declare native_platform_build in
# deploy/docker/container-inventory.json.
NATIVE_PLATFORM_IMAGE_NAMES = frozenset({"vss-behavior-analytics"})
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


def is_native_platform_build(entry: dict) -> bool:
    return (
        entry["name"] in NATIVE_PLATFORM_IMAGE_NAMES
        or entry.get("native_platform_build") is True
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


def paths_changed_under(changed: list[str] | None, directory: str) -> bool:
    """Whether the diff contains ``directory`` or one of its descendants.

    ``None`` represents an unavailable diff and deliberately fails open so a
    history or range-resolution problem runs CI instead of silently skipping
    it. The directory boundary prevents similarly named siblings from
    matching (for example, ``spatialai-data-utils-old``).
    """
    directory = directory.rstrip("/")
    if not directory:
        raise ValueError("directory must not be empty")
    if changed is None:
        return True
    return any(
        path == directory or path.startswith(directory + "/")
        for path in changed
    )


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
        if paths_changed_under(changed, entry["source_path"])
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


def add_missing_content_tags(
    buildable: list[dict],
    selected: list[dict],
    repo: Path,
    commit: str,
    probe: Callable[[str], bool | None],
    owner: str,
) -> tuple[list[dict], list[str]]:
    """Add images whose content tag is absent, whatever the path diff said.

    This is what makes "every image at the tip has a tree-<sha>" true by
    construction rather than by assumption about build history: a missing tag
    pulls the image into the matrix, the build republishes it, and the
    post-merge retag finds it.
    """
    have = {entry["name"] for entry in selected}
    added = [
        entry
        for entry in buildable
        if entry["name"] not in have
        and content_tag_missing(entry, repo, commit, probe, owner)
    ]
    if not added:
        return selected, []
    names = [entry["name"] for entry in added]
    return selected + added, names


def ghcr_tag_exists(reference: str) -> bool | None:
    """True/False if the manifest read succeeded, None if it could not be read."""
    result = subprocess.run(
        ["docker", "manifest", "inspect", reference],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True
    combined = (result.stderr + result.stdout).lower()
    if "manifest unknown" in combined or "not found" in combined:
        return False
    return None  # auth, network, rate limit: unknown, so build


def content_tag_missing(
    entry: dict,
    repo: Path,
    commit: str,
    probe: Callable[[str], bool | None],
    owner: str,
) -> bool:
    """True when this image has no published ``tree-<sha>`` for its current tree.

    A path diff is a *proxy* for "did the content change"; the tree hash IS the
    content. An image whose tree is unchanged is normally skipped -- but only
    safely so if the content tag for that tree actually exists, because the
    post-merge retag sources the candidate set from it.

    Fails **open**: a probe that errors returns None and the image is built.
    An unreachable registry must never look like "already published" -- a
    spurious rebuild costs minutes, a spurious skip costs a missing tag that
    surfaces somewhere else hours later.
    """
    source_path = entry.get("source_path")
    if not source_path:
        return False
    result = run_git(repo, "rev-parse", f"{commit}:{source_path}")
    if result.returncode != 0:
        return True
    tree_sha = result.stdout.strip()
    repository = entry.get("repository", entry["name"])
    tag_suffix = entry.get("tag_suffix", "")
    reference = (
        f"ghcr.io/{owner.lower()}/vss/{repository}:"
        f"tree-{tree_sha}{tag_suffix}"
    )
    return probe(reference) is not True


def _format_build_args(build_args: dict | None) -> str:
    """Render inventory build_args as newline KEY=VALUE for build-push-action.

    Empty string when an image declares no build_args, which build-push-action
    treats as "no args" -- so existing images are unaffected.
    """
    return "\n".join(f"{key}={value}" for key, value in (build_args or {}).items())


def matrix_entry(entry: dict) -> dict:
    matrix = {
        "name": entry["name"],
        "repository": entry.get("repository", entry["name"]),
        "tag_suffix": entry.get("tag_suffix", ""),
        "context": entry["context"],
        "dockerfile": entry["dockerfile"],
        "lfs_include": entry.get("lfs_include", ""),
        "platforms": ",".join(entry["platforms"]),
        "source_path": entry["source_path"],
        "build_args": _format_build_args(entry.get("build_args")),
    }
    return matrix


def to_matrix(entries: list[dict]) -> dict:
    return {"include": [matrix_entry(entry) for entry in entries]}


def split_build_matrices(entries: list[dict]) -> dict[str, dict]:
    """Partition selected images and expand native builds by platform."""
    standard = [entry for entry in entries if not is_native_platform_build(entry)]
    native = [entry for entry in entries if is_native_platform_build(entry)]
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
    parser.add_argument(
        "--owner",
        default=os.environ.get("GITHUB_REPOSITORY_OWNER", ""),
        help="GHCR owner; enables the content-tag gap check when set.",
    )
    parser.add_argument("--commit", default=os.environ.get("GITHUB_SHA", "HEAD"))
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

    # A path diff only says the source did not change. It cannot say the content
    # tag for that source was ever published -- and the post-merge retag sources
    # the candidate set from tree-<sha>. Pull in any image missing one, so
    # "every image at the tip has a content tag" holds by construction.
    if args.owner:
        buildable = [
            entry
            for entry in inventory["images"]
            if entry.get("strategy") == "build" and entry.get("ghcr_build")
        ]
        entries, backfilled = add_missing_content_tags(
            buildable, entries, repo_root, args.commit, ghcr_tag_exists, args.owner
        )
        if backfilled:
            selection_reason += (
                f"; no published content tag for {', '.join(backfilled)}"
            )
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
