# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import io
import ipaddress
import json
import os
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

RUNNER_PATH = Path(__file__).parents[1] / "run_setup_notebook.py"
MODULE_SPEC = importlib.util.spec_from_file_location("run_setup_notebook_under_test", RUNNER_PATH)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError(f"Could not load {RUNNER_PATH}")
runner = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(runner)

SCRIPTS_DIR = RUNNER_PATH.parent


def marker_cell(*sources: str) -> dict:
    return {"cells": [{"source": source} for source in sources]}


class ParameterContractTests(unittest.TestCase):
    def test_covers_checked_in_setup_notebooks(self) -> None:
        self.assertEqual(
            sorted(runner.NOTEBOOK_PARAMETERS),
            [
                "deploy_nemo_relay.ipynb",
                "deploy_nemoclaw.ipynb",
                "deploy_vss_orchestrator.ipynb",
            ],
        )
        for name in runner.NOTEBOOK_PARAMETERS:
            self.assertTrue((SCRIPTS_DIR / name).is_file(), name)

    def test_repo_root_resolves_to_the_checkout(self) -> None:
        self.assertEqual(runner.repo_root(), SCRIPTS_DIR.parents[2])

    def test_an_unknown_notebook_names_the_ones_that_are_known(self) -> None:
        with self.assertRaises(ValueError) as raised:
            runner.parameters_for(Path("deploy_unknown.ipynb"))
        self.assertIn("deploy_nemoclaw.ipynb", str(raised.exception))


class ParameterizeNotebookTests(unittest.TestCase):
    def test_the_environment_wins_over_the_settings_literal(self) -> None:
        notebook = marker_cell(
            f'NEMOCLAW_MODEL = "literal"\n{runner.DERIVED_SETTINGS_MARKER}\n'
        )
        runner.parameterize_notebook(notebook, ("NEMOCLAW_MODEL",))
        namespace: dict[str, object] = {}
        with mock.patch.dict(os.environ, {"NEMOCLAW_MODEL": "from-env"}, clear=True):
            exec(  # noqa: S102 - synthetic cell built in this test.
                compile(notebook["cells"][0]["source"], "<cell>", "exec"), namespace
            )
        self.assertEqual(namespace["NEMOCLAW_MODEL"], "from-env")

    def test_the_literal_stands_when_the_variable_is_unset(self) -> None:
        notebook = marker_cell(
            f'NEMOCLAW_MODEL = "literal"\n{runner.DERIVED_SETTINGS_MARKER}\n'
        )
        runner.parameterize_notebook(notebook, ("NEMOCLAW_MODEL",))
        namespace: dict[str, object] = {}
        with mock.patch.dict(os.environ, {}, clear=True):
            exec(  # noqa: S102 - synthetic cell built in this test.
                compile(notebook["cells"][0]["source"], "<cell>", "exec"), namespace
            )
        self.assertEqual(namespace["NEMOCLAW_MODEL"], "literal")

    def test_the_last_mutually_exclusive_provider_cell_no_longer_wins(self) -> None:
        """Sections 1.2 (a)/(b)/(c) all execute in a top-to-bottom run."""

        notebook = marker_cell(
            'NEMOCLAW_PROVIDER = "install-vllm"\n',
            'NEMOCLAW_PROVIDER = "build-nvidia"\n',
            f'NEMOCLAW_PROVIDER = "custom"\n{runner.DERIVED_SETTINGS_MARKER}\n',
        )
        runner.parameterize_notebook(notebook, ("NEMOCLAW_PROVIDER",))
        namespace: dict[str, object] = {}
        with mock.patch.dict(
            os.environ, {"NEMOCLAW_PROVIDER": "requested"}, clear=True
        ):
            for cell in notebook["cells"]:
                exec(  # noqa: S102 - synthetic cells built in this test.
                    compile(cell["source"], "<cell>", "exec"), namespace
                )
        self.assertEqual(namespace["NEMOCLAW_PROVIDER"], "requested")

    def test_injects_before_the_marker_and_into_that_cell_only(self) -> None:
        notebook = marker_cell(
            "UNTOUCHED = 1\n",
            f"{runner.DERIVED_SETTINGS_MARKER}\nDERIVED = 2\n",
            f"{runner.DERIVED_SETTINGS_MARKER}\n",
        )
        runner.parameterize_notebook(notebook, ("NEMOCLAW_MODEL",))
        cells = [cell["source"] for cell in notebook["cells"]]
        self.assertEqual(cells[0], "UNTOUCHED = 1\n")
        self.assertLess(
            cells[1].index("NEMOCLAW_MODEL = "),
            cells[1].index(runner.DERIVED_SETTINGS_MARKER),
        )
        self.assertNotIn("NEMOCLAW_MODEL", cells[2])

    def test_accepts_a_source_stored_as_a_list_of_lines(self) -> None:
        notebook = {
            "cells": [{"source": ['MODEL = "literal"\n', f"{runner.DERIVED_SETTINGS_MARKER}\n"]}]
        }
        runner.parameterize_notebook(notebook, ("MODEL",))
        self.assertIsInstance(notebook["cells"][0]["source"], str)

    def test_no_parameters_leaves_the_notebook_alone(self) -> None:
        notebook = marker_cell("MODEL = 1\n")
        runner.parameterize_notebook(notebook, ())
        self.assertEqual(notebook["cells"][0]["source"], "MODEL = 1\n")

    def test_a_notebook_without_the_marker_is_an_error(self) -> None:
        with self.assertRaises(RuntimeError) as raised:
            runner.parameterize_notebook(
                marker_cell("MODEL = 1\n"),
                ("MODEL",),
                label="deploy_nemoclaw.ipynb",
            )
        self.assertIn("deploy_nemoclaw.ipynb", str(raised.exception))

    def test_the_checked_in_notebooks_still_carry_the_marker(self) -> None:
        # The marker is a string match against notebooks that are edited by
        # hand, so drift here silently disables every override.
        for name in runner.NOTEBOOK_PARAMETERS:
            path = SCRIPTS_DIR / name
            notebook = json.loads(path.read_text(encoding="utf-8"))
            parameters = runner.parameters_for(path)
            runner.parameterize_notebook(
                notebook, parameters, label=name
            )
            injected = [
                cell["source"]
                for cell in notebook["cells"]
                if "run_setup_notebook" in str(cell.get("source", ""))
            ]
            if not parameters:
                self.assertEqual(injected, [], name)
                continue
            self.assertEqual(len(injected), 1, name)
            for parameter in parameters:
                self.assertIn(f"{parameter} = _vss_setup_os.environ.get", injected[0])


