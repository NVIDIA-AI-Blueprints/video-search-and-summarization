#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Verify that source files start with the NVIDIA SPDX + Apache-2.0 header.

The first non-shebang lines of each tracked source file must contain:

    SPDX-FileCopyrightText: Copyright (c) <YEAR>[-<YEAR>], NVIDIA CORPORATION & AFFILIATES. All rights reserved.
    SPDX-License-Identifier: Apache-2.0
    ... full Apache-2.0 boilerplate ...

prefixed with the comment style appropriate for the file (``#`` for
Python/YAML/shell, ``//`` for JS/TS, ``<!--`` for HTML/Markdown).

Files are discovered via ``git ls-files`` so the check honours
``.gitignore``. A small set of paths and globs is excluded by default
(``3rdparty/``, generated dirs, vendored UI workspaces, lockfiles).

Usage::

    python3 .github/scripts/check_license_headers.py [--root REPO_ROOT]

Exit code 0 on success, 1 if any file is missing the header.
"""

from __future__ import annotations

import argparse
import fnmatch
from pathlib import Path
import re
import subprocess
import sys

# ---------------------------------------------------------------------------
# Header definition
# ---------------------------------------------------------------------------

# Year/year-range tolerated: 2025, 2024-2026, 2025-2026, ...
COPYRIGHT_RE = re.compile(
    r"SPDX-FileCopyrightText: Copyright \(c\) \d{4}(?:-\d{4})?, "
    r"NVIDIA CORPORATION & AFFILIATES\. All rights reserved\."
)

# After the copyright line, these must appear in order. Each entry is
# either a literal string or a compiled regex. Strings are matched
# against the line *after* the comment-prefix has been stripped.
HEADER_BODY: list[str | re.Pattern[str]] = [
    COPYRIGHT_RE,
    "SPDX-License-Identifier: Apache-2.0",
    "",
    'Licensed under the Apache License, Version 2.0 (the "License");',
    "you may not use this file except in compliance with the License.",
    "You may obtain a copy of the License at",
    "",
    "http://www.apache.org/licenses/LICENSE-2.0",
    "",
    "Unless required by applicable law or agreed to in writing, software",
    'distributed under the License is distributed on an "AS IS" BASIS,',
    "WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.",
    "See the License for the specific language governing permissions and",
    "limitations under the License.",
]

# ---------------------------------------------------------------------------
# Comment styles per file extension
#
# `prefix`   : leading comment marker that must appear (with one optional
#              space) before each header line.
# `block_open`/`block_close`: optional opening/closing tokens for block
#              comments (e.g. ``/*`` ... ``*/`` or ``<!--`` ... ``-->``).
#              When set, the script tolerates a wrapper that surrounds the
#              header lines.
# ---------------------------------------------------------------------------


HASH_STYLE = {"prefix": "#", "block_open": None, "block_close": None}
SLASH_STYLE = {"prefix": "//", "block_open": None, "block_close": None}
BLOCK_STAR_STYLE = {"prefix": "*", "block_open": "/*", "block_close": "*/"}
BLOCK_HTML_STYLE = {"prefix": None, "block_open": "<!--", "block_close": "-->"}

EXTENSION_STYLES: dict[str, list[dict]] = {
    # Python
    ".py": [HASH_STYLE],
    ".pyi": [HASH_STYLE],
    # Shell
    ".sh": [HASH_STYLE],
    ".bash": [HASH_STYLE],
    ".zsh": [HASH_STYLE],
    # YAML
    ".yml": [HASH_STYLE],
    ".yaml": [HASH_STYLE],
    # Dockerfiles handled by name below
    # JS / TS family
    ".js": [SLASH_STYLE, BLOCK_STAR_STYLE],
    ".jsx": [SLASH_STYLE, BLOCK_STAR_STYLE],
    ".mjs": [SLASH_STYLE, BLOCK_STAR_STYLE],
    ".cjs": [SLASH_STYLE, BLOCK_STAR_STYLE],
    ".ts": [SLASH_STYLE, BLOCK_STAR_STYLE],
    ".tsx": [SLASH_STYLE, BLOCK_STAR_STYLE],
    # CSS / SCSS — block comments only
    ".css": [BLOCK_STAR_STYLE],
    ".scss": [BLOCK_STAR_STYLE],
    # Markdown / HTML
    ".md": [BLOCK_HTML_STYLE],
    ".mdx": [BLOCK_HTML_STYLE],
    ".html": [BLOCK_HTML_STYLE],
    ".htm": [BLOCK_HTML_STYLE],
    # TOML
    ".toml": [HASH_STYLE],
}

# Files matched by basename rather than extension.
BASENAME_STYLES: dict[str, list[dict]] = {
    "Dockerfile": [HASH_STYLE],
    "Makefile": [HASH_STYLE],
}

# Default excludes (gitignore-style globs, evaluated against the path
# returned by `git ls-files`, which is repo-relative POSIX).
DEFAULT_EXCLUDES: list[str] = [
    # Vendored / 3rd-party code: we do not own the headers.
    "services/agent/3rdparty/**",
    "services/agent/stubs/**",
    "services/ui/packages/nemo-agent-toolkit-ui/**",
    "services/ui/packages/nv-metropolis-bp-vss-ui/**",
    "services/ui/apps/**",
    "services/ui/packages/common/**",
    # Generated / lock files.
    "**/uv.lock",
    "**/package-lock.json",
    "**/yarn.lock",
    "**/pnpm-lock.yaml",
    "**/*.lock",
    # Common root files that ship their own license text.
    "LICENSE",
    "LICENSE.*",
    "LICENSE-*",
    "**/LICENSE",
    "**/LICENSE.*",
    "**/LICENSE-*",
    # Repo metadata that does not need a header.
    ".gitattributes",
    ".gitignore",
    ".github/CODEOWNERS",
    ".github/copy-pr-bot.yaml",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/ISSUE_TEMPLATE/**",
    "**/.gitignore",
    "**/.gitattributes",
    # Markdown files inside services may legitimately ship without a
    # header (READMEs, contributing docs). Limit the header check on
    # markdown to a small set of files we maintain.
    # Excluding all markdown by default to avoid noise; opt in via the
    # repo CSV/policy if needed.
    "**/*.md",
    "**/*.mdx",
    # Database/config CSVs we generated do not need headers.
    ".github/license-database/*.csv",
]

# ---------------------------------------------------------------------------


def discover_files(root: Path) -> list[Path]:
    """Use ``git ls-files`` so we honour .gitignore and ignore untracked junk."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        text=False,
        check=True,
    )
    relpaths = [p.decode("utf-8") for p in out.stdout.split(b"\x00") if p]
    return [root / r for r in relpaths]


