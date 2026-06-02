#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the container-source gate helper.

Run directly (no pytest dependency): ``python3 .github/scripts/test_source_check_only_nonbuild.py``.
The repo's pytest job runs from services/agent, so it won't collect this; the
container-source workflows run it as a step instead, so a helper regression
fails the required gate it powers.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_container_tag_source as chk  # noqa: E402
import source_check_only_nonbuild as gate  # noqa: E402


class TestIsNonbuild(unittest.TestCase):
    def nb(self, image, rel):
        return gate.is_nonbuild(rel, gate.NONBUILD_PATTERNS[image])

    def test_agent_docs_and_tests_are_nonbuild(self):
        self.assertTrue(self.nb("vss-agent", "README.md"))
        self.assertTrue(self.nb("vss-agent", "AGENTS.md"))
        self.assertTrue(self.nb("vss-agent", "tests/test_x.py"))
        self.assertTrue(self.nb("vss-agent", "stubs/nat/__init__.pyi"))

    def test_agent_shipped_src_md_is_build_relevant(self):
        # COPY agent/src/vss_agents wholesale ships these — must NOT be skipped.
        self.assertFalse(self.nb("vss-agent", "src/vss_agents/orchestrator/README.md"))
        self.assertFalse(self.nb("vss-agent", "pyproject.toml"))
        self.assertFalse(self.nb("vss-agent", "uv.lock"))

    def test_ui_markdown_and_ts_tests_are_nonbuild(self):
        self.assertTrue(self.nb("vss-agent-ui", "README.md"))
        self.assertTrue(self.nb("vss-agent-ui", "apps/x/notes.md"))
        self.assertTrue(self.nb("vss-agent-ui", "apps/x/Button.test.tsx"))
        self.assertTrue(self.nb("vss-agent-ui", "apps/x/util.spec.ts"))

    def test_ui_nonjs_test_and_source_are_build_relevant(self):
        # .dockerignore only drops the .js/.ts/.tsx test/spec variants.
        self.assertFalse(self.nb("vss-agent-ui", "scripts/gen.test.py"))
        self.assertFalse(self.nb("vss-agent-ui", "apps/x/Button.tsx"))
        self.assertFalse(self.nb("vss-agent-ui", "package.json"))


class TestResolveServiceRefs(unittest.TestCase):
    def setUp(self):
        self.repo = Path("/repo")
        self.compose = self.repo / "deploy/docker/services/agent/compose.yml"
        self.env = self.repo / "deploy/docker/dev/.env"

    def resolve(self, contents, image="vss-agent"):
        def read(rel):
            return contents.get(rel)

        with mock.patch.object(chk, "discover_compose_files", return_value=[self.compose]), \
             mock.patch.object(chk, "discover_env_files", return_value=[self.env]):
            return gate.resolve_service_refs(self.repo, read, image)

    def test_resolves_via_env(self):
        resolved, unresolved = self.resolve({
            "deploy/docker/services/agent/compose.yml":
                "    image: nvcr.io/x/vss-agent:${VSS_AGENT_VERSION}\n",
            "deploy/docker/dev/.env": "VSS_AGENT_VERSION=1.2.3\n",
        })
        self.assertEqual(resolved, {"nvcr.io/x/vss-agent:1.2.3"})
        self.assertEqual(unresolved, set())

    def test_unresolvable_ref_is_recorded(self):
        resolved, unresolved = self.resolve({
            "deploy/docker/services/agent/compose.yml":
                "    image: nvcr.io/x/vss-agent:${UNDEFINED_XYZ}\n",
            "deploy/docker/dev/.env": "VSS_AGENT_VERSION=1.2.3\n",
        })
        self.assertEqual(resolved, set())
        self.assertTrue(any("UNDEFINED_XYZ" in r for r in unresolved))

    def test_other_service_ref_ignored(self):
        # A ui ref must not appear when resolving for vss-agent.
        resolved, unresolved = self.resolve({
            "deploy/docker/services/agent/compose.yml":
                "    image: nvcr.io/x/vss-agent-ui:9.9.9\n",
            "deploy/docker/dev/.env": "",
        })
        self.assertEqual(resolved, set())
        self.assertEqual(unresolved, set())


class TestServiceTagChanged(unittest.TestCase):
    def run_with(self, head, base):
        with mock.patch.object(gate, "resolve_service_refs", side_effect=[head, base]):
            return gate.service_tag_changed(Path("/repo"), "deadbeef", "vss-agent")

    def test_no_change_skips(self):
        self.assertFalse(self.run_with(({"a:1"}, set()), ({"a:1"}, set())))

    def test_tag_bump_runs(self):
        self.assertTrue(self.run_with(({"a:2"}, set()), ({"a:1"}, set())))

    def test_no_refs_at_head_is_conservative(self):
        self.assertTrue(self.run_with((set(), set()), ({"a:1"}, set())))

    def test_newly_unresolvable_runs(self):
        self.assertTrue(self.run_with(({"a:1"}, {"x:${NOPE}"}), ({"a:1"}, set())))


if __name__ == "__main__":
    unittest.main(verbosity=2)
