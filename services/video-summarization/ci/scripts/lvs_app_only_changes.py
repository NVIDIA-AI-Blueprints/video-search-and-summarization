#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Determine whether video-summarization CI should rebuild the LVS image.

Modes:
  app-only (default): exit 0 / print "true" when all changes under
    services/video-summarization are app-only (or there are no service changes).
  any: exit 0 / print "true" when any file under services/video-summarization
    changed between ref and HEAD.

Changes outside services/video-summarization are ignored in both modes.

Usage:
  lvs_app_only_changes.py [--ref REF] [--mode app-only|any]
  REF defaults to HEAD^ (parent commit).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

from ci.utils.lvs_paths import LVS_SERVICE_PREFIX, is_under_service, strip_service_prefix

# Paths under the service root that do NOT require rebuilding the LVS image.
APP_ONLY_PREFIXES = (
    "src/",
    "perf/",
    "ci/",
    # Optional: add config subpaths that are runtime-only if we want to allow config changes without rebuild
    # "config/some_runtime_only/",
)


def _repo_root() -> Path:
    return Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )


def _ref_exists(repo_root: Path, ref: str) -> bool:
    return (
        subprocess.run(
            ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
            cwd=repo_root,
            capture_output=True,
        ).returncode
        == 0
    )


class UndeterminableChanges(Exception):
    """Raised when the changed file set cannot be reliably computed.

    Typically happens on a shallow clone (Jenkins default) where the parent
    commit object is absent and cannot be fetched. Callers should fail open
    (assume changes are present) rather than silently skipping work.
    """


def _is_shallow(repo_root: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() == "true"


def _try_deepen(repo_root: Path, *, quiet: bool = False) -> None:
    """Best-effort: fetch one more commit so HEAD^ resolves in a shallow clone.

    The repo is public, so no credentials are required. Failures are ignored;
    the caller re-checks whether the parent became available.
    """
    for extra in (["--deepen=1"], ["--unshallow"]):
        if _ref_exists(repo_root, "HEAD^"):
            return
        cmd = ["git", "fetch", "--no-tags", "--quiet", *extra]
        if not quiet:
            print(f"Shallow clone: attempting `{' '.join(cmd)}` to expose HEAD^", file=sys.stderr)
        subprocess.run(cmd, cwd=repo_root, capture_output=True)


def resolve_change_ref(repo_root: Path, ref: str, *, quiet: bool = False) -> str | None:
    """Return a ref suitable for ``git diff ref..HEAD``, or None if undeterminable."""
    # Jenkins/GitLab MR builds often checkout the merge commit; compare vs first parent.
    if _ref_exists(repo_root, "HEAD^2") and _ref_exists(repo_root, "HEAD^1"):
        return "HEAD^1"
    if _ref_exists(repo_root, ref):
        return ref
    # Shallow clone: the parent object is absent. Try to fetch just enough history.
    if _is_shallow(repo_root):
        _try_deepen(repo_root, quiet=quiet)
        if _ref_exists(repo_root, "HEAD^2") and _ref_exists(repo_root, "HEAD^1"):
            return "HEAD^1"
        if _ref_exists(repo_root, ref):
            return ref
    return None


def get_changed_files(ref: str, *, quiet: bool = False) -> list[str]:
    """Return file paths changed between ref and HEAD (relative to the git root)."""
    repo_root = _repo_root()
    effective_ref = resolve_change_ref(repo_root, ref, quiet=quiet)
    if effective_ref is None:
        raise UndeterminableChanges(
            f"Ref {ref!r} is unavailable and history could not be deepened (shallow clone)"
        )
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{effective_ref}..HEAD"],
        check=True,
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    return [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]


def filter_service_changes(changed: list[str]) -> list[str]:
    return [path for path in changed if is_under_service(path)]


def is_app_only_path(path: str) -> bool:
    p = path.lstrip("/").replace("\\", "/")
    if not p:
        return False
    return any(p.startswith(prefix) or p == prefix.rstrip("/") for prefix in APP_ONLY_PREFIXES)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check video-summarization changes for CI image reuse / pipeline gating"
    )
    parser.add_argument(
        "--ref",
        default="HEAD^",
        help="Git ref to compare against HEAD (default: HEAD^)",
    )
    parser.add_argument(
        "--mode",
        choices=("app-only", "any"),
        default="app-only",
        help="app-only: only app-only service paths changed; any: any service path changed",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print true/false, no extra messages to stderr",
    )
    args = parser.parse_args()

    try:
        changed = get_changed_files(args.ref, quiet=args.quiet)
    except UndeterminableChanges as exc:
        # Fail open: on uncertainty, do the work rather than skip it.
        #   any mode (pipeline gate): treat as "has changes" -> run the pipeline.
        #   app-only mode (image reuse): treat as "not app-only" -> force a rebuild.
        if not args.quiet:
            print(f"{exc}; failing open (assuming changes present)", file=sys.stderr)
        print("true" if args.mode == "any" else "false")
        return 0 if args.mode == "any" else 1
    except subprocess.CalledProcessError as exc:
        if not args.quiet:
            print(f"git diff failed: {exc}", file=sys.stderr)
        print("false")
        return 1

    service_changed = filter_service_changes(changed)
    if not service_changed:
        if not args.quiet:
            ignored = [path for path in changed if not is_under_service(path)]
            if ignored:
                print(
                    f"Ignoring {len(ignored)} change(s) outside {LVS_SERVICE_PREFIX}",
                    file=sys.stderr,
                )
        print("true" if args.mode == "app-only" else "false")
        return 0 if args.mode == "app-only" else 1

    if args.mode == "any":
        if not args.quiet:
            print(
                f"Service changes detected under {LVS_SERVICE_PREFIX}: {service_changed}",
                file=sys.stderr,
            )
        print("true")
        return 0

    relative_changed = [strip_service_prefix(path) for path in service_changed]
    non_app = [path for path in relative_changed if not is_app_only_path(path)]
    if non_app:
        if not args.quiet:
            print(
                f"Non-app-only service changes (require LVS rebuild): {non_app}",
                file=sys.stderr,
            )
        print("false")
        return 1

    print("true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
