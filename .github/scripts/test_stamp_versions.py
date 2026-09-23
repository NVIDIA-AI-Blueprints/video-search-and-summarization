#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for stamp_versions.py: one git-derived version in every hand-readable field."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stamp_versions as sv  # noqa: E402

SKILL = """---
name: vss-example
description: Example.
license: Apache-2.0
metadata:
  version: "1.0.0"
  author: "NVIDIA"
  vss-requires: "summarize"
---

# Example

Body text that mentions version: 9.9.9 and must not change.
"""

CONTAINERS_ENV = """# coordinates
VSS_CONTAINER_TAG="${VSS_CONTAINER_TAG:-develop-latest}"
# the version the edge reports
VSS_VERSION="1.0.0"
VSS_OTHER_VERSION="${VSS_OTHER_VERSION:-3.3.0-6}"
"""

VALUES = """global:
  externalHost: ""
# the version the ingress reports
vssVersion: "1.0.0"
vssIngress:
  enabled: false
  version: "not this one"
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True, capture_output=True
    ).stdout.strip()


def make_repo(tmp: str, *skills: str, charts: tuple[str, ...] = ("dev-profile-a",)) -> Path:
    repo = Path(tmp)
    git(repo, "init", "-q", "-b", "develop")
    git(repo, "config", "user.email", "test@test")
    git(repo, "config", "user.name", "test")
    for name in skills:
        path = repo / "skills" / "operations" / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(SKILL.replace("vss-example", name))
    env = repo / sv.CONTAINERS_ENV
    env.parent.mkdir(parents=True)
    env.write_text(CONTAINERS_ENV)
    for chart in charts:
        chart_dir = repo / "deploy/helm/developer-profiles" / chart
        (chart_dir / "templates").mkdir(parents=True)
        (chart_dir / "templates/vss-ingress.yaml").write_text("kind: Ingress\n")
        (chart_dir / "values.yaml").write_text(VALUES)
    # A chart with no ingress template carries no version and is never touched.
    plain = repo / "deploy/helm/services/plain"
    plain.mkdir(parents=True)
    (plain / "values.yaml").write_text("replicas: 1\n")
    (repo / "README.md").write_text("version: 0.0.1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "initial")
    return repo


class DeriveVersionTest(unittest.TestCase):
    def test_pre_release_tag_renders_as_semver(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp, "vss-a")
            git(repo, "tag", "v3.3.0rc0")
            git(repo, "commit", "-q", "--allow-empty", "-m", "later")
            self.assertEqual(sv.derive_version(repo), "3.3.0-rc0")

    def test_release_tag_is_the_bare_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp, "vss-a")
            git(repo, "tag", "v3.3.0")
            self.assertEqual(sv.derive_version(repo), "3.3.0")

    def test_distance_and_sha_are_never_part_of_the_stamp(self):
        """The stamp changes only when a tag is pushed, never per merge."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp, "vss-a")
            git(repo, "tag", "v3.3.0rc0")
            for i in range(3):
                git(repo, "commit", "-q", "--allow-empty", "-m", f"merge {i}")
            self.assertEqual(sv.derive_version(repo), "3.3.0-rc0")

    def test_non_release_tags_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp, "vss-a")
            git(repo, "tag", "v3.2.1")
            git(repo, "commit", "-q", "--allow-empty", "-m", "nightly")
            git(repo, "tag", "nightly-20260924")
            self.assertEqual(sv.derive_version(repo), "3.2.1")

    def test_no_release_tag_is_an_error_not_a_guess(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp, "vss-a")
            with self.assertRaisesRegex(RuntimeError, "no v\\* tag"):
                sv.derive_version(repo)


class SkillStampTest(unittest.TestCase):
    def test_only_the_frontmatter_version_changes(self):
        stamped = sv.skill_stamp(SKILL, "3.3.0-rc0")
        assert stamped is not None
        self.assertIn('  version: "3.3.0-rc0"\n', stamped)
        self.assertIn("mentions version: 9.9.9 and must not change", stamped)
        self.assertEqual(stamped.count("3.3.0-rc0"), 1)

    def test_quote_style_is_kept(self):
        single = SKILL.replace('version: "1.0.0"', "version: '1.0.0'")
        stamped = sv.skill_stamp(single, "3.3.0")
        assert stamped is not None
        self.assertIn("  version: '3.3.0'\n", stamped)

    def test_skill_without_a_version_is_reported_not_invented(self):
        self.assertIsNone(sv.skill_stamp(SKILL.replace('  version: "1.0.0"\n', ""), "3.3.0"))
        self.assertIsNone(sv.skill_stamp("# no frontmatter\n", "3.3.0"))

    def test_current_version_reads_the_frontmatter_only(self):
        self.assertEqual(sv.skill_current(SKILL), "1.0.0")
        self.assertIsNone(sv.skill_current("# body version: 1.2.3\n"))


