# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import subprocess
import signal
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HELPER_PATH = Path(__file__).parents[1] / "orchestrator_mcp_helper.py"
MODULE_SPEC = importlib.util.spec_from_file_location(
    "orchestrator_mcp_helper_under_test", HELPER_PATH
)
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


class StopExistingOrchestratorMcpListenerTests(unittest.TestCase):
    def test_listener_without_inspectable_owner_fails_closed(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tempdir,
            mock.patch.object(
                helper,
                "_listening_socket_inodes",
                return_value={"424242"},
            ),
            self.assertRaisesRegex(
                RuntimeError, "owning processes cannot be inspected"
            ),
        ):
            helper._listening_pids(9988, Path(tempdir))

    def test_partially_inspectable_listener_batch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            proc_root = Path(tempdir)
            file_descriptor_dir = proc_root / "1234" / "fd"
            file_descriptor_dir.mkdir(parents=True)
            (file_descriptor_dir / "5").symlink_to("socket:[111]")
            with (
                mock.patch.object(
                    helper,
                    "_listening_socket_inodes",
                    return_value={"111", "222"},
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "socket:\\[222\\]",
                ),
            ):
                helper._listening_pids(9988, proc_root)

    def test_stops_matching_stale_listener(self) -> None:
        config_path = Path(
            "/repo/deploy/docker/scripts/vss_orchestrator_mcp_config.yml"
        )
        command = (
            "/repo/services/agent/.venv/bin/python",
            "/repo/services/agent/.venv/bin/nat",
            "mcp",
            "serve",
            "--config_file",
            str(config_path),
            "--port",
            "9988",
        )
        identity = (1000, Path("/repo/services/agent"), "12345", 1234, command)
        with (
            mock.patch.object(
                helper,
                "_listening_pids",
                side_effect=({1234}, {1234}, set()),
            ),
            mock.patch.object(
                helper,
                "_read_process_identity",
                return_value=identity,
            ),
            mock.patch.object(helper.os, "geteuid", return_value=1000),
            mock.patch.object(helper.os, "killpg") as kill,
            mock.patch.object(helper.time, "sleep"),
        ):
            stopped = helper.stop_existing_orchestrator_mcp_listener(
                9988,
                config_path,
                "/repo/services/agent",
            )

        self.assertEqual(stopped, [1234])
        kill.assert_called_once_with(1234, signal.SIGTERM)

    def test_stops_legacy_uv_wrapper_process_group(self) -> None:
        config_path = Path(
            "/repo/deploy/docker/scripts/vss_orchestrator_mcp_config.yml"
        )
        listener_command = (
            "/repo/services/agent/.venv/bin/python",
            "/repo/services/agent/.venv/bin/nat",
            "mcp",
            "serve",
            "--config_file",
            str(config_path),
            "--port",
            "9988",
        )
        leader_command = (
            "uv",
            "run",
            "nat",
            "mcp",
            "serve",
            "--config_file",
            str(config_path),
            "--port",
            "9988",
        )
        identities = {
            2002: (
                1000,
                Path("/repo/services/agent"),
                "listener-start",
                2001,
                listener_command,
            ),
            2001: (
                1000,
                Path("/repo/services/agent"),
                "leader-start",
                2001,
                leader_command,
            ),
        }
        with (
            mock.patch.object(
                helper,
                "_listening_pids",
                side_effect=({2002}, {2002}, set()),
            ),
            mock.patch.object(
                helper,
                "_read_process_identity",
                side_effect=lambda pid: identities[pid],
            ),
            mock.patch.object(helper.os, "geteuid", return_value=1000),
            mock.patch.object(helper.os, "killpg") as killpg,
            mock.patch.object(helper.time, "sleep"),
        ):
            stopped = helper.stop_existing_orchestrator_mcp_listener(
                9988,
                config_path,
                "/repo/services/agent",
            )

        self.assertEqual(stopped, [2002])
        killpg.assert_called_once_with(2001, signal.SIGTERM)

    def test_listener_set_change_signals_none(self) -> None:
        config_path = Path("/repo/vss_orchestrator_mcp_config.yml")
        command = (
            "nat",
            "mcp",
            "serve",
            "--config_file",
            str(config_path),
            "--port",
            "9988",
        )
        identity = (1000, Path("/repo/services/agent"), "12345", 1234, command)
        with (
            mock.patch.object(
                helper,
                "_listening_pids",
                side_effect=({1234}, {1234, 5678}),
            ),
            mock.patch.object(
                helper,
                "_read_process_identity",
                return_value=identity,
            ),
            mock.patch.object(helper.os, "geteuid", return_value=1000),
            mock.patch.object(helper.os, "killpg") as killpg,
            self.assertRaisesRegex(RuntimeError, "listener set changed"),
        ):
            helper.stop_existing_orchestrator_mcp_listener(
                9988,
                config_path,
                "/repo/services/agent",
            )

        killpg.assert_not_called()

    def test_refuses_unrelated_listener(self) -> None:
        with (
            mock.patch.object(helper, "_listening_pids", return_value={5678}),
            mock.patch.object(
                helper,
                "_read_process_identity",
                return_value=(
                    1000,
                    Path("/repo/services/agent"),
                    "12345",
                    5678,
                    ("python", "unrelated_server.py", "--port", "9988"),
                ),
            ),
            mock.patch.object(helper.os, "geteuid", return_value=1000),
            mock.patch.object(helper.os, "killpg") as kill,
            self.assertRaisesRegex(
                RuntimeError,
                "Refusing to stop an unrelated process",
            ),
        ):
            helper.stop_existing_orchestrator_mcp_listener(
                9988,
                "/repo/vss_orchestrator_mcp_config.yml",
                "/repo/services/agent",
            )

        kill.assert_not_called()

    def test_returns_without_signals_when_port_is_free(self) -> None:
        with (
            mock.patch.object(helper, "_listening_pids", return_value=set()),
            mock.patch.object(helper.os, "killpg") as kill,
        ):
            stopped = helper.stop_existing_orchestrator_mcp_listener(
                9988,
                "/repo/vss_orchestrator_mcp_config.yml",
                "/repo/services/agent",
            )

        self.assertEqual(stopped, [])
        kill.assert_not_called()

    def test_refuses_wrong_uid_without_signaling(self) -> None:
        command = (
            "nat",
            "mcp",
            "serve",
            "--config_file",
            "/repo/vss_orchestrator_mcp_config.yml",
            "--port",
            "9988",
        )
        with (
            mock.patch.object(helper, "_listening_pids", return_value={9012}),
            mock.patch.object(
                helper,
                "_read_process_identity",
                return_value=(
                    2000,
                    Path("/repo/services/agent"),
                    "12345",
                    9012,
                    command,
                ),
            ),
            mock.patch.object(helper.os, "geteuid", return_value=1000),
            mock.patch.object(helper.os, "killpg") as kill,
            self.assertRaisesRegex(
                RuntimeError, "Refusing to stop an unrelated process"
            ),
        ):
            helper.stop_existing_orchestrator_mcp_listener(
                9988,
                "/repo/vss_orchestrator_mcp_config.yml",
                "/repo/services/agent",
            )

        kill.assert_not_called()

    def test_mixed_listener_batch_signals_none(self) -> None:
        config_path = "/repo/vss_orchestrator_mcp_config.yml"
        expected_command = (
            "nat",
            "mcp",
            "serve",
            "--config_file",
            config_path,
            "--port",
            "9988",
        )
        identities = {
            1234: (
                1000,
                Path("/repo/services/agent"),
                "12345",
                1234,
                expected_command,
            ),
            5678: (
                1000,
                Path("/repo/services/agent"),
                "67890",
                5678,
                ("python", "unrelated_server.py", "--port", "9988"),
            ),
        }
        with (
            mock.patch.object(helper, "_listening_pids", return_value={1234, 5678}),
            mock.patch.object(
                helper,
                "_read_process_identity",
                side_effect=lambda pid: identities[pid],
            ),
            mock.patch.object(helper.os, "geteuid", return_value=1000),
            mock.patch.object(helper.os, "killpg") as kill,
            self.assertRaisesRegex(
                RuntimeError, "Refusing to stop an unrelated process"
            ),
        ):
            helper.stop_existing_orchestrator_mcp_listener(
                9988,
                config_path,
                "/repo/services/agent",
            )

        kill.assert_not_called()


