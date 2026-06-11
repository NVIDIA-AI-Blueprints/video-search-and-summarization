#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import textwrap
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


notebook_adapter = load_module(
    "notebook_setup_adapter",
    REPO_ROOT / ".github" / "skill-eval" / "nemoclaw" / "notebook_setup_adapter.py",
)
deploy_adapter = load_module(
    "vss_deploy_profile_generate",
    REPO_ROOT / ".github" / "skill-eval" / "adapters" / "vss-deploy-profile" / "generate.py",
)
orchestrator_mcp_helper = load_module(
    "orchestrator_mcp_helper",
    REPO_ROOT / "deploy" / "docker" / "scripts" / "orchestrator_mcp_helper.py",
)
headless_runner = load_module(
    "nemoclaw_headless_runner",
    REPO_ROOT / ".github" / "skill-eval" / "nemoclaw" / "headless_runner.py",
)
openclaw_stream_patch = load_module(
    "openclaw_stream_patch",
    REPO_ROOT / "deploy" / "docker" / "scripts" / "nemoclaw" / "patch_openclaw_streaming.py",
)
update_openclaw_config = load_module(
    "update_openclaw_config",
    REPO_ROOT / "deploy" / "docker" / "scripts" / "nemoclaw" / "update_openclaw_config.py",
)
readiness = load_module(
    "nemoclaw_readiness",
    REPO_ROOT / ".github" / "skill-eval" / "nemoclaw" / "readiness.py",
)
smoke_runner = load_module(
    "nemoclaw_smoke_runner",
    REPO_ROOT / ".github" / "skill-eval" / "nemoclaw" / "smoke_runner.py",
)
skills_eval_agent = load_module(
    "skills_eval_agent",
    REPO_ROOT / ".github" / "skill-eval" / "skills_eval_agent.py",
)


class NotebookSetupAdapterTest(unittest.TestCase):
    def test_sidecar_manifest_matches_current_notebook_cells(self):
        manifest_path = REPO_ROOT / ".github" / "skill-eval" / "nemoclaw" / "notebook_cells.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source = json.loads((REPO_ROOT / manifest["notebook"]).read_text(encoding="utf-8"))

        built = notebook_adapter.build_notebook(source, manifest)

        ids = [cell.get("id") for cell in built["cells"]]
        self.assertIn("ci-parameters", ids)
        self.assertIn("ci-persist-env", ids)

    def test_build_notebook_injects_parameters_before_derived_cell(self):
        source = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [
                {"id": "settings", "cell_type": "code", "metadata": {}, "source": ["A=1\n"], "outputs": []},
                {"id": "derived", "cell_type": "code", "metadata": {}, "source": ["B=A\n"], "outputs": []},
            ],
        }
        manifest = {"cells": ["settings", "derived"], "insert_parameters_before": "derived"}

        built = notebook_adapter.build_notebook(source, manifest)
        ids = [cell.get("id") for cell in built["cells"]]

        self.assertEqual(ids, ["settings", "ci-parameters", "derived", "ci-persist-env"])
        self.assertTrue(all(isinstance(cell["source"], str) for cell in built["cells"]))
        self.assertEqual(built["cells"][0]["source"], "A=1\n")

    def test_ci_notebook_makes_optional_9090_forward_best_effort(self):
        source = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [
                {
                    "id": "run-code",
                    "cell_type": "code",
                    "metadata": {},
                    "source": [
                        "print('setup')\n",
                        "ensure_openshell_forward(9090, NEMOCLAW_SANDBOX_NAME)\n",
                    ],
                    "outputs": [],
                }
            ],
        }
        manifest = {"cells": ["run-code"], "insert_parameters_before": "run-code"}

        built = notebook_adapter.build_notebook(source, manifest)
        run_cell = next(cell for cell in built["cells"] if cell.get("id") == "run-code")

        self.assertIn("try:", run_cell["source"])
        self.assertIn("optional OpenShell forward 9090 skipped in CI", run_cell["source"])
        self.assertIn("ensure_openshell_forward(9090, NEMOCLAW_SANDBOX_NAME)", run_cell["source"])

    def test_redacts_configured_secret_values(self):
        os.environ["NVIDIA_API_KEY"] = "nvapi-secret"
        try:
            redacted = notebook_adapter._redact(
                {"outputs": [{"text": "token=nvapi-secret"}]},
                notebook_adapter._redaction_values(),
            )
        finally:
            os.environ.pop("NVIDIA_API_KEY", None)

        self.assertEqual(redacted["outputs"][0]["text"], "token=<redacted:NVIDIA_API_KEY>")

    def test_redacts_anthropic_api_key_from_notebook_outputs(self):
        os.environ["ANTHROPIC_API_KEY"] = "anthropic-secret"
        try:
            redacted = notebook_adapter._redact(
                {"outputs": [{"text": "token=anthropic-secret"}]},
                notebook_adapter._redaction_values(),
            )
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

        self.assertEqual(redacted["outputs"][0]["text"], "token=<redacted:ANTHROPIC_API_KEY>")

    def test_redacts_generated_openclaw_bearer_token_from_notebook_outputs(self):
        redacted = notebook_adapter._redact(
            {
                "outputs": [
                    {
                        "text": (
                            "$ curl -H 'Authorization: Bearer "
                            "33edab45ea2845acc0498b5139a5142bafd3b4b2d32ebfc58f40a563cba18cae' "
                            "http://127.0.0.1:18789/hooks/agent"
                        )
                    }
                ]
            },
            {},
        )

        self.assertIn("Authorization: Bearer <redacted:OPENCLAW_HOOKS_TOKEN>", redacted["outputs"][0]["text"])
        self.assertNotIn("33edab45", redacted["outputs"][0]["text"])

    def test_persist_cell_keeps_hooks_token_out_of_debug_env_file(self):
        source = notebook_adapter.PERSIST_SOURCE
        keys_block = source.split("_keys = [", 1)[1].split("]", 1)[0]

        self.assertNotIn("OPENCLAW_HOOKS_TOKEN", keys_block)
        self.assertIn("NEMOCLAW_HOOKS_TOKEN_FILE", source)
        self.assertIn("chmod(0o600)", source)

    def test_parameter_cell_derives_nemoclaw_provider_from_remote_llm_env(self):
        defaults = {
            "HARDWARE_PROFILE": "RTXPRO6000BW",
            "NEMOCLAW_ENDPOINT_URL": "",
            "NEMOCLAW_MODEL": "",
            "COMPATIBLE_API_KEY": "",
            "NEMOCLAW_INSTALL_REF": "",
            "OPENCLAW_HOOKS_PATH": "/hooks",
            "VSS_LLM_NAME": "",
            "VSS_LLM_ENDPOINT_URL": "",
            "VSS_LLM_MODEL_TYPE": "",
            "VSS_LLM_ENABLE_THINKING": "",
            "VSS_OPENAI_API_KEY": "",
            "VSS_VLM_NAME": "",
            "VSS_VLM_ENDPOINT_URL": "",
            "VSS_VLM_MODEL_TYPE": "",
            "LLM_DEVICE_ID": "",
            "VLM_DEVICE_ID": "",
            "EXTERNAL_IP": "",
        }
        env_keys = (
            "LLM_REMOTE_URL",
            "LLM_REMOTE_MODEL",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_MODEL",
            "ANTHROPIC_API_KEY",
            "NEMOCLAW_ENDPOINT_URL",
            "NEMOCLAW_MODEL",
            "COMPATIBLE_API_KEY",
            "NVIDIA_API_KEY",
            "VSS_ORCHESTRATOR_MCP_URL",
            "VSS_ORCHESTRATOR_MCP_TYPE",
            "VSS_ORCHESTRATOR_MCP_SSE_PORT",
        )
        previous = {key: os.environ.get(key) for key in env_keys}
        for key in env_keys:
            os.environ.pop(key, None)
        os.environ["LLM_REMOTE_URL"] = "https://inference-api.example"
        os.environ["LLM_REMOTE_MODEL"] = "nvidia/example-model"
        os.environ["NVIDIA_API_KEY"] = "nvapi-ci"
        try:
            exec(notebook_adapter.PARAMETER_SOURCE, defaults)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual(defaults["NEMOCLAW_ENDPOINT_URL"], "https://inference-api.example/v1")
        self.assertEqual(defaults["NEMOCLAW_MODEL"], "nvidia/example-model")
        self.assertEqual(defaults["COMPATIBLE_API_KEY"], "nvapi-ci")
        self.assertEqual(defaults["OPENCLAW_DISABLE_STREAMING_TOOL_CALLS"], "1")
        self.assertEqual(defaults["VSS_ORCHESTRATOR_MCP_TYPE"], "sse")
        self.assertEqual(defaults["VSS_ORCHESTRATOR_MCP_URL"], "http://host.openshell.internal:9989/sse")

    def test_parameter_cell_prefers_ci_agent_model_over_vss_runtime_model(self):
        defaults = {
            "HARDWARE_PROFILE": "RTXPRO6000BW",
            "NEMOCLAW_ENDPOINT_URL": "",
            "NEMOCLAW_MODEL": "",
            "COMPATIBLE_API_KEY": "",
        }
        env_keys = (
            "LLM_REMOTE_URL",
            "LLM_REMOTE_MODEL",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_MODEL",
            "ANTHROPIC_API_KEY",
            "NEMOCLAW_ENDPOINT_URL",
            "NEMOCLAW_MODEL",
            "COMPATIBLE_API_KEY",
            "NVIDIA_API_KEY",
        )
        previous = {key: os.environ.get(key) for key in env_keys}
        for key in env_keys:
            os.environ.pop(key, None)
        os.environ["LLM_REMOTE_URL"] = "https://vss-runtime.example"
        os.environ["LLM_REMOTE_MODEL"] = "nvidia/nvidia-nemotron-nano-9b-v2"
        os.environ["ANTHROPIC_BASE_URL"] = "https://ci-agent.example/v1"
        os.environ["ANTHROPIC_MODEL"] = "aws/anthropic/bedrock-claude-opus-4-8"
        os.environ["ANTHROPIC_API_KEY"] = "anthropic-ci"
        os.environ["NVIDIA_API_KEY"] = "nvapi-ci"
        try:
            exec(notebook_adapter.PARAMETER_SOURCE, defaults)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual(defaults["NEMOCLAW_ENDPOINT_URL"], "https://ci-agent.example/v1")
        self.assertEqual(defaults["NEMOCLAW_MODEL"], "aws/anthropic/bedrock-claude-opus-4-8")
        self.assertEqual(defaults["COMPATIBLE_API_KEY"], "anthropic-ci")

    def test_parameter_cell_tolerates_missing_advanced_defaults(self):
        defaults = {
            "HARDWARE_PROFILE": "RTXPRO6000BW",
            "NEMOCLAW_ENDPOINT_URL": "",
            "NEMOCLAW_MODEL": "",
            "COMPATIBLE_API_KEY": "",
        }

        exec(notebook_adapter.PARAMETER_SOURCE, defaults)

        self.assertEqual(defaults["OPENCLAW_HOOKS_PATH"], "/hooks")
        self.assertEqual(defaults["NEMOCLAW_INSTALL_REF"], "")
        self.assertEqual(defaults["VSS_ORCHESTRATOR_MCP_TYPE"], "sse")

    def test_agent_setup_cell_allows_managed_python_downloads(self):
        notebook = json.loads((REPO_ROOT / "deploy" / "docker" / "scripts" / "deploy_nemoclaw_vss.ipynb").read_text())
        setup_cells = [cell for cell in notebook["cells"] if cell.get("id") == "c13aaf5e"]
        self.assertEqual(len(setup_cells), 1)
        source = "".join(setup_cells[0].get("source", ""))

        self.assertIn('uv_env["UV_PYTHON_DOWNLOADS"] = "automatic"', source)
        self.assertIn('run_uv(["uv", "python", "install", "3.13"])', source)
        self.assertIn('run_uv(["uv", "venv", "--clear", "--python", "3.13"])', source)
        self.assertIn("stdout tail", source)
        self.assertIn("stderr tail", source)
        compile(source, "deploy_nemoclaw_vss.ipynb:c13aaf5e", "exec")


