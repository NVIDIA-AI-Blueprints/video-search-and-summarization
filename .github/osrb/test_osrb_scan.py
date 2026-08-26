#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the OSRB Scan declaration-side inventory.

Two kinds of test live here and both are load-bearing:

* fixture tests, which pin a parser's contract against a small hand-written
  file, and
* real-tree tests, which run the same code over this repository's actual
  lockfiles, manifests and build files.

The real-tree tests exist because every silent failure this scanner has had
was invisible to a fixture: `httpx` was dropped by a prefix check no synthetic
requirements.txt happened to trigger, and a `pdm.lock` went unread because no
fixture was named that. A parser that only ever sees its own fixture is a
parser nobody has checked against the repo it protects.
"""

from __future__ import annotations

import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("osrb_scan.py")
MODULE_SPEC = importlib.util.spec_from_file_location("osrb_scan", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
osrb_scan = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(osrb_scan)

REPO_ROOT = Path(__file__).parents[2]


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


class ParseUvLockTest(unittest.TestCase):
    def test_agent_lock_excludes_development_only_packages(self) -> None:
        lock_path = Path(__file__).parents[2] / "services" / "agent" / "uv.lock"

        inventory = osrb_scan.parse_uv_lock(lock_path.read_bytes())
        names = {name for name, _version in inventory}

        self.assertTrue(names.isdisjoint({"coverage", "mypy", "pytest", "ruff"}))
        # The shipping agent stack lives behind the root project's `agent`
        # extra; following root extras must keep it in the OSRB inventory.
        self.assertIn("nvidia-nat", names)

    def test_includes_extras_of_the_root_project(self) -> None:
        lock = b'''version = 1

[[package]]
name = "light-dependency"
version = "1.0.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "agent-only-dependency"
version = "2.0.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "sample-project"
version = "0.1.0"
source = { editable = "." }
dependencies = [
    { name = "light-dependency" },
]

[package.optional-dependencies]
agent = [
    { name = "agent-only-dependency" },
]
'''

        inventory = osrb_scan.parse_uv_lock(lock)

        self.assertEqual(
            {("light-dependency", "1.0.0"), ("agent-only-dependency", "2.0.0")},
            set(inventory),
        )

    def test_includes_runtime_closure_but_excludes_dev_dependencies_and_root(self) -> None:
        lock = b'''version = 1

[[package]]
name = "runtime-dependency"
version = "1.2.3"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "test-runner"
version = "9.9.9"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "sample-project"
version = "0.1.0"
source = { editable = "." }
dependencies = [
    { name = "runtime-dependency" },
]

[package.dev-dependencies]
dev = [
    { name = "test-runner" },
]
'''

        inventory = osrb_scan.parse_uv_lock(lock)

        self.assertEqual({("runtime-dependency", "1.2.3")}, set(inventory))

    def test_selects_the_version_referenced_by_a_forked_dependency(self) -> None:
        lock = b'''version = 1

[[package]]
name = "runtime-dependency"
version = "1.0.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "runtime-dependency"
version = "2.0.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "sample-project"
version = "0.1.0"
source = { editable = "." }
dependencies = [
    { name = "runtime-dependency", version = "2.0.0" },
]
'''

        inventory = osrb_scan.parse_uv_lock(lock)

        self.assertEqual({("runtime-dependency", "2.0.0")}, set(inventory))

    def test_includes_only_extras_requested_by_runtime_dependencies(self) -> None:
        lock = b'''version = 1

[[package]]
name = "base-dependency"
version = "1.0.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "enabled-extra-dependency"
version = "2.0.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "disabled-extra-dependency"
version = "3.0.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "runtime-dependency"
version = "1.0.0"
source = { registry = "https://pypi.org/simple" }
dependencies = [
    { name = "base-dependency" },
]

[package.optional-dependencies]
enabled = [
    { name = "enabled-extra-dependency" },
]
disabled = [
    { name = "disabled-extra-dependency" },
]

[[package]]
name = "sample-project"
version = "0.1.0"
source = { editable = "." }
dependencies = [
    { name = "runtime-dependency", extra = ["enabled"] },
]
'''

        inventory = osrb_scan.parse_uv_lock(lock)

        self.assertEqual(
            {
                ("base-dependency", "1.0.0"),
                ("enabled-extra-dependency", "2.0.0"),
                ("runtime-dependency", "1.0.0"),
            },
            set(inventory),
        )


class DiffRequirementsTest(unittest.TestCase):
    @mock.patch.object(osrb_scan, "pypi_metadata")
    def test_version_bump_resolves_both_license_versions(self, metadata: mock.Mock) -> None:
        metadata.side_effect = [
            {"license": "MIT", "repository_url": "https://example.com/old"},
            {"license": "MPL-2.0", "repository_url": "https://example.com/new"},
        ]

        rows = osrb_scan.diff_requirements(
            {"demo": "1.0.0"},
            {"demo": "2.0.0"},
            set(),
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["old_license"], "MIT")
        self.assertEqual(rows[0]["new_license"], "MPL-2.0")
        self.assertEqual(rows[0]["repository_url"], "https://example.com/new")
        self.assertIn("license changed", rows[0]["notes"])


class ParsePyprojectTest(unittest.TestCase):
    def test_reads_pep621_pins_and_skips_dev_extras(self) -> None:
        manifest = b'''
[project]
name = "sample"
dependencies = [
    "pillow==12.2.0",
    "requests>=2.32",
    "ray[default]==2.54.0",
]

[project.optional-dependencies]
dev = [
    "pytest==8.1.1",
]
'''

        inventory = osrb_scan.parse_pyproject(manifest)

        self.assertEqual(inventory["pillow"], "12.2.0")
        self.assertEqual(inventory["ray"], "2.54.0")
        self.assertEqual(inventory["requests"], "")
        self.assertNotIn("pytest", inventory)

    def test_reads_poetry_runtime_dependencies(self) -> None:
        manifest = b'''
[tool.poetry.dependencies]
python = ">=3.10,<4.0.0"
requests = "^2.31.0"
mcp = "1.23.0"
local-tool = {path = "."}
remote-tool = {git = "https://example.com/tool.git"}
'''

        inventory = osrb_scan.parse_pyproject(manifest)

        self.assertEqual(inventory["mcp"], "1.23.0")
        self.assertEqual(inventory["requests"], "")
        self.assertNotIn("python", inventory)
        self.assertNotIn("local-tool", inventory)
        self.assertNotIn("remote-tool", inventory)

    def test_lvs_py_deps_manifest_is_inventoried(self) -> None:
        path = (
            Path(__file__).parents[2]
            / "services"
            / "video-summarization"
            / "docker"
            / "base"
            / "py_deps"
            / "pyproject.toml"
        )

        inventory = osrb_scan.parse_pyproject(path.read_bytes())

        self.assertTrue(path.is_file())
        self.assertTrue(inventory["pillow"])
        self.assertTrue(inventory["urllib3"])
        self.assertIn("requests", inventory)


class ParsePdmLockTest(unittest.TestCase):
    def test_includes_default_group_and_excludes_dev(self) -> None:
        lock = b'''
[[package]]
name = "aiohttp"
version = "3.13.3"
groups = ["default"]

[[package]]
name = "pytest"
version = "8.1.1"
groups = ["dev"]

[[package]]
name = "ungrouped"
version = "1.0.0"
'''

        inventory = osrb_scan.parse_pdm_lock(lock)

        self.assertEqual(
            {("aiohttp", "3.13.3"), ("ungrouped", "1.0.0")},
            set(inventory),
        )


class ParsePoetryLockTest(unittest.TestCase):
    def test_includes_main_group_and_excludes_dev_only(self) -> None:
        lock = b'''
[[package]]
name = "requests"
version = "2.32.0"
groups = ["main"]

[[package]]
name = "black"
version = "25.1.0"
groups = ["dev"]

[[package]]
name = "shared"
version = "1.0.0"
groups = ["main", "dev"]

[[package]]
name = "legacy-dev"
version = "0.1.0"
category = "dev"
'''

        inventory = osrb_scan.parse_poetry_lock(lock)

        self.assertEqual(
            {("requests", "2.32.0"), ("shared", "1.0.0")},
            set(inventory),
        )


class DiffPyprojectTest(unittest.TestCase):
    @mock.patch.object(osrb_scan, "pypi_metadata")
    def test_new_pyproject_dependency_uses_source_note(self, metadata: mock.Mock) -> None:
        metadata.return_value = {
            "license": "MIT",
            "repository_url": "https://example.com/demo",
            "version": "1.0.0",
        }

        rows = osrb_scan.diff_requirements(
            {},
            {"demo": "1.0.0"},
            set(),
            source="pyproject.toml",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["change"], "added")
        self.assertEqual(rows[0]["package"], "demo")
        self.assertIn("pyproject.toml", rows[0]["notes"])


class ParseRequirementsHttpPrefixTest(unittest.TestCase):
    """The `http` prefix bug: package names are not URLs.

    `line.startswith("http")` was meant to skip `https://...` installs and
    instead deleted every package whose name starts with those four letters
    from BOTH refs, so the diff stayed empty and the drop was invisible.
    """

    def test_httpx_and_siblings_are_not_mistaken_for_urls(self) -> None:
        requirements = b"""httpx>=0.27.0
