# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HELPER_PATH = Path(__file__).parents[1] / "orchestrator_mcp_helper.py"
MODULE_SPEC = importlib.util.spec_from_file_location("orchestrator_mcp_helper_under_test", HELPER_PATH)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError(f"Could not load {HELPER_PATH}")
helper = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(helper)


def _write_context(path: Path, *, env_id: str, ports: list[dict]) -> None:
    path.write_text(json.dumps({"environment_id": env_id, "ports": ports}), encoding="utf-8")


class DetectBrevLinkDomainTests(unittest.TestCase):
    def test_explicit_override_wins(self) -> None:
        with mock.patch.dict(os.environ, {"BREV_LINK_DOMAIN": " custom.example.com "}, clear=True):
            self.assertEqual(helper.detect_brev_link_domain(), "custom.example.com")

    def test_derives_domain_from_context_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context_path = Path(tmp) / "environment-context.json"
            _write_context(
                context_path,
                env_id="juud6xh3e",
                ports=[
                    {
                        "destination_port": 18789,
                        "fqdn": "18789-juud6xh3e.stg.apps.launchpad.nvidia.com",
                    }
                ],
            )
            with mock.patch.dict(
                os.environ,
                {"BREV_ENVIRONMENT_CONTEXT_PATH": str(context_path)},
                clear=True,
            ):
                self.assertEqual(
                    helper.detect_brev_link_domain(),
                    "stg.apps.launchpad.nvidia.com",
                )

    def test_returns_empty_when_context_path_unset(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(helper.detect_brev_link_domain(), "")

    def test_returns_empty_when_context_unreadable(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"BREV_ENVIRONMENT_CONTEXT_PATH": "/no/such/environment-context.json"},
            clear=True,
        ):
            self.assertEqual(helper.detect_brev_link_domain(), "")


class BuildVssUiUrlTests(unittest.TestCase):
    def test_prefers_exact_fqdn_from_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context_path = Path(tmp) / "environment-context.json"
            _write_context(
                context_path,
                env_id="env-123",
                ports=[{"destination_port": 7777, "fqdn": "7777-env-123.stg.apps.launchpad.nvidia.com"}],
            )
            with mock.patch.dict(
                os.environ,
                {"BREV_ENVIRONMENT_CONTEXT_PATH": str(context_path)},
                clear=True,
            ):
                self.assertEqual(
                    helper.build_vss_ui_url(7777),
                    "https://7777-env-123.stg.apps.launchpad.nvidia.com/",
                )

    def test_builds_url_from_derived_domain_when_port_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context_path = Path(tmp) / "environment-context.json"
            _write_context(
                context_path,
                env_id="env-123",
                ports=[
                    {
                        "destination_port": 18789,
                        "fqdn": "18789-env-123.apps.run.brev.nvidia.com",
                    }
                ],
            )
            with mock.patch.dict(
                os.environ,
                {
                    "BREV_ENVIRONMENT_CONTEXT_PATH": str(context_path),
                    "BREV_LINK_PREFIX": "ui",
                },
                clear=True,
            ):
                self.assertEqual(
                    helper.build_vss_ui_url(7777),
                    "https://ui-env-123.apps.run.brev.nvidia.com/",
                )

    def test_returns_none_without_context_or_domain(self) -> None:
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(helper, "read_etc_environment", return_value={}),
        ):
            self.assertIsNone(helper.build_vss_ui_url())


if __name__ == "__main__":
    unittest.main()
