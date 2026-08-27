#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the OSRB state comparison.

Two kinds of test live here. Most are unit tests over small hand-built
baselines. The last class runs against the real ``approved.csv`` committed
next to this file, because the failures this comparator has to survive --
``0.0`` in a usage column, one answer spelled two ways, 700 rows saying
"Other" -- are properties of that specific export and would not survive being
paraphrased into a fixture.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("osrb_compare.py")
MODULE_SPEC = importlib.util.spec_from_file_location("osrb_compare", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
compare_mod = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(compare_mod)

MAP_PATH = Path(__file__).with_name("module_map.py")
MAP_SPEC = importlib.util.spec_from_file_location("module_map", MAP_PATH)
assert MAP_SPEC is not None and MAP_SPEC.loader is not None
module_map = importlib.util.module_from_spec(MAP_SPEC)
MAP_SPEC.loader.exec_module(module_map)

APPROVED_CSV = Path(__file__).with_name("approved.csv")


def approved_row(
    package: str = "demo",
    *,
    version: str = "1.0.0",
    license: str = "MIT",
    module: str = "AGENT",
    repo_modules: str = "services/agent",
    vendored: str = "",
    distribution_method: str = "Container - Added to Base Container listed in Row 1",
    usage_method: str = "Pre-installed inside the container",
    comments: str = "",
) -> dict[str, str]:
    return {
        "package": package,
        "version": version,
        "license": license,
        "module": module,
        "vendored": vendored,
        "downloaded_at_build": "",
        "distribution_method": distribution_method,
        "usage_method": usage_method,
        "comments": comments,
        "license_link": "",
        "download_location": "",
        "repo_modules": repo_modules,
    }


def inventory_row(
    package: str = "demo",
    *,
    version: str = "1.0.0",
    license: str = "MIT",
    module: str = "services/agent",
    language: str = "python",
    usage_evidence: str = "",
    source_kind: str = "lockfile",
    source_file: str = "services/agent/uv.lock",
    risk: str = "",
) -> dict[str, str]:
    return {
        "package": package,
        "version": version,
        "license": license,
        "module": module,
        "language": language,
        "usage_evidence": usage_evidence,
        "source_kind": source_kind,
        "source_file": source_file,
        "risk": risk,
    }


def verdict_of(inventory: dict[str, str], approved: list[dict[str, str]]) -> str:
    index = compare_mod.ApprovedIndex(approved)
    return compare_mod.classify(inventory, index)["verdict"]


def judge(inventory: dict[str, str], approved: list[dict[str, str]]) -> dict[str, str]:
    return compare_mod.classify(inventory, compare_mod.ApprovedIndex(approved))


class MatchingTest(unittest.TestCase):
    def test_exact_match_is_approved(self) -> None:
        self.assertEqual(verdict_of(inventory_row(), [approved_row()]), "APPROVED")

    def test_different_version_is_version_drift(self) -> None:
        row = judge(inventory_row(version="2.0.0"), [approved_row(version="1.0.0")])
        self.assertEqual(row["verdict"], "VERSION_DRIFT")
        # The approved version has to be IN the row: a reviewer's next move is
        # deciding whether to re-submit or roll back, and both need the number.
        self.assertIn("1.0.0", row["notes"])
        self.assertEqual(row["approved_version"], "1.0.0")

    def test_version_drift_beats_a_matching_package_in_another_module(self) -> None:
        # Approved for LVS at 2.0.0; the repo has it in AGENT at 2.0.0. That is
        # NOT_APPROVED for AGENT, not an approval borrowed across modules.
        approved = [
            approved_row(
                version="2.0.0", module="LVS", repo_modules="services/video-summarization"
            )
        ]
        self.assertEqual(verdict_of(inventory_row(version="2.0.0"), approved), "NOT_APPROVED")

    def test_package_name_spellings_fold_together(self) -> None:
        # PyPI treats these as one package and so must we, or every
        # underscore-spelled lock entry becomes a phantom finding.
        self.assertEqual(
            verdict_of(inventory_row(package="Ruamel.Yaml"), [approved_row(package="ruamel-yaml")]),
            "APPROVED",
        )

    def test_v_prefix_on_a_version_is_not_drift(self) -> None:
        self.assertEqual(
            verdict_of(inventory_row(version="v1.0.0"), [approved_row(version="1.0.0")]),
            "APPROVED",
        )

    def test_distro_version_is_compared_literally(self) -> None:
        approved = [approved_row(package="libslang2", version="2.3.3-3build2")]
        self.assertEqual(
            verdict_of(inventory_row(package="libslang2", version="2.3.3-3build2"), approved),
            "APPROVED",
        )
        self.assertEqual(
            verdict_of(inventory_row(package="libslang2", version="2.3.3-3build1"), approved),
            "VERSION_DRIFT",
        )

    def test_an_exact_pin_matches_the_resolved_version(self) -> None:
        # A requirements.txt `==1.0.0` and a lockfile `1.0.0` are one fact
        # written twice; reporting drift between them is pure noise.
        self.assertEqual(
            verdict_of(inventory_row(version="==1.0.0"), [approved_row(version="1.0.0")]),
            "APPROVED",
        )

    def test_a_range_spec_is_not_reported_as_version_drift(self) -> None:
        # Manifests carry ranges by design. `httpx>=0.27.0` can never equal an
        # approved version string, so comparing it would put every manifest row
        # in the repo into VERSION_DRIFT and drown the real ones.
        row = judge(inventory_row(version=">=0.27.0"), [approved_row(version="1.0.0")])
        self.assertEqual(row["verdict"], "APPROVED")
        self.assertIn("version not compared", row["notes"])

    def test_a_range_spec_still_fails_the_package_gate(self) -> None:
        # Leniency about the version must not become leniency about approval.
        self.assertEqual(
            verdict_of(inventory_row(package="ghost", version=">=1.0"), [approved_row()]),
            "NOT_APPROVED",
        )

    def test_range_detection(self) -> None:
        for spec in ["1.0.0", "==1.0.0", "v1.0.0", "2.3.3-3build2", "1.24.2-1ubuntu1.1"]:
            self.assertTrue(compare_mod.is_comparable_version(spec), spec)
        for spec in ["", ">=1.0", "^1.2.0", "~=1.2", "1.*", ">=1,<2", "1.0 - 2.0"]:
            self.assertFalse(compare_mod.is_comparable_version(spec), spec)

    def test_a_local_build_tag_difference_is_reported_and_labelled(self) -> None:
        # torch 2.10.0 vs 2.10.0+cpu. Still drift -- a +cpu and a +cu wheel
        # bundle different libraries -- but the note has to say the upstream
        # release is the same, or it reads as a bump nobody made.
        row = judge(
            inventory_row(package="torch", version="2.10.0+cpu"),
            [approved_row(package="torch", version="2.10.0")],
        )
        self.assertEqual(row["verdict"], "VERSION_DRIFT")
        self.assertIn("local build tag", row["notes"])

    def test_an_ordinary_version_bump_is_not_labelled_a_build_variant(self) -> None:
        row = judge(inventory_row(version="2.0.0"), [approved_row(version="1.0.0")])
        self.assertNotIn("local build tag", row["notes"])

    def test_an_approval_with_no_version_cannot_disagree_with_one(self) -> None:
        # 248 baseline rows record no version. Comparing against them produced
        # "approved at ; repo has 13.610.43" -- five findings a reviewer can do
        # nothing with, because the sheet never claimed a version.
        row = judge(inventory_row(version="13.610.43"), [approved_row(version="")])
        self.assertEqual(row["verdict"], "APPROVED")
        self.assertIn("records no version", row["notes"])

    def test_a_versioned_approval_still_wins_over_a_version_less_one(self) -> None:
        approved = [approved_row(version=""), approved_row(version="1.0.0", license="MIT")]
        row = judge(inventory_row(version="1.0.0"), approved)
        self.assertEqual(row["verdict"], "APPROVED")
        self.assertEqual(row["approved_version"], "1.0.0")

    def test_a_stated_version_that_matches_nothing_is_still_drift(self) -> None:
        row = judge(inventory_row(version="9.9.9"), [approved_row(version="1.0.0")])
        self.assertEqual(row["verdict"], "VERSION_DRIFT")

    def test_an_approval_covering_two_repo_modules_covers_both(self) -> None:
        approved = [
            approved_row(
                module="RTVI_VLM+RTVI_EMBED (container 3.2 GA)",
                repo_modules="services/rtvi/rt-vlm;services/rtvi/rt-embed",
            )
        ]
        for module in ("services/rtvi/rt-vlm", "services/rtvi/rt-embed"):
            self.assertEqual(verdict_of(inventory_row(module=module), approved), "APPROVED")

    def test_approval_under_any_submission_of_a_module_counts(self) -> None:
        # rt-vlm has three submissions. A package approved under only the GHCR
        # one is approved for rt-vlm; requiring all three would report drift
        # for a package nobody changed.
        approved = [
            approved_row(module="RTVI_VLM_GHCR", repo_modules="services/rtvi/rt-vlm"),
            approved_row(
                version="9.9.9",
                module="RTVI_VLM (source scope 3.2 GA)",
                repo_modules="services/rtvi/rt-vlm",
            ),
        ]
        self.assertEqual(
            verdict_of(inventory_row(module="services/rtvi/rt-vlm"), approved), "APPROVED"
        )


class LicenseTest(unittest.TestCase):
    def test_spelling_variants_are_not_drift(self) -> None:
        for found, approved in [
            ("Apache-2.0", "Apache 2.0"),
            ("Apache-2.0", "APACHE-2.0"),
            ("Apache-2.0", "Apache License"),
            ("BSD-3-Clause", "BSD (any variant)"),
            ("BSD-2-Clause", "BSD"),
            ("LGPL-2.1-or-later", "LGPL (Library or Lesser GPL)"),
            ("ISC", "ISC (Internet Software Consortium)"),
            ("MIT", "Expat"),
        ]:
            with self.subTest(found=found, approved=approved):
                self.assertTrue(compare_mod.licenses_compatible(found, approved))

    def test_a_real_licence_change_is_drift(self) -> None:
        row = judge(
            inventory_row(license="GPL-3.0-only"), [approved_row(license="MIT")]
        )
        self.assertEqual(row["verdict"], "LICENSE_DRIFT")
        self.assertIn("MIT", row["notes"])
        self.assertIn("GPL-3.0-only", row["notes"])
        # Risk travels with the row so the worst drift sorts to the top of a
        # reviewer's spreadsheet without them re-deriving it.
        self.assertEqual(row["risk"], "High")

    def test_lgpl_is_not_read_as_gpl(self) -> None:
        # "LGPL (Library or Lesser GPL)" contains the letters GPL. Reading it
        # as GPL would make all 281 LGPL rows in the baseline drift.
        self.assertEqual(compare_mod.license_families("LGPL (Library or Lesser GPL)"), {"LGPL"})
        self.assertFalse(compare_mod.licenses_compatible("GPL-2.0", "LGPL (Library or Lesser GPL)"))

    def test_indeterminate_sheet_values_never_drift(self) -> None:
        for value in [
            "",
            "0.0",
            "Other (Please describe in Comments)",
            "More than one license (Please specify in Comments)",
            "UNKNOWN",
            "NOASSERTION",
        ]:
            with self.subTest(value=value):
                self.assertEqual(compare_mod.license_families(value), frozenset())
                self.assertTrue(compare_mod.licenses_compatible("MIT", value))
                self.assertTrue(compare_mod.licenses_compatible(value, "MIT"))

    def test_composite_expression_matches_on_any_shared_family(self) -> None:
        self.assertTrue(compare_mod.licenses_compatible("Apache-2.0", "Apache-2.0 AND BSD-3-Clause"))
        self.assertFalse(compare_mod.licenses_compatible("GPL-2.0", "Apache-2.0 AND BSD-3-Clause"))


class UsageDriftTest(unittest.TestCase):
    """The check this file exists for, and the one most able to ruin the report."""

    def test_blank_usage_method_never_drifts(self) -> None:
        row = judge(
            inventory_row(usage_evidence="vendored-source;shipped-in-container"),
            [approved_row(usage_method="", vendored="", distribution_method="")],
        )
        self.assertEqual(row["verdict"], "APPROVED")
        self.assertIn("not compared", row["notes"])

    def test_other_usage_method_never_drifts(self) -> None:
        row = judge(
            inventory_row(usage_evidence="vendored-source;shipped-in-container"),
            [
                approved_row(
                    usage_method="Other (Please describe in Comments)",
                    vendored="",
                    distribution_method="",
                )
            ],
        )
        self.assertEqual(row["verdict"], "APPROVED")
        self.assertIn("Other", row["notes"])

    def test_zero_point_zero_usage_method_never_drifts(self) -> None:
        # The six `0.0` rows are a spreadsheet artefact, not an answer.
        row = judge(
            inventory_row(usage_evidence="static-link;shipped-in-container"),
            [approved_row(usage_method="0.0", vendored="", distribution_method="")],
        )
        self.assertEqual(row["verdict"], "APPROVED")

    def test_unrecognised_evidence_never_drifts(self) -> None:
        row = judge(
            inventory_row(usage_evidence="smells-funny;probably-linked"),
            [approved_row(usage_method="Build-time dependency")],
        )
        self.assertEqual(row["verdict"], "APPROVED")

    def test_vendored_source_against_a_not_vendored_approval(self) -> None:
        row = judge(
            inventory_row(usage_evidence="vendored-source"),
            [approved_row(vendored="No")],
        )
        self.assertEqual(row["verdict"], "USAGE_DRIFT")
        self.assertIn("vendored", row["notes"])
        self.assertEqual(row["approved_vendored"], "No")

    def test_shipping_a_test_time_only_approval(self) -> None:
        row = judge(
            inventory_row(usage_evidence="shipped-in-container"),
            [approved_row(vendored="No, test-time only")],
        )
        self.assertEqual(row["verdict"], "USAGE_DRIFT")
        self.assertIn("test-time only", row["notes"])

    def test_a_sample_dockerfile_approval_is_not_contradicted_by_a_dockerfile(self) -> None:
        # Ran against the tree with a rule for this and it produced 12
        # findings, every one of them the sample Dockerfile the sheet is
        # describing. "Provided as part of sample dockerfile" answers a
        # question about a Dockerfile; finding the package in one confirms it.
        row = judge(
            inventory_row(
                usage_evidence="shipped-in-container",
                source_file="libs/analytics/spatialai-data-utils/docker/Dockerfile#L144",
            ),
            [approved_row(vendored="Provided as part of sample dockerfile", usage_method="")],
        )
        self.assertEqual(row["verdict"], "APPROVED")

    def test_shipping_a_build_time_only_approval(self) -> None:
        row = judge(
            inventory_row(usage_evidence="shipped-in-container"),
            [approved_row(usage_method="Build-time dependency", vendored="")],
        )
        self.assertEqual(row["verdict"], "USAGE_DRIFT")

    def test_static_linking_a_dynamically_linked_approval(self) -> None:
        row = judge(
            inventory_row(usage_evidence="static-link"),
            [approved_row(usage_method="Dynamic Linking", vendored="")],
        )
        self.assertEqual(row["verdict"], "USAGE_DRIFT")

    def test_shipping_something_the_sheet_says_is_removed_at_build(self) -> None:
        row = judge(
            inventory_row(usage_evidence="shipped-in-container"),
            [
                approved_row(
                    distribution_method=(
                        "Not distributed in the default NVIDIA image "
                        "(removed during image build)"
                    ),
                    usage_method="Optional operator-side installation only",
                )
            ],
        )
        self.assertEqual(row["verdict"], "USAGE_DRIFT")

    def test_declaring_something_removed_at_build_is_not_drift(self) -> None:
        # opencv-python-headless: resolved by uv, then deleted during the image
        # build. A lockfile entry is evidence of resolution, not of shipping,
        # and treating it as shipping manufactures a finding on a row whose
        # comments already explain it.
        row = judge(
            inventory_row(source_kind="lockfile", usage_evidence=""),
            [
                approved_row(
                    distribution_method=(
                        "Not distributed in the default NVIDIA image "
                        "(removed during image build)"
                    ),
                    usage_method="Optional operator-side installation only",
                )
            ],
        )
        self.assertEqual(row["verdict"], "APPROVED")

    def test_dynamic_linking_with_matching_evidence_is_approved(self) -> None:
        row = judge(
            inventory_row(usage_evidence="dynamic-link"),
            [approved_row(usage_method="Dynamic Linking", vendored="")],
        )
        self.assertEqual(row["verdict"], "APPROVED")
        self.assertIn("consistent with", row["notes"])

    def test_usage_is_only_checked_after_version_and_licence(self) -> None:
        # A row that drifts on both version and usage reports the version,
        # because that is the thing to fix first and one row must carry one
        # verdict.
        row = judge(
            inventory_row(version="2.0.0", usage_evidence="vendored-source"),
            [approved_row(version="1.0.0", vendored="No")],
        )
        self.assertEqual(row["verdict"], "VERSION_DRIFT")

    def test_any_fitting_approval_clears_the_row(self) -> None:
        # Two submissions cover rt-vlm: one says not vendored, one says
        # vendored within the container. A vendored copy matches the second,
        # so the row is approved rather than drifting against the first.
        approved = [
            approved_row(
                module="RTVI_VLM (source scope 3.2 GA)",
                repo_modules="services/rtvi/rt-vlm",
                vendored="No",
            ),
            approved_row(
                module="RTVI_VLM_GHCR",
                repo_modules="services/rtvi/rt-vlm",
                vendored="Yes, within container",
            ),
        ]
        self.assertEqual(
            verdict_of(
                inventory_row(module="services/rtvi/rt-vlm", usage_evidence="vendored-source"),
                approved,
            ),
            "APPROVED",
        )

    def test_every_conflict_rule_names_two_known_values(self) -> None:
        # The guard on noise: no rule may fire off `unspecified` or `other`,
        # and no rule may reference an evidence token the scanner cannot emit.
        inert = {
            compare_mod.USAGE_UNSPECIFIED,
            compare_mod.USAGE_OTHER,
            compare_mod.DIST_UNSPECIFIED,
            compare_mod.VENDORED_UNSPECIFIED,
        }
        for (axis, value), rules in compare_mod.USAGE_CONFLICTS.items():
            self.assertNotIn(value, inert, f"{axis}={value} must not produce drift")
            for token in rules:
                self.assertIn(token, compare_mod.EVIDENCE_VOCABULARY)


class EvidenceTest(unittest.TestCase):
    def test_evidence_column_wins_over_derivation(self) -> None:
        row = inventory_row(usage_evidence="static-link", source_kind="container")
        self.assertEqual(compare_mod.evidence_for(row), {"static-link"})

    def test_lockfile_derives_declared_not_shipped(self) -> None:
        self.assertEqual(
            compare_mod.evidence_for(inventory_row(source_kind="lockfile")), {"declared"}
        )

    def test_dockerfile_derives_shipped(self) -> None:
        self.assertEqual(
            compare_mod.evidence_for(
                inventory_row(source_kind="container", source_file="services/agent/Dockerfile")
            ),
            {"shipped-in-container"},
        )

    def test_a_vendored_path_derives_vendored_source(self) -> None:
        self.assertIn(
            "vendored-source",
            compare_mod.evidence_for(
                inventory_row(
                    source_kind="usage",
                    source_file="services/rtvi/rt-vlm/3rdparty/foo/foo.cpp#L12",
                )
            ),
        )

    def test_a_test_path_cancels_a_shipping_claim(self) -> None:
        evidence = compare_mod.evidence_for(
            inventory_row(source_kind="container", source_file="services/agent/tests/Dockerfile")
        )
        self.assertNotIn("shipped-in-container", evidence)
        self.assertIn("test-only", evidence)


class ModuleTest(unittest.TestCase):
    def test_unsubmitted_module_is_never_reported_as_not_approved(self) -> None:
        # The distinction decides whether someone files a new OSRB bug or
        # chases an existing one, so it must survive even when the package
        # would otherwise look like an ordinary miss.
        row = judge(inventory_row(module="services/sdrc"), [approved_row()])
        self.assertEqual(row["verdict"], "MODULE_UNSUBMITTED")
        self.assertIn("new OSRB bug", row["notes"])

    def test_unsubmitted_wins_even_when_a_package_name_matches_elsewhere(self) -> None:
        approved = [approved_row(package="demo")]
        self.assertEqual(
            verdict_of(inventory_row(package="demo", module="libs/nvschema"), approved),
            "MODULE_UNSUBMITTED",
        )

    def test_an_unsubmitted_subtree_beats_its_submitted_parent_module(self) -> None:
        # services/vios IS submitted (325 approved rows) but
        # services/vios/ui/vios-ui is on the unsubmitted list, and ownership
        # rounds the second to the first. Matching on the module name alone
        # put 734 VIOS-UI rows into NOT_APPROVED, pointing every reader at an
        # OSRB bug about a different piece of software.
        row = judge(
            inventory_row(
                module="services/vios",
                source_file="services/vios/ui/vios-ui/package-lock.json",
            ),
            [approved_row(module="VIOS_VST_V2.1 [main package]", repo_modules="services/vios")],
        )
        self.assertEqual(row["verdict"], "MODULE_UNSUBMITTED")
        self.assertIn("services/vios/ui/vios-ui", row["notes"])

    def test_the_submitted_part_of_that_module_is_still_compared(self) -> None:
        # The subtree rule must not swallow the module it sits in.
        row = judge(
            inventory_row(
                module="services/vios", source_file="services/vios/src/package-lock.json"
            ),
            [approved_row(module="VIOS_VST_V2.1 [main package]", repo_modules="services/vios")],
        )
        self.assertEqual(row["verdict"], "APPROVED")

    def test_unsubmitted_subtrees_that_ownership_can_never_report(self) -> None:
        # Five UNSUBMITTED entries are paths owning_module() never returns, so
        # a name-only check silently ignores them. Pin the behaviour that
        # rescues them.
        for module, path in [
            ("services/vios", "services/vios/ui/streaming-lib/package-lock.json"),
            ("deploy", "deploy/docker/services/infra/docker-compose.yml"),
            ("deploy", "deploy/helm/services/monitoring/Chart.yaml"),
            ("docs", "docs/smartcity-docs/package.json"),
        ]:
            with self.subTest(path=path):
                self.assertTrue(compare_mod.unsubmitted_scope(module, path), path)

    def test_a_multi_path_citation_needs_every_path_unsubmitted(self) -> None:
        # The inventory merges every file that declares a package into one
        # `;`-joined cell. A package used by the unsubmitted VIOS UI *and* the
        # submitted VIOS backend still has to be judged against the backend's
        # approvals.
        both = (
            "services/vios/ui/vios-ui/package-lock.json;services/vios/src/package-lock.json"
        )
        self.assertEqual(compare_mod.unsubmitted_scope("services/vios", both), "")
        only_ui = (
            "services/vios/ui/vios-ui/package-lock.json;"
            "services/vios/ui/streaming-lib/package-lock.json"
        )
        self.assertEqual(
            compare_mod.unsubmitted_scope("services/vios", only_ui),
            "services/vios/ui/vios-ui",
        )

    def test_a_module_in_neither_list_says_where_the_fix_goes(self) -> None:
        row = judge(inventory_row(module="services/brand-new"), [approved_row()])
        self.assertEqual(row["verdict"], "MODULE_UNSUBMITTED")
        self.assertIn("module_map.py", row["notes"])

    def test_missing_package_in_a_submitted_module_is_not_approved(self) -> None:
        row = judge(inventory_row(package="ghost"), [approved_row(package="demo")])
        self.assertEqual(row["verdict"], "NOT_APPROVED")

    def test_every_unsubmitted_module_is_absent_from_the_map(self) -> None:
        # The two lists answer the same question and must not both claim a
        # module; if they did, the verdict would depend on evaluation order.
        overlap = module_map.SUBMITTED_MODULES & module_map.UNSUBMITTED_SET
        self.assertEqual(overlap, frozenset())


class StaleApprovalTest(unittest.TestCase):
    def test_missing_package_in_a_scanned_module_is_reported(self) -> None:
        rows = compare_mod.compare(
            [inventory_row(package="present")],
            compare_mod.ApprovedIndex(
                [approved_row(package="present"), approved_row(package="gone")]
            ),
        )
        stale = [row for row in rows if row["verdict"] == "APPROVED_NOT_PRESENT"]
        self.assertEqual([row["package"] for row in stale], ["gone"])

    def test_unscanned_modules_produce_no_stale_rows(self) -> None:
        # Scanning one service must not report every package of the other
        # twenty as vanished; 3,000 informational rows would bury the report.
        rows = compare_mod.compare(
            [inventory_row(package="present")],
            compare_mod.ApprovedIndex(
                [
                    approved_row(package="present"),
                    approved_row(package="elsewhere", module="LVS",
                                repo_modules="services/video-summarization"),
                ]
            ),
        )
        self.assertEqual([row["verdict"] for row in rows if row["package"] == "elsewhere"], [])


class NormalisationTest(unittest.TestCase):
    def test_both_container_spellings_normalise_together(self) -> None:
        self.assertEqual(
            compare_mod.normalise_usage_method("Pre installed inside of container"),
            compare_mod.normalise_usage_method("Pre-installed inside the container"),
        )

    def test_zero_point_zero_is_unspecified_not_a_value(self) -> None:
        self.assertEqual(compare_mod.normalise_usage_method("0.0"), compare_mod.USAGE_UNSPECIFIED)
        self.assertEqual(compare_mod.normalise_vendored("0.0"), compare_mod.VENDORED_UNSPECIFIED)

    def test_an_unknown_usage_value_is_counted_not_swallowed(self) -> None:
        seen = compare_mod.Normalisations()
        compare_mod.normalise_usage_method("Dynamic Link (sort of)", seen)
        self.assertEqual(
            seen.unmapped["usage_method"]["Dynamic Link (sort of)"],
            1,
            "an unmapped value silently disables the usage check for those rows",
        )

    def test_census_covers_the_whole_sheet(self) -> None:
        seen = compare_mod.normalisation_census(
            [approved_row(usage_method="0.0"), approved_row(usage_method="Dynamic Linking")]
        )
        self.assertEqual(seen.applied["usage_method"][("0.0", "unspecified")], 1)


class InventoryReadingTest(unittest.TestCase):
    def _write(self, text: str) -> str:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, encoding="utf-8"
        )
        handle.write(text)
        handle.close()
        return handle.name

    def test_scan_column_names_are_accepted(self) -> None:
        # The scan CSV calls these new_version/new_license. Refusing them would
        # mean the two halves of the pipeline cannot be piped together.
        path = self._write(
            "language,package,new_version,new_license,module,source_kind,source_file\n"
            "python,demo,1.0.0,MIT,services/agent,lockfile,services/agent/uv.lock\n"
        )
        rows = compare_mod.load_inventory(path)
        self.assertEqual(rows[0]["version"], "1.0.0")
        self.assertEqual(rows[0]["license"], "MIT")

    def test_a_missing_required_column_raises(self) -> None:
        # Silently comparing against a column that is not there would report
        # every package in the repo as unapproved, which reads like a crisis.
        path = self._write("package,license\ndemo,MIT\n")
        with self.assertRaises(compare_mod.InventoryError):
            compare_mod.load_inventory(path)

    def test_rows_without_a_package_name_are_dropped(self) -> None:
        path = self._write("package,version,module\n,1.0,services/agent\nx,1.0,services/agent\n")
        self.assertEqual([row["package"] for row in compare_mod.load_inventory(path)], ["x"])


