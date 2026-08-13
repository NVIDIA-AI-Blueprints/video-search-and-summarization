# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import types
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


class NotebookRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.adapter = _load(
            "notebook_setup_adapter",
            NEMOCLAW_DIR / "notebook_setup_adapter.py",
        )

    def test_checked_in_notebooks_run_in_documented_order(self) -> None:
        self.assertEqual(
            self.adapter.notebook_paths(REPO_ROOT),
            (
                REPO_ROOT / "deploy/docker/scripts/deploy_nemoclaw.ipynb",
                REPO_ROOT / "deploy/docker/scripts/deploy_vss_orchestrator.ipynb",
            ),
        )
        self.assertFalse((NEMOCLAW_DIR / "notebook_cells.json").exists())

    def test_run_all_preserves_current_nvidia_inference_provider(self) -> None:
        environment = {
            "NGC_CLI_API_KEY": "ngc-test",
            "ANTHROPIC_BASE_URL": "https://inference-api.nvidia.com",
            "ANTHROPIC_MODEL": "aws/anthropic/bedrock-claude-sonnet-4-6",
            "ANTHROPIC_API_KEY": "provider-test-key",
            "PATH": os.environ.get("PATH", ""),
        }
        self.adapter.prepare_environment(environment, root=REPO_ROOT)
        notebook = json.loads(
            (REPO_ROOT / "deploy/docker/scripts/deploy_nemoclaw.ipynb").read_text(
                encoding="utf-8"
            )
        )
        cells = {cell.get("id"): cell for cell in notebook["cells"]}
        namespace: dict[str, object] = {}
        with (
            mock.patch.dict(os.environ, environment, clear=True),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            for cell_id in (
                "994c77c2",
                "47d20bb1",
                "23c61200",
                "ce326252",
                "e67f6da4",
            ):
                source = "".join(cells[cell_id]["source"])
                exec(  # noqa: S102 - checked-in notebook settings cells only.
                    compile(source, f"deploy_nemoclaw.ipynb:{cell_id}", "exec"),
                    namespace,
                )

        self.assertEqual(namespace["NEMOCLAW_PROVIDER"], "custom")
        self.assertEqual(
            namespace["NEMOCLAW_ENDPOINT_URL"],
            "https://inference-api.nvidia.com/v1",
        )
        self.assertEqual(
            namespace["NEMOCLAW_MODEL"],
            "aws/anthropic/bedrock-claude-sonnet-4-6",
        )
        self.assertTrue(namespace["NEMOCLAW_CLEAN_SETUP"])

    def test_webhook_config_uses_a_bounded_shields_window(self) -> None:
        notebook = json.loads(
            (REPO_ROOT / "deploy/docker/scripts/deploy_nemoclaw.ipynb").read_text(
                encoding="utf-8"
            )
        )
        cell = next(cell for cell in notebook["cells"] if cell.get("id") == "s37-code")
        source = "".join(cell["source"])

        self.assertIn('shields down "', source)
        self.assertIn('--timeout 15m --reason "notebook webhook config"', source)
        self.assertIn("finally:\n", source)
        self.assertIn("shields up", source)
        self.assertLess(source.index("shields down"), source.index("_config_set_cmd"))
        self.assertLess(source.index("_config_set_cmd"), source.index("finally:\n"))
        self.assertLess(source.index("finally:\n"), source.index("shields up"))

    def test_remote_vss_models_are_mapped_to_notebook_variables(self) -> None:
        environment = {
            "NGC_CLI_API_KEY": "ngc-test",
            "ANTHROPIC_BASE_URL": "https://inference-api.nvidia.com/v1",
            "ANTHROPIC_MODEL": "agent-model",
            "ANTHROPIC_API_KEY": "provider-test-key",
            "LLM_REMOTE_URL": "https://integrate.api.nvidia.com/v1",
            "LLM_REMOTE_MODEL": "llm-model",
            "VLM_REMOTE_URL": "https://integrate.api.nvidia.com/v1/models",
            "VLM_REMOTE_MODEL": "vlm-model",
        }
        self.adapter.prepare_environment(environment, root=REPO_ROOT)
        self.assertEqual(
            environment["LLM_ENDPOINT_URL"], "https://integrate.api.nvidia.com"
        )
        self.assertEqual(environment["LLM_NAME"], "llm-model")
        self.assertEqual(
            environment["VLM_ENDPOINT_URL"], "https://integrate.api.nvidia.com"
        )
        self.assertEqual(environment["VLM_NAME"], "vlm-model")

    def test_runtime_env_contains_coordinates_but_not_credentials(self) -> None:
        environment = {
            "NEMOCLAW_SANDBOX_NAME": "demo",
            "NEMOCLAW_GATEWAY_PORT": "8080",
            "ORCHESTRATOR_ENABLE_HTTPS": "false",
            "VSS_ORCHESTRATOR_MCP_PORT": "9988",
            "VSS_ORCHESTRATOR_MCP_URL": "http://host.openshell.internal:9988/mcp",
            "VSS_ORCHESTRATOR_MCP_TYPE": "streamable-http",
            "HOST_INTERNAL_ALIAS": "host.openshell.internal",
            "HARDWARE_PROFILE": "RTXPRO6000BW",
            "COMPATIBLE_API_KEY": "must-not-be-written",
        }
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "nemoclaw.env"
            self.adapter.write_runtime_environment(output, environment)
            content = output.read_text(encoding="utf-8")
        self.assertIn("export NEMOCLAW_SANDBOX_NAME=demo", content)
        self.assertIn("export MCP_URL=http://127.0.0.1:9988/mcp", content)
        self.assertNotIn("must-not-be-written", content)

    def test_adapter_has_no_custom_secret_scrubber(self) -> None:
        source = (NEMOCLAW_DIR / "notebook_setup_adapter.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("SECRET" + "_TEXT_PATTERNS", source)
        self.assertNotIn("def _" + "redact", source)
        self.assertNotIn("nbformat.write", source)
        self.assertIn("outputs were not persisted", source)


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

    def test_nemoclaw_exec_uses_the_trusted_runtime_env(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
        with mock.patch.object(
            self.runner,
            "_sandbox_exec",
            return_value=completed,
        ) as sandbox_exec:
            result = self.runner._nemoclaw_exec(
                "demo",
                "openclaw agent --message test",
                timeout=120,
            )

        self.assertIs(result, completed)
        sandbox_exec.assert_called_once()
        self.assertEqual(sandbox_exec.call_args.args[0], "demo")
        command = sandbox_exec.call_args.args[1]
        self.assertIn(". /tmp/nemoclaw-proxy-env.sh", command)
        self.assertIn("unset OPENCLAW_GATEWAY_TOKEN", command)
        self.assertTrue(command.endswith("openclaw agent --message test"))
        self.assertEqual(
            sandbox_exec.call_args.kwargs,
            {"timeout": 120},
        )

    def test_openclaw_run_routes_only_the_agent_through_nemoclaw_exec(self) -> None:
        session_file = "/sandbox/.openclaw/agents/main/sessions/session-1.jsonl"
        envelope = {
            "meta": {
                "agentMeta": {
                    "sessionId": "session-1",
                    "sessionFile": session_file,
                    "model": "test-model",
                }
            }
        }
        session = "\n".join(
            [
                json.dumps(
                    {
                        "type": "message",
                        "message": {"role": "user", "content": "deploy"},
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "done"}],
                            "usage": {"input": 10, "output": 2},
                        },
                    }
                ),
            ]
        )
        with (
            mock.patch.object(
                self.runner,
                "_nemoclaw_exec",
                return_value=subprocess.CompletedProcess(
                    [], 0, stdout=json.dumps(envelope), stderr=""
                ),
            ) as nemoclaw_exec,
            mock.patch.object(
                self.runner,
                "_sandbox_exec",
                return_value=subprocess.CompletedProcess(
                    [], 0, stdout=session, stderr=""
                ),
            ) as sandbox_exec,
        ):
            _, metrics, _ = self.runner._run_openclaw("demo", "deploy", 120)

        nemoclaw_exec.assert_called_once()
        sandbox_exec.assert_called_once_with(
            "demo",
            f"cat -- {session_file}",
            timeout=60,
        )
        self.assertEqual(metrics["turns"], 1)
        self.assertEqual(metrics["prompt_tokens"], 10)


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
                '[metadata]\nplatform = "ANY"\n\n'
                '[verifier.env]\nANTHROPIC_API_KEY = "x"\n',
                encoding="utf-8",
            )
            row = self.scenario.MatrixRow(
                "vss-setup-behavior-analytics",
                "standalone_deploy",
                "skills/vss-setup-behavior-analytics/evals/standalone_deploy.json",
                "ANY",
                "eval",
                "vss-setup-behavior-analytics__standalone_deploy__ANY",
            )
            self.scenario._wrap_task(task, row, 3300)
            parsed = tomllib.loads((task / "task.toml").read_text(encoding="utf-8"))
            self.assertEqual(parsed["metadata"]["runner"], "nemoclaw")
            self.assertNotIn("expected_skill", parsed["metadata"])
            self.assertNotIn("deployment_profile", parsed["metadata"])
            self.assertNotIn("required_mcp_tools", parsed["metadata"])
            self.assertIn(
                "headless_runner.py",
                (task / "instruction.md").read_text(encoding="utf-8"),
            )
            prompt = (task / "tests/nemoclaw_prompt.md").read_text(encoding="utf-8")
            self.assertIn("Original eval request", prompt)
            self.assertIn("Deploy the base profile.", prompt)

    def test_matrix_comes_from_the_shared_skill_eval_planner(self) -> None:
        rows = self.scenario._selected_rows("vss-query-analytics")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].skill, "vss-query-analytics")
        self.assertEqual(rows[0].spec, "query_analytics")
        self.assertEqual(
            rows[0].spec_file,
            "skills/vss-query-analytics/evals/query_analytics.json",
        )
        self.assertEqual(rows[0].kind, "eval")
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.scenario._selected_rows("not-a-skill")

    def test_common_adapter_contract_generates_complete_ordered_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = self.scenario._selected_rows("vss-query-analytics")
            rows += [
                row
                for row in self.scenario._selected_rows("vss-deploy-profile")
                if row.spec == "base" and row.platform == "RTXPRO6000BW"
            ]
            self.assertEqual(len(rows), 2)
            for row in rows:
                task_dirs = self.scenario._generate_dataset(
                    row,
                    root / row.slug,
                )
                self.assertTrue(task_dirs)
                self.assertEqual(
                    task_dirs,
                    sorted(task_dirs, key=self.scenario._task_dir_sort_key),
                )
                for task_dir in task_dirs:
                    original = (task_dir / "instruction.md").read_text(encoding="utf-8")
                    scenario = self.scenario._wrap_task(task_dir, row, 3300)
                    metadata = tomllib.loads(
                        (task_dir / "task.toml").read_text(encoding="utf-8")
                    )["metadata"]
                    self.assertEqual(metadata["platform"], row.platform)
                    self.assertEqual(metadata["runner"], "nemoclaw")
                    self.assertEqual(scenario.gpu_count, metadata.get("gpu_count", 1))
                    prompt = (task_dir / "tests/nemoclaw_prompt.md").read_text(
                        encoding="utf-8"
                    )
                    self.assertIn(original.strip(), prompt)
                    self.assertIn("GPU resource boundary", prompt)

    def test_every_planned_adapter_exposes_the_common_cli(self) -> None:
        rows = self.scenario._selected_rows("*")
        skills = sorted({row.skill for row in rows if row.kind == "eval"})
        self.assertTrue(skills)
        for skill in skills:
            adapter = self.scenario.ADAPTERS_ROOT / skill / "generate.py"
            help_text = self.scenario._adapter_help(adapter)
            for option in ("--output-dir", "--skill-dir", "--spec", "--platform"):
                with self.subTest(skill=skill, option=option):
                    self.assertIn(option, help_text)

    def test_nemoclaw_orchestration_has_no_deployment_specific_behavior(self) -> None:
        source = (NEMOCLAW_DIR / "single_scenario.py").read_text(encoding="utf-8")
        self.assertNotIn("COMPATIBLE_ROWS", source)
        self.assertNotIn("deployment_profile", source)
        self.assertNotIn("vss-" + "deploy-profile", source)

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

    def test_latest_trial_keeps_setup_error_without_reward(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            date = root / "2026-08-12"
            trial = date / "trial-1"
            trial.mkdir(parents=True)
            (trial / "trial.log").write_text("setup failed", encoding="utf-8")
            (trial / "exception.txt").write_text("RuntimeError", encoding="utf-8")
            (trial / "result.json").write_text(
                '{"exception_info": {"exception_type": "RuntimeError"}}',
                encoding="utf-8",
            )
            (date / "result.json").write_text('{"scope": "run"}', encoding="utf-8")

            selected, result = self.scenario._latest_trial(root)

            self.assertEqual(selected, trial)
            self.assertEqual(
                result["exception_info"]["exception_type"],
                "RuntimeError",
            )

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
            "vss-setup-behavior-analytics",
            "standalone_deploy",
            "skills/vss-setup-behavior-analytics/evals/standalone_deploy.json",
            "ANY",
            "eval",
            "vss-setup-behavior-analytics__standalone_deploy__ANY",
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
                Path("/tmp/results/123/standalone"),
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
        rows = self.scenario._selected_rows("vss-setup-behavior-analytics")[:2]
        records = [
            {
                "skill": rows[0].skill,
                "spec": rows[0].spec,
                "platform": rows[0].platform,
                "task": "step-1",
                "status": "PASS",
            },
            {
                "skill": rows[1].skill,
                "spec": rows[1].spec,
                "platform": rows[1].platform,
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
    @classmethod
    def setUpClass(cls) -> None:
        envs = types.ModuleType("envs")
        envs.__path__ = []
        brev_env = types.ModuleType("envs.brev_env")
        brev_env.BrevEnvironment = type("BrevEnvironment", (), {})
        brev_env._run_brev_exec = lambda *args, **kwargs: None
        previous_envs = sys.modules.get("envs")
        previous_brev_env = sys.modules.get("envs.brev_env")
        sys.modules["envs"] = envs
        sys.modules["envs.brev_env"] = brev_env
        try:
            cls.env_module = _load(
                "nemoclaw_brev_env",
                REPO_ROOT / ".github/skill-eval/envs/nemoclaw_brev_env.py",
            )
        finally:
            if previous_envs is None:
                del sys.modules["envs"]
            else:
                sys.modules["envs"] = previous_envs
            if previous_brev_env is None:
                del sys.modules["envs.brev_env"]
            else:
                sys.modules["envs.brev_env"] = previous_brev_env

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
        self.assertNotIn("NEMOCLAW_REQUIRED_MCP_TOOLS", source)
        self.assertNotIn("required_mcp_tools", source)
        self.assertNotIn("readiness.py", source)

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

    def test_failed_notebook_setup_collects_bounded_gateway_errors(self) -> None:
        source = (REPO_ROOT / ".github/skill-eval/envs/nemoclaw_brev_env.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('nemoclaw "$NEMOCLAW_SANDBOX_NAME" logs --tail 200', source)
        self.assertIn("timeout --signal=TERM --kill-after=10 60s", source)
        self.assertIn("tail -n 120", source)
        self.assertNotIn("logs --follow", source)

    def test_legacy_registry_cleanup_is_limited_to_old_ci_names(self) -> None:
        source = (REPO_ROOT / ".github/skill-eval/envs/nemoclaw_brev_env.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("skill-eval-[0-9]+", source)
        self.assertIn("se-[0-9]+", source)
        self.assertIn("vss-eval-u[0-9]+-p[0-9]+", source)
        self.assertIn('state_root / "gateways"', source)
        self.assertIn("del sandboxes[name]", source)
        self.assertNotIn("sandboxes.clear", source)
        command = self.env_module._setup_command(5400)
        cleanup = command.split("python3 - <<'__NEMOCLAW_LEGACY_ROW_CLEANUP__'\n", 1)[
            1
        ].split("\n__NEMOCLAW_LEGACY_ROW_CLEANUP__", 1)[0]
        compile(cleanup, "<nemoclaw-legacy-row-cleanup>", "exec")
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            default_registry = home / ".nemoclaw/sandboxes.json"
            gateway_registry = home / ".nemoclaw/gateways/19080/sandboxes.json"
            gateway_registry.parent.mkdir(parents=True)
            default_registry.write_text(
                json.dumps(
                    {
                        "defaultSandbox": "skill-eval-123",
                        "sandboxes": {
                            "skill-eval-123": {"name": "skill-eval-123"},
                            "interactive": {"name": "interactive"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            gateway_registry.write_text(
                json.dumps(
                    {
                        "defaultSandbox": "vss-eval-u1000-p19080-nc097-c5",
                        "sandboxes": {
                            "vss-eval-u1000-p19080-nc097-c5": {"legacy": True},
                            "se-123": {"name": "se-123"},
                            "keep": {"name": "keep"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            subprocess.run(
                [sys.executable, "-c", cleanup],
                env={**os.environ, "HOME": str(home)},
                check=True,
                capture_output=True,
                text=True,
            )
            default_document = json.loads(default_registry.read_text(encoding="utf-8"))
            gateway_document = json.loads(gateway_registry.read_text(encoding="utf-8"))

        self.assertEqual(default_document["defaultSandbox"], None)
        self.assertEqual(
            default_document["sandboxes"], {"interactive": {"name": "interactive"}}
        )
        self.assertEqual(gateway_document["defaultSandbox"], None)
        self.assertEqual(gateway_document["sandboxes"], {"keep": {"name": "keep"}})

    def test_workflow_keeps_claude_default_and_bounds_nemoclaw(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/skills-eval.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('default: "claude-code"', workflow)
        self.assertIn("inputs.runner != 'nemoclaw'", workflow)
        self.assertIn("nemoclaw_instance must name", workflow)
        self.assertIn("timeout-minutes: 380", workflow)
        self.assertIn("timeout-minutes: 360", workflow)
        self.assertIn("NEMOCLAW_HARBOR_TIMEOUT_SEC=12600", workflow)
        self.assertIn('--skills "$INPUT_SKILLS"', workflow)
        self.assertIn('NEMOCLAW_SANDBOX_NAME="se-${GITHUB_RUN_ID}"', workflow)
        self.assertIn("NEMOCLAW_GATEWAY_PORT=8990", workflow)
        self.assertIn(
            "NEMOCLAW_DASHBOARD_PORT=$((20000 + GITHUB_RUN_ID % 40000))",
            workflow,
        )
        self.assertIn("NEMOCLAW_INSTALL_REF=v0.0.108", workflow)
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
