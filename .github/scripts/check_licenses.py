#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""License compliance check for video-search-and-summarization.

The script consumes a JSON dependency report (produced by `pip-licenses`
for Python or `license-checker-rseidelsohn` for npm) and verifies each
dependency against:

  * `.github/license-database/policy.yaml`
      Lists of allowed / denied / review-required SPDX patterns and
      packages to ignore.

  * `.github/license-database/permissive-licenses.csv`
      Approved packages with permissive licenses.

  * `.github/license-database/non-permissive-licenses.csv`
      Packages with non-permissive licenses (LGPL, MPL with caveats, etc.)
      that have been explicitly approved with OSRB justification.

  * `.github/license-database/license-overrides.csv`
      Manual license overrides for packages whose detected license is
      misleading.

Outcome per package is one of:

  * APPROVED       - package found in permissive-licenses.csv with a
                     matching allowed license.
  * APPROVED-OSRB  - package found in non-permissive-licenses.csv (OSRB
                     review on file) with the same review-required
                     license.
  * DENIED         - license matches a `denied` pattern, or a
                     review-required license without an OSRB entry.
  * NEW            - package is not in any CSV. Requires triage.
  * UNKNOWN        - license could not be parsed.

In `--strict` mode (default) the job exits 1 if any package is DENIED,
NEW or UNKNOWN.

Usage:
    check_licenses.py --ecosystem python --input pip-licenses.json
    check_licenses.py --ecosystem npm    --input license-checker.json
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from dataclasses import field
import fnmatch
import json
from pathlib import Path
import re
import sys

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_DIR = REPO_ROOT / ".github" / "license-database"

POLICY_FILE = DB_DIR / "policy.yaml"
PERMISSIVE_CSV = DB_DIR / "permissive-licenses.csv"
NON_PERMISSIVE_CSV = DB_DIR / "non-permissive-licenses.csv"
OVERRIDES_CSV = DB_DIR / "license-overrides.csv"

# Status constants
ST_APPROVED = "APPROVED"
ST_APPROVED_OSRB = "APPROVED-OSRB"
ST_DENIED = "DENIED"
ST_NEW = "NEW"
ST_UNKNOWN = "UNKNOWN"

# Order of importance for the summary table
STATUS_PRIORITY = [ST_DENIED, ST_UNKNOWN, ST_NEW, ST_APPROVED_OSRB, ST_APPROVED]


def _normalize_name(name: str, ecosystem: str) -> str:
    """Lower-case the name and (for Python) collapse `_`/`-`/`.` per PEP 503."""
    n = name.strip().lower()
    if ecosystem == "python":
        n = re.sub(r"[-_.]+", "-", n)
    return n


@dataclass
class Package:
    name: str
    version: str
    license: str
    ecosystem: str
    source_url: str = ""
    status: str = ""
    reason: str = ""

    @property
    def normalized_name(self) -> str:
        return _normalize_name(self.name, self.ecosystem)

    @property
    def key(self) -> str:
        return f"{self.normalized_name}::{self.version}"


@dataclass
class Policy:
    allowed: list[str]
    denied: list[str]
    review_required: list[str]
    ignored: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Policy:
        with path.open() as f:
            data = yaml.safe_load(f) or {}
        return cls(
            allowed=[str(p) for p in data.get("allowed", [])],
            denied=[str(p) for p in data.get("denied", [])],
            review_required=[str(p) for p in data.get("review_required", [])],
            ignored=data.get("ignored_packages", {}) or {},
        )

    def is_ignored(self, ecosystem: str, name: str) -> bool:
        patterns = self.ignored.get(ecosystem, []) or []
        lname = _normalize_name(name, ecosystem)
        return any(fnmatch.fnmatchcase(lname, _normalize_name(pat, ecosystem)) for pat in patterns)

    @staticmethod
    def _matches_any(text: str, patterns: list[str]) -> bool:
        if not text:
            return False
        # Word-boundary match: the pattern must be surrounded by
        # non-alphanumeric characters in the detected license string.
        # This prevents `GPL` from matching inside `LGPL`, `AGPLv3`
        # from matching inside something else, etc.
        text_l = text.lower()
        for pat in patterns:
            p = pat.lower()
            # `re.escape` keeps regex special chars (e.g. `+`, `.`) literal.
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(p)}(?![A-Za-z0-9])", text_l):
                return True
        return False

    def classify_license(self, license_str: str) -> str:
        """Return 'denied', 'review_required', 'allowed' or 'unknown'."""
        if self._matches_any(license_str, self.denied):
            return "denied"
        if self._matches_any(license_str, self.review_required):
            return "review_required"
        if self._matches_any(license_str, self.allowed):
            return "allowed"
        return "unknown"


