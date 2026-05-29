#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
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
readiness = load_module(
    "nemoclaw_readiness",
    REPO_ROOT / ".github" / "skill-eval" / "nemoclaw" / "readiness.py",
)


class NotebookSetupAdapterTest(unittest.TestCase):
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
        previous = {key: os.environ.get(key) for key in ("LLM_REMOTE_URL", "LLM_REMOTE_MODEL", "NVIDIA_API_KEY")}
        os.environ["LLM_REMOTE_URL"] = "https://inference-api.example/v1"
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
        self.assertIn("return { stream: true };", updated)


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
            self.assertTrue((task_dir / "tests" / "nemoclaw_prompt.md").exists())


if __name__ == "__main__":
    unittest.main()
