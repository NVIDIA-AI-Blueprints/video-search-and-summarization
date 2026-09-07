#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render the OSRB-review overview from the complete OSRB Scan CSV.

The input remains the audit record.  The overview shows only what a reader has
to act on, split into three classes that carry different consequences:

* **Review rows** — new dependencies, license changes, and version updates
  whose old/new license could not be resolved.  These are the OSRB
  re-engagement policy and they are what ``review_rows`` counts.  Same-license
  version updates and removals stay in the raw CSV but are omitted here.
* **``UNCOVERED_SOURCE``** — a file that carries third-party dependencies which
  the scanner cannot parse.  Nothing in it was inventoried, so the rest of this
  report is *incomplete* in a way no reader can see; that is why it blocks.
  It is a scanner defect, not an OSRB decision, and is counted separately in
  ``uncovered_rows`` so the workflow can say so.
* **``USED_UNDECLARED``** — imported in source but declared in no manifest.
  Report-only by owner decision: the use-side pass infers rather than reads a
  declaration, so a false positive here must never be able to block a PR.

``review_rows`` deliberately keeps the exact meaning it had before the two new
classes existed.  Branch protection and the private OSRB pipeline both key off
that number; folding a new class into it would change what "requires OSRB
attention" means for every PR at once, without anyone deciding to.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import TextIO

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

try:  # the repo's own permissive allowlist, same one the green-gate uses
    from check_python_licenses import COMPILED_ALLOWLIST as _PERMISSIVE_RE
except ImportError:  # pragma: no cover
    _PERMISSIVE_RE = None


def _is_permissive_expr(expr: str) -> bool:
    """True when EVERY operand of a licence expression is permissive.

    A single label is checked directly. A composite like
    ``Apache-2.0 AND BSD-3-Clause AND MIT`` — which is what several PyPI wheels
    (torch, numpy) report — is permissive iff each operand is, so it is split on
    AND/OR/WITH and every part must match the allowlist. UNKNOWN/empty is never
    permissive: absence of a licence is exactly what a reader must still see.
    """
    if _PERMISSIVE_RE is None:
        return False
    text = (expr or "").strip().strip('"')
    if not text or text.upper() in {"UNKNOWN", "NOASSERTION", "SEE-LICENSE-TEXT"}:
        return False
    # Strip `WITH <exception>` first: it modifies a licence (Apache-2.0 WITH
    # LLVM-exception is still Apache-2.0), it is not a separate operand. Then
    # split on the real operators.
    text = re.sub(r"\s+WITH\s+[\w.-]+", "", text, flags=re.IGNORECASE)
    parts = [p.strip(" ()") for p in re.split(r"\b(?:AND|OR)\b", text) if p.strip(" ()")]
    return bool(parts) and all(_PERMISSIVE_RE.match(p) for p in parts)


