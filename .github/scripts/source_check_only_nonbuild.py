#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Decide whether a PR touches only non-build files under a service folder.

The ``Check {Agent,UI} Container Source`` gates compare the git tree SHA of
``services/agent`` / ``services/ui`` against the ``com.nvidia.vss.source_tree_sha``
baked into the deployed image. That tree SHA covers the *whole* folder, so a
docs- or tests-only change (which never enters the image — the Dockerfiles use
selective ``COPY`` / a ``.dockerignore``) still trips the gate and forces a
needless container re-spin or manifest re-stamp.

This helper is the stop-gap: it prints ``true`` when **every** file the PR
changed under the service folder is one the image build provably does not
consume, so the caller can skip the SHA comparison for that run. It prints
``false`` (run the real check) whenever it is unsure — unknown base, an
integration-branch push, or any build-relevant file in the diff.

The durable fix is to make the source_tree_sha cover only build inputs (honor
the ``.dockerignore`` / COPY set) on both the build-stamp and check sides; once
that lands this helper can be removed.

Output: a single ``true``/``false`` token on stdout. Diagnostics go to stderr.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys
from pathlib import Path


# Image name -> source folder (must match check_container_tag_source.py).
SOURCE_PATHS = {
    "vss-agent": "services/agent",
    "vss-agent-ui": "services/ui",
}

# Paths (relative to the service folder) the image build does NOT consume.
#
# vss-agent: services/agent/docker/Dockerfile uses selective COPY of
#   pyproject.toml, uv.lock, src/, 3rdparty/, docker/*.py and the license
#   files — so the service-root docs, tests/, stubs/ and CI-metadata files
#   below never enter the build context. Note the patterns are anchored to the
#   service root: src/vss_agents/**/README.md IS copied and must NOT be ignored.
#
# vss-agent-ui: services/ui/.dockerignore excludes **/*.md and the
#   *.test.{js,ts,tsx} / *.spec.{js,ts,tsx} files from the build context, so
#   markdown and those test/spec files never ship.
NONBUILD_PATTERNS = {
    "vss-agent": [
        "AGENTS.md",
        "README.md",
        "LICENSE.md",
        "tests/**",
        "stubs/**",
        ".gitattributes",
        ".gitleaks.toml",
        ".secrets.baseline",
        "gitleaks-baseline.json",
    ],
    "vss-agent-ui": [
        # Strict subset of services/ui/.dockerignore's tracked-file exclusions,
        # so this never skips a file that could ship. (.dockerignore only drops
        # the .js/.ts/.tsx test/spec variants — not e.g. *.test.py.)
        "**/*.md",
        "**/*.test.js",
        "**/*.test.ts",
        "**/*.test.tsx",
        "**/*.spec.js",
        "**/*.spec.ts",
        "**/*.spec.tsx",
    ],
}


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def matches(rel: str, pattern: str) -> bool:
    base = rel.rsplit("/", 1)[-1]
    if pattern.endswith("/**") or pattern.endswith("/*"):
        directory = pattern.rsplit("/", 1)[0].rstrip("/")
        return rel == directory or rel.startswith(directory + "/")
    if pattern.startswith("**/"):
        return fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(base, pattern[3:])
    if "/" not in pattern and ("*" in pattern or "?" in pattern):
        return fnmatch.fnmatch(base, pattern)
    return rel == pattern


def is_nonbuild(rel: str, patterns: list[str]) -> bool:
    return any(matches(rel, p) for p in patterns)


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-name", choices=sorted(SOURCE_PATHS), required=True)
    parser.add_argument("--base-ref", default="origin/develop")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    repo = args.repo_root.resolve()
    source_path = SOURCE_PATHS[args.image_name]
    patterns = NONBUILD_PATTERNS[args.image_name]

    # Always run the full check on integration-branch pushes — there is no PR
    # diff to scope against and we want the develop/main health signal.
    ref_name = os.environ.get("GITHUB_REF_NAME", "")
    if ref_name in ("develop", "main"):
        log(f"on integration branch {ref_name!r}; running full source check.")
        print("false")
        return 0

    # Make sure the base ref is present, then find the merge-base.
    remote_base = args.base_ref.split("/", 1)[-1] if "/" in args.base_ref else args.base_ref
    git(repo, "fetch", "--no-tags", "--quiet", "origin", remote_base)
    mb = git(repo, "merge-base", "HEAD", args.base_ref)
    if mb.returncode != 0 or not mb.stdout.strip():
        log(f"could not find merge-base with {args.base_ref}; running full source check.")
        print("false")
        return 0
    base = mb.stdout.strip()

    diff = git(repo, "diff", "--name-only", base, "HEAD", "--", f"{source_path}/")
    if diff.returncode != 0:
        log(f"git diff failed ({diff.stderr.strip()}); running full source check.")
        print("false")
        return 0

    changed = [line for line in diff.stdout.splitlines() if line.strip()]
    if not changed:
        log(f"no changes under {source_path}/ vs {base[:12]}; running full source check.")
        print("false")
        return 0

    log(f"changed files under {source_path}/ (vs {base[:12]}):")
    build_relevant = []
    for path in changed:
        rel = path[len(source_path) + 1 :]
        if is_nonbuild(rel, patterns):
            log(f"  non-build : {path}")
        else:
            log(f"  BUILD-REL : {path}")
            build_relevant.append(path)

    if build_relevant:
        log(
            f"{len(build_relevant)} build-relevant file(s) changed; "
            "running full source check."
        )
        print("false")
        return 0

    log(
        f"only non-build files changed under {source_path}/; "
        "skipping the container-source SHA check for this run."
    )
    print("true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
