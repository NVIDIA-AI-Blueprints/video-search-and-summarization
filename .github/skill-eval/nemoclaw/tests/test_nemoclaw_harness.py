# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
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
        self.assertEqual(len(composed["cells"]), 24)
        self.assertIn("_cdi_gpu_devices", cells["f6a006d3"]["source"])
        self.assertIn(
            "AGENT_HOOKS_ENABLED = False",
            cells["e67f6da4"]["source"],
        )
        skill_source = cells["s34-code"]["source"]
        self.assertIn("shields down ", skill_source)
        self.assertIn("--timeout 15m --reason", skill_source)
        self.assertIn("try:\n", skill_source)
        self.assertIn("finally:\n", skill_source)
        self.assertIn("shields up", skill_source)
        self.assertLess(
            skill_source.index("try:\n"),
            skill_source.index("shields down"),
        )
        self.assertLess(
            skill_source.index("shields down"),
            skill_source.index("_skill_install_cmd"),
        )
        self.assertLess(
            skill_source.index("_skill_install_cmd"),
            skill_source.index("finally:\n"),
        )
        self.assertLess(
            skill_source.index("finally:\n"),
            skill_source.index("shields up"),
        )
        workspace_source = cells["s35-code"]["source"]
        self.assertIn('reason "skill-eval workspace setup"', workspace_source)
        self.assertIn("shields down failed before workspace upload", workspace_source)
        self.assertIn("_remote_doc =", workspace_source)
        self.assertIn("rm -rf --", workspace_source)
        self.assertIn("workspace cleanup failed", workspace_source)
        self.assertLess(
            workspace_source.index("_cleanup_script"),
            workspace_source.index("_upload_cmd"),
        )
        self.assertLess(
            workspace_source.index("try:\n"),
            workspace_source.index("shields down"),
        )
        self.assertLess(
            workspace_source.index("_upload_cmd"),
            workspace_source.index("finally:\n"),
        )
        self.assertLess(
            workspace_source.index("finally:\n"),
            workspace_source.index("shields up"),
        )
        self.assertIn("mcporter", cells["s36-code"]["source"])
        rtsp_source = cells["s37-code"]["source"]
        self.assertIn("config_sets = []", rtsp_source)
        self.assertIn("env.vars.RTSP_SAMPLE_URL", rtsp_source)
        self.assertIn("requires the fixed public relay", rtsp_source)
        self.assertNotIn("print(_rtsp_sample_url", rtsp_source)
        self.assertIn("--no-install-package", cells["c13aaf5e"]["source"])
        self.assertIn("ensure_agent_venv", cells["c13aaf5e"]["source"])
        self.assertIn(
            'env.pop("UV_PROJECT_ENVIRONMENT", None)',
            cells["c13aaf5e"]["source"],
        )
        self.assertIn(
            "Refusing to replace symlinked orchestrator environment",
            cells["c13aaf5e"]["source"],
        )
        compile(
            cells["c13aaf5e"]["source"],
            "deploy_vss_orchestrator.ipynb:c13aaf5e",
            "exec",
        )
        mcp_source = cells["042eabd1"]["source"]
        self.assertIn(
            "Path(config_arg).resolve() == Path(MCP_CONFIG_PATH).resolve()", mcp_source
        )
        self.assertIn('Path(arg).name == "nat"', mcp_source)
        self.assertIn("Prepared MCP port for the current checkout", mcp_source)
        self.assertIn("orchestrator-mcp.pid", mcp_source)
        self.assertIn("os.kill(_pid, signal.SIGTERM)", mcp_source)
        compile(
            mcp_source,
            "deploy_vss_orchestrator.ipynb:042eabd1",
            "exec",
        )
        self.assertIn("ci-persist-env", cells)

    def test_invalid_orchestrator_venv_is_rebuilt(self) -> None:
        notebook = json.loads(
            (
                REPO_ROOT / "deploy/docker/scripts/deploy_vss_orchestrator.ipynb"
            ).read_text(encoding="utf-8")
        )
        cell = next(cell for cell in notebook["cells"] if cell.get("id") == "c13aaf5e")
        patched = self.adapter._patch_ci_cell(
            "c13aaf5e", self.adapter._normalize_cell(cell)
        )
        tree = ast.parse(patched["source"])
        functions = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {"uv_env_for_agent", "ensure_agent_venv"}
        ]
        module = ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[]))

        with tempfile.TemporaryDirectory() as temporary:
            agent_dir = Path(temporary) / "services/agent"
            venv_dir = agent_dir / ".venv"
            venv_dir.mkdir(parents=True)
            commands: list[tuple[list[str], dict[str, object]]] = []

            def fake_run(command, **kwargs):
                commands.append((command, kwargs))
                if command == ["uv", "venv", "--help"]:
                    return subprocess.CompletedProcess(command, 0, stdout="  --force\n")
                python = venv_dir / "bin/python"
                python.parent.mkdir(parents=True, exist_ok=True)
                python.write_text("#!/bin/sh\n", encoding="utf-8")
                python.chmod(0o755)
                return subprocess.CompletedProcess(command, 0)

            namespace = {
                "AGENT_DIR": agent_dir,
                "ORCHESTRATOR_MCP_VENV_DIR": venv_dir,
                "ORCHESTRATOR_MCP_PYTHON_VERSION": "3.13",
                "os": os,
                "subprocess": mock.Mock(run=fake_run),
            }
            exec(  # noqa: S102 - execute only AST extracted from a checked-in cell.
                compile(module, "orchestrator-venv-repair", "exec"),
                namespace,
            )
            with mock.patch.dict(
                os.environ,
                {
                    "VIRTUAL_ENV": "/outside/kernel",
                    "UV_PROJECT_ENVIRONMENT": "/outside/project",
                },
            ):
                namespace["ensure_agent_venv"]()

            self.assertEqual(commands[0][0], ["uv", "venv", "--help"])
            self.assertEqual(
                commands[1][0],
                [
                    "uv",
                    "venv",
                    "--clear",
                    "--force",
                    "--python",
                    "3.13",
                    str(venv_dir),
                ],
            )
            for _, kwargs in commands:
                self.assertNotIn("VIRTUAL_ENV", kwargs["env"])
                self.assertNotIn("UV_PROJECT_ENVIRONMENT", kwargs["env"])

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

    def test_real_openclaw_result_envelope_is_unwrapped(self) -> None:
        document = {
            "runId": "run-1",
            "status": "success",
            "summary": "completed",
            "result": {
                "payloads": [{"text": "done"}],
                "meta": {
                    "agentMeta": {
                        "sessionId": "session-1",
                        "sessionFile": (
                            "/sandbox/.openclaw/agents/main/sessions/session-1.jsonl"
                        ),
                    }
                },
            },
        }

        envelope = self.runner._json_object(
            "OpenClaw warning before result\n" + json.dumps(document, indent=2)
        )

        self.assertEqual(envelope, document["result"])
        self.assertEqual(
            self.runner._session_file(envelope),
            "/sandbox/.openclaw/agents/main/sessions/session-1.jsonl",
        )

    def test_openclaw_wrapped_exec_is_normalized_with_metrics(self) -> None:
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
                            "name": "tool_call",
                            "arguments": {
                                "id": "openclaw:core:exec",
                                "args": {
                                    "command": "echo ready",
                                    "yieldMs": 30_000,
                                },
                            },
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
        self.assertEqual(call["arguments"]["yieldMs"], 30_000)
        self.assertEqual(metrics["turns"], 1)
        self.assertEqual(metrics["prompt_tokens"], 10)
        self.assertEqual(metrics["cached_tokens"], 2)
        self.assertEqual(trajectory["final_metrics"]["total_prompt_tokens"], 12)


class GatewayReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.release = _load(
            "release_gateway_port",
            NEMOCLAW_DIR / "release_gateway_port.py",
        )

    def test_release_is_scoped_to_exact_nemoclaw_gateway_identity(self) -> None:
        identity = self.release.ProcessIdentity(
            pid=123,
            start_time=456,
            argv=("openshell-gateway[nemoclaw=nemoclaw;port=8080]",),
            executable="/usr/local/bin/openshell-gateway",
        )
        unrelated = self.release.ProcessIdentity(
            pid=124,
            start_time=457,
            argv=("python3", "-m", "http.server", "8080"),
            executable="/usr/bin/python3",
        )

        self.assertTrue(self.release._is_managed_gateway(identity, 8080))
        self.assertFalse(self.release._is_managed_gateway(identity, 19080))
        self.assertFalse(self.release._is_managed_gateway(unrelated, 8080))


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
                '[task]\nname = "test"\n\n'
                '[metadata]\nplatform = "RTXPRO6000BW"\n\n'
                '[verifier.env]\nANTHROPIC_API_KEY = "x"\n',
                encoding="utf-8",
            )
            row = self.scenario.MatrixRow(
                "vss-deploy-profile",
                "base",
                "RTXPRO6000BW",
                1,
                "base",
            )
            self.scenario._wrap_task(task, row, 3300)
            parsed = tomllib.loads((task / "task.toml").read_text(encoding="utf-8"))
            self.assertEqual(parsed["metadata"]["runner"], "nemoclaw")
            self.assertTrue(parsed["metadata"]["requires_mcp"])
            self.assertEqual(parsed["metadata"]["expected_skill"], "vss-deploy-profile")
            self.assertIn(
                "headless_runner.py",
                (task / "instruction.md").read_text(encoding="utf-8"),
            )
            prompt = (task / "tests/nemoclaw_prompt.md").read_text(encoding="utf-8")
            self.assertIn("Original eval request", prompt)
            self.assertIn("Deploy the base profile.", prompt)

    def test_compatible_matrix_has_eight_rows_and_nineteen_tasks(self) -> None:
        rows = self.scenario._selected_rows("*")
        self.assertEqual(len(rows), 8)
        self.assertEqual(sum(row.task_limit for row in rows), 19)
        self.assertEqual(
            [row.skill for row in rows],
            [
                "vss-ask-video",
                "vss-deploy-dense-captioning",
                "vss-deploy-profile",
                "vss-generate-video-report",
                "vss-manage-alerts",
                "vss-query-analytics",
                "vss-setup-behavior-analytics",
                "vss-summarize-video",
            ],
        )
        self.assertEqual(
            self.scenario._selected_rows("vss-deploy-profile"),
            [rows[2]],
        )
        with self.assertRaisesRegex(ValueError, "not NemoClaw-compatible"):
            self.scenario._selected_rows("vss-search-archive")

    def test_current_adapters_generate_validated_ordered_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for row in self.scenario.COMPATIBLE_ROWS:
                task_dirs = self.scenario._generate_dataset(
                    row,
                    root / row.slug,
                )
                self.assertEqual(len(task_dirs), row.task_limit)
                self.assertEqual(
                    task_dirs,
                    sorted(task_dirs, key=self.scenario._task_dir_sort_key),
                )
                for task_dir in task_dirs:
                    scenario = self.scenario._wrap_task(task_dir, row, 3300)
                    metadata = tomllib.loads(
                        (task_dir / "task.toml").read_text(encoding="utf-8")
                    )["metadata"]
                    self.assertEqual(metadata["platform"], row.platform)
                    self.assertEqual(metadata["runner"], "nemoclaw")
                    self.assertEqual(metadata["expected_skill"], row.skill)
                    self.assertEqual(scenario.gpu_count, metadata.get("gpu_count", 1))
                    prompt = (task_dir / "tests/nemoclaw_prompt.md").read_text(
                        encoding="utf-8"
                    )
                    self.assertIn(f"Use the `/{row.skill}` skill", prompt)
                    self.assertIn("GPU resource boundary", prompt)
                    if row.deployment_profile:
                        self.assertEqual(
                            metadata["deployment_profile"], row.deployment_profile
                        )

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
            self.scenario._native_metrics_valid({"turns": 3, "prompt_tokens": 1200})
        )

    def test_native_metrics_do_not_fall_back_to_harbor_trajectory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trial = Path(temporary)
            trajectory = trial / "agent/trajectory.json"
            trajectory.parent.mkdir(parents=True)
            trajectory.write_text(
                json.dumps(
                    {
                        "steps": [{"source": "agent"}],
                        "final_metrics": {
                            "total_prompt_tokens": 1200,
                            "total_cached_tokens": 200,
                        },
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.scenario._inner_metrics(trial), {})

            metrics = trial / "artifacts/nemoclaw/metrics.json"
            metrics.parent.mkdir(parents=True)
            expected = {
                "turns": 3,
                "prompt_tokens": 1000,
                "cached_tokens": 200,
            }
            metrics.write_text(json.dumps(expected), encoding="utf-8")
            self.assertEqual(self.scenario._inner_metrics(trial), expected)

    def test_trial_success_requires_explicitly_empty_exception(self) -> None:
        self.assertTrue(self.scenario._trial_succeeded({"exception_info": None}))
        self.assertFalse(self.scenario._trial_succeeded({}))
        self.assertFalse(
            self.scenario._trial_succeeded(
                {"exception_info": {"exception_type": "AgentError"}}
            )
        )

    def test_latest_trial_ignores_run_level_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            date = root / "2026-08-11"
            trial = date / "trial-1"
            (trial / "verifier").mkdir(parents=True)
            (trial / "verifier/reward.txt").write_text("1.0", encoding="utf-8")
            (trial / "result.json").write_text('{"scope": "trial"}', encoding="utf-8")
            (date / "result.json").write_text('{"scope": "run"}', encoding="utf-8")
            selected, result = self.scenario._latest_trial(root)
            self.assertEqual(selected, trial)
            self.assertEqual(result["scope"], "trial")

    def test_reward_rejects_non_finite_and_out_of_range_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trial = Path(temporary)
            (trial / "verifier").mkdir()
            reward = trial / "verifier/reward.txt"
            for invalid in ("nan", "inf", "-0.1", "1.1"):
                reward.write_text(invalid, encoding="utf-8")
                self.assertIsNone(self.scenario._reward(trial))
            reward.write_text("0.5", encoding="utf-8")
            self.assertEqual(self.scenario._reward(trial), 0.5)

    def test_harbor_uses_isolated_nemoclaw_environment(self) -> None:
        row = self.scenario.MatrixRow(
            "vss-deploy-profile",
            "base",
            "RTXPRO6000BW",
            1,
            "base",
        )
        scenario = self.scenario.Scenario(
            row=row,
            task_dir=Path("/tmp/dataset/base/rtxpro6000bw"),
            gpu_count=1,
        )
        with (
            mock.patch.object(self.scenario, "_uvx", return_value="uvx"),
            mock.patch.dict(
                os.environ,
                {"ANTHROPIC_MODEL": "test-model"},
                clear=False,
            ),
        ):
            command = self.scenario._harbor_command(
                scenario,
                Path("/tmp/results/123/vss-deploy-profile"),
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
        self.assertEqual(
            command[command.index("--include-task-name") + 1],
            "rtxpro6000bw",
        )

    def test_aggregate_treats_semantic_failure_as_reported(self) -> None:
        rows = list(self.scenario.COMPATIBLE_ROWS[:2])
        records = [
            {
                "skill": rows[0].skill,
                "spec": rows[0].spec,
                "task": "step-1",
                "status": "PASS",
            },
            {
                "skill": rows[1].skill,
                "spec": rows[1].spec,
                "task": "step-1",
                "status": "FAIL",
            },
        ]
        with tempfile.TemporaryDirectory() as temporary:
            benchmark = Path(temporary) / "benchmark.md"
            benchmark.write_text("# benchmark\n", encoding="utf-8")
            verdict = Path(temporary) / "verdict.json"
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}):
                self.scenario._write_aggregate(
                    rows=rows,
                    records=records,
                    run_id="local",
                    benchmark=benchmark,
                    verdict=verdict,
                )
            parsed = json.loads(verdict.read_text(encoding="utf-8"))
            self.assertEqual(
                [row["status"] for row in parsed["rows"]],
                ["PASS", "FAIL"],
            )
            self.assertEqual(parsed["counts"]["ERROR"], 0)

    def test_uvx_resolves_user_install_outside_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            uvx = Path(temporary) / "bin" / "uvx"
            uvx.parent.mkdir()
            uvx.write_text("", encoding="utf-8")
            with (
                mock.patch.object(self.scenario.shutil, "which", return_value=None),
                mock.patch.object(
                    self.scenario.site,
                    "getuserbase",
                    return_value=temporary,
                ),
            ):
                self.assertEqual(self.scenario._uvx(), str(uvx))


class WorkflowScopeTests(unittest.TestCase):
    def test_remote_notebook_kernel_uses_supported_python(self) -> None:
        source = (REPO_ROOT / ".github/skill-eval/envs/nemoclaw_brev_env.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "uv python install --reinstall --force --no-cache 3.12",
            source,
        )
        self.assertIn("uv venv --managed-python --python 3.12", source)
        self.assertNotIn("python3 -m venv", source)
        self.assertIn("openshell.db openshell.db-wal openshell.db-shm", source)
        self.assertIn("Refusing symlinked OpenShell database", source)
        self.assertNotIn("chown -R", source)

    def test_rtsp_sample_url_reaches_the_notebook_environment(self) -> None:
        source = (REPO_ROOT / ".github/skill-eval/envs/nemoclaw_brev_env.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"RTSP_SAMPLE_URL",', source)
        self.assertIn("--required-tools", source)
        self.assertIn("required_mcp_tools", source)

    def test_docker_reset_preserves_only_validated_openshell_bridge(self) -> None:
        source = (REPO_ROOT / ".github/skill-eval/envs/nemoclaw_brev_env.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("async def _reset_docker_runtime", source)
        self.assertIn('network_name" = "openshell-docker', source)
        self.assertIn('network_driver" = "bridge', source)
        self.assertIn('network_owner" = "openshell', source)
        self.assertIn('index .Labels "openshell.ai/managed-by"', source)
        self.assertNotIn("docker network prune", source)
        self.assertNotIn("docker network create", source)

    def test_missing_bridge_uses_scoped_gateway_recovery(self) -> None:
        source = (REPO_ROOT / ".github/skill-eval/envs/nemoclaw_brev_env.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("openshell_network_names", source)
        self.assertIn("gateway-port-release.js", source)
        self.assertIn("releaseManagedGatewayPort", source)
        self.assertIn("release_gateway_port.py", source)
        self.assertIn('gateway_release_status" -eq 42', source)
        self.assertIn('. "$HOME/.profile"', source)
        self.assertIn("gateway_port_is_free", source)
        self.assertNotIn("pkill", source)
        self.assertNotIn("killall", source)

    def test_workflow_keeps_claude_default_and_bounds_nemoclaw(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/skills-eval.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('default: "claude-code"', workflow)
        self.assertIn("inputs.runner != 'nemoclaw'", workflow)
        self.assertIn("nemoclaw_instance must name", workflow)
        self.assertIn("timeout-minutes: 2880", workflow)
        self.assertIn("timeout-minutes: 2860", workflow)
        self.assertIn("NEMOCLAW_HARBOR_TIMEOUT_SEC=12600", workflow)
        self.assertIn('--skills "$INPUT_SKILLS"', workflow)
        self.assertIn("NEMOCLAW_SANDBOX_NAME=demo", workflow)
        self.assertNotIn('NEMOCLAW_SANDBOX_NAME="skill-eval-', workflow)
        self.assertIn("--exclude='agent'", workflow)
        self.assertIn('if [ -d "$RUN_RESULTS" ]; then', workflow)

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