class CheckMcpHealthTests(unittest.TestCase):
    def test_rejects_stale_runtime_provenance(self) -> None:
        result = mock.Mock(
            returncode=0,
            stdout='{"status":"success","profiles":["base"]}',
            stderr="",
        )
        with mock.patch.object(helper.subprocess, "run", return_value=result):
            healthy, message = helper.check_mcp_health(
                "http://127.0.0.1:9988/mcp",
                "/repo/services/agent",
                expected_instance_id="new-instance",
                expected_source_sha256="f" * 64,
                expected_git_sha="a" * 40,
            )

        self.assertFalse(healthy)
        self.assertIn("stale or unexpected runtime", message)
        self.assertIn("runtime_instance_id=None", message)

    def test_accepts_exact_runtime_provenance(self) -> None:
        source_sha = "f" * 64
        git_sha = "a" * 40
        result = mock.Mock(
            returncode=0,
            stdout=(
                '{"status":"success","profiles":["base"],'
                '"runtime_instance_id":"new-instance",'
                f'"runtime_source_sha256":"{source_sha}",'
                f'"runtime_git_sha":"{git_sha}"}}'
            ),
            stderr="",
        )
        with mock.patch.object(helper.subprocess, "run", return_value=result):
            healthy, message = helper.check_mcp_health(
                "http://127.0.0.1:9988/mcp",
                "/repo/services/agent",
                expected_instance_id="new-instance",
                expected_source_sha256=source_sha,
                expected_git_sha=git_sha,
            )

        self.assertTrue(healthy)
        self.assertEqual(message, "VSS Orchestrator MCP health check succeeded")


if __name__ == "__main__":
    unittest.main()
