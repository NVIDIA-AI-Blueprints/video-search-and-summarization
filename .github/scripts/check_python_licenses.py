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
    # LGPL — restricted to V2.1 per Bernd's OSRB regime list
    # (https://gitlab-master.nvidia.com/bweber/osrb-skills .cursor reference.md).
    # LGPL-3.0 / LGPL-3 is *not* on Bernd's list; the few LGPL-3.0-or-later
    # deps (svglib, python-bidi) carry their own line in
    # license_allowlist_overrides.txt pending explicit OSRB confirmation.
    r"^LGPL[ -]?(?:v)?2\.1(?:\+|[ -]?(?:only|or[ -]later))?$",
    r"^GNU Lesser General Public License(?:[ -]?v?2\.1)?(?:[ -]?or[ -]later)?(?:\s*\(LGPL[^)]*\))?$",
]

COMPILED_ALLOWLIST = re.compile(
    "|".join(f"(?:{p})" for p in PERMISSIVE_LICENSE_PATTERNS),
    re.IGNORECASE,
)

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


# Placeholder chars used to hide label-internal parens from the SPDX tokenizer.
# Trailing label-clarifying parens like "Mozilla Public License 2.0 (MPL 2.0)"
# must stay attached to the label; only grouping parens (those containing an
# SPDX operator at their level) participate in the expression grammar.
_LABEL_LPAREN = "\x01"
_LABEL_RPAREN = "\x02"
_SPDX_OPERATOR_INSIDE_RE = re.compile(r"\b(?:AND|OR|WITH)\b")
_OP_BOUNDARY_RE = re.compile(r"\s(AND|OR|WITH)(?=\s|$|\))")


