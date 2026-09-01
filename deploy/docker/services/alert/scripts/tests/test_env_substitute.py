# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for deploy/docker/services/alert/scripts/env-substitute.py."""

from __future__ import annotations

import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from typing import List, Optional
from unittest import mock

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "env-substitute.py"
MODULE_SPEC = importlib.util.spec_from_file_location(
    "env_substitute_under_test", SCRIPT_PATH
)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError(f"Could not load {SCRIPT_PATH}")
env_sub = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(env_sub)


def _run_main(argv: List[str], *, execvp: Optional[mock.Mock] = None) -> int:
    """Invoke main() with argv and a stubbed execvp; return exit code."""
    if execvp is None:
        execvp = mock.Mock(side_effect=RuntimeError("execvp should not run"))

    with (
        mock.patch.object(env_sub.sys, "argv", argv),
        mock.patch.object(env_sub.os, "execvp", execvp),
    ):
        try:
            env_sub.main()
        except SystemExit as exc:
            return int(exc.code or 0)
        except RuntimeError as exc:
            if str(exc) == "execvp should not run":
                return 0
            raise
    return 0


class TestSubstituteEnvVars(unittest.TestCase):
    def test_replaces_set_variables(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"VLM_NAME": "cosmos-reason3", "ALERT_AGENT_ALWAYS_ON": "true"},
            clear=False,
        ):
            out = env_sub.substitute_env_vars(
                'model: "${VLM_NAME}"\nalways_on: ${ALERT_AGENT_ALWAYS_ON}\n'
            )
        self.assertEqual(out, 'model: "cosmos-reason3"\nalways_on: true\n')

    def test_empty_or_unset_variables_become_empty_string(self) -> None:
        env = os.environ.copy()
        env.pop("MISSING_VAR", None)
        env["EMPTY_VAR"] = ""
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(env_sub.sys, "stderr", stderr),
        ):
            out = env_sub.substitute_env_vars("${MISSING_VAR}|${EMPTY_VAR}")

        self.assertEqual(out, "|")
        err = stderr.getvalue()
        self.assertIn("MISSING_VAR", err)
        self.assertIn("EMPTY_VAR", err)

    def test_leaves_non_placeholder_text_unchanged(self) -> None:
        with mock.patch.dict(os.environ, {"FOO": "bar"}, clear=False):
            content = "plain text $FOO ${bad-name} ${123} keep"
            self.assertEqual(env_sub.substitute_env_vars(content), content)

    def test_replaces_repeated_occurrences(self) -> None:
        with mock.patch.dict(os.environ, {"HOST": "rtvi-vlm"}, clear=False):
            out = env_sub.substitute_env_vars("${HOST} -> ${HOST}:8000")
        self.assertEqual(out, "rtvi-vlm -> rtvi-vlm:8000")


class TestMainArgumentValidation(unittest.TestCase):
    def test_missing_separator_exits_before_exec(self) -> None:
        execvp = mock.Mock()
        stderr = io.StringIO()
        with mock.patch.object(env_sub.sys, "stderr", stderr):
            code = _run_main(
                ["env-substitute.py", "--source", "a", "--output", "b", "true"],
                execvp=execvp,
            )

        self.assertEqual(code, 1)
        execvp.assert_not_called()
        self.assertIn("Missing '--' separator", stderr.getvalue())

    def test_no_command_after_separator_exits(self) -> None:
        execvp = mock.Mock()
        stderr = io.StringIO()
        with mock.patch.object(env_sub.sys, "stderr", stderr):
            code = _run_main(
                [
                    "env-substitute.py",
                    "--source",
                    "a.yml",
                    "--output",
                    "b.yml",
                    "--",
                ],
                execvp=execvp,
            )

        self.assertEqual(code, 1)
        execvp.assert_not_called()
        self.assertIn("No command provided", stderr.getvalue())

    def test_mismatched_required_source_output_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            src = tmp / "a.yml"
            src.write_text("x: 1\n", encoding="utf-8")
            execvp = mock.Mock()
            stderr = io.StringIO()
            with mock.patch.object(env_sub.sys, "stderr", stderr):
                code = _run_main(
                    [
                        "env-substitute.py",
                        "--source",
                        str(src),
                        "--source",
                        str(src),
                        "--output",
                        str(tmp / "out.yml"),
                        "--",
                        "true",
                    ],
                    execvp=execvp,
                )

            self.assertEqual(code, 1)
            execvp.assert_not_called()
            self.assertIn(
                "Each --source must have a matching --output",
                stderr.getvalue(),
            )

    def test_mismatched_optional_source_output_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            src = tmp / "a.yml"
            src.write_text("x: 1\n", encoding="utf-8")
            execvp = mock.Mock()
            stderr = io.StringIO()
            with mock.patch.object(env_sub.sys, "stderr", stderr):
                code = _run_main(
                    [
                        "env-substitute.py",
                        "--source",
                        str(src),
                        "--output",
                        str(tmp / "out.yml"),
                        "--optional-source",
                        str(tmp / "opt.yml"),
                        "--",
                        "true",
                    ],
                    execvp=execvp,
                )

            self.assertEqual(code, 1)
            execvp.assert_not_called()
            self.assertIn(
                "Each --optional-source must have a matching --optional-output",
                stderr.getvalue(),
            )