httpcore==1.0.9
httplib2==0.22.0
http-parser==0.9.0
requests==2.31.0
"""

        inventory = osrb_scan.parse_requirements(requirements)

        self.assertIn("httpx", inventory)
        self.assertEqual(inventory["httpx"], "")
        self.assertEqual(inventory["httpcore"], "1.0.9")
        self.assertIn("httplib2", inventory)
        self.assertIn("http-parser", inventory)

    def test_real_url_and_vcs_installs_are_still_skipped(self) -> None:
        requirements = b"""git+https://github.com/example/pkg.git#egg=pkg
hg+http://example.com/pkg
svn+ssh://example.com/pkg
bzr+lp:pkg
file:../local-wheel
https://example.com/pkg-1.0.tar.gz
s2wrapper @ https://github.com/bfshi/scaling_on_scales/archive/60da2af.zip
keepme==1.0.0
"""

        inventory = osrb_scan.parse_requirements(requirements)

        self.assertEqual({"keepme": "1.0.0"}, inventory)

    def test_alert_service_declares_httpx_in_the_real_tree(self) -> None:
        path = REPO_ROOT / "services" / "alert" / "requirements.txt"
        self.assertTrue(path.is_file())

        inventory = osrb_scan.parse_requirements(path.read_bytes())

        self.assertIn("httpx", inventory)

    def test_rt_embed_pins_httpcore_and_httpx_in_the_real_tree(self) -> None:
        path = (
            REPO_ROOT
            / "services" / "rtvi" / "rt-embed" / "docker" / "py_deps" / "requirements.txt"
        )
        self.assertTrue(path.is_file())

        inventory = osrb_scan.parse_requirements(path.read_bytes())

        self.assertEqual(inventory["httpcore"], "1.0.9")
        self.assertEqual(inventory["httpx"], "0.28.1")

    def test_pyproject_inherits_the_fix_through_pep621(self) -> None:
        manifest = b'''
[project]
name = "sample"
dependencies = ["httpx>=0.27.0"]
'''

        self.assertIn("httpx", osrb_scan.parse_pyproject(manifest))


class LicenseRiskTest(unittest.TestCase):
    def test_permissive_licenses_are_no_risk(self) -> None:
        for expr in ("MIT", "MIT License", "Apache-2.0", "Apache Software License",
                     "BSD-3-Clause", "0BSD", "ISC", "The Unlicense", "CC0-1.0",
                     "PSF-2.0", "Zlib", "BSL-1.0", "Public Domain"):
            with self.subTest(expr=expr):
                self.assertEqual(osrb_scan.RISK_NONE, osrb_scan.license_risk(expr))

    def test_weak_copyleft_is_medium(self) -> None:
        for expr in ("LGPL-2.1-only", "LGPL-3.0-or-later", "MPL-2.0",
                     "Mozilla Public License 2.0", "EPL-2.0", "CDDL-1.1",
                     "OSL-3.0", "AFL-2.1", "MS-PL", "FreeType", "Artistic-2.0"):
            with self.subTest(expr=expr):
                self.assertEqual(osrb_scan.RISK_MEDIUM, osrb_scan.license_risk(expr))

    def test_strong_copyleft_and_proprietary_are_high(self) -> None:
        for expr in ("GPL-2.0-or-later", "GPL-3.0", "AGPL-3.0", "SSPL-1.0",
                     "Elastic-2.0", "BUSL-1.1", "Business Source License 1.1",
                     "Commons-Clause", "RSALv2", "Confluent Community License",
                     "NVIDIA Software License", "Proprietary",
                     "CC-BY-NC-4.0", "non-commercial", "research-only"):
            with self.subTest(expr=expr):
                self.assertEqual(osrb_scan.RISK_HIGH, osrb_scan.license_risk(expr))

    def test_lgpl_is_not_read_as_gpl(self) -> None:
        """The substring trap: "LGPL" contains "GPL".

        Classifying LGPL as High would make the High bucket meaningless — half
        the C libraries in any image are LGPL — and reviewers would start
        skimming past the rows that actually matter.
        """
        self.assertEqual(osrb_scan.RISK_MEDIUM, osrb_scan.license_risk("LGPL-2.1"))
        self.assertEqual(osrb_scan.RISK_HIGH, osrb_scan.license_risk("GPL-2.1"))

    def test_boost_bsl_is_not_read_as_business_source_busl(self) -> None:
        """BSL-1.0 (Boost, permissive) and BUSL-1.1 (source-available) differ by
        one letter and by everything that matters legally."""
        self.assertEqual(osrb_scan.RISK_NONE, osrb_scan.license_risk("BSL-1.0"))
        self.assertEqual(osrb_scan.RISK_HIGH, osrb_scan.license_risk("BUSL-1.1"))

    def test_composite_takes_the_worst_operand(self) -> None:
        self.assertEqual(
            osrb_scan.RISK_HIGH, osrb_scan.license_risk("MIT AND GPL-2.0-or-later")
        )
        self.assertEqual(osrb_scan.RISK_HIGH, osrb_scan.license_risk("MIT OR AGPL-3.0"))
        self.assertEqual(
            osrb_scan.RISK_HIGH, osrb_scan.license_risk("Apache-2.0 WITH Commons-Clause")
        )
        self.assertEqual(osrb_scan.RISK_MEDIUM, osrb_scan.license_risk("MIT OR LGPL-2.1"))
        self.assertEqual(osrb_scan.RISK_NONE, osrb_scan.license_risk("MIT OR Apache-2.0"))
        self.assertEqual(osrb_scan.RISK_NONE, osrb_scan.license_risk("MIT/Apache-2.0"))

    def test_unrecognised_or_absent_is_unknown(self) -> None:
        self.assertEqual(osrb_scan.RISK_UNKNOWN, osrb_scan.license_risk(""))
        self.assertEqual(osrb_scan.RISK_UNKNOWN, osrb_scan.license_risk("   "))
        self.assertEqual(
            osrb_scan.RISK_UNKNOWN, osrb_scan.license_risk("see LICENSE in the tarball")
        )

    def test_permissive_operand_does_not_vouch_for_an_unknown_one(self) -> None:
        self.assertEqual(
            osrb_scan.RISK_UNKNOWN, osrb_scan.license_risk("MIT AND Bespoke-Terms-9000")
        )


class OwningModuleTest(unittest.TestCase):
    def test_component_directories(self) -> None:
        cases = {
            "services/agent/uv.lock": "services/agent",
            "services/alert/requirements.txt": "services/alert",
            "services/rtvi/rt-embed/docker/py_deps/requirements.txt":
                "services/rtvi/rt-embed",
            "services/rtvi/rt-vlm/LICENSE.3rdparty": "services/rtvi/rt-vlm",
            "services/rtvi/rt-cv-3d/rt-cv-mv3dt/3rdParty_Licenses.md":
                "services/rtvi/rt-cv-3d/rt-cv-mv3dt",
            "services/analytics/behavior-analytics/setup.py":
                "services/analytics/behavior-analytics",
            "services/configurators/vss-configurator/3rdParty_Licenses.md":
                "services/configurators/vss-configurator",
            "libs/analytics/spatialai-data-utils/Pipfile":
                "libs/analytics/spatialai-data-utils",
            "libs/nvschema/pyproject.toml": "libs/nvschema",
            "tools/logstash-plugins/input/redis-stream/build.gradle":
                "tools/logstash-plugins",
            "skills/some-skill/SKILL.md": "skills/some-skill",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(expected, osrb_scan.owning_module(path))

    def test_deploy_and_github_are_single_modules(self) -> None:
        self.assertEqual(
            "deploy", osrb_scan.owning_module("deploy/helm/services/rtvi/Chart.yaml")
        )
        self.assertEqual("deploy", osrb_scan.owning_module("deploy/docker/compose.yml"))
        self.assertEqual(
            ".github", osrb_scan.owning_module(".github/workflows/osrb-scan.yml")
        )

    def test_a_file_directly_inside_a_container_directory_belongs_to_it(self) -> None:
        self.assertEqual(
            "services/rtvi/rt-cv-3d",
            osrb_scan.owning_module("services/rtvi/rt-cv-3d/README.md"),
        )

    def test_top_level_files_and_unknown_trees(self) -> None:
        self.assertEqual("<root>", osrb_scan.owning_module("LICENSE-3rd-party.txt"))
        self.assertEqual("<root>", osrb_scan.owning_module("pyproject.toml"))
        self.assertEqual("docs", osrb_scan.owning_module("docs/guides/overview.md"))
        self.assertEqual("fern", osrb_scan.owning_module("fern/docs.yml"))


class MakeRowTest(unittest.TestCase):
    def test_column_order_appends_after_notes(self) -> None:
        """The nine original columns are a published surface.

        The private GitLab OSRB pipeline reads this CSV by column name out of
        the `license-diff` artifact. Reordering or renaming any of the first
        nine breaks a consumer nobody here can see or fix.
        """
        self.assertEqual(
            [
                "language", "package", "change", "old_version", "new_version",
                "old_license", "new_license", "repository_url", "notes",
                "source_kind", "source_file", "module", "risk",
            ],
            osrb_scan.ROW_FIELDS,
        )
        self.assertEqual(osrb_scan.ROW_FIELDS[:9], osrb_scan.HEADERS)

    def test_every_column_is_present_and_defaulted(self) -> None:
        row = osrb_scan.make_row(package="demo")

        self.assertEqual(set(osrb_scan.ROW_FIELDS), set(row))
        self.assertEqual("", row["language"])

    def test_module_and_risk_are_derived(self) -> None:
        row = osrb_scan.make_row(
            package="demo",
            new_license="GPL-3.0-only",
            source_file="services/rtvi/rt-embed/docker/py_deps/requirements.txt",
        )

        self.assertEqual("services/rtvi/rt-embed", row["module"])
        self.assertEqual(osrb_scan.RISK_HIGH, row["risk"])

    def test_module_ignores_a_line_number_suffix(self) -> None:
        row = osrb_scan.make_row(source_file="services/alert/Dockerfile#L3")

        self.assertEqual("services/alert", row["module"])

    def test_removed_row_takes_risk_from_the_old_license(self) -> None:
        row = osrb_scan.make_row(change="removed", old_license="AGPL-3.0")

        self.assertEqual(osrb_scan.RISK_HIGH, row["risk"])

    def test_explicit_values_win_over_derivation(self) -> None:
        row = osrb_scan.make_row(
            source_file="services/alert/requirements.txt",
            module="deploy",
            risk=osrb_scan.RISK_NONE,
            new_license="GPL-3.0",
        )

        self.assertEqual("deploy", row["module"])
        self.assertEqual(osrb_scan.RISK_NONE, row["risk"])

    def test_unknown_column_is_rejected_at_construction(self) -> None:
        """Three modules write this CSV; a typo must fail here, not silently.

        `csv.DictWriter` drops unknown keys, so a misspelled column in
        osrb_sources would otherwise mean a column of empty cells that looks
        like real data.
        """
        with self.assertRaises(ValueError) as caught:
            osrb_scan.make_row(package="demo", licence="MIT")

        self.assertIn("licence", str(caught.exception))

    def test_normalize_row_drops_extras_instead_of_raising(self) -> None:
        row = osrb_scan.normalize_row({"package": "demo", "bogus": "x"})

        self.assertEqual("demo", row["package"])
        self.assertNotIn("bogus", row)


class IsDependencyFileTest(unittest.TestCase):
    def test_every_shape_is_recognised(self) -> None:
        cases = {
            # lockfiles
            "services/agent/uv.lock": "lockfile",
            "a/pdm.lock": "lockfile",
            "a/poetry.lock": "lockfile",
            "a/Pipfile.lock": "lockfile",
            "a/package-lock.json": "lockfile",
            "a/yarn.lock": "lockfile",
            "a/pnpm-lock.yaml": "lockfile",
            "a/Cargo.lock": "lockfile",
            "a/go.sum": "lockfile",
            "a/Gemfile.lock": "lockfile",
            "a/composer.lock": "lockfile",
            # manifests
            "a/pyproject.toml": "manifest",
            "a/requirements.txt": "manifest",
            "a/requirements-dev.txt": "manifest",
            "a/setup.py": "manifest",
            "a/setup.cfg": "manifest",
            "a/Pipfile": "manifest",
            "a/package.json": "manifest",
            "a/go.mod": "manifest",
            "a/Cargo.toml": "manifest",
            "a/pom.xml": "manifest",
            "a/build.gradle": "manifest",
            "a/build.gradle.kts": "manifest",
            "a/Gemfile": "manifest",
            "a/demo.gemspec": "manifest",
            "a/conanfile.txt": "manifest",
            "a/conanfile.py": "manifest",
            "a/vcpkg.json": "manifest",
            "a/environment.yml": "manifest",
            "a/MODULE.bazel": "manifest",
            "a/WORKSPACE": "manifest",
            # container / compose / chart / build / ci
            "services/alert/Dockerfile": "container",
            "a/Dockerfile.base": "container",
            "a/elasticsearch.Dockerfile": "container",
            "deploy/docker/compose.yml": "compose",
            "a/docker-compose.yaml": "compose",
            "a/compose.fusion-test.yml": "compose",
            "services/alert/deploy_docker-compose.yml": "compose",
            "services/video-summarization/docker/logstash/logstash-compose.yml": "compose",
            "deploy/helm/services/rtvi/Chart.yaml": "chart",
            "deploy/helm/services/rtvi/Chart.lock": "chart",
            "a/CMakeLists.txt": "build",
            "a/cmake/FindFoo.cmake": "build",
            ".pre-commit-config.yaml": "ci",
            ".github/workflows/ci.yml": "ci",
            # attribution
            "services/rtvi/rt-cv/LICENSE.3rdparty": "attribution",
            "services/agent/LICENSE-3rd-party.txt": "attribution",
            "services/sdrc/3rdParty_Licenses.md": "attribution",
            "deploy/docker/services/infra/3rdParty_Licenses": "attribution",
            "libs/analytics/spatialai-data-utils/NOTICE": "attribution",
            "deploy/docker/NOTICE.md": "attribution",
            "a/THIRD_PARTY_LICENSES.md": "attribution",
            "a/oss-licenses.txt": "attribution",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(expected, osrb_scan.is_dependency_file(path))

    def test_non_dependency_files_are_not_recognised(self) -> None:
        for path in (
            "docs/overview.md",
            "LICENSE",
            "services/vios/include/3rdparty/aws/auth/auth.h",
            "services/analytics/behavior-analytics/src/mdx/core/logger_setup.py",
            "deploy/helm/services/rtvi/values.yaml",
            "services/ui/Dockerfile.dockerignore",
            "services/sdrc/tests/test_dockerfile_security.py",
            "services/ui/node_modules/leftpad/package.json",
            "services/ui/node_modules/leftpad/package-lock.json",
        ):
            with self.subTest(path=path):
                self.assertIsNone(osrb_scan.is_dependency_file(path))

    def test_repo_license_is_not_an_attribution_file(self) -> None:
        """The repo's own LICENSE is our grant, not a third party's."""
        self.assertIsNone(osrb_scan.is_dependency_file("LICENSE"))
        self.assertEqual(
            "attribution", osrb_scan.is_dependency_file("LICENSE-3rd-party.txt")
        )


class IsParsedTest(unittest.TestCase):
    def test_shapes_with_a_parser(self) -> None:
        for path in (
            "a/uv.lock", "a/pdm.lock", "a/poetry.lock", "a/Pipfile.lock",
            "a/package-lock.json", "a/yarn.lock", "a/pnpm-lock.yaml",
            "a/Cargo.lock", "a/go.sum", "a/Gemfile.lock",
            "a/pyproject.toml", "a/requirements.txt", "a/setup.py", "a/setup.cfg",
            "a/Pipfile", "a/package.json", "a/go.mod", "a/Cargo.toml", "a/pom.xml",
            "a/build.gradle", "a/build.gradle.kts", "a/demo.gemspec",
            "a/conanfile.txt", "a/vcpkg.json", "a/environment.yml",
            "a/Dockerfile", "a/compose.yml", "a/Chart.yaml", "a/CMakeLists.txt",
            ".github/workflows/ci.yml",
        ):
            with self.subTest(path=path):
                self.assertTrue(osrb_scan.is_parsed(path))

    def test_recognised_shapes_without_a_parser_stay_false(self) -> None:
        """These are the rows that fail a PR, and that is the intended design.

        Marking a shape parsed without a parser behind it converts a loud
        failure into the silent one this module exists to prevent.
        """
        for path in (
            "a/composer.lock", "a/MODULE.bazel", "a/WORKSPACE", "a/Gemfile",
            "services/new/LICENSE.3rdparty", "services/new/NOTICE",
        ):
            with self.subTest(path=path):
                self.assertIsNotNone(osrb_scan.is_dependency_file(path))
                self.assertFalse(osrb_scan.is_parsed(path))

    def test_chart_lock_rides_on_the_parsed_chart_yaml(self) -> None:
        """Chart.lock only pins digests for Chart.yaml's `dependencies:` list.

        osrb_sources.parse_helm_chart reads that list, so a Chart.lock carries
        no dependency the scan has not already seen. Exempting it is only
        legitimate while Chart.yaml stays parsed — if that ever changes, this
        exemption has to go with it.
        """
        self.assertTrue(osrb_scan.is_parsed("deploy/helm/services/rtvi/Chart.yaml"))
        self.assertTrue(osrb_scan.is_parsed("deploy/helm/services/rtvi/Chart.lock"))

    def test_every_parsed_shape_in_the_real_tree_has_a_reachable_parser(self) -> None:
        """The invariant that keeps is_parsed honest.

        is_parsed() is a hand-maintained list. If it drifts ahead of the
        parsers — a filename added to it with no parser written — the scanner
        goes back to reporting nothing for that file while claiming coverage.
        """
        paths = repo_paths()
        if not paths:
            self.skipTest("git tree unavailable")

        python_walker_basenames = {
            filename.lower() for filename, _parser in osrb_scan.PYTHON_LOCKS
        } | {"package-lock.json", "pyproject.toml"}

        orphans = []
        for path in paths:
            if not osrb_scan.is_parsed(path):
                continue
            base = path.rsplit("/", 1)[-1].lower()
            if osrb_scan.ecosystem_parser(path) is not None:
                continue
            if osrb_scan.is_dependency_file(path) in osrb_scan._KINDS_PARSED_BY_SOURCES:
                continue
            if base in python_walker_basenames:
                continue
            if base.startswith("requirements") and base.endswith(".txt"):
                continue
            orphans.append(path)

        self.assertEqual([], orphans)

    def test_real_tree_recognises_the_expected_shapes(self) -> None:
        paths = repo_paths()
        if not paths:
            self.skipTest("git tree unavailable")

        kinds: dict[str, int] = {}
        for path in paths:
            kind = osrb_scan.is_dependency_file(path)
            if kind:
                kinds[kind] = kinds.get(kind, 0) + 1

        # Guard rails, not golden values: these counts move with the repo, but
        # a collapse to zero means a matcher stopped matching.
        self.assertGreaterEqual(kinds.get("chart", 0), 50)
        self.assertGreaterEqual(kinds.get("compose", 0), 60)
        self.assertGreaterEqual(kinds.get("container", 0), 40)
        self.assertGreaterEqual(kinds.get("lockfile", 0), 10)
        self.assertGreaterEqual(kinds.get("manifest", 0), 40)
        self.assertGreaterEqual(kinds.get("ci", 0), 15)
        self.assertGreaterEqual(kinds.get("attribution", 0), 20)


class UncoveredDependencyFilesTest(unittest.TestCase):
    def test_added_file_without_a_parser_is_reported(self) -> None:
        uncovered = osrb_scan.uncovered_dependency_files(
            ["services/keep/uv.lock"],
            [
                "services/keep/uv.lock",
                "services/new/MODULE.bazel",
                "services/new/composer.lock",
            ],
        )

        self.assertEqual(
            ["services/new/MODULE.bazel", "services/new/composer.lock"],
            uncovered,
        )

    def test_added_file_with_a_parser_is_not_reported(self) -> None:
        uncovered = osrb_scan.uncovered_dependency_files(
            [],
            [
                "services/rtvi/rt-vlm/docker/rtvi_vlm/py_deps/pyproject.toml",
                "services/rtvi/rt-vlm/docker/rtvi_vlm/py_deps/pdm.lock",
                "services/example/poetry.lock",
                "services/agent/uv.lock",
                "libs/analytics/spatialai-data-utils/Pipfile.lock",
                "services/foo/requirements.txt",
                "services/foo/requirements-dev.txt",
                "services/ui/package-lock.json",
                "services/ui/package.json",
                "services/new/Cargo.lock",
                "services/new/Cargo.toml",
                "services/new/go.mod",
                "services/new/go.sum",
                "tools/plugin/build.gradle",
                "services/new/pom.xml",
                "services/new/Dockerfile",
                "deploy/docker/compose.yml",
                "deploy/helm/x/Chart.yaml",
            ],
        )

        self.assertEqual([], uncovered)

    def test_exempt_and_filtered_paths_are_not_reported(self) -> None:
        uncovered = osrb_scan.uncovered_dependency_files(
            [],
            [
                "deploy/helm/services/rtvi/Chart.lock",
                "services/video-summarization/docker/base/requirements_apt.txt",
                "ui/node_modules/leftpad/package.json",
                "docs/overview.md",
            ],
        )

        self.assertEqual([], uncovered)

    def test_pre_existing_gaps_do_not_fail_every_pr(self) -> None:
        """Only ADDITIONS block.

        Failing every PR in the repo for a file that was already on the base
        branch trains everyone to click past this check, which costs more than
        the gap does.
        """
        uncovered = osrb_scan.uncovered_dependency_files(
            ["services/old/NOTICE"],
            ["services/old/NOTICE", "services/new/NOTICE"],
        )

        self.assertEqual(["services/new/NOTICE"], uncovered)

    def test_row_names_the_file_and_blocks(self) -> None:
        rows = osrb_scan.uncovered_source_rows(["services/new/MODULE.bazel"])

        self.assertEqual(1, len(rows))
        self.assertEqual("UNCOVERED_SOURCE", rows[0]["change"])
        self.assertEqual("MODULE.bazel", rows[0]["package"])
        self.assertEqual("services/new/MODULE.bazel", rows[0]["source_file"])
        self.assertEqual("manifest", rows[0]["source_kind"])
        self.assertEqual("services/new", rows[0]["module"])
        self.assertEqual(osrb_scan.RISK_UNKNOWN, rows[0]["risk"])

    def test_the_repository_is_clean_against_itself(self) -> None:
        """HEAD vs HEAD must add nothing; otherwise every PR starts red."""
        paths = repo_paths()
        if not paths:
            self.skipTest("git tree unavailable")

        self.assertEqual([], osrb_scan.uncovered_dependency_files(paths, paths))


class ParsePackageJsonTest(unittest.TestCase):
    MANIFEST = b'''{
  "name": "demo",
  "version": "1.0.0",
  "dependencies": {
    "react": "^18.3.0",
    "vst-streaming-lib": "file:../streaming-lib",
    "internal-ui": "workspace:*",
    "forked-dep": "https://example.com/pkg.tgz"
  },
  "optionalDependencies": { "fsevents": "~2.3.3" },
  "peerDependencies": { "react-dom": ">=18" },
  "devDependencies": { "jest": "^29.0.0" }
}'''

    def test_runtime_optional_and_peer_are_inventoried(self) -> None:
        inventory = osrb_scan.parse_package_json(self.MANIFEST)

        self.assertEqual(
            {("react", "^18.3.0"), ("fsevents", "~2.3.3"), ("react-dom", ">=18")},
            set(inventory),
        )

    def test_dev_dependencies_and_local_protocols_are_excluded(self) -> None:
        names = {name for name, _spec in osrb_scan.parse_package_json(self.MANIFEST)}

        self.assertNotIn("jest", names)
        self.assertNotIn("vst-streaming-lib", names)
        self.assertNotIn("internal-ui", names)
        self.assertNotIn("forked-dep", names)

    def test_real_vios_ui_manifest_drops_its_file_dependency(self) -> None:
        path = REPO_ROOT / "services" / "vios" / "ui" / "vios-ui" / "package.json"
        self.assertTrue(path.is_file())

        names = {name for name, _spec in osrb_scan.parse_package_json(path.read_bytes())}

        self.assertNotIn("vst-streaming-lib", names)
        self.assertTrue(names)


class ParseYarnLockTest(unittest.TestCase):
    def test_yarn_v1_format(self) -> None:
        lock = b'''# yarn lockfile v1


lodash@^4.17.21:
  version "4.17.21"
  resolved "https://registry.yarnpkg.com/lodash/-/lodash-4.17.21.tgz#abc"

"@babel/core@^7.0.0", "@babel/core@^7.1.0":
  version "7.20.0"
  resolved "https://registry.yarnpkg.com/@babel/core/-/core-7.20.0.tgz#def"
'''

        inventory = osrb_scan.parse_yarn_lock(lock)

        self.assertEqual(
            {("lodash", "4.17.21"), ("@babel/core", "7.20.0")}, set(inventory)
        )

    def test_yarn_berry_format_and_workspace_entries(self) -> None:
        lock = b'''# This file is generated by running "yarn install"

__metadata:
  version: 6
  cacheKey: 8

"lodash@npm:^4.17.21":
  version: 4.17.21
  resolution: "lodash@npm:4.17.21"

"root-workspace-0b6124@workspace:.":
  version: 0.0.0-use.local
  resolution: "root-workspace-0b6124@workspace:."
'''

        inventory = osrb_scan.parse_yarn_lock(lock)

        self.assertEqual({("lodash", "4.17.21")}, set(inventory))

    def test_scoped_package_name_survives_the_at_split(self) -> None:
        self.assertEqual(
            "@babel/core", osrb_scan._yarn_descriptor_name('"@babel/core@npm:^7.0.0"')
        )


class ParsePnpmLockTest(unittest.TestCase):
    def test_v9_and_v6_key_shapes(self) -> None:
        lock = b'''lockfileVersion: '9.0'

importers:
  .:
    dependencies:
      lodash:
        specifier: ^4.17.21
        version: 4.17.21

packages:

  lodash@4.17.21:
    resolution: {integrity: sha512-abc}

  '@babel/core@7.20.0':
    resolution: {integrity: sha512-def}

  /legacy-pkg@1.0.0:
    resolution: {integrity: sha512-ghi}

  react-dom@18.3.1(react@18.3.1):
    resolution: {integrity: sha512-jkl}
'''

        inventory = osrb_scan.parse_pnpm_lock(lock)

        self.assertEqual(
            {
                ("lodash", "4.17.21"),
                ("@babel/core", "7.20.0"),
                ("legacy-pkg", "1.0.0"),
                ("react-dom", "18.3.1"),
            },
            set(inventory),
        )

    def test_v5_slash_separated_keys(self) -> None:
        lock = b'''lockfileVersion: 5.4

packages:

  /lodash/4.17.21:
    resolution: {integrity: sha512-abc}

  /@babel/core/7.20.0:
    resolution: {integrity: sha512-def}
'''

        inventory = osrb_scan.parse_pnpm_lock(lock)

        self.assertEqual(
            {("lodash", "4.17.21"), ("@babel/core", "7.20.0")}, set(inventory)
        )


class ParseGoTest(unittest.TestCase):
    GO_MOD = b'''module example.com/demo

go 1.21

require github.com/pkg/errors v0.9.1

require (
\tgithub.com/gorilla/mux v1.8.1
\tgolang.org/x/net v0.17.0 // indirect
)

replace (
\tgithub.com/old/thing => github.com/new/thing v1.0.0
)

exclude github.com/bad/pkg v0.1.0
'''

    def test_single_line_and_block_requires(self) -> None:
        inventory = osrb_scan.parse_go_mod(self.GO_MOD)

        self.assertEqual(
            {
                ("github.com/pkg/errors", "v0.9.1"),
                ("github.com/gorilla/mux", "v1.8.1"),
                ("golang.org/x/net", "v0.17.0"),
            },
            set(inventory),
        )

    def test_replace_and_exclude_are_not_requirements(self) -> None:
        names = {name for name, _v in osrb_scan.parse_go_mod(self.GO_MOD)}

        self.assertNotIn("github.com/old/thing", names)
        self.assertNotIn("github.com/new/thing", names)
        self.assertNotIn("github.com/bad/pkg", names)

    def test_go_sum_collapses_the_go_mod_hash_rows(self) -> None:
        go_sum = b'''github.com/gorilla/mux v1.8.1 h1:abc=
github.com/gorilla/mux v1.8.1/go.mod h1:def=
golang.org/x/net v0.17.0 h1:ghi=
golang.org/x/net v0.17.0/go.mod h1:jkl=
'''

        inventory = osrb_scan.parse_go_sum(go_sum)

        self.assertEqual(
            {("github.com/gorilla/mux", "v1.8.1"), ("golang.org/x/net", "v0.17.0")},
            set(inventory),
        )


class ParseCargoTest(unittest.TestCase):
    def test_manifest_reads_runtime_and_target_dependencies_only(self) -> None:
        manifest = b'''
[package]
name = "demo"
version = "0.1.0"

[dependencies]
serde = "1.0.197"
tokio = { version = "1.36", features = ["full"] }
local-helper = { path = "../helper" }

[build-dependencies]
cc = "1.0"

[dev-dependencies]
criterion = "0.5"

[target."cfg(unix)".dependencies]
nix = "0.28"
'''

        inventory = osrb_scan.parse_cargo(manifest)

        self.assertEqual(
            {("serde", "1.0.197"), ("tokio", "1.36"), ("nix", "0.28")},
            set(inventory),
        )

    def test_lock_reads_resolved_crates_and_skips_the_local_root(self) -> None:
        lock = b'''version = 3

[[package]]
name = "serde"
version = "1.0.197"
source = "registry+https://github.com/rust-lang/crates.io-index"

[[package]]
name = "demo"
version = "0.1.0"
'''

        inventory = osrb_scan.parse_cargo(lock)

        self.assertEqual({("serde", "1.0.197")}, set(inventory))


class ParsePomXmlTest(unittest.TestCase):
    POM = b'''<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>demo</artifactId>
  <version>1.0.0</version>
  <properties>
    <log4j.version>2.25.4</log4j.version>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.apache.logging.log4j</groupId>
      <artifactId>log4j-core</artifactId>
      <version>${log4j.version}</version>
    </dependency>
    <dependency>
      <groupId>junit</groupId>
      <artifactId>junit</artifactId>
      <version>4.13.2</version>
      <scope>test</scope>
    </dependency>
  </dependencies>
  <dependencyManagement>
    <dependencies>
      <dependency>
        <groupId>com.google.protobuf</groupId>
        <artifactId>protobuf-java</artifactId>
        <version>4.28.3</version>
      </dependency>
    </dependencies>
  </dependencyManagement>
</project>
'''

    def test_namespaced_dependencies_are_found(self) -> None:
        """The namespace trap.

        ElementTree reports `{http://maven.apache.org/POM/4.0.0}dependency`.
        Matching the raw tag finds zero dependencies in a perfectly ordinary
        POM and reports a Java service as depending on nothing at all.
        """
        inventory = osrb_scan.parse_pom_xml(self.POM)

        self.assertIn(("org.apache.logging.log4j:log4j-core", "2.25.4"), inventory)

    def test_properties_are_resolved_and_test_scope_dropped(self) -> None:
        inventory = osrb_scan.parse_pom_xml(self.POM)

        self.assertEqual(
            {
                ("org.apache.logging.log4j:log4j-core", "2.25.4"),
                ("com.google.protobuf:protobuf-java", "4.28.3"),
            },
            set(inventory),
        )

    def test_malformed_pom_is_reported_not_swallowed(self) -> None:
        with self.assertRaises(osrb_scan.UnparseableManifest):
            osrb_scan.parse_pom_xml(b"<project><dependencies>")


class ParseGradleTest(unittest.TestCase):
    def test_coordinate_and_map_forms(self) -> None:
        build = b'''
dependencies {
    implementation 'org.apache.commons:commons-lang3:3.7'
    api "com.google.code.gson:gson:2.13.1"
    runtimeOnly group: 'redis.clients', name: 'jedis', version: '6.1.0'
    testImplementation 'junit:junit:4.13.2'
    implementation fileTree(dir: LIB_PATH, include: "**/*.jar")
}

buildscript {
    dependencies {
        classpath "org.yaml:snakeyaml:2.2"
    }
}
'''

        inventory = osrb_scan.parse_gradle(build)

        self.assertEqual(
            {
                ("org.apache.commons:commons-lang3", "3.7"),
                ("com.google.code.gson:gson", "2.13.1"),
                ("redis.clients:jedis", "6.1.0"),
            },
            set(inventory),
        )

    def test_real_logstash_plugin_build_file(self) -> None:
        path = (
            REPO_ROOT / "tools" / "logstash-plugins" / "input" / "redis-stream"
            / "build.gradle"
        )
        self.assertTrue(path.is_file())

        inventory = osrb_scan.parse_gradle(path.read_bytes())
        names = {name for name, _v in inventory}

        self.assertIn("org.apache.logging.log4j:log4j-core", names)
        self.assertIn("redis.clients:jedis", names)
        self.assertIn("com.google.protobuf:protobuf-java", names)
        # testImplementation and the buildscript classpath do not ship.
        self.assertNotIn("junit:junit", names)
        self.assertNotIn("org.jruby:jruby-complete", names)
        self.assertNotIn("org.yaml:snakeyaml", names)
        # Prose in this file mentions `$LS_HOME/bin/logstash-plugin`; a looser
        # regex reads that as a coordinate.
        self.assertTrue(all(":" in name for name in names))


class ParseRubyTest(unittest.TestCase):
    def test_gemfile_lock_reads_resolutions_not_constraints(self) -> None:
        lock = b'''GEM
  remote: https://rubygems.org/
  specs:
    rake (13.0.6)
    rspec (3.12.0)
      rspec-core (~> 3.12.0)

PLATFORMS
  ruby

DEPENDENCIES
  rspec
'''

        inventory = osrb_scan.parse_gemfile_lock(lock)

        self.assertEqual({("rake", "13.0.6"), ("rspec", "3.12.0")}, set(inventory))

    def test_gemspec_excludes_development_dependencies(self) -> None:
        gemspec = b'''Gem::Specification.new do |s|
  s.name = 'demo'
  s.add_dependency 'rake', '~> 13.0'
  s.add_runtime_dependency 'jwt', '2.7.1'
  s.add_development_dependency 'rspec', '3.12'
end
'''

        inventory = osrb_scan.parse_gemspec(gemspec)

        self.assertEqual({("rake", "~> 13.0"), ("jwt", "2.7.1")}, set(inventory))


class ParseCppTest(unittest.TestCase):
    def test_conanfile_txt_sections(self) -> None:
        conanfile = b'''[requires]
zlib/1.2.13
openssl/3.1.2

[tool_requires]
cmake/3.27.0

[generators]
CMakeDeps
'''

        inventory = osrb_scan.parse_conanfile(conanfile)

        self.assertEqual({("zlib", "1.2.13"), ("openssl", "3.1.2")}, set(inventory))

    def test_conanfile_py_is_read_not_executed(self) -> None:
        conanfile = b'''from conan import ConanFile


class DemoConan(ConanFile):
    requires = "zlib/1.2.13", "fmt/10.1.1"
    build_requires = "cmake/3.27.0"

    def requirements(self):
        self.requires("spdlog/1.12.0")
'''

        inventory = osrb_scan.parse_conanfile(conanfile)
        names = {name for name, _v in inventory}

        self.assertIn("zlib", names)
        self.assertIn("fmt", names)
        self.assertIn("spdlog", names)
        self.assertNotIn("cmake", names)

    def test_vcpkg_manifest(self) -> None:
        manifest = b'''{
  "name": "demo",
  "version": "1.0.0",
  "dependencies": [
    "zlib",
    { "name": "boost-asio", "version>=": "1.84.0" }
  ],
  "overrides": [ { "name": "fmt", "version": "10.1.1" } ]
}'''

        inventory = osrb_scan.parse_vcpkg_json(manifest)

        self.assertEqual(
            {("zlib", ""), ("boost-asio", "1.84.0"), ("fmt", "10.1.1")},
            set(inventory),
        )


class ParseCondaEnvironmentTest(unittest.TestCase):
    def test_conda_and_nested_pip_dependencies(self) -> None:
        environment = b'''name: demo
channels:
  - conda-forge
dependencies:
  - python=3.11
  - numpy=1.26.4
  - pip
  - pip:
    - httpx==0.28.1
    - requests
'''

        inventory = osrb_scan.parse_conda_environment(environment)
        names = {name for name, _v in inventory}

        self.assertIn(("numpy", "1.26.4"), inventory)
        self.assertIn(("httpx", "0.28.1"), inventory)
        self.assertIn(("requests", ""), inventory)
        # The interpreter is not a package OSRB reviews.
        self.assertNotIn("python", names)


class ParsePythonManifestTest(unittest.TestCase):
    def test_setup_cfg_install_requires(self) -> None:
        cfg = b'''[metadata]
name = demo
version = 1.0.0

[options]
install_requires =
    httpx>=0.27.0
    requests==2.31.0

[options.extras_require]
dev =
    pytest==8.1.1
'''

        inventory = osrb_scan.parse_setup_cfg(cfg)

        self.assertEqual({("httpx", ""), ("requests", "2.31.0")}, set(inventory))

    def test_setup_cfg_percent_sign_does_not_break_interpolation(self) -> None:
        cfg = b'''[metadata]
description = 100% coverage

[options]
install_requires =
    requests==2.31.0
'''

        self.assertEqual({("requests", "2.31.0")}, set(osrb_scan.parse_setup_cfg(cfg)))

    def test_setup_py_literal_install_requires(self) -> None:
        setup_py = b'''from setuptools import setup

setup(name="demo", install_requires=["httpx>=0.27.0", "requests==2.31.0"])
'''

        inventory = osrb_scan.parse_setup_py(setup_py)

        self.assertEqual({("httpx", ""), ("requests", "2.31.0")}, set(inventory))

    def test_setup_py_module_level_binding_is_resolved(self) -> None:
        setup_py = b'''from setuptools import setup

INSTALL_REQUIRES = ["requests==2.31.0"]

setup(name="demo", install_requires=INSTALL_REQUIRES)
'''

        self.assertEqual({("requests", "2.31.0")}, set(osrb_scan.parse_setup_py(setup_py)))

    def test_setup_py_is_never_executed(self) -> None:
        """Executing a PR's setup.py hands a contributor code execution on a
        runner holding a GITHUB_TOKEN. The dependency list is not worth that."""
        setup_py = b'''import sys

raise SystemExit("this setup.py must never run")

setup(install_requires=["requests==2.31.0"])
'''

        self.assertEqual({("requests", "2.31.0")}, set(osrb_scan.parse_setup_py(setup_py)))

    def test_computed_install_requires_is_reported_not_guessed(self) -> None:
        setup_py = b'''from setuptools import setup

setup(install_requires=open("requirements.txt").read().splitlines())
'''

        with self.assertRaises(osrb_scan.UnparseableManifest):
            osrb_scan.parse_setup_py(setup_py)

    def test_setup_py_without_install_requires_is_not_a_gap(self) -> None:
        """Both real setup.py files in this repo are version shims.

        Their dependencies live in pyproject.toml, which IS parsed. Treating
        the absence of install_requires as a coverage gap would fail every PR
        that touches either of them, for nothing.
        """
        for relative in (
            "services/analytics/behavior-analytics/setup.py",
            "libs/analytics/spatialai-data-utils/release/setup.py",
        ):
            with self.subTest(path=relative):
                path = REPO_ROOT / relative
                self.assertTrue(path.is_file())
                self.assertEqual({}, osrb_scan.parse_setup_py(path.read_bytes()))

    def test_pipfile_packages_only(self) -> None:
        pipfile = b'''[packages]
requests = "==2.31.0"
httpx = "*"
localpkg = {path = "."}

[dev-packages]
pytest = "*"
'''

        inventory = osrb_scan.parse_pipfile(pipfile)
        names = {name for name, _v in inventory}

        self.assertEqual({("requests", "==2.31.0"), ("httpx", "")}, set(inventory))
        self.assertNotIn("pytest", names)
        self.assertNotIn("localpkg", names)

    def test_real_pipfiles_parse(self) -> None:
        paths = [p for p in repo_paths() if p.rsplit("/", 1)[-1] == "Pipfile"]
        if not paths:
            self.skipTest("no Pipfile in tree")
        for relative in paths:
            with self.subTest(path=relative):
                inventory = osrb_scan.parse_pipfile((REPO_ROOT / relative).read_bytes())
                self.assertTrue(inventory)


class DiffSourceRowsTest(unittest.TestCase):
    def _row(self, package: str, version: str) -> dict[str, str]:
        return osrb_scan.make_row(
            language="container",
            package=package,
            new_version=version,
            source_kind="container",
            source_file="services/alert/Dockerfile",
        )

    def test_unchanged_entries_produce_nothing(self) -> None:
        rows = [self._row("ubuntu", "22.04")]

        self.assertEqual([], osrb_scan.diff_source_rows(rows, list(rows)))

    def test_version_change_becomes_one_updated_row(self) -> None:
        rows = osrb_scan.diff_source_rows(
            [self._row("ubuntu", "22.04")], [self._row("ubuntu", "24.04")]
        )

        self.assertEqual(1, len(rows))
        self.assertEqual("updated", rows[0]["change"])
        self.assertEqual("22.04", rows[0]["old_version"])
        self.assertEqual("24.04", rows[0]["new_version"])

    def test_added_and_removed(self) -> None:
        rows = osrb_scan.diff_source_rows(
            [self._row("ubuntu", "22.04")], [self._row("alpine", "3.19")]
        )
        by_change = {row["change"]: row for row in rows}

        self.assertEqual({"added", "removed"}, set(by_change))
        self.assertEqual("alpine", by_change["added"]["package"])
        self.assertEqual("ubuntu", by_change["removed"]["package"])


class SiblingModuleLoadingTest(unittest.TestCase):
    def test_missing_sibling_returns_none_instead_of_raising(self) -> None:
        """A missing osrb_sources/osrb_usage must not crash the gate.

        Crashing takes the declaration side down with it and leaves every PR
        in the repo unprotected, which is strictly worse than a smaller scan
        with a loud annotation.
        """
        self.assertIsNone(osrb_scan._load_sibling_module("osrb_not_a_real_module"))


class DeclaredByModuleTest(unittest.TestCase):
    def test_names_are_grouped_by_owning_module(self) -> None:
        inventory = {
            ("httpx", "0.28.1"): {"source_file": "services/alert/requirements.txt"},
            ("numpy", "2.1.0"): {
                "source_file": "services/rtvi/rt-embed/docker/py_deps/requirements.txt"
            },
        }

        declared = osrb_scan.declared_by_module([inventory])

        self.assertEqual({"httpx"}, declared["services/alert"])
        self.assertEqual({"numpy"}, declared["services/rtvi/rt-embed"])



class ReportOnlyIsStructuralTest(unittest.TestCase):
    """USED_UNDECLARED must be unable to fail the job, by construction.

    The owner's decision was that the use-side pass is advisory: it infers a
    dependency from an import rather than reading a declaration, so a mapping
    miss must never block someone's pull request. A comment saying so is not
    evidence -- these assert it.
    """

    def test_blocks_merge_is_false_for_every_use_side_row(self) -> None:
        for kind in osrb_scan.SOURCE_KINDS:
            row = osrb_scan.make_row(
                package="anything",
                change=osrb_scan.CHANGE_USED_UNDECLARED,
                source_kind=kind,
                source_file="services/x/thing.py",
            )
            self.assertFalse(osrb_scan.blocks_merge(row), kind)

    def test_blocks_merge_is_false_for_added_and_updated_and_removed(self) -> None:
        # Those are OSRB's business, counted by review_rows in the summary --
        # not by the coverage gate. Double-counting them here would make one
        # dependency bump fail the job twice for two different stated reasons.
        for change in ("added", "removed", "updated"):
            row = osrb_scan.make_row(
                package="anything", change=change,
                source_kind=osrb_scan.KIND_MANIFEST,
                source_file="services/x/pyproject.toml",
            )
            self.assertFalse(osrb_scan.blocks_merge(row), change)

    def test_blocks_merge_is_true_for_an_unparseable_manifest(self) -> None:
        row = osrb_scan.make_row(
            package="pom.xml",
            change=osrb_scan.CHANGE_UNCOVERED_SOURCE,
            source_kind=osrb_scan.KIND_MANIFEST,
            source_file="services/x/pom.xml",
        )
        self.assertTrue(osrb_scan.blocks_merge(row))

    def test_attribution_addition_does_not_block(self) -> None:
        # Adding a LICENSE.3rdparty is the behaviour the licence process asks
        # for. Nothing parses prose, so the remedy this gate offers -- extend
        # is_dependency_file -- cannot be acted on, and blocking would punish
        # the right action with advice nobody can follow.
        row = osrb_scan.uncovered_source_rows(["services/x/LICENSE.3rdparty"])[0]
        self.assertEqual(row["source_kind"], osrb_scan.KIND_ATTRIBUTION)
        self.assertFalse(osrb_scan.blocks_merge(row))


class ConsumerContractTest(unittest.TestCase):
    """The private GitLab OSRB pipeline reads this CSV and we cannot see it.

    Freezing the first nine columns is the whole compatibility story: a
    consumer indexing by position breaks on a reorder, and one indexing by
    header name breaks on a rename. Appending is safe; nothing else is.
    """

    FROZEN = [
        "language", "package", "change", "old_version", "new_version",
        "old_license", "new_license", "repository_url", "notes",
    ]

    def test_first_nine_columns_are_unchanged_in_name_and_order(self) -> None:
        self.assertEqual(osrb_scan.ROW_FIELDS[:9], self.FROZEN)

    def test_new_columns_are_appended_not_inserted(self) -> None:
        self.assertEqual(
            osrb_scan.ROW_FIELDS[9:],
            ["source_kind", "source_file", "module", "risk"],
        )

    def test_make_row_emits_exactly_the_declared_fields(self) -> None:
        row = osrb_scan.make_row(package="x", change="added")
        self.assertEqual(list(row), osrb_scan.ROW_FIELDS)


class SourceSideFailsClosedTest(unittest.TestCase):
    """Losing the source-side scanner must fail the job, not quietly shrink it.

    This is the regression that matters most in the whole module. Before the
    fix, a missing or import-broken osrb_sources.py produced a ::warning,
    zero rows, exit 0, and a PR comment reading "No changes require OSRB
    re-engagement" -- while the private GitLab pipeline downloaded a CSV that
    had silently lost every container, compose, chart, CMake and CI
    dependency. One dropped file in a rebase was enough. The scanner cannot
    detect this on its own, because is_parsed() returns True for those kinds
    unconditionally, so uncovered_dependency_files() sees nothing wrong.
    """

    def test_uncovered_rows_are_synthesised_for_every_source_side_path(self) -> None:
        head_paths = [
            "services/a/Dockerfile",
            "deploy/compose.yaml",
            "deploy/helm/x/Chart.yaml",
            "services/b/CMakeLists.txt",
            ".pre-commit-config.yaml",
            "services/c/pyproject.toml",   # declaration side, NOT affected
            "README.md",                   # not a dependency file at all
        ]
        affected = sorted(
            path for path in head_paths
            if (osrb_scan.is_dependency_file(path) or "")
            in osrb_scan._KINDS_PARSED_BY_SOURCES
        )
        self.assertEqual(len(affected), 5, affected)

        rows = osrb_scan.uncovered_source_rows(affected, reason="scanner unavailable")
        self.assertEqual(len(rows), 5)
        # Every one must actually block -- an advisory row here would restore
        # the exact fail-open this test exists to prevent.
        self.assertTrue(all(osrb_scan.blocks_merge(row) for row in rows), rows)
        self.assertTrue(
            all(row["change"] == osrb_scan.CHANGE_UNCOVERED_SOURCE for row in rows)
        )

    def test_pyproject_is_not_swept_up_by_the_source_side_failure(self) -> None:
        # The declaration side has its own parsers and is unaffected by
        # osrb_sources being gone. Failing on it too would make the error
        # message point at the wrong module.
        self.assertNotIn(
            osrb_scan.is_dependency_file("services/c/pyproject.toml"),
            osrb_scan._KINDS_PARSED_BY_SOURCES,
        )

if __name__ == "__main__":
    unittest.main()
