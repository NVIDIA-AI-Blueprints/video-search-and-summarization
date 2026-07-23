# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

HELPER_PATH = Path(__file__).parents[1] / "orchestrator_mcp_helper.py"
MODULE_SPEC = importlib.util.spec_from_file_location("orchestrator_mcp_helper_under_test", HELPER_PATH)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError(f"Could not load {HELPER_PATH}")
helper = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(helper)


class DetectBrevLinkDomainTests(unittest.TestCase):
    def test_explicit_override_wins_without_calling_netbird(self) -> None:
        with (
            mock.patch.dict(os.environ, {"BREV_LINK_DOMAIN": " custom.example.com "}, clear=True),
            mock.patch.object(helper.subprocess, "run", side_effect=AssertionError("netbird must not run")),
        ):
            self.assertEqual(helper.detect_brev_link_domain(), "custom.example.com")

    def test_netbird_detailed_status_selects_secure_link_domain(self) -> None:
        netbird_command = ["netbird", "status", "-d"]
        generic_status = (
            "OS: linux/amd64\n"
            "Daemon version: 0.54.1\n"
            "Management: Connected\n"
            "Signal: Connected\n"
            "FQDN: generic-peer.netbird.cloud\n"
        )
        skybridge_status = (
            "OS: linux/amd64\n"
            "Daemon version: 0.54.1\n"
            "Management: Connected\n"
            "Signal: Connected\n"
            "FQDN: skybridge-env.apps.run.brev.nvidia.com\n"
        )
        cases = [
            (
                "generic healthy NetBird",
                subprocess.CompletedProcess(
                    netbird_command,
                    0,
                    stdout=generic_status,
                    stderr="",
                ),
                "brevlab.com",
            ),
            (
                "Skybridge identity",
                subprocess.CompletedProcess(
                    netbird_command,
                    0,
                    stdout=skybridge_status,
                    stderr="",
                ),
                "apps.run.brev.nvidia.com",
            ),
            (
                "nonzero status with Skybridge identity",
                subprocess.CompletedProcess(
                    netbird_command,
                    1,
                    stdout=skybridge_status,
                    stderr="status unavailable",
                ),
                "brevlab.com",
            ),
            (
                "netbird missing",
                FileNotFoundError(2, "No such file or directory", "netbird"),
                "brevlab.com",
            ),
            (
                "status timeout",
                subprocess.TimeoutExpired(netbird_command, 3),
                "brevlab.com",
            ),
        ]

        for name, result_or_error, expected in cases:
            with self.subTest(name=name):
                if isinstance(result_or_error, BaseException):
                    netbird_patch = mock.patch.object(helper.subprocess, "run", side_effect=result_or_error)
                else:
                    netbird_patch = mock.patch.object(helper.subprocess, "run", return_value=result_or_error)

                with mock.patch.dict(os.environ, {}, clear=True), netbird_patch as netbird_run:
                    self.assertEqual(helper.detect_brev_link_domain(), expected)
                    netbird_run.assert_called_once_with(
                        netbird_command,
                        capture_output=True,
                        text=True,
                        timeout=3,
                    )


class BuildVssUiUrlTests(unittest.TestCase):
    def test_builds_url_for_each_detected_domain(self) -> None:
        netbird_command = ["netbird", "status", "-d"]
        generic_status = (
            "OS: linux/amd64\n"
            "Daemon version: 0.54.1\n"
            "Management: Connected\n"
            "Signal: Connected\n"
            "FQDN: generic-peer.netbird.cloud\n"
        )
        skybridge_status = (
            "OS: linux/amd64\n"
            "Daemon version: 0.54.1\n"
            "Management: Connected\n"
            "Signal: Connected\n"
            "FQDN: skybridge-env.apps.run.brev.nvidia.com\n"
        )
        cases = [
            (
                "Skybridge identity",
                subprocess.CompletedProcess(
                    netbird_command,
                    0,
                    stdout=skybridge_status,
                    stderr="",
                ),
                "https://ui-env-123.apps.run.brev.nvidia.com/",
            ),
            (
                "generic healthy NetBird",
                subprocess.CompletedProcess(
                    netbird_command,
                    0,
                    stdout=generic_status,
                    stderr="",
                ),
                "https://ui-env-123.brevlab.com/",
            ),
        ]

        for name, netbird_result, expected in cases:
            with self.subTest(name=name):
                environment = {
                    "BREV_ENV_ID": "env-123",
                    "BREV_LINK_PREFIX": "ui",
                }
                with (
                    mock.patch.dict(os.environ, environment, clear=True),
                    mock.patch.object(
                        helper.subprocess,
                        "run",
                        return_value=netbird_result,
                    ),
                ):
                    self.assertEqual(helper.build_vss_ui_url(), expected)


    def test_returns_none_outside_brev(self) -> None:
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(helper, "read_etc_environment", return_value={}),
            mock.patch.object(helper.subprocess, "run", side_effect=AssertionError("netbird must not run")),
        ):
            self.assertIsNone(helper.build_vss_ui_url())

if __name__ == "__main__":
    unittest.main()