@dataclass
class Database:
    """Approved packages, indexed by `name::version` and `name`."""

    permissive: dict[str, dict[str, str]]
    non_permissive: dict[str, dict[str, str]]
    overrides: dict[str, dict[str, str]]

    @classmethod
    def load(cls) -> Database:
        return cls(
            permissive=_load_csv(PERMISSIVE_CSV, key_fields=("Component Name", "Version")),
            non_permissive=_load_csv(NON_PERMISSIVE_CSV, key_fields=("Component Name", "Version")),
            overrides=_load_csv(OVERRIDES_CSV, key_fields=("Component Name",)),
        )

    def lookup(self, pkg: Package) -> tuple[str | None, dict[str, str] | None]:
        """Return ('permissive'|'non_permissive', row) or (None, None)."""
        keys = (f"{pkg.normalized_name}::{pkg.version}", pkg.normalized_name)
        for key in keys:
            if key in self.permissive:
                return "permissive", self.permissive[key]
            if key in self.non_permissive:
                return "non_permissive", self.non_permissive[key]
        return None, None

    def override(self, pkg: Package) -> dict[str, str] | None:
        return self.overrides.get(pkg.normalized_name)


def _load_csv(path: Path, key_fields: tuple[str, ...]) -> dict[str, dict[str, str]]:
    """Load a CSV into a dict keyed on `name::version` (and `name` as fallback).

    Names are normalized via PEP 503 rules for the python ecosystem so that
    `numpy`, `Numpy`, and `nu_mpy` all hash to the same key.
    """
    out: dict[str, dict[str, str]] = {}
    if not path.exists():
        return out
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_name = (row.get("Component Name") or "").strip()
            if not raw_name:
                continue
            ecosystem = (row.get("Ecosystem") or "python").strip().lower()
            name = _normalize_name(raw_name, ecosystem)
            row = {k: (v or "").strip() for k, v in row.items()}
            if len(key_fields) == 2:
                version = row.get("Version", "")
                out[f"{name}::{version}"] = row
                out.setdefault(name, row)
            else:
                out[name] = row
    return out


# ---------------------------------------------------------------------------
# Input parsers
# ---------------------------------------------------------------------------


def parse_pip_licenses(path: Path) -> list[Package]:
    """Parse `pip-licenses --format=json` output."""
    data = json.loads(path.read_text())
    pkgs = []
    for item in data:
        name = item.get("Name") or item.get("name") or ""
        version = item.get("Version") or item.get("version") or ""
        license_str = item.get("License") or item.get("license") or ""
        url = item.get("URL") or item.get("HomePage") or ""
        if not name:
            continue
        pkgs.append(
            Package(
                name=name,
                version=version,
                license=_normalize_license(license_str),
                ecosystem="python",
                source_url=url,
            )
        )
    return pkgs


def parse_license_checker(path: Path) -> list[Package]:
    """Parse `license-checker-rseidelsohn --json` output.

    The JSON is keyed on `name@version` with values containing `licenses`,
    `repository`, `licenseFile`, etc.
    """
    data = json.loads(path.read_text())
    pkgs = []
    pat = re.compile(r"^(?P<name>(?:@[^/]+/)?[^@]+)@(?P<version>.+)$")
    for key, value in data.items():
        m = pat.match(key)
        if not m:
            continue
        name = m.group("name")
        version = m.group("version")
        licenses = value.get("licenses", "")
        if isinstance(licenses, list):
            license_str = " AND ".join(licenses)
        else:
            license_str = licenses or ""
        pkgs.append(
            Package(
                name=name,
                version=version,
                license=_normalize_license(license_str),
                ecosystem="npm",
                source_url=value.get("repository", "") or value.get("url", ""),
            )
        )
    return pkgs


