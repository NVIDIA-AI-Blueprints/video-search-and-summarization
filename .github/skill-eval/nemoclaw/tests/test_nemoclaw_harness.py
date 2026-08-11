# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[4]
NEMOCLAW_DIR = REPO_ROOT / ".github/skill-eval/nemoclaw"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NotebookAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.adapter = _load(
            "notebook_setup_adapter",
            NEMOCLAW_DIR / "notebook_setup_adapter.py",
        )

    def test_manifest_selects_current_notebook_cells(self) -> None:
        manifest = json.loads(
            (NEMOCLAW_DIR / "notebook_cells.json").read_text(encoding="utf-8")
        )
        notebooks = []
        for section in manifest["notebooks"]:
            notebook = json.loads(
                (REPO_ROOT / section["notebook"]).read_text(encoding="utf-8")
            )
            ids = {cell.get("id") for cell in notebook["cells"]}
            self.assertTrue(set(section["cells"]) <= ids)
            notebooks.append(notebook)

        composed = self.adapter.build_notebooks(notebooks, manifest)
        cells = {cell.get("id"): cell for cell in composed["cells"]}
        self.assertEqual(len(composed["cells"]), 22)
        self.assertIn("mcporter", cells["s36-code"]["source"])
        self.assertIn("--no-install-package", cells["c13aaf5e"]["source"])
        self.assertIn("ci-persist-env", cells)

    def test_adapter_has_no_custom_secret_scrubber(self) -> None:
        source = (NEMOCLAW_DIR / "notebook_setup_adapter.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("SECRET" + "_TEXT_PATTERNS", source)
        self.assertNotIn("def _" + "redact", source)
        self.assertIn("executed notebook was not persisted", source)


class HeadlessTrajectoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = _load(
            "headless_runner",
            NEMOCLAW_DIR / "headless_runner.py",
        )

    def test_openclaw_exec_is_normalized_to_bash_with_metrics(self) -> None:
        rows = [
            {
                "type": "message",
                "timestamp": "2026-08-11T00:00:00Z",
                "message": {"role": "user", "content": "deploy"},
            },
            {
                "type": "message",
                "timestamp": "2026-08-11T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolCall",
                            "id": "call-1",
                            "name": "exec",
                            "arguments": {"cmd": "echo ready"},
                        }
                    ],
                    "usage": {
                        "input": 10,
                        "cacheRead": 2,
                        "output": 3,
                    },
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "toolResult",
                    "toolCallId": "call-1",
                    "content": [{"type": "text", "text": "ready"}],
                },
            },
        ]
        envelope = {
            "meta": {
                "agentMeta": {
                    "sessionId": "session-1",
                    "model": "test-model",
                    "usage": {"input": 10, "cacheRead": 2, "output": 3},
                }
            }
        }
        trajectory, metrics = self.runner._session_to_atif(
            "\n".join(json.dumps(row) for row in rows),
            envelope,
            "deploy",
        )
        call = trajectory["steps"][1]["tool_calls"][0]
        self.assertEqual(call["function_name"], "Bash")
        self.assertEqual(call["arguments"]["command"], "echo ready")
        self.assertEqual(metrics["turns"], 1)
        self.assertEqual(metrics["prompt_tokens"], 10)
        self.assertEqual(metrics["cached_tokens"], 2)
        self.assertEqual(
            trajectory["final_metrics"]["total_prompt_tokens"], 12
        )


class SingleScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scenario = _load(
            "single_scenario",
            NEMOCLAW_DIR / "single_scenario.py",
        )

    def test_task_wrapper_is_fail_closed_nemoclaw_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary)
            (task / "tests").mkdir()
            (task / "instruction.md").write_text(
                "Deploy the base profile.", encoding="utf-8"
            )
            (task / "task.toml").write_text(
                "[task]\nname = \"test\"\n\n"
                "[metadata]\nplatform = \"RTXPRO6000BW\"\n\n"
                "[verifier.env]\nANTHROPIC_API_KEY = \"x\"\n",
                encoding="utf-8",
            )
            self.scenario._wrap_task(task, 3300)
            parsed = tomllib.loads(
                (task / "task.toml").read_text(encoding="utf-8")
            )
            self.assertEqual(parsed["metadata"]["runner"], "nemoclaw")
            self.assertTrue(parsed["metadata"]["requires_mcp"])
            self.assertEqual(
                parsed["metadata"]["expected_skill"], "vss-deploy-profile"
            )
            self.assertIn(
                "headless_runner.py",
                (task / "instruction.md").read_text(encoding="utf-8"),
            )
            prompt = (task / "tests/nemoclaw_prompt.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("Original eval request", prompt)
            self.assertIn("Deploy the base profile.", prompt)

    def test_timeout_budgets_are_nested(self) -> None:
        self.scenario._validate_timeouts(
            setup_timeout=5400,
            agent_timeout=3300,
            harbor_timeout=12600,
        )
        with self.assertRaises(ValueError):
            self.scenario._validate_timeouts(
                setup_timeout=6000,
                agent_timeout=3300,
                harbor_timeout=12600,
            )
        with self.assertRaises(ValueError):
            self.scenario._validate_timeouts(
                setup_timeout=5400,
                agent_timeout=3300,
                harbor_timeout=11100,
            )

    def test_success_requires_native_turn_and_token_metrics(self) -> None:
        self.assertTrue(
            self.scenario._native_metrics_valid(
                {"turns": 3, "prompt_tokens": 1200, "cached_tokens": 0}
            )
        )
        self.assertFalse(
            self.scenario._native_metrics_valid(
                {"turns": "n/a", "prompt_tokens": 1200, "cached_tokens": 0}
            )
        )
        self.assertFalse(
            self.scenario._native_metrics_valid(
                {"turns": 3, "prompt_tokens": 0, "cached_tokens": 0}
            )
        )
        self.assertFalse(
            self.scenario._native_metrics_valid(
                {"turns": 3, "prompt_tokens": 1200}
            )
        )

    def test_latest_trial_ignores_run_level_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            date = root / "2026-08-11"
            trial = date / "trial-1"
            (trial / "verifier").mkdir(parents=True)
            (trial / "verifier/reward.txt").write_text(
                "1.0", encoding="utf-8"
            )
            (trial / "result.json").write_text(
                '{"scope": "trial"}', encoding="utf-8"
            )
            (date / "result.json").write_text(
                '{"scope": "run"}', encoding="utf-8"
            )
            selected, result = self.scenario._latest_trial(root)
            self.assertEqual(selected, trial)
            self.assertEqual(result["scope"], "trial")

    def test_harbor_uses_isolated_nemoclaw_environment(self) -> None:
        with (
            mock.patch.object(self.scenario, "_uvx", return_value="uvx"),
            mock.patch.dict(
                os.environ,
                {"ANTHROPIC_MODEL": "test-model"},
                clear=False,
            ),
        ):
            command = self.scenario._harbor_command(
                Path("/tmp/dataset"),
                Path("/tmp/results"),
                "123",
            )
        self.assertIn(
            "envs.nemoclaw_brev_env:NemoClawBrevEnvironment",
            command,
        )
        self.assertEqual(
            command[command.index("--from") + 1],
            self.scenario.HARBOR_REQUIREMENT,
        )
        self.assertEqual(
            command[command.index("--python") + 1],
            self.scenario.sys.executable,
        )
        self.assertEqual(command[command.index("-a") + 1], "claude-code")

    def test_uvx_resolves_user_install_outside_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            uvx = Path(temporary) / "bin" / "uvx"
            uvx.parent.mkdir()
            uvx.write_text("", encoding="utf-8")
            with (
                mock.patch.object(
                    self.scenario.shutil, "which", return_value=None
                ),
                mock.patch.object(
                    self.scenario.site,
                    "getuserbase",
                    return_value=temporary,
                ),
            ):
                self.assertEqual(self.scenario._uvx(), str(uvx))


class WorkflowScopeTests(unittest.TestCase):
    def test_remote_notebook_kernel_uses_supported_python(self) -> None:
        source = (
            REPO_ROOT / ".github/skill-eval/envs/nemoclaw_brev_env.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "uv python install --reinstall --force --no-cache 3.12",
            source,
        )
        self.assertIn("uv venv --managed-python --python 3.12", source)
        self.assertNotIn("python3 -m venv", source)

    def test_workflow_keeps_claude_default_and_bounds_nemoclaw(self) -> None:
        workflow = (
            REPO_ROOT / ".github/workflows/skills-eval.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('default: "claude-code"', workflow)
        self.assertIn("inputs.runner != 'nemoclaw'", workflow)
        self.assertIn("nemoclaw_instance must name", workflow)
        self.assertIn("timeout-minutes: 240", workflow)
        self.assertIn("timeout-minutes: 220", workflow)
        self.assertIn("NEMOCLAW_HARBOR_TIMEOUT_SEC=12600", workflow)
        self.assertIn("--exclude='agent'", workflow)

    def test_excluded_subsystems_are_not_in_scoped_sources(self) -> None:
        paths = [
            *NEMOCLAW_DIR.glob("*.py"),
            REPO_ROOT / ".github/skill-eval/envs/nemoclaw_brev_env.py",
            REPO_ROOT / ".github/workflows/skills-eval.yml",
        ]
        source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        self.assertNotIn("remote" + "_worker_lock", source)
        self.assertNotIn("setup" + "_failure", source)
        self.assertNotIn("smoke" + "_runner.py", source)
        self.assertNotIn("report" + "_results.py", source)


if __name__ == "__main__":
    unittest.main()
