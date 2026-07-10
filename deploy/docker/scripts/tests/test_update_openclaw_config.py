# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock

SCRIPT_PATH = Path(__file__).parents[1] / "nemoclaw" / "update_openclaw_config.py"
MODULE_SPEC = importlib.util.spec_from_file_location("update_openclaw_config_under_test", SCRIPT_PATH)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError(f"Could not load {SCRIPT_PATH}")
update_config = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(update_config)


class DetectBrevLinkDomainTests(unittest.TestCase):
    def test_explicit_override_wins_without_calling_netbird(self) -> None:
        with (
            mock.patch.dict(os.environ, {"BREV_LINK_DOMAIN": " custom.example.com "}, clear=True),
            mock.patch.object(
                update_config.subprocess,
                "run",
                side_effect=AssertionError("netbird must not run"),
            ),
        ):
            self.assertEqual(update_config.detect_brev_link_domain(), "custom.example.com")

    def test_netbird_status_selects_link_provider(self) -> None:
        netbird_command = ["netbird", "status", "-d"]
        cases = [
            (
                "generic NetBird",
                subprocess.CompletedProcess(
                    netbird_command,
                    0,
                    stdout="Management: Connected\nSignal: Connected\n",
                    stderr="",
                ),
                "brevlab.com",
            ),
            (
                "Skybridge identity",
                subprocess.CompletedProcess(
                    netbird_command,
                    0,
                    stdout="FQDN: skybridge-env.apps.run.brev.nvidia.com\n",
                    stderr="",
                ),
                "apps.run.brev.nvidia.com",
            ),
            (
                "status failure",
                subprocess.CompletedProcess(
                    netbird_command,
                    1,
                    stdout="",
                    stderr="status unavailable",
                ),
                "brevlab.com",
            ),
            ("netbird missing", FileNotFoundError("netbird"), "brevlab.com"),
            ("status timeout", subprocess.TimeoutExpired("netbird", 3), "brevlab.com"),
        ]

        for name, result_or_error, expected in cases:
            with self.subTest(name=name):
                if isinstance(result_or_error, BaseException):
                    netbird_result = mock.patch.object(
                        update_config.subprocess,
                        "run",
                        side_effect=result_or_error,
                    )
                else:
                    netbird_result = mock.patch.object(
                        update_config.subprocess,
                        "run",
                        return_value=result_or_error,
                    )

                with mock.patch.dict(os.environ, {}, clear=True), netbird_result:
                    self.assertEqual(update_config.detect_brev_link_domain(), expected)


class BrevHostnameParsingTests(unittest.TestCase):
    def test_extracts_environment_id_from_both_secure_link_domains(self) -> None:
        cases = [
            ("7777-sky-env.apps.run.brev.nvidia.com", "sky-env"),
            ("5601-cloud-env.brevlab.com", "cloud-env"),
            ("7777-Mixed-Case.Apps.Run.Brev.Nvidia.Com.", "mixed-case"),
        ]

        for hostname, expected in cases:
            with (
                self.subTest(hostname=hostname),
                mock.patch.dict(os.environ, {"HOSTNAME": hostname}, clear=True),
                mock.patch.object(update_config, "read_etc_environment", return_value={}),
                mock.patch.object(update_config.socket, "getfqdn", return_value=""),
                mock.patch.object(update_config.socket, "gethostname", return_value=""),
            ):
                self.assertEqual(update_config.get_brev_env_id(), expected)

    def test_returns_empty_for_non_brev_hostname(self) -> None:
        with (
            mock.patch.dict(os.environ, {"HOSTNAME": "ordinary.example.com"}, clear=True),
            mock.patch.object(update_config, "read_etc_environment", return_value={}),
            mock.patch.object(update_config.socket, "getfqdn", return_value="ordinary.example.com"),
            mock.patch.object(update_config.socket, "gethostname", return_value="ordinary"),
        ):
            self.assertEqual(update_config.get_brev_env_id(), "")


class AllowedOriginTests(unittest.TestCase):
    def test_brev_origin_uses_link_prefix_not_dashboard_port(self) -> None:
        environment = {
            "BREV_ENV_ID": "env-123",
            "BREV_LINK_PREFIX": "ui",
            "NEMOCLAW_DASHBOARD_PORT": "18789",
        }
        with (
            mock.patch.dict(os.environ, environment, clear=True),
            mock.patch.object(
                update_config.sys,
                "argv",
                ["update_openclaw_config.py", "demo"],
            ),
            mock.patch.object(
                update_config,
                "detect_brev_link_domain",
                return_value="brevlab.com",
            ),
            mock.patch.object(update_config, "read_remote_file", return_value="{}"),
            mock.patch.object(update_config, "backup_remote_file"),
            mock.patch.object(update_config, "write_remote_file") as write_remote_file,
            mock.patch.object(update_config, "chmod_and_chown"),
            mock.patch.object(update_config, "get_dashboard_token", return_value=None),
        ):
            self.assertEqual(update_config.main(), 0)

        updated = json.loads(write_remote_file.call_args.args[2])
        self.assertEqual(
            updated["gateway"]["controlUi"]["allowedOrigins"],
            ["https://ui-env-123.brevlab.com"],
        )


if __name__ == "__main__":
    unittest.main()
