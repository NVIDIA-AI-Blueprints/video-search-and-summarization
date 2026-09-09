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

H100_UUIDS = [
    "GPU-11111111-1111-1111-1111-111111111111",
    "GPU-22222222-2222-2222-2222-222222222222",
]
H100_SMI_OUTPUT = (
    f"GPU 0: NVIDIA H100 80GB HBM3 (UUID: {H100_UUIDS[0]})\n"
    f"GPU 1: NVIDIA H100 80GB HBM3 (UUID: {H100_UUIDS[1]})\n"
)

MIG_UUIDS = [
    "GPU-33333333-3333-3333-3333-333333333333",
    "MIG-44444444-4444-4444-4444-444444444444",
    "MIG-55555555-5555-5555-5555-555555555555",
]
MIG_SMI_OUTPUT = (
    f"GPU 0: NVIDIA A100-SXM4-40GB (UUID: {MIG_UUIDS[0]})\n"
    f"  MIG 3g.20gb     Device  0: (UUID: {MIG_UUIDS[1]})\n"
    f"  MIG 1g.5gb      Device  1: (UUID: {MIG_UUIDS[2]})\n"
)


def _nvidia_smi(stdout: str = "", *, returncode: int = 0, stderr: str = ""):
    """Answer `nvidia-smi -L` with the given result and reject any other command."""

    def run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd != ["nvidia-smi", "-L"]:
            raise AssertionError(f"unexpected command: {cmd}")
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    return run


class GpuDeviceIdsTests(unittest.TestCase):
    def test_returns_every_index_and_uuid(self) -> None:
        with (
            mock.patch.object(helper.shutil, "which", return_value="/usr/bin/nvidia-smi"),
            mock.patch.object(helper.subprocess, "run", side_effect=_nvidia_smi(H100_SMI_OUTPUT)) as run,
        ):
            indices, device_ids = helper.gpu_device_ids()
        self.assertEqual(indices, ["0", "1"])
        self.assertEqual(device_ids, ["0", "1", *H100_UUIDS])
        run.assert_called_once()

    def test_accepts_mig_uuids_as_devices_but_not_as_indices(self) -> None:
        # A *_DEVICE_ID may name a MIG instance, but the indented MIG lines are
        # not GPU indices — a single physical GPU still reports one index.
        with (
            mock.patch.object(helper.shutil, "which", return_value="/usr/bin/nvidia-smi"),
            mock.patch.object(helper.subprocess, "run", side_effect=_nvidia_smi(MIG_SMI_OUTPUT)),
        ):
            indices, device_ids = helper.gpu_device_ids()
        self.assertEqual(indices, ["0"])
        self.assertEqual(device_ids, ["0", *MIG_UUIDS])

    def test_raises_when_nvidia_smi_is_missing(self) -> None:
        with (
            mock.patch.object(helper.shutil, "which", return_value=None),
            mock.patch.object(helper.subprocess, "run", side_effect=AssertionError("nvidia-smi must not run")),
            self.assertRaises(RuntimeError) as ctx,
        ):
            helper.gpu_device_ids()
        self.assertIn("nvidia-smi is not installed", str(ctx.exception))

    def test_raises_with_the_exit_code_and_stderr_when_the_command_fails(self) -> None:
        with (
            mock.patch.object(helper.shutil, "which", return_value="/usr/bin/nvidia-smi"),
            mock.patch.object(
                helper.subprocess,
                "run",
                side_effect=_nvidia_smi(returncode=9, stderr="Failed to initialize NVML: Driver/library mismatch\n"),
            ),
            self.assertRaises(RuntimeError) as ctx,
        ):
            helper.gpu_device_ids()
        self.assertIn("exit code 9", str(ctx.exception))
        self.assertIn("Driver/library mismatch", str(ctx.exception))

    def test_reports_stdout_when_a_failure_writes_nothing_to_stderr(self) -> None:
        with (
            mock.patch.object(helper.shutil, "which", return_value="/usr/bin/nvidia-smi"),
            mock.patch.object(
                helper.subprocess,
                "run",
                side_effect=_nvidia_smi("No devices were found\n", returncode=1),
            ),
            self.assertRaises(RuntimeError) as ctx,
        ):
            helper.gpu_device_ids()
        self.assertIn("No devices were found", str(ctx.exception))

    def test_reports_a_silent_failure_as_no_output(self) -> None:
        with (
            mock.patch.object(helper.shutil, "which", return_value="/usr/bin/nvidia-smi"),
            mock.patch.object(helper.subprocess, "run", side_effect=_nvidia_smi(returncode=1)),
            self.assertRaises(RuntimeError) as ctx,
        ):
            helper.gpu_device_ids()
        self.assertIn("(no output)", str(ctx.exception))

    def test_raises_when_the_command_succeeds_without_listing_a_gpu(self) -> None:
        with (
            mock.patch.object(helper.shutil, "which", return_value="/usr/bin/nvidia-smi"),
            mock.patch.object(helper.subprocess, "run", side_effect=_nvidia_smi("\n")),
            self.assertRaises(RuntimeError) as ctx,
        ):
            helper.gpu_device_ids()
        self.assertIn("listed no GPUs", str(ctx.exception))