def _match_glob(relpath: str, pattern: str) -> bool:
    """Gitignore-flavoured glob match.

    Supports a leading ``**/`` to mean "in any directory, including
    repo root", which plain ``fnmatch`` does not handle.
    """
    if fnmatch.fnmatchcase(relpath, pattern):
        return True
    return bool(pattern.startswith("**/") and fnmatch.fnmatchcase(relpath, pattern[3:]))


def is_excluded(relpath: str, excludes: list[str]) -> bool:
    return any(_match_glob(relpath, pat) for pat in excludes)


def styles_for(path: Path) -> list[dict] | None:
    name = path.name
    if name in BASENAME_STYLES:
        return BASENAME_STYLES[name]
    return EXTENSION_STYLES.get(path.suffix.lower())


def _strip_prefix(line: str, prefix: str) -> str | None:
    """Return the line content with the comment ``prefix`` stripped.

    Returns ``None`` if the line does not start with the prefix.
    """
    s = line.rstrip("\r\n")
    if not s.startswith(prefix):
        return None
    rest = s[len(prefix) :]
    # Allow exactly one optional space after the prefix.
    if rest.startswith(" "):
        rest = rest[1:]
    return rest


def _matches(expected: str | re.Pattern[str], actual: str) -> bool:
    if isinstance(expected, re.Pattern):
        return bool(expected.search(actual))
    return actual == expected


