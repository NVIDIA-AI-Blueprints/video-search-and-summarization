# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch


_HELPER_PATH = Path(__file__).resolve().parents[1] / "orchestrator_mcp_helper.py"
_SPEC = importlib.util.spec_from_file_location("orchestrator_mcp_helper", _HELPER_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"Unable to load {_HELPER_PATH}")
orchestrator_mcp_helper = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(orchestrator_mcp_helper)


class DetectBrevLinkDomainTest(unittest.TestCase):
    def test_explicit_domain_wins_without_running_netbird(self) -> None:
        with (
            patch.dict(os.environ, {"BREV_LINK_DOMAIN": "  links.example.test  "}, clear=True),
            patch.object(
                orchestrator_mcp_helper.subprocess,
                "run",
                side_effect=AssertionError("netbird must not run for an explicit domain"),
            ),
        ):
            domain = orchestrator_mcp_helper.detect_brev_link_domain()

        self.assertEqual(domain, "links.example.test")

    def test_successful_netbird_status_selects_skybridge(self) -> None:
        completed = subprocess.CompletedProcess(["netbird", "status"], returncode=0)
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(orchestrator_mcp_helper.subprocess, "run", return_value=completed),
        ):
            domain = orchestrator_mcp_helper.detect_brev_link_domain()

        self.assertEqual(domain, "apps.run.brev.nvidia.com")

    def test_unavailable_netbird_selects_cloudflare(self) -> None:
        cases = {
            "status failure": subprocess.CompletedProcess(["netbird", "status"], returncode=1),
            "missing executable": FileNotFoundError("netbird"),
        }
        for name, outcome in cases.items():
            with self.subTest(name=name):
                kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
                with (
                    patch.dict(os.environ, {}, clear=True),
                    patch.object(orchestrator_mcp_helper.subprocess, "run", **kwargs),
                ):
                    domain = orchestrator_mcp_helper.detect_brev_link_domain()

                self.assertEqual(domain, "brevlab.com")


class BuildVssUiUrlTest(unittest.TestCase):
    def test_combines_prefix_environment_and_domain(self) -> None:
        env = {
            "BREV_ENV_ID": "release-321",
            "BREV_LINK_PREFIX": "vss-ui",
            "BREV_LINK_DOMAIN": "links.example.test",
        }
        with patch.dict(os.environ, env, clear=True):
            url = orchestrator_mcp_helper.build_vss_ui_url()

        self.assertEqual(url, "https://vss-ui-release-321.links.example.test/")

    def test_no_brev_environment_returns_none(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(orchestrator_mcp_helper, "read_etc_environment", return_value={}),
            patch.object(
                orchestrator_mcp_helper,
                "detect_brev_link_domain",
                side_effect=AssertionError("domain detection must not run without a Brev environment"),
            ),
        ):
            url = orchestrator_mcp_helper.build_vss_ui_url()

        self.assertIsNone(url)


if __name__ == "__main__":
    unittest.main()
