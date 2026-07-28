# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import unittest
from pathlib import Path
from unittest import mock

HELPER_PATH = Path(__file__).parents[1] / "orchestrator_mcp_helper.py"
MODULE_SPEC = importlib.util.spec_from_file_location("orchestrator_mcp_helper_under_test", HELPER_PATH)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError(f"Could not load {HELPER_PATH}")
helper = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(helper)


class ResolveOpenshellGatewayContainerTests(unittest.TestCase):
    def test_returns_first_matching_container_name(self) -> None:
        result = mock.Mock()
        result.stdout = "openshell-demo-abc\n"
        result.returncode = 0
        with mock.patch.object(helper.subprocess, "run", return_value=result) as run:
            name = helper.resolve_openshell_gateway_container("demo")
        self.assertEqual(name, "openshell-demo-abc")
        run.assert_called_once()
        args = run.call_args.args[0]
        self.assertIn("label=openshell.ai/sandbox-name=demo", args)

    def test_returns_none_when_no_containers(self) -> None:
        result = mock.Mock()
        result.stdout = "\n"
        result.returncode = 0
        with mock.patch.object(helper.subprocess, "run", return_value=result):
            self.assertIsNone(helper.resolve_openshell_gateway_container("demo"))



class EnsureMcpTlsCertsTests(unittest.TestCase):
    def test_returns_existing_paths_without_openssl(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            cert = tmp / "cert.pem"
            key = tmp / "key.pem"
            cert.write_text("cert", encoding="utf-8")
            key.write_text("key", encoding="utf-8")
            with mock.patch.object(helper.shutil, "which", side_effect=AssertionError("openssl must not run")):
                got_cert, got_key = helper.ensure_mcp_tls_certs(cert, key, san="DNS:localhost")
            self.assertEqual(got_cert, cert.resolve())
            self.assertEqual(got_key, key.resolve())

    def test_generates_missing_pair_via_openssl(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            cert = tmp / "sub" / "cert.pem"
            key = tmp / "sub" / "key.pem"

            def fake_openssl(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
                del check
                Path(cmd[cmd.index("-keyout") + 1]).parent.mkdir(parents=True, exist_ok=True)
                Path(cmd[cmd.index("-keyout") + 1]).write_text("key", encoding="utf-8")
                Path(cmd[cmd.index("-out") + 1]).write_text("cert", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0)

            with (
                mock.patch.object(helper.shutil, "which", return_value="/usr/bin/openssl"),
                mock.patch.object(helper.subprocess, "run", side_effect=fake_openssl) as run,
            ):
                got_cert, got_key = helper.ensure_mcp_tls_certs(
                    cert,
                    key,
                    san="DNS:localhost,IP:127.0.0.1",
                )
            self.assertTrue(got_cert.is_file())
            self.assertTrue(got_key.is_file())
            run.assert_called_once()
            cmd = run.call_args.args[0]
            self.assertIn("subjectAltName=DNS:localhost,IP:127.0.0.1", cmd)

    def test_requires_san_when_generating(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            with self.assertRaises(ValueError):
                helper.ensure_mcp_tls_certs(tmp / "c.pem", tmp / "k.pem", san="  ")


if __name__ == "__main__":
    unittest.main()