def _check_with_style(lines: list[str], style: dict) -> bool:
    """Return True if ``lines`` (already shebang-stripped) match the header in this style."""
    block_open = style.get("block_open")
    block_close = style.get("block_close")
    prefix = style.get("prefix")

    idx = 0

    if block_open is not None:
        # First non-empty line must start with the block opener.
        while idx < len(lines) and lines[idx].strip() == "":
            idx += 1
        if idx >= len(lines):
            return False
        first = lines[idx].rstrip("\r\n").lstrip()
        if not first.startswith(block_open):
            return False
        # If the line has more than the opener, treat the remainder as
        # the first header line (e.g. ``<!-- SPDX-... -->``).
        remainder = first[len(block_open) :].strip()
        if remainder:
            # Could be either a single-line block comment with the whole
            # header on one line (uncommon), or just `<!--` followed by
            # content on the next lines. Defer to the multi-line path:
            # treat the remainder as a candidate first body line.
            body_lines = [remainder, *[ln.rstrip("\r\n") for ln in lines[idx + 1 :]]]
        else:
            body_lines = [ln.rstrip("\r\n") for ln in lines[idx + 1 :]]
    else:
        body_lines = [ln.rstrip("\r\n") for ln in lines[idx:]]

    # Now match each expected entry against successive body lines.
    consumed = 0
    for expected in HEADER_BODY:
        if consumed >= len(body_lines):
            return False
        raw = body_lines[consumed]
        consumed += 1

        if prefix is not None:
            stripped = _strip_prefix(raw, prefix)
            if stripped is None:
                return False
            actual = stripped
        else:
            # Pure block comment (HTML). Lines may have leading spaces.
            actual = raw.strip()
            if actual == block_close:
                return False

        if not _matches(expected, actual):
            return False

    if block_close is not None:
        # The next non-empty line should be the block closer (or contain it).
        while consumed < len(body_lines) and body_lines[consumed].strip() == "":
            consumed += 1
        if consumed >= len(body_lines):
            return False
        if block_close not in body_lines[consumed]:
            return False

    return True


def has_header(path: Path) -> bool:
    styles = styles_for(path)
    if styles is None:
        # Unknown file type: pass.
        return True

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return True  # Binary or unreadable: skip.

    raw_lines = text.splitlines()
    if not raw_lines:
        return False

    # Drop a leading shebang line if present.
    if raw_lines[0].startswith("#!"):
        lines = raw_lines[1:]
    else:
        lines = raw_lines

    return any(_check_with_style(lines, style) for style in styles)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository root (default: cwd).")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Additional gitignore-style glob to exclude. May be repeated.",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    excludes = [*DEFAULT_EXCLUDES, *args.exclude]

    all_files = discover_files(root)

    checked: list[Path] = []
    missing: list[Path] = []

    for path in all_files:
        rel = path.relative_to(root).as_posix()
        if is_excluded(rel, excludes):
            continue
        if styles_for(path) is None:
            continue
        checked.append(path)
        if not has_header(path):
            missing.append(path)

    if missing:
        print(f"ERROR: {len(missing)} file(s) missing the required NVIDIA SPDX + Apache-2.0 header:\n")
        for p in missing:
            print(f"  {p.relative_to(root).as_posix()}")
        print("\nExpected header (after an optional shebang) using the file's comment style.")
        print("Examples:\n")
        print("  Python / YAML / shell (`#`):")
        print(
            "    # SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved."
        )
        print("    # SPDX-License-Identifier: Apache-2.0")
        print("    # ... Apache-2.0 boilerplate ...\n")
        print("  JavaScript / TypeScript (`//`):")
        print(
            "    // SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved."
        )
        print("    // SPDX-License-Identifier: Apache-2.0")
        print("    // ... Apache-2.0 boilerplate ...")
        return 1

    print(f"OK: All {len(checked)} source file(s) have the required NVIDIA SPDX header.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