def _mask_label_internal_parens(s: str) -> str:
    """Replace label-internal `(...)` with placeholder chars.

    A paren group is "grouping" (SPDX operator) iff its top-level contents
    contain an `AND`/`OR`/`WITH` token. Otherwise it's "label-internal" — e.g.
    the trailing "(MPL 2.0)" in "Mozilla Public License 2.0 (MPL 2.0)" — and
    we hide its parens with placeholder chars so the SPDX tokenizer treats
    them as part of the label.
    """
    out: list[str] = []
    i = 0
    while i < len(s):
        if s[i] != "(":
            out.append(s[i])
            i += 1
            continue
        depth = 1
        j = i + 1
        while j < len(s) and depth > 0:
            if s[j] == "(":
                depth += 1
            elif s[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            # unbalanced — leave alone, treat as plain char
            out.append(s[i])
            i += 1
            continue
        inner = s[i + 1 : j]
        if _SPDX_OPERATOR_INSIDE_RE.search(f" {inner} "):
            # grouping paren — keep as-is
            out.append(s[i : j + 1])
        else:
            # label-internal — mask
            out.append(_LABEL_LPAREN + inner + _LABEL_RPAREN)
        i = j + 1
    return "".join(out)


def _restore_label_parens(label: str) -> str:
    return label.replace(_LABEL_LPAREN, "(").replace(_LABEL_RPAREN, ")")


def _label_matches_allowlist(label: str) -> bool:
    """fullmatch the (paren-restored) label against the permissive allowlist."""
    normalized = _restore_label_parens(label.strip().strip("'\"").strip())
    return COMPILED_ALLOWLIST.fullmatch(normalized) is not None


class _SpdxParser:
    """Recursive descent SPDX expression evaluator.

    Grammar (with normal AND-binds-tighter-than-OR precedence):

        expr   ::= or
        or     ::= and ( 'OR'   and )*
        and    ::= atom ( 'AND' atom )*
        atom   ::= '(' or ')' | label ( 'WITH' label )?
        label  ::= one or more whitespace-separated tokens that are
                   not 'AND' / 'OR' / 'WITH' / '(' / ')'

    Fixes the false-positive Greptile flagged on ``(A OR B) AND C`` (which the
    old flat splitter evaluated as ``A OR (B AND C)``) and properly handles
    the SPDX ``WITH`` exception operator.
    """

    _OPS_AND_PARENS = {"AND", "OR", "WITH", "(", ")"}

    def __init__(self, expr: str) -> None:
        self.s = expr
        self.pos = 0
        self.n = len(expr)

    # ---------- helpers ----------

    def _skip_ws(self) -> None:
        while self.pos < self.n and self.s[self.pos].isspace():
            self.pos += 1

    def _peek(self) -> str | None:
        """Return the next token TYPE without consuming.

        Returns one of ``"AND"``, ``"OR"``, ``"WITH"``, ``"("``, ``")"``, or
        ``None`` (meaning "next thing is a label fragment").
        """
        self._skip_ws()
        if self.pos >= self.n:
            return None
        ch = self.s[self.pos]
        if ch in "()":
            return ch
        # match a word-boundary operator
        m = re.match(r"(AND|OR|WITH)(?=\s|$|\))", self.s[self.pos :])
        if m:
            return m.group(1)
        return None

    def _consume(self, token: str) -> None:
        self._skip_ws()
        self.pos += len(token)

    # ---------- grammar ----------

    def evaluate(self) -> bool:
        return self._or()

    def _or(self) -> bool:
        result = self._and()
        while self._peek() == "OR":
            self._consume("OR")
            right = self._and()
            result = result or right
        return result

    def _and(self) -> bool:
        result = self._atom()
        while self._peek() == "AND":
            self._consume("AND")
            right = self._atom()
            result = result and right
        return result

    def _atom(self) -> bool:
        self._skip_ws()
        if self._peek() == "(":
            self._consume("(")
            result = self._or()
            if self._peek() == ")":
                self._consume(")")
            return result
        label = self._read_label()
        # SPDX `WITH <exception>` — evaluate the base license only (the
        # exception itself never makes a non-permissive license permissive).
        if self._peek() == "WITH":
            self._consume("WITH")
            self._read_label()  # consume + discard the exception name
        return _label_matches_allowlist(label)

    def _read_label(self) -> str:
        self._skip_ws()
        start = self.pos
        while self.pos < self.n:
            ch = self.s[self.pos]
            if ch in "()":
                break
            m = re.match(r"(AND|OR|WITH)(?=\s|$|\))", self.s[self.pos :])
            if m and (self.pos == 0 or self.s[self.pos - 1].isspace()):
                break
            self.pos += 1
        return self.s[start : self.pos].strip()


def license_passes(license_field: str) -> bool:
    """Whether the license string is acceptable under the permissive allowlist.

    Handles SPDX boolean expressions with full precedence: ``AND`` binds
    tighter than ``OR``; explicit parens ``(A OR B) AND C`` mean exactly
    that. ``WITH <exception>`` is consumed; only the base license name
    determines permissive-ness (an exception cannot rescue a non-permissive
    license under our allowlist policy).
    """
    if not license_field or license_field.strip().upper() in ("UNKNOWN", "NONE"):
        return False
    shortened = shorten_license_field(license_field)
    # Comma/semicolon are common separators in old pip metadata and not part
    # of SPDX 2+ syntax — treat them as AND so e.g. "MIT; BSD-3-Clause" parses
    # the same as "MIT AND BSD-3-Clause".
    normalized = re.sub(r"\s*[;,]\s*", " AND ", shortened)
    masked = _mask_label_internal_parens(normalized)
    return _SpdxParser(masked).evaluate()


def load_denylist(path: Path) -> set[str]:
    """Parse the package-name denylist (one canonical pip name per line)."""
    return load_overrides(path)  # same `#`-comment + per-line format


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overrides",
        type=Path,
        required=True,
        help="Path to the package-name override file (OSRB-cleared exceptions).",
    )
    parser.add_argument(
        "--denylist",
        type=Path,
        required=True,
        help=(
            "Path to the package-name denylist (always fails, regardless of "
            "license metadata — for packages whose declared license is known "
            "to misrepresent the wheel's actual terms)."
        ),
    )
    args = parser.parse_args()

    overrides = load_overrides(args.overrides)
    denylist = load_denylist(args.denylist)
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

    denied: list[tuple[str, str, str]] = []
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
        # 1. Hard denylist wins over everything (override cannot rescue).
        if name.lower() in denylist:
            denied.append((name, version, license_field))
            continue
        # 2. Permissive allowlist by license string.
        if license_passes(license_field):
            continue
        # 3. OSRB-cleared override by package name.
        if name.lower() in overrides:
            overridden.append((name, version, license_field))
            continue
        violations.append((name, version, license_field))

    if overridden:
        print(f"INFO: {len(overridden)} package(s) cleared via OSRB override:")
        for name, ver, lic in sorted(overridden):
            print(f"  {name} {ver}  ->  {lic!r}")
        print()

    failed = False
    if denied:
        failed = True
        print(f"ERROR: {len(denied)} package(s) on the explicit denylist:")
        for name, ver, lic in sorted(denied):
            print(f"  {name} {ver}  ->  declared {lic!r}")
        print()
        print("These packages are denied regardless of declared license — their")
        print("pip metadata misrepresents the wheel's actual terms (e.g. an")
        print("Apache-2.0 declaration alongside an ELv2 IP_NOTICE in the bundle).")
        print("To resolve: replace each with a permissive-licensed alternative.")
        print()

    if violations:
        failed = True
        print(f"ERROR: {len(violations)} package(s) with non-permissive license metadata:")
        for name, ver, lic in sorted(violations):
            print(f"  {name} {ver}  ->  {lic!r}")
        print()
        print("To resolve each: either")
        print("  (a) replace the dep with a permissive-licensed alternative,")
        print("  (b) get OSRB sign-off and add the package to")
        print("      .github/scripts/license_allowlist_overrides.txt, or")
        print("  (c) if the package's pip metadata is wrong, file the upstream")
        print("      bug, OSRB-clear, and add to the override file with a note.")
        print()

    if failed:
        return 1
    print(f"OK: all {total} runtime Python dep(s) carry a permissive license.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