class OutputTest(unittest.TestCase):
    def test_csv_columns_are_append_only(self) -> None:
        # Pinned because a workflow uploads this file and a downstream reader
        # addresses it by column name.
        self.assertEqual(
            compare_mod.OUTPUT_FIELDS[:8],
            [
                "verdict",
                "module",
                "language",
                "package",
                "version",
                "license",
                "risk",
                "usage_evidence",
            ],
        )

    def test_github_output_carries_one_key_per_verdict(self) -> None:
        counts = compare_mod.count_verdicts(
            [
                {"verdict": "APPROVED"},
                {"verdict": "NOT_APPROVED"},
                {"verdict": "USAGE_DRIFT"},
                {"verdict": "APPROVED_NOT_PRESENT"},
            ]
        )
        with tempfile.NamedTemporaryFile("r+", delete=False) as handle:
            path = handle.name
        compare_mod.write_github_output(counts, path)
        written = dict(
            line.split("=", 1) for line in Path(path).read_text().splitlines() if line
        )
        for verdict in compare_mod.VERDICTS:
            self.assertIn(verdict.lower(), written)
        self.assertEqual(written["not_approved"], "1")
        self.assertEqual(written["usage_drift"], "1")
        # A stale approval is not a finding: gating on it would block a release
        # for a row somebody forgot to delete from a spreadsheet.
        self.assertEqual(written["findings"], "2")
        self.assertEqual(written["total"], "4")

    def test_github_output_appends(self) -> None:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as handle:
            handle.write("preexisting=1\n")
            path = handle.name
        compare_mod.write_github_output(compare_mod.count_verdicts([]), path)
        self.assertIn("preexisting=1", Path(path).read_text())

    def test_summary_names_the_normalisations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "s.md"
            seen = compare_mod.normalisation_census([approved_row(usage_method="0.0")])
            compare_mod.write_summary(
                [],
                compare_mod.count_verdicts([]),
                seen,
                summary,
                inventory_rows=0,
                approved_rows=1,
                warnings=[],
            )
            text = summary.read_text()
            self.assertIn("0.0", text)
            self.assertIn("unspecified", text)


