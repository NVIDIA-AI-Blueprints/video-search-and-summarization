# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reject unintended Fern environment-variable substitutions in docs inputs."""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = (
    REPO_ROOT / "docs",
    REPO_ROOT / "fern",
)
EXTRA_INPUTS = (
    REPO_ROOT
    / "services"
    / "analytics"
    / "video-analytics-api"
    / "src"
    / "app"
    / "specification"
    / "openapi.json",
)
TEXT_SUFFIXES = frozenset({".json", ".md", ".mdx", ".yaml", ".yml"})

# Intentionally empty for the substitution-enablement migration. Add future
# build-time documentation variables explicitly; use a VSS_DOCS_ prefix.
ALLOWED_SUBSTITUTIONS: frozenset[str] = frozenset()

# These mirror Fern's documented/implemented forms:
#   substitution: ${NAME}
#   literal:      \$\{NAME\}
SUBSTITUTION_PATTERN = re.compile(r"\$\{(?P<name>\w+)\}")
ESCAPE_PREFIX_PATTERN = re.compile(r"\\\$\\\{(?P<name>\w+)(?P<close>\\?\})")


def iter_inputs(scan_roots: Iterable[Path], extra_inputs: Iterable[Path]) -> Iterable[Path]:
    """Yield Fern text inputs without following directory symlinks."""
    for root in scan_roots:
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.suffix.lower() in TEXT_SUFFIXES:
                yield path
    for path in extra_inputs:
        if path.is_file():
            yield path


def find_issues(path: Path, text: str) -> list[str]:
    """Return actionable errors for one Fern input file."""
    issues: list[str] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in SUBSTITUTION_PATTERN.finditer(line):
            name = match.group("name")
            if name not in ALLOWED_SUBSTITUTIONS:
                issues.append(
                    f"{path}:{line_number}: unintended Fern substitution ${{{name}}}; "
                    f"write \\$\\{{{name}\\}} for a literal"
                )

        for match in ESCAPE_PREFIX_PATTERN.finditer(line):
            if match.group("close") != r"\}":
                name = match.group("name")
                issues.append(
                    f"{path}:{line_number}: malformed Fern literal escape for {name}; "
                    f"write \\$\\{{{name}\\}}"
                )

    return issues


def main() -> int:
    issues: list[str] = []
    files = list(iter_inputs(SCAN_ROOTS, EXTRA_INPUTS))
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        issues.extend(find_issues(path.relative_to(REPO_ROOT), text))

    if issues:
        print("Fern environment-variable substitution safety check failed:", file=sys.stderr)
        for issue in issues:
            print(f"  {issue}", file=sys.stderr)
        print(
            "Escape documentation literals as \\$\\{NAME\\}. "
            "Actual build-time variables must be explicitly allowlisted.",
            file=sys.stderr,
        )
        return 1

    print(f"Fern substitution safety check passed ({len(files)} text inputs scanned).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
