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
import tempfile
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
        # COPY agent/src/agent wholesale ships these — must NOT be skipped.
        self.assertFalse(self.nb("vss-agent", "src/agent/orchestrator/README.md"))
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

    def test_alert_ms_docs_tests_and_meta_are_nonbuild(self):
        self.assertTrue(self.nb("vss-alert-ms", "README.md"))
        self.assertTrue(self.nb("vss-alert-ms", "docs/overview.md"))
        self.assertTrue(self.nb("vss-alert-ms", "notebooks/demo.ipynb"))
        self.assertTrue(self.nb("vss-alert-ms", "test/test_alert.py"))
        self.assertTrue(self.nb("vss-alert-ms", "tests/test_alert.py"))
        self.assertTrue(self.nb("vss-alert-ms", ".github/workflows/ci.yml"))
        self.assertTrue(self.nb("vss-alert-ms", "scripts/run.sh"))
        self.assertTrue(self.nb("vss-alert-ms", "docker-compose.dev.yml"))
        self.assertTrue(self.nb("vss-alert-ms", ".dockerignore"))
        self.assertTrue(self.nb("vss-alert-ms", "debug.log"))

    def test_alert_ms_source_is_build_relevant(self):
        # services/alert/Dockerfile does `COPY . /app`, so non-excluded files
        # ship and must NOT be skipped.
        self.assertFalse(self.nb("vss-alert-ms", "app.py"))
        self.assertFalse(self.nb("vss-alert-ms", "src/enhance_alert_with_vlm.py"))
        self.assertFalse(self.nb("vss-alert-ms", "requirements.txt"))
        self.assertFalse(self.nb("vss-alert-ms", "Dockerfile"))

    def test_video_analytics_docs_and_tests_are_nonbuild(self):
        self.assertTrue(self.nb("vss-video-analytics-api", "README.md"))
        self.assertTrue(self.nb("vss-video-analytics-api", "test/unit/x.test.js"))
        self.assertTrue(self.nb("vss-video-analytics-api", "configs/sample-configs/x.json"))

    def test_video_analytics_sources_are_build_relevant(self):
        self.assertFalse(self.nb("vss-video-analytics-api", "src/app/index.js"))
        self.assertFalse(self.nb("vss-video-analytics-api", "configs/default-configs/config.json"))

    def test_behavior_analytics_docs_tests_and_configs_are_nonbuild(self):
        self.assertTrue(self.nb("vss-behavior-analytics", "README.md"))
        self.assertTrue(self.nb("vss-behavior-analytics", "tests/unit/test_app.py"))
        self.assertTrue(self.nb("vss-behavior-analytics", "configs/warehouse_2d_config.json"))

    def test_behavior_analytics_sources_are_build_relevant(self):
        self.assertFalse(self.nb("vss-behavior-analytics", "src/mdx/analytics/core/app.py"))
        self.assertFalse(self.nb("vss-behavior-analytics", "Pipfile.lock"))
        self.assertFalse(self.nb("vss-behavior-analytics", "docker/Dockerfile"))


class TestDiscoverEnvFiles(unittest.TestCase):
    def test_discovers_include_level_env_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            profile_env = repo / "deploy/docker/developer-profiles/dev-profile-base/.env"
            service_env = repo / "deploy/docker/services/agent/agent.env"
            profile_env.parent.mkdir(parents=True)
            service_env.parent.mkdir(parents=True)
            profile_env.write_text("HOST_IP=127.0.0.1\n")
            service_env.write_text("VSS_AGENT_VERSION=1.2.3\n")

            env_files = {
                path.relative_to(repo).as_posix()
                for path in chk.discover_env_files(repo)
            }

        self.assertIn("deploy/docker/developer-profiles/dev-profile-base/.env", env_files)
        self.assertIn("deploy/docker/services/agent/agent.env", env_files)


class TestResolveServiceRefs(unittest.TestCase):
    def setUp(self):
        self.repo = Path("/repo")
        self.compose = self.repo / "deploy/docker/services/agent/compose.yml"
        self.env = self.repo / "deploy/docker/dev/.env"

    def resolve(self, contents, image="vss-agent"):
        def read(rel):
            return contents.get(rel)

        with (
            mock.patch.object(
                chk, "discover_compose_files", return_value=[self.compose]
            ),
            mock.patch.object(chk, "discover_env_files", return_value=[self.env]),
        ):
            return gate.resolve_service_refs(self.repo, read, image)

    def test_resolves_via_env(self):
        resolved, unresolved = self.resolve(
            {
                "deploy/docker/services/agent/compose.yml": "    image: nvcr.io/x/vss-agent:${VSS_AGENT_VERSION}\n",
                "deploy/docker/dev/.env": "VSS_AGENT_VERSION=1.2.3\n",
            }
        )
        self.assertEqual(resolved, {"nvcr.io/x/vss-agent:1.2.3"})
        self.assertEqual(unresolved, set())

    def test_unresolvable_ref_is_recorded(self):
        resolved, unresolved = self.resolve(
            {
                "deploy/docker/services/agent/compose.yml": "    image: nvcr.io/x/vss-agent:${UNDEFINED_XYZ}\n",
                "deploy/docker/dev/.env": "VSS_AGENT_VERSION=1.2.3\n",
            }
        )
        self.assertEqual(resolved, set())
        self.assertTrue(any("UNDEFINED_XYZ" in r for r in unresolved))

    def test_other_service_ref_ignored(self):
        # A ui ref must not appear when resolving for vss-agent.
        resolved, unresolved = self.resolve(
            {
                "deploy/docker/services/agent/compose.yml": "    image: nvcr.io/x/vss-agent-ui:9.9.9\n",
                "deploy/docker/dev/.env": "",
            }
        )
        self.assertEqual(resolved, set())
        self.assertEqual(unresolved, set())


