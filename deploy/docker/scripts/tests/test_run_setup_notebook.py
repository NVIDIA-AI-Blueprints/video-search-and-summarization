# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import builtins
import importlib.util
import io
import json
import os
import re
import subprocess
import symtable
import sys
import tempfile
import unittest
from contextlib import nullcontext, redirect_stdout
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


def without_ipython_magics(source: str) -> str:
    """*source* with IPython's `!` and `%` lines reduced to plain Python."""

    kept = []
    for line in source.splitlines():
        if line.lstrip().startswith(("!", "%")):
            continue
        kept.append(re.sub(r"=\s*!.*", "= None", line))
    return "\n".join(kept)


def referenced_globals(table: symtable.SymbolTable) -> set[str]:
    """Names *table* and its nested scopes read from the enclosing namespace.

    A function's own locals and parameters are not global, so they drop out
    here while a global the body reads is kept, whichever scope reads it.
    """

    names = {
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_referenced() and symbol.is_global()
    }
    for child in table.get_children():
        names |= referenced_globals(child)
    return names


def notebook_names(source: str) -> tuple[set[str], set[str]]:
    """The global names *source* reads and the names it binds at module scope.

    Bindings stay at module scope, so a local or a parameter never stands in
    for a notebook-level definition.
    """

    table = symtable.symtable(without_ipython_magics(source), "notebook", "exec")
    bound = {
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_assigned() or symbol.is_imported()
    }
    return referenced_globals(table), bound


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
        guidance = sources["d3d4cd3e"]
        settings = sources["20b35654"]
        server = sources["042eabd1"]

        self.assertIn("Structured HITL is enabled by default for `vss-agent`", guidance)
        self.assertIn("Set `HITL_ENABLED=False` to disable it", guidance)
        self.assertIn("HITL_ENABLED = True", settings)
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
        self.assertIn('export HITL_ENABLED="${HITL_ENABLED-false}"', environment)
        self.assertIn("never invoke `AskUserQuestion`", instructions)
        self.assertIn("ordinary assistant text", instructions)

    def test_every_shipped_hitl_tool_config_defaults_on(self) -> None:
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
                        "hitl_enabled: ${HITL_ENABLED:-true}",
                        block,
                        f"{path.relative_to(repo)}:{index + 1}",
                    )

        self.assertEqual(found_types, self.HITL_TOOL_TYPES)

    def test_docker_defaults_hitl_on_for_agent_and_both_ui_surfaces(self) -> None:
        repo = runner.repo_root()
        agent_compose = (
            repo / "deploy" / "docker" / "services" / "agent" / "compose.yml"
        ).read_text(encoding="utf-8")
        ui_compose = (
            repo / "deploy" / "docker" / "services" / "ui" / "compose.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("HITL_ENABLED: ${HITL_ENABLED:-true}", agent_compose)
        self.assertIn(
            "NEXT_PUBLIC_ENABLE_HITL: "
            "${NEXT_PUBLIC_ENABLE_HITL:-${HITL_ENABLED:-true}}",
            ui_compose,
        )
        self.assertIn(
            "NEXT_PUBLIC_SIDEBAR_CHAT_ENABLE_HITL: "
            "${NEXT_PUBLIC_SIDEBAR_CHAT_ENABLE_HITL:-"
            "${NEXT_PUBLIC_ENABLE_HITL:-${HITL_ENABLED:-true}}}",
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
                "HITL_ENABLED=${HITL_ENABLED:-true}",
                path.read_text(encoding="utf-8"),
                str(path.relative_to(repo)),
            )

    def test_helm_defaults_agent_and_ui_hitl_on(self) -> None:
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

        self.assertIn("hitlEnabled: true", agent_values)
        self.assertIn("- name: HITL_ENABLED", agent_deployment)
        # No `| default true`: Sprig's `default` treats an explicit `false`
        # as empty and silently discards it, so a values override could
        # never disable HITL. values.yaml already declares the base default.
        self.assertIn(".Values.hitlEnabled | quote", agent_deployment)
        self.assertIn('- name: NEXT_PUBLIC_ENABLE_HITL\n    value: "true"', ui_values)
        self.assertIn(
            '- name: NEXT_PUBLIC_SIDEBAR_CHAT_ENABLE_HITL\n    value: "true"',
            ui_values,
        )


class NemoClawNotebookContractTests(unittest.TestCase):
    def _run_settings_cell(
        self, shell_env: dict[str, str], environ: dict[str, str] | None = None
    ) -> tuple[dict[str, object], dict[str, str]]:
        """Execute the settings cell with *shell_env* standing in for the shell.

        Returns the cell's namespace and the environment as the cell left it,
        which is only observable while the patched environment is still up.
        """

        notebook = json.loads(
            (SCRIPTS_DIR / "deploy_nemoclaw.ipynb").read_text(encoding="utf-8")
        )
        settings = next(
            "".join(cell.get("source", []))
            for cell in notebook["cells"]
            if "_agent_adapter_raw = (" in "".join(cell.get("source", []))
        )
        namespace: dict[str, object] = {
            "_NOTEBOOK_SHELL_ENV": shell_env,
            "NVIDIA_API_KEY": "",
            "NEMOCLAW_PROVIDER": "",
            "NEMOCLAW_ENDPOINT_URL": "",
            "NEMOCLAW_MODEL": "anthropic/claude-opus",
            "COMPATIBLE_API_KEY": "",
        }
        with (
            mock.patch.dict(os.environ, environ or {}, clear=True),
            mock.patch("subprocess.check_output", return_value="test-token"),
            redirect_stdout(io.StringIO()),
        ):
            exec(  # noqa: S102 - executes a checked-in notebook settings cell.
                compile(settings, "deploy_nemoclaw.ipynb:settings", "exec"), namespace
            )
            return namespace, dict(os.environ)

    def test_blank_tool_disclosure_clears_a_previous_notebook_run(self) -> None:
        namespace, environ = self._run_settings_cell(
            {}, {"NEMOCLAW_TOOL_DISCLOSURE": "direct"}
        )
        self.assertNotIn("NEMOCLAW_TOOL_DISCLOSURE", environ)
        self.assertEqual(namespace["NEMOCLAW_TOOL_DISCLOSURE"], "")

    def test_the_settings_cell_defaults_the_adapter_flag_off(self) -> None:
        namespace, _ = self._run_settings_cell({})
        self.assertIs(namespace["VSS_AGENT_ADAPTER_ENABLED"], False)

    def test_the_shell_can_turn_the_adapter_flag_on(self) -> None:
        # The harness documentation tells operators to export this before running the
        # notebook, so the settings cell has to read it the way HITL_ENABLED does.
        namespace, _ = self._run_settings_cell({"VSS_AGENT_ADAPTER_ENABLED": "true"})
        self.assertIs(namespace["VSS_AGENT_ADAPTER_ENABLED"], True)

    def test_a_non_boolean_adapter_flag_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "VSS_AGENT_ADAPTER_ENABLED must be true or false"
        ):
            self._run_settings_cell({"VSS_AGENT_ADAPTER_ENABLED": "maybe"})

    def test_the_ui_cell_reads_no_name_the_notebook_never_binds(self) -> None:
        """Section 3.5 inherits the namespace the earlier cells built, so a name none
        of them binds is a NameError for every operator. Stubbing such a name into the
        cell's namespace makes a unit test pass over a notebook that cannot run."""

        notebook = json.loads(
            (SCRIPTS_DIR / "deploy_nemoclaw.ipynb").read_text(encoding="utf-8")
        )
        available = set(dir(builtins)) | {"get_ipython", "In", "Out"}
        for cell in notebook["cells"]:
            if cell.get("cell_type") != "code":
                continue
            read, bound = notebook_names("".join(cell["source"]))
            if cell.get("id") != "s37-ui-code":
                available |= bound
                continue
            self.assertEqual(sorted(read - bound - available), [])
            return
        self.fail("deploy_nemoclaw.ipynb has no s37-ui-code cell")