class HitlLaunchContractTests(unittest.TestCase):
    HITL_TOOL_TYPES = frozenset(
        {"lvs_config_media", "lvs_video_understanding", "video_report_gen"}
    )

    @staticmethod
    def _sources(name: str) -> dict[str, str]:
        path = SCRIPTS_DIR / name
        notebook = json.loads(path.read_text(encoding="utf-8"))
        return {
            cell["id"]: "".join(cell.get("source", "")) for cell in notebook["cells"]
        }

    def test_nemoclaw_defaults_to_normal_chat_questions(self) -> None:
        sources = self._sources("deploy_nemoclaw.ipynb")
        settings = sources["e67f6da4"]
        renderer = sources["s33-code"]

        self.assertIn("HITL_ENABLED = False", settings)
        self.assertIn('SHELL_ENV.get("HITL_ENABLED", "")', settings)
        self.assertIn('r"^export HITL_ENABLED=.*$"', renderer)
        self.assertIn("str(HITL_ENABLED).lower()", renderer)

    def test_external_adapter_forces_hitl_off_in_vss_deployment(self) -> None:
        sources = self._sources("deploy_vss_orchestrator.ipynb")
        settings = sources["20b35654"]
        server = sources["042eabd1"]

        self.assertIn("HITL_ENABLED = False", settings)
        self.assertIn(
            "if VSS_AGENT_ADAPTER_ENABLED:\n    HITL_ENABLED = False",
            settings,
        )
        self.assertIn(
            'env["HITL_ENABLED"] = "true" if HITL_ENABLED else "false"',
            server,
        )

    def test_external_adapter_ignores_a_requested_structured_hitl_mode(self) -> None:
        sources = self._sources("deploy_vss_orchestrator.ipynb")
        namespace: dict[str, object] = {}
        environment = {
            "VSS_AGENT_ADAPTER_ENABLED": "true",
            "VSS_AGENT_BACKEND_URL": "ws://agent.internal:18789",
            "VSS_AGENT_BACKEND_TOKEN": "test-token",
            "HITL_ENABLED": "true",
        }

        with (
            mock.patch.dict(os.environ, environment, clear=True),
            mock.patch("subprocess.check_output", return_value="192.0.2.10\n"),
            mock.patch("builtins.print"),
        ):
            for cell_id in ("7db6e569", "20b35654"):
                exec(  # noqa: S102 - executes checked-in notebook settings cells.
                    compile(
                        sources[cell_id], f"deploy_vss_orchestrator:{cell_id}", "exec"
                    ),
                    namespace,
                )

        self.assertIs(namespace["VSS_AGENT_ADAPTER_ENABLED"], True)
        self.assertIs(namespace["HITL_ENABLED"], False)
        self.assertEqual(namespace["VSS_AGENT_BACKEND_TOKEN"], "test-token")

    def test_external_adapter_requires_an_explicit_backend_url(self) -> None:
        sources = self._sources("deploy_vss_orchestrator.ipynb")
        namespace: dict[str, object] = {}

        with (
            mock.patch.dict(
                os.environ, {"VSS_AGENT_ADAPTER_ENABLED": "true"}, clear=True
            ),
            mock.patch("subprocess.check_output", return_value="192.0.2.10\n"),
            mock.patch("builtins.print"),
            self.assertRaisesRegex(
                ValueError,
                "VSS_AGENT_BACKEND_URL is required.*reachable from the VSS UI containers",
            ),
        ):
            for cell_id in ("7db6e569", "20b35654"):
                exec(  # noqa: S102 - executes checked-in notebook settings cells.
                    compile(
                        sources[cell_id], f"deploy_vss_orchestrator:{cell_id}", "exec"
                    ),
                    namespace,
                )

    def test_external_adapter_preserves_environment_backend_settings(self) -> None:
        sources = self._sources("deploy_vss_orchestrator.ipynb")
        namespace: dict[str, object] = {}
        environment = {
            "VSS_AGENT_ADAPTER_ENABLED": "true",
            "VSS_AGENT_BACKEND_PROTOCOL": "responses",
            "VSS_AGENT_BACKEND_URL": "http://agent.local:8642",
            "VSS_AGENT_BACKEND_PATH": "/v1/responses",
            "VSS_AGENT_BACKEND_TOKEN": "test-token",
        }

        with (
            mock.patch.dict(os.environ, environment, clear=True),
            mock.patch("subprocess.check_output", return_value="192.0.2.10\n"),
            mock.patch("builtins.print"),
        ):
            for cell_id in ("7db6e569", "20b35654"):
                exec(  # noqa: S102 - executes checked-in notebook settings cells.
                    compile(
                        sources[cell_id], f"deploy_vss_orchestrator:{cell_id}", "exec"
                    ),
                    namespace,
                )

        self.assertEqual(namespace["VSS_AGENT_BACKEND_PROTOCOL"], "responses")
        self.assertEqual(namespace["VSS_AGENT_BACKEND_URL"], "http://agent.local:8642")
        self.assertEqual(namespace["VSS_AGENT_BACKEND_PATH"], "/v1/responses")
        self.assertEqual(namespace["VSS_AGENT_BACKEND_TOKEN"], "test-token")
        self.assertIn(
            'env["VSS_AGENT_BACKEND_PATH"] = VSS_AGENT_BACKEND_PATH',
            sources["042eabd1"],
        )
        self.assertNotIn('env["VSS_AGENT_BACKEND_PATH"] = "/"', sources["042eabd1"])

    def test_vss_agent_can_explicitly_opt_in_to_structured_hitl(self) -> None:
        sources = self._sources("deploy_vss_orchestrator.ipynb")
        namespace: dict[str, object] = {}
        environment = {
            "VSS_AGENT_ADAPTER_ENABLED": "false",
            "HITL_ENABLED": "true",
        }

        with (
            mock.patch.dict(os.environ, environment, clear=True),
            mock.patch("subprocess.check_output", return_value="192.0.2.10\n"),
            mock.patch("builtins.print"),
        ):
            for cell_id in ("7db6e569", "20b35654"):
                exec(  # noqa: S102 - executes checked-in notebook settings cells.
                    compile(
                        sources[cell_id], f"deploy_vss_orchestrator:{cell_id}", "exec"
                    ),
                    namespace,
                )

        self.assertIs(namespace["VSS_AGENT_ADAPTER_ENABLED"], False)
        self.assertIs(namespace["HITL_ENABLED"], True)

    def test_workspace_instructions_override_structured_question_tools(self) -> None:
        repo = runner.repo_root()
        environment = (
            repo / ".openclaw" / "workspace" / "_nemoclaw" / "ENV.md"
        ).read_text(encoding="utf-8")
        instructions = (
            repo / ".openclaw" / "workspace" / "_nemoclaw" / "AGENTS.md"
        ).read_text(encoding="utf-8")
        self.assertIn("export HITL_ENABLED=false", environment)
        self.assertIn("never invoke `AskUserQuestion`", instructions)
        self.assertIn("ordinary assistant text", instructions)

    def test_every_shipped_hitl_tool_config_is_opt_in(self) -> None:
        repo = runner.repo_root()
        found_types: set[str] = set()

        for root in (repo / "deploy" / "docker", repo / "deploy" / "helm"):
            for path in root.rglob("*.yml"):
                lines = path.read_text(encoding="utf-8").splitlines()
                for index, line in enumerate(lines):
                    stripped = line.strip()
                    if not stripped.startswith("_type: "):
                        continue
                    tool_type = stripped.removeprefix("_type: ")
                    if tool_type not in self.HITL_TOOL_TYPES:
                        continue

                    found_types.add(tool_type)
                    type_indent = len(line) - len(line.lstrip())
                    block_end = len(lines)
                    for candidate_index in range(index + 1, len(lines)):
                        candidate = lines[candidate_index]
                        if not candidate.strip():
                            continue
                        candidate_indent = len(candidate) - len(candidate.lstrip())
                        if candidate_indent < type_indent:
                            block_end = candidate_index
                            break
                    block = "\n".join(lines[index:block_end])
                    self.assertIn(
                        "hitl_enabled: ${HITL_ENABLED:-false}",
                        block,
                        f"{path.relative_to(repo)}:{index + 1}",
                    )

        self.assertEqual(found_types, self.HITL_TOOL_TYPES)

    def test_docker_uses_one_opt_in_for_agent_and_both_ui_surfaces(self) -> None:
        repo = runner.repo_root()
        agent_compose = (
            repo / "deploy" / "docker" / "services" / "agent" / "compose.yml"
        ).read_text(encoding="utf-8")
        ui_compose = (
            repo / "deploy" / "docker" / "services" / "ui" / "compose.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("HITL_ENABLED: ${HITL_ENABLED:-false}", agent_compose)
        self.assertIn(
            "NEXT_PUBLIC_ENABLE_HITL: "
            "${NEXT_PUBLIC_ENABLE_HITL:-${HITL_ENABLED:-false}}",
            ui_compose,
        )
        self.assertIn(
            "NEXT_PUBLIC_SIDEBAR_CHAT_ENABLE_HITL: "
            "${NEXT_PUBLIC_SIDEBAR_CHAT_ENABLE_HITL:-"
            "${NEXT_PUBLIC_ENABLE_HITL:-${HITL_ENABLED:-false}}}",
            ui_compose,
        )

        override_paths = [
            *(repo / "deploy" / "docker" / "developer-profiles").glob(
                "dev-profile-*/overrides.env"
            ),
            *(repo / "deploy" / "docker" / "industry-profiles").glob("*/overrides.env"),
        ]
        self.assertTrue(override_paths)
        for path in override_paths:
            self.assertIn(
                "HITL_ENABLED=${HITL_ENABLED:-false}",
                path.read_text(encoding="utf-8"),
                str(path.relative_to(repo)),
            )

    def test_helm_defaults_agent_and_ui_hitl_off(self) -> None:
        repo = runner.repo_root()
        agent_chart = (
            repo / "deploy" / "helm" / "services" / "agent" / "charts" / "agent"
        )
        agent_values = (agent_chart / "values.yaml").read_text(encoding="utf-8")
        agent_deployment = (agent_chart / "templates" / "deployment.yaml").read_text(
            encoding="utf-8"
        )
        ui_values = (
            repo / "deploy" / "helm" / "services" / "ui" / "values.yaml"
        ).read_text(encoding="utf-8")

        self.assertIn("hitlEnabled: false", agent_values)
        self.assertIn("- name: HITL_ENABLED", agent_deployment)
        self.assertIn(".Values.hitlEnabled | default false", agent_deployment)
        self.assertIn('- name: NEXT_PUBLIC_ENABLE_HITL\n    value: "false"', ui_values)
        self.assertIn(
            '- name: NEXT_PUBLIC_SIDEBAR_CHAT_ENABLE_HITL\n    value: "false"',
            ui_values,
        )


class NemoClawNotebookContractTests(unittest.TestCase):
    def test_blank_tool_disclosure_clears_a_previous_notebook_run(self) -> None:
        notebook = json.loads(
            (SCRIPTS_DIR / "deploy_nemoclaw.ipynb").read_text(encoding="utf-8")
        )
        settings = next(
            "".join(cell.get("source", []))
            for cell in notebook["cells"]
            if "NEMOCLAW_TOOL_DISCLOSURE = SHELL_ENV.get" in "".join(
                cell.get("source", [])
            )
        )
        namespace = {
            "_NOTEBOOK_SHELL_ENV": {},
            "NVIDIA_API_KEY": "",
            "NEMOCLAW_PROVIDER": "",
            "NEMOCLAW_ENDPOINT_URL": "",
            "NEMOCLAW_MODEL": "anthropic/claude-opus",
            "COMPATIBLE_API_KEY": "",
        }
        with (
            mock.patch.dict(
                os.environ, {"NEMOCLAW_TOOL_DISCLOSURE": "direct"}, clear=True
            ),
            mock.patch("subprocess.check_output", return_value="test-token"),
            redirect_stdout(io.StringIO()),
        ):
            exec(  # noqa: S102 - executes a checked-in notebook settings cell.
                compile(settings, "deploy_nemoclaw.ipynb:settings", "exec"), namespace
            )
            self.assertNotIn("NEMOCLAW_TOOL_DISCLOSURE", os.environ)
        self.assertEqual(namespace["NEMOCLAW_TOOL_DISCLOSURE"], "")


class NemoClawForwardContractTests(unittest.TestCase):
    HOST_GATEWAY = "192.0.2.44"
    PORT = 18789
    SANDBOX = "demo"

    @classmethod
    def setUpClass(cls) -> None:
        path = SCRIPTS_DIR / "deploy_nemoclaw.ipynb"
        notebook = json.loads(path.read_text(encoding="utf-8"))
        source = next(
            "".join(cell["source"])
            for cell in notebook["cells"]
            if cell["id"] == "s37-ui-code"
        )
        cls.source = source.replace(
            "    !setsid -f openshell forward start --background {_fwd} "
            "{NEMOCLAW_SANDBOX_NAME}\n",
            "    _start_forward(_fwd, NEMOCLAW_SANDBOX_NAME)\n",
        )
        verify_source = next(
            "".join(cell["source"])
            for cell in notebook["cells"]
            if cell["id"] == "verify-code"
        )
        hooks_start = verify_source.index("if AGENT_HOOKS_ENABLED:\n")
        hooks_end = verify_source.index("\nif gateway_container:\n", hooks_start)
        cls.hooks_source = verify_source[hooks_start:hooks_end]

    def _run_ui_cell(
        self,
        *,
        adapter_enabled: bool,
        chat_fqdn: str | None,
        existing_bind: str | None,
        brev_env_id: str | None = None,
        gateway_lookup_fails: bool = False,
    ) -> tuple[dict[str, object], list[tuple[str, str]], list[tuple[str, ...]]]:
        state = {"bind": existing_bind}
        starts: list[tuple[str, str]] = []
        calls: list[tuple[str, ...]] = []

        def completed(
            command: list[str], returncode: int = 0, stdout: str = "", stderr: str = ""
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, returncode, stdout, stderr)

        def run(
            command: list[str], **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            calls.append(tuple(command))
            if command[:3] == ["docker", "exec", "vss-agent-ui"]:
                if gateway_lookup_fails:
                    return completed(command, 1, stderr="host alias unavailable")
                return completed(command, stdout=f"{self.HOST_GATEWAY}\n")
            if command[:3] == ["openshell", "forward", "list"]:
                bind = state["bind"]
                row = (
                    f"{self.SANDBOX} {bind} {self.PORT} 4242 running\n" if bind else ""
                )
                return completed(command, stdout=row)
            if command[:2] == ["lsof", "-t"]:
                return completed(command, stdout="4242\n" if state["bind"] else "")
            if command[:2] == ["ps", "-p"]:
                return completed(command, stdout="ssh --sandbox-id sandbox-id\n")
            if command[:3] == ["openshell", "sandbox", "get"]:
                return completed(command, stdout="Id: sandbox-id\n")
            if command[0] == "curl":
                host = command[-1].split("://", 1)[1].rsplit(":", 1)[0]
                reachable = state["bind"] in ("0.0.0.0", host)
                return completed(command, 0 if reachable else 7)
            if command[:3] == ["openshell", "forward", "stop"]:
                state["bind"] = None
                return completed(command)
            if command[0] == "kill":
                state["bind"] = None
                return completed(command)
            if command[:2] == ["hostname", "-I"]:
                return completed(command, stdout="192.0.2.10\n")
            raise AssertionError(f"unexpected command: {command}")

        def start_forward(forward: str, sandbox: str) -> None:
            starts.append((forward, sandbox))
            state["bind"] = forward.rsplit(":", 1)[0]

        namespace: dict[str, object] = {
            "AGENT_CONNECT_CMD": "",
            "AGENT_DASHBOARD_PORT": self.PORT,
            "AGENT_DASHBOARD_URL_CMD": "",
            "AGENT_GATEWAY_TOKEN_CMD": "",
            "AGENT_LABEL": "OpenClaw",
            "AGENT_UI_USES_GATEWAY_TOKEN": False,
            "BREV_ENVIRONMENT_CONTEXT_PATH": "",
            "NEMOCLAW_SANDBOX_NAME": self.SANDBOX,
            "VSS_AGENT_ADAPTER_ENABLED": adapter_enabled,
            "_start_forward": start_forward,
            "brev_environment_id": lambda: brev_env_id,
            "brev_secure_link_fqdn": lambda _port: chat_fqdn,
            "ipaddress": ipaddress,
            "resolve_openshell_gateway_container": lambda _sandbox: "gateway",
        }
        with (
            mock.patch("subprocess.run", side_effect=run),
            mock.patch("builtins.print"),
        ):
            exec(  # noqa: S102 - executes a checked-in notebook cell with mocked I/O.
                compile(self.source, "deploy_nemoclaw:s37-ui-code", "exec"),
                namespace,
            )
        return namespace, starts, calls

    def test_non_brev_without_adapter_keeps_loopback(self) -> None:
        namespace, starts, calls = self._run_ui_cell(
            adapter_enabled=False, chat_fqdn=None, existing_bind="127.0.0.1"
        )
        self.assertEqual(namespace["_desired_bind"], "127.0.0.1")
        self.assertEqual(namespace["_health"], f"http://127.0.0.1:{self.PORT}/health")
        self.assertEqual(starts, [])
        self.assertFalse(any(command[0] == "docker" for command in calls))

    def test_brev_keeps_wildcard_without_host_gateway_lookup(self) -> None:
        namespace, starts, calls = self._run_ui_cell(
            adapter_enabled=True,
            chat_fqdn="agent.example.test",
            existing_bind="0.0.0.0",
        )
        self.assertEqual(namespace["_desired_bind"], "0.0.0.0")
        self.assertEqual(namespace["_health"], f"http://127.0.0.1:{self.PORT}/health")
        self.assertEqual(starts, [])
        self.assertFalse(any(command[0] == "docker" for command in calls))

    def test_unreadable_brev_context_keeps_wildcard(self) -> None:
        namespace, starts, calls = self._run_ui_cell(
            adapter_enabled=True,
            chat_fqdn=None,
            brev_env_id="brev-env",
            existing_bind="0.0.0.0",
        )
        self.assertEqual(namespace["_desired_bind"], "0.0.0.0")
        self.assertEqual(starts, [])
        self.assertFalse(any(command[0] == "docker" for command in calls))

    def test_non_brev_adapter_uses_vss_ui_host_gateway(self) -> None:
        namespace, starts, calls = self._run_ui_cell(
            adapter_enabled=True, chat_fqdn=None, existing_bind=self.HOST_GATEWAY
        )
        self.assertEqual(namespace["_desired_bind"], self.HOST_GATEWAY)
        self.assertEqual(
            namespace["_health"], f"http://{self.HOST_GATEWAY}:{self.PORT}/health"
        )
        self.assertEqual(starts, [])
        self.assertTrue(
            any(command[:3] == ("docker", "exec", "vss-agent-ui") for command in calls)
        )

    def test_host_gateway_discovery_failure_stops_the_cell(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "Could not resolve host.docker.internal inside vss-agent-ui: "
            "host alias unavailable",
        ):
            self._run_ui_cell(
                adapter_enabled=True,
                chat_fqdn=None,
                existing_bind="127.0.0.1",
                gateway_lookup_fails=True,
            )

    def test_wrong_bind_is_replaced_with_vss_ui_host_gateway(self) -> None:
        namespace, starts, calls = self._run_ui_cell(
            adapter_enabled=True, chat_fqdn=None, existing_bind="127.0.0.1"
        )
        self.assertEqual(namespace["_bind"], "127.0.0.1")
        self.assertEqual(
            starts, [(f"{self.HOST_GATEWAY}:{self.PORT}", self.SANDBOX)]
        )
        self.assertEqual(namespace["_held"], (self.HOST_GATEWAY, "4242"))
        self.assertIn(
            ("openshell", "forward", "stop", str(self.PORT), self.SANDBOX), calls
        )

    def test_hooks_verification_uses_rebound_host_gateway_without_proxy(self) -> None:
        namespace, starts, _calls = self._run_ui_cell(
            adapter_enabled=True, chat_fqdn=None, existing_bind="127.0.0.1"
        )
        hook_calls: list[list[str]] = []

        def run(
            command: list[str], **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            hook_calls.append(command)
            return subprocess.CompletedProcess(command, 0, "{}\n200", "")

        namespace.update(
            {
                "AGENT_HOOKS_ENABLED": True,
                "AGENT_HOOKS_PATH": "/hooks",
                "AGENT_HOOKS_TOKEN": "test-token",
                "AGENT_RUNTIME": "openclaw",
                "json": json,
            }
        )
        with (
            mock.patch("subprocess.run", side_effect=run),
            mock.patch("builtins.print"),
        ):
            exec(  # noqa: S102 - executes the checked-in hooks verification block.
                compile(
                    self.hooks_source,
                    "deploy_nemoclaw:verify-code:hooks",
                    "exec",
                ),
                namespace,
            )

        self.assertEqual(
            starts, [(f"{self.HOST_GATEWAY}:{self.PORT}", self.SANDBOX)]
        )
        self.assertEqual(len(hook_calls), 1)
        self.assertEqual(hook_calls[0][:4], ["curl", "-sS", "--noproxy", "*"])
        self.assertIn(
            f"http://{self.HOST_GATEWAY}:{self.PORT}/hooks/agent", hook_calls[0]
        )


class NemoRelayNotebookContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.path = SCRIPTS_DIR / "deploy_nemo_relay.ipynb"
        cls.notebook = json.loads(cls.path.read_text(encoding="utf-8"))
        cls.sources = {
            cell["id"]: "".join(cell["source"]) for cell in cls.notebook["cells"]
        }

    def test_relay_is_a_separate_line_formatted_notebook(self) -> None:
        parent = (SCRIPTS_DIR / "deploy_nemoclaw.ipynb").read_text(encoding="utf-8")
        self.assertIn("deploy_nemo_relay.ipynb", parent)
        self.assertNotIn("nemo-relay-openclaw", parent)
        for cell in self.notebook["cells"]:
            self.assertIsInstance(cell["source"], list)
            self.assertTrue(all(line.count("\n") <= 1 for line in cell["source"]))
            if cell["cell_type"] == "code":
                self.assertIsNone(cell["execution_count"])
                self.assertEqual(cell["outputs"], [])
                compile("".join(cell["source"]), f"{self.path}:{cell['id']}", "exec")

    def test_install_targets_the_active_pinned_plugin_generation(self) -> None:
        settings = self.sources["relay-settings"]
        preflight = self.sources["relay-preflight"]
        configure = self.sources["relay-configure"]
        self.assertNotIn('"openshell"', preflight)
        self.assertIn(
            '["nemoclaw", NEMOCLAW_SANDBOX_NAME, "status", "--json"]',
            preflight,
        )
        self.assertIn("result = sandbox_exec(probe", preflight)
        self.assertIn('RELAY_RELEASE = "0.7.3"', settings)
        self.assertIn("npm:nemo-relay-openclaw@{RELAY_RELEASE}", settings)
        self.assertIn('"policy", "add", "npm", "--yes"', configure)
        self.assertIn("openclaw plugins install", configure)
        self.assertIn("--force", configure)
        self.assertIn("openclaw plugins inspect nemo-relay --json", configure)
        self.assertIn(
            "nemo-relay-node-linux-$(node -p process.arch)-gnu@{RELAY_RELEASE}",
            configure,
        )
        self.assertIn("openclaw plugins disable nemo-relay", configure)
        self.assertIn("openclaw plugins inspect nemo-relay --runtime --json", configure)
        self.assertNotIn("plugins.allow", configure)

    def test_capture_configuration_keeps_call_payloads_out(self) -> None:
        configure = self.sources["relay-configure"]
        for setting in ("includePrompts", "includeResponses"):
            self.assertIn(f'"{setting}": False', configure)
        for setting in ("stripToolArgs", "stripToolResults"):
            self.assertIn(f'"{setting}": True', configure)
        self.assertIn('"allowConversationAccess": True', configure)
        self.assertIn('"opentelemetry": {"enabled": False}', configure)
        self.assertIn('"output_directory": RELAY_ATIF_DIR', configure)

    def test_environment_can_disable_the_default(self) -> None:
        notebook = json.loads(self.path.read_text(encoding="utf-8"))
        settings = next(
            cell for cell in notebook["cells"] if cell["id"] == "relay-settings"
        )
        namespace: dict[str, object] = {}
        with mock.patch.dict(os.environ, {"RELAY_OBSERVABILITY": "false"}, clear=True):
            exec(  # noqa: S102 - executes a checked-in notebook settings cell.
                compile(
                    "".join(settings["source"]),
                    f"{self.path}:relay-settings",
                    "exec",
                ),
                namespace,
            )
        self.assertIs(namespace["RELAY_OBSERVABILITY"], False)


class OutputTests(unittest.TestCase):
    def test_collects_stream_and_result_payloads(self) -> None:
        notebook = {
            "cells": [
                {
                    "outputs": [
                        {"output_type": "stream", "text": "Agent UI: http://x\n"},
                        {
                            "output_type": "execute_result",
                            "data": {"text/plain": "'ready'"},
                        },
                        {"output_type": "error", "evalue": "ignored"},
                    ]
                }
            ]
        }
        text = runner.output_text(notebook)
        self.assertIn("Agent UI: http://x", text)
        self.assertIn("ready", text)
        self.assertNotIn("ignored", text)

    def test_a_notebook_with_no_outputs_is_empty_not_an_error(self) -> None:
        self.assertEqual(runner.output_text({"cells": [{}]}), "")

    def test_require_output_accepts_a_marker_that_was_printed(self) -> None:
        notebook = {
            "cells": [{"outputs": [{"output_type": "stream", "text": "SANDBOX READY"}]}]
        }
        runner.require_output(notebook, "SANDBOX READY", notebook_name="nb.ipynb")

    def test_require_output_rejects_a_run_that_skipped_the_step(self) -> None:
        with self.assertRaises(RuntimeError) as raised:
            runner.require_output(
                {"cells": []}, "SANDBOX READY", notebook_name="nb.ipynb"
            )
        self.assertIn("nb.ipynb", str(raised.exception))
        self.assertIn("SANDBOX READY", str(raised.exception))


class RunNotebooksTests(unittest.TestCase):
    @staticmethod
    def _streamed(text: str) -> dict:
        return {"cells": [{"outputs": [{"output_type": "stream", "text": text}]}]}

    def test_a_missing_notebook_fails_before_any_kernel_starts(self) -> None:
        with (
            mock.patch.object(runner, "execute_notebook") as execute,
            self.assertRaises(FileNotFoundError),
        ):
            runner.run_notebooks(
                [SCRIPTS_DIR / "deploy_nemoclaw.ipynb", Path("/nonexistent.ipynb")],
                cwd=SCRIPTS_DIR,
                timeout=600,
            )
        execute.assert_not_called()

    def test_executes_in_the_order_given(self) -> None:
        first = SCRIPTS_DIR / "deploy_nemoclaw.ipynb"
        second = SCRIPTS_DIR / "deploy_nemo_relay.ipynb"
        third = SCRIPTS_DIR / "deploy_vss_orchestrator.ipynb"
        with mock.patch.object(
            runner, "execute_notebook", return_value=self._streamed("")
        ) as execute:
            runner.run_notebooks([first, second, third], cwd=SCRIPTS_DIR, timeout=600)
        self.assertEqual(
            [call.args[0] for call in execute.call_args_list], [first, second, third]
        )

    def test_a_marker_may_be_printed_by_any_notebook_in_the_run(self) -> None:
        outputs = [self._streamed("nothing here"), self._streamed("SANDBOX READY")]
        with mock.patch.object(runner, "execute_notebook", side_effect=outputs):
            runner.run_notebooks(
                [
                    SCRIPTS_DIR / "deploy_nemoclaw.ipynb",
                    SCRIPTS_DIR / "deploy_vss_orchestrator.ipynb",
                ],
                cwd=SCRIPTS_DIR,
                timeout=600,
                required_output=("SANDBOX READY",),
            )

    def test_every_absent_marker_is_reported_at_once(self) -> None:
        with (
            mock.patch.object(
                runner, "execute_notebook", return_value=self._streamed("SANDBOX READY")
            ),
            self.assertRaises(RuntimeError) as raised,
        ):
            runner.run_notebooks(
                [SCRIPTS_DIR / "deploy_nemoclaw.ipynb"],
                cwd=SCRIPTS_DIR,
                timeout=600,
                required_output=("SANDBOX READY", "MCP READY", "UI READY"),
            )
        self.assertIn("MCP READY", str(raised.exception))
        self.assertIn("UI READY", str(raised.exception))
        self.assertNotIn("SANDBOX READY", str(raised.exception))


class CommandLineTests(unittest.TestCase):
    def test_forwards_the_notebooks_cwd_timeout_and_markers(self) -> None:
        notebook = SCRIPTS_DIR / "deploy_nemoclaw.ipynb"
        with mock.patch.object(runner, "run_notebooks") as run_notebooks:
            exit_code = runner.main(
                [
                    "--notebook",
                    str(notebook),
                    "--cwd",
                    str(SCRIPTS_DIR),
                    "--timeout",
                    "900",
                    "--require-output",
                    "SANDBOX READY",
                ]
            )
        self.assertEqual(exit_code, 0)
        run_notebooks.assert_called_once()
        self.assertEqual(run_notebooks.call_args.args[0], [notebook])
        self.assertEqual(run_notebooks.call_args.kwargs["cwd"], SCRIPTS_DIR)
        self.assertEqual(run_notebooks.call_args.kwargs["timeout"], 900)
        self.assertEqual(
            run_notebooks.call_args.kwargs["required_output"], ("SANDBOX READY",)
        )

    def test_defaults_the_kernel_directory_to_the_repository_root(self) -> None:
        with mock.patch.object(runner, "run_notebooks") as run_notebooks:
            runner.main(["--notebook", str(SCRIPTS_DIR / "deploy_nemoclaw.ipynb")])
        self.assertEqual(run_notebooks.call_args.kwargs["cwd"], runner.repo_root())

    def test_a_notebook_with_no_contract_is_rejected_before_execution(self) -> None:
        with (
            mock.patch.object(runner, "run_notebooks") as run_notebooks,
            self.assertRaises(SystemExit),
        ):
            runner.main(["--notebook", str(SCRIPTS_DIR / "deploy_unknown.ipynb")])
        run_notebooks.assert_not_called()

    def test_an_absent_notebook_is_rejected_before_execution(self) -> None:
        with (
            mock.patch.object(runner, "run_notebooks") as run_notebooks,
            mock.patch.object(runner, "parameters_for", return_value=()),
            self.assertRaises(SystemExit),
        ):
            runner.main(["--notebook", "/nonexistent/deploy_nemoclaw.ipynb"])
        run_notebooks.assert_not_called()

    def test_a_timeout_too_short_for_a_setup_cell_is_rejected(self) -> None:
        with (
            mock.patch.object(runner, "run_notebooks") as run_notebooks,
            self.assertRaises(SystemExit),
        ):
            runner.main(
                [
                    "--notebook",
                    str(SCRIPTS_DIR / "deploy_nemoclaw.ipynb"),
                    "--timeout",
                    "1",
                ]
            )
        run_notebooks.assert_not_called()


class NoPersistTests(unittest.TestCase):
    def test_the_runner_never_writes_an_executed_notebook_back(self) -> None:
        # Executed notebooks hold the credentials the caller passed in, so the
        # guarantee is that nothing reaches the checkout.
        source = RUNNER_PATH.read_text(encoding="utf-8")
        self.assertNotIn("nbformat.write", source)
        self.assertIn("outputs were not persisted", source)


if __name__ == "__main__":
    unittest.main()
