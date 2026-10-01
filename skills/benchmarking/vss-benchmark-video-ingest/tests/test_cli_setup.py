# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Installed CLI selection and strict preflight protocol tests; no deployment needed."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from vss_cli import CliResult, VssCli


class CliSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "checkout"

    def executable(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "print(json.dumps({'args':sys.argv[1:], 'venv':os.environ.get('VIRTUAL_ENV'), "
            "'config':os.environ.get('VSS_CONFIG_HOME'), 'path':os.environ.get('PATH')}))\n"
        )
        path.chmod(0o755)
        return path

    def test_explicit_direct_binary_is_resolved_once_with_safe_arguments(self):
        binary = self.executable(self.root / "tool env/bin/vss")
        (binary.parent.parent / "pyvenv.cfg").touch()
        link = self.root / "vss-link"
        link.symlink_to(binary)
        with patch.dict(os.environ, {"VIRTUAL_ENV": "/unrelated", "PATH": "/original"}):
            cli = VssCli(self.repo, self.root / "private config", executable=str(link))
        with patch("vss_cli.shutil.which", side_effect=AssertionError("resolved again")):
            first = cli.call("vios", "add", "file name; touch NOPE.mp4")
            second = cli.call("configure", "show")
        self.assertEqual(cli.command, (str(binary),))
        self.assertEqual(first.body["args"], ["vios", "add", "file name; touch NOPE.mp4"])
        self.assertEqual(second.body["args"], ["configure", "show"])
        self.assertEqual(first.body["venv"], str(binary.parent.parent))
        self.assertEqual(first.body["config"], str(self.root / "private config"))
        self.assertEqual(first.body["path"], str(binary.parent) + os.pathsep + "/original")
        self.assertFalse((self.root / "NOPE.mp4").exists())

    def test_path_install_needs_no_agent_checkout(self):
        binary = self.executable(self.root / "bin/vss")
        with patch.dict(os.environ, {"PATH": str(binary.parent)}):
            cli = VssCli(self.repo)
        self.assertEqual(cli.command, (str(binary),))

    def test_current_workspace_environment_preferred_over_legacy(self):
        current = self.executable(self.repo / "libs/vss/.venv/bin/vss")
        self.executable(self.repo / "services/agent/.venv/bin/vss")
        with patch("vss_cli.shutil.which", return_value=None):
            cli = VssCli(self.repo)
        self.assertEqual(cli.command, (str(current),))

    def test_invalid_explicit_binary_does_not_silently_fallback(self):
        path = self.root / "not-executable"
        path.touch()
        for selected in (str(path), str(self.root / "missing")):
            with self.subTest(selected=selected), self.assertRaisesRegex(ValueError, "not executable"):
                VssCli(self.repo, executable=selected)

    def test_uv_fallback_covers_new_workspace_and_old_layout_offline(self):
        for layout, expected in (("libs/vss", ("--package", "nvidia-vss-cli")), ("services/agent", ("--extra", "cli"))):
            with self.subTest(layout=layout):
                root = self.repo / layout.replace("/", "-")
                project = root / layout
                project.mkdir(parents=True)
                (project / "pyproject.toml").touch()
                with (
                    patch("vss_cli.shutil.which", return_value=None),
                    patch.dict(os.environ, {"VIRTUAL_ENV": "/other"}),
                ):
                    cli = VssCli(root)
                self.assertEqual(
                    cli.command, ("uv", "run", "--project", str(project), "--no-sync", "--no-dev", *expected, "vss")
                )
                self.assertNotIn("VIRTUAL_ENV", cli.env)
                self.assertEqual(cli.env["UV_OFFLINE"], "true")
                self.assertEqual(cli.env["UV_PYTHON_DOWNLOADS"], "never")

    def test_custom_uv_override_preserved_when_path_vss_exists(self):
        binary = self.executable(self.root / "bin/vss")
        project = self.repo / "services/agent"
        project.mkdir(parents=True)
        (project / "pyproject.toml").touch()
        with patch.dict(os.environ, {"PATH": str(binary.parent)}):
            cli = VssCli(self.repo, uv="/custom/uv")
            explicit = VssCli(self.repo, uv="/custom/uv", executable=str(binary))
        self.assertEqual(cli.command[0], "/custom/uv")
        self.assertEqual(explicit.command, (str(binary),))

    def test_invalid_config_and_list_json_are_not_healthy(self):
        binary = self.executable(self.root / "vss")
        cli = VssCli(self.repo, executable=str(binary))
        valid = {"base_url": "http://fixture.invalid", "services": {"vst": {"url": "http://fixture.invalid/vst"}}}
        invalid = ({}, {"services": []}, {"services": {"vst": "yes"}}, {"services": {"vst": {"url": "not-url"}}})
        for body in invalid:
            with self.subTest(body=body), patch.object(cli, "call", return_value=CliResult(0, body)):
                with self.assertRaises(ValueError):
                    cli.deployment(check_health=False)
        for body in ({}, {"sensors": {}}, {"sensors": ["bad-row"]}):
            with (
                self.subTest(body=body),
                patch.object(cli, "call", side_effect=[CliResult(0, valid), CliResult(0, body)]),
            ):
                with self.assertRaisesRegex(ValueError, "sensors array"):
                    cli.deployment()
        with patch.object(cli, "call", side_effect=[CliResult(0, valid), CliResult(0, {"count": 0, "sensors": []})]):
            self.assertEqual(cli.deployment(), valid)


if __name__ == "__main__":
    unittest.main()
