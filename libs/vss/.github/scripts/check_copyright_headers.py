#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check tracked source files for SPDX license headers."""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
import sys
from pathlib import Path

REQUIRED_MARKER = "SPDX-License-Identifier"
CHECK_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx"}
EXCLUDE_PATTERNS = (
    "**/node_modules/**",
    "**/__pycache__/**",
    "**/.venv/**",
    "**/3rdparty/**",
    "**/*-env.d.ts",
    "**/next-env.d.ts",
    "**/.eslintrc.js",
    "**/uv.lock",
    "**/package-lock.json",
    "**/stubs/**",
)


def repo_root() -> Path:
    """Return the repository root, or the CI workspace when Git is absent."""

    if shutil.which("git") is None:
        return Path(os.environ.get("CI_PROJECT_DIR", ".")).resolve()

    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return Path(result.stdout.strip())
    return Path(os.environ.get("CI_PROJECT_DIR", ".")).resolve()


def tracked_files(root: Path) -> list[str]:
    """Return tracked files, or workspace files when Git is unavailable."""

    if shutil.which("git") is not None:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip().splitlines()

    files: list[str] = []
    excluded_directories = {".git", ".venv", "__pycache__", "node_modules"}
    for directory, directories, filenames in os.walk(root):
        directories[:] = [
            name for name in directories if name not in excluded_directories
        ]
        parent = Path(directory)
        files.extend(
            (parent / filename).relative_to(root).as_posix() for filename in filenames
        )
    return files


def is_excluded(path: str) -> bool:
    """Return whether a path matches an excluded glob."""

    return any(fnmatch.fnmatch(path, pattern) for pattern in EXCLUDE_PATTERNS)


def has_spdx_header(filepath: Path) -> bool:
    """Return whether the first five lines contain the SPDX marker."""

    try:
        with filepath.open(encoding="utf-8", errors="ignore") as source:
            return any(
                REQUIRED_MARKER in line
                for line_number, line in enumerate(source)
                if line_number < 5
            )
    except OSError:
        return True


def main() -> int:
    """Report tracked source files that do not have SPDX headers."""

    root = repo_root()
    checked = [
        path
        for path in tracked_files(root)
        if Path(path).suffix in CHECK_EXTENSIONS and not is_excluded(path)
    ]
    missing = [path for path in checked if not has_spdx_header(root / path)]

    if missing:
        print(f"ERROR: {len(missing)} file(s) missing SPDX copyright headers:\n")
        for path in sorted(missing):
            print(f"  {path}")
        print(f"\nExpected '{REQUIRED_MARKER}' in the first five lines.")
        return 1

    print(f"OK: All {len(checked)} checked source files have SPDX headers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
