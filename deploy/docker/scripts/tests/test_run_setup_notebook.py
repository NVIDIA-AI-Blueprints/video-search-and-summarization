# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import os
import unittest
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
    def test_covers_both_checked_in_setup_notebooks(self) -> None:
        self.assertEqual(
            sorted(runner.NOTEBOOK_PARAMETERS),
            ["deploy_nemoclaw.ipynb", "deploy_vss_orchestrator.ipynb"],
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
            runner.parameterize_notebook(
                notebook, runner.parameters_for(path), label=name
            )
            injected = [
                cell["source"]
                for cell in notebook["cells"]
                if "run_setup_notebook" in str(cell.get("source", ""))
            ]
            self.assertEqual(len(injected), 1, name)
            for parameter in runner.parameters_for(path):
                self.assertIn(f"{parameter} = _vss_setup_os.environ.get", injected[0])


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
        second = SCRIPTS_DIR / "deploy_vss_orchestrator.ipynb"
        with mock.patch.object(
            runner, "execute_notebook", return_value=self._streamed("")
        ) as execute:
            runner.run_notebooks([first, second], cwd=SCRIPTS_DIR, timeout=600)
        self.assertEqual(
            [call.args[0] for call in execute.call_args_list], [first, second]
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
