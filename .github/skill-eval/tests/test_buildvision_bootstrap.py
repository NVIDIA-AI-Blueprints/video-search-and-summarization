#!/usr/bin/env python3
"""Tests for the Build Vision AI -> NemoClaw provisioning handoff."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "buildvision_bootstrap", _ROOT / "nemoclaw" / "buildvision_bootstrap.py"
)
bootstrap = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bootstrap)


class BuildVisionBootstrapTest(unittest.TestCase):
    def test_creates_a_coding_agent_task_from_the_operational_spec(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            build_skill = repo / "skills" / "vss-build-vision-ai"
            build_skill.mkdir(parents=True)
            (build_skill / "SKILL.md").write_text("# build\n")
            source_task = root / "source" / "task.toml"
            source_task.parent.mkdir(parents=True)
            source_task.write_text(
                "[metadata]\ngpu_type = \"L40S\"\ngpu_count = 1\n"
            )
            spec = root / "alerts.json"
            spec.write_text(json.dumps({"profile": "alerts", "deploy_mode": "verification"}))

            project = bootstrap.create_bootstrap_task(
                destination=root / "bootstrap",
                source_task_toml=source_task,
                spec_path=spec,
                skill="vss-manage-alerts",
                platform="L40S",
                repo_root=repo,
            )

            task = project / bootstrap.BOOTSTRAP_TASK
            self.assertEqual((task / "task.toml").read_text(), source_task.read_text())
            instruction = (task / "instruction.md").read_text()
            self.assertIn("`alerts` VSS profile", instruction)
            self.assertIn("`verification` mode", instruction)
            self.assertIn("`/vss-manage-alerts`", instruction)
            self.assertIn("host-side\nNemoClaw", instruction)
            self.assertTrue((task / "skills" / "vss-build-vision-ai" / "SKILL.md").is_file())
            verifier = (task / "tests" / "test.sh").read_text()
            self.assertIn("openshell sandbox exec", verifier)
            self.assertIn('"$reward_dir/reward.txt"', verifier)

    def test_rejects_a_non_object_spec(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.json"
            path.write_text("[]")
            with self.assertRaisesRegex(ValueError, "not a JSON object"):
                bootstrap._spec_deployment(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
