#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render the OSRB-review overview from the complete License Diff CSV.

The input remains the audit record.  The overview contains only changes that
match OSRB's re-engagement policy: new dependencies, license changes, and
version updates whose old/new license could not be resolved.  Same-license
version updates and removals stay in the raw CSV but are omitted from the
review table.
"""

from __future__ import annotations

import argparse
import csv
import re
from typing import TextIO

UNKNOWN_LICENSES = {
    "",
    "unknown",
    "unknown license",
    "n/a",
    "none",
    "not found",
    "see license in",
}

LICENSE_ALIASES = {
    "apache 2": "apache-2.0",
    "apache 2.0": "apache-2.0",
    "apache software license": "apache-2.0",
    "apache license 2.0": "apache-2.0",
    "mit license": "mit",
    "isc license": "isc",
    "mozilla public license 2.0": "mpl-2.0",
    "python software foundation license": "psf-2.0",
}


def normalize_license(value: str) -> str:
    """Normalize common label variants without guessing ambiguous licenses."""
    normalized = re.sub(r"\s+", " ", value.strip().lower())
    return LICENSE_ALIASES.get(normalized, normalized)


def license_is_unknown(value: str) -> bool:
    normalized = normalize_license(value)
    return normalized in UNKNOWN_LICENSES or normalized.startswith("unknown")


def review_category(row: dict[str, str]) -> str | None:
    """Return the overview category for one raw row, if review is needed."""
    change = row.get("change", "").strip().lower()
    if change == "added":
        return "added"
    if change != "updated":
        return None

    old_license = row.get("old_license", "")
    new_license = row.get("new_license", "")
    if license_is_unknown(old_license) or license_is_unknown(new_license):
        return "unresolved"
    if normalize_license(old_license) != normalize_license(new_license):
        return "license_changed"
    return None


def classify_rows(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {
        "added": [],
        "license_changed": [],
        "unresolved": [],
        "version_only": [],
        "removed": [],
    }
    for row in rows:
        category = review_category(row)
        if category:
            grouped[category].append(row)
        elif row.get("change", "").strip().lower() == "removed":
            grouped["removed"].append(row)
        elif row.get("change", "").strip().lower() == "updated":
            grouped["version_only"].append(row)
    return grouped


def markdown_cell(value: str) -> str:
    value = value.strip()
    return value.replace("|", "\\|") if value else "—"


def _write_table(
    output: TextIO,
    rows: list[dict[str, str]],
    *,
    license_change: bool = False,
) -> None:
    if license_change:
        print("| Component | Version | License change | Public component or license URL |", file=output)
        print("|---|---|---|---|", file=output)
    else:
        print("| Component | Version | Assessed license | Public component or license URL |", file=output)
        print("|---|---|---|---|", file=output)

    for row in rows:
        version = row.get("new_version", "")
        if row.get("old_version") and row.get("new_version"):
            version = f"{row['old_version']} → {row['new_version']}"
        license_value = row.get("new_license", "")
        if license_change:
            license_value = f"{row.get('old_license', '')} → {row.get('new_license', '')}"
        values = [
            row.get("package", ""),
            version,
            license_value,
            row.get("repository_url", ""),
        ]
        print("| " + " | ".join(markdown_cell(value) for value in values) + " |", file=output)


def render_summary(rows: list[dict[str, str]], output: TextIO) -> dict[str, int]:
    grouped = classify_rows(rows)
    review_rows = (
        len(grouped["added"])
        + len(grouped["license_changed"])
        + len(grouped["unresolved"])
    )
    counts = {
        "raw_rows": len(rows),
        "review_rows": review_rows,
        "added_rows": len(grouped["added"]),
        "license_changed_rows": len(grouped["license_changed"]),
        "unresolved_rows": len(grouped["unresolved"]),
        "version_only_rows": len(grouped["version_only"]),
        "removed_rows": len(grouped["removed"]),
    }

    print("# OSRB license review overview", file=output)
    print(file=output)
    if review_rows:
        print(f"**{review_rows} change(s) require OSRB attention.**", file=output)
    else:
        print("**No changes require OSRB re-engagement.**", file=output)
    print(file=output)
    print(
        f"The complete CSV contains {len(rows)} change(s): "
        f"{counts['version_only_rows']} same-license version update(s) and "
        f"{counts['removed_rows']} removal(s) are retained there but omitted below.",
        file=output,
    )

    sections = [
        ("New dependencies", grouped["added"], False),
        ("License changes", grouped["license_changed"], True),
        ("Licenses requiring investigation", grouped["unresolved"], True),
    ]
    for title, section_rows, license_change in sections:
        if not section_rows:
            continue
        print(file=output)
        print(f"## {title} ({len(section_rows)})", file=output)
        print(file=output)
        _write_table(output, section_rows, license_change=license_change)

    return counts


def write_github_output(path: str, counts: dict[str, int]) -> None:
    with open(path, "a", encoding="utf-8") as output:
        for name, value in counts.items():
            print(f"{name}={value}", file=output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="license-diff.csv")
    parser.add_argument("--output", default="osrb-summary.md")
    parser.add_argument("--github-output", help="Optional path supplied by GitHub Actions.")
    args = parser.parse_args()

    with open(args.input, newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    with open(args.output, "w", encoding="utf-8") as output:
        counts = render_summary(rows, output)
    if args.github_output:
        write_github_output(args.github_output, counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
