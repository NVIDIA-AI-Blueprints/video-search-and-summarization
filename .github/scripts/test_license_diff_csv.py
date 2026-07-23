#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the OSRB license-diff inventory helpers."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
