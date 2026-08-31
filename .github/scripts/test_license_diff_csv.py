#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the OSRB license-diff inventory helpers."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("license_diff_csv.py")
MODULE_SPEC = importlib.util.spec_from_file_location("license_diff_csv", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
license_diff_csv = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(license_diff_csv)


class ParseUvLockTest(unittest.TestCase):
    def test_agent_lock_excludes_development_only_packages(self) -> None:
        lock_path = Path(__file__).parents[2] / "services" / "agent" / "uv.lock"

        inventory = license_diff_csv.parse_uv_lock(lock_path.read_bytes())
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

        inventory = license_diff_csv.parse_uv_lock(lock)

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

        inventory = license_diff_csv.parse_uv_lock(lock)

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

        inventory = license_diff_csv.parse_uv_lock(lock)

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

        inventory = license_diff_csv.parse_uv_lock(lock)

        self.assertEqual(
            {
                ("base-dependency", "1.0.0"),
                ("enabled-extra-dependency", "2.0.0"),
                ("runtime-dependency", "1.0.0"),
            },
            set(inventory),
        )


class DiffRequirementsTest(unittest.TestCase):
    @mock.patch.object(license_diff_csv, "pypi_metadata")
    def test_version_bump_resolves_both_license_versions(self, metadata: mock.Mock) -> None:
        metadata.side_effect = [
            {"license": "MIT", "repository_url": "https://example.com/old"},
            {"license": "MPL-2.0", "repository_url": "https://example.com/new"},
        ]

        rows = license_diff_csv.diff_requirements(
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

        inventory = license_diff_csv.parse_pyproject(manifest)

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

        inventory = license_diff_csv.parse_pyproject(manifest)

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

        inventory = license_diff_csv.parse_pyproject(path.read_bytes())

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

        inventory = license_diff_csv.parse_pdm_lock(lock)

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

        inventory = license_diff_csv.parse_poetry_lock(lock)

        self.assertEqual(
            {("requests", "2.32.0"), ("shared", "1.0.0")},
            set(inventory),
        )


class DiffPyprojectTest(unittest.TestCase):
    @mock.patch.object(license_diff_csv, "pypi_metadata")
    def test_new_pyproject_dependency_uses_source_note(self, metadata: mock.Mock) -> None:
        metadata.return_value = {
            "license": "MIT",
            "repository_url": "https://example.com/demo",
            "version": "1.0.0",
        }

        rows = license_diff_csv.diff_requirements(
            {},
            {"demo": "1.0.0"},
            set(),
            source="pyproject.toml",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["change"], "added")
        self.assertEqual(rows[0]["package"], "demo")
        self.assertIn("pyproject.toml", rows[0]["notes"])


class UnscannedAddedManifestsTest(unittest.TestCase):
    def test_warns_on_added_lockfile_the_scanner_does_not_parse(self) -> None:
        skipped = license_diff_csv.unscanned_added_manifests(
            ["services/keep/uv.lock"],
            [
                "services/keep/uv.lock",
                "services/new/Cargo.lock",
            ],
        )

        self.assertEqual(["services/new/Cargo.lock"], skipped)

    def test_does_not_warn_for_filenames_already_in_the_scan_set(self) -> None:
        skipped = license_diff_csv.unscanned_added_manifests(
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
            ],
        )

        self.assertEqual([], skipped)

    def test_does_not_warn_for_known_non_package_or_filtered_paths(self) -> None:
        skipped = license_diff_csv.unscanned_added_manifests(
            [],
            [
                "deploy/helm/services/rtvi/Chart.lock",
                "services/video-summarization/docker/base/requirements_apt.txt",
                "ui/node_modules/leftpad/package.lock",
                "docs/overview.md",
            ],
        )

        self.assertEqual([], skipped)

    def test_warning_message_names_the_skipped_path(self) -> None:
        with mock.patch.object(license_diff_csv, "_log") as log:
            license_diff_csv.warn_unscanned_added_manifests(
                ["services/new/Cargo.lock"]
            )

        log.assert_called_once_with(
            "WARNING: Skipped path — not in scan set: services/new/Cargo.lock"
        )


if __name__ == "__main__":
    unittest.main()