class SkillsEvalAgentProtocolTest(unittest.TestCase):
    def test_final_marker_must_be_last_nonempty_line(self):
        self.assertIsNone(
            skills_eval_agent._final_protocol_marker([
                "I will emit `DONE:` later.\n",
                "The monitor is still running.",
            ])
        )
        self.assertEqual(
            skills_eval_agent._final_protocol_marker(["analysis\n", "BLOCKED: mcp policy denied\n"]),
            "BLOCKED: mcp policy denied",
        )


class NemoClawEnvFileTest(unittest.TestCase):
    def test_headless_runner_reads_hooks_token_from_token_file(self):
        with tempfile.TemporaryDirectory() as td:
            token_path = Path(td) / "hooks_token"
            token_path.write_text("secret-token\n", encoding="utf-8")
            previous = {
                "OPENCLAW_HOOKS_TOKEN": os.environ.pop("OPENCLAW_HOOKS_TOKEN", None),
                "NEMOCLAW_HOOKS_TOKEN_FILE": os.environ.get("NEMOCLAW_HOOKS_TOKEN_FILE"),
            }
            os.environ["NEMOCLAW_HOOKS_TOKEN_FILE"] = str(token_path)
            try:
                self.assertEqual(headless_runner._read_hooks_token(), "secret-token")
            finally:
                if previous["OPENCLAW_HOOKS_TOKEN"] is not None:
                    os.environ["OPENCLAW_HOOKS_TOKEN"] = previous["OPENCLAW_HOOKS_TOKEN"]
                else:
                    os.environ.pop("OPENCLAW_HOOKS_TOKEN", None)
                if previous["NEMOCLAW_HOOKS_TOKEN_FILE"] is not None:
                    os.environ["NEMOCLAW_HOOKS_TOKEN_FILE"] = previous["NEMOCLAW_HOOKS_TOKEN_FILE"]
                else:
                    os.environ.pop("NEMOCLAW_HOOKS_TOKEN_FILE", None)

    def test_readiness_env_parser_matches_shell_quoting(self):
        with tempfile.TemporaryDirectory() as td:
            env_path = Path(td) / "nemoclaw.env"
            env_path.write_text("export NEMOCLAW_SANDBOX_NAME='demo sandbox'\n", encoding="utf-8")
            previous = os.environ.pop("NEMOCLAW_SANDBOX_NAME", None)
            try:
                readiness._load_env_file(env_path)
                self.assertEqual(os.environ["NEMOCLAW_SANDBOX_NAME"], "demo sandbox")
            finally:
                if previous is not None:
                    os.environ["NEMOCLAW_SANDBOX_NAME"] = previous
                else:
                    os.environ.pop("NEMOCLAW_SANDBOX_NAME", None)