class NemoClawForwardContractTests(unittest.TestCase):
    """Section 3.5's contract: NemoClaw's forward stays on loopback (its recovery
    re-creates it there and refuses a port shared with any other listener), and
    off-loopback clients get a relay on its own port whose bind is resolved at run
    time - Docker's host-gateway address for the containerized UI, the wildcard on
    Brev - and only when such a client exists."""

    HOST_GATEWAY = "192.0.2.44"
    PORT = 18789
    RELAY_PORT = 18790
    SANDBOX = "demo"
    RELAY_SCRIPT = (SCRIPTS_DIR / "nemoclaw" / "dashboard-relay.py").resolve()
    RECOVER = ("nemoclaw", SANDBOX, "recover")
    RECOVER_FAILURE = "another process owns the recorded dashboard port"

    @classmethod
    def setUpClass(cls) -> None:
        path = SCRIPTS_DIR / "deploy_nemoclaw.ipynb"
        notebook = json.loads(path.read_text(encoding="utf-8"))
        cls.source = next(
            "".join(cell["source"])
            for cell in notebook["cells"]
            if cell["id"] == "s37-ui-code"
        )
        verify_source = next(
            "".join(cell["source"])
            for cell in notebook["cells"]
            if cell["id"] == "verify-code"
        )
        hooks_start = verify_source.index("if AGENT_HOOKS_ENABLED:\n")
        hooks_end = verify_source.index("\nif gateway_container:\n", hooks_start)
        cls.hooks_source = verify_source[hooks_start:hooks_end]
        # The real generator, loaded the way the notebook loads it: the origin the
        # cell demands has to be one NemoClaw would have baked.
        spec = importlib.util.spec_from_file_location(
            "apply_onboard_config", SCRIPTS_DIR.parents[2] / ".openclaw" / "apply-onboard-config.py"
        )
        onboard_config = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(onboard_config)
        cls.control_ui = staticmethod(onboard_config.control_ui)

    def _run_ui_cell(
        self,
        *,
        adapter_enabled: bool,
        chat_fqdn: str | None,
        forward_up: bool = True,
        recover_restores_forward: bool = True,
        ui_origin_baked: bool = True,
        brev_env_id: str | None = None,
        gateway_lookup_fails: bool = False,
        relay_running_for: list[str] | None = None,
        relay_dead: bool = False,
        runtime: str = "openclaw",
    ) -> tuple[dict[str, object], list[tuple[str, ...]], list[list[str]]]:
        """Run 3.5 against a fake host. `relay_running_for` is the --listen list of a
        relay already on the relay port (this checkout's script, this sandbox);
        `relay_dead` makes that relay hold the port without answering through it."""

        # The config the image carries: whatever onboard's generator derived from the
        # CHAT_UI_URL 3.1 baked in - this session's secure link, or loopback alone
        # when the sandbox was built without a remote origin.
        built_for = (
            f"https://{chat_fqdn}"
            if chat_fqdn and ui_origin_baked
            else f"http://127.0.0.1:{self.PORT}"
        )
        state = {
            "forward": forward_up,
            "relay": relay_running_for,
            "relay_dead": relay_dead,
            "config": {
                "gateway": {
                    "port": self.PORT,
                    "controlUi": self.control_ui(built_for, self.PORT),
                }
            },
        }
        calls: list[tuple[str, ...]] = []
        relays: list[list[str]] = []
        # Also on the instance, so a run that raises can still be inspected.
        self.calls, self.relays = calls, relays

        def completed(command, returncode=0, stdout="", stderr=""):
            return subprocess.CompletedProcess(command, returncode, stdout, stderr)

        def relay_args():
            return (
                f"python3 {self.RELAY_SCRIPT} --sandbox {self.SANDBOX} --listen "
                f"{','.join(state['relay'])} --port {self.RELAY_PORT} "
                f"--upstream 127.0.0.1:{self.PORT}"
            )

        def run(command, **_kwargs):
            calls.append(tuple(command))
            if command[:3] == ["docker", "inspect", "--format"]:
                return completed(command, stdout="sha256:gateway-image\n")
            if command[:2] == ["docker", "run"]:
                if gateway_lookup_fails:
                    return completed(command, 1, stderr="host alias unavailable")
                return completed(command, stdout=f"{self.HOST_GATEWAY}\n")
            if command[:3] == ["nemoclaw", self.SANDBOX, "recover"]:
                # recover fails closed on its own preflight gates, leaving the
                # forward as it found it.
                if not recover_restores_forward:
                    return completed(command, 1, stderr=f"{self.RECOVER_FAILURE}\n")
                state["forward"] = True
                return completed(command)
            if command[:2] == ["lsof", "-t"]:
                return completed(command, stdout="5151\n" if state["relay"] else "")
            if command[:2] == ["ps", "-p"]:
                return completed(command, stdout=relay_args() + "\n" if state["relay"] else "")
            if command[:3] == ["openshell", "sandbox", "exec"]:
                return completed(command, stdout=json.dumps(state["config"]))
            if command[0] == "curl":
                url = command[-1]
                host, port = url.split("://", 1)[1].rsplit("/", 1)[0].rsplit(":", 1)
                if int(port) == self.PORT:
                    return completed(command, 0 if state["forward"] and host == "127.0.0.1" else 7)
                reachable = state["relay"] is not None and not state["relay_dead"] and (
                    "0.0.0.0" in state["relay"] or host in state["relay"]
                )
                return completed(command, 0 if reachable else 7)
            if command[0] == "kill":
                state["relay"] = None
                return completed(command)
            if command[:2] == ["hostname", "-I"]:
                return completed(command, stdout="192.0.2.10\n")
            raise AssertionError(f"unexpected command: {command}")

        def popen(command, **_kwargs):
            relays.append(list(command))
            listen = command[command.index("--listen") + 1]
            state["relay"] = listen.split(",")
            state["relay_dead"] = False
            process = mock.Mock()
            process.poll.return_value = None
            return process

        namespace: dict[str, object] = {
            "AGENT_CONNECT_CMD": "",
            "AGENT_DASHBOARD_PORT": self.PORT,
            "AGENT_DASHBOARD_RELAY_PORT": self.RELAY_PORT,
            "AGENT_DASHBOARD_URL_CMD": "",
            "AGENT_GATEWAY_TOKEN_CMD": "",
            "AGENT_LABEL": "OpenClaw",
            "AGENT_RUNTIME": runtime,
            "AGENT_UI_USES_GATEWAY_TOKEN": False,
            "BREV_ENVIRONMENT_CONTEXT_PATH": "",
            "DASHBOARD_RELAY_PATH": self.RELAY_SCRIPT,
            "NEMOCLAW_SANDBOX_NAME": self.SANDBOX,
            "Path": lambda p: Path(self._tmp) / Path(p).name,
            "SANDBOX_CONFIG_PATH": "/sandbox/.openclaw/openclaw.json",
            "VSS_AGENT_ADAPTER_ENABLED": adapter_enabled,
            "brev_environment_id": lambda: brev_env_id,
            "brev_secure_link_fqdn": lambda _port: chat_fqdn,
            "resolve_openshell_gateway_container": lambda _sandbox: "gateway",
        }
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("subprocess.run", side_effect=run),
            mock.patch("subprocess.Popen", side_effect=popen),
            mock.patch("time.sleep"),
            mock.patch("builtins.print"),
        ):
            self._tmp = tmp
            exec(  # noqa: S102 - executes a checked-in notebook cell with mocked I/O.
                compile(self.source, "deploy_nemoclaw:s37-ui-code", "exec"),
                namespace,
            )
        return namespace, calls, relays

    def test_the_forward_is_never_re_bound_off_loopback(self) -> None:
        # No configuration makes 3.5 request another bind: it neither starts nor stops
        # the forward, and `nemoclaw recover` restores the recorded port, which is
        # loopback.
        self.assertNotIn("0.0.0.0:{AGENT_DASHBOARD_PORT}", self.source)
        self.assertNotIn("_desired_bind", self.source)

    def test_non_brev_without_adapter_keeps_loopback_and_starts_no_relay(self) -> None:
        namespace, calls, relays = self._run_ui_cell(adapter_enabled=False, chat_fqdn=None)
        self.assertEqual(namespace["_health"], f"http://127.0.0.1:{self.PORT}/health")
        self.assertEqual(namespace["origin"], f"http://127.0.0.1:{self.PORT}")
        self.assertNotIn(self.RECOVER, calls)
        self.assertEqual(relays, [])
        self.assertFalse(namespace["_relay_up"])
        self.assertFalse(any(command[0] == "docker" for command in calls))

    def test_a_dead_forward_is_repaired_by_nemoclaw_recover(self) -> None:
        # 3.5 owns neither the forward nor the port check: a failed health probe hands
        # the repair to NemoClaw, which verifies ownership and restores loopback.
        namespace, calls, _ = self._run_ui_cell(
            adapter_enabled=False, chat_fqdn=None, forward_up=False
        )
        self.assertIn(self.RECOVER, calls)
        self.assertEqual(namespace["origin"], f"http://127.0.0.1:{self.PORT}")

    def test_a_forward_recover_cannot_repair_stops_the_cell(self) -> None:
        # recover's preflight gates are the only account of why it declined, so the
        # cell has to carry its output out; nothing downstream may run on a dead
        # forward, least of all a relay pointed at it.
        with self.assertRaises(RuntimeError) as raised:
            self._run_ui_cell(
                adapter_enabled=True,
                chat_fqdn=None,
                forward_up=False,
                recover_restores_forward=False,
            )
        self.assertIn(f"`nemoclaw {self.SANDBOX} recover` (exit 1)", str(raised.exception))
        self.assertIn(self.RECOVER_FAILURE, str(raised.exception))
        self.assertEqual(self.relays, [])

    def test_brev_relays_on_the_wildcard_without_host_gateway_lookup(self) -> None:
        namespace, calls, relays = self._run_ui_cell(
            adapter_enabled=False, chat_fqdn="agent.example.test"
        )
        self.assertEqual(len(relays), 1)
        self.assertEqual(relays[0][relays[0].index("--listen") + 1], "0.0.0.0")
        self.assertEqual(relays[0][relays[0].index("--upstream") + 1], f"127.0.0.1:{self.PORT}")
        self.assertTrue(namespace["_relay_up"])
        self.assertEqual(namespace["origin"], "https://agent.example.test")
        self.assertFalse(any(command[0] == "docker" for command in calls))

    def test_the_brev_ui_origin_is_read_from_the_image_not_written(self) -> None:
        # Onboard derives gateway.controlUi from CHAT_UI_URL at build time and `config
        # set` refuses gateway.*, so nothing can add the origin to a running sandbox.
        # 3.5 may only read the live config and confirm the image was built for it.
        namespace, calls, relays = self._run_ui_cell(
            adapter_enabled=False, chat_fqdn="agent.example.test"
        )
        execs = [c for c in calls if c[:3] == ("openshell", "sandbox", "exec")]
        self.assertEqual(len(execs), 1)
        self.assertEqual(execs[0][-2:], ("cat", namespace["SANDBOX_CONFIG_PATH"]))
        self.assertEqual(len(relays), 1)
        self.assertTrue(namespace["_relay_up"])

    def test_an_image_built_for_another_ui_origin_stops_the_cell(self) -> None:
        # Without the origin baked in, the gateway answers "Browser origin not
        # allowed" over the secure link, and only a rebuild can add it - so the cell
        # has to stop at the check and name the step that rebuilds, not relay past it.
        with self.assertRaises(AssertionError) as raised:
            self._run_ui_cell(
                adapter_enabled=False,
                chat_fqdn="agent.example.test",
                ui_origin_baked=False,
            )
        self.assertIn(
            "https://agent.example.test is not in the sandbox's allowedOrigins",
            str(raised.exception),
        )
        self.assertIn("NEMOCLAW_RECREATE_SANDBOX = True", str(raised.exception))
        self.assertEqual(self.relays, [])

    def test_hermes_checks_no_ui_origin(self) -> None:
        # Hermes has no controlUi block, so there is nothing for the secure link to
        # check and no reason to read the sandbox's config at all.
        _, calls, relays = self._run_ui_cell(
            adapter_enabled=False, chat_fqdn="agent.example.test", runtime="hermes"
        )
        self.assertFalse(any(c[:3] == ("openshell", "sandbox", "exec") for c in calls))
        self.assertEqual(len(relays), 1)

    def test_unreadable_brev_context_still_relays_on_the_wildcard(self) -> None:
        namespace, calls, relays = self._run_ui_cell(
            adapter_enabled=False, chat_fqdn=None, brev_env_id="brev-env"
        )
        self.assertEqual(relays[0][relays[0].index("--listen") + 1], "0.0.0.0")
        self.assertTrue(namespace["_relay_up"])
        self.assertFalse(any(command[0] == "docker" for command in calls))

    def test_non_brev_adapter_relays_on_the_docker_host_gateway(self) -> None:
        namespace, calls, relays = self._run_ui_cell(adapter_enabled=True, chat_fqdn=None)
        self.assertNotIn(self.RECOVER, calls)
        self.assertEqual(relays[0][relays[0].index("--listen") + 1], self.HOST_GATEWAY)
        self.assertTrue(namespace["_relay_up"])
        # The forward itself stays on loopback, so the printed local link does too.
        self.assertEqual(namespace["origin"], f"http://127.0.0.1:{self.PORT}")
        inspect = next(c for c in calls if c[:3] == ("docker", "inspect", "--format"))
        probe = next(c for c in calls if c[:2] == ("docker", "run"))
        self.assertEqual(inspect[-1], "gateway")
        self.assertIn("host.docker.internal:host-gateway", probe)
        self.assertIn("sha256:gateway-image", probe)
        self.assertIn("--rm", probe)
        self.assertFalse(any("vss-agent-ui" in c for c in calls))

    def test_host_gateway_discovery_failure_stops_the_cell(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError, "Could not resolve Docker's host-gateway mapping: host alias unavailable"
        ):
            self._run_ui_cell(adapter_enabled=True, chat_fqdn=None, gateway_lookup_fails=True)

    def test_a_relay_bound_for_a_previous_run_is_replaced(self) -> None:
        namespace, calls, relays = self._run_ui_cell(
            adapter_enabled=True, chat_fqdn=None, relay_running_for=["0.0.0.0"]
        )
        self.assertIn(("kill", "5151"), calls)
        self.assertEqual(len(relays), 1)
        self.assertEqual(relays[0][relays[0].index("--listen") + 1], self.HOST_GATEWAY)
        self.assertTrue(namespace["_relay_up"])

    def test_a_matching_relay_is_kept(self) -> None:
        namespace, calls, relays = self._run_ui_cell(
            adapter_enabled=True, chat_fqdn=None, relay_running_for=[self.HOST_GATEWAY]
        )
        self.assertEqual(relays, [])
        self.assertNotIn(("kill", "5151"), calls)
        self.assertTrue(namespace["_relay_up"])

    def test_a_matching_relay_that_no_longer_answers_is_replaced(self) -> None:
        # Bound correctly but dead end-to-end: kept relays get the same probe as new ones.
        namespace, calls, relays = self._run_ui_cell(
            adapter_enabled=True, chat_fqdn=None,
            relay_running_for=[self.HOST_GATEWAY], relay_dead=True,
        )
        self.assertIn(("kill", "5151"), calls)
        self.assertEqual(len(relays), 1)
        self.assertTrue(namespace["_relay_up"])

    def test_hooks_verification_posts_to_the_loopback_forward_without_proxy(self) -> None:
        namespace, _, _ = self._run_ui_cell(adapter_enabled=True, chat_fqdn=None)
        hook_calls: list[list[str]] = []

        def run(command, **_kwargs):
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
        with mock.patch("subprocess.run", side_effect=run), mock.patch("builtins.print"):
            exec(  # noqa: S102 - executes the checked-in hooks verification block.
                compile(self.hooks_source, "deploy_nemoclaw:verify-code:hooks", "exec"),
                namespace,
            )
        self.assertEqual(len(hook_calls), 1)
        self.assertEqual(hook_calls[0][:4], ["curl", "-sS", "--noproxy", "*"])
        self.assertIn(f"http://127.0.0.1:{self.PORT}/hooks/agent", hook_calls[0])


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


