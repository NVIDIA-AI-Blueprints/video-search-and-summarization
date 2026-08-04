#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the OSRB license-diff overview."""

from __future__ import annotations

import importlib.util
import io
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("license_diff_summary.py")
MODULE_SPEC = importlib.util.spec_from_file_location("license_diff_summary", MODULE_PATH)
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
        "notes": "",
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


if __name__ == "__main__":
    unittest.main()