class EdgeStampTest(unittest.TestCase):
    def test_containers_env_line_only(self):
        current, stamp = sv._env_current, sv._env_stamp
        self.assertEqual(current(CONTAINERS_ENV), "1.0.0")
        stamped = stamp(CONTAINERS_ENV, "3.3.0-rc0")
        assert stamped is not None
        self.assertIn('\nVSS_VERSION="3.3.0-rc0"\n', stamped)
        # Neighbouring version-shaped pins and shell-default forms are untouched.
        self.assertIn('VSS_OTHER_VERSION="${VSS_OTHER_VERSION:-3.3.0-6}"', stamped)
        self.assertIn('VSS_CONTAINER_TAG="${VSS_CONTAINER_TAG:-develop-latest}"', stamped)

    def test_containers_env_with_a_shell_default_is_not_a_stamp_field(self):
        """`VSS_VERSION="${VSS_VERSION:-x}"` would let a shell override it; refuse it."""
        text = CONTAINERS_ENV.replace('VSS_VERSION="1.0.0"', 'VSS_VERSION="${VSS_VERSION:-1.0.0}"')
        self.assertIsNone(sv._env_current(text))

    def test_values_top_level_key_only(self):
        self.assertEqual(sv._values_current(VALUES), "1.0.0")
        stamped = sv._values_stamp(VALUES, "3.3.0-rc0")
        assert stamped is not None
        self.assertIn('\nvssVersion: "3.3.0-rc0"\n', stamped)
        self.assertIn('  version: "not this one"', stamped)


class MainTest(unittest.TestCase):
    def test_check_fails_until_stamped_then_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp, "vss-a", "vss-b", charts=("dev-profile-a", "warehouse-x"))
            git(repo, "tag", "v3.3.0rc0")

            self.assertEqual(sv.main(["--repo-root", tmp, "--check"]), 1)
            self.assertEqual(sv.main(["--repo-root", tmp]), 0)
            self.assertEqual(sv.main(["--repo-root", tmp, "--check"]), 0)

            for name in ("vss-a", "vss-b"):
                text = (repo / "skills/operations" / name / "SKILL.md").read_text()
                self.assertEqual(sv.skill_current(text), "3.3.0-rc0")
            self.assertEqual(sv._env_current((repo / sv.CONTAINERS_ENV).read_text()), "3.3.0-rc0")
            for chart in ("dev-profile-a", "warehouse-x"):
                values = (repo / "deploy/helm/developer-profiles" / chart / "values.yaml").read_text()
                self.assertEqual(sv._values_current(values), "3.3.0-rc0")
            # Files outside the targets are never touched.
            self.assertEqual((repo / "README.md").read_text(), "version: 0.0.1\n")
            self.assertEqual((repo / "deploy/helm/services/plain/values.yaml").read_text(), "replicas: 1\n")

    def test_check_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp, "vss-a")
            git(repo, "tag", "v3.3.0rc0")
            sv.main(["--repo-root", tmp, "--check"])
            self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_explicit_version_bypasses_git_and_must_be_semver(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp, "vss-a")
            self.assertEqual(sv.main(["--repo-root", tmp, "--version", "4.0.0"]), 0)
            text = (repo / "skills/operations/vss-a/SKILL.md").read_text()
            self.assertEqual(sv.skill_current(text), "4.0.0")
            self.assertEqual(sv.main(["--repo-root", tmp, "--version", "v4"]), 2)

    def test_skill_without_a_version_fails_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp, "vss-a")
            path = repo / "skills/operations/vss-a/SKILL.md"
            path.write_text(SKILL.replace('  version: "1.0.0"\n', ""))
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "drop version")
            git(repo, "tag", "v3.3.0rc0")
            self.assertEqual(sv.main(["--repo-root", tmp]), 2)

    def test_ingress_chart_without_vss_version_fails_the_run(self):
        """A chart that renders /api/v1/version must declare what it answers."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp, "vss-a")
            values = repo / "deploy/helm/developer-profiles/dev-profile-a/values.yaml"
            values.write_text(VALUES.replace('vssVersion: "1.0.0"\n', ""))
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "drop key")
            git(repo, "tag", "v3.3.0rc0")
            self.assertEqual(sv.main(["--repo-root", tmp]), 2)


class RepositoryWiringTest(unittest.TestCase):
    """The stamped fields are only worth stamping if the edges read them."""

    ROOT = Path(__file__).resolve().parents[2]

    def test_compose_edge_returns_the_stamped_version(self):
        template = (self.ROOT / "deploy/docker/services/infra/haproxy/haproxy.cfg.template").read_text()
        self.assertIn("acl p_api_version path /api/v1/version", template)
        self.assertIn('lf-string \'{"service":"vss","version":"%[env(VSS_VERSION)]"}\' if h_main p_api_version', template)
        # The return must be evaluated before /api is routed to the agent.
        self.assertLess(template.index("p_api_version"), template.index("use_backend bk_vss_agent if h_main p_api"))
        compose = (self.ROOT / "deploy/docker/services/infra/haproxy/compose.yml").read_text()
        self.assertIn("VSS_VERSION: ${VSS_VERSION}", compose)
        env = (self.ROOT / sv.CONTAINERS_ENV).read_text()
        self.assertIsNotNone(sv._env_current(env), "containers.env must carry a stamped VSS_VERSION line")

    def test_every_ingress_chart_renders_the_version_route(self):
        charts = sv.chart_values_files(self.ROOT)
        self.assertGreaterEqual(len(charts), 7)
        for values in charts:
            template = (values.parent / sv.INGRESS_TEMPLATE).read_text()
            with self.subTest(chart=values.parent.name):
                self.assertIn("haproxy.org/frontend-config-snippet", template)
                self.assertIn("/api/v1/version", template)
                self.assertIn(".Values.vssVersion", template)
                self.assertIsNotNone(sv._values_current(values.read_text()), f"{values} lacks vssVersion")


if __name__ == "__main__":
    unittest.main(verbosity=2)