class TokenRedactionTests(unittest.TestCase):
    """What an echoed log may contain once the gateway token is scrubbed."""

    TOKEN = "cf1ba9d0e5b74c2f8a3b6d91e0472c5d"
    SECOND_TOKEN = "7e42af08b1c34d96b5170ea3c8fd62b1"

    @staticmethod
    def _notebook(*outputs: dict) -> dict:
        return {"cells": [{"outputs": [output]} for output in outputs]}

    @staticmethod
    def _streamed(*texts: str) -> dict:
        return {
            "cells": [
                {"outputs": [{"output_type": "stream", "text": text}]} for text in texts
            ]
        }

    def _echo(self, notebook: dict) -> str:
        stream = io.StringIO()
        with redirect_stdout(stream):
            runner.echo_notebook_output(notebook)
        return stream.getvalue()

    def test_the_whole_token_goes_and_the_link_stays_readable(self) -> None:
        printed = self._echo(
            self._streamed(f"Agent UI: http://192.0.2.10:18789/#token={self.TOKEN}\n")
        )
        self.assertIn("Agent UI: http://192.0.2.10:18789/#token=<redacted>", printed)
        # A pattern that matched only part of the value would leave one end of
        # the secret in the log, so check both ends are gone, not just the URL.
        self.assertNotIn(self.TOKEN, printed)
        self.assertNotIn(self.TOKEN[:8], printed)
        self.assertNotIn(self.TOKEN[-8:], printed)

    def test_every_token_in_the_run_is_redacted(self) -> None:
        printed = self._echo(
            self._notebook(
                {
                    "output_type": "stream",
                    "text": f"Agent UI: http://host-a:18789/#token={self.TOKEN}\n",
                },
                {
                    "output_type": "execute_result",
                    "data": {
                        "text/plain": (
                            f"'http://host-b:18789/#token={self.SECOND_TOKEN}'"
                        )
                    },
                },
            )
        )
        self.assertNotIn(self.TOKEN, printed)
        self.assertNotIn(self.SECOND_TOKEN, printed)
        self.assertEqual(printed.count("#token=<redacted>"), 2)

    def test_redaction_ends_with_the_token_not_the_rest_of_the_line(self) -> None:
        printed = self._echo(
            self._streamed(
                f'{{"url": "http://host:18789/#token={self.TOKEN}"}}\n'
                f'<a href="http://host:18789/#token={self.TOKEN}">open</a>\n'
                f"http://host:18789/#token={self.TOKEN} (paste this)\n"
            )
        )
        self.assertNotIn(self.TOKEN, printed)
        self.assertIn('{"url": "http://host:18789/#token=<redacted>"}', printed)
        self.assertIn('#token=<redacted>">open</a>', printed)
        self.assertIn("#token=<redacted> (paste this)", printed)

    def test_output_that_carries_no_token_is_echoed_as_it_was(self) -> None:
        text = (
            "Sandbox 'nemoclaw-vss' ready.\n"
            "WARNING: the dashboard forward did not come up\n"
        )
        self.assertEqual(self._echo(self._streamed(text)), f"{text}\n")

    def test_a_run_that_printed_nothing_echoes_nothing(self) -> None:
        self.assertEqual(self._echo({"cells": []}), "")
        self.assertEqual(self._echo(self._streamed("\n   \n")), "")