class RealBaselineTest(unittest.TestCase):
    """Properties of the committed approved.csv, not of a fixture."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = compare_mod.load_approved(APPROVED_CSV)

    def test_baseline_is_the_expected_size(self) -> None:
        self.assertEqual(len(self.rows), 3877)

    def test_every_osrb_label_maps_to_repo_modules(self) -> None:
        self.assertEqual(
            module_map.unmapped_osrb_modules([row["module"] for row in self.rows]),
            [],
            "an unmapped label makes its approvals unreachable, inflating NOT_APPROVED",
        )

    def test_derived_column_agrees_with_the_module_map(self) -> None:
        self.assertEqual(module_map.check_repo_modules_column(self.rows), [])

    def test_every_row_lands_under_at_least_one_repo_module(self) -> None:
        index = compare_mod.ApprovedIndex(self.rows)
        self.assertEqual(index.rows_without_module, [])

    def test_the_known_data_quality_defects_are_neutralised(self) -> None:
        broken = [row for row in self.rows if row["usage_method"] == "0.0"]
        self.assertEqual(len(broken), 6, "the baseline's known 0.0 rows")
        for row in broken:
            self.assertEqual(
                compare_mod.normalise_usage_method(row["usage_method"]),
                compare_mod.USAGE_UNSPECIFIED,
            )
            self.assertEqual(
                compare_mod.usage_conflicts(
                    set(compare_mod.EVIDENCE_VOCABULARY), row
                ),
                [],
                "a 0.0 cell must not be able to contradict anything",
            )

    def test_no_usage_value_in_the_baseline_is_unmapped(self) -> None:
        seen = compare_mod.normalisation_census(self.rows)
        self.assertEqual(
            dict(seen.unmapped),
            {},
            "an unmapped value means the usage check silently stopped running",
        )

    def test_an_approved_row_round_trips_to_approved(self) -> None:
        # Take real rows off the sheet, feed them back as if the repo held
        # exactly them, and require APPROVED. Anything else is a false
        # positive by construction.
        # Excludes inline-addition rows on purpose. Those are packages someone
        # added in a bug comment, and a module whose ONLY approvals are inline
        # is reported MODULE_UNSUBMITTED by design -- the two @img/sharp rows
        # under services/ui are exactly that, and feeding them here would
        # assert the behaviour this comparator deliberately does not have.
        sample = [
            row
            for row in self.rows
            if row["repo_modules"]
            and row["version"]
            and row.get("provenance", "submission") != "inline-addition"
        ][:400]
        index = compare_mod.ApprovedIndex(self.rows)
        for row in sample:
            module = module_map.split_repo_modules(row["repo_modules"])[0]
            verdict = compare_mod.classify(
                inventory_row(
                    package=row["package"],
                    version=row["version"],
                    license=row["license"],
                    module=module,
                    source_kind="lockfile",
                    source_file="",
                ),
                index,
            )["verdict"]
            self.assertEqual(
                verdict, "APPROVED", f"{row['package']} {row['version']} in {module}"
            )


    def test_an_inline_only_module_reports_unsubmitted_not_approved(self) -> None:
        """The counterpart, asserted against the real baseline.

        services/ui holds two approved rows, both inline additions from a bug
        comment against 2157 resolved npm packages. Treating that as an OSRB
        submission reported 1350 packages as individually NOT_APPROVED and
        buried every other module's findings. It is one missing submission.
        """
        index = compare_mod.ApprovedIndex(self.rows)
        self.assertTrue(index.is_inline_only("services/ui"))

        verdict = compare_mod.classify(
            inventory_row(
                package="@img/sharp-freebsd-wasm32",
                version="0.35.3",
                license="Apache-2.0",
                module="services/ui",
                source_kind="lockfile",
                source_file="services/ui/package-lock.json",
            ),
            index,
        )
        self.assertEqual(verdict["verdict"], "MODULE_UNSUBMITTED")
        self.assertIn("no OSRB submission", verdict["notes"])

    def test_a_genuinely_submitted_module_is_not_inline_only(self) -> None:
        # Guard against the check being so broad it swallows real submissions.
        index = compare_mod.ApprovedIndex(self.rows)
        for module in ("services/agent", "services/vios", "services/rtvi/rt-vlm"):
            self.assertFalse(index.is_inline_only(module), module)


if __name__ == "__main__":
    unittest.main()