class RequireGpuDeviceTests(unittest.TestCase):
    KNOWN = ["0", "1", *H100_UUIDS]
    REMEDY = "Fix it in section 1.2, or leave it blank for the profile default."

    def test_accepts_an_index_or_uuid_from_a_cached_inventory(self) -> None:
        with mock.patch.object(helper, "gpu_device_ids", side_effect=AssertionError("nvidia-smi must not run")):
            for device_id in ("0", "1", *H100_UUIDS):
                with self.subTest(device_id=device_id):
                    helper.require_gpu_device(
                        "LLM_DEVICE_ID",
                        device_id,
                        remedy=self.REMEDY,
                        known_device_ids=self.KNOWN,
                    )

    def test_accepts_a_mig_uuid(self) -> None:
        with mock.patch.object(helper, "gpu_device_ids", side_effect=AssertionError("nvidia-smi must not run")):
            helper.require_gpu_device(
                "NEMOCLAW_VLLM_GPU_DEVICE",
                MIG_UUIDS[1],
                remedy=self.REMEDY,
                known_device_ids=["0", *MIG_UUIDS],
            )

    def test_accepts_a_blank_value_without_reading_the_host(self) -> None:
        # Blank leaves the default to whatever consumes the setting, so the
        # check must not even ask what GPUs the host has.
        with mock.patch.object(helper, "gpu_device_ids", side_effect=AssertionError("nvidia-smi must not run")):
            helper.require_gpu_device("VLM_DEVICE_ID", "", remedy=self.REMEDY)

    def test_names_the_setting_the_known_ids_and_the_remedy_when_it_fails(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            helper.require_gpu_device(
                "VLM_DEVICE_ID",
                "3",
                remedy=self.REMEDY,
                known_device_ids=self.KNOWN,
            )
        message = str(ctx.exception)
        self.assertIn("VLM_DEVICE_ID=3", message)
        self.assertIn(", ".join(self.KNOWN), message)
        self.assertIn(self.REMEDY, message)

    def test_reads_the_host_once_when_no_inventory_is_cached(self) -> None:
        with (
            mock.patch.object(helper.shutil, "which", return_value="/usr/bin/nvidia-smi"),
            mock.patch.object(helper.subprocess, "run", side_effect=_nvidia_smi(H100_SMI_OUTPUT)) as run,
        ):
            helper.require_gpu_device("LLM_DEVICE_ID", "1", remedy=self.REMEDY)
            with self.assertRaises(RuntimeError) as ctx:
                helper.require_gpu_device("VLM_DEVICE_ID", "2", remedy=self.REMEDY)
        self.assertIn("VLM_DEVICE_ID=2", str(ctx.exception))
        self.assertEqual(run.call_count, 2)

    def test_propagates_a_host_inventory_failure(self) -> None:
        with (
            mock.patch.object(helper.shutil, "which", return_value=None),
            self.assertRaises(RuntimeError) as ctx,
        ):
            helper.require_gpu_device("LLM_DEVICE_ID", "0", remedy=self.REMEDY)
        self.assertIn("nvidia-smi is not installed", str(ctx.exception))


class SupportedHardwareProfilesTests(unittest.TestCase):
    CONFIG_PATH = Path(__file__).parents[1] / "vss_orchestrator_mcp_config.yml"

    BLOCK = """\
functions:
  vss_orchestrator:
    model_resolution:
      hardware:
        edge_profiles:
        - DGX-SPARK
        edge_device_ids:
          llm: "0"
        # Keys define the set of supported hardware profiles.
        hardware_profiles:
          H100:
          RTXPRO4500BW:
            alerts:
              RTVI_VLM_MAX_MODEL_LEN: "18000"
          GB300:
            # A comment inside the block.
            VSS_RT_VLM_TAG: "develop-latest-sbsa"
            alerts:
              RTVI_VLLM_GPU_MEMORY_UTILIZATION: "0.2"
          OTHER:
        profile_mode_to_env_modes:
          alerts:
            verification: 2d_cv
"""

    def _write(self, text: str) -> Path:
        path = Path(tempfile.mkdtemp()) / "config.yml"
        path.write_text(text)
        return path

    def test_returns_only_the_profile_keys_in_file_order(self) -> None:
        # Env overrides, per-profile blocks and comments all sit inside the
        # block; none of them is a HARDWARE_PROFILE value.
        profiles = helper.supported_hardware_profiles(self._write(self.BLOCK))
        self.assertEqual(profiles, ("H100", "RTXPRO4500BW", "GB300", "OTHER"))

    def test_stops_at_the_next_sibling_block(self) -> None:
        profiles = helper.supported_hardware_profiles(self._write(self.BLOCK))
        self.assertNotIn("profile_mode_to_env_modes", profiles)
        self.assertNotIn("alerts", profiles)

    def test_reads_the_checked_in_config(self) -> None:
        profiles = helper.supported_hardware_profiles(self.CONFIG_PATH)
        # The values section 1.1 offers, and what docker_generate accepts.
        for expected in ("H100", "GB300", "L40S", "DGX-SPARK", "IGX-THOR", "AGX-THOR", "OTHER"):
            with self.subTest(profile=expected):
                self.assertIn(expected, profiles)
        self.assertNotIn("alerts", profiles)

    def test_raises_when_the_block_is_absent(self) -> None:
        absent = "functions:\n  vss_orchestrator:\n    include:\n    - profiles\n"
        with self.assertRaises(ValueError) as ctx:
            helper.supported_hardware_profiles(self._write(absent))
        self.assertIn("hardware_profiles", str(ctx.exception))

    def test_raises_when_the_block_declares_nothing(self) -> None:
        empty = (
            "    model_resolution:\n      hardware:\n        hardware_profiles:\n"
            "        edge_profiles:\n        - DGX-SPARK\n"
        )
        with self.assertRaises(ValueError) as ctx:
            helper.supported_hardware_profiles(self._write(empty))
        self.assertIn("hardware_profiles", str(ctx.exception))

    def test_raises_when_the_config_is_missing(self) -> None:
        with self.assertRaises(FileNotFoundError):
            helper.supported_hardware_profiles(Path(tempfile.mkdtemp()) / "absent.yml")


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


class SandboxHostCidrsTests(unittest.TestCase):
    @staticmethod
    def _docker(networks: str, inspected: str):
        """Answer the ps / inspect / network-inspect calls the helper makes."""

        def run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd[1] == "ps":
                return subprocess.CompletedProcess(cmd, 0, stdout="openshell-demo-abc\n")
            if cmd[1] == "inspect":
                return subprocess.CompletedProcess(cmd, 0, stdout=networks)
            if cmd[1:3] == ["network", "inspect"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=inspected)
            raise AssertionError(f"unexpected command: {cmd}")

        return run

    def test_returns_the_subnets_of_every_attached_network(self) -> None:
        run = self._docker(
            '{"openshell-docker": {"Gateway": "172.19.0.1"}}',
            '[{"Name": "openshell-docker",'
            ' "IPAM": {"Config": [{"Subnet": "172.19.0.0/16", "Gateway": "172.19.0.1"}]}}]',
        )
        with mock.patch.object(helper.subprocess, "run", side_effect=run):
            self.assertEqual(helper.sandbox_host_cidrs("demo"), ["172.19.0.0/16"])

    def test_sorts_by_network_address_and_drops_ipv6(self) -> None:
        run = self._docker(
            '{"a": {}, "b": {}}',
            '[{"IPAM": {"Config": [{"Subnet": "172.20.0.0/16"}, {"Subnet": "fd00::/64"}]}},'
            ' {"IPAM": {"Config": [{"Subnet": "172.19.0.0/16"}]}}]',
        )
        with mock.patch.object(helper.subprocess, "run", side_effect=run):
            self.assertEqual(
                helper.sandbox_host_cidrs("demo"),
                ["172.19.0.0/16", "172.20.0.0/16"],
            )

    def test_skips_a_subnet_that_is_not_an_address_range(self) -> None:
        # The result is written into a policy file, so anything unparseable is
        # dropped rather than passed through.
        run = self._docker(
            '{"a": {}}',
            '[{"IPAM": {"Config": [{"Subnet": "not-a-cidr"}, {"Subnet": "172.19.0.0/16"}]}}]',
        )
        with mock.patch.object(helper.subprocess, "run", side_effect=run):
            self.assertEqual(helper.sandbox_host_cidrs("demo"), ["172.19.0.0/16"])

    def test_returns_empty_when_the_sandbox_is_absent(self) -> None:
        result = subprocess.CompletedProcess(["docker", "ps"], 0, stdout="\n")
        with mock.patch.object(helper.subprocess, "run", return_value=result):
            self.assertEqual(helper.sandbox_host_cidrs("demo"), [])

    def test_returns_empty_when_docker_inspect_fails(self) -> None:
        def run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd[1] == "ps":
                return subprocess.CompletedProcess(cmd, 0, stdout="openshell-demo-abc\n")
            raise subprocess.CalledProcessError(1, cmd)

        with mock.patch.object(helper.subprocess, "run", side_effect=run):
            self.assertEqual(helper.sandbox_host_cidrs("demo"), [])

    def test_returns_empty_when_docker_stops_responding(self) -> None:
        def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            self.assertEqual(kwargs.get("timeout"), helper.DOCKER_QUERY_TIMEOUT_S)
            if cmd[1] == "ps":
                return subprocess.CompletedProcess(cmd, 0, stdout="openshell-demo-abc\n")
            raise subprocess.TimeoutExpired(cmd, helper.DOCKER_QUERY_TIMEOUT_S)

        with mock.patch.object(helper.subprocess, "run", side_effect=run):
            self.assertEqual(helper.sandbox_host_cidrs("demo"), [])

    def test_returns_empty_when_the_container_has_no_networks(self) -> None:
        def run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd[1] == "ps":
                return subprocess.CompletedProcess(cmd, 0, stdout="openshell-demo-abc\n")
            if cmd[1] == "inspect":
                return subprocess.CompletedProcess(cmd, 0, stdout="{}\n")
            raise AssertionError("network inspect must not run without a network")

        with mock.patch.object(helper.subprocess, "run", side_effect=run):
            self.assertEqual(helper.sandbox_host_cidrs("demo"), [])


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
