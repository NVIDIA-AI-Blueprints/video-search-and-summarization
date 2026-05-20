#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Permissive-license allowlist enforcer for the agent's runtime Python deps.

Reads `pip-licenses --format=csv` from stdin and fails the build for any
package whose declared license does not match the permissive allowlist
below — unless the package name is listed in the override file (one name
per line, `#` comments allowed).

The override file is the single audit-trail document: every line there is a
package that OSRB has explicitly cleared despite a non-allowlist license
string. Removing the override means re-justifying or replacing the dep.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

# Each entry is a regex matching one canonical permissive license. Match is
# case-insensitive and applied to each clause of the package's license field
# (clauses split on ; , and the SPDX operators OR / AND). A package passes
# only when *every* clause matches at least one entry.
PERMISSIVE_LICENSE_PATTERNS = [
    # Apache 2.0 — covers "Apache-2.0", "Apache 2.0", "Apache License 2.0",
    # "Apache Software License", "Apache Software License 2.0", and the
    # bare "Apache".
    r"^Apache(?:[ -]?Software)?(?:[ -]?License)?(?:[ -]?(?:2|2\.0))?$",
    r"^Apache(?:[ -]?(?:2|2\.0))(?:[ -]?License)?$",
    # MIT — plain, "MIT License", "MIT-CMU" (historical Pillow variant),
    # "The MIT License (MIT)".
    r"^MIT(?:[ -](?:License|CMU))?$",
    r"^The MIT License(?: \(MIT\))?$",
    # BSD family in both orderings: "BSD-3-Clause" and "3-Clause BSD License",
    # plus 0BSD, 2-clause, 4-clause, and bare "BSD".
    r"^BSD(?:[ -]?(?:0|2|3|4)(?:[ -]?Clause)?)?(?:[ -]?License)?$",
    r"^0BSD$",
    r"^(?:0|2|3|4)[ -]?Clause[ -]?BSD(?:[ -]?License)?$",
    # ISC
    r"^ISC(?:[ -]?License(?: \(ISCL\))?)?$",
    # Python Software Foundation — accepts "Python-2.0", "PSF-2.0",
    # "Python Software Foundation License", "PSF".
    r"^Python(?:[ -]?(?:2|2\.0))?$",
    r"^Python[ -]?Software[ -]?Foundation(?:[ -]?License(?:[ -]?2\.0)?)?$",
    r"^PSF(?:[ -]?(?:License|2\.0))?$",
    # CNRI-Python (historical Python license, OSI-approved permissive)
    r"^CNRI[ -]?Python$",
    # Public-domain-equivalents
    r"^Public Domain$",
    r"^Unlicense$",
    r"^CC0(?:[ -]?1\.0)?(?:[ -]?Universal)?$",
    # Zlib / libpng
    r"^Zlib(?:[ -]?License)?$",
    r"^Zlib/libpng$",
    # Boost
    r"^Boost Software License(?:[ -]?1\.0)?$",
    r"^BSL[ -]?1\.0$",
    # MPL 2.0 — weak copyleft but Apache-2.0 compatible; commonly cleared
    # at NVIDIA. Accepts "MPL-2.0", "Mozilla Public License 2.0", and the
    # parenthetical "Mozilla Public License 2.0 (MPL 2.0)" form pip-licenses
    # emits.
    r"^Mozilla Public License(?:[ -]?2(?:\.0)?)?(?:\s*\(MPL[ -]?2(?:\.0)?\))?$",
    r"^MPL[ -]?2(?:\.0)?(?:\s*\(MPL[ -]?2(?:\.0)?\))?$",
    # LGPL — preserved from prior CI behavior (dynamic linking compatible).
    # Accepts SPDX-style ("LGPL-2.1-only", "LGPL-3.0-or-later") and the
    # historical pip-metadata strings ("GNU Library or Lesser General
    # Public License (LGPL)", "GNU Lesser General Public License v3 or later").
    r"^LGPL(?:v)?[ -]?(?:2\.0|2\.1|3\.0|3)?(?:\+|[ -]?(?:only|or[ -]later))?$",
    r"^GNU Lesser General Public License(?:[ -]?v?(?:2|2\.0|2\.1|3|3\.0))?(?:[ -]?or[ -]later)?(?:\s*\(LGPL[^)]*\))?$",
    r"^GNU Library or Lesser General Public License(?:\s*\(LGPL[^)]*\))?$",
]

COMPILED_ALLOWLIST = re.compile(
    "|".join(f"(?:{p})" for p in PERMISSIVE_LICENSE_PATTERNS),
    re.IGNORECASE,
)

# SPDX-style "AND" / OR splitting. OR means "user picks one" — only one
# alternative needs to be permissive. AND/;/, means "must satisfy all".
# CASE-SENSITIVE on purpose: lowercase "or" / "and" inside license-name
# phrases like "GNU Library or Lesser General Public License" must NOT be
# treated as the SPDX operator. Real SPDX expressions use uppercase OR/AND.
OR_SPLIT_RE = re.compile(r"\s+OR\s+")
AND_SPLIT_RE = re.compile(r"\s*(?:;|,|\sAND\s)\s*")


