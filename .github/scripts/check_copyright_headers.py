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
import os
import subprocess
import sys
from pathlib import Path

# SPDX identifier that must appear in the first 5 lines of each file
REQUIRED_MARKER = "SPDX-License-Identifier"

# File extensions to check
CHECK_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx",
    ".sh", ".c", ".cc", ".cpp", ".h", ".hpp", ".cu", ".cuh", ".proto",
}

# SPDX licence expressions a first-party header may declare. Anything else
# (including a malformed value like "Apache-2.0/") fails the check.
ALLOWED_LICENSES = {
    "Apache-2.0",
    "MIT",
    "MIT AND Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "LicenseRef-NvidiaProprietary",
}

# The only NVIDIA copyright entity allowed in SPDX-FileCopyrightText lines.
NVIDIA_ENTITY = "NVIDIA CORPORATION & AFFILIATES"

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
    # Vendored upstream trees — third-party headers, not ours to stamp
    "services/vios/src/framework/webrtc_streamer/inc/webrtc_headers/**",
    "services/vios/include/opentelemetry/**",
    "services/vios/src/framework/live555/inc/live/**",
    "libs/nvschema/protobuf/struct.proto",
    # Protobuf generated files
    "**/schema_pb.js",
    "**/ext_pb.js",
    "**/schema_pb2.py",
    "**/ext_pb2.py",
    "**/schema_pb.rb",
    "**/ext_pb.rb"
)


def repo_root() -> Path:
    """Return the repository root, or the CI workspace when .git is absent."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return Path(result.stdout.strip())
    return Path(os.environ.get("GITHUB_WORKSPACE", ".")).resolve()


def tracked_files(root: Path) -> list[str]:
    """Return all tracked files from git or the CI source manifest."""
    manifest = root / ".ci" / "tracked-files.txt"
    if manifest.is_file():
        return manifest.read_text(encoding="utf-8").splitlines()

    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip().splitlines()


def is_excluded(path: str) -> bool:
    """Check if path matches any exclude pattern."""
    return any(fnmatch.fnmatch(path, pat) for pat in EXCLUDE_PATTERNS)


def header_problem(filepath: str) -> str | None:
    """Return a problem description for the file's header, or None if valid.

    A valid header, within the first 5 lines, has:
      - an SPDX-License-Identifier whose value is on ALLOWED_LICENSES
      - an SPDX-FileCopyrightText line
      - the canonical entity on any NVIDIA copyright line
    """
    try:
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            head = [line for _, line in zip(range(5), f)]
    except (OSError, UnicodeDecodeError):
        return None  # skip unreadable files

    licence = None
    holder = False
    for line in head:
        if REQUIRED_MARKER in line:
            value = line.split(REQUIRED_MARKER + ":", 1)[-1].strip()
            for terminator in ("*/", "-->"):
                value = value.removesuffix(terminator).strip()
            licence = value
        if "SPDX-FileCopyrightText" in line:
            value = line.split("SPDX-FileCopyrightText:", 1)[-1].strip()
            for terminator in ("*/", "-->"):
                value = value.removesuffix(terminator).strip()
            if "copyright" not in value.lower():
                return "SPDX-FileCopyrightText carries no copyright text"
            holder = True
            if "NVIDIA" in line.upper() and NVIDIA_ENTITY not in line:
                return f"copyright entity is not '{NVIDIA_ENTITY}'"
    if licence is None:
        return "missing SPDX-License-Identifier"
    if licence not in ALLOWED_LICENSES:
        return f"licence {licence!r} is not on the allowed list"
    if not holder:
        return "missing SPDX-FileCopyrightText"
    return None


def main() -> int:
    root = repo_root()
    files = tracked_files(root)
    missing: list[str] = []

    for filepath in files:
        ext = Path(filepath).suffix
        if ext not in CHECK_EXTENSIONS:
            continue
        if is_excluded(filepath):
            continue
        problem = header_problem(str(root / filepath))
        if problem:
            missing.append(f"{filepath}: {problem}")

    if missing:
        print(f"ERROR: {len(missing)} file(s) with SPDX header problems:\n")
        for f in sorted(missing):
            print(f"  {f}")
        print(f"\nExpected '{REQUIRED_MARKER}' and 'SPDX-FileCopyrightText' in "
              "the first 5 lines, with an allowed licence and the canonical "
              "NVIDIA entity.")
        print("See CONTRIBUTING.md for the required header format.")
        return 1

    print(f"OK: All {len(files)} tracked files checked — no missing headers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
