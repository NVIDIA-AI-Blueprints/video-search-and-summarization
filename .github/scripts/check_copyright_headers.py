#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check that source files contain an SPDX copyright header.

Scans Python (.py) and TypeScript/JavaScript (.ts, .tsx, .js, .jsx)
files tracked by git. Files matching EXCLUDE_PATTERNS are skipped.

Exit code 0 if all files pass, 1 if any are missing headers.
"""

from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path

# SPDX identifier that must appear in the first 5 lines of each file
REQUIRED_MARKER = "SPDX-License-Identifier"

# File extensions to check
CHECK_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx"}

# Glob patterns to skip (relative to repo root)
EXCLUDE_PATTERNS = (
    # Auto-generated / third-party
    "**/node_modules/**",
    "**/__pycache__/**",
    "**/.venv/**",
    "**/3rdparty/**",
    # Next.js generated type declarations
    "**/*-env.d.ts",
    "**/next-env.d.ts",
    # Config files that are too short for headers
    "**/.eslintrc.js",
    # Lock files
    "**/uv.lock",
    "**/package-lock.json",
    # Stubs (third-party type stubs)
    "**/stubs/**",
)


def git_ls_files() -> list[str]:
    """Return all git-tracked files."""
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip().splitlines()


def is_excluded(path: str) -> bool:
    """Check if path matches any exclude pattern."""
    return any(fnmatch.fnmatch(path, pat) for pat in EXCLUDE_PATTERNS)


def has_spdx_header(filepath: str) -> bool:
    """Check if the first 5 lines contain the SPDX marker."""
    try:
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                if i >= 5:
                    break
                if REQUIRED_MARKER in line:
                    return True
    except (OSError, UnicodeDecodeError):
        return True  # skip unreadable files
    return False


def main() -> int:
    files = git_ls_files()
    missing: list[str] = []

    for filepath in files:
        ext = Path(filepath).suffix
        if ext not in CHECK_EXTENSIONS:
            continue
        if is_excluded(filepath):
            continue
        if not has_spdx_header(filepath):
            missing.append(filepath)

    if missing:
        print(f"ERROR: {len(missing)} file(s) missing SPDX copyright header:\n")
        for f in sorted(missing):
            print(f"  {f}")
        print(f"\nExpected '{REQUIRED_MARKER}' in the first 5 lines.")
        print("See CONTRIBUTING.md for the required header format.")
        return 1

    print(f"OK: All {len(files)} tracked files checked — no missing headers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