def normalize_clause(clause: str) -> str:
    """Strip surrounding whitespace and quote characters.

    Do NOT strip parentheses — some labels include trailing parentheticals
    that the allowlist regexes match literally (e.g. "Mozilla Public License
    2.0 (MPL 2.0)", "GNU Lesser General Public License v3 (LGPL)").
    """
    return clause.strip().strip("'\"").strip()


def shorten_license_field(license_field: str) -> str:
    """Collapse a pip-metadata `license = <full-text>` blob to its leading label.

    Older PEP 621 metadata embeds the entire license body in the `License`
    field (tiktoken, some pypi packages of the bad-old-days). Treat the
    first non-empty line as the canonical label — `"MIT License"` from a
    leading "MIT License\\n\\nCopyright (c) ..." blob.
    """
    s = license_field.strip()
    if "\n" not in s and len(s) < 200:
        return s
    for line in s.splitlines():
        candidate = line.strip()
        if candidate:
            return candidate
    return s


def load_overrides(path: Path) -> set[str]:
    """Parse one-package-name-per-line override file (canonical pip name)."""
    if not path.exists():
        return set()
    names: set[str] = set()
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            names.add(line.lower())
    return names


_SPDX_OPERATOR_RE = re.compile(r"\b(?:AND|OR)\b")


def _strip_grouping_parens(s: str) -> str:
    """Flatten SPDX grouping parens to normalize for the AND/OR splitter.

    SPDX boolean expressions like ``MPL-2.0 AND (Apache-2.0 OR MIT)`` need the
    grouping parens removed so a flat AND/OR scan can evaluate them. But many
    *label-internal* parens are not grouping — ``Mozilla Public License 2.0
    (MPL 2.0)`` and ``GNU Library or Lesser General Public License (LGPL)``
    have trailing parentheticals that the allowlist regexes match literally.

    Heuristic: only flatten parens when the string actually looks like an SPDX
    boolean expression — i.e. it contains *both* a paren and an uppercase
    ``AND``/``OR`` operator. Otherwise leave the string untouched.
    """
    if "(" not in s or not _SPDX_OPERATOR_RE.search(s):
        return s
    return s.replace("(", " ").replace(")", " ")


def license_passes(license_field: str) -> bool:
    """Whether the license string is acceptable under the permissive allowlist.

    Boolean evaluation: "A OR B" passes iff *any* alternative passes; each
    alternative may itself be "X AND Y", in which case all of X, Y must pass.
    Grouping parens ("(A OR B) AND C") are stripped before splitting so the
    naive AND/OR scanner can still evaluate the expression correctly under
    SPDX's "AND binds tighter than OR" precedence.
    """
    if not license_field or license_field.strip().upper() in ("UNKNOWN", "NONE"):
        return False
    shortened = shorten_license_field(license_field)
    shortened = _strip_grouping_parens(shortened)
    or_alternatives = OR_SPLIT_RE.split(shortened)
    for alt in or_alternatives:
        and_clauses = [normalize_clause(c) for c in AND_SPLIT_RE.split(alt) if c.strip()]
        if not and_clauses:
            continue
        if all(COMPILED_ALLOWLIST.search(c) is not None for c in and_clauses):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overrides",
        type=Path,
        required=True,
        help="Path to the package-name override file.",
    )
    args = parser.parse_args()

    overrides = load_overrides(args.overrides)
    reader = csv.reader(sys.stdin)
    header = next(reader, None)
    if header is None:
        print("ERROR: pip-licenses CSV had no header.", file=sys.stderr)
        return 1
    # Expect at least: Name, Version, License
    try:
        name_idx = header.index("Name")
        version_idx = header.index("Version")
        license_idx = header.index("License")
    except ValueError as exc:
        print(f"ERROR: unexpected pip-licenses CSV header: {header} ({exc})", file=sys.stderr)
        return 1

    violations: list[tuple[str, str, str]] = []
    overridden: list[tuple[str, str, str]] = []
    total = 0

    for row in reader:
        if len(row) <= max(name_idx, version_idx, license_idx):
            continue
        name = row[name_idx].strip()
        version = row[version_idx].strip()
        license_field = row[license_idx].strip()
        total += 1
        if license_passes(license_field):
            continue
        if name.lower() in overrides:
            overridden.append((name, version, license_field))
            continue
        violations.append((name, version, license_field))

    if overridden:
        print(f"INFO: {len(overridden)} package(s) cleared via OSRB override:")
        for name, ver, lic in sorted(overridden):
            print(f"  {name} {ver}  ->  {lic!r}")
        print()

    if violations:
        print(f"ERROR: {len(violations)} package(s) with non-permissive license metadata:")
        for name, ver, lic in sorted(violations):
            print(f"  {name} {ver}  ->  {lic!r}")
        print()
        print("To resolve each: either")
        print("  (a) replace the dep with a permissive-licensed alternative,")
        print("  (b) get OSRB sign-off and add the package to")
        print(f"      .github/scripts/license_allowlist_overrides.txt, or")
        print("  (c) if the package's pip metadata is wrong, file the upstream")
        print("      bug, OSRB-clear, and add to the override file with a note.")
        return 1

    print(f"OK: all {total} runtime Python dep(s) carry a permissive license.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
