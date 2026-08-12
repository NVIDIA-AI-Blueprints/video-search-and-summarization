#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit-style tests for the skill's read-only helper scripts."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
CONTEXT_SCRIPT = SCRIPT_DIR / "compose_helm_context.py"
VALIDATOR_SCRIPT = SCRIPT_DIR / "validate_chart_structure.py"


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class ContextInventoryTests(unittest.TestCase):
    def make_repo(self, root: Path) -> None:
        write(
            root / "deploy/docker/compose.yml",
            "include:\n  - path: ./services/compose.yml\n",
        )
        write(
            root / "deploy/docker/services/compose.yml",
            "include:\n  - path: ./demo/compose.yml\n",
        )
        write(root / "deploy/docker/containers.env", "VSS_CONTAINER_TAG=fixture\n")
        write(
            root / "deploy/docker/scripts/dev-profile.sh",
            "#!/bin/bash\n# Derive COMPOSE_PROFILES for developer deployments.\n",
        )
        write(
            root / "deploy/docker/services/demo/compose.yml",
            """services:
  # helm-sync: helm-only | Add a ServiceAccount with namespaced read access.
  demo:
    image: example.invalid/demo:${DEMO_TAG:-1.0.0}
    profiles: [llm_local_demo]
    ports:
      - "8080:8000" # helm-sync: replace | Create a ClusterIP Service on port 8080 targeting 8000.
    # Kubernetes needs a PodDisruptionBudget when replicas exceed one.
""",
        )
        write(
            root / "deploy/docker/developer-profiles/example/overrides.env",
            "LLM_MODE=local\nLLM_NAME_SLUG=demo\nCOMPOSE_PROFILES=llm_${LLM_MODE}_${LLM_NAME_SLUG}\n",
        )
        write(
            root / "deploy/helm/services/demo/Chart.yaml",
            "apiVersion: v2\nname: demo\nversion: 1.0.0\n",
        )
        write(root / "deploy/helm/services/demo/values.yaml", "enabled: true\n")
        write(root / "deploy/helm/services/demo/templates/_helpers.tpl", "{{/* helpers */}}\n")
        write(
            root / "deploy/helm/services/demo/templates/deployment.yaml",
            "apiVersion: apps/v1\nkind: Deployment\n",
        )
        write(
            root / "deploy/helm/developer-profiles/example/Chart.yaml",
            """apiVersion: v2
name: example
version: 1.0.0
dependencies:
  - name: demo
    version: 1.0.0
    repository: file://../../services/demo
    condition: demo.enabled
""",
        )
        write(root / "deploy/helm/developer-profiles/example/values.yaml", "demo:\n  enabled: true\n")

    def run_context(
        self, root: Path, selection: list[str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        arguments = [
            "python3",
            str(CONTEXT_SCRIPT),
            "--repo-root",
            str(root),
            *(selection or ["--path", "deploy/docker/services/demo"]),
            "--format",
            "json",
        ]
        return subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_inventory_extracts_directives_and_transitive_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repo(root)
            result = self.run_context(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(
                [item["action"] for item in report["directives"]],
                ["helm-only", "replace"],
            )
            self.assertEqual(
                report["transitive_consumer_charts"],
                ["deploy/helm/developer-profiles/example"],
            )
            self.assertEqual(
                report["docker_profile_consumers"],
                ["deploy/docker/developer-profiles/example"],
            )
            self.assertEqual(
                report["profile_helm_targets"],
                ["deploy/helm/developer-profiles/example"],
            )
            self.assertEqual(report["compose_files"][-1]["services"], ["demo"])
            self.assertEqual(report["compose_files"][-1]["environment_tokens"], ["DEMO_TAG"])
            self.assertEqual(report["service_inventory"][0]["image"], "example.invalid/demo:${DEMO_TAG:-1.0.0}")
            self.assertEqual(report["service_inventory"][0]["ports"], ["8080:8000"])
            self.assertEqual(len(report["deployment_comments"]), 1)
            self.assertIn(
                "deploy/docker/scripts/dev-profile.sh",
                report["source_context_files"],
            )
            self.assertIn(
                "deploy/docker/containers.env",
                report["source_context_files"],
            )

    def test_malformed_directive_is_a_hard_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repo(root)
            compose = root / "deploy/docker/services/demo/compose.yml"
            compose.write_text(
                compose.read_text(encoding="utf-8")
                + "# helm-sync: replace this vague requirement\n",
                encoding="utf-8",
            )
            result = self.run_context(root)
            self.assertEqual(result.returncode, 2, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(len(report["malformed_directives"]), 1)

    def test_changed_from_inventories_committed_delta(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repo(root)
            commands = [
                ["git", "init", "-q"],
                ["git", "config", "user.email", "fixture@localhost"],
                ["git", "config", "user.name", "Fixture"],
                ["git", "add", "."],
                ["git", "commit", "-qm", "base"],
                ["git", "tag", "fixture-base"],
            ]
            for command in commands:
                subprocess.run(command, cwd=root, check=True, capture_output=True)
            compose = root / "deploy/docker/services/demo/compose.yml"
            compose.write_text(
                compose.read_text(encoding="utf-8") + "    restart: unless-stopped\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "add", str(compose.relative_to(root))],
                cwd=root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-qm", "change compose"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            result = self.run_context(root, ["--changed-from", "fixture-base"])
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(len(report["selected_changes"]), 1)
            self.assertEqual(
                report["selected_changes"][0]["path"],
                "deploy/docker/services/demo/compose.yml",
            )
            self.assertEqual(report["selected_changes"][0]["origin"], "fixture-base...HEAD")


class ChartValidatorTests(unittest.TestCase):
    def make_charts(self, root: Path) -> tuple[Path, Path]:
        child = root / "deploy/helm/services/demo"
        parent = root / "deploy/helm/developer-profiles/example"
        write(
            child / "Chart.yaml",
            "apiVersion: v2\nname: demo\ntype: application\nversion: 1.0.0\n",
        )
        write(child / "values.yaml", "enabled: true\nimage:\n  tag: '1.0.0'\n")
        write(child / "configs/app.yaml", "feature: true\n")
        write(child / "templates/_helpers.tpl", "{{/* helpers */}}\n")
        write(
            child / "templates/configmap.yaml",
            """apiVersion: v1
kind: ConfigMap
data:
  app.yaml: |
{{ .Files.Get "configs/app.yaml" | indent 4 }}
{{- if .Files.Glob "configs/optional.yaml" }}
  optional.yaml: |
{{ .Files.Get "configs/optional.yaml" | indent 4 }}
{{- end }}
""",
        )
        write(
            parent / "Chart.yaml",
            """apiVersion: v2
name: example
type: application
version: 1.0.0
dependencies:
  - name: demo
    version: 1.0.0
    repository: file://../../services/demo
    condition: demo.enabled
""",
        )
        write(parent / "values.yaml", "demo:\n  enabled: true\n")
        write(
            parent / "Chart.lock",
            """dependencies:
- name: demo
  repository: file://../../services/demo
  version: 1.0.0
digest: sha256:fixture
generated: "2026-01-01T00:00:00Z"
""",
        )
        return child, parent

    def run_validator(self, root: Path, chart: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(VALIDATOR_SCRIPT),
                "--repo-root",
                str(root),
                "--chart",
                str(chart),
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_valid_leaf_and_parent_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child, parent = self.make_charts(root)
            child_result = self.run_validator(root, child)
            parent_result = self.run_validator(root, parent)
            self.assertEqual(child_result.returncode, 0, child_result.stdout)
            self.assertEqual(parent_result.returncode, 0, parent_result.stdout)

    def test_dependency_version_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child, parent = self.make_charts(root)
            chart_file = child / "Chart.yaml"
            chart_file.write_text(
                chart_file.read_text(encoding="utf-8").replace("1.0.0", "1.1.0"),
                encoding="utf-8",
            )
            result = self.run_validator(root, parent)
            report = json.loads(result.stdout)
            self.assertEqual(result.returncode, 1)
            self.assertTrue(
                any("does not match child" in item["message"] for item in report["findings"])
            )

    def test_missing_files_get_target_and_duplicate_key_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child, _ = self.make_charts(root)
            (child / "configs/app.yaml").unlink()
            (child / "values.yaml").write_text(
                "enabled: true\nenabled: false\n", encoding="utf-8"
            )
            result = self.run_validator(root, child)
            report = json.loads(result.stdout)
            messages = [item["message"] for item in report["findings"]]
            self.assertEqual(result.returncode, 1)
            self.assertTrue(any("duplicate key" in message for message in messages))
            self.assertTrue(any("references missing file" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
