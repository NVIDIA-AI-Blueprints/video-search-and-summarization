#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import ast
import base64
import builtins
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path
from unittest import mock

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
readiness = load_module(
    "nemoclaw_readiness",
    REPO_ROOT / ".github" / "skill-eval" / "nemoclaw" / "readiness.py",
)
nemoclaw_deploy_profile_verifier = load_module(
    "nemoclaw_deploy_profile_verifier",
    REPO_ROOT / ".github" / "skill-eval" / "verifiers" / "nemoclaw_deploy_profile.py",
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
    def test_ci_confirms_only_the_managed_sandbox_it_recreates(self):
        with mock.patch.dict(
            os.environ,
            {
                "NEMOCLAW_SANDBOX_NAME": "demo",
                "NEMOCLAW_RECREATE_SANDBOX": "1",
            },
            clear=True,
        ):
            notebook_adapter._prepare_ci_nemoclaw_environment()

            self.assertEqual(
                os.environ["NEMOCLAW_CONFIRM_LEGACY_MANAGED_RECREATE"],
                '["demo"]',
            )

    def test_ci_does_not_confirm_legacy_migration_without_recreation(self):
        with mock.patch.dict(
            os.environ,
            {
                "NEMOCLAW_SANDBOX_NAME": "demo",
                "NEMOCLAW_RECREATE_SANDBOX": "0",
            },
            clear=True,
        ):
            notebook_adapter._prepare_ci_nemoclaw_environment()

            self.assertNotIn(
                "NEMOCLAW_CONFIRM_LEGACY_MANAGED_RECREATE",
                os.environ,
            )

    def test_ci_preserves_explicit_legacy_migration_confirmation(self):
        with mock.patch.dict(
            os.environ,
            {
                "NEMOCLAW_SANDBOX_NAME": "demo",
                "NEMOCLAW_RECREATE_SANDBOX": "1",
                "NEMOCLAW_CONFIRM_LEGACY_MANAGED_RECREATE": '["demo","other"]',
            },
            clear=True,
        ):
            notebook_adapter._prepare_ci_nemoclaw_environment()

            self.assertEqual(
                os.environ["NEMOCLAW_CONFIRM_LEGACY_MANAGED_RECREATE"],
                '["demo","other"]',
            )

    def test_sidecar_manifest_matches_current_notebook_cells(self):
        manifest_path = REPO_ROOT / ".github" / "skill-eval" / "nemoclaw" / "notebook_cells.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sources = [
            json.loads((REPO_ROOT / item["notebook"]).read_text(encoding="utf-8"))
            for item in manifest["notebooks"]
        ]

        built = notebook_adapter.build_notebooks(sources, manifest)

        ids = [cell.get("id") for cell in built["cells"]]
        self.assertEqual(ids.count("ci-parameters-1"), 1)
        self.assertEqual(ids.count("ci-parameters-2"), 1)
        self.assertIn("ci-persist-env", ids)
        self.assertNotIn("s37-ui-code", ids)
        self.assertNotIn("verify-code", ids)
        self.assertLess(ids.index("ci-parameters-1"), ids.index("e67f6da4"))

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

    def test_ci_notebook_makes_docker_login_best_effort(self):
        source = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [
                {
                    "id": "4c91fd59",
                    "cell_type": "code",
                    "metadata": {},
                    "source": [
                        'if login_result.returncode != 0:\n',
                        '    raise RuntimeError(f"Docker login to nvcr.io failed\\n{login_result.stderr}")\n',
                        '\n',
                        'print("Docker login to nvcr.io: OK")\n',
                    ],
                    "outputs": [],
                }
            ],
        }
        manifest = {"cells": ["4c91fd59"], "insert_parameters_before": "4c91fd59"}

        built = notebook_adapter.build_notebook(source, manifest)
        login_cell = next(cell for cell in built["cells"] if cell.get("id") == "4c91fd59")

        self.assertIn("WARNING: Docker login to nvcr.io failed; continuing in CI", login_cell["source"])
        self.assertNotIn("raise RuntimeError", login_cell["source"])
        self.assertIn("else:", login_cell["source"])

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
        self.assertEqual(defaults["VSS_ORCHESTRATOR_MCP_TYPE"], "streamable-http")
        self.assertEqual(defaults["VSS_ORCHESTRATOR_MCP_URL"], "http://host.openshell.internal:9988/mcp")
        self.assertEqual(defaults["MCP_URL"], "http://127.0.0.1:9988/mcp")

    def test_parameter_cell_accepts_ngc_api_key_alias(self):
        defaults = {
            "HARDWARE_PROFILE": "RTXPRO6000BW",
            "NEMOCLAW_ENDPOINT_URL": "",
            "NEMOCLAW_MODEL": "",
            "COMPATIBLE_API_KEY": "",
        }
        env_keys = ("NGC_CLI_API_KEY", "NGC_API_KEY")
        previous = {key: os.environ.get(key) for key in env_keys}
        for key in env_keys:
            os.environ.pop(key, None)
        os.environ["NGC_API_KEY"] = "ngc-alias"
        try:
            exec(notebook_adapter.PARAMETER_SOURCE, defaults)
            self.assertEqual(os.environ.get("NGC_CLI_API_KEY"), "ngc-alias")
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual(defaults["NGC_CLI_API_KEY"], "ngc-alias")

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
        self.assertEqual(defaults["VSS_ORCHESTRATOR_MCP_TYPE"], "streamable-http")
        self.assertEqual(
            defaults["VSS_ORCHESTRATOR_MCP_URL"],
            "http://host.openshell.internal:9988/mcp",
        )
        self.assertEqual(defaults["MCP_URL"], "http://127.0.0.1:9988/mcp")

    def test_vss_notebook_leaves_gpu_placement_to_profile_defaults(self):
        notebook = json.loads(
            (REPO_ROOT / "deploy" / "docker" / "scripts" / "deploy_vss_orchestrator.ipynb").read_text()
        )
        settings_cells = [cell for cell in notebook["cells"] if cell.get("id") == "20b35654"]
        self.assertEqual(len(settings_cells), 1)
        source = "".join(settings_cells[0].get("source", ""))

        self.assertIn('LLM_DEVICE_ID = ""', source)
        self.assertIn('VLM_DEVICE_ID = ""', source)
        self.assertNotIn('LLM_DEVICE_ID = "0"', source)
        self.assertNotIn('VLM_DEVICE_ID = "1"', source)

    def test_parameter_cell_preserves_blank_notebook_gpu_defaults(self):
        defaults = {
            "HARDWARE_PROFILE": "RTXPRO6000BW",
            "NEMOCLAW_ENDPOINT_URL": "",
            "NEMOCLAW_MODEL": "",
            "COMPATIBLE_API_KEY": "",
            "LLM_DEVICE_ID": "",
            "VLM_DEVICE_ID": "",
        }

        with mock.patch.dict(os.environ, {}, clear=True):
            exec(notebook_adapter.PARAMETER_SOURCE, defaults)

        self.assertEqual(defaults["LLM_DEVICE_ID"], "")
        self.assertEqual(defaults["VLM_DEVICE_ID"], "")

    def test_parameter_cell_honors_explicit_ci_gpu_placement(self):
        defaults = {
            "HARDWARE_PROFILE": "RTXPRO6000BW",
            "NEMOCLAW_ENDPOINT_URL": "",
            "NEMOCLAW_MODEL": "",
            "COMPATIBLE_API_KEY": "",
            "LLM_DEVICE_ID": "",
            "VLM_DEVICE_ID": "",
        }

        with mock.patch.dict(
            os.environ,
            {"LLM_DEVICE_ID": "2", "VLM_DEVICE_ID": "3"},
            clear=True,
        ):
            exec(notebook_adapter.PARAMETER_SOURCE, defaults)

        self.assertEqual(defaults["LLM_DEVICE_ID"], "2")
        self.assertEqual(defaults["VLM_DEVICE_ID"], "3")

    def test_agent_setup_cell_compiles_from_split_vss_notebook(self):
        notebook = json.loads(
            (REPO_ROOT / "deploy" / "docker" / "scripts" / "deploy_vss_orchestrator.ipynb").read_text()
        )
        setup_cells = [cell for cell in notebook["cells"] if cell.get("id") == "c13aaf5e"]
        self.assertEqual(len(setup_cells), 1)
        source = "".join(setup_cells[0].get("source", ""))

        self.assertIn("run_uv_sync", source)
        self.assertIn('"uv", "sync", "--no-dev", "--extra", "agent"', source)
        self.assertIn("ensure_agent_venv", source)
        self.assertIn('command.append("--clear")', source)
        self.assertIn('if "--force" in uv_venv_help.stdout', source)
        self.assertNotIn('command.extend(["--clear", "--force"])', source)
        self.assertIn("Refusing to replace symlinked orchestrator environment", source)
        compile(source, "deploy_vss_orchestrator.ipynb:c13aaf5e", "exec")

        tree = ast.parse(source)
        ensure_venv_nodes = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {"uv_env_for_agent", "ensure_agent_venv"}
        ]
        self.assertEqual(
            [node.name for node in ensure_venv_nodes],
            ["uv_env_for_agent", "ensure_agent_venv"],
        )
        ensure_venv_module = ast.fix_missing_locations(
            ast.Module(body=ensure_venv_nodes, type_ignores=[])
        )

        with tempfile.TemporaryDirectory() as tempdir:
            agent_dir = Path(tempdir) / "services" / "agent"
            venv_dir = agent_dir / ".venv"
            venv_dir.mkdir(parents=True)
            commands: list[tuple[list[str], dict[str, object]]] = []
            supports_force = {"value": False}

            def fake_run(command, **kwargs):
                commands.append((command, kwargs))
                if command == ["uv", "venv", "--help"]:
                    stdout = "      --force\\n" if supports_force["value"] else ""
                    return subprocess.CompletedProcess(command, 0, stdout=stdout)
                venv_python = venv_dir / "bin" / "python"
                venv_python.parent.mkdir(parents=True, exist_ok=True)
                venv_python.write_text("#!/bin/sh\n")
                venv_python.chmod(0o755)
                return subprocess.CompletedProcess(command, 0)

            namespace = {
                "AGENT_DIR": agent_dir,
                "ORCHESTRATOR_MCP_VENV_DIR": venv_dir,
                "ORCHESTRATOR_MCP_PYTHON_VERSION": "3.10",
                "os": os,
                "subprocess": mock.Mock(run=fake_run),
            }
            exec(
                compile(
                    ensure_venv_module,
                    "deploy_vss_orchestrator.ipynb:c13aaf5e:ensure_agent_venv",
                    "exec",
                ),
                namespace,
            )
            ensure_agent_venv = namespace["ensure_agent_venv"]

            with mock.patch.dict(
                os.environ,
                {
                    "VIRTUAL_ENV": "/outside/kernel-venv",
                    "UV_PROJECT_ENVIRONMENT": "/outside/project-venv",
                },
            ):
                ensure_agent_venv()
            self.assertEqual(len(commands), 2)
            help_command, help_kwargs = commands[0]
            self.assertEqual(help_command, ["uv", "venv", "--help"])
            self.assertEqual(help_kwargs["cwd"], str(agent_dir))
            self.assertTrue(help_kwargs["check"])
            self.assertTrue(help_kwargs["capture_output"])
            self.assertTrue(help_kwargs["text"])
            self.assertNotIn("VIRTUAL_ENV", help_kwargs["env"])
            self.assertNotIn("UV_PROJECT_ENVIRONMENT", help_kwargs["env"])

            command, kwargs = commands[1]
            self.assertEqual(
                command,
                [
                    "uv",
                    "venv",
                    "--clear",
                    "--python",
                    "3.10",
                    str(venv_dir),
                ],
            )
            self.assertEqual(kwargs["cwd"], str(agent_dir))
            self.assertTrue(kwargs["check"])
            self.assertNotIn("VIRTUAL_ENV", kwargs["env"])
            self.assertNotIn("UV_PROJECT_ENVIRONMENT", kwargs["env"])

            ensure_agent_venv()
            self.assertEqual(len(commands), 2)

            (venv_dir / "bin" / "python").unlink()
            supports_force["value"] = True
            ensure_agent_venv()
            self.assertEqual(len(commands), 4)
            self.assertEqual(commands[2][0], ["uv", "venv", "--help"])
            self.assertEqual(
                commands[3][0],
                [
                    "uv",
                    "venv",
                    "--clear",
                    "--force",
                    "--python",
                    "3.10",
                    str(venv_dir),
                ],
            )

            target_dir = agent_dir / "target-venv"
            target_python = target_dir / "bin" / "python"
            target_python.parent.mkdir(parents=True)
            target_python.write_text("#!/bin/sh\n")
            target_python.chmod(0o755)
            symlink_dir = agent_dir / "symlink-venv"
            symlink_dir.symlink_to(target_dir, target_is_directory=True)
            namespace["ORCHESTRATOR_MCP_VENV_DIR"] = symlink_dir
            with self.assertRaisesRegex(
                RuntimeError, "Refusing to replace symlinked orchestrator environment"
            ):
                ensure_agent_venv()
            self.assertEqual(len(commands), 4)

    def test_ci_parameters_drive_nemoclaw_provider_derivation(self):
        notebook = json.loads(
            (REPO_ROOT / "deploy" / "docker" / "scripts" / "deploy_nemoclaw.ipynb").read_text()
        )
        sources = {
            cell.get("id"): "".join(cell.get("source", ""))
            for cell in notebook["cells"]
        }
        namespace: dict[str, object] = {}
        ci_env = {
            "LLM_REMOTE_URL": "https://inference-api.example",
            "LLM_REMOTE_MODEL": "nvidia/example-model",
            "NVIDIA_API_KEY": "nvapi-ci",
            "VSS_REPO_DIR": str(REPO_ROOT),
        }

        with (
            mock.patch.dict(os.environ, ci_env, clear=True),
            mock.patch("subprocess.check_output", return_value="hooks-token\n"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            exec(sources["994c77c2"], namespace)
            exec(sources["47d20bb1"], namespace)
            exec(notebook_adapter.PARAMETER_SOURCE, namespace)
            exec(sources["e67f6da4"], namespace)

        self.assertEqual(namespace["NEMOCLAW_PROVIDER"], "custom")
        self.assertEqual(namespace["NEMOCLAW_ENDPOINT_URL"], "https://inference-api.example/v1")
        self.assertEqual(namespace["NEMOCLAW_MODEL"], "nvidia/example-model")
        self.assertEqual(namespace["COMPATIBLE_API_KEY"], "nvapi-ci")

    def test_split_notebooks_use_refactored_brev_util_path(self):
        expected_parts = (
            '"packages"',
            '"vss_agents"',
            '"src"',
            '"vss_agents"',
            '"orchestrator"',
            '"brev_util.py"',
        )
        for notebook_name, cell_id in (
            ("deploy_nemoclaw.ipynb", "e67f6da4"),
            ("deploy_vss_orchestrator.ipynb", "20b35654"),
        ):
            notebook = json.loads(
                (REPO_ROOT / "deploy" / "docker" / "scripts" / notebook_name).read_text()
            )
            cells = [cell for cell in notebook["cells"] if cell.get("id") == cell_id]
            self.assertEqual(len(cells), 1)
            source = "".join(cells[0].get("source", ""))
            brev_util_line = next(
                line for line in source.splitlines() if line.startswith("BREV_UTIL_PATH = ")
            )
            for part in expected_parts:
                self.assertIn(part, brev_util_line)
            self.assertNotIn('/ "agent" / "orchestrator"', brev_util_line)

    def test_composed_notebook_separates_host_and_sandbox_mcp_urls(self):
        manifest = json.loads(
            (
                REPO_ROOT
                / ".github"
                / "skill-eval"
                / "nemoclaw"
                / "notebook_cells.json"
            ).read_text()
        )
        notebooks = [
            json.loads((REPO_ROOT / item["notebook"]).read_text())
            for item in manifest["notebooks"]
        ]
        built = notebook_adapter.build_notebooks(notebooks, manifest)
        sources = {
            cell.get("id"): "".join(cell.get("source", ""))
            for cell in built["cells"]
        }

        self.assertIn('"mcporter", "config", "add", "vss_orchestrator"', sources["s36-code"])
        self.assertIn('"--url", VSS_ORCHESTRATOR_MCP_URL', sources["s36-code"])
        self.assertIn('"--scope", "home"', sources["s36-code"])
        self.assertIn(
            '"mcporter", "config", "get", "vss_orchestrator", "--json"',
            sources["s36-code"],
        )
        self.assertNotIn("!nemoclaw sandbox mcp", sources["s36-code"])
        self.assertIn("NEMOCLAW_RECREATE_SANDBOX", sources["s31-code"])
        self.assertIn("if _exit_code != 0 or _recreate_sandbox:", sources["s31-code"])
        self.assertIn(
            "check_mcp_health(MCP_URL, AGENT_DIR)",
            sources["042eabd1"],
        )
        self.assertIn(
            'MCP_URL = f"http://127.0.0.1:{MCP_PORT}/mcp"',
            sources["20b35654"],
        )

    def test_policy_allows_supported_docker_bridge_ranges_for_host_routes(self):
        policy = (
            REPO_ROOT / "assets" / "vss_nemoclaw_policy.yaml"
        ).read_text(encoding="utf-8")
        host_route_count = policy.count("host: host.openshell.internal")

        self.assertGreater(host_route_count, 0)
        self.assertEqual(policy.count("- 172.19.0.0/16"), host_route_count)

    def test_brev_util_imports_without_stdlib_strenum(self):
        path = (
            REPO_ROOT
            / "services"
            / "agent"
            / "packages"
            / "vss_agents"
            / "src"
            / "vss_agents"
            / "orchestrator"
            / "brev_util.py"
        )
        spec = importlib.util.spec_from_file_location("brev_util_py310_compat", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        original_import = builtins.__import__

        def import_without_strenum(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "enum" and "StrEnum" in fromlist:
                raise ImportError("simulated Python 3.10 enum")
            return original_import(name, globals, locals, fromlist, level)

        with mock.patch("builtins.__import__", side_effect=import_without_strenum):
            spec.loader.exec_module(module)

        self.assertEqual(module.BrevEnvKey.BREV_ENV_ID.value, "BREV_ENV_ID")


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

    def test_readiness_requires_gateway_health_inside_sandbox(self):
        calls: list[tuple[str, ...]] = []

        def fake_run(cmd, *, timeout=30, cwd=None):
            calls.append(tuple(cmd))
            if "exec" in cmd:
                return subprocess.CompletedProcess(
                    cmd,
                    7,
                    stdout="",
                    stderr="curl: failed to connect",
                )
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="sandbox exists",
                stderr="",
            )

        with (
            mock.patch.object(readiness.shutil, "which", return_value="/usr/bin/openshell"),
            mock.patch.object(readiness, "_run", side_effect=fake_run),
        ):
            report = readiness._check_sandbox("demo")

        self.assertFalse(report["ok"])
        self.assertFalse(report["gateway_ok"])
        self.assertIn("failed to connect", report["gateway_stderr_tail"])
        self.assertEqual(
            calls[1],
            (
                "openshell",
                "sandbox",
                "exec",
                "-n",
                "demo",
                "--",
                "sh",
                "-lc",
                "curl -fsS http://127.0.0.1:18789/health >/dev/null",
            ),
        )

    def test_readiness_accepts_healthy_gateway_inside_sandbox(self):
        responses = [
            subprocess.CompletedProcess(
                ["openshell", "sandbox", "get", "demo"],
                0,
                stdout="sandbox exists",
                stderr="",
            ),
            subprocess.CompletedProcess(
                ["openshell", "sandbox", "exec", "demo"],
                0,
                stdout="",
                stderr="",
            ),
        ]
        with (
            mock.patch.object(readiness.shutil, "which", return_value="/usr/bin/openshell"),
            mock.patch.object(readiness, "_run", side_effect=responses) as run,
        ):
            report = readiness._check_sandbox("demo")

        self.assertTrue(report["ok"])
        self.assertTrue(report["gateway_ok"])
        self.assertEqual(run.call_count, 2)

    def test_readiness_skips_gateway_probe_when_sandbox_lookup_fails(self):
        with (
            mock.patch.object(readiness.shutil, "which", return_value="/usr/bin/openshell"),
            mock.patch.object(
                readiness,
                "_run",
                return_value=subprocess.CompletedProcess(
                    ["openshell", "sandbox", "get", "demo"],
                    1,
                    stdout="",
                    stderr="sandbox not found",
                ),
            ) as run,
        ):
            report = readiness._check_sandbox("demo")

        self.assertFalse(report["ok"])
        self.assertFalse(report["gateway_ok"])
        run.assert_called_once()

    def test_readiness_reports_gateway_probe_timeout(self):
        sandbox_result = subprocess.CompletedProcess(
            ["openshell", "sandbox", "get", "demo"],
            0,
            stdout="sandbox exists",
            stderr="",
        )
        with (
            mock.patch.object(readiness.shutil, "which", return_value="/usr/bin/openshell"),
            mock.patch.object(
                readiness,
                "_run",
                side_effect=[
                    sandbox_result,
                    subprocess.TimeoutExpired(["openshell"], 30),
                ],
            ),
        ):
            report = readiness._check_sandbox("demo")

        self.assertFalse(report["ok"])
        self.assertFalse(report["gateway_ok"])
        self.assertIn("timed out after 30s", report["gateway_stderr_tail"])


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

    def test_expected_skill_rejects_stale_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            prompt = root / "prompt.md"
            prompt.write_text(
                "Use the `/vss-generate-video-report` skill for this task.",
                encoding="utf-8",
            )

            with contextlib.redirect_stdout(io.StringIO()):
                rc = headless_runner.main([
                    "--prompt-file",
                    str(prompt),
                    "--log-dir",
                    str(log_dir),
                    "--expected-skill",
                    "vss-deploy-dense-captioning",
                    "--launch-mode",
                    "cli",
                ])

            report = json.loads((log_dir / "nemoclaw_hooks_response.json").read_text(encoding="utf-8"))

        self.assertEqual(rc, 1)
        self.assertEqual(report["response"]["error_type"], "RuntimeError")
        self.assertIn("does not reference expected skill", report["response"]["error"])

    def test_cli_launch_runs_openclaw_agent_inside_sandbox(self):
        calls: list[tuple[str, ...]] = []
        previous = {
            "_run": headless_runner._run,
            "_gateway_reachable": headless_runner._gateway_reachable,
        }

        def fake_run(cmd, *, timeout=30):
            call_index = len(calls)
            calls.append(tuple(cmd))
            if call_index in {2, 4}:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout='{"result":{"payloads":[{"text":"done"}]}}',
                    stderr="",
                )
            if call_index == 1:
                return subprocess.CompletedProcess(cmd, 0, stdout="stopped\n", stderr="")
            if call_index == 3:
                return subprocess.CompletedProcess(cmd, 0, stdout="0\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="started", stderr="")

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
        self.assertIn("mode=blocking-poll", launch_log)
        self.assertIn("completed=true", launch_log)
        wrapper = next(call[-1] for call in calls if "base64 -d" in " ".join(call))
        encoded = wrapper.split("printf %s ", 1)[1].split(" | base64 -d", 1)[0].strip("'")
        script = base64.b64decode(encoded).decode("utf-8")
        self.assertIn("echo started", script)
        self.assertIn("openclaw-agent.rc", script)
        self.assertIn("openclaw-agent.rc.tmp", script)
        self.assertIn("mv /tmp/vss-skill-eval-openclaw/openclaw-agent.rc.tmp", script)
        self.assertNotIn("while kill -0", script)
        self.assertIn("--message", script)
        self.assertNotIn("--local", script)
        self.assertIn("--json", script)
        self.assertIn("--thinking off", script)
        self.assertNotIn("--thinking medium", script)
        self.assertIn("OPENCLAW_DISABLE_STREAMING_TOOL_CALLS=1", script)
        self.assertIn("NO_PROXY=localhost,127.0.0.1,::1,10.200.0.1", script)
        no_proxy_exports = [
            part
            for part in script.split("; ")
            if part.startswith(("export NO_PROXY=", "export no_proxy="))
        ]
        self.assertEqual(len(no_proxy_exports), 2)
        self.assertTrue(all("host.openshell.internal" not in part for part in no_proxy_exports))

    def test_openclaw_completion_accepts_v0080_json_envelopes(self):
        fixtures = (
            '{"result":{"payloads":[{"text":"done"}]}}',
            '{"payloads":[{"text":"done"}]}',
            'openclaw info\\n{"result":{"payloads":[{"text":"done"}]}}\\n',
        )
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            for fixture in fixtures:
                (log_dir / "openclaw-agent.log").write_text(fixture, encoding="utf-8")
                self.assertTrue(headless_runner._openclaw_log_completed(log_dir))

            (log_dir / "openclaw-agent.log").write_text(
                '{"result":{"payloads":[]}}',
                encoding="utf-8",
            )
            self.assertFalse(headless_runner._openclaw_log_completed(log_dir))

            for fixture in (
                '{"status":"error","payloads":[{"text":"not complete"}]}',
                '{"result":{"error":"boom","payloads":[{"text":"not complete"}]}}',
                '{"payloads":[{"isError":true,"text":"not complete"}]}',
            ):
                (log_dir / "openclaw-agent.log").write_text(fixture, encoding="utf-8")
                self.assertFalse(headless_runner._openclaw_log_completed(log_dir))

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

    def test_stop_openclaw_cli_allows_log_flush_before_force_kill(self):
        calls: list[tuple[str, ...]] = []
        previous = headless_runner._run

        def fake_run(cmd, *, timeout=30):
            calls.append(tuple(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        headless_runner._run = fake_run
        try:
            headless_runner.stop_openclaw_cli("demo")
        finally:
            headless_runner._run = previous

        wrapper = next(call[-1] for call in calls if "base64 -d" in " ".join(call))
        encoded = wrapper.split("printf %s ", 1)[1].split(" | base64 -d", 1)[0].strip("'")
        script = base64.b64decode(encoded).decode("utf-8")
        self.assertIn("sleep 5", script)
        self.assertIn("kill -9", script)

    def test_cli_launch_returns_after_start_when_waiting_for_profile(self):
        calls: list[tuple[str, ...]] = []
        previous = {
            "_run": headless_runner._run,
            "_gateway_reachable": headless_runner._gateway_reachable,
            "NEMOCLAW_FAST_READINESS_MODE": os.environ.get("NEMOCLAW_FAST_READINESS_MODE"),
        }

        def fake_run(cmd, *, timeout=30):
            calls.append(tuple(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="started", stderr="")

        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            headless_runner._run = fake_run
            headless_runner._gateway_reachable = lambda sandbox: True
            os.environ["NEMOCLAW_FAST_READINESS_MODE"] = "1"
            try:
                response = headless_runner.run_openclaw_cli(
                    "demo",
                    "Deploy base",
                    30,
                    log_dir,
                    wait_profile="base",
                )
            finally:
                headless_runner._run = previous["_run"]
                headless_runner._gateway_reachable = previous["_gateway_reachable"]
                if previous["NEMOCLAW_FAST_READINESS_MODE"] is None:
                    os.environ.pop("NEMOCLAW_FAST_READINESS_MODE", None)
                else:
                    os.environ["NEMOCLAW_FAST_READINESS_MODE"] = previous["NEMOCLAW_FAST_READINESS_MODE"]

            launch_log = (log_dir / "openclaw-launch.log").read_text(encoding="utf-8")

        self.assertEqual(response["status"], 200)
        self.assertEqual(response["body"]["mode"], "cli-async")
        self.assertIn("mode=async", launch_log)
        wrapper = next(call[-1] for call in calls if "base64 -d" in " ".join(call))
        encoded = wrapper.split("printf %s ", 1)[1].split(" | base64 -d", 1)[0].strip("'")
        script = base64.b64decode(encoded).decode("utf-8")
        self.assertIn("echo started", script)
        self.assertNotIn("while kill -0", script)

    def test_wait_for_lvs_profile_requires_lvs_ready_endpoint(self):
        previous = {
            "_run": headless_runner._run,
            "sleep": headless_runner.time.sleep,
            "time": headless_runner.time.time,
        }
        calls: list[list[str]] = []
        now = iter([0, 1, 2, 3, 4, 61])

        def fake_run(cmd, *, timeout=30):
            calls.append(cmd)
            if "38111/v1/ready" in " ".join(cmd):
                return subprocess.CompletedProcess(cmd, 7, stdout="", stderr="connection refused")
            if cmd[:2] == ["docker", "ps"]:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout="vss-agent\nvss-agent-ui\nredis\nvss-lvs\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as td:
            headless_runner._run = fake_run
            headless_runner.time.sleep = lambda seconds: None
            headless_runner.time.time = lambda: next(now)
            try:
                report = headless_runner.wait_for_profile("lvs", 60, Path(td))
            finally:
                headless_runner._run = previous["_run"]
                headless_runner.time.sleep = previous["sleep"]
                headless_runner.time.time = previous["time"]

        self.assertTrue(report["waited"])
        self.assertFalse(report["ok"])
        self.assertEqual(report["profile"], "lvs")
        self.assertIn("38111/v1/ready", report["message"])
        self.assertTrue(any("38111/v1/ready" in " ".join(call) for call in calls))

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

            headless_runner.run_openclaw_cli = (
                lambda sandbox, message, timeout, logs, wait_profile="": {
                "status": 202,
                "body": {"ok": True},
                }
            )
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
        self.assertEqual(calls, ["stop", "collect"])

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

    def test_gateway_recovery_uses_managed_nemoclaw_restart(self):
        calls: list[tuple[str, ...]] = []
        gateway_checks = iter([False, True])
        previous = {
            "_run": headless_runner._run,
            "_gateway_reachable": headless_runner._gateway_reachable,
        }

        def fake_run(cmd, *, timeout=30):
            calls.append(tuple(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            headless_runner._run = fake_run
            headless_runner._gateway_reachable = lambda sandbox: next(gateway_checks)
            try:
                headless_runner.ensure_openclaw_gateway("demo", log_dir)
            finally:
                headless_runner._run = previous["_run"]
                headless_runner._gateway_reachable = previous["_gateway_reachable"]

            recover_log = (log_dir / "openclaw_gateway_recover.log").read_text(encoding="utf-8")

        self.assertIn("managed restart", recover_log)
        self.assertIn("returncode=0", recover_log)
        self.assertEqual(
            calls,
            [("nemoclaw", "sandbox", "gateway", "restart", "demo")],
        )

    def test_gateway_probe_treats_exec_timeout_as_unreachable(self):
        previous = headless_runner._sandbox_exec

        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 20))

        headless_runner._sandbox_exec = timeout
        try:
            self.assertFalse(headless_runner._gateway_reachable("demo"))
        finally:
            headless_runner._sandbox_exec = previous

    def test_gateway_recovery_falls_back_to_sandbox_recover(self):
        calls: list[tuple[str, ...]] = []
        gateway_checks = iter([False, True])
        previous = {
            "_run": headless_runner._run,
            "_gateway_reachable": headless_runner._gateway_reachable,
        }

        def fake_run(cmd, *, timeout=30):
            calls.append(tuple(cmd))
            returncode = 1 if "restart" in cmd else 0
            return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            headless_runner._run = fake_run
            headless_runner._gateway_reachable = lambda sandbox: next(gateway_checks)
            try:
                headless_runner.ensure_openclaw_gateway("demo", log_dir)
            finally:
                headless_runner._run = previous["_run"]
                headless_runner._gateway_reachable = previous["_gateway_reachable"]

            recover_log = (log_dir / "openclaw_gateway_recover.log").read_text(encoding="utf-8")

        self.assertIn("managed restart", recover_log)
        self.assertIn("sandbox recover", recover_log)
        self.assertEqual(
            calls,
            [
                ("nemoclaw", "sandbox", "gateway", "restart", "demo"),
                ("nemoclaw", "sandbox", "recover", "demo"),
            ],
        )


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

    def test_brev_json_parser_accepts_workspaces_object(self):
        raw = json.dumps(
            {
                "workspaces": [
                    {
                        "id": "instance-explicit",
                        "name": "vss-eval-rtx-1g-10",
                        "status": "RUNNING",
                        "gpu": "RTXPro6000",
                    }
                ]
            }
        )

        parsed = smoke_runner._parse_brev_json(raw)

        self.assertEqual(parsed[0]["id"], "instance-explicit")
        self.assertEqual(parsed[0]["name"], "vss-eval-rtx-1g-10")

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
        deploy_row = next(row for row in rows if row["skill"] == "vss-deploy-profile")
        self.assertEqual(deploy_row["spec_stem"], "base")
        self.assertNotIn("vss-deploy-detection-tracking-2d", skills)
        self.assertNotIn("vss-deploy-detection-tracking-3d", skills)
        self.assertNotIn("vss-deploy-video-embedding", skills)
        self.assertNotIn("vss-generate-video-calibration", skills)
        self.assertNotIn("vss-manage-video-io-storage", skills)
        self.assertNotIn("vss-search-archive", skills)
        self.assertIn("vss-setup-behavior-analytics", skills)
        self.assertNotIn("vss-setup-video-analytics-api", skills)
        self.assertNotIn("evals", [row["spec_stem"] for row in rows])
        behavior_row = next(
            row
            for row in rows
            if row["skill"] == "vss-setup-behavior-analytics"
        )
        self.assertEqual(behavior_row["spec_stem"], "deploy_search_and_alerts")
        self.assertEqual(behavior_row["platform"], "ANY")
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
        self.assertTrue(
            any(
                "vss-deploy-profile/alerts_cv.json: alerts CV mode requires real RT-CV model artifacts"
                in item
                for item in blockers
            )
        )

    def test_manual_single_skill_matrix_uses_representative_row_by_default(self):
        previous_env = {
            key: os.environ.pop(key, None)
            for key in (
                "MANUAL_SKILLS_FILTER",
                "NEMOCLAW_EVAL_SPEC",
                "NEMOCLAW_EVAL_PLATFORM",
                "NEMOCLAW_ALL_SPECS",
            )
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                rc = smoke_runner.main([
                    "--print-matrix",
                    "--skills",
                    "vss-manage-alerts",
                ])
        finally:
            for key, value in previous_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual(rc, 0)
        rows = json.loads(stdout.getvalue())["include"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["skill"], "vss-manage-alerts")
        self.assertEqual(rows[0]["spec_stem"], "alerts_vlm_real_time")
        self.assertEqual(rows[0]["platform"], "RTXPRO6000BW")
        self.assertEqual(rows[0]["task_limit"], "1")

    def test_alerts_cv_is_blocked_for_nemoclaw_unless_rt_cv_is_enabled(self):
        previous = os.environ.pop("NEMOCLAW_ENABLE_RTCV", None)
        try:
            rows, blockers = smoke_runner._build_matrix(
                skills_filter="vss-deploy-profile",
                profile_filter=None,
                platform_filter=None,
                spec_filter="alerts_cv",
                representative_per_skill=False,
            )

            self.assertEqual(rows, [])
            self.assertTrue(
                any("alerts CV mode requires real RT-CV model artifacts" in item for item in blockers)
            )

            os.environ["NEMOCLAW_ENABLE_RTCV"] = "1"
            rows, blockers = smoke_runner._build_matrix(
                skills_filter="vss-deploy-profile",
                profile_filter=None,
                platform_filter=None,
                spec_filter="alerts_cv",
                representative_per_skill=False,
            )

            self.assertEqual(blockers, [])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["spec_stem"], "alerts_cv")
        finally:
            if previous is None:
                os.environ.pop("NEMOCLAW_ENABLE_RTCV", None)
            else:
                os.environ["NEMOCLAW_ENABLE_RTCV"] = previous

    def test_standalone_host_docker_spec_is_blocked_while_live_specs_run(self):
        rows, blockers = smoke_runner._build_matrix(
            skills_filter="vss-setup-behavior-analytics",
            profile_filter=None,
            platform_filter=None,
            spec_filter=None,
            representative_per_skill=False,
        )

        self.assertEqual(
            {row["spec_stem"] for row in rows},
            {
                "deploy_search_and_alerts",
                "fov_count_alert",
                "proximity_alert",
                "roi_bbox_overlap",
            },
        )
        self.assertTrue(all(row["platform"] == "ANY" for row in rows))
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
        smoke_runner._reachable = lambda instance, exec_target=None: True
        smoke_runner._try_acquire_lock = (
            lambda instance, exec_target=None: None
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

    def test_worker_selection_skips_excluded_candidate(self):
        instances = [
            {"name": "vss-eval-rtx-1g-2", "status": "RUNNING", "gpu": "RTX PRO 6000"},
            {"name": "vss-eval-rtx-1g-3", "status": "RUNNING", "gpu": "RTX PRO 6000"},
        ]
        acquire_lock = mock.Mock(
            return_value=smoke_runner.WorkerLock(123, object(), None)
        )

        with (
            mock.patch.object(smoke_runner, "_list_instances", return_value=instances),
            mock.patch.object(smoke_runner, "_reachable", return_value=True),
            mock.patch.object(smoke_runner, "_try_acquire_lock", acquire_lock),
        ):
            selected, _lock = smoke_runner._select_and_lock_instance(
                "RTXPRO6000BW",
                1,
                None,
                10,
                excluded={"vss-eval-rtx-1g-2"},
            )

        self.assertEqual(selected, "vss-eval-rtx-1g-3")
        acquire_lock.assert_called_once_with(
            "vss-eval-rtx-1g-3",
            "vss-eval-rtx-1g-3",
        )

    def test_pre_agent_brev_environment_failure_is_retryable(self):
        result = {
            "config": {
                "environment": {
                    "import_path": "envs.brev_env:BrevEnvironment",
                }
            },
            "environment_setup": {
                "started_at": "2026-07-27T12:49:45Z",
                "finished_at": "2026-07-27T12:52:09Z",
            },
            "agent_setup": None,
            "agent_execution": None,
            "verifier": None,
            "agent_result": None,
            "verifier_result": None,
            "exception_info": {
                "exception_type": "RuntimeError",
                "exception_message": (
                    "NemoClaw setup failed on vss-eval-rtx-1g-3: exit 1"
                ),
                "exception_traceback": (
                    'File "/workspace/.github/skill-eval/envs/brev_env.py", '
                    "line 489, in start"
                ),
            },
        }

        with mock.patch.object(
            smoke_runner,
            "_latest_trial",
            return_value=(Path("/tmp/trial"), result),
        ):
            reason = smoke_runner._retryable_worker_setup_failure(
                Path("/tmp/results"),
                "30266918843",
                since=1.0,
                instance="vss-eval-rtx-1g-3",
            )
            mismatched_worker_reason = (
                smoke_runner._retryable_worker_setup_failure(
                    Path("/tmp/results"),
                    "30266918843",
                    since=1.0,
                    instance="vss-eval-rtx-1g-2",
                )
            )

        self.assertEqual(
            reason,
            "NemoClaw setup failed on vss-eval-rtx-1g-3: exit 1",
        )
        self.assertIsNone(mismatched_worker_reason)

    def test_worker_bound_transport_and_resource_failures_are_retryable(self):
        messages = (
            "Upload dir failed on vss-eval-rtx-1g-3: No space left on device",
            (
                "Cannot reach Brev instance 'vss-eval-rtx-1g-3': "
                "connection reset"
            ),
            (
                "Brev instance 'vss-eval-rtx-1g-3' root disk is 100 GB; "
                "task requires at least 400 GB"
            ),
            (
                "Brev instance 'vss-eval-rtx-1g-3' not found "
                "(is it deleted? wrong org?)"
            ),
            (
                "Brev instance 'vss-eval-rtx-1g-3' does not meet task "
                "requirements:"
            ),
            (
                "Brev instance 'vss-eval-rtx-1g-3' has NVIDIA driver 570; "
                "task requires 580+"
            ),
            (
                "Unexpected response from instance 'vss-eval-rtx-1g-3': "
                "'not-ready'"
            ),
        )

        for message in messages:
            result = {
                "config": {
                    "environment": {
                        "import_path": "envs.brev_env:BrevEnvironment",
                    }
                },
                "environment_setup": {
                    "started_at": "2026-07-27T12:49:45Z",
                    "finished_at": "2026-07-27T12:52:09Z",
                },
                "agent_setup": None,
                "agent_execution": None,
                "verifier": None,
                "agent_result": None,
                "verifier_result": None,
                "exception_info": {
                    "exception_type": "RuntimeError",
                    "exception_message": message,
                    "exception_traceback": (
                        'File "/workspace/.github/skill-eval/envs/brev_env.py", '
                        "line 210, in start"
                    ),
                },
            }
            with (
                self.subTest(message=message),
                mock.patch.object(
                    smoke_runner,
                    "_latest_trial",
                    return_value=(Path("/tmp/trial"), result),
                ),
            ):
                reason = smoke_runner._retryable_worker_setup_failure(
                    Path("/tmp/results"),
                    "30266918843",
                    since=1.0,
                    instance="vss-eval-rtx-1g-3",
                )
                mismatched_worker_reason = (
                    smoke_runner._retryable_worker_setup_failure(
                        Path("/tmp/results"),
                        "30266918843",
                        since=1.0,
                        instance="vss-eval-rtx-1g-2",
                    )
                )

            self.assertEqual(reason, message)
            self.assertIsNone(mismatched_worker_reason)

    def test_unbound_or_post_agent_transport_failures_are_not_retryable(self):
        messages = (
            "Upload dir failed: No space left on device",
            "Upload failed: connection reset",
            "Download dir failed: connection reset",
            (
                "No BREV_INSTANCE set and no `brev_instance` in task.toml "
                "[metadata]"
            ),
        )
        for message in messages:
            result = {
                "config": {
                    "environment": {
                        "import_path": "envs.brev_env:BrevEnvironment",
                    }
                },
                "environment_setup": {
                    "started_at": "2026-07-27T12:49:45Z",
                    "finished_at": "2026-07-27T12:52:09Z",
                },
                "agent_setup": None,
                "agent_execution": None,
                "verifier": None,
                "agent_result": None,
                "verifier_result": None,
                "exception_info": {
                    "exception_type": "RuntimeError",
                    "exception_message": message,
                    "exception_traceback": (
                        'File "/workspace/.github/skill-eval/envs/brev_env.py", '
                        "line 210, in start"
                    ),
                },
            }
            with (
                self.subTest(message=message),
                mock.patch.object(
                    smoke_runner,
                    "_latest_trial",
                    return_value=(Path("/tmp/trial"), result),
                ),
            ):
                reason = smoke_runner._retryable_worker_setup_failure(
                    Path("/tmp/results"),
                    "30266918843",
                    since=1.0,
                    instance="vss-eval-rtx-1g-3",
                )

            self.assertIsNone(reason)

    def test_worker_failure_after_agent_setup_is_not_retryable(self):
        result = {
            "config": {
                "environment": {
                    "import_path": "envs.brev_env:BrevEnvironment",
                }
            },
            "environment_setup": {},
            "agent_setup": {
                "started_at": "2026-07-27T12:52:09Z",
            },
            "agent_execution": None,
            "verifier": None,
            "agent_result": None,
            "verifier_result": None,
            "exception_info": {
                "exception_type": "RuntimeError",
                "exception_message": (
                    "NemoClaw setup failed on vss-eval-rtx-1g-3: exit 1"
                ),
                "exception_traceback": (
                    'File "/workspace/.github/skill-eval/envs/brev_env.py", '
                    "line 489, in start"
                ),
            },
        }

        with mock.patch.object(
            smoke_runner,
            "_latest_trial",
            return_value=(Path("/tmp/trial"), result),
        ):
            reason = smoke_runner._retryable_worker_setup_failure(
                Path("/tmp/results"),
                "30266918843",
                since=1.0,
                instance="vss-eval-rtx-1g-3",
            )

        self.assertIsNone(reason)

    def test_runner_fails_over_once_and_reports_only_final_attempt(self):
        scenario = smoke_runner.NemoClawScenario(
            skill="vss-deploy-profile",
            spec_name="base",
            spec_path=Path("/tmp/base.json"),
            platform="RTXPRO6000BW",
            gpu_count=1,
            task_dir=Path("/tmp/dataset/base/rtxpro6000bw"),
            harbor_path=Path("/tmp/dataset/base"),
            task_name="rtxpro6000bw",
            deployment_profile="base",
        )
        selections: list[set[str]] = []
        events: list[str] = []

        def select_worker(*args, excluded=None, **kwargs):
            selections.append(set(excluded or set()))
            instance = (
                "vss-eval-rtx-1g-2"
                if len(selections) == 1
                else "vss-eval-rtx-1g-3"
            )
            events.append(f"select:{instance}")
            return instance, smoke_runner.WorkerLock(123, object(), None)

        def release_worker(instance, worker_lock):
            events.append(f"release:{instance}")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            report = mock.Mock()
            release = mock.Mock(side_effect=release_worker)
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "GITHUB_RUN_ID": "30266918843",
                        "NEMOCLAW_MAX_WORKER_FAILOVERS": "2",
                    },
                ),
                mock.patch.object(
                    smoke_runner,
                    "_discover_scenarios",
                    return_value=([scenario], []),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_scenario_groups",
                    return_value=[[scenario]],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_select_and_lock_instance",
                    side_effect=select_worker,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_harbor_command",
                    return_value=["harbor", "run"],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_stream_command",
                    side_effect=[0, 0],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_latest_reward",
                    side_effect=[(None, None), (1.0, Path("/tmp/reward.txt"))],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_retryable_worker_setup_failure",
                    side_effect=["repo sync failed on worker", None],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_append_harbor_report",
                    report,
                ),
                mock.patch.object(smoke_runner, "_release_lock", release),
            ):
                rc = smoke_runner.main(
                    [
                        "--skills",
                        "vss-deploy-profile",
                        "--dataset-root",
                        str(root / "dataset"),
                        "--results-root",
                        str(root / "results"),
                        "--scratch-root",
                        str(root / "scratch"),
                    ]
                )

        self.assertEqual(rc, 0)
        self.assertEqual(
            selections,
            [set(), {"vss-eval-rtx-1g-2"}],
        )
        self.assertEqual(
            events,
            [
                "select:vss-eval-rtx-1g-2",
                "release:vss-eval-rtx-1g-2",
                "select:vss-eval-rtx-1g-3",
                "release:vss-eval-rtx-1g-3",
            ],
        )
        self.assertEqual(release.call_count, 2)
        report.assert_called_once()
        self.assertEqual(
            report.call_args.kwargs["instance"],
            "vss-eval-rtx-1g-3",
        )
        self.assertIn(
            "vss-eval-rtx-1g-3",
            report.call_args.kwargs["log_path"].name,
        )

    def test_runner_does_not_fail_over_explicit_worker(self):
        scenario = smoke_runner.NemoClawScenario(
            skill="vss-deploy-profile",
            spec_name="base",
            spec_path=Path("/tmp/base.json"),
            platform="RTXPRO6000BW",
            gpu_count=1,
            task_dir=Path("/tmp/dataset/base/rtxpro6000bw"),
            harbor_path=Path("/tmp/dataset/base"),
            task_name="rtxpro6000bw",
            deployment_profile="base",
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            select = mock.Mock(
                return_value=(
                    "pinned-worker",
                    smoke_runner.WorkerLock(123, object(), None),
                )
            )
            report = mock.Mock()
            release = mock.Mock()
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "GITHUB_RUN_ID": "30266918843",
                        "NEMOCLAW_MAX_WORKER_FAILOVERS": "2",
                    },
                ),
                mock.patch.object(
                    smoke_runner,
                    "_discover_scenarios",
                    return_value=([scenario], []),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_scenario_groups",
                    return_value=[[scenario]],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_select_and_lock_instance",
                    select,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_harbor_command",
                    return_value=["harbor", "run"],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_stream_command",
                    return_value=0,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_latest_reward",
                    return_value=(None, None),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_retryable_worker_setup_failure",
                    return_value="repo sync failed on pinned-worker",
                ),
                mock.patch.object(
                    smoke_runner,
                    "_append_harbor_report",
                    report,
                ),
                mock.patch.object(smoke_runner, "_release_lock", release),
            ):
                rc = smoke_runner.main(
                    [
                        "--skills",
                        "vss-deploy-profile",
                        "--instance",
                        "pinned-worker",
                        "--dataset-root",
                        str(root / "dataset"),
                        "--results-root",
                        str(root / "results"),
                        "--scratch-root",
                        str(root / "scratch"),
                    ]
                )

        self.assertEqual(rc, 1)
        select.assert_called_once()
        report.assert_called_once()
        release.assert_called_once()

    def test_runner_does_not_restart_group_after_second_step_setup_failure(self):
        scenarios = [
            smoke_runner.NemoClawScenario(
                skill="vss-ask-video",
                spec_name="base_profile_video_understanding",
                spec_path=Path("/tmp/base_profile_video_understanding.json"),
                platform="RTXPRO6000BW",
                gpu_count=1,
                task_dir=Path("/tmp/dataset/step-1"),
                harbor_path=Path("/tmp/dataset"),
                task_name="step-1",
                deployment_profile="base",
            ),
            smoke_runner.NemoClawScenario(
                skill="vss-ask-video",
                spec_name="base_profile_video_understanding",
                spec_path=Path("/tmp/base_profile_video_understanding.json"),
                platform="RTXPRO6000BW",
                gpu_count=1,
                task_dir=Path("/tmp/dataset/step-2"),
                harbor_path=Path("/tmp/dataset"),
                task_name="step-2",
                deployment_profile="base",
            ),
        ]

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            select = mock.Mock(
                return_value=(
                    "vss-eval-rtx-1g-2",
                    smoke_runner.WorkerLock(123, object(), None),
                )
            )
            report = mock.Mock()
            release = mock.Mock()
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "GITHUB_RUN_ID": "30266918843",
                        "NEMOCLAW_MAX_WORKER_FAILOVERS": "2",
                    },
                ),
                mock.patch.object(
                    smoke_runner,
                    "_discover_scenarios",
                    return_value=(scenarios, []),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_scenario_groups",
                    return_value=[scenarios],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_select_and_lock_instance",
                    select,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_harbor_command",
                    return_value=["harbor", "run"],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_stream_command",
                    side_effect=[0, 0],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_latest_reward",
                    side_effect=[(1.0, Path("/tmp/reward.txt")), (None, None)],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_retryable_worker_setup_failure",
                    return_value="NemoClaw setup failed on vss-eval-rtx-1g-2",
                ),
                mock.patch.object(
                    smoke_runner,
                    "_append_harbor_report",
                    report,
                ),
                mock.patch.object(smoke_runner, "_release_lock", release),
            ):
                rc = smoke_runner.main(
                    [
                        "--skills",
                        "vss-ask-video",
                        "--dataset-root",
                        str(root / "dataset"),
                        "--results-root",
                        str(root / "results"),
                        "--scratch-root",
                        str(root / "scratch"),
                    ]
                )

        self.assertEqual(rc, 1)
        select.assert_called_once()
        self.assertEqual(report.call_count, 2)
        release.assert_called_once()

    def test_worker_selection_uses_brev_id_as_exec_target(self):
        previous = {
            "_list_instances": smoke_runner._list_instances,
            "_reachable": smoke_runner._reachable,
            "_try_acquire_lock": smoke_runner._try_acquire_lock,
        }
        calls: dict[str, tuple[str, str | None]] = {}
        instances = [
            {
                "id": "instance-123",
                "name": "vss-eval-rtx-1g-2",
                "status": "RUNNING",
                "gpu": "RTX PRO 6000",
            },
        ]

        def fake_reachable(instance: str, exec_target: str | None = None) -> bool:
            calls["reachable"] = (instance, exec_target)
            return True

        def fake_lock(instance: str, exec_target: str | None = None):
            calls["lock"] = (instance, exec_target)
            return smoke_runner.WorkerLock(123, object(), "owner", exec_target)

        smoke_runner._list_instances = lambda: instances
        smoke_runner._reachable = fake_reachable
        smoke_runner._try_acquire_lock = fake_lock
        try:
            selected, lock = smoke_runner._select_and_lock_instance(
                "RTXPRO6000BW",
                1,
                None,
                10,
            )
        finally:
            smoke_runner._list_instances = previous["_list_instances"]
            smoke_runner._reachable = previous["_reachable"]
            smoke_runner._try_acquire_lock = previous["_try_acquire_lock"]

        self.assertEqual(selected, "vss-eval-rtx-1g-2")
        self.assertEqual(calls["reachable"], ("vss-eval-rtx-1g-2", "instance-123"))
        self.assertEqual(calls["lock"], ("vss-eval-rtx-1g-2", "instance-123"))
        self.assertEqual(lock.remote_target, "instance-123")

    def test_explicit_worker_uses_brev_id_as_exec_target_when_visible(self):
        previous = {
            "_list_instances": smoke_runner._list_instances,
            "_reachable": smoke_runner._reachable,
            "_try_acquire_lock": smoke_runner._try_acquire_lock,
        }
        calls: dict[str, tuple[str, str | None]] = {}
        instances = [
            {
                "id": "instance-explicit",
                "name": "vss-eval-rtx-1g-10",
                "status": "RUNNING",
                "gpu": "RTXPro6000",
            },
        ]

        def fake_reachable(instance: str, exec_target: str | None = None) -> bool:
            calls["reachable"] = (instance, exec_target)
            return True

        def fake_lock(instance: str, exec_target: str | None = None):
            calls["lock"] = (instance, exec_target)
            return smoke_runner.WorkerLock(123, object(), "owner", exec_target)

        smoke_runner._list_instances = lambda: instances
        smoke_runner._reachable = fake_reachable
        smoke_runner._try_acquire_lock = fake_lock
        try:
            selected, lock = smoke_runner._select_and_lock_instance(
                "RTXPRO6000BW",
                1,
                "vss-eval-rtx-1g-10",
                10,
            )
        finally:
            smoke_runner._list_instances = previous["_list_instances"]
            smoke_runner._reachable = previous["_reachable"]
            smoke_runner._try_acquire_lock = previous["_try_acquire_lock"]

        self.assertEqual(selected, "vss-eval-rtx-1g-10")
        self.assertEqual(calls["reachable"], ("vss-eval-rtx-1g-10", "instance-explicit"))
        self.assertEqual(calls["lock"], ("vss-eval-rtx-1g-10", "instance-explicit"))
        self.assertEqual(lock.remote_target, "instance-explicit")

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

    def test_explicit_worker_timeout_names_worker(self):
        previous = {
            "_list_instances": smoke_runner._list_instances,
            "_reachable": smoke_runner._reachable,
            "sleep": smoke_runner.time.sleep,
        }
        smoke_runner._list_instances = lambda: []
        smoke_runner._reachable = lambda instance, exec_target=None: False
        smoke_runner.time.sleep = lambda seconds: None
        try:
            with self.assertRaises(smoke_runner.InfrastructureBlocked) as ctx:
                smoke_runner._select_and_lock_instance(
                    "RTXPRO6000BW",
                    1,
                    "vss-eval-rtx-2g-5",
                    0,
                )
        finally:
            smoke_runner._list_instances = previous["_list_instances"]
            smoke_runner._reachable = previous["_reachable"]
            smoke_runner.time.sleep = previous["sleep"]

        message = str(ctx.exception)
        self.assertIn("explicit worker vss-eval-rtx-2g-5", message)
        self.assertIn("RTXPRO6000BW", message)

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
        smoke_runner._reachable = lambda instance, exec_target=None: True
        smoke_runner._try_acquire_lock = lambda instance, exec_target=None: smoke_runner.WorkerLock(
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

    def test_remote_lock_owner_helpers_are_conservative(self):
        self.assertEqual(
            smoke_runner._remote_lock_owner_from_output(
                "NemoClaw worker is locked by 27354810855__nemoclaw-eval__123"
            ),
            "27354810855__nemoclaw-eval__123",
        )
        self.assertEqual(
            smoke_runner._github_run_id_from_lock_owner("27354810855__nemoclaw-eval__123"),
            "27354810855",
        )
        self.assertIsNone(smoke_runner._github_run_id_from_lock_owner("manual__nemoclaw"))

    def test_remote_lock_from_current_run_is_active(self):
        previous = {"GITHUB_RUN_ID": os.environ.get("GITHUB_RUN_ID")}
        os.environ["GITHUB_RUN_ID"] = "27354810855"
        try:
            self.assertFalse(
                smoke_runner._remote_lock_owner_is_inactive(
                    "27354810855__nemoclaw-eval__123"
                )
            )
        finally:
            if previous["GITHUB_RUN_ID"] is None:
                os.environ.pop("GITHUB_RUN_ID", None)
            else:
                os.environ["GITHUB_RUN_ID"] = previous["GITHUB_RUN_ID"]

    def test_remote_lock_reconciles_exact_owner_after_response_loss(self):
        calls: list[list[str]] = []

        def fake_run(cmd, *, timeout=60, env=None):
            calls.append(cmd)
            command_body = cmd[3]
            match = re.search(r"^owner=([^\n]+)$", command_body, re.MULTILINE)
            self.assertIsNotNone(match)
            owner = match.group(1)
            return smoke_runner.CommandResult(
                1,
                f"NemoClaw worker is locked by {owner} age=0s",
                "",
            )

        nonce = mock.Mock(hex="a" * 32)
        with (
            mock.patch.object(smoke_runner, "_run", side_effect=fake_run),
            mock.patch.object(smoke_runner.os, "getpid", return_value=1234),
            mock.patch.object(smoke_runner.time, "time", return_value=1730000000),
            mock.patch.object(smoke_runner.uuid, "uuid4", return_value=nonce),
            mock.patch.dict(
                os.environ,
                {
                    "GITHUB_RUN_ID": "30275546898",
                    "GITHUB_RUN_ATTEMPT": "2",
                    "NEMOCLAW_LOCK_OWNER_CONTEXT": (
                        "vss-ask-video/base_profile_video_understanding/"
                        "RTXPRO6000BW"
                    ),
                },
            ),
        ):
            owner = smoke_runner._try_acquire_remote_worker_lock(
                "vss-eval-rtx-2g-2"
            )

        self.assertEqual(
            owner,
            "v2__30275546898__2__"
            "vss-ask-video-base-profile-video-understanding-rtxpro6000bw__"
            "1234__1730000000__"
            f"{'a' * 32}",
        )
        self.assertEqual(len(calls), 1)
        self.assertIn("cleanup_incomplete_lock", calls[0][3])

    def test_remote_lock_refresh_is_atomic_and_exact_owner_only(self):
        with mock.patch.object(
            smoke_runner,
            "_run",
            return_value=smoke_runner.CommandResult(
                0,
                "refreshed NemoClaw worker lock owned by expected-owner",
                "",
            ),
        ) as run:
            status = smoke_runner._refresh_remote_worker_lock(
                "worker-id",
                "expected-owner",
            )

        self.assertEqual(status, "refreshed")
        command = run.call_args.args[0][3]
        self.assertIn("expected=expected-owner", command)
        self.assertIn("stat -Lc '%d:%i'", command)
        self.assertIn('mktemp "$lock_dir/.created.', command)
        self.assertIn('mv -f "$tmp" "$lock_dir/created"', command)
        self.assertNotIn('mkdir "$lock_dir"', command)
        self.assertNotIn('rm -rf "$lock_dir"', command)

    def test_remote_lock_refresh_reports_owner_loss_without_mutation(self):
        with mock.patch.object(
            smoke_runner,
            "_run",
            return_value=smoke_runner.CommandResult(
                3,
                "NemoClaw worker lock is not owned by expected-owner",
                "",
            ),
        ):
            status = smoke_runner._refresh_remote_worker_lock(
                "worker-id",
                "expected-owner",
            )

        self.assertEqual(status, "not_owner")

    def test_remote_lock_refresh_timeout_is_unknown(self):
        with mock.patch.object(
            smoke_runner,
            "_run",
            side_effect=subprocess.TimeoutExpired(["brev", "exec"], 30),
        ):
            status = smoke_runner._refresh_remote_worker_lock(
                "worker-id",
                "expected-owner",
            )

        self.assertEqual(status, "unknown")

    def test_remote_lock_refresh_os_error_is_unknown(self):
        with mock.patch.object(
            smoke_runner,
            "_run",
            side_effect=OSError("brev executable unavailable"),
        ):
            status = smoke_runner._refresh_remote_worker_lock(
                "worker-id",
                "expected-owner",
            )

        self.assertEqual(status, "unknown")

    def test_heartbeat_start_failure_clears_exact_remote_lock(self):
        handle = mock.Mock()
        with (
            mock.patch.object(smoke_runner.os, "open", return_value=123),
            mock.patch.object(smoke_runner.os, "fdopen", return_value=handle),
            mock.patch.object(smoke_runner.fcntl, "flock"),
            mock.patch.object(
                smoke_runner,
                "_try_acquire_remote_worker_lock",
                return_value="expected-owner",
            ),
            mock.patch.object(
                smoke_runner,
                "_start_remote_worker_lock_heartbeat",
                side_effect=RuntimeError("thread start failed"),
            ),
            mock.patch.object(
                smoke_runner,
                "_clear_remote_worker_lock",
                return_value=True,
            ) as clear,
            self.assertRaisesRegex(RuntimeError, "thread start failed"),
        ):
            smoke_runner._try_acquire_lock(
                "worker-name",
                "worker-id",
            )

        clear.assert_called_once_with("worker-id", "expected-owner")
        handle.close.assert_called_once()

    def test_release_stops_heartbeat_before_exact_owner_delete(self):
        events: list[str] = []
        heartbeat = smoke_runner.RemoteLockHeartbeat(
            threading.Event(),
            threading.Event(),
            mock.Mock(),
        )
        handle = mock.Mock()

        def stop_heartbeat(_heartbeat):
            events.append("stop")

        def run(cmd, *, timeout=60, env=None):
            events.append("delete")
            return smoke_runner.CommandResult(0, "", "")

        with (
            mock.patch.object(
                smoke_runner,
                "_stop_remote_worker_lock_heartbeat",
                side_effect=stop_heartbeat,
            ),
            mock.patch.object(smoke_runner, "_run", side_effect=run),
            mock.patch.object(smoke_runner.fcntl, "flock"),
        ):
            smoke_runner._release_lock(
                "worker-name",
                smoke_runner.WorkerLock(
                    123,
                    handle,
                    "expected-owner",
                    "worker-id",
                    heartbeat,
                ),
            )

        self.assertEqual(events, ["stop", "delete"])
        handle.close.assert_called_once()

    def test_stream_command_aborts_after_confirmed_lock_loss(self):
        abort_event = threading.Event()
        abort_event.set()
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "harbor.log"
            rc = smoke_runner._stream_command(
                [
                    sys.executable,
                    "-c",
                    "pass",
                ],
                timeout_s=10,
                env=os.environ.copy(),
                log_path=log_path,
                abort_event=abort_event,
            )

            log = log_path.read_text(encoding="utf-8")

        self.assertEqual(rc, 125)
        self.assertIn("aborting Harbor after remote worker lock loss", log)

    def test_completed_matrix_job_lock_is_inactive_within_current_run(self):
        owner = (
            "v2__30275546898__1__"
            "vss-ask-video-base-profile-video-understanding-rtxpro6000bw__"
            "1234__1730000000__nonce"
        )
        with (
            mock.patch.dict(
                os.environ,
                {"GITHUB_RUN_ID": "30275546898"},
            ),
            mock.patch.object(
                smoke_runner,
                "_github_job_status",
                return_value="completed",
            ) as job_status,
            mock.patch.object(smoke_runner, "_github_run_status") as run_status,
        ):
            inactive = smoke_runner._remote_lock_owner_is_inactive(owner)

        self.assertTrue(inactive)
        job_status.assert_called_once_with(
            "30275546898",
            "1",
            "vss-ask-video-base-profile-video-understanding-rtxpro6000bw",
        )
        run_status.assert_not_called()

    def test_github_job_status_matches_matrix_context(self):
        payload = {
            "jobs": [
                {
                    "name": (
                        "NemoClaw / vss-ask-video/"
                        "base_profile_video_understanding/RTXPRO6000BW"
                    ),
                    "status": "completed",
                },
                {
                    "name": "NemoClaw / vss-deploy-profile/base/RTXPRO6000BW",
                    "status": "in_progress",
                },
            ]
        }
        with (
            mock.patch.dict(
                os.environ,
                {
                    "GITHUB_REPOSITORY": (
                        "NVIDIA-AI-Blueprints/video-search-and-summarization"
                    ),
                    "GH_TOKEN": "test-token",
                },
            ),
            mock.patch.object(
                smoke_runner,
                "_run",
                return_value=smoke_runner.CommandResult(
                    0,
                    json.dumps(payload),
                    "",
                ),
            ) as run,
        ):
            status = smoke_runner._github_job_status(
                "30275546898",
                "1",
                "vss-ask-video-base-profile-video-understanding-rtxpro6000bw",
            )

        self.assertEqual(status, "completed")
        self.assertIn(
            "repos/NVIDIA-AI-Blueprints/video-search-and-summarization/"
            "actions/runs/30275546898/attempts/1/jobs?per_page=100",
            run.call_args.args[0],
        )

    def test_remote_lock_from_completed_run_is_cleared_and_retried(self):
        previous = {
            "_run": smoke_runner._run,
            "_github_run_status": smoke_runner._github_run_status,
            "GITHUB_RUN_ID": os.environ.get("GITHUB_RUN_ID"),
            "GITHUB_JOB": os.environ.get("GITHUB_JOB"),
        }
        calls: list[list[str]] = []

        def fake_run(cmd, *, timeout=60, env=None):
            calls.append(cmd)
            command_body = cmd[3] if cmd[:2] == ["brev", "exec"] and len(cmd) > 3 else ""
            if command_body and "expected=" in command_body and "rm -rf" in command_body:
                return smoke_runner.CommandResult(
                    0,
                    "removed NemoClaw worker lock owned by 27354810855__nemoclaw-eval__old",
                    "",
                )
            if cmd[:2] == ["brev", "exec"] and len(calls) == 1:
                return smoke_runner.CommandResult(
                    1,
                    "NemoClaw worker is locked by 27354810855__nemoclaw-eval__old",
                    "",
                )
            return smoke_runner.CommandResult(0, "", "")

        smoke_runner._run = fake_run
        smoke_runner._github_run_status = lambda run_id: "completed"
        os.environ["GITHUB_RUN_ID"] = "27358558981"
        os.environ["GITHUB_JOB"] = "nemoclaw-eval"
        try:
            owner = smoke_runner._try_acquire_remote_worker_lock("vss-eval-rtx-2g-2")
        finally:
            smoke_runner._run = previous["_run"]
            smoke_runner._github_run_status = previous["_github_run_status"]
            for key in ("GITHUB_RUN_ID", "GITHUB_JOB"):
                if previous[key] is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = previous[key]

        self.assertIsNotNone(owner)
        self.assertEqual(len(calls), 3)
        self.assertIn("rm -rf", calls[1][3])

    def test_aged_remote_lock_from_active_run_is_never_evicted(self):
        calls: list[list[str]] = []

        def fake_run(cmd, *, timeout=60, env=None):
            calls.append(cmd)
            return smoke_runner.CommandResult(
                1,
                (
                    "NemoClaw worker is locked by "
                    "30266918843__nemoclaw-eval__old age=1800s"
                ),
                "",
            )

        with (
            mock.patch.object(smoke_runner, "_run", side_effect=fake_run),
            mock.patch.object(
                smoke_runner,
                "_github_run_status",
                return_value="in_progress",
            ),
            mock.patch.dict(
                os.environ,
                {
                    "GITHUB_RUN_ID": "30272325661",
                    "GITHUB_JOB": "nemoclaw-eval",
                },
            ),
        ):
            owner = smoke_runner._try_acquire_remote_worker_lock(
                "vss-eval-rtx-2g-2"
            )

        self.assertIsNone(owner)
        self.assertEqual(len(calls), 1)
        self.assertIn("age=$((now - created))", calls[0][3])
        self.assertNotIn("expected=", calls[0][3])

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

    def test_reachability_failure_logs_brev_output(self):
        previous = {"_run": smoke_runner._run}
        smoke_runner._run = lambda *args, **kwargs: smoke_runner.CommandResult(
            255,
            "",
            "ssh: Could not resolve hostname vss-eval-rtx-2g-4",
        )
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                reachable = smoke_runner._reachable("vss-eval-rtx-2g-4")
        finally:
            smoke_runner._run = previous["_run"]

        self.assertFalse(reachable)
        self.assertIn("reachability failed rc=255", output.getvalue())
        self.assertIn("Could not resolve hostname", output.getvalue())

    def test_reachability_refreshes_ssh_config_after_hostname_failure(self):
        previous = {"_run": smoke_runner._run}
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:2] == ["brev", "refresh"]:
                return smoke_runner.CommandResult(0, "refreshed\n", "")
            if len(calls) == 1:
                return smoke_runner.CommandResult(
                    1,
                    "",
                    "ssh: Could not resolve hostname vss-eval-rtx-2g-4",
                )
            return smoke_runner.CommandResult(0, "harbor-ready\n", "")

        smoke_runner._run = fake_run
        try:
            reachable = smoke_runner._reachable("vss-eval-rtx-2g-4")
        finally:
            smoke_runner._run = previous["_run"]

        self.assertTrue(reachable)
        self.assertEqual(
            calls,
            [
                ["brev", "exec", "vss-eval-rtx-2g-4", "echo harbor-ready"],
                ["brev", "refresh"],
                ["brev", "exec", "vss-eval-rtx-2g-4", "echo harbor-ready"],
            ],
        )

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
        self.assertIn("## GPU resource boundary", prompt)
        self.assertIn("only valid device ID is 0", prompt)
        self.assertIn("Never request GPU 1", prompt)
        self.assertIn("headless_runner.py", instruction)
        self.assertIn("--wait-profile base", instruction)
        self.assertIn('runner = "nemoclaw"', task_toml)
        self.assertIn('expected_skill = "vss-ask-video"', task_toml)
        self.assertIn("vss_orchestrator__docker_status", task_toml)

    def test_nemoclaw_wrapper_rejects_conflicting_gpu_boundary(self):
        with self.assertRaisesRegex(RuntimeError, "disagrees with task gpu_count=1"):
            smoke_runner._with_gpu_resource_guidance(
                "This trial reserves exactly 2 GPUs; valid device IDs are 0 through 1.\n",
                gpu_count=1,
            )

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
                      --timeout 1500
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

    def test_nemoclaw_report_reads_openclaw_json_usage_when_trajectory_missing(self):
        with tempfile.TemporaryDirectory() as td:
            trial_dir = Path(td) / "trial"
            log_dir = trial_dir / "artifacts" / "nemoclaw"
            log_dir.mkdir(parents=True)
            (log_dir / "openclaw-agent.log").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "assistant_message",
                                "usage": {
                                    "input_tokens": 8400,
                                    "cache_read_input_tokens": 100,
                                    "cache_creation_input_tokens": 50,
                                },
                            }
                        ),
                        "prefixed log "
                        + json.dumps(
                            {
                                "role": "assistant",
                                "modelUsage": {
                                    "main": {
                                        "inputTokens": 1100,
                                        "cacheReadInputTokens": 25,
                                    }
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            metrics = smoke_runner._load_trajectory_metrics(
                trial_dir,
                {"agent_result": {"n_input_tokens": None, "n_cache_tokens": None}},
            )

        self.assertEqual(metrics, ("2", "9.5k", "175"))

    def test_nemoclaw_report_reads_pretty_openclaw_result_payloads(self):
        with tempfile.TemporaryDirectory() as td:
            trial_dir = Path(td) / "trial"
            log_dir = trial_dir / "artifacts" / "nemoclaw"
            log_dir.mkdir(parents=True)
            (log_dir / "openclaw-agent.log").write_text(
                "warning before json\n"
                + json.dumps(
                    {
                        "runId": "abc",
                        "status": "ok",
                        "result": {
                            "payloads": [{"text": "one"}, {"text": "two"}],
                            "meta": {
                                "agentMeta": {
                                    "lastCallUsage": {
                                        "input": 42,
                                        "cacheRead": 5,
                                        "cacheWrite": 7,
                                    }
                                }
                            },
                        },
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            metrics = smoke_runner._load_trajectory_metrics(
                trial_dir,
                {"agent_result": {"n_input_tokens": None, "n_cache_tokens": None}},
            )

        self.assertEqual(metrics, ("2", "42", "12"))

    def test_nemoclaw_report_preserves_zero_openclaw_usage(self):
        with tempfile.TemporaryDirectory() as td:
            trial_dir = Path(td) / "trial"
            log_dir = trial_dir / "artifacts" / "nemoclaw"
            log_dir.mkdir(parents=True)
            (log_dir / "openclaw-agent.log").write_text(
                json.dumps(
                    {
                        "result": {
                            "payloads": [{"text": "done"}],
                            "meta": {
                                "agentMeta": {
                                    "lastCallUsage": {
                                        "input": 0,
                                        "cacheRead": 0,
                                        "cacheWrite": 0,
                                    }
                                }
                            },
                        }
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            metrics = smoke_runner._load_trajectory_metrics(
                trial_dir,
                {"agent_result": {"n_input_tokens": None, "n_cache_tokens": None}},
            )

        self.assertEqual(metrics, ("1", "0", "0"))

    def test_nemoclaw_report_estimates_prompt_tokens_when_openclaw_usage_is_zero(self):
        with tempfile.TemporaryDirectory() as td:
            trial_dir = Path(td) / "trial"
            log_dir = trial_dir / "artifacts" / "nemoclaw"
            log_dir.mkdir(parents=True)
            (log_dir / "openclaw-agent.log").write_text(
                json.dumps(
                    {
                        "result": {
                            "payloads": [{"text": "done"}],
                            "meta": {
                                "systemPromptReport": {
                                    "systemPrompt": {"chars": 4000},
                                    "skills": {"promptChars": 800},
                                },
                                "finalPromptText": "x" * 400,
                                "agentMeta": {
                                    "lastCallUsage": {
                                        "input": 0,
                                        "cacheRead": 0,
                                        "cacheWrite": 0,
                                    }
                                },
                            },
                        }
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            metrics = smoke_runner._load_trajectory_metrics(
                trial_dir,
                {"agent_result": {"n_input_tokens": None, "n_cache_tokens": None}},
            )

        self.assertEqual(metrics, ("1", "~1.3k", "0"))

    def test_nemoclaw_report_falls_back_to_harbor_tokens_when_openclaw_zero(self):
        with tempfile.TemporaryDirectory() as td:
            trial_dir = Path(td) / "trial"
            log_dir = trial_dir / "artifacts" / "nemoclaw"
            log_dir.mkdir(parents=True)
            (log_dir / "openclaw-agent.log").write_text(
                json.dumps(
                    {
                        "result": {
                            "payloads": [{"text": "done"}],
                            "meta": {
                                "agentMeta": {
                                    "lastCallUsage": {
                                        "input": 0,
                                        "cacheRead": 0,
                                        "cacheWrite": 0,
                                    }
                                }
                            },
                        }
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            metrics = smoke_runner._load_trajectory_metrics(
                trial_dir,
                {"agent_result": {"n_input_tokens": 28_008_935, "n_cache_tokens": 27_445_245}},
            )

        self.assertEqual(metrics, ("1", "28.0M", "27.4M"))

    def test_nemoclaw_report_marks_async_metrics_as_not_emitted(self):
        with tempfile.TemporaryDirectory() as td:
            trial_dir = Path(td) / "trial"
            log_dir = trial_dir / "artifacts" / "nemoclaw"
            log_dir.mkdir(parents=True)
            (log_dir / "openclaw-agent.log").write_text(
                json.dumps({"type": "assistant_partial"}) + "\n",
                encoding="utf-8",
            )
            (log_dir / "nemoclaw_hooks_response.json").write_text(
                json.dumps(
                    {
                        "elapsed_s": 699.94,
                        "response": {
                            "status": 200,
                            "body": {
                                "ok": True,
                                "mode": "cli-async",
                                "returncode": 0,
                            },
                        },
                        "wait": {
                            "waited": True,
                            "ok": True,
                            "profile": "base",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (log_dir / "nemoclaw_wait.json").write_text(
                json.dumps([{"ok": False}, {"ok": True}]),
                encoding="utf-8",
            )

            metrics = smoke_runner._load_trajectory_metrics(
                trial_dir,
                {"agent_result": {"n_input_tokens": None, "n_cache_tokens": None}},
            )
            details = smoke_runner._nemoclaw_runtime_details(trial_dir)

        self.assertEqual(metrics, ("async readiness", "not emitted", "not emitted"))
        self.assertIn("- Readiness wait: `11m 40s`", details)
        self.assertIn("- Readiness polls: `2`", details)

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
            test_script = (task_dir / "tests" / "test.sh").read_text(encoding="utf-8")
            solve_script = (task_dir / "solution" / "solve.sh").read_text(encoding="utf-8")

            self.assertIn('runner = "nemoclaw"', task_toml)
            self.assertIn('requires_mcp = true', task_toml)
            self.assertIn('vss_orchestrator__docker_up', task_toml)
            self.assertIn("headless_runner.py", instruction)
            self.assertIn("--log-dir /logs/artifacts/nemoclaw", instruction)
            self.assertIn("--launch-mode cli", instruction)
            self.assertIn("--timeout 1500", instruction)
            self.assertIn("--wait-profile base", instruction)
            self.assertIn("nemoclaw_deploy_profile.py", test_script)
            self.assertTrue((task_dir / "tests" / "nemoclaw_deploy_profile.py").exists())
            prompt = (task_dir / "tests" / "nemoclaw_prompt.md").read_text(encoding="utf-8")
            self.assertIn("Use the `/vss-deploy-profile` skill", prompt)
            self.assertIn("reserves exactly 1 GPU", prompt)
            self.assertIn("only valid device ID is 0", prompt)
            self.assertIn("Leave GPU device-ID overrides unset", prompt)
            self.assertIn("shared placement on GPU 0", prompt)
            self.assertIn("Never request GPU 1", prompt)
            self.assertIn("git clean -fdx -e data/ -e /.env", solve_script)
            self.assertNotIn("git clean -fdx -e data/ -e .env", solve_script)

    def test_nemoclaw_prompt_bounds_multi_gpu_device_ids(self):
        prompt = deploy_adapter.generate_nemoclaw_prompt(
            "base",
            "H100",
            deploy_adapter.PROFILES["base"],
            gpu_count=2,
        )

        self.assertIn("reserves exactly 2 GPUs", prompt)
        self.assertIn("valid device IDs are 0 through 1", prompt)
        self.assertIn("Never request an out-of-range device", prompt)

    def test_nemoclaw_deploy_profile_checks_include_openclaw_log_fallbacks(self):
        rendered = deploy_adapter._render_nemoclaw_eval_spec(
            {
                "expects": [
                    {
                        "checks": [
                            "`curl -sf --max-time 15 http://localhost:8000/health` returns exit 0",
                            "`curl -sf --max-time 15 http://localhost:3000/` returns exit 0",
                            "`docker ps --format '{{.Names}}' | grep -qx vss-agent` returns exit 0",
                            "`docker ps --format '{{.Names}}' | grep -qx phoenix` returns exit 0",
                        ]
                    }
                ]
            }
        )

        checks = rendered["expects"][0]["checks"]
        self.assertTrue(all("/logs/artifacts/nemoclaw/openclaw-agent.log" in check for check in checks))
        self.assertIn("vss-agent` health check", checks[0])
        self.assertIn("Brev secure-link URL", checks[1])
        self.assertIn("`vss-agent` as running", checks[2])
        self.assertIn("`vss-haproxy-ingress`", checks[3])

    def test_nemoclaw_deploy_profile_verifier_uses_openclaw_fallback(self):
        raw_log = "finalAssistantVisibleText: vss-agent health checks passing: 200 OK; https://7777-x.brevlab.com/; vss-haproxy-ingress running"
        final = raw_log

        api = nemoclaw_deploy_profile_verifier._fallback_pass(
            "`curl -sf --max-time 15 http://localhost:8000/health` returns exit 0",
            final,
            raw_log,
        )
        ui = nemoclaw_deploy_profile_verifier._fallback_pass(
            "`curl -sf --max-time 15 http://localhost:3000/` returns exit 0",
            final,
            raw_log,
        )
        phoenix = nemoclaw_deploy_profile_verifier._fallback_pass(
            "`docker ps --format '{{.Names}}' | grep -qx phoenix` returns exit 0",
            final,
            raw_log,
        )

        self.assertTrue(api[0])
        self.assertTrue(ui[0])
        self.assertTrue(phoenix[0])

    def test_nemoclaw_deploy_profile_verifier_reads_v0080_payload_text(self):
        previous = nemoclaw_deploy_profile_verifier.LOG_PATH
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "openclaw-agent.log"
            log_path.write_text(
                "openclaw info\n"
                + json.dumps(
                    {
                        "status": "ok",
                        "result": {
                            "payloads": [
                                {
                                    "text": (
                                        "vss-agent health checks passing: 200 OK; "
                                        "https://7777-x.brevlab.com/; "
                                        "vss-haproxy-ingress running"
                                    )
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            nemoclaw_deploy_profile_verifier.LOG_PATH = log_path
            try:
                raw, final = nemoclaw_deploy_profile_verifier._openclaw_text()
            finally:
                nemoclaw_deploy_profile_verifier.LOG_PATH = previous

        self.assertIn("openclaw info", raw)
        self.assertIn("vss-agent health checks passing: 200 OK", final)
        self.assertIn("vss-haproxy-ingress running", final)

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

        self.assertIn("max-parallel: 1", source)
        self.assertIn("nemoclaw_instance:", source)
        self.assertIn("runs-on: [self-hosted, nemoclaw-ci-runner]", source)
        self.assertIn("timeout-minutes: 90", source)
        self.assertIn("export NEMOCLAW_LOCK_TIMEOUT_SEC=1200", source)
        self.assertIn(
            "export NEMOCLAW_REMOTE_LOCK_HEARTBEAT_SEC=180",
            source,
        )
        self.assertIn(
            "export NEMOCLAW_REMOTE_LOCK_HEARTBEAT_MAX_SILENCE_SEC=660",
            source,
        )
        self.assertIn("NEMOCLAW_INPUT_INSTANCE:", source)
        self.assertIn('export NEMOCLAW_BREV_INSTANCE="$NEMOCLAW_INPUT_INSTANCE"', source)
        self.assertIn("export NEMOCLAW_HARBOR_TIMEOUT_SEC=2400", source)
        self.assertIn("export NEMOCLAW_REMOTE_SETUP_TIMEOUT_SEC=1500", source)
        self.assertIn("export NEMOCLAW_SETUP_TIMEOUT_SEC=1620", source)
        self.assertIn("export NEMOCLAW_SETUP_CELL_TIMEOUT=900", source)
        self.assertIn("export NEMOCLAW_AGENT_TIMEOUT_SEC=1500", source)
        self.assertIn(
            'export NEMOCLAW_LOCK_OWNER_CONTEXT="${{ matrix.name }}"',
            source,
        )
        self.assertNotIn("NEMOCLAW_REMOTE_LOCK_STALE_SEC", source)


if __name__ == "__main__":
    unittest.main()
