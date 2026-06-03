#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Decide whether the container-source SHA check is relevant to this PR.

The ``Check {Agent,UI} Container Source`` gates compare the git tree SHA of
``services/agent`` / ``services/ui`` against the ``com.nvidia.vss.source_tree_sha``
baked into the deployed image. Because that tree SHA covers the *whole* folder,
once an allowed docs change merges to ``develop`` the folder drifts from the
promoted image — and then the check fails on **every** later PR, even ones that
touch neither the service nor the image tag (it re-evaluates HEAD's drifted
folder). The gate is also a required status check, so it must always run and
report a conclusion; we can't skip it at the workflow trigger or the PR hangs.

So the gate runs the SHA comparison only when the PR could actually affect the
match, i.e. it changed either:

  * a **build-relevant** file under the service folder (anything the image
    build consumes — i.e. not the allowlisted docs/tests/metadata below), or
  * the service's **resolved deployable image tag** (a tag bump in
    ``deploy/docker`` compose/.env).

Otherwise it prints ``true`` (skip — this PR can't have broken the match), so a
predecessor's docs-drift doesn't red-flag unrelated PRs. It prints ``false``
(run the real check) whenever a build-relevant file or this service's tag
changed, on integration-branch pushes, or whenever it is unsure.

The tag comparison is per-service and value-based (resolve the image ref at
HEAD vs the merge-base), so a UI tag bump in a shared ``.env`` does not trip the
agent gate and vice-versa.

Output: a single ``true``/``false`` token on stdout. Diagnostics go to stderr.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys
from pathlib import Path

# Sibling module in .github/scripts/ (sys.path[0] when run as a script). We
# reuse its compose/.env discovery + variable resolution so the gate's notion
# of "the service's image tag" is identical to the check's.
import check_container_tag_source as chk


# Image name -> source folder, derived from the check's own config so the two
# can't drift.
SOURCE_PATHS = {
    name: cfg.source_path.as_posix() for name, cfg in chk.IMAGE_CONFIGS.items()
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


# --- resolved image-tag comparison (per service, value-based) ---------------
#
# Reuse check_container_tag_source's compose/.env discovery + variable
# expansion, but feed file contents through a pluggable reader so we can
# resolve at HEAD (working tree) and at the merge-base (git show) and compare
# the resolved ref sets. We reuse chk.parse_env_text / chk.image_refs_in_text
# so the gate resolves identically to the check (no parallel parsers to drift).

def resolve_service_refs(repo: Path, read_text, image_name: str) -> tuple[set[str], set[str]]:
    """Return ``(resolved, unresolved)`` deployable image refs for ``image_name``.

    ``read_text(relpath)`` returns the file's content (or None if absent) at the
    revision being inspected. ``unresolved`` holds raw refs that match the
    image name but that no ``.env`` could fully expand — the check treats those
    as a hard failure, so the gate must run when one is newly introduced.
    """
    config = chk.IMAGE_CONFIGS[image_name]
    compose_files = chk.discover_compose_files(repo)
    env_files = chk.discover_env_files(repo)

    env_caches: dict[Path, dict[str, str]] = {}
    for ef in env_files:
        env_caches[ef] = chk.parse_env_text(read_text(str(ef.relative_to(repo))) or "")

    resolved: set[str] = set()
    unresolved: set[str] = set()
    for cf in compose_files:
        text = read_text(str(cf.relative_to(repo)))
        if not text:
            continue
        for raw in chk.image_refs_in_text(text, config.image_name):
            _, needed = chk.resolve_compose_vars(raw, {})
            if not needed:
                expanded, _ = chk.resolve_compose_vars(raw, dict(os.environ))
                resolved.add(expanded)
                continue
            applied = False
            for ef in env_files:
                env_values = env_caches[ef]
                if all(name in env_values for name in needed):
                    expanded, missing = chk.resolve_compose_vars(
                        raw, {**env_values, **os.environ}
                    )
                    if not missing:
                        resolved.add(expanded)
                        applied = True
            if not applied:
                # No env file supplies the needed vars — the check would record
                # this as an unresolved image and fail.
                unresolved.add(raw)
    return resolved, unresolved


def service_tag_changed(repo: Path, base: str, image_name: str) -> bool:
    """True if the service's image ref(s) differ between base and HEAD, or HEAD
    introduces a ref the check would treat as unresolved."""
    def head_reader(rel: str):
        path = repo / rel
        return path.read_text() if path.exists() else None

    def base_reader(rel: str):
        result = git(repo, "show", f"{base}:{rel}")
        return result.stdout if result.returncode == 0 else None

    head_resolved, head_unresolved = resolve_service_refs(repo, head_reader, image_name)
    if not head_resolved and not head_unresolved:
        # No deployable ref for this service at HEAD — be conservative and run.
        log("found no image refs at HEAD; running full source check.")
        return True

    base_resolved, base_unresolved = resolve_service_refs(repo, base_reader, image_name)

    if head_resolved != base_resolved:
        log(f"image tag changed vs {base[:12]}:")
        for r in sorted(head_resolved - base_resolved):
            log(f"  + {r}")
        for r in sorted(base_resolved - head_resolved):
            log(f"  - {r}")
        return True

    # A ref that resolves at HEAD nowhere it didn't at base would make the real
    # check fail (unresolved image); don't let the gate mask that.
    new_unresolved = head_unresolved - base_unresolved
    if new_unresolved:
        log("PR introduces image ref(s) that no .env resolves; running full source check:")
        for r in sorted(new_unresolved):
            log(f"  ? {r}")
        return True

    return False


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

    # (1) Did the PR change a build-relevant file under the service folder?
    diff = git(repo, "diff", "--name-only", base, "HEAD", "--", f"{source_path}/")
    if diff.returncode != 0:
        log(f"git diff failed ({diff.stderr.strip()}); running full source check.")
        print("false")
        return 0
    changed = [line for line in diff.stdout.splitlines() if line.strip()]
    build_relevant = []
    if changed:
        log(f"changed files under {source_path}/ (vs {base[:12]}):")
        for path in changed:
            rel = path[len(source_path) + 1 :]
            if is_nonbuild(rel, patterns):
                log(f"  non-build : {path}")
            else:
                log(f"  BUILD-REL : {path}")
                build_relevant.append(path)
    else:
        log(f"no changes under {source_path}/ vs {base[:12]}.")

    if build_relevant:
        log(
            f"{len(build_relevant)} build-relevant file(s) changed; "
            "running full source check."
        )
        print("false")
        return 0

    # (2) Did the PR bump this service's deployable image tag?
    if service_tag_changed(repo, base, args.image_name):
        log("image tag changed; running full source check.")
        print("false")
        return 0

    log(
        f"neither build-relevant source nor the {args.image_name} image tag "
        "changed; skipping the container-source SHA check for this run."
    )
    print("true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
