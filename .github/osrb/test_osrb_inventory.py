#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the OSRB state inventory.

Same two kinds of test as ``test_osrb_scan.py``, for the same reason: fixtures
pin a contract, and real-tree tests run the code over this repository, because
every silent failure this tooling has had was invisible to a fixture.

Three of these carry more weight than the rest:

* ``EvidenceSeamTest`` runs the real ``osrb_sources`` pass and asserts that
  every note it produces classifies into the ``usage_evidence`` vocabulary. The
  note text is the seam between two files owned by different changes; when it
  drifts, this test is what says so instead of the inventory quietly filling
  with UNKNOWN.
* ``DeterminismTest`` generates the whole inventory twice, in two processes
  with different ``PYTHONHASHSEED``, and compares SHA-256. This file is
  committed and diffed, so "same tree, same bytes" is a hard requirement and
  set-iteration order is the way it breaks.
* ``CommittedInventoryTest`` re-checks the invariants against the CSV actually
  committed in the repo, so a hand-edited or stale ``inventory.csv`` fails here
  rather than in the private OSRB comparison.

Run standalone:  python3 .github/osrb/test_osrb_inventory.py
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("osrb_inventory.py")
MODULE_SPEC = importlib.util.spec_from_file_location("osrb_inventory", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
osrb_inventory = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(osrb_inventory)

osrb_scan = osrb_inventory.osrb_scan
osrb_sources = osrb_inventory.osrb_sources

REPO_ROOT = Path(__file__).parents[2]
COMMITTED_INVENTORY = Path(__file__).with_name("inventory.csv")


def repo_paths() -> list[str]:
    """Every tracked path at HEAD, or [] when git is unavailable."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "ls-tree", "-r", "--name-only", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return out.splitlines()


class ColumnsTest(unittest.TestCase):
    def test_column_order_is_the_published_shape(self) -> None:
        # This CSV is committed and read by the private OSRB comparison. A
        # reorder is a breaking change to a consumer that is not visible from
        # this repository, so it has to be a deliberate edit here too.
        self.assertEqual(
            osrb_inventory.COLUMNS,
            [
                "package",
                "version",
                "license",
                "module",
                "language",
                "source_kind",
                "source_file",
                "dep_scope",
                "vendored_in_repo",
                "copied_adapted",
                "container_only",
                "usage_evidence",
                "risk",
            ],
        )

    def test_usage_evidence_vocabulary_is_closed_and_sorted(self) -> None:
        self.assertEqual(
            list(osrb_inventory.USAGE_EVIDENCE), sorted(osrb_inventory.USAGE_EVIDENCE)
        )
        self.assertEqual(
            set(osrb_inventory.USAGE_EVIDENCE),
            {
                "build-fetch",
                "ci-tooling",
                "container-apt",
                "container-base",
                "container-image",
                "container-pip",
                "declared-manifest",
                "imported-only",
                "vendored-source",
            },
        )

    def test_every_mapped_evidence_is_in_the_vocabulary(self) -> None:
        for _kind, _prefix, evidence in osrb_inventory._EVIDENCE_BY_NOTE:
            self.assertIn(evidence, osrb_inventory.USAGE_EVIDENCE)


class EvidenceClassificationTest(unittest.TestCase):
    def classify(self, kind: str, notes: str) -> str:
        return osrb_inventory.evidence_for_source_row(
            {"source_kind": kind, "notes": notes, "package": "x"}
        )

    def test_each_container_shape_maps_to_its_own_bucket(self) -> None:
        container = osrb_sources.KIND_CONTAINER
        self.assertEqual(self.classify(container, "base image (FROM)"), "container-base")
        self.assertEqual(
            self.classify(container, "external image (COPY --from)"), "container-base"
        )
        self.assertEqual(
            self.classify(container, "OS package installed into the image"), "container-apt"
        )
        self.assertEqual(
            self.classify(container, "pip install in a Dockerfile — declared in no manifest"),
            "container-pip",
        )
        self.assertEqual(
            self.classify(container, "fetched into the image with curl"), "build-fetch"
        )
        self.assertEqual(
            self.classify(container, "upstream source cloned into the image"), "build-fetch"
        )

    def test_compose_and_chart_are_whole_images(self) -> None:
        self.assertEqual(
            self.classify(osrb_sources.KIND_COMPOSE, "compose service image"),
            "container-image",
        )
        self.assertEqual(
            self.classify(osrb_sources.KIND_CHART, "Helm chart dependency"),
            "container-image",
        )

    def test_cmake_separates_fetching_from_linking(self) -> None:
        build = osrb_sources.KIND_BUILD
        self.assertEqual(
            self.classify(
                build, "third-party source built during the build (fetchcontent_declare)"
            ),
            "build-fetch",
        )
        # find_package / pkg-config link against a library nothing fetched and
        # no manifest names — the same evidence class as an undeclared import.
        self.assertEqual(
            self.classify(build, "native dependency required at build time (find_package)"),
            "imported-only",
        )
        self.assertEqual(
            self.classify(build, "native dependency resolved via pkg-config"), "imported-only"
        )

    def test_ci_pins_are_ci_tooling(self) -> None:
        self.assertEqual(
            self.classify(osrb_sources.KIND_CI, "GitHub Actions dependency — runs in CI"),
            "ci-tooling",
        )
        self.assertEqual(
            self.classify(osrb_sources.KIND_CI, "pre-commit hook — runs at dev/CI time"),
            "ci-tooling",
        )
        self.assertEqual(
            self.classify(osrb_sources.KIND_CI, "container action image"), "ci-tooling"
        )

    def test_the_note_suffix_after_a_semicolon_is_ignored(self) -> None:
        # osrb_sources appends qualifiers ("; unresolved build/env
        # substitution", "; pinned to v4.3.1") after a semicolon. Matching the
        # whole string would drop those rows into UNKNOWN.
        self.assertEqual(
            self.classify(
                osrb_sources.KIND_CONTAINER,
                "base image (FROM); unresolved build/env substitution — resolve before review",
            ),
            "container-base",
        )

    def test_an_unrecognised_note_is_unknown_not_a_guess(self) -> None:
        # A per-kind default would file a brand-new kind of container evidence
        # as a base image, and the row would read as reviewed when it was
        # guessed. UNKNOWN is loud; a wrong bucket is not.
        self.assertEqual(
            self.classify(osrb_sources.KIND_CONTAINER, "installed by some new mechanism"),
            "UNKNOWN",
        )
        self.assertEqual(self.classify("brand-new-kind", "base image (FROM)"), "UNKNOWN")


class EvidenceSeamTest(unittest.TestCase):
    """The real-tree half of the classification contract."""

    def test_every_note_the_repo_produces_is_classified(self) -> None:
        paths = repo_paths()
        if not paths:
            self.skipTest("git is unavailable")
        rows = osrb_sources.inventory_at_ref("HEAD", osrb_scan._git_show, paths)
        unclassified = sorted(
            {
                (row["source_kind"], row.get("notes", ""))
                for row in rows.values()
                if row.get("change") != osrb_sources.CHANGE_UNCOVERED
                and osrb_inventory._FIRST_PARTY_NOTE not in row.get("notes", "")
                and osrb_inventory.evidence_for_source_row(row) == osrb_inventory.UNKNOWN
            }
        )
        self.assertEqual(unclassified, [], f"unclassified osrb_sources evidence: {unclassified}")

    def test_in_repo_subcharts_are_not_inventoried_as_third_party(self) -> None:
        paths = repo_paths()
        if not paths:
            self.skipTest("git is unavailable")
        inventory = osrb_inventory.Inventory()
        osrb_inventory.collect_sources("HEAD", paths, inventory)
        packages = {entry.package for entry in inventory.entries()}
        # deploy/helm ships subcharts declared as `repository: file://...`;
        # they are this repository's own code and must not reach OSRB.
        self.assertNotIn("vss-blueprint", packages)


class AttributionTest(unittest.TestCase):
    def test_reads_both_heading_shapes(self) -> None:
        text = (
            "# Third-Party Licenses\n\n"
            "## attrs:26.1.0\n\n"
            "**License Type:** MIT\n\n"
            "```\nfull text\n```\n\n"
            "## aioboto3 (15.5.0)\n\n"
            "**License:** Apache-2.0\n\n"
        )
        self.assertEqual(
            osrb_inventory.parse_attribution(text),
            {("attrs", "26.1.0"): "MIT", ("aioboto3", "15.5.0"): "Apache-2.0"},
        )

    def test_a_heading_with_no_license_line_contributes_nothing(self) -> None:
        # Inheriting the previous package's licence is exactly the kind of
        # plausible-looking guess that ends a review on the wrong answer.
        text = "## alpha (1.0)\n\n**License:** MIT\n\n## beta (2.0)\n\nsome prose\n"
        self.assertEqual(osrb_inventory.parse_attribution(text), {("alpha", "1.0"): "MIT"})

    def test_placeholder_licenses_are_not_recorded(self) -> None:
        text = "## alpha (1.0)\n\n**License:** UNKNOWN\n"
        self.assertEqual(osrb_inventory.parse_attribution(text), {})

    def test_document_title_is_not_a_package(self) -> None:
        text = "## Third-Party Licenses\n\n**License:** MIT\n"
        self.assertEqual(osrb_inventory.parse_attribution(text), {})

    def test_reads_the_repository_s_own_attribution_files(self) -> None:
        paths = repo_paths()
        if not paths:
            self.skipTest("git is unavailable")
        licenses = osrb_inventory.attribution_licenses("HEAD", paths)
        self.assertTrue(licenses, "no attribution file in the tree was parsed")
        # Pinned against the real files: the agent image publishes aioboto3 and
        # the configurator publishes attrs. If either stops resolving, the
        # licence column silently loses a few hundred rows to UNKNOWN.
        self.assertEqual(licenses.get(("", "aioboto3", "15.5.0")), "Apache-2.0")
        self.assertEqual(licenses.get(("", "attrs", "26.1.0")), "MIT")


class LicenseResolutionTest(unittest.TestCase):
    def entry(self, **kwargs):
        defaults = {
            "package": "widget",
            "version": "1.0.0",
            "module": "services/agent",
            "language": "python",
        }
        defaults.update(kwargs)
        return osrb_inventory._Entry(**defaults)

    def test_parser_metadata_wins(self) -> None:
        entry = self.entry(licenses={"MIT"})
        resolved = osrb_inventory.resolve_license(
            entry, {("", "widget", "1.0.0"): "Apache-2.0"}, {("widget", "1.0.0"): "BSD-3-Clause"}
        )
        self.assertEqual(resolved, ("MIT", "parser"))

    def test_a_module_s_own_attribution_beats_another_module_s(self) -> None:
        entry = self.entry()
        attribution = {
            ("services/agent", "widget", "1.0.0"): "BSD-3-Clause",
            ("", "widget", "1.0.0"): "BSD License",
        }
        self.assertEqual(
            osrb_inventory.resolve_license(entry, attribution, {}),
            ("BSD-3-Clause", "attribution"),
        )

    def test_another_parser_in_the_tree_answers_for_the_same_release(self) -> None:
        # `deepmerge 4.3.1` is bare in one package-lock.json in this repo and
        # MIT in another. Without this tier the bare row stays UNKNOWN, and the
        # NEXT run picks it up out of --previous instead — which means the
        # committed file changes with no change to the tree.
        entries = [
            self.entry(package="deepmerge", version="4.3.1", licenses={"MIT"}),
            self.entry(package="deepmerge", version="4.3.1", module="services/vios"),
        ]
        in_tree = osrb_inventory.unanimous_parser_licenses(entries)
        self.assertEqual(
            osrb_inventory.resolve_license(entries[1], {}, {}, in_tree),
            ("MIT", "another-parser"),
        )

    def test_parsers_that_disagree_do_not_answer_for_each_other(self) -> None:
        entries = [
            self.entry(licenses={"MIT"}),
            self.entry(module="services/vios", licenses={"GPL-3.0-only"}),
            self.entry(module="services/alert"),
        ]
        in_tree = osrb_inventory.unanimous_parser_licenses(entries)
        self.assertEqual(in_tree, {})
        self.assertEqual(
            osrb_inventory.resolve_license(entries[2], {}, {}, in_tree),
            ("UNKNOWN", "unresolved"),
        )

    def test_carry_forward_only_for_the_same_version(self) -> None:
        previous = {("widget", "1.0.0"): "MIT"}
        self.assertEqual(
            osrb_inventory.resolve_license(self.entry(), {}, previous), ("MIT", "carried-forward")
        )
        # A version bump must NOT inherit the old release's licence: that is
        # the whole reason the carry-forward key is the (package, version) pair.
        self.assertEqual(
            osrb_inventory.resolve_license(self.entry(version="2.0.0"), {}, previous),
            ("UNKNOWN", "unresolved"),
        )

    def test_conflicting_parser_licenses_are_both_kept_and_the_risk_is_the_worst(self) -> None:
        entry = self.entry(licenses={"MIT", "GPL-3.0-only"})
        resolved, source = osrb_inventory.resolve_license(entry, {}, {})
        self.assertEqual((resolved, source), ("GPL-3.0-only;MIT", "parser"))
        self.assertEqual(osrb_scan.license_risk(resolved), osrb_scan.RISK_HIGH)

    def test_previous_csv_never_carries_forward_an_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "previous.csv")
            with open(path, "w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=osrb_inventory.COLUMNS)
                writer.writeheader()
                writer.writerow({"package": "widget", "version": "1.0.0", "license": "UNKNOWN"})
                writer.writerow({"package": "gadget", "version": "2.0.0", "license": "MIT"})
            self.assertEqual(
                osrb_inventory.previous_licenses(path), {("gadget", "2.0.0"): "MIT"}
            )


class VendoredTest(unittest.TestCase):
    def test_the_innermost_vendor_directory_owns_the_package(self) -> None:
        # webrtc's own `third_party/` sits inside our `include/`. Attributing
        # abseil to the outer tree would file five upstream projects as one.
        roots = osrb_inventory.vendored_roots(
            [
                "services/vios/include/3rdparty/aws/auth/auth.h",
                "services/vios/src/x/inc/webrtc/src/third_party/abseil-cpp/absl/base/config.h",
                "services/agent/src/app.py",
            ]
        )
        self.assertEqual(
            sorted(roots),
            [
                "services/vios/include/3rdparty/aws",
                "services/vios/src/x/inc/webrtc/src/third_party/abseil-cpp",
            ],
        )

    def test_version_comes_from_an_archive_name_only_when_unambiguous(self) -> None:
        self.assertEqual(
            osrb_inventory.vendored_version(["a/3rdparty/ffmpeg/FFmpeg-n8.0.1.tar.gz"]), "8.0.1"
        )
        self.assertEqual(
            osrb_inventory.vendored_version(
                ["a/x-1.0.tar.gz", "a/x-2.0.tar.gz"]
            ),
            "UNKNOWN",
        )
        # A directory name like `libjpeg-8b` is not mined for a version: a
        # wrong version silently matches the wrong row in the approved sheet.
        self.assertEqual(osrb_inventory.vendored_version(["a/libjpeg-8b/jpeglib.h"]), "UNKNOWN")

    def test_language_is_the_most_common_extension_ties_broken_alphabetically(self) -> None:
        self.assertEqual(
            osrb_inventory.vendored_language(["a/x.h", "a/y.h", "a/z.py"]), "c"
        )
        self.assertEqual(osrb_inventory.vendored_language(["a/x.tar.gz"]), "UNKNOWN")

    def test_real_vendored_trees_are_found_and_flagged(self) -> None:
        paths = repo_paths()
        if not paths:
            self.skipTest("git is unavailable")
        inventory = osrb_inventory.Inventory()
        osrb_inventory.collect_vendored("HEAD", paths, inventory)
        rows = {entry.package: entry for entry in inventory.entries()}
        self.assertIn("aws", rows)
        self.assertIn("ffmpeg", rows)
        self.assertEqual(rows["ffmpeg"].version, "8.0.1")
        for entry in rows.values():
            self.assertEqual(entry.evidence, {osrb_inventory.EV_VENDORED_SOURCE})

    def test_a_single_gpl_file_in_a_vendored_tree_is_not_averaged_away(self) -> None:
        paths = repo_paths()
        if not paths:
            self.skipTest("git is unavailable")
        expr = osrb_inventory.spdx_licenses("HEAD", "services/vios/include/3rdparty/aws")
        # 594 Apache-2.0 headers and one GPL: keeping only the majority answer
        # would hide the single file the review is actually about.
        self.assertIn("Apache-2.0", expr)
        self.assertIn("GPL-2.0-only OR BSD-3-Clause", expr)
        self.assertEqual(osrb_scan.license_risk(expr), osrb_scan.RISK_HIGH)


class RowRenderingTest(unittest.TestCase):
    def row(self, **kwargs) -> dict[str, str]:
        entry = osrb_inventory._Entry(
            package=kwargs.get("package", "widget"),
            version=kwargs.get("version", "1.0.0"),
            module=kwargs.get("module", "services/agent"),
            language=kwargs.get("language", "python"),
            source_files=kwargs.get("source_files", {"services/agent/uv.lock"}),
            source_kinds=kwargs.get("source_kinds", {"lockfile"}),
            evidence=kwargs.get("evidence", {"declared-manifest"}),
            scopes=kwargs.get("scopes", {"runtime"}),
        )
        return osrb_inventory.row_for(entry, kwargs.get("license", "MIT"))

    def test_multi_valued_cells_are_sorted(self) -> None:
        row = self.row(
            evidence={"declared-manifest", "container-pip"},
            source_files={"b/Dockerfile", "a/uv.lock"},
            source_kinds={"lockfile", "container"},
        )
        self.assertEqual(row["usage_evidence"], "container-pip;declared-manifest")
        self.assertEqual(row["source_file"], "a/uv.lock;b/Dockerfile")
        self.assertEqual(row["source_kind"], "container;lockfile")

    def test_container_only_is_yes_only_when_nothing_in_the_tree_declares_it(self) -> None:
        self.assertEqual(self.row(evidence={"container-apt"})["container_only"], "yes")
        self.assertEqual(
            self.row(evidence={"container-apt", "declared-manifest"})["container_only"], "no"
        )

    def test_vendored_rows_admit_they_cannot_prove_they_are_pristine(self) -> None:
        row = self.row(evidence={"vendored-source"})
        self.assertEqual(row["vendored_in_repo"], "yes")
        # Answering "no" would claim an upstream diff this tool never made.
        self.assertEqual(row["copied_adapted"], "UNKNOWN")
        self.assertEqual(self.row()["copied_adapted"], "no")

    def test_ci_scope_only_when_every_piece_of_evidence_is_ci(self) -> None:
        self.assertEqual(self.row(scopes={"ci"})["dep_scope"], "ci")
        # Anything that also ships is runtime: a package present in an image
        # must not be filed as tooling because CI happens to pin it too.
        self.assertEqual(self.row(scopes={"ci", "runtime"})["dep_scope"], "runtime")

    def test_unknown_license_is_unknown_risk_not_permissive(self) -> None:
        row = self.row(license="UNKNOWN")
        self.assertEqual(row["risk"], osrb_scan.RISK_UNKNOWN)

    def test_line_numbers_are_stripped_from_source_files(self) -> None:
        inventory = osrb_inventory.Inventory()
        inventory.add(
            package="grafana",
            version="11.0.0",
            module="deploy",
            language="container",
            evidence="container-image",
            source_file="deploy/docker/compose.yaml#L42",
            source_kind="compose",
        )
        # A committed state file must not churn every row below an unrelated
        # edit; the delta pipeline is where the line number belongs.
        self.assertEqual(inventory.entries()[0].source_files, {"deploy/docker/compose.yaml"})


class DedupeTest(unittest.TestCase):
    def test_a_manifest_row_yields_to_the_same_module_s_lockfile(self) -> None:
        inventory = osrb_inventory.Inventory()
        inventory.add(
            package="httpx", version="0.28.1", module="services/agent", language="python",
            evidence="declared-manifest", source_file="services/agent/uv.lock",
            source_kind=osrb_scan.KIND_LOCKFILE,
        )
        inventory.add(
            package="httpx", version=">=0.27", module="services/agent", language="python",
            evidence="declared-manifest", source_file="services/agent/pyproject.toml",
            source_kind=osrb_scan.KIND_MANIFEST,
        )
        self.assertEqual(inventory.drop_manifest_rows_covered_by_a_lockfile(), 1)
        self.assertEqual([e.version for e in inventory.entries()], ["0.28.1"])

    def test_one_module_s_lockfile_does_not_suppress_another_s_manifest(self) -> None:
        inventory = osrb_inventory.Inventory()
        inventory.add(
            package="httpx", version="0.28.1", module="services/agent", language="python",
            evidence="declared-manifest", source_file="services/agent/uv.lock",
            source_kind=osrb_scan.KIND_LOCKFILE,
        )
        inventory.add(
            package="httpx", version=">=0.27", module="services/alert", language="python",
            evidence="declared-manifest", source_file="services/alert/requirements.txt",
            source_kind=osrb_scan.KIND_MANIFEST,
        )
        # OSRB reviews per shipped artifact; services/alert has to declare its
        # own dependency, and suppressing the row would hide that it has not.
        self.assertEqual(inventory.drop_manifest_rows_covered_by_a_lockfile(), 0)
        self.assertEqual(len(inventory.entries()), 2)

    def test_container_evidence_survives_the_lockfile_dedupe(self) -> None:
        # A pip install inside a Dockerfile is a DIFFERENT use from a manifest
        # declaration even for the same package, and use is what OSRB approves.
        inventory = osrb_inventory.Inventory()
        inventory.add(
            package="requests", version="2.32.3", module="services/agent", language="python",
            evidence="declared-manifest", source_file="services/agent/uv.lock",
            source_kind=osrb_scan.KIND_LOCKFILE,
        )
        inventory.add(
            package="requests", version="", module="services/agent", language="python",
            evidence="container-pip", source_file="services/agent/Dockerfile",
            source_kind=osrb_scan.KIND_CONTAINER,
        )
        self.assertEqual(inventory.drop_manifest_rows_covered_by_a_lockfile(), 0)
        self.assertEqual(len(inventory.entries()), 2)


class CommittedInventoryTest(unittest.TestCase):
    """Invariants of the CSV that is actually committed in this repo."""

    def setUp(self) -> None:
        if not COMMITTED_INVENTORY.exists():
            self.skipTest("inventory.csv has not been generated yet")
        self.raw = COMMITTED_INVENTORY.read_bytes()
        with COMMITTED_INVENTORY.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            self.header = reader.fieldnames
            self.rows = list(reader)

    def test_header_matches_the_module(self) -> None:
        self.assertEqual(self.header, osrb_inventory.COLUMNS)

    def test_rows_are_sorted(self) -> None:
        keys = [osrb_inventory.sort_key(row) for row in self.rows]
        self.assertEqual(keys, sorted(keys))

    def test_the_sort_key_is_total(self) -> None:
        # Two rows sharing a sort key would be ordered by whichever the sort
        # happened to see first, which is the definition of a non-deterministic
        # file.
        keys = [osrb_inventory.sort_key(row) for row in self.rows]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_cell_uses_the_closed_vocabularies(self) -> None:
        for row in self.rows:
            for evidence in row["usage_evidence"].split(";"):
                self.assertIn(evidence, osrb_inventory.USAGE_EVIDENCE, row)
            self.assertIn(row["dep_scope"], {"runtime", "ci"}, row)
            self.assertIn(row["vendored_in_repo"], {"yes", "no"}, row)
            self.assertIn(row["container_only"], {"yes", "no"}, row)
            self.assertIn(row["copied_adapted"], {"yes", "no", "UNKNOWN"}, row)
            self.assertIn(
                row["risk"],
                {
                    osrb_scan.RISK_NONE,
                    osrb_scan.RISK_MEDIUM,
                    osrb_scan.RISK_HIGH,
                    osrb_scan.RISK_UNKNOWN,
                },
                row,
            )

    def test_multi_valued_cells_are_sorted_and_deduplicated(self) -> None:
        for row in self.rows:
            for column in ("usage_evidence", "source_kind", "source_file"):
                parts = row[column].split(";")
                self.assertEqual(parts, sorted(set(parts)), (column, row["package"]))

    def test_no_absolute_paths_and_no_line_numbers(self) -> None:
        for row in self.rows:
            for path in row["source_file"].split(";"):
                self.assertFalse(path.startswith("/"), row)
                self.assertNotIn("#L", path, row)

    def test_written_with_lf_endings(self) -> None:
        self.assertNotIn(b"\r\n", self.raw)


class DeterminismTest(unittest.TestCase):
    """Same tree in, same bytes out — the requirement that makes this file diffable."""

    def test_two_runs_produce_identical_bytes(self) -> None:
        if not repo_paths():
            self.skipTest("git is unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            outputs = [os.path.join(tmp, "a.csv"), os.path.join(tmp, "b.csv")]
            processes = []
            # Different PYTHONHASHSEED in each process: string hashing is
            # randomised per process, so any place this module iterated a set
            # instead of sorting it would diverge here and nowhere else.
            for output, seed in zip(outputs, ("0", "524287")):
                env = {**os.environ, "PYTHONHASHSEED": seed}
                processes.append(
                    subprocess.Popen(
                        [sys.executable, str(MODULE_PATH), "--ref", "HEAD", "--output", output],
                        cwd=str(REPO_ROOT),
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                )
            for process in processes:
                self.assertEqual(process.wait(), 0)
            digests = [hashlib.sha256(Path(o).read_bytes()).hexdigest() for o in outputs]
            self.assertEqual(digests[0], digests[1])
            self.assertGreater(Path(outputs[0]).stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
