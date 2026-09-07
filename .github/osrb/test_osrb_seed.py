#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the licence seeder's guard rails.

The seeder writes into a committed compliance artifact and its output rides the
generator's carry-forward indefinitely, so a wrong fill outlives the run that
made it. Every guard here exists because its absence produced a real wrong row.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).with_name("osrb_seed.py")
SPEC = importlib.util.spec_from_file_location("osrb_seed", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
osrb_seed = importlib.util.module_from_spec(SPEC)
sys.modules["osrb_seed"] = osrb_seed
SPEC.loader.exec_module(osrb_seed)


def row(**kw) -> dict[str, str]:
    base = {
        "package": "demo", "version": "1.0.0", "license": "UNKNOWN",
        "language": "python", "usage_evidence": "declared-manifest", "risk": "Unknown",
    }
    base.update(kw)
    return base


class ProvenanceGuardTest(unittest.TestCase):
    """A registry may only answer for rows that came from that registry.

    ``pyds`` is DeepStream's bindings arriving inside the base image; PyPI's
    ``pyds`` is an unrelated GPLv3 package sharing the name. Without this guard
    the seeder wrote that GPLv3 onto our row — a fabricated copyleft finding
    that would have ridden the carry-forward until someone noticed.
    """

    def test_imported_only_rows_are_never_seeded(self) -> None:
        rows = [row(package="pyds", usage_evidence="imported-only")]
        with mock.patch.object(osrb_seed, "resolve_pypi", return_value="GPL-3.0"):
            self.assertEqual(osrb_seed.seed(rows), [])

    def test_declared_manifest_rows_are_seeded(self) -> None:
        rows = [row(usage_evidence="declared-manifest")]
        with mock.patch.object(osrb_seed, "resolve_pypi", return_value="MIT"):
            filled = osrb_seed.seed(rows)
        self.assertEqual([(rows[0], "MIT")], filled)

    def test_container_pip_counts_as_pypi_provenance(self) -> None:
        rows = [row(usage_evidence="container-pip")]
        with mock.patch.object(osrb_seed, "resolve_pypi", return_value="MIT"):
            self.assertEqual(len(osrb_seed.seed(rows)), 1)

    def test_mixed_evidence_needs_only_one_registry_class(self) -> None:
        rows = [row(usage_evidence="imported-only;declared-manifest")]
        with mock.patch.object(osrb_seed, "resolve_pypi", return_value="MIT"):
            self.assertEqual(len(osrb_seed.seed(rows)), 1)


class NonAnswerGuardTest(unittest.TestCase):
    def test_a_registry_declaring_unknown_is_not_progress(self) -> None:
        # PyPI's `arango` literally declares license: "UNKNOWN". Writing that
        # over our own UNKNOWN counted as "filled: 1" forever while changing
        # nothing.
        rows = [row(package="arango")]
        for answer in ("UNKNOWN", "unknown", "NOASSERTION", "", "None", "other"):
            with mock.patch.object(osrb_seed, "resolve_pypi", return_value=answer):
                self.assertEqual(osrb_seed.seed(rows), [], answer)

    def test_already_licensed_rows_are_never_touched(self) -> None:
        rows = [row(license="MIT")]
        with mock.patch.object(osrb_seed, "resolve_pypi", return_value="GPL-3.0"):
            self.assertEqual(osrb_seed.seed(rows), [])


class ClassifierTest(unittest.TestCase):
    def test_two_different_licence_classifiers_stay_ambiguous(self) -> None:
        self.assertEqual(
            osrb_seed._classifier_licence(
                ["License :: OSI Approved :: MIT License",
                 "License :: OSI Approved :: Apache Software License"]
            ),
            "",
        )

    def test_one_classifier_resolves(self) -> None:
        self.assertEqual(
            osrb_seed._classifier_licence(["License :: OSI Approved :: MIT License"]),
            "MIT License",
        )


if __name__ == "__main__":
    unittest.main()
