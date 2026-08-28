#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for detect_sonarqube_projects.py. Run directly:

    python3 .github/scripts/test_detect_sonarqube_projects.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import detect_sonarqube_projects as dsp  # noqa: E402


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def make_repo(tmp: str) -> Path:
    repo = Path(tmp)
    git(repo, "init", "-q", "-b", "develop")
    git(repo, "config", "user.email", "test@test")
    git(repo, "config", "user.name", "test")
    for rel in (
        "services/agent/app.py",
        "services/ui/app.js",
        "skills/README.md",
        "docs/readme.md",
        ".github/workflows/sonarqube.yml",
        ".github/scripts/detect_sonarqube_projects.py",
    ):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("v1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "initial")
    return repo


def commit_change(repo: Path, rel: str, content: str, message: str) -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


class SelectProjectsTest(unittest.TestCase):
    def test_none_diff_scans_everything(self):
        selected, reason = dsp.select_projects(None)
        self.assertEqual(len(selected), len(dsp.PROJECTS))
        self.assertIn("all", reason)

    def test_unrelated_paths_scan_nothing(self):
        selected, reason = dsp.select_projects(["docs/readme.md", "README.md"])
        self.assertEqual(selected, [])
        self.assertIn("0 of", reason)

    def test_agent_change_scans_only_agent(self):
        selected, _ = dsp.select_projects(["services/agent/packages/vss_cli/src/x.py"])
        self.assertEqual([project["name"] for project in selected], ["agent"])

    def test_sibling_directory_does_not_match(self):
        selected, _ = dsp.select_projects(["services/agent-old/app.py"])
        self.assertEqual(selected, [])

    def test_workflow_contract_scans_everything(self):
        selected, reason = dsp.select_projects([".github/workflows/sonarqube.yml"])
        self.assertEqual(len(selected), len(dsp.PROJECTS))
        self.assertIn("contract", reason)

    def test_script_contract_scans_everything(self):
        selected, reason = dsp.select_projects(
            [".github/scripts/detect_sonarqube_projects.py"]
        )
        self.assertEqual(len(selected), len(dsp.PROJECTS))
        self.assertIn("contract", reason)

    def test_matrix_entry_omits_paths_and_sets_scan_runner(self):
        entry = dsp.matrix_entry(dsp.PROJECTS[0], scan=True)
        self.assertNotIn("paths", entry)
        self.assertEqual(entry["name"], "agent")
        self.assertEqual(entry["python_version"], "3.13")
        self.assertEqual(entry["scan"], "true")
        self.assertEqual(entry["runner"], dsp.SONAR_RUNNER)
        skip = dsp.matrix_entry(dsp.PROJECTS[0], scan=False)
        self.assertEqual(skip["scan"], "false")
        self.assertEqual(skip["runner"], dsp.SKIP_RUNNER)

    def test_pr_reports_every_project_as_noop_when_nothing_changed(self):
        include = dsp.matrix_for([], "pull_request", "develop")
        self.assertEqual(len(include), len(dsp.PROJECTS))
        self.assertEqual({row["scan"] for row in include}, {"false"})
        self.assertEqual(
            [row["name"] for row in include],
            [project["name"] for project in dsp.PROJECTS],
        )

    def test_pr_scans_only_the_changed_project(self):
        ui = next(project for project in dsp.PROJECTS if project["name"] == "ui")
        include = dsp.matrix_for([ui], "pull_request", "main")
        by_name = {row["name"]: row["scan"] for row in include}
        self.assertEqual(len(by_name), len(dsp.PROJECTS))
        self.assertEqual(by_name["ui"], "true")
        self.assertEqual(
            {name for name, scan in by_name.items() if scan == "true"},
            {"ui"},
        )

    def test_push_does_not_pad_unselected_projects(self):
        ui = next(project for project in dsp.PROJECTS if project["name"] == "ui")
        include = dsp.matrix_for([ui], "push", "develop")
        self.assertEqual([(row["name"], row["scan"]) for row in include], [("ui", "true")])

    def test_project_names_are_unique(self):
        names = [project["name"] for project in dsp.PROJECTS]
        self.assertEqual(names, list(dict.fromkeys(names)))


class PlanTest(unittest.TestCase):
    def test_push_scans_all_even_when_only_docs_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            commit_change(repo, "docs/readme.md", "v2\n", "docs")
            result = dsp.plan(repo, "push", "develop", "")
            self.assertTrue(result["any"])
            self.assertEqual(result["count"], len(dsp.PROJECTS))
            self.assertIn("push", result["reason"])

    def test_workflow_dispatch_scans_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            result = dsp.plan(repo, "workflow_dispatch", "", "")
            self.assertEqual(result["count"], len(dsp.PROJECTS))

    def test_pull_request_skips_unrelated_trees(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            base_sha = git(repo, "rev-parse", "HEAD")
            git(repo, "checkout", "-q", "-b", "feature")
            commit_change(repo, "docs/readme.md", "v2\n", "docs only")
            result = dsp.plan(repo, "pull_request", "develop", base_sha)
            self.assertEqual(result["count"], 0)
            self.assertEqual(result["projects"], [])
            self.assertTrue(result["any"])
            include = result["matrix"]["include"]
            self.assertEqual(len(include), len(dsp.PROJECTS))
            self.assertEqual({row["scan"] for row in include}, {"false"})

    def test_pull_request_to_main_also_reports_every_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            base_sha = git(repo, "rev-parse", "HEAD")
            git(repo, "checkout", "-q", "-b", "feature")
            commit_change(repo, "docs/readme.md", "v2\n", "docs only")
            result = dsp.plan(repo, "pull_request", "main", base_sha)
            self.assertTrue(result["any"])
            self.assertEqual(result["count"], 0)
            self.assertEqual(len(result["matrix"]["include"]), len(dsp.PROJECTS))

    def test_pull_request_scans_only_changed_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            base_sha = git(repo, "rev-parse", "HEAD")
            git(repo, "checkout", "-q", "-b", "feature")
            commit_change(repo, "services/ui/app.js", "v2\n", "ui")
            result = dsp.plan(repo, "pull_request", "develop", base_sha)
            self.assertEqual(result["projects"], ["ui"])
            by_name = {row["name"]: row for row in result["matrix"]["include"]}
            self.assertEqual(len(by_name), len(dsp.PROJECTS))
            self.assertEqual(by_name["ui"]["scan"], "true")
            self.assertEqual(by_name["agent"]["scan"], "false")
            self.assertEqual(by_name["alert"]["scan"], "false")
            self.assertEqual(by_name["skills"]["scan"], "false")
            self.assertNotIn("python_version", by_name["ui"])

    def test_missing_pr_base_fails_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            result = dsp.plan(repo, "pull_request", "no-such-branch", "0" * 40)
            self.assertEqual(result["count"], len(dsp.PROJECTS))
            self.assertIn("could not resolve PR base", result["reason"])

    def test_github_output_is_single_line_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            result = dsp.plan(repo, "workflow_dispatch", "", "")
            output = Path(tmp) / "github-output"
            dsp.write_github_output(output, result)
            lines = {
                line.split("=", 1)[0]: line.split("=", 1)[1]
                for line in output.read_text().splitlines()
            }
            self.assertEqual(lines["any"], "true")
            self.assertEqual(int(lines["count"]), len(dsp.PROJECTS))
            self.assertNotIn("\n", lines["matrix"])
            parsed = json.loads(lines["matrix"])
            self.assertEqual(len(parsed["include"]), len(dsp.PROJECTS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