class NemoClawHeadlessRunnerTest(unittest.TestCase):
    def test_non_json_hook_response_is_not_treated_as_success(self):
        self.assertFalse(headless_runner._response_ok({"status": 200, "body": "ok"}))
        self.assertTrue(headless_runner._response_ok({"status": 200, "body": {"ok": True}}))

    def test_healthy_dashboard_forward_is_kept_even_if_registry_is_empty(self):
        calls: list[tuple[str, ...]] = []
        previous = {
            "_dashboard_healthy": headless_runner._dashboard_healthy,
            "_forward_running": headless_runner._forward_running,
            "_run": headless_runner._run,
        }

        def fake_run(cmd, *, timeout=30):
            calls.append(tuple(cmd))
            raise AssertionError("ensure_forward should not restart a healthy dashboard")

        headless_runner._dashboard_healthy = lambda port: True
        headless_runner._forward_running = lambda port, sandbox: False
        headless_runner._run = fake_run
        try:
            headless_runner.ensure_forward("18789", "demo")
        finally:
            headless_runner._dashboard_healthy = previous["_dashboard_healthy"]
            headless_runner._forward_running = previous["_forward_running"]
            headless_runner._run = previous["_run"]

        self.assertEqual(calls, [])

    def test_forward_failure_writes_structured_report(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prompt = root / "prompt.md"
            log_dir = root / "logs"
            prompt.write_text("deploy base", encoding="utf-8")
            previous = {
                "OPENCLAW_HOOKS_TOKEN": os.environ.get("OPENCLAW_HOOKS_TOKEN"),
                "NEMOCLAW_HOOKS_TOKEN_FILE": os.environ.get("NEMOCLAW_HOOKS_TOKEN_FILE"),
                "ensure_forward": headless_runner.ensure_forward,
            }
            os.environ["OPENCLAW_HOOKS_TOKEN"] = "token"
            os.environ.pop("NEMOCLAW_HOOKS_TOKEN_FILE", None)
            headless_runner.ensure_forward = lambda port, sandbox: (_ for _ in ()).throw(RuntimeError("forward down"))
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = headless_runner.main([
                        "--prompt-file",
                        str(prompt),
                        "--log-dir",
                        str(log_dir),
                    ])
            finally:
                headless_runner.ensure_forward = previous["ensure_forward"]
                if previous["OPENCLAW_HOOKS_TOKEN"] is None:
                    os.environ.pop("OPENCLAW_HOOKS_TOKEN", None)
                else:
                    os.environ["OPENCLAW_HOOKS_TOKEN"] = previous["OPENCLAW_HOOKS_TOKEN"]
                if previous["NEMOCLAW_HOOKS_TOKEN_FILE"] is None:
                    os.environ.pop("NEMOCLAW_HOOKS_TOKEN_FILE", None)
                else:
                    os.environ["NEMOCLAW_HOOKS_TOKEN_FILE"] = previous["NEMOCLAW_HOOKS_TOKEN_FILE"]

            report = json.loads((log_dir / "nemoclaw_hooks_response.json").read_text(encoding="utf-8"))

        self.assertEqual(rc, 1)
        self.assertEqual(report["response"]["error_type"], "RuntimeError")
        self.assertIn("forward down", report["response"]["error"])

    def test_missing_prompt_file_writes_structured_report(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            missing_prompt = root / "missing.md"
            previous = {
                "OPENCLAW_HOOKS_TOKEN": os.environ.get("OPENCLAW_HOOKS_TOKEN"),
                "NEMOCLAW_HOOKS_TOKEN_FILE": os.environ.get("NEMOCLAW_HOOKS_TOKEN_FILE"),
            }
            os.environ["OPENCLAW_HOOKS_TOKEN"] = "token"
            os.environ.pop("NEMOCLAW_HOOKS_TOKEN_FILE", None)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = headless_runner.main([
                        "--prompt-file",
                        str(missing_prompt),
                        "--log-dir",
                        str(log_dir),
                    ])
            finally:
                if previous["OPENCLAW_HOOKS_TOKEN"] is None:
                    os.environ.pop("OPENCLAW_HOOKS_TOKEN", None)
                else:
                    os.environ["OPENCLAW_HOOKS_TOKEN"] = previous["OPENCLAW_HOOKS_TOKEN"]
                if previous["NEMOCLAW_HOOKS_TOKEN_FILE"] is None:
                    os.environ.pop("NEMOCLAW_HOOKS_TOKEN_FILE", None)
                else:
                    os.environ["NEMOCLAW_HOOKS_TOKEN_FILE"] = previous["NEMOCLAW_HOOKS_TOKEN_FILE"]

            report = json.loads((log_dir / "nemoclaw_hooks_response.json").read_text(encoding="utf-8"))

        self.assertEqual(rc, 1)
        self.assertEqual(report["response"]["error_type"], "FileNotFoundError")
        self.assertIn("missing.md", report["response"]["error"])

    def test_cli_launch_runs_openclaw_agent_inside_sandbox(self):
        calls: list[tuple[str, ...]] = []
        previous = {
            "_run": headless_runner._run,
            "_gateway_reachable": headless_runner._gateway_reachable,
        }

        def fake_run(cmd, *, timeout=30):
            calls.append(tuple(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="agent done", stderr="")

        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            headless_runner._run = fake_run
            headless_runner._gateway_reachable = lambda sandbox: True
            try:
                response = headless_runner.run_openclaw_cli(
                    "demo",
                    "Deploy base",
                    30,
                    log_dir,
                )
            finally:
                headless_runner._run = previous["_run"]
                headless_runner._gateway_reachable = previous["_gateway_reachable"]

            launch_log = (log_dir / "openclaw-launch.log").read_text(encoding="utf-8")

        self.assertEqual(response["status"], 200)
        self.assertEqual(response["body"]["mode"], "cli")
        self.assertEqual(response["body"]["returncode"], 0)
        self.assertTrue(any("base64 -d" in " ".join(call) for call in calls))
        self.assertIn("agent done", launch_log)
        wrapper = next(call[-1] for call in calls if "base64 -d" in " ".join(call))
        encoded = wrapper.split("printf %s ", 1)[1].split(" | base64 -d", 1)[0].strip("'")
        script = base64.b64decode(encoded).decode("utf-8")
        self.assertNotIn("nohup sh -lc", script)
        self.assertIn("wait \"$pid\"", script)
        self.assertIn("openclaw-agent.rc", script)
        self.assertIn("--message", script)
        self.assertNotIn("--local", script)
        self.assertIn("--json", script)
        self.assertIn("OPENCLAW_DISABLE_STREAMING_TOOL_CALLS=1", script)

    def test_collect_openclaw_cli_log_copies_sandbox_output(self):
        calls: list[tuple[str, ...]] = []
        previous = headless_runner._run

        def fake_run(cmd, *, timeout=30):
            calls.append(tuple(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="agent transcript", stderr="")

        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            headless_runner._run = fake_run
            try:
                headless_runner.collect_openclaw_cli_log("demo", log_dir)
            finally:
                headless_runner._run = previous

            openclaw_log = (log_dir / "openclaw-agent.log").read_text(encoding="utf-8")

        self.assertEqual(openclaw_log, "agent transcript")
        wrapper = next(call[-1] for call in calls if "base64 -d" in " ".join(call))
        encoded = wrapper.split("printf %s ", 1)[1].split(" | base64 -d", 1)[0].strip("'")
        script = base64.b64decode(encoded).decode("utf-8")
        self.assertIn("openclaw-agent.log", script)

    def test_cli_launch_stops_openclaw_even_when_readiness_fails(self):
        calls: list[str] = []
        previous = {
            "run_openclaw_cli": headless_runner.run_openclaw_cli,
            "wait_for_profile": headless_runner.wait_for_profile,
            "collect_openclaw_cli_log": headless_runner.collect_openclaw_cli_log,
            "stop_openclaw_cli": headless_runner.stop_openclaw_cli,
        }

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prompt = root / "prompt.md"
            prompt.write_text("Deploy base", encoding="utf-8")
            log_dir = root / "logs"

            headless_runner.run_openclaw_cli = lambda sandbox, message, timeout, logs: {
                "status": 202,
                "body": {"ok": True},
            }
            headless_runner.wait_for_profile = lambda profile, timeout, logs: {
                "waited": True,
                "ok": False,
                "profile": profile,
            }
            headless_runner.collect_openclaw_cli_log = lambda sandbox, logs: calls.append("collect")
            headless_runner.stop_openclaw_cli = lambda sandbox: calls.append("stop")
            try:
                rc = headless_runner.main([
                    "--prompt-file",
                    str(prompt),
                    "--log-dir",
                    str(log_dir),
                    "--launch-mode",
                    "cli",
                    "--wait-profile",
                    "base",
                ])
            finally:
                headless_runner.run_openclaw_cli = previous["run_openclaw_cli"]
                headless_runner.wait_for_profile = previous["wait_for_profile"]
                headless_runner.collect_openclaw_cli_log = previous["collect_openclaw_cli_log"]
                headless_runner.stop_openclaw_cli = previous["stop_openclaw_cli"]

        self.assertEqual(rc, 1)
        self.assertEqual(calls, ["collect", "stop"])

    def test_sandbox_exec_wraps_multiline_scripts_for_openshell(self):
        calls: list[tuple[str, ...]] = []
        previous = {
            "_run": headless_runner._run,
            "shutil_which": headless_runner.shutil_which,
        }

        def fake_run(cmd, *, timeout=30):
            calls.append(tuple(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

        headless_runner._run = fake_run
        headless_runner.shutil_which = lambda name: "/usr/bin/openshell" if name == "openshell" else None
        try:
            result = headless_runner._sandbox_exec("demo", "echo one\necho two", timeout=30)
        finally:
            headless_runner._run = previous["_run"]
            headless_runner.shutil_which = previous["shutil_which"]

        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)
        command = calls[0]
        self.assertEqual(command[:5], ("openshell", "sandbox", "exec", "-n", "demo"))
        self.assertTrue(all("\n" not in arg and "\r" not in arg for arg in command))
        self.assertIn("base64 -d", " ".join(command))

    def test_gateway_recovery_uses_openshell_not_nemoclaw_recover(self):
        calls: list[tuple[str, ...]] = []
        gateway_checks = iter([False, True])
        previous = {
            "_run": headless_runner._run,
            "_gateway_reachable": headless_runner._gateway_reachable,
            "shutil_which": headless_runner.shutil_which,
        }

        def fake_run(cmd, *, timeout=30):
            calls.append(tuple(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            headless_runner._run = fake_run
            headless_runner._gateway_reachable = lambda sandbox: next(gateway_checks)
            headless_runner.shutil_which = lambda name: "/usr/bin/openshell" if name == "openshell" else None
            try:
                headless_runner.ensure_openclaw_gateway("demo", log_dir)
            finally:
                headless_runner._run = previous["_run"]
                headless_runner._gateway_reachable = previous["_gateway_reachable"]
                headless_runner.shutil_which = previous["shutil_which"]

            recover_log = (log_dir / "openclaw_gateway_recover.log").read_text(encoding="utf-8")

        self.assertIn("returncode=0", recover_log)
        command_text = "\n".join(" ".join(call) for call in calls)
        self.assertIn("openshell sandbox exec -n demo", command_text)
        self.assertNotIn("nemoclaw demo recover", command_text)
        wrapper = next(call[-1] for call in calls if "base64 -d" in " ".join(call))
        encoded = wrapper.split("printf %s ", 1)[1].split(" | base64 -d", 1)[0].strip("'")
        script = base64.b64decode(encoded).decode("utf-8")
        self.assertIn("openclaw dashboard", script)
        self.assertIn("systemctl --user restart openclaw-gateway", script)


class NemoClawSmokeRunnerTest(unittest.TestCase):
    def test_default_smoke_profile_is_lightweight_base(self):
        self.assertEqual(smoke_runner.DEFAULT_PROFILE, "base")
        self.assertEqual(
            smoke_runner._gpu_count_from_spec("base", "RTXPRO6000BW"),
            1,
        )

    def test_brev_json_parser_ignores_trailing_cli_text(self):
        raw = '[{"name":"vss-eval-rtx-1g-2","status":"RUNNING READY"}]\nNext steps...'

        parsed = smoke_runner._parse_brev_json(raw)

        self.assertEqual(parsed[0]["name"], "vss-eval-rtx-1g-2")

    def test_instance_candidates_prefer_matching_gpu_partition(self):
        instances = [
            {
                "name": "vss-eval-rtx-2g",
                "status": "RUNNING",
                "gpu": "RTX PRO 6000",
                "instance_type": "g7e.12xlarge",
            },
            {
                "name": "vss-eval-rtx-1g-2",
                "status": "RUNNING",
                "gpu": "RTX PRO 6000",
                "instance_type": "g7e.4xlarge",
            },
            {
                "name": "personal-rtx",
                "status": "RUNNING READY",
                "gpu": "RTX PRO 6000",
            },
        ]

        candidates = smoke_runner._instance_candidates(
            instances,
            platform="RTXPRO6000BW",
            gpu_count=1,
        )

        self.assertEqual(candidates[0], "vss-eval-rtx-1g-2")
        self.assertNotIn("personal-rtx", candidates)
        self.assertIn("vss-eval-rtx-2g", candidates)

    def test_instance_candidates_allow_larger_partition_for_one_gpu_smoke(self):
        instances = [
            {"name": "vss-eval-rtx-1g-2", "status": "RUNNING", "gpu": "RTX PRO 6000"},
            {"name": "vss-eval-rtx-2g-4", "status": "RUNNING", "gpu": "RTX PRO 6000"},
        ]

        one_gpu = smoke_runner._instance_candidates(
            instances,
            platform="RTXPRO6000BW",
            gpu_count=1,
        )
        two_gpu = smoke_runner._instance_candidates(
            instances,
            platform="RTXPRO6000BW",
            gpu_count=2,
        )

        self.assertEqual(one_gpu, ["vss-eval-rtx-1g-2", "vss-eval-rtx-2g-4"])
        self.assertEqual(two_gpu, ["vss-eval-rtx-2g-4"])

    def test_instance_candidates_allow_any_platform_for_gpu_free_tasks(self):
        instances = [
            {"name": "vss-eval-l40s-1g", "status": "RUNNING", "gpu": "L40S"},
            {"name": "vss-eval-rtx-1g", "status": "RUNNING", "gpu": "RTX PRO 6000"},
            {"name": "personal-l40s", "status": "RUNNING", "gpu": "L40S"},
        ]

        candidates = smoke_runner._instance_candidates(
            instances,
            platform="ANY",
            gpu_count=0,
        )

        self.assertCountEqual(candidates, ["vss-eval-l40s-1g", "vss-eval-rtx-1g"])
        self.assertNotIn("personal-l40s", candidates)

    def test_all_skills_matrix_uses_one_representative_row_per_skill(self):
        rows, blockers = smoke_runner._build_matrix(
            skills_filter="*",
            profile_filter=None,
            platform_filter=None,
            spec_filter=None,
            representative_per_skill=True,
        )

        skills = [row["skill"] for row in rows]
        self.assertEqual(len(skills), len(set(skills)))
        self.assertTrue(all(row["task_limit"] == "1" for row in rows))
        self.assertIn("vss-deploy-profile", skills)
        self.assertIn("vss-ask-video", skills)
        self.assertNotIn("vss-deploy-detection-tracking-2d", skills)
        self.assertNotIn("vss-deploy-detection-tracking-3d", skills)
        self.assertNotIn("vss-deploy-video-embedding", skills)
        self.assertNotIn("vss-generate-video-calibration", skills)
        self.assertNotIn("vss-manage-video-io-storage", skills)
        self.assertNotIn("vss-search-archive", skills)
        self.assertNotIn("vss-setup-behavior-analytics", skills)
        self.assertNotIn("vss-setup-video-analytics-api", skills)
        self.assertNotIn("evals", [row["spec_stem"] for row in rows])
        self.assertTrue(
            any("vss-generate-video-report-rag: missing Harbor adapter" in item for item in blockers)
        )
        self.assertTrue(
            any(
                "vss-setup-behavior-analytics/standalone_deploy.json: standalone host-Docker eval"
                in item
                for item in blockers
            )
        )
        self.assertTrue(
            any(
                "vss-search-archive/search.json: search archive is not yet bounded"
                in item
                for item in blockers
            )
        )

    def test_standalone_host_docker_spec_is_blocked_for_nemoclaw(self):
        rows, blockers = smoke_runner._build_matrix(
            skills_filter="vss-setup-behavior-analytics",
            profile_filter=None,
            platform_filter=None,
            spec_filter=None,
            representative_per_skill=False,
        )

        self.assertEqual(rows, [])
        self.assertTrue(
            any(
                "vss-setup-behavior-analytics/standalone_deploy.json: standalone host-Docker eval"
                in item
                for item in blockers
            )
        )

    def test_explicit_array_spec_is_not_treated_as_nemoclaw_live_scenario(self):
        rows, blockers = smoke_runner._build_matrix(
            skills_filter="vss-search-archive",
            profile_filter=None,
            platform_filter=None,
            spec_filter="evals",
            representative_per_skill=False,
        )

        self.assertEqual(rows, [])
        self.assertTrue(
            any(
                "vss-search-archive/evals.json: array-format skill eval is not a NemoClaw live scenario"
                in item
                for item in blockers
            )
        )

    def test_task_dir_sort_key_orders_steps_naturally(self):
        root = Path("/tmp/dataset/base/l40s")
        task_dirs = [root / "step-10", root / "step-2", root / "step-1"]

        ordered = sorted(task_dirs, key=smoke_runner._task_dir_sort_key)

        self.assertEqual([path.name for path in ordered], ["step-1", "step-2", "step-10"])

    def test_scenario_groups_keep_multistep_tasks_on_same_worker(self):
        root = Path("/tmp/dataset/base/l40s")
        scenarios = [
            smoke_runner.NemoClawScenario(
                skill="vss-ask-video",
                spec_name="base_profile_video_understanding",
                spec_path=Path("spec.json"),
                platform="L40S",
                gpu_count=1,
                task_dir=root / "step-1",
                harbor_path=root,
                task_name="step-1",
                deployment_profile="base",
            ),
            smoke_runner.NemoClawScenario(
                skill="vss-ask-video",
                spec_name="base_profile_video_understanding",
                spec_path=Path("spec.json"),
                platform="L40S",
                gpu_count=1,
                task_dir=root / "step-2",
                harbor_path=root,
                task_name="step-2",
                deployment_profile="base",
            ),
            smoke_runner.NemoClawScenario(
                skill="vss-deploy-profile",
                spec_name="base",
                spec_path=Path("base.json"),
                platform="RTXPRO6000BW",
                gpu_count=1,
                task_dir=Path("/tmp/dataset/deploy/base/rtxpro6000bw"),
                harbor_path=Path("/tmp/dataset/deploy/base"),
                task_name="rtxpro6000bw",
                deployment_profile="base",
            ),
        ]

        groups = smoke_runner._scenario_groups(scenarios)

        self.assertEqual([len(group) for group in groups], [2, 1])
        self.assertEqual([scenario.task_name for scenario in groups[0]], ["step-1", "step-2"])

    def test_focused_deploy_profile_matrix_keeps_base_smoke(self):
        rows, blockers = smoke_runner._build_matrix(
            skills_filter="vss-deploy-profile",
            profile_filter="base",
            platform_filter="RTXPRO6000BW",
            spec_filter=None,
            representative_per_skill=False,
        )

        self.assertEqual(blockers, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["skill"], "vss-deploy-profile")
        self.assertEqual(rows[0]["spec_stem"], "base")
        self.assertEqual(rows[0]["platform"], "RTXPRO6000BW")
        self.assertEqual(rows[0]["task_limit"], "0")

    def test_worker_selection_skips_locked_candidate(self):
        previous = {
            "_list_instances": smoke_runner._list_instances,
            "_reachable": smoke_runner._reachable,
            "_try_acquire_lock": smoke_runner._try_acquire_lock,
        }
        instances = [
            {"name": "vss-eval-rtx-1g-2", "status": "RUNNING", "gpu": "RTX PRO 6000"},
            {"name": "vss-eval-rtx-1g-3", "status": "RUNNING", "gpu": "RTX PRO 6000"},
        ]

        smoke_runner._list_instances = lambda: instances
        smoke_runner._reachable = lambda instance: True
        smoke_runner._try_acquire_lock = (
            lambda instance: None
            if instance == "vss-eval-rtx-1g-2"
            else smoke_runner.WorkerLock(123, object(), None)
        )
        try:
            selected, _lock = smoke_runner._select_and_lock_instance(
                "RTXPRO6000BW",
                1,
                None,
                10,
            )
        finally:
            smoke_runner._list_instances = previous["_list_instances"]
            smoke_runner._reachable = previous["_reachable"]
            smoke_runner._try_acquire_lock = previous["_try_acquire_lock"]

        self.assertEqual(selected, "vss-eval-rtx-1g-3")

    def test_worker_selection_reports_visible_pool_when_platform_missing(self):
        previous = {"_list_instances": smoke_runner._list_instances}
        smoke_runner._list_instances = lambda: [
            {"name": "vss-eval-l40s-1g", "status": "RUNNING", "gpu": "L40S"},
        ]
        try:
            with self.assertRaises(smoke_runner.InfrastructureBlocked) as ctx:
                smoke_runner._select_and_lock_instance(
                    "RTXPRO6000BW",
                    1,
                    None,
                    0,
                )
        finally:
            smoke_runner._list_instances = previous["_list_instances"]

        message = str(ctx.exception)
        self.assertIn("no running vss-eval-* candidate for RTXPRO6000BW", message)
        self.assertIn("vss-eval-l40s-1g", message)

    def test_worker_selection_retries_transient_inventory_timeout(self):
        previous = {
            "_list_instances": smoke_runner._list_instances,
            "_reachable": smoke_runner._reachable,
            "_try_acquire_lock": smoke_runner._try_acquire_lock,
            "sleep": smoke_runner.time.sleep,
        }
        calls = {"list": 0}

        def fake_list_instances():
            calls["list"] += 1
            if calls["list"] == 1:
                raise smoke_runner.InfrastructureBlocked(
                    "brev ls --json timed out after 45s"
                )
            return [
                {"name": "vss-eval-rtx-1g-2", "status": "RUNNING", "gpu": "RTX PRO 6000"},
            ]

        smoke_runner._list_instances = fake_list_instances
        smoke_runner._reachable = lambda instance: True
        smoke_runner._try_acquire_lock = lambda instance: smoke_runner.WorkerLock(
            123, object(), None
        )
        smoke_runner.time.sleep = lambda seconds: None
        try:
            selected, _lock = smoke_runner._select_and_lock_instance(
                "RTXPRO6000BW",
                1,
                None,
                10,
            )
        finally:
            smoke_runner._list_instances = previous["_list_instances"]
            smoke_runner._reachable = previous["_reachable"]
            smoke_runner._try_acquire_lock = previous["_try_acquire_lock"]
            smoke_runner.time.sleep = previous["sleep"]

        self.assertEqual(selected, "vss-eval-rtx-1g-2")
        self.assertEqual(calls["list"], 2)

    def test_worker_selection_reports_inventory_timeout_after_deadline(self):
        previous = {
            "_list_instances": smoke_runner._list_instances,
            "sleep": smoke_runner.time.sleep,
            "time": smoke_runner.time.time,
        }
        times = iter([0, 0, 20])

        smoke_runner._list_instances = lambda: (_ for _ in ()).throw(
            smoke_runner.InfrastructureBlocked("brev ls --json timed out after 45s")
        )
        smoke_runner.time.sleep = lambda seconds: None
        smoke_runner.time.time = lambda: next(times)
        try:
            with self.assertRaises(smoke_runner.InfrastructureBlocked) as ctx:
                smoke_runner._select_and_lock_instance(
                    "RTXPRO6000BW",
                    1,
                    None,
                    10,
                )
        finally:
            smoke_runner._list_instances = previous["_list_instances"]
            smoke_runner.time.sleep = previous["sleep"]
            smoke_runner.time.time = previous["time"]

        message = str(ctx.exception)
        self.assertIn("worker inventory unavailable for RTXPRO6000BW after 10s", message)
        self.assertIn("brev ls --json timed out after 45s", message)

    def test_brev_inventory_timeout_is_infrastructure_blocked(self):
        previous = {"_run": smoke_runner._run}
        smoke_runner._run = lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(["brev", "ls", "--json"], 45)
        )
        try:
            with self.assertRaises(smoke_runner.InfrastructureBlocked) as ctx:
                smoke_runner._list_instances()
        finally:
            smoke_runner._run = previous["_run"]

        self.assertIn("brev ls --json timed out after 45s", str(ctx.exception))

    def test_reachability_timeout_skips_candidate(self):
        previous = {"_run": smoke_runner._run}
        smoke_runner._run = lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(["brev", "exec", "vss-eval-rtx-2g-4"], 45)
        )
        try:
            reachable = smoke_runner._reachable("vss-eval-rtx-2g-4")
        finally:
            smoke_runner._run = previous["_run"]

        self.assertFalse(reachable)

    def test_generic_task_wrapper_creates_nemoclaw_launcher(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task_dir = root / "base" / "l40s" / "step-1"
            task_dir.mkdir(parents=True)
            (task_dir / "instruction.md").write_text(
                "Use the /vss-ask-video skill against the already running base profile.",
                encoding="utf-8",
            )
            (task_dir / "task.toml").write_text(
                textwrap.dedent(
                    """
                    [task]
                    name = "nvidia-vss/vss-ask-video-base-l40s-step-1"

                    [metadata]
                    skill = "vss-ask-video"
                    profile = "base"
                    platform = "L40S"
                    gpu_count = 1
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            scenario = smoke_runner._wrap_task_for_nemoclaw(
                task_dir=task_dir,
                skill="vss-ask-video",
                spec_path=REPO_ROOT / "skills" / "vss-ask-video" / "evals" / "base_profile_video_understanding.json",
                platform="L40S",
            )

            prompt = (task_dir / "tests" / "nemoclaw_prompt.md").read_text(encoding="utf-8")
            instruction = (task_dir / "instruction.md").read_text(encoding="utf-8")
            task_toml = (task_dir / "task.toml").read_text(encoding="utf-8")

        self.assertEqual(scenario.skill, "vss-ask-video")
        self.assertEqual(scenario.task_name, "step-1")
        self.assertEqual(scenario.deployment_profile, "base")
        self.assertIn("Use the `/vss-ask-video` skill as the primary workflow", prompt)
        self.assertIn("requires the `base` VSS profile", prompt)
        self.assertIn("Use the /vss-ask-video skill against", prompt)
        self.assertIn("headless_runner.py", instruction)
        self.assertIn("--wait-profile base", instruction)
        self.assertIn('runner = "nemoclaw"', task_toml)
        self.assertIn('expected_skill = "vss-ask-video"', task_toml)
        self.assertIn("vss_orchestrator__docker_status", task_toml)

    def test_generic_task_wrapper_replaces_stale_launcher_without_wait_profile(self):
        with tempfile.TemporaryDirectory() as td:
            task_dir = Path(td) / "base" / "rtxpro6000bw" / "step-1"
            task_dir.mkdir(parents=True)
            (task_dir / "instruction.md").write_text(
                textwrap.dedent(
                    """
                    This Harbor trial is a thin launcher for NemoClaw/OpenClaw.

                    ```bash
                    python3 .github/skill-eval/nemoclaw/headless_runner.py \\
                      --prompt-file /tests/nemoclaw_prompt.md \\
                      --log-dir /logs/artifacts/nemoclaw \\
                      --launch-mode cli \\
                      --timeout 2400
                    ```
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (task_dir / "task.toml").write_text(
                textwrap.dedent(
                    """
                    [task]
                    name = "nvidia-vss/vss-ask-video-base-rtx-step-1"

                    [metadata]
                    skill = "vss-ask-video"
                    profile = "base"
                    platform = "RTXPRO6000BW"
                    gpu_count = 1
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            smoke_runner._wrap_task_for_nemoclaw(
                task_dir=task_dir,
                skill="vss-ask-video",
                spec_path=REPO_ROOT / "skills" / "vss-ask-video" / "evals" / "base_profile_video_understanding.json",
                platform="RTXPRO6000BW",
            )

            instruction = (task_dir / "instruction.md").read_text(encoding="utf-8")

        self.assertIn("headless_runner.py", instruction)
        self.assertIn("--wait-profile base", instruction)

    def test_generic_task_wrapper_infers_profile_from_eval_spec(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task_dir = root / "generated" / "rtxpro6000bw" / "step-1"
            task_dir.mkdir(parents=True)
            (task_dir / "instruction.md").write_text(
                "Existing launcher without profile wait\n"
                "python3 .github/skill-eval/nemoclaw/headless_runner.py\n",
                encoding="utf-8",
            )
            (task_dir / "task.toml").write_text(
                textwrap.dedent(
                    """
                    [task]
                    name = "nvidia-vss/generated-alerts-step-1"

                    [metadata]
                    skill = "vss-manage-alerts"
                    platform = "RTXPRO6000BW"
                    gpu_count = 1
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            spec_path = root / "alerts_vlm_real_time.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "expects": [
                            {
                                "query": "Deploy the VSS **alerts** profile in `real-time` mode on `{{platform}}`."
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            scenario = smoke_runner._wrap_task_for_nemoclaw(
                task_dir=task_dir,
                skill="vss-manage-alerts",
                spec_path=spec_path,
                platform="RTXPRO6000BW",
            )

            instruction = (task_dir / "instruction.md").read_text(encoding="utf-8")
            task_toml = (task_dir / "task.toml").read_text(encoding="utf-8")

        self.assertEqual(scenario.deployment_profile, "alerts")
        self.assertIn("--wait-profile alerts", instruction)
        self.assertIn('deployment_profile = "alerts"', task_toml)

    def test_task_metadata_reader_falls_back_without_tomllib(self):
        with tempfile.TemporaryDirectory() as td:
            task_dir = Path(td)
            (task_dir / "task.toml").write_text(
                textwrap.dedent(
                    """
                    [metadata]
                    skill = "vss-ask-video"
                    profile = "base"
                    platform = "L40S"
                    gpu_count = 1
                    requires_nemoclaw = true
                    required_mcp_tools = ["vss_orchestrator__profiles", "vss_orchestrator__docker_status"]
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            previous = smoke_runner.tomllib
            smoke_runner.tomllib = None
            try:
                parsed = smoke_runner._read_task_toml(task_dir)
            finally:
                smoke_runner.tomllib = previous

        self.assertEqual(parsed["metadata"]["skill"], "vss-ask-video")
        self.assertEqual(parsed["metadata"]["gpu_count"], 1)
        self.assertTrue(parsed["metadata"]["requires_nemoclaw"])
        self.assertEqual(
            parsed["metadata"]["required_mcp_tools"],
            ["vss_orchestrator__profiles", "vss_orchestrator__docker_status"],
        )

    def test_nemoclaw_report_uses_harbor_eval_format(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            results_root = root / "results"
            run_id = "123456"
            trial_dir = (
                results_root
                / run_id
                / "2026-06-02__08-00-00"
                / "nvidia-vss-vss-deploy-profile-base-rtxpro6000bw"
            )
            (trial_dir / "verifier").mkdir(parents=True)
            (trial_dir / "agent").mkdir()
            (trial_dir / "result.json").write_text(
                json.dumps(
                    {
                        "trial_started_at": "2026-06-02T08:00:00Z",
                        "trial_finished_at": "2026-06-02T08:26:57Z",
                    }
                ),
                encoding="utf-8",
            )
            (trial_dir / "verifier" / "reward.txt").write_text("0.5", encoding="utf-8")
            (trial_dir / "verifier" / "judge.json").write_text(
                json.dumps(
                    {
                        "total": 2,
                        "passed": 1,
                        "checks": [
                            {"pass": True, "check": "docs endpoint responds"},
                            {
                                "pass": False,
                                "check": "MCP docker_status reached terminal state",
                                "rationale": "docker_status was not observed",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (trial_dir / "agent" / "trajectory.json").write_text(
                json.dumps(
                    {
                        "steps": [
                            {
                                "message": json.dumps(
                                    {
                                        "type": "assistant",
                                        "message": {
                                            "usage": {
                                                "input_tokens": 100,
                                                "cache_read_input_tokens": 10,
                                            }
                                        },
                                    }
                                )
                            }
                        ],
                        "final_metrics": {
                            "modelUsage": {
                                "claude": {
                                    "inputTokens": 8400,
                                    "cacheReadInputTokens": 100,
                                    "cacheCreationInputTokens": 50,
                                }
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            summary = root / "summary.md"
            previous = {
                "GITHUB_STEP_SUMMARY": os.environ.get("GITHUB_STEP_SUMMARY"),
                "GITHUB_RUN_ID": os.environ.get("GITHUB_RUN_ID"),
                "PR_HEAD_SHA": os.environ.get("PR_HEAD_SHA"),
                "PR_REPO": os.environ.get("PR_REPO"),
                "BREV_ENV_ID": os.environ.get("BREV_ENV_ID"),
            }
            os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
            os.environ["GITHUB_RUN_ID"] = run_id
            os.environ["PR_HEAD_SHA"] = "abcdef0123456789"
            os.environ["PR_REPO"] = "NVIDIA-AI-Blueprints/video-search-and-summarization"
            os.environ["BREV_ENV_ID"] = "abc123"
            old_scratch = smoke_runner.SCRATCH_ROOT
            smoke_runner.SCRATCH_ROOT = root / "scratch"
            scenario = smoke_runner.NemoClawScenario(
                skill="vss-deploy-profile",
                spec_name="base",
                spec_path=REPO_ROOT / "skills" / "vss-deploy-profile" / "evals" / "base.json",
                platform="RTXPRO6000BW",
                gpu_count=1,
                task_dir=trial_dir,
                harbor_path=trial_dir.parent,
                task_name="rtxpro6000bw",
                deployment_profile="base",
            )
            try:
                smoke_runner._append_harbor_report(
                    scenario=scenario,
                    instance="vss-eval-rtx-1g-2",
                    results_root=results_root,
                    run_id=run_id,
                    reward=0.5,
                    harbor_rc=1,
                    log_path=root / "harbor.log",
                )
            finally:
                smoke_runner.SCRATCH_ROOT = old_scratch
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

            report = summary.read_text(encoding="utf-8")
            benchmark = (root / "scratch" / run_id / "benchmark.md").read_text(encoding="utf-8")

        self.assertIn("## Harbor Eval - `skills/vss-deploy-profile/evals/base.json`", report)
        self.assertIn("runtime `NemoClaw/OpenClaw`", report)
        self.assertIn("| RTXPRO6000BW | FAIL 0.5 (1/2) | 0.5 | 26m 57s | 1 | 8.4k | 150 |", report)
        self.assertIn("MCP docker_status reached terminal state", report)
        self.assertIn("[trace](https://harbor-abc123.brevlab.com/jobs/", report)
        self.assertIn("Skills Eval Benchmark - NemoClaw sweep", benchmark)

    def test_nemoclaw_report_prefers_leaf_trial_and_links_run_when_viewer_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            results_root = root / "results"
            run_id = "999"
            job_dir = results_root / run_id / "2026-06-02__08-00-00"
            trial_dir = job_dir / "rtxpro6000bw__abc"
            trial_dir.mkdir(parents=True)
            (job_dir / "result.json").write_text(
                json.dumps({"started_at": "2026-06-02T08:00:00Z", "finished_at": "2026-06-02T09:00:00Z"}),
                encoding="utf-8",
            )
            (trial_dir / "verifier").mkdir()
            (trial_dir / "result.json").write_text(
                json.dumps(
                    {
                        "started_at": "2026-06-02T08:10:00Z",
                        "finished_at": "2026-06-02T08:20:00Z",
                        "agent_result": {
                            "n_input_tokens": None,
                            "n_cache_tokens": None,
                        },
                    }
                ),
                encoding="utf-8",
            )
            (trial_dir / "verifier" / "reward.txt").write_text("1.0", encoding="utf-8")
            (trial_dir / "verifier" / "judge.json").write_text(
                json.dumps({"total": 7, "passed": 7, "checks": [{"pass": True, "check": "ok"}]}),
                encoding="utf-8",
            )
            summary = root / "summary.md"
            previous = {
                "GITHUB_STEP_SUMMARY": os.environ.get("GITHUB_STEP_SUMMARY"),
                "GITHUB_RUN_ID": os.environ.get("GITHUB_RUN_ID"),
                "PR_REPO": os.environ.get("PR_REPO"),
                "BREV_ENV_ID": os.environ.get("BREV_ENV_ID"),
            }
            os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
            os.environ["GITHUB_RUN_ID"] = run_id
            os.environ["PR_REPO"] = "NVIDIA-AI-Blueprints/video-search-and-summarization"
            os.environ.pop("BREV_ENV_ID", None)
            old_scratch = smoke_runner.SCRATCH_ROOT
            smoke_runner.SCRATCH_ROOT = root / "scratch"
            scenario = smoke_runner.NemoClawScenario(
                skill="vss-deploy-profile",
                spec_name="base",
                spec_path=REPO_ROOT / "skills" / "vss-deploy-profile" / "evals" / "base.json",
                platform="RTXPRO6000BW",
                gpu_count=1,
                task_dir=trial_dir,
                harbor_path=trial_dir.parent,
                task_name="rtxpro6000bw",
                deployment_profile="base",
            )
            try:
                smoke_runner._append_harbor_report(
                    scenario=scenario,
                    instance="vss-eval-rtx-2g-4",
                    results_root=results_root,
                    run_id=run_id,
                    reward=1.0,
                    harbor_rc=0,
                    log_path=root / "harbor.log",
                )
            finally:
                smoke_runner.SCRATCH_ROOT = old_scratch
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

            report = summary.read_text(encoding="utf-8")

        self.assertIn("PASS 1 (7/7)", report)
        self.assertIn("Total: `10m 0s`", report)
        self.assertIn("| RTXPRO6000BW | PASS 1 (7/7) | 1 | 10m 0s | n/a | n/a | n/a |", report)
        self.assertIn("[artifacts](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/actions/runs/999)", report)


class OpenClawStreamPatchTest(unittest.TestCase):
    def test_patch_openai_chat_completions_disables_streaming_tools(self):
        source = textwrap.dedent(
            """
            function buildOpenAICompletionsParams(context) {
              return {
                model: context.model,
                messages: context.messages,
                stream: true,
                stream_options: {
                  include_usage: true,
                },
                temperature: 0,
              };
            }
            function buildOpenAIResponsesParams(context) {
              return { stream: true };
            }
            """
        )

        updated, found, changed = openclaw_stream_patch.patch_source(source)

        self.assertTrue(found)
        self.assertTrue(changed)
        self.assertIn("stream: false", updated)
        self.assertNotIn("stream_options", updated.split("buildOpenAIResponsesParams", 1)[0])
        self.assertNotIn("stream: true", updated)

    def test_patch_openai_responses_disables_streaming_tools(self):
        source = textwrap.dedent(
            """
            function buildOpenAIResponsesParams(context) {
              return {
                model: context.model,
                input: context.input,
                stream: true,
                stream_options: {
                  include_usage: true,
                },
              };
            }
            """
        )

        updated, found, changed = openclaw_stream_patch.patch_source(source)

        self.assertTrue(found)
        self.assertTrue(changed)
        self.assertIn("stream: false", updated)
        self.assertNotIn("stream_options", updated)

    def test_patch_ignores_references_before_real_function_definition(self):
        source = textwrap.dedent(
            """
            const selected = buildOpenAICompletionsParams;
            const unrelated = { stream: true };
            function buildOpenAICompletionsParams(context) {
              return {
                stream: true,
                stream_options: { include_usage: true },
              };
            }
            """
        )

        updated, found, changed = openclaw_stream_patch.patch_source(source)

        self.assertTrue(found)
        self.assertTrue(changed)
        self.assertIn("const unrelated = { stream: true };", updated)
        self.assertIn("stream: false", updated)


class InitNemoClawScriptTest(unittest.TestCase):
    def test_dashboard_forward_cleanup_handles_stale_port_listener(self):
        source = (REPO_ROOT / "deploy" / "docker" / "scripts" / "nemoclaw" / "init_nemoclaw.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("kill_stale_dashboard_listeners", source)
        self.assertIn("lsof -tiTCP", source)
        self.assertIn("kill_stale_dashboard_listeners \"$port\"", source)
        self.assertIn("start_dashboard_forward \"$port\" \"$forward_log\"", source)
        self.assertIn("retrying start", source)

    def test_existing_sandbox_path_refreshes_tunnel_and_recovers_stale_state(self):
        source = (REPO_ROOT / "deploy" / "docker" / "scripts" / "nemoclaw" / "init_nemoclaw.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("ensure_nemoclaw_tunnel", source)
        self.assertIn("NEMOCLAW_EXISTING_SANDBOX_READY_TIMEOUT", source)
        self.assertIn("rerunning NemoClaw setup", source)
        self.assertIn("onboard_or_install_sandbox", source)

    def test_onboard_preflights_openshell_gateway_firewall(self):
        source = (REPO_ROOT / "deploy" / "docker" / "scripts" / "nemoclaw" / "init_nemoclaw.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("allow_openshell_gateway_bridge", source)
        self.assertIn("NEMOCLAW_CONFIGURE_UFW_GATEWAY", source)
        self.assertIn("sudo -n ufw allow from", source)
        self.assertLess(
            source.index("allow_openshell_gateway_bridge", source.index("onboard_or_install_sandbox()")),
            source.index("run_onboard", source.index("onboard_or_install_sandbox()")),
        )

    def test_gateway_restart_explicitly_starts_openclaw_dashboard(self):
        source = (REPO_ROOT / "deploy" / "docker" / "scripts" / "nemoclaw" / "init_nemoclaw.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("NEMOCLAW_GATEWAY_READY_TIMEOUT", source)
        self.assertIn("systemctl --user restart openclaw-gateway", source)
        self.assertIn("nohup openclaw dashboard", source)
        self.assertIn("/tmp/openclaw-dashboard.log", source)
        self.assertIn("dump_openclaw_gateway_diagnostics", source)


class UpdateOpenClawConfigTest(unittest.TestCase):
    def test_registers_sse_mcp_server(self):
        data: dict = {}

        changed = update_openclaw_config.update_mcp_server(
            data,
            name="vss_orchestrator",
            url="http://host.openshell.internal:9989/sse",
            server_type="sse",
        )

        self.assertTrue(changed)
        self.assertEqual(
            data["mcp"]["servers"]["vss_orchestrator"],
            {"type": "sse", "url": "http://host.openshell.internal:9989/sse"},
        )

    def test_write_remote_file_avoids_stdin_streaming(self):
        calls = []
        previous = update_openclaw_config.sandbox_exec

        def fake_sandbox_exec(sandbox_name, remote_args, capture_output=False, input_text=None):
            calls.append(
                {
                    "sandbox_name": sandbox_name,
                    "remote_args": remote_args,
                    "capture_output": capture_output,
                    "input_text": input_text,
                }
            )
            return subprocess.CompletedProcess(remote_args, 0, stdout="", stderr="")

        update_openclaw_config.sandbox_exec = fake_sandbox_exec
        try:
            update_openclaw_config.write_remote_file(
                "demo",
                "/sandbox/.openclaw/openclaw.json",
                '{"mcp":{"servers":{}}}\n',
            )
        finally:
            update_openclaw_config.sandbox_exec = previous

        self.assertGreaterEqual(len(calls), 2)
        self.assertIsNone(calls[0]["input_text"])
        self.assertEqual(calls[0]["remote_args"][0], "python3")
        self.assertEqual(calls[0]["remote_args"][1], "-c")
        self.assertEqual(calls[0]["remote_args"][3], "/sandbox/.openclaw/openclaw.json.tmp")
        self.assertNotIn("\n", calls[0]["remote_args"][-1])


class OrchestratorMcpHelperCompatTest(unittest.TestCase):
    def test_orchestrator_tool_is_string_enum_on_eval_workers(self):
        self.assertIsInstance(orchestrator_mcp_helper.OrchestratorTool.PROFILES, str)
        source = (REPO_ROOT / "deploy" / "docker" / "scripts" / "orchestrator_mcp_helper.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("except ImportError", source)


class DeployProfileNemoClawAdapterTest(unittest.TestCase):
    def test_evals_dir_and_nemoclaw_metadata_are_supported(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            skill_dir = root / "skills" / "vss-deploy-profile"
            (skill_dir / "evals").mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# skill\n", encoding="utf-8")
            (skill_dir / "evals" / "base.json").write_text(
                json.dumps(
                    {
                        "skills": ["vss-deploy-profile"],
                        "runner": "nemoclaw",
                        "requires_mcp": True,
                        "resources": {"platforms": {"L40S": {"gpu_count": 1}}},
                        "env": "env",
                        "expects": [{"query": "deploy base", "checks": ["ok"]}],
                    }
                ),
                encoding="utf-8",
            )
            out = root / "datasets"

            matrix, skipped = deploy_adapter.expand_matrix("base", "L40S", skill_dir=skill_dir)
            self.assertEqual(skipped, [])
            self.assertEqual(matrix, [("base", "L40S", 1)])

            deploy_adapter.generate_task(
                "base",
                "L40S",
                deploy_adapter.PROFILES["base"],
                out,
                skill_dir,
                gpu_count=1,
            )

            task_dir = out / "base" / "l40s"
            task_toml = (task_dir / "task.toml").read_text(encoding="utf-8")
            instruction = (task_dir / "instruction.md").read_text(encoding="utf-8")

            self.assertIn('runner = "nemoclaw"', task_toml)
            self.assertIn('requires_mcp = true', task_toml)
            self.assertIn('vss_orchestrator__docker_up', task_toml)
            self.assertIn("headless_runner.py", instruction)
            self.assertIn("--log-dir /logs/artifacts/nemoclaw", instruction)
            self.assertIn("--launch-mode cli", instruction)
            self.assertIn("--timeout 2400", instruction)
            self.assertIn("--wait-profile base", instruction)
            self.assertTrue((task_dir / "tests" / "nemoclaw_prompt.md").exists())

    def test_missing_eval_spec_does_not_generate_nemoclaw_launcher(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            skill_dir = root / "skills" / "vss-deploy-profile"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# skill\n", encoding="utf-8")
            out = root / "datasets"
            previous = os.environ.get("SKILLS_EVAL_RUNNER")
            os.environ["SKILLS_EVAL_RUNNER"] = "nemoclaw"
            try:
                deploy_adapter.generate_task(
                    "base",
                    "L40S",
                    deploy_adapter.PROFILES["base"],
                    out,
                    skill_dir,
                    gpu_count=1,
                )
            finally:
                if previous is None:
                    os.environ.pop("SKILLS_EVAL_RUNNER", None)
                else:
                    os.environ["SKILLS_EVAL_RUNNER"] = previous

            task_dir = out / "base" / "l40s"
            instruction = (task_dir / "instruction.md").read_text(encoding="utf-8")
            test_script = (task_dir / "tests" / "test.sh").read_text(encoding="utf-8")
            task_toml = (task_dir / "task.toml").read_text(encoding="utf-8")
            prompt_exists = (task_dir / "tests" / "nemoclaw_prompt.md").exists()

        self.assertNotIn("headless_runner.py", instruction)
        self.assertFalse(prompt_exists)
        self.assertIn("FAIL: no eval spec", test_script)
        self.assertNotIn('runner = "nemoclaw"', task_toml)


class SkillsEvalWorkflowTimeoutTest(unittest.TestCase):
    def test_nemoclaw_workflow_exports_bounded_timeouts(self):
        source = (REPO_ROOT / ".github" / "workflows" / "skills-eval.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("max-parallel: 2", source)
        self.assertIn("timeout-minutes: 90", source)
        self.assertIn("export NEMOCLAW_LOCK_TIMEOUT_SEC=900", source)
        self.assertIn("export NEMOCLAW_HARBOR_TIMEOUT_SEC=3300", source)
        self.assertIn("export NEMOCLAW_REMOTE_SETUP_TIMEOUT_SEC=1500", source)
        self.assertIn("export NEMOCLAW_SETUP_TIMEOUT_SEC=1620", source)
        self.assertIn("export NEMOCLAW_SETUP_CELL_TIMEOUT=900", source)
        self.assertIn("export NEMOCLAW_AGENT_TIMEOUT_SEC=1200", source)


if __name__ == "__main__":
    unittest.main()
