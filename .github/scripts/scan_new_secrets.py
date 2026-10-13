#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Fail when a change adds a secret that was not in its base commit.

No baseline is committed. The base commit's copy of every changed file is
scanned into a temporary baseline, and detect-secrets-hook checks the head
copies against it. Strings that were already there pass; new ones fail.

The range is the one ``detect_changed_images.resolve_diff_base`` uses: the
push range on ``main``/``develop``, the merge-base with ``develop`` on
``pull-request/N``. When no range resolves (initial push, force-push), the
last commit is checked instead, so the gate never passes without a scan.

Git LFS paths are skipped: CI checks out pointers, and a changed pointer
carries a new sha256 that would read as a new secret.

Usage:
    python3 .github/scripts/scan_new_secrets.py [--base <commit>]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from detect_changed_images import resolve_diff_base  # noqa: E402

# Analytics fixtures hold 40-hex object ids that the hex entropy plugin flags.
EXCLUDE_LINES = r'^\s*"Id": "[0-9a-f]{40}",?$'
NOT_SCANNED_MODES = {"120000", "160000"}  # symlink, submodule


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE
    ).stdout


def changed_files(repo: Path, base: str) -> list[tuple[str | None, str]]:
    """``(base path or None, head path)`` for each added, modified or renamed file."""
    raw = git(
        repo,
        "diff",
        "--raw",
        "-z",
        "-M",
        "--no-abbrev",
        "--diff-filter=d",
        base,
        "HEAD",
        "--",
        ".",
        ":(exclude,attr:filter=lfs)",
    ).decode()
    fields = raw.split("\0")
    pairs: list[tuple[str | None, str]] = []
    i = 0
    while i < len(fields) and fields[i]:
        old_mode, new_mode, _, _, status = fields[i].lstrip(":").split(" ")
        if status[0] in "RC":
            old, new = fields[i + 1], fields[i + 2]
            i += 3
        else:
            old = new = fields[i + 1]
            i += 2
        if new_mode in NOT_SCANNED_MODES:
            continue
        pairs.append(
            (None if status == "A" or old_mode in NOT_SCANNED_MODES else old, new)
        )
    return pairs


def scan_new_secrets(repo: Path, base: str) -> int:
    pairs = changed_files(repo, base)
    print(f"files to scan: {len(pairs)}")
    if not pairs:
        return 0
    missing = [new for _, new in pairs if not (repo / new).is_file()]
    if missing:
        for path in missing:
            print(
                f"::error::changed file is not readable from the repository root: {path}"
            )
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        base_dir = Path(tmp) / "base"
        base_files = []
        for old, new in pairs:
            if old is None:
                continue
            dest = base_dir / new
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(git(repo, "show", f"{base}:{old}"))
            base_files.append(new)

        hook = [
            sys.executable,
            "-m",
            "detect_secrets.pre_commit_hook",
            "--no-verify",
            "--exclude-lines",
            EXCLUDE_LINES,
        ]
        if base_files:
            baseline = Path(tmp) / "baseline.json"
            with baseline.open("w") as out:
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "detect_secrets",
                        "scan",
                        "--slim",
                        "--no-verify",
                        "--exclude-lines",
                        EXCLUDE_LINES,
                        *base_files,
                    ],
                    cwd=base_dir,
                    check=True,
                    stdout=out,
                )
            hook += ["--baseline", str(baseline)]

        result = subprocess.run(
            [*hook, *(new for _, new in pairs)],
            cwd=repo,
            check=False,
            stdout=subprocess.PIPE,
            text=True,
        )
    # 3 means no new secrets but the baseline was rewritten, e.g. a secret was
    # removed. The baseline is a temporary file, so that is a pass.
    if result.returncode == 3:
        return 0
    print(result.stdout, end="")
    return result.returncode


def resolve_base(repo: Path) -> str:
    ref_name = os.environ.get("GITHUB_REF_NAME", "")
    base, reason = resolve_diff_base(
        repo,
        os.environ.get("GITHUB_EVENT_NAME", ""),
        ref_name,
        os.environ.get("BEFORE_SHA", ""),
        ref_name if ref_name in {"main", "develop"} else "develop",
    )
    if base is None:
        print(f"no diff base ({reason.split(';')[0]}); scanning the last commit")
        return "HEAD^"
    print(f"diff base: {reason}")
    return base


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", help="commit to compare against (default: CI range)")
    args = parser.parse_args(argv)
    repo = Path.cwd()
    return scan_new_secrets(repo, args.base or resolve_base(repo))


if __name__ == "__main__":
    sys.exit(main())