def _normalize_license(s: str) -> str:
    """Strip SPDX expression noise and collapse whitespace."""
    if not s:
        return ""
    # Strip trailing parens like "(MIT)" and collapse whitespace.
    s = s.replace("(", " ").replace(")", " ")
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify(packages: list[Package], policy: Policy, db: Database) -> list[Package]:
    out = []
    for pkg in packages:
        if policy.is_ignored(pkg.ecosystem, pkg.name):
            # Drop ignored packages from the report entirely.
            continue

        # Apply manual override first, by name.
        override = db.override(pkg)
        if override and override.get("License"):
            pkg.license = override["License"]

        license_class = policy.classify_license(pkg.license)
        bucket, row = db.lookup(pkg)

        if license_class == "denied":
            pkg.status = ST_DENIED
            pkg.reason = f"license '{pkg.license or 'UNKNOWN'}' is on the denied list"
        elif license_class == "review_required":
            if bucket == "non_permissive":
                pkg.status = ST_APPROVED_OSRB
                pkg.reason = f"OSRB-approved (non-permissive license '{pkg.license}')"
            else:
                pkg.status = ST_DENIED
                pkg.reason = (
                    f"license '{pkg.license}' requires OSRB review; "
                    "add an entry to non-permissive-licenses.csv with justification"
                )
        elif license_class == "allowed":
            if bucket is None:
                pkg.status = ST_NEW
                pkg.reason = (
                    f"new package with permissive license '{pkg.license}'; add to permissive-licenses.csv after review"
                )
            else:
                pkg.status = ST_APPROVED if bucket == "permissive" else ST_APPROVED_OSRB
                pkg.reason = f"approved ({pkg.license})"
        else:  # unknown / unparseable license
            if bucket is not None:
                # The bucket itself is the approval signal: anything in
                # `permissive-licenses.csv` has been reviewed and accepted
                # for permissive use, and `non-permissive-licenses.csv`
                # entries already have OSRB sign-off. Trust it even if
                # the recorded license string is just copyright text.
                row_license = (row or {}).get("License", "") if row else ""
                if bucket == "permissive":
                    pkg.status = ST_APPROVED
                    pkg.reason = f"approved via database ({row_license or 'see CSV'})"
                else:
                    pkg.status = ST_APPROVED_OSRB
                    pkg.reason = f"OSRB-approved via database ({row_license or 'see CSV'})"
            else:
                pkg.status = ST_UNKNOWN
                pkg.reason = (
                    f"could not classify license '{pkg.license or 'UNKNOWN'}'; "
                    "add a license-overrides.csv entry or update the package metadata"
                )

        out.append(pkg)
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def write_report(packages: list[Package], output_dir: Path, ecosystem: str) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = dict.fromkeys(STATUS_PRIORITY, 0)
    for pkg in packages:
        counts[pkg.status] = counts.get(pkg.status, 0) + 1

    # Per-status CSVs (only write non-empty ones).
    for status in (ST_DENIED, ST_NEW, ST_UNKNOWN, ST_APPROVED_OSRB):
        rows = [p for p in packages if p.status == status]
        if not rows:
            continue
        path = output_dir / f"{ecosystem}-{status.lower().replace('_', '-')}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            # Force LF line endings (csv.writer defaults to CRLF per RFC).
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["Component Name", "Version", "License", "Source URL", "Reason"])
            for p in sorted(rows, key=lambda x: x.name.lower()):
                w.writerow([p.name, p.version, p.license, p.source_url, p.reason])

    # Full report
    full = output_dir / f"{ecosystem}-license-report.csv"
    with full.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["Component Name", "Version", "License", "Status", "Reason"])
        for p in sorted(packages, key=lambda x: (STATUS_PRIORITY.index(x.status), x.name.lower())):
            w.writerow([p.name, p.version, p.license, p.status, p.reason])

    return counts


def print_summary(ecosystem: str, packages: list[Package], counts: dict[str, int]) -> None:
    bar = "=" * 78
    print(bar)
    print(f"License check summary ({ecosystem})")
    print(bar)
    total = len(packages)
    for status in STATUS_PRIORITY:
        n = counts.get(status, 0)
        print(f"  {status:<15} {n:>4}")
    print(f"  {'TOTAL':<15} {total:>4}")
    print(bar)

    failing = [p for p in packages if p.status in (ST_DENIED, ST_UNKNOWN, ST_NEW)]
    if not failing:
        return
    print()
    print("Issues requiring action:")
    print()
    for p in failing:
        print(f"  [{p.status}] {p.name} {p.version} ({p.license or 'UNKNOWN'})")
        print(f"      -> {p.reason}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ecosystem", required=True, choices=["python", "npm"])
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="JSON dependency report (pip-licenses --format=json or license-checker --json)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("license-check-output"),
        help="Where to write per-status CSV reports.",
    )
    parser.add_argument(
        "--strict",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Exit non-zero on DENIED, NEW, or UNKNOWN packages (default: enabled).",
    )

    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"ERROR: input file not found: {args.input}", file=sys.stderr)
        return 2

    policy = Policy.load(POLICY_FILE)
    db = Database.load()

    if args.ecosystem == "python":
        packages = parse_pip_licenses(args.input)
    else:
        packages = parse_license_checker(args.input)

    classified = classify(packages, policy, db)
    counts = write_report(classified, args.output_dir, args.ecosystem)
    print_summary(args.ecosystem, classified, counts)

    failing = counts.get(ST_DENIED, 0) + counts.get(ST_UNKNOWN, 0) + counts.get(ST_NEW, 0)
    if failing and args.strict:
        print(
            f"FAIL: {failing} package(s) require attention. See {args.output_dir}/{args.ecosystem}-*.csv",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {len(classified)} package(s) classified, no blocking issues.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