class EchoOutputTests(unittest.TestCase):
    """Whether `execute_notebook` puts the notebook's output in the log."""

    TOKEN = "b93c7f1ad0e4426fa8225c6e3b07d914"
    AGENT_UI = "Agent UI: http://192.0.2.10:18789/#token="

    def _execute(self, *, fail: bool = False, **kwargs: object) -> str:
        """Capture stdout from a run whose kernel is a stub.

        `nbformat` and `nbclient` are absent from the environment CI runs these
        tests in, so they are injected rather than patched in place.
        """

        notebook = {"cells": [{"source": "", "outputs": []}]}

        def execute() -> dict:
            # NotebookClient records outputs on the notebook it was handed, so
            # a failing run still leaves the completed cells' output behind.
            notebook["cells"][0]["outputs"] = [
                {"output_type": "stream", "text": f"{self.AGENT_UI}{self.TOKEN}\n"}
            ]
            if fail:
                raise RuntimeError("cell 3 raised")
            return notebook

        nbformat = mock.Mock()
        nbformat.read.return_value = notebook
        nbclient = mock.Mock()
        nbclient.NotebookClient.return_value.execute.side_effect = execute

        # Pinned to the stub's message: the runner raises RuntimeError for a
        # missing nbformat too, and that would echo nothing for another reason.
        raised = (
            self.assertRaisesRegex(RuntimeError, "cell 3 raised")
            if fail
            else nullcontext()
        )
        stream = io.StringIO()
        with (
            mock.patch.dict(sys.modules, {"nbformat": nbformat, "nbclient": nbclient}),
            redirect_stdout(stream),
            raised,
        ):
            runner.execute_notebook(
                SCRIPTS_DIR / "deploy_nemoclaw.ipynb",
                cwd=SCRIPTS_DIR,
                timeout=600,
                parameters=(),
                **kwargs,
            )
        return stream.getvalue()

    def test_a_default_run_reports_the_summary_and_nothing_else(self) -> None:
        printed = self._execute()
        self.assertIn("outputs were not persisted", printed)
        self.assertNotIn("Agent UI", printed)
        self.assertNotIn(self.TOKEN, printed)

    def test_an_opt_in_run_echoes_the_output_with_the_token_redacted(self) -> None:
        printed = self._execute(echo_output=True)
        self.assertIn(f"{self.AGENT_UI}<redacted>", printed)
        self.assertNotIn(self.TOKEN, printed)
        self.assertIn("outputs were not persisted", printed)

    def test_a_failed_cell_still_echoes_what_the_run_completed(self) -> None:
        printed = self._execute(fail=True, echo_output=True)
        self.assertIn(f"{self.AGENT_UI}<redacted>", printed)
        self.assertNotIn(self.TOKEN, printed)
        # The run did not finish, so it must not claim it did.
        self.assertNotIn("outputs were not persisted", printed)

    def test_a_failed_default_run_echoes_nothing(self) -> None:
        printed = self._execute(fail=True)
        self.assertNotIn("Agent UI", printed)
        self.assertNotIn(self.TOKEN, printed)


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

    def test_output_is_not_echoed_unless_the_caller_asked(self) -> None:
        with mock.patch.object(
            runner, "execute_notebook", return_value=self._streamed("")
        ) as execute:
            runner.run_notebooks(
                [SCRIPTS_DIR / "deploy_nemoclaw.ipynb"], cwd=SCRIPTS_DIR, timeout=600
            )
        self.assertIs(execute.call_args.kwargs["echo_output"], False)

    def test_the_echo_choice_reaches_every_notebook_in_the_run(self) -> None:
        with mock.patch.object(
            runner, "execute_notebook", return_value=self._streamed("")
        ) as execute:
            runner.run_notebooks(
                [
                    SCRIPTS_DIR / "deploy_nemoclaw.ipynb",
                    SCRIPTS_DIR / "deploy_vss_orchestrator.ipynb",
                ],
                cwd=SCRIPTS_DIR,
                timeout=600,
                echo_output=True,
            )
        self.assertEqual(
            [call.kwargs["echo_output"] for call in execute.call_args_list],
            [True, True],
        )


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

    def test_echoing_the_output_is_opt_in(self) -> None:
        notebook = SCRIPTS_DIR / "deploy_nemoclaw.ipynb"
        with mock.patch.object(runner, "run_notebooks") as run_notebooks:
            runner.main(["--notebook", str(notebook)])
        self.assertIs(run_notebooks.call_args.kwargs["echo_output"], False)

        with mock.patch.object(runner, "run_notebooks") as run_notebooks:
            runner.main(["--notebook", str(notebook), "--echo-output"])
        self.assertIs(run_notebooks.call_args.kwargs["echo_output"], True)

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
