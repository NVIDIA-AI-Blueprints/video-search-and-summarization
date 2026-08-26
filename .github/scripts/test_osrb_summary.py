#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the OSRB scan overview."""

from __future__ import annotations

import csv
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("osrb_summary.py")
MODULE_SPEC = importlib.util.spec_from_file_location("osrb_summary", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
summary = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(summary)


def row(
    change: str,
    *,
    package: str = "demo",
    old_version: str = "1.0",
    new_version: str = "2.0",
    old_license: str = "MIT",
    new_license: str = "MIT",
    source_kind: str = "lockfile",
    source_file: str = "",
    module: str = "",
    risk: str = "None",
    notes: str = "",
) -> dict[str, str]:
    return {
        "language": "python",
        "package": package,
        "change": change,
        "old_version": old_version,
        "new_version": new_version,
        "old_license": old_license,
        "new_license": new_license,
        "repository_url": f"https://example.com/{package}",
        "notes": notes,
        "source_kind": source_kind,
        "source_file": source_file,
        "module": module,
        "risk": risk,
    }


class ReviewCategoryTest(unittest.TestCase):
    def test_addition_requires_review(self) -> None:
        self.assertEqual(
            summary.review_category(
                row("added", old_version="", old_license="", new_license="Apache-2.0")
            ),
            "added",
        )

    def test_same_license_version_update_does_not_require_review(self) -> None:
        self.assertIsNone(summary.review_category(row("updated")))

    def test_equivalent_license_labels_do_not_create_false_drift(self) -> None:
        self.assertIsNone(
            summary.review_category(
                row(
                    "updated",
                    old_license="Apache Software License",
                    new_license="Apache-2.0",
                )
            )
        )

    def test_classifier_derived_apache_label_does_not_create_false_drift(self) -> None:
        self.assertIsNone(
            summary.review_category(
                row(
                    "updated",
                    old_license="Apache Software License",
                    new_license="Apache Software",
                )
            )
        )

    def test_license_change_requires_review(self) -> None:
        self.assertEqual(
            summary.review_category(
                row("updated", old_license="MIT", new_license="GPL-3.0")
            ),
            "license_changed",
        )

    def test_unresolved_license_comparison_requires_review(self) -> None:
        self.assertEqual(
            summary.review_category(row("updated", old_license="", new_license="MIT")),
            "unresolved",
        )

    def test_removal_does_not_require_review(self) -> None:
        self.assertIsNone(
            summary.review_category(
                row("removed", new_version="", new_license="")
            )
        )


class RenderSummaryTest(unittest.TestCase):
    def test_overview_keeps_raw_counts_but_hides_non_actionable_rows(self) -> None:
        rows = [
            row("added", package="new-dep", old_version="", old_license=""),
            row("updated", package="version-only"),
            row(
                "updated",
                package="relicensed",
                old_license="MIT",
                new_license="MPL-2.0",
            ),
            row("removed", package="deleted", new_version="", new_license=""),
        ]
        output = io.StringIO()

        counts = summary.render_summary(rows, output)
        rendered = output.getvalue()

        self.assertEqual(counts["raw_rows"], 4)
        self.assertEqual(counts["review_rows"], 2)
        self.assertEqual(counts["version_only_rows"], 1)
        self.assertEqual(counts["removed_rows"], 1)
        self.assertIn("new-dep", rendered)
        self.assertIn("relicensed", rendered)
        self.assertNotIn("| version-only |", rendered)
        self.assertNotIn("| deleted |", rendered)

    def test_markdown_escapes_table_separators(self) -> None:
        self.assertEqual(summary.markdown_cell("MIT OR Apache | custom"), "MIT OR Apache \\| custom")

    def test_overview_renders_without_the_appended_columns(self) -> None:
        """An older CSV must still render, not crash the reporting step.

        A rerun of an in-flight PR, or an artifact replayed from history, can
        hold a CSV written before source_kind/source_file/module existed. A
        KeyError here would lose the whole overview for changes that do need
        review.
        """
        legacy = row("added", package="old-shape", old_version="", old_license="")
        for column in ("source_kind", "source_file", "module", "risk"):
            legacy.pop(column)
        output = io.StringIO()

        counts = summary.render_summary([legacy], output)

        self.assertEqual(counts["review_rows"], 1)
        self.assertEqual(counts["uncovered_rows"], 0)
        self.assertIn("old-shape", output.getvalue())


class RaggedRowTest(unittest.TestCase):
    def test_a_short_csv_line_does_not_lose_the_whole_overview(self) -> None:
        """csv.DictReader fills a truncated line with None, not "".

        One malformed line must degrade to a dash in one cell, not raise
        AttributeError inside the reporting step and take the entire OSRB
        overview -- including the rows that do need review -- with it.
        """
        header = (
            "language,package,change,old_version,new_version,old_license,"
            "new_license,repository_url,notes,source_kind,source_file,module,risk\n"
        )
        raw = header + "python,httpx,USED_UNDECLARED,,,,,,\n"
        rows = list(csv.DictReader(io.StringIO(raw)))
        self.assertIsNone(rows[0]["source_file"])
        output = io.StringIO()

        counts = summary.render_summary(rows, output)

        self.assertEqual(counts["used_undeclared_rows"], 1)
        self.assertIn("| httpx | python | — | — |", output.getvalue())


class UncoveredSourceTest(unittest.TestCase):
    """UNCOVERED_SOURCE blocks, but never as an OSRB approval request."""

    def uncovered(self) -> dict[str, str]:
        return row(
            "UNCOVERED_SOURCE",
            package="services/rtvi/rt-vlm/Cargo.toml",
            old_version="",
            new_version="",
            old_license="",
            new_license="",
            source_kind="manifest",
            source_file="services/rtvi/rt-vlm/Cargo.toml",
            module="services/rtvi/rt-vlm",
            risk="Unknown",
            notes="no parser for Cargo.toml",
        )

    def test_uncovered_is_not_an_osrb_review_row(self) -> None:
        self.assertIsNone(summary.review_category(self.uncovered()))

    def test_uncovered_is_counted_separately_from_review_rows(self) -> None:
        output = io.StringIO()

        counts = summary.render_summary([self.uncovered()], output)

        self.assertEqual(counts["uncovered_rows"], 1)
        self.assertEqual(counts["review_rows"], 0)

    def test_uncovered_tells_the_reader_to_change_the_scanner(self) -> None:
        output = io.StringIO()

        summary.render_summary([self.uncovered()], output)
        rendered = output.getvalue()

        self.assertIn("Dependency files the scanner cannot read (1)", rendered)
        self.assertIn("is_dependency_file", rendered)
        self.assertIn("osrb_scan.py", rendered)
        self.assertIn("**Blocking.**", rendered)
        self.assertIn("services/rtvi/rt-vlm/Cargo.toml", rendered)

    def test_uncovered_does_not_inflate_the_osrb_headline(self) -> None:
        """The blocking sentence must not read as "OSRB must approve this"."""
        output = io.StringIO()

        summary.render_summary([self.uncovered()], output)
        rendered = output.getvalue()

        self.assertIn("**No changes require OSRB re-engagement.**", rendered)
        self.assertIn("not an OSRB approval", rendered)


class UsedUndeclaredTest(unittest.TestCase):
    """The use-side pass is report-only; a false positive must never block."""

    def used(self) -> dict[str, str]:
        return row(
            "USED_UNDECLARED",
            package="httpx",
            old_version="",
            new_version="",
            old_license="",
            new_license="",
            source_kind="usage",
            source_file="services/ingestion/app/main.py#L12",
            module="services/ingestion",
            risk="Unknown",
        )

    def test_used_undeclared_is_not_an_osrb_review_row(self) -> None:
        self.assertIsNone(summary.review_category(self.used()))

    def test_used_undeclared_never_contributes_to_the_failure_count(self) -> None:
        output = io.StringIO()

        counts = summary.render_summary([self.used()], output)

        self.assertEqual(counts["review_rows"], 0)
        self.assertEqual(counts["uncovered_rows"], 0)
        self.assertEqual(counts["used_undeclared_rows"], 1)

    def test_used_undeclared_is_labelled_report_only(self) -> None:
        output = io.StringIO()

        summary.render_summary([self.used()], output)
        rendered = output.getvalue()

        self.assertIn("Imported but not declared (1)", rendered)
        self.assertIn("report-only, does not block this PR", rendered)
        self.assertIn("services/ingestion/app/main.py#L12", rendered)


class GithubOutputTest(unittest.TestCase):
    def test_failure_gate_can_read_both_counts(self) -> None:
        """osrb-scan.yml keys its failure step on exactly these two names."""
        rows = [
            row("added", package="new-dep", old_version="", old_license=""),
            row("UNCOVERED_SOURCE", package="a/build.gradle", source_file="a/build.gradle"),
            row("USED_UNDECLARED", package="httpx", source_file="a/main.py#L1"),
        ]
        output = io.StringIO()
        counts = summary.render_summary(rows, output)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "github_output"
            path.write_text("")
            summary.write_github_output(str(path), counts)
            emitted = dict(
                line.split("=", 1) for line in path.read_text().splitlines() if line
            )

        self.assertEqual(emitted["review_rows"], "1")
        self.assertEqual(emitted["uncovered_rows"], "1")
        self.assertEqual(emitted["used_undeclared_rows"], "1")
        self.assertEqual(emitted["raw_rows"], "3")


if __name__ == "__main__":
    unittest.main()
