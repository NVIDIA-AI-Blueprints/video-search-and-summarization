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
        self.adapter._parameterize_notebook(
            notebook,
            REPO_ROOT / "deploy/docker/scripts/deploy_nemoclaw.ipynb",
        )
        cells = {cell.get("id"): cell for cell in notebook["cells"]}
        self.assertIsInstance(cells["e67f6da4"]["source"], str)
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

    def test_orchestrator_ci_values_are_injected_without_source_edits(self) -> None:
        environment = {
            "NGC_CLI_API_KEY": "ngc-test",
            "NVIDIA_API_KEY": "nvapi-test",
            "HARDWARE_PROFILE": "L40S",
            "LLM_DEVICE_ID": "",
            "VLM_DEVICE_ID": "",
            "LLM_NAME": "llm-model",
            "LLM_ENDPOINT_URL": "https://llm.example.test",
            "OPENAI_API_KEY": "provider-test-key",
            "VLM_NAME": "vlm-model",
            "VLM_ENDPOINT_URL": "https://vlm.example.test",
        }
        path = REPO_ROOT / "deploy/docker/scripts/deploy_vss_orchestrator.ipynb"
        notebook = json.loads(path.read_text(encoding="utf-8"))
        self.adapter._parameterize_notebook(notebook, path)
        cells = {cell.get("id"): cell for cell in notebook["cells"]}
        namespace: dict[str, object] = {}
        with (
            mock.patch.dict(os.environ, environment, clear=True),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            for cell_id in ("7db6e569", "20b35654"):
                source = "".join(cells[cell_id]["source"])
                exec(  # noqa: S102 - checked-in notebook settings cells only.
                    compile(source, f"deploy_vss_orchestrator.ipynb:{cell_id}", "exec"),
                    namespace,
                )

        self.assertEqual(namespace["HARDWARE_PROFILE"], "L40S")
        self.assertEqual(namespace["LLM_DEVICE_ID"], "")
        self.assertEqual(namespace["VLM_DEVICE_ID"], "")
        self.assertEqual(namespace["LLM_NAME"], "llm-model")
        self.assertEqual(namespace["VLM_NAME"], "vlm-model")

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

    def test_adapter_never_persists_notebooks_or_adds_a_secret_scrubber(self) -> None:
        source = (NEMOCLAW_DIR / "notebook_setup_adapter.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("SECRET" + "_TEXT_PATTERNS", source)
        self.assertNotIn("def _" + "redact", source)
        self.assertNotIn("nbformat.write", source)
        self.assertIn("outputs were not persisted", source)


class HeadlessRunnerTests(unittest.TestCase):
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

    def test_exec_wrapper_is_normalized_for_the_stock_harbor_converter(self) -> None:
        record = {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "call-1",
                        "name": "tool_call",
                        "arguments": {
                            "id": "openclaw:core:exec",
                            "args": {"cmd": "echo ready"},
                        },
                    }
                ],
                "usage": {"input": 10, "cacheRead": 2, "output": 3},
            },
        }
        normalized, totals = self.runner._normalize_session(json.dumps(record))
        parsed = json.loads(normalized)
        call = parsed["message"]["content"][0]
        self.assertEqual(call["name"], "Bash")
        self.assertEqual(call["arguments"], {"command": "echo ready"})
        self.assertEqual(
            totals,
            {"input": 10, "cacheRead": 2, "output": 3, "turns": 1},
        )

        envelope = {"meta": {"agentMeta": {}}}
        self.runner._set_native_usage(envelope, totals)
        self.assertEqual(
            envelope["meta"]["agentMeta"]["usage"],
            {"input": 10, "cacheRead": 2, "output": 3},
        )

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
        command = sandbox_exec.call_args.args[1]
        self.assertIn(". /tmp/nemoclaw-proxy-env.sh", command)
        self.assertIn("unset OPENCLAW_GATEWAY_TOKEN", command)
        self.assertTrue(command.endswith("openclaw agent --message test"))

    def test_gateway_health_uses_configured_dashboard_port(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with (
            mock.patch.dict(os.environ, {"NEMOCLAW_DASHBOARD_PORT": "30754"}),
            mock.patch.object(
                self.runner,
                "_sandbox_exec",
                return_value=completed,
            ) as sandbox_exec,
        ):
            self.assertTrue(self.runner._gateway_healthy("demo"))

        self.assertIn(
            "http://127.0.0.1:30754/health",
            sandbox_exec.call_args.args[1],
        )

    def test_openclaw_run_returns_native_logs_for_harbor(self) -> None:
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
        session = json.dumps(
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "done"}],
                    "usage": {"input": 10, "output": 2},
                },
            }
        )
        with (
            mock.patch.object(
                self.runner,
                "_nemoclaw_exec",
                return_value=subprocess.CompletedProcess(
                    [], 0, stdout=json.dumps(envelope), stderr=""
                ),
            ),
            mock.patch.object(
                self.runner,
                "_sandbox_exec",
                return_value=subprocess.CompletedProcess(
                    [], 0, stdout=session, stderr=""
                ),
            ),
        ):
            result, normalized = self.runner._run_openclaw("demo", "deploy", 120)

        self.assertEqual(result["meta"]["agentMeta"]["usage"]["input"], 10)
        self.assertEqual(json.loads(normalized)["message"]["role"], "assistant")

    def test_runner_does_not_duplicate_harbor_session_conversion(self) -> None:
        source = (NEMOCLAW_DIR / "headless_runner.py").read_text(encoding="utf-8")
        self.assertNotIn("_session_to_atif", source)
        self.assertNotIn("trajectory.json", source)
        self.assertNotIn("sandbox recover", source)

    def test_notebook_adapter_only_repairs_an_incomplete_orchestrator_venv(
        self,
    ) -> None:
        source = (NEMOCLAW_DIR / "notebook_setup_adapter.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('root / "services" / "agent" / ".venv"', source)
        self.assertIn('orchestrator_venv / "bin" / "python"', source)
        self.assertIn("shutil.rmtree(orchestrator_venv)", source)


class HarnessScopeTests(unittest.TestCase):
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
        self.assertIn("uv run --isolated --no-project --python 3.12", command)
        self.assertIn("notebook_setup_adapter.py", command)
        for excluded in (
            "apt-get",
            "chown",
            "docker network",
            "release_gateway_port.py",
            "uv pip",
            "LEGACY_ROW_CLEANUP",
        ):
            self.assertNotIn(excluded, source)

    def test_onboarding_failure_uses_native_diagnostics(self) -> None:
        command = self.env_module._onboarding_diagnostics_command("8991")
        self.assertIn("nemoclaw debug --quick", command)
        self.assertIn("/logs/artifacts/nemoclaw-debug.tar.gz", command)
        self.assertIn('export HOME="$host_home/.skill-eval/nemoclaw-home"', command)
        self.assertIn("export NEMOCLAW_GATEWAY_PORT=8991", command)
        self.assertNotIn("docker", command)
        self.assertNotIn("sudo", command)

    def test_environment_defaults_hold_nemoclaw_runtime_assumptions(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"GITHUB_RUN_ID": "123", "EVAL_PLATFORM": "L40S"},
            clear=True,
        ):
            forwarded = self.env_module._forwarded_nemoclaw_env()
        self.assertIn("export NEMOCLAW_SANDBOX_NAME=skill-eval", forwarded)
        self.assertIn("export NEMOCLAW_GATEWAY_PORT=8991", forwarded)
        self.assertIn("export NEMOCLAW_DASHBOARD_PORT=20123", forwarded)
        self.assertIn("export HARDWARE_PROFILE=L40S", forwarded)

    def test_environment_does_not_intercept_agent_execution(self) -> None:
        source = (REPO_ROOT / ".github/skill-eval/envs/nemoclaw_brev_env.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("async def exec", source)
        self.assertNotIn("claude --verbose", source)
        self.assertNotIn("HARBOR_CLAUDE_CODE_INSTRUCTION_", source)

    def test_eval_harness_only_destroys_the_named_sandbox(self) -> None:
        command = self.env_module._destroy_sandbox_command("skill-eval", "8991")
        source = (REPO_ROOT / ".github/skill-eval/envs/nemoclaw_brev_env.py").read_text(
            encoding="utf-8"
        )
        start = source.split("    async def start", 1)[1]
        self.assertIn("openshell sandbox get skill-eval", command)
        self.assertIn('export HOME="$host_home/.skill-eval/nemoclaw-home"', command)
        self.assertIn("export NEMOCLAW_GATEWAY_PORT=8991", command)
        self.assertIn(
            "nemoclaw skill-eval destroy --yes --cleanup-gateway",
            command,
        )
        self.assertLess(
            start.index("_destroy_sandbox_command(sandbox, gateway_port)"),
            start.index("await super().start(force_build)"),
        )
        self.assertNotIn("sudo", command)
        self.assertNotIn("docker", command)
        self.assertNotIn("pkill", command)

    def test_agent_is_a_thin_variant_of_harbor_openclaw(self) -> None:
        source = (REPO_ROOT / ".github/skill-eval/agents/nemoclaw.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("class NemoClaw(OpenClaw)", source)
        self.assertIn("headless_runner.py", source)
        self.assertNotIn("populate_context_post_run", source)
        self.assertNotIn("trajectory.json", source)

    def test_workflow_uses_the_shared_plan_and_dispatch_path(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/skills-eval.yml").read_text(
            encoding="utf-8"
        )
        eval_job = workflow.split("\n  eval:\n", 1)[1]
        self.assertIn('default: "claude-code"', workflow)
        self.assertIn("matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}", eval_job)
        self.assertIn("max-parallel: 8", eval_job)
        self.assertEqual(workflow.count("Run skills eval agent (single spec)"), 1)
        self.assertIn("EVAL_AGENT:", workflow)
        self.assertNotIn("nemoclaw_instance", workflow)
        self.assertNotIn("NEMOCLAW_INSTANCE", workflow)
        self.assertNotIn("inputs.runner != 'nemoclaw'", workflow)
        self.assertNotIn("single_scenario.py", workflow)
        self.assertNotIn("Run selected skill through NemoClaw", workflow)
        self.assertIn("Collect results for workflow artifact", workflow)
        self.assertIn("--exclude='agent'", workflow)
        self.assertNotIn("Collect NemoClaw diagnostics", workflow)
        self.assertNotIn("skills-eval-nemoclaw-", workflow)

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