def _load_inventory_licences(path: str | None) -> dict[str, str]:
    """canonical package name -> resolved licence, from the committed inventory.

    The use-side rows carry no licence themselves (a lockfile-free import has
    nothing to read one from), so the permissive gate needs the inventory,
    which already resolved each package. Missing file -> no gating, and the
    section renders in full as before rather than silently hiding rows.
    """
    if not path:
        return {}
    licences: dict[str, str] = {}
    try:
        with open(path, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                lic = row.get("license", "")
                if lic and lic != "UNKNOWN":
                    licences.setdefault(_canon(row.get("package", "")), lic)
    except OSError:
        return {}
    return licences


def _canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", (name or "").strip().lower()).lstrip("@")

UNKNOWN_LICENSES = {
    "",
    "unknown",
    "unknown license",
    "n/a",
    "none",
    "not found",
    "see license in",
}

# Change values from osrb_scan.py that are not package deltas. Compared
# case-insensitively because the CSV is written by a different module and a
# casing change there must not quietly reclassify a blocking row as noise.
CHANGE_UNCOVERED_SOURCE = "uncovered_source"
SOURCE_KIND_ATTRIBUTION = "attribution"
CHANGE_USED_UNDECLARED = "used_undeclared"

LICENSE_ALIASES = {
    "apache 2": "apache-2.0",
    "apache 2.0": "apache-2.0",
    "apache software license": "apache-2.0",
    "apache software": "apache-2.0",
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
    """Return the overview category for one raw row, if OSRB review is needed.

    Only ``added`` and ``updated`` package deltas can answer yes.  The two
    non-delta change values fall out through the ``!= "updated"`` guard below
    and must keep doing so: ``UNCOVERED_SOURCE`` blocks for its own reason and
    ``USED_UNDECLARED`` never blocks, and neither is an OSRB approval request.
    """
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
        "uncovered": [],
        "used_undeclared": [],
    }
    for row in rows:
        change = row.get("change", "").strip().lower()
        if change == CHANGE_UNCOVERED_SOURCE:
            grouped["uncovered"].append(row)
            continue
        if change == CHANGE_USED_UNDECLARED:
            grouped["used_undeclared"].append(row)
            continue
        category = review_category(row)
        if category:
            grouped[category].append(row)
        elif change == "removed":
            grouped["removed"].append(row)
        elif change == "updated":
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


def code_cell(value: str | None) -> str:
    """Render a path/identifier cell, leaving an empty one as a plain dash.

    Backticks around an em dash would read as a file literally named "—",
    which is exactly the kind of ghost a reader wastes time looking for.
    ``None`` is accepted because ``csv.DictReader`` fills a short row with it,
    and losing the whole overview to an AttributeError over one ragged line is
    a far worse outcome than one dash.
    """
    value = (value or "").strip()
    return f"`{value}`".replace("|", "\\|") if value else "—"


def evidence_of(row: dict[str, str]) -> str:
    """Where the row came from, tolerating a CSV written before the columns existed.

    ``source_file`` is appended after ``notes`` by osrb_scan.py.  A CSV
    produced by an older scanner (a rerun of an in-flight PR, an artifact
    replayed from history) simply lacks the column; falling back to an empty
    cell keeps the overview renderable instead of raising KeyError inside CI
    and losing the whole report.
    """
    return row.get("source_file") or ""


def _write_uncovered_table(output: TextIO, rows: list[dict[str, str]]) -> None:
    print("| File | Detected kind | Module | Notes |", file=output)
    print("|---|---|---|---|", file=output)
    for row in rows:
        path = evidence_of(row) or row.get("package") or ""
        print(
            "| "
            + " | ".join(
                [
                    code_cell(path),
                    markdown_cell(row.get("source_kind") or ""),
                    markdown_cell(row.get("module") or ""),
                    markdown_cell(row.get("notes") or ""),
                ]
            )
            + " |",
            file=output,
        )


def _write_usage_table(
    output: TextIO,
    rows: list[dict[str, str]],
    inventory_licences: dict[str, str] | None = None,
) -> None:
    inventory_licences = inventory_licences or {}
    print("| Imported name | Language | Licence | Module | Evidence |", file=output)
    print("|---|---|---|---|---|", file=output)
    for row in rows:
        licence = row.get("new_license") or inventory_licences.get(
            _canon(row.get("package") or ""), ""
        )
        print(
            "| "
            + " | ".join(
                [
                    markdown_cell(row.get("package") or ""),
                    markdown_cell(row.get("language") or ""),
                    markdown_cell(licence or "unknown"),
                    markdown_cell(row.get("module") or ""),
                    code_cell(evidence_of(row)),
                ]
            )
            + " |",
            file=output,
        )


def render_summary(
    rows: list[dict[str, str]],
    output: TextIO,
    inventory_licences: dict[str, str] | None = None,
) -> dict[str, int]:
    grouped = classify_rows(rows)
    inventory_licences = inventory_licences or {}
    review_rows = (
        len(grouped["added"])
        + len(grouped["license_changed"])
        + len(grouped["unresolved"])
    )
    blocking_uncovered = [
        row for row in grouped["uncovered"]
        if row.get("source_kind", "").strip().lower() != SOURCE_KIND_ATTRIBUTION
    ]
    advisory_uncovered = [
        row for row in grouped["uncovered"]
        if row.get("source_kind", "").strip().lower() == SOURCE_KIND_ATTRIBUTION
    ]

    counts = {
        "raw_rows": len(rows),
        # review_rows is the branch-protection number. It counts OSRB
        # re-engagement only; the two classes below are reported separately so
        # that adding them could not shift what a red check has always meant.
        "review_rows": review_rows,
        "added_rows": len(grouped["added"]),
        "license_changed_rows": len(grouped["license_changed"]),
        "unresolved_rows": len(grouped["unresolved"]),
        "version_only_rows": len(grouped["version_only"]),
        "removed_rows": len(grouped["removed"]),
        # Split deliberately. Attribution files are recognised as
        # dependency-bearing but nothing parses prose, so they can never become
        # "covered" and the remedy this report gives — extend
        # osrb_scan.is_dependency_file — cannot be acted on for them. Blocking a
        # PR for adding a LICENSE.3rdparty would punish the exact behaviour the
        # licence process asks for. Only `uncovered_rows` feeds the job failure.
        "uncovered_rows": len(blocking_uncovered),
        "attribution_rows": len(advisory_uncovered),
        "used_undeclared_rows": len(grouped["used_undeclared"]),
    }

    print("# OSRB license review overview", file=output)
    print(file=output)
    if review_rows:
        print(f"**{review_rows} change(s) require OSRB attention.**", file=output)
    else:
        print("**No changes require OSRB re-engagement.**", file=output)
    if counts["uncovered_rows"]:
        # Said separately from the OSRB sentence above on purpose: a coverage
        # gap is not an approval request, and a reader who treats it as one
        # will go ask OSRB for something OSRB cannot give.
        print(file=output)
        print(
            f"**{counts['uncovered_rows']} dependency file(s) could not be read by the "
            "scanner, so this report is incomplete. This blocks the pull request and "
            "needs a scanner change, not an OSRB approval.**",
            file=output,
        )
    if counts["attribution_rows"]:
        print(file=output)
        print(
            f"{counts['attribution_rows']} third-party attribution file(s) were added. "
            "Advisory, not blocking — an attribution file is the output of the licence "
            "process, not a dependency declaration. Confirm by hand that it matches the "
            "resolved dependency set for its component.",
            file=output,
        )
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

    if blocking_uncovered:
        print(file=output)
        print(
            f"## Dependency files the scanner cannot read ({len(blocking_uncovered)})",
            file=output,
        )
        print(file=output)
        print(
            "**Blocking.** These files bring third-party software into the repository, "
            "but `osrb_scan.py` has no parser for them. Nothing inside them was "
            "inventoried, so neither this overview nor the CSV says anything about what "
            "they pull in.",
            file=output,
        )
        print(file=output)
        print(
            "An OSRB approval cannot clear this — there is nothing to approve yet. Fix it "
            "by teaching the scanner: extend `is_dependency_file` in "
            "`.github/osrb/osrb_scan.py` so the path is recognised, add or extend the "
            "parser that turns it into rows, and cover it in "
            "`.github/osrb/test_osrb_scan.py`. If the file genuinely carries no "
            "third-party dependency, exclude it there and say why in a comment.",
            file=output,
        )
        print(file=output)
        _write_uncovered_table(output, blocking_uncovered)

    if advisory_uncovered:
        # Rendered apart from the blocking section on purpose. Listing an
        # attribution file under a heading whose first word is "Blocking",
        # beside remediation text telling the author to write a parser, next to
        # a GREEN check, is an artifact that contradicts itself - and the
        # instruction cannot be followed, because nothing parses prose.
        print(file=output)
        print(
            f"## Third-party attribution files added ({len(advisory_uncovered)})",
            file=output,
        )
        print(file=output)
        print(
            "**Advisory - this does not block the pull request.** An attribution file is "
            "the output of the licence process, not a dependency declaration, so there is "
            "no parser to write and no OSRB approval to obtain. Confirm by hand that its "
            "contents match the resolved dependency set for the component.",
            file=output,
        )
        print(file=output)
        print(
            "Note that `.github/CODEOWNERS` routes only `LICENSE-3rd-party.txt` to "
            "`@NVIDIA-AI-Blueprints/VSS_OSRB_Approvers`; other attribution filenames are "
            "not routed anywhere, so this section may be the only signal.",
            file=output,
        )
        print(file=output)
        _write_uncovered_table(output, advisory_uncovered)

    # Permissive imports are not worth a reviewer's attention: the same rule the
    # OSRB review comment applies. Resolve each import's licence from the
    # committed inventory (the use-side row carries none of its own) and split.
    def _row_licence(row: dict[str, str]) -> str:
        return row.get("new_license") or inventory_licences.get(
            _canon(row.get("package", "")), ""
        )

    shown_undeclared = [
        row for row in grouped["used_undeclared"]
        if not _is_permissive_expr(_row_licence(row))
    ]
    permissive_undeclared = [
        row for row in grouped["used_undeclared"]
        if _is_permissive_expr(_row_licence(row))
    ]
    counts["used_undeclared_permissive"] = len(permissive_undeclared)

    if shown_undeclared:
        print(file=output)
        print(
            f"## Imported but not declared ({len(shown_undeclared)})",
            file=output,
        )
        print(file=output)
        print(
            "**Advisory — report-only, does not block this PR.** Source files import "
            "these names, but no manifest or lockfile in the owning module declares "
            "them. This pass infers a dependency from an `import` rather than reading a "
            "declaration, so it can be wrong; it is never counted toward the OSRB review "
            "total and never fails the check.",
            file=output,
        )
        print(file=output)
        print(
            "Common causes, in the order worth checking: the package is available only "
            "transitively (it works until an upstream release drops it), the import name "
            "differs from the distribution name, or the code is vendored. Declare it in "
            "the module's manifest when the gap is real and yours to fix.",
            file=output,
        )
        print(file=output)
        _write_usage_table(output, shown_undeclared, inventory_licences)
        if permissive_undeclared:
            print(file=output)
            print(
                f"_{len(permissive_undeclared)} further imported-but-undeclared name(s) "
                "resolve to a permissive licence and are omitted._",
                file=output,
            )
    elif grouped["used_undeclared"]:
        # Everything was permissive: say so rather than showing nothing.
        print(file=output)
        print(
            f"## Imported but not declared (0 non-permissive)", file=output
        )
        print(file=output)
        print(
            f"_All {len(grouped['used_undeclared'])} imported-but-undeclared name(s) "
            "resolve to a permissive licence; none needs review._",
            file=output,
        )

    return counts


def write_github_output(path: str, counts: dict[str, int]) -> None:
    with open(path, "a", encoding="utf-8") as output:
        for name, value in counts.items():
            print(f"{name}={value}", file=output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # license-diff.csv, not osrb-scan.csv: the private GitLab OSRB pipeline
    # fetches the artifact and this filename out of it. Renaming to match the
    # workflow would break that consumer silently. See osrb-scan.yml.
    parser.add_argument("--input", default="license-diff.csv")
    parser.add_argument("--output", default="osrb-summary.md")
    parser.add_argument("--github-output", help="Optional path supplied by GitHub Actions.")
    parser.add_argument(
        "--inventory",
        help="committed inventory.csv; supplies licences so the imported-but-"
        "undeclared section can hide permissive names.",
    )
    args = parser.parse_args()

    with open(args.input, newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    with open(args.output, "w", encoding="utf-8") as output:
        counts = render_summary(
            rows, output, _load_inventory_licences(getattr(args, "inventory", None))
        )
    if args.github_output:
        write_github_output(args.github_output, counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
