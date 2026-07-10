# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "nemoclaw" / "update_openclaw_config.py"
_SPEC = importlib.util.spec_from_file_location("update_openclaw_config", _SCRIPT_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"Unable to load {_SCRIPT_PATH}")
update_openclaw_config = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(update_openclaw_config)


class DetectBrevLinkDomainTest(unittest.TestCase):
    def test_explicit_domain_wins_without_running_netbird(self) -> None:
        with (
            patch.dict(os.environ, {"BREV_LINK_DOMAIN": "  links.example.test  "}, clear=True),
            patch.object(
                update_openclaw_config.subprocess,
                "run",
                side_effect=AssertionError("netbird must not run for an explicit domain"),
            ),
        ):
            domain = update_openclaw_config.detect_brev_link_domain()

        self.assertEqual(domain, "links.example.test")

    def test_successful_netbird_status_selects_skybridge(self) -> None:
        completed = subprocess.CompletedProcess(["netbird", "status"], returncode=0)
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(update_openclaw_config.subprocess, "run", return_value=completed),
        ):
            domain = update_openclaw_config.detect_brev_link_domain()

        self.assertEqual(domain, "apps.run.brev.nvidia.com")

    def test_missing_netbird_selects_cloudflare(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(update_openclaw_config.subprocess, "run", side_effect=FileNotFoundError("netbird")),
        ):
            domain = update_openclaw_config.detect_brev_link_domain()

        self.assertEqual(domain, "brevlab.com")


class GetBrevEnvIdTest(unittest.TestCase):
    def test_extracts_environment_id_from_secure_link_hostname(self) -> None:
        cases = {
            "Skybridge": "7777-release-321.apps.run.brev.nvidia.com",
            "Cloudflare": "7777-release-321.brevlab.com",
        }
        for name, hostname in cases.items():
            with self.subTest(name=name):
                with (
                    patch.dict(os.environ, {"HOSTNAME": hostname}, clear=True),
                    patch.object(update_openclaw_config, "read_etc_environment", return_value={}),
                    patch.object(update_openclaw_config.socket, "getfqdn", return_value="unrelated.example.test"),
                    patch.object(update_openclaw_config.socket, "gethostname", return_value="unrelated"),
                ):
                    env_id = update_openclaw_config.get_brev_env_id()

                self.assertEqual(env_id, "release-321")


if __name__ == "__main__":
    unittest.main()