class TestParseImageRef(unittest.TestCase):
    def test_tag_only(self):
        self.assertEqual(
            chk._parse_image_ref("nvcr.io/nvidia/vss-core/vss-agent:3.2.0"),
            ("nvcr.io", "nvidia/vss-core/vss-agent", "3.2.0"),
        )

    def test_digest_only(self):
        self.assertEqual(
            chk._parse_image_ref(
                "nvcr.io/nvidia/vss-core/vss-agent@sha256:" + "a" * 64
            ),
            ("nvcr.io", "nvidia/vss-core/vss-agent", "sha256:" + "a" * 64),
        )

    def test_tag_and_digest_strips_tag_from_name(self):
        # Regression: a ``name:tag@digest`` ref must not glue ``:tag`` onto the
        # repo name, or the manifest URL 404s.
        self.assertEqual(
            chk._parse_image_ref(
                "nvcr.io/nv-metropolis-dev/met/vss-agent:3.2.0-abc@sha256:" + "b" * 64
            ),
            ("nvcr.io", "nv-metropolis-dev/met/vss-agent", "sha256:" + "b" * 64),
        )

    def test_registry_port_with_digest(self):
        # The ``:port`` on the registry host must survive; only a tag in the last
        # path component is stripped.
        self.assertEqual(
            chk._parse_image_ref("localhost:5000/foo/bar:tag@sha256:" + "c" * 64),
            ("localhost:5000", "foo/bar", "sha256:" + "c" * 64),
        )


class TestPathIsIgnored(unittest.TestCase):
    PATTERNS = [
        "tests/",
        "stubs/",
        "README.md",
        "AGENTS.md",
        ".ignore_source_code_check",
        "**/*.snap",
    ]

    def ig(self, rel):
        return chk.path_is_ignored(rel, self.PATTERNS)

    def test_directory_prefix_ignored(self):
        self.assertTrue(self.ig("tests"))
        self.assertTrue(self.ig("tests/unit/test_x.py"))
        self.assertTrue(self.ig("stubs/nat/__init__.pyi"))

    def test_root_files_ignored(self):
        self.assertTrue(self.ig("README.md"))
        self.assertTrue(self.ig("AGENTS.md"))
        self.assertTrue(self.ig(".ignore_source_code_check"))

    def test_shipped_src_not_ignored(self):
        # A bare README.md must NOT ignore a shipped src/**/README.md.
        self.assertFalse(self.ig("src/agent/orchestrator/README.md"))
        self.assertFalse(self.ig("src/agent/tools/video_understanding.py"))
        self.assertFalse(self.ig("pyproject.toml"))
        self.assertFalse(self.ig("docker/Dockerfile"))

    def test_anydepth_glob(self):
        self.assertTrue(self.ig("src/components/Button.snap"))
        self.assertTrue(self.ig("foo.snap"))


class TestDiffIsIgnoredOnly(unittest.TestCase):
    def run_diff(self, changed, patterns=("tests/", "README.md")):
        repo = Path("/repo")
        src = Path("services/agent")
        cp = mock.Mock(
            returncode=0,
            stdout="\n".join(f"{src.as_posix()}/{c}" for c in changed) + "\n",
        )
        with mock.patch.object(chk, "run_git", return_value=cp):
            return chk.diff_is_ignored_only(repo, src, "aaa", "bbb", list(patterns))

    def test_all_ignored(self):
        ok, changed = self.run_diff(["tests/unit/test_x.py", "README.md"])
        self.assertTrue(ok)
        self.assertEqual(set(changed), {"tests/unit/test_x.py", "README.md"})

    def test_some_build_relevant(self):
        ok, _ = self.run_diff(["tests/unit/test_x.py", "src/agent/tools/x.py"])
        self.assertFalse(ok)

    def test_no_changes_returns_false(self):
        repo = Path("/repo")
        cp = mock.Mock(returncode=0, stdout="\n")
        with mock.patch.object(chk, "run_git", return_value=cp):
            ok, changed = chk.diff_is_ignored_only(
                repo, Path("services/agent"), "a", "b", ["tests/"]
            )
        self.assertFalse(ok)
        self.assertEqual(changed, [])

    def test_git_failure_returns_false(self):
        repo = Path("/repo")
        cp = mock.Mock(returncode=1, stdout="")
        with mock.patch.object(chk, "run_git", return_value=cp):
            ok, changed = chk.diff_is_ignored_only(
                repo, Path("services/agent"), "a", "b", ["tests/"]
            )
        self.assertFalse(ok)
        self.assertEqual(changed, [])


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
