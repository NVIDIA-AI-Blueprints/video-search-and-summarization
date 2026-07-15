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


def get_changed_files(ref: str) -> list[str]:
    """Return file paths changed between ref and HEAD (relative to the git root)."""
    repo_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{ref}..HEAD"],
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
        changed = get_changed_files(args.ref)
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