class TestMainFileHandling(unittest.TestCase):
    def test_required_missing_source_fails_before_exec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            execvp = mock.Mock()
            stderr = io.StringIO()
            with mock.patch.object(env_sub.sys, "stderr", stderr):
                code = _run_main(
                    [
                        "env-substitute.py",
                        "--source",
                        str(tmp / "missing.yml"),
                        "--output",
                        str(tmp / "out.yml"),
                        "--",
                        "true",
                    ],
                    execvp=execvp,
                )

            self.assertEqual(code, 1)
            execvp.assert_not_called()
            self.assertIn("Source config file not found", stderr.getvalue())
            self.assertFalse((tmp / "out.yml").exists())

    def test_optional_missing_source_is_skipped_then_exec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            required = tmp / "config.yml"
            required.write_text('name: "${APP_NAME}"\n', encoding="utf-8")
            out = tmp / "runtime" / "config.yml"
            optional_out = tmp / "runtime" / "realtime-config.yml"
            execvp = mock.Mock()

            with mock.patch.dict(os.environ, {"APP_NAME": "alert-bridge"}, clear=False):
                code = _run_main(
                    [
                        "env-substitute.py",
                        "--source",
                        str(required),
                        "--output",
                        str(out),
                        "--optional-source",
                        str(tmp / "realtime-config.yml"),
                        "--optional-output",
                        str(optional_out),
                        "--",
                        "enhance_alert_with_vlm.py",
                        "--config",
                        str(out),
                    ],
                    execvp=execvp,
                )

            self.assertEqual(code, 0)
            self.assertEqual(out.read_text(encoding="utf-8"), 'name: "alert-bridge"\n')
            self.assertFalse(optional_out.exists())
            execvp.assert_called_once_with(
                "enhance_alert_with_vlm.py",
                ["enhance_alert_with_vlm.py", "--config", str(out)],
            )

    def test_substitutes_multiple_required_and_optional_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            cfg = tmp / "config.yml"
            cfg.write_text("flag: ${FLAG}\n", encoding="utf-8")
            rules = tmp / "realtime-config.yml"
            rules.write_text('model: "${VLM_NAME}"\n', encoding="utf-8")
            extra = tmp / "extra.yml"
            extra.write_text("extra: ${FLAG}\n", encoding="utf-8")

            out_cfg = tmp / "runtime" / "config.yml"
            out_rules = tmp / "runtime" / "realtime-config.yml"
            out_extra = tmp / "runtime" / "extra.yml"
            execvp = mock.Mock()

            with mock.patch.dict(
                os.environ,
                {"VLM_NAME": "cosmos-reason3", "FLAG": "on"},
                clear=False,
            ):
                code = _run_main(
                    [
                        "env-substitute.py",
                        "--source",
                        str(cfg),
                        "--output",
                        str(out_cfg),
                        "--source",
                        str(extra),
                        "--output",
                        str(out_extra),
                        "--optional-source",
                        str(rules),
                        "--optional-output",
                        str(out_rules),
                        "--",
                        "true",
                    ],
                    execvp=execvp,
                )

            self.assertEqual(code, 0)
            self.assertEqual(out_cfg.read_text(encoding="utf-8"), "flag: on\n")
            self.assertEqual(out_extra.read_text(encoding="utf-8"), "extra: on\n")
            self.assertEqual(
                out_rules.read_text(encoding="utf-8"),
                'model: "cosmos-reason3"\n',
            )
            execvp.assert_called_once_with("true", ["true"])

    def test_creates_output_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            src = tmp / "src.yml"
            src.write_text("x: ${X}\n", encoding="utf-8")
            out = tmp / "deep" / "nested" / "out.yml"
            execvp = mock.Mock()

            with mock.patch.dict(os.environ, {"X": "1"}, clear=False):
                code = _run_main(
                    [
                        "env-substitute.py",
                        "--source",
                        str(src),
                        "--output",
                        str(out),
                        "--",
                        "true",
                    ],
                    execvp=execvp,
                )

            self.assertEqual(code, 0)
            self.assertEqual(out.read_text(encoding="utf-8"), "x: 1\n")
            execvp.assert_called_once()

    def test_write_failure_exits_before_exec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            src = tmp / "src.yml"
            src.write_text("x: ${X}\n", encoding="utf-8")
            out = tmp / "out.yml"
            execvp = mock.Mock()
            stderr = io.StringIO()
            real_open = io.open

            def _open_fail(path, mode="r", *args, **kwargs):
                if str(path) == str(out) and "w" in mode:
                    raise OSError("disk full")
                return real_open(path, mode, *args, **kwargs)

            with (
                mock.patch.dict(os.environ, {"X": "1"}, clear=False),
                mock.patch.object(env_sub.sys, "stderr", stderr),
                mock.patch("builtins.open", side_effect=_open_fail),
            ):
                code = _run_main(
                    [
                        "env-substitute.py",
                        "--source",
                        str(src),
                        "--output",
                        str(out),
                        "--",
                        "true",
                    ],
                    execvp=execvp,
                )

            self.assertEqual(code, 1)
            execvp.assert_not_called()
            self.assertIn("Error writing processed config", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
