# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import subprocess
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
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            cert = tmp / "cert.pem"
            key = tmp / "key.pem"
            cert.write_text("cert", encoding="utf-8")
            key.write_text("key", encoding="utf-8")
            # No openssl → skip the expiry check and reuse the pair as-is.
            with (
                mock.patch.object(helper.shutil, "which", return_value=None),
                mock.patch.object(helper.subprocess, "run", side_effect=AssertionError("openssl must not run")),
            ):
                got_cert, got_key = helper.ensure_mcp_tls_certs(cert, key, san="DNS:localhost")
            self.assertEqual(got_cert, cert.resolve())
            self.assertEqual(got_key, key.resolve())

    def test_keeps_unexpired_existing_pair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            cert = tmp / "cert.pem"
            key = tmp / "key.pem"
            cert.write_text("cert", encoding="utf-8")
            key.write_text("key", encoding="utf-8")

            def fake_checkend(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                self.assertIn("-checkend", cmd)
                return subprocess.CompletedProcess(cmd, 0)

            with (
                mock.patch.object(helper.shutil, "which", return_value="/usr/bin/openssl"),
                mock.patch.object(helper.subprocess, "run", side_effect=fake_checkend),
            ):
                got_cert, got_key = helper.ensure_mcp_tls_certs(cert, key, san="DNS:localhost")
            self.assertEqual(got_cert, cert.resolve())
            self.assertEqual(cert.read_text(encoding="utf-8"), "cert")

    def test_raises_when_existing_cert_is_expired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            cert = tmp / "cert.pem"
            key = tmp / "key.pem"
            cert.write_text("expired-cert", encoding="utf-8")
            key.write_text("expired-key", encoding="utf-8")

            def fake_checkend(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                self.assertIn("-checkend", cmd)
                return subprocess.CompletedProcess(cmd, 1)

            with (
                mock.patch.object(helper.shutil, "which", return_value="/usr/bin/openssl"),
                mock.patch.object(helper.subprocess, "run", side_effect=fake_checkend),
                self.assertRaises(RuntimeError) as ctx,
            ):
                helper.ensure_mcp_tls_certs(cert, key, san="DNS:localhost")
            self.assertIn("expired", str(ctx.exception))
            self.assertEqual(cert.read_text(encoding="utf-8"), "expired-cert")
            self.assertEqual(key.read_text(encoding="utf-8"), "expired-key")

    def test_generates_missing_pair_via_openssl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            cert = tmp / "sub" / "cert.pem"
            key = tmp / "sub" / "key.pem"

            def fake_openssl(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
                del check
                key_out = Path(cmd[cmd.index("-keyout") + 1])
                key_out.parent.mkdir(parents=True, exist_ok=True)
                key_out.write_text("key", encoding="utf-8")
                # Mimic openssl -nodes under umask 022: world-readable until we chmod.
                key_out.chmod(0o644)
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
            self.assertEqual(got_key.stat().st_mode & 0o777, 0o600)
            run.assert_called_once()
            cmd = run.call_args.args[0]
            self.assertIn("subjectAltName=DNS:localhost,IP:127.0.0.1", cmd)

    def test_errors_when_only_cert_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            cert = tmp / "cert.pem"
            key = tmp / "key.pem"
            cert.write_text("custom-ca-cert", encoding="utf-8")
            with (
                mock.patch.object(helper.shutil, "which", side_effect=AssertionError("openssl must not run")),
                mock.patch.object(helper.subprocess, "run", side_effect=AssertionError("openssl must not run")),
                self.assertRaises(FileNotFoundError) as ctx,
            ):
                helper.ensure_mcp_tls_certs(cert, key, san="DNS:localhost")
            self.assertIn("both exist or both be absent", str(ctx.exception))
            self.assertEqual(cert.read_text(encoding="utf-8"), "custom-ca-cert")
            self.assertFalse(key.exists())

    def test_errors_when_only_key_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            cert = tmp / "cert.pem"
            key = tmp / "key.pem"
            key.write_text("custom-key", encoding="utf-8")
            with (
                mock.patch.object(helper.shutil, "which", side_effect=AssertionError("openssl must not run")),
                mock.patch.object(helper.subprocess, "run", side_effect=AssertionError("openssl must not run")),
                self.assertRaises(FileNotFoundError) as ctx,
            ):
                helper.ensure_mcp_tls_certs(cert, key, san="DNS:localhost")
            self.assertIn("both exist or both be absent", str(ctx.exception))
            self.assertEqual(key.read_text(encoding="utf-8"), "custom-key")
            self.assertFalse(cert.exists())

    def test_requires_san_when_generating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            with self.assertRaises(ValueError):
                helper.ensure_mcp_tls_certs(tmp / "c.pem", tmp / "k.pem", san="  ")


if __name__ == "__main__":
    unittest.main()
