#!/usr/bin/env python3
"""Validate that selected directories only contain expected entries.

Each rule lists the directory to inspect plus the file / sub-directory
name patterns (``fnmatch`` globs) that are allowed at its top level.
Any entry whose name does not match at least one allowed pattern is
reported as a violation and the script exits with status 1.

To enforce structure on more directories, append another ``Rule`` to
``RULES`` below.
"""

from __future__ import annotations

import fnmatch
import sys
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path


@dataclass(frozen=True)
class Rule:
    """A folder-structure rule."""

    folder: str
    files: tuple[str, ...] = field(default_factory=tuple)
    dirs: tuple[str, ...] = field(default_factory=tuple)


RULES: tuple[Rule, ...] = (
    # deploy/developer-workflow/ may only contain:
    #   - a file named compose.yml
    #   - directories whose names start with dev-profile-
    Rule(
        folder="deploy/developer-workflow",
        files=("compose.yml",),
        dirs=("dev-profile-*",),
    ),
    # To add more rules copy the pattern above, e.g.:
    # Rule(folder="deployments/production", files=("compose.yml",), dirs=("prod-profile-*",)),
)


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(name, pat) for pat in patterns)


def check_folder(rule: Rule) -> int:
    """Return the number of violations for ``rule`` (0 when OK)."""
    print(f"Checking {rule.folder}/")
    path = Path(rule.folder)

    if not path.is_dir():
        print(f"  ERROR: '{rule.folder}' does not exist in the repository.")
        return 1

    violations = 0
    for entry in sorted(path.iterdir(), key=lambda p: p.name):
        if entry.is_dir():
            if not _matches(entry.name, rule.dirs):
                print(f"  Unexpected directory: {entry.name}")
                violations += 1
        elif not _matches(entry.name, rule.files):
            print(f"  Unexpected file: {entry.name}")
            violations += 1

    if violations == 0:
        print("  OK")
    return violations


def main() -> int:
    failed = sum(check_folder(rule) for rule in RULES)
    if failed:
        print("\nFAILED: folder structure violations found. See entries above.")
        return 1
    print("\nPASSED: all folder structure checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
