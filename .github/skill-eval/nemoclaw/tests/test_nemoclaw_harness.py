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
            "HOME": os.environ.get("HOME", str(Path.home())),
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
        self.assertNotIn("NEMOCLAW_CLEAN_SETUP", namespace)

    def test_webhook_config_does_not_toggle_shields(self) -> None:
        notebook = json.loads(
            (REPO_ROOT / "deploy/docker/scripts/deploy_nemoclaw.ipynb").read_text(
                encoding="utf-8"
            )
        )
        cell = next(cell for cell in notebook["cells"] if cell.get("id") == "s37-code")
        source = "".join(cell["source"])

        self.assertNotIn("shields down", source)
        self.assertNotIn("shields up", source)
        self.assertIn("_config_set_cmd", source)

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
            "NEMOCLAW_DASHBOARD_PORT": "30754",
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
        self.assertIn("export NEMOCLAW_DASHBOARD_PORT=30754", content)
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

    def test_gateway_health_uses_configured_dashboard_port(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with (
            mock.patch.dict(
                os.environ,
                {"NEMOCLAW_DASHBOARD_PORT": "30754"},
            ),
            mock.patch.object(
                self.runner,
                "_sandbox_exec",
                return_value=completed,
            ) as sandbox_exec,
        ):
            self.assertTrue(self.runner._gateway_healthy("demo"))

        command = sandbox_exec.call_args.args[1]
        self.assertIn("http://127.0.0.1:30754/health", command)
        self.assertNotIn("http://127.0.0.1:18789/health", command)

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


class SingleScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scenario = _load(
            "single_scenario",
            NEMOCLAW_DIR / "single_scenario.py",
        )
        cls.planner = _load(
            "plan_matrix_for_nemoclaw_tests",
            REPO_ROOT / ".github/skill-eval/plan_matrix.py",
        )

    def _planned_rows(self, skills: str):
        with mock.patch.dict(
            os.environ,
            {"MANUAL_SKILLS_FILTER": skills},
            clear=False,
        ):
            os.environ.pop("CHANGED_FILES", None)
            changed = self.planner.list_changed_files()
        return self.scenario._rows_from_plan(
            {"include": self.planner.build_matrix(changed)}
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

    def test_matrix_row_runner_consumes_the_shared_skill_eval_plan(self) -> None:
        rows = self._planned_rows("vss-query-analytics")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].skill, "vss-query-analytics")
        self.assertEqual(rows[0].spec, "query_analytics")
        self.assertEqual(
            rows[0].spec_file,
            "skills/vss-query-analytics/evals/query_analytics.json",
        )
        self.assertEqual(rows[0].kind, "eval")
        with self.assertRaisesRegex(ValueError, "does not exist|skill not found"):
            self._planned_rows("not-a-skill")

    def test_shared_plan_rows_are_validated_at_the_serial_boundary(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-empty include"):
            self.scenario._rows_from_plan({"include": []})
        with self.assertRaisesRegex(ValueError, "duplicate.*slug"):
            row = {
                "skill": "vss-query-analytics",
                "spec_stem": "query_analytics",
                "spec_path": "skills/vss-query-analytics/evals/query_analytics.json",
                "platform": "RTXPRO6000BW",
                "kind": "eval",
                "slug": "vss-query-analytics__query_analytics__RTXPRO6000BW",
            }
            self.scenario._rows_from_plan({"include": [row, row]})

    def test_semantic_failure_does_not_block_dependent_scenarios(self) -> None:
        self.assertFalse(self.scenario._blocks_dependent_scenarios({"status": "PASS"}))
        self.assertFalse(self.scenario._blocks_dependent_scenarios({"status": "FAIL"}))
        self.assertTrue(self.scenario._blocks_dependent_scenarios({"status": "ERROR"}))

    def test_common_adapter_contract_generates_complete_ordered_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = self._planned_rows("vss-query-analytics")
            rows += [
                row
                for row in self._planned_rows("vss-deploy-profile")
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
        rows = self._planned_rows("*")
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
        rows = self._planned_rows("vss-setup-behavior-analytics")[:2]
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

    def test_setup_command_only_executes_the_notebook_adapter(self) -> None:
        source = (REPO_ROOT / ".github/skill-eval/envs/nemoclaw_brev_env.py").read_text(
            encoding="utf-8"
        )
        command = self.env_module._setup_command(5400)
        self.assertIn('. "$HOME/.eval_env"', command)
        self.assertIn("notebook_setup_adapter.py", command)
        self.assertIn("--timeout \"$NEMOCLAW_SETUP_CELL_TIMEOUT_SEC\"", command)
        for excluded in (
            "apt-get",
            "chown",
            "docker network",
            "gateway-port-release",
            "release_gateway_port.py",
            "uv python",
            "uv pip",
            "uv venv",
            "LEGACY_ROW_CLEANUP",
        ):
            self.assertNotIn(excluded, source)

    def test_rtsp_sample_url_reaches_the_notebook_environment(self) -> None:
        source = (REPO_ROOT / ".github/skill-eval/envs/nemoclaw_brev_env.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"RTSP_SAMPLE_URL",', source)
        self.assertNotIn("NEMOCLAW_REQUIRED_MCP_TOOLS", source)
        self.assertNotIn("required_mcp_tools", source)
        self.assertNotIn("readiness.py", source)

    def test_eval_harness_only_destroys_the_named_sandbox(self) -> None:
        command = self.env_module._destroy_sandbox_command("skill-eval")
        source = (REPO_ROOT / ".github/skill-eval/envs/nemoclaw_brev_env.py").read_text(
            encoding="utf-8"
        )
        start = source.split("    async def start", 1)[1].split(
            "    async def exec", 1
        )[0]
        self.assertIn("openshell sandbox get skill-eval", command)
        self.assertIn("nemoclaw skill-eval destroy --yes", command)
        self.assertLess(
            start.index("_destroy_sandbox_command(sandbox)"),
            start.index("await super().start(force_build)"),
        )
        self.assertNotIn("sudo", command)
        self.assertNotIn("docker", command)
        self.assertNotIn("pkill", command)

    def test_workflow_keeps_claude_default_and_bounds_nemoclaw(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/skills-eval.yml").read_text(
            encoding="utf-8"
        )
        plan_job = workflow.split("  plan:\n", 1)[1].split("\n  eval:\n", 1)[0]
        eval_job = workflow.split("\n  eval:\n", 1)[1]
        self.assertIn('default: "claude-code"', workflow)
        self.assertNotIn("inputs.runner != 'nemoclaw'", plan_job)
        self.assertNotIn("\n  nemoclaw-eval:\n", workflow)
        self.assertIn("needs: plan", eval_job)
        self.assertIn("matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}", eval_job)
        self.assertIn("inputs.runner == 'nemoclaw' && 1 || 8", eval_job)
        self.assertIn("EVAL_PLAN_ROW: ${{ toJSON(matrix) }}", eval_job)
        self.assertIn("nemoclaw_instance must name", workflow)
        self.assertIn("timeout-minutes: 360", workflow)
        self.assertIn("NEMOCLAW_HARBOR_TIMEOUT_SEC=12600", workflow)
        self.assertIn('--plan-file "$PLAN_FILE"', workflow)
        self.assertNotIn('--skills "$INPUT_SKILLS"', workflow)
        self.assertIn(
            'NEMOCLAW_SANDBOX_NAME="skill-eval"',
            workflow,
        )
        self.assertIn("NEMOCLAW_GATEWAY_PORT=8990", workflow)
        self.assertIn(
            "NEMOCLAW_DASHBOARD_PORT=$((20000 + (GITHUB_RUN_ID + ${{ strategy.job-index }}) % 40000))",
            workflow,
        )
        self.assertIn(
            "format('skills-eval-nemoclaw-{0}', inputs.nemoclaw_instance)",
            workflow,
        )
        self.assertIn("NEMOCLAW_POLICY_MODE=skip", workflow)
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
