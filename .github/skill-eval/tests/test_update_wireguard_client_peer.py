# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for atomic WireGuard GPU client peer updates."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "update_wireguard_client_peer",
    Path(__file__).resolve().parents[1]
    / "ops"
    / "postgres-ha"
    / "update-wireguard-client-peer.py",
)
update_peer = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = update_peer
_SPEC.loader.exec_module(update_peer)
OPS_DIR = Path(__file__).resolve().parents[1] / "ops" / "postgres-ha"

KEY_A = "A" * 43 + "="
KEY_B = "B" * 43 + "="
KEY_C = "C" * 43 + "="
OPERATION_A = "9d13aa12-7567-47a8-a45c-47e06ee9090d"
OPERATION_B = "7f94d868-68cf-412f-973a-b7a820a9a7d2"
BASE_CONFIG = f"""[Interface]
Address = 10.203.142.1/32

# BEGIN VSS CLIENT gpu-a
[Peer]
PublicKey = {KEY_A}
AllowedIPs = 10.203.142.101/32
# END VSS CLIENT gpu-a
"""


class UpdateWireGuardClientPeerTests(unittest.TestCase):
    def test_rollout_scripts_share_lock_and_use_operation_scoped_staging(self):
        enrollment = (OPS_DIR / "enroll-postgres-client.sh").read_text(encoding="utf-8")
        node_install = (OPS_DIR / "install-wireguard-node.sh").read_text(
            encoding="utf-8"
        )
        client_install = (OPS_DIR / "install-wireguard-client.sh").read_text(
            encoding="utf-8"
        )
        runner_stage = (OPS_DIR.parent / "stage-distributed-runners.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            'server_helper="/run/vss-update-wireguard-client-peer-${worker}-${operation_id}.py"',
            enrollment,
        )
        self.assertIn(
            'client_backup="/run/vss-wireguard-client-${worker}-${operation_id}.rollback"',
            enrollment,
        )
        self.assertIn(
            'remote_dir="/tmp/vss-postgres-client-install-${operation_id}"',
            enrollment,
        )
        self.assertIn("/run/vss-wireguard-peer-update.lock", node_install)
        self.assertIn("flock -x 9", node_install)
        self.assertIn('server_lock_indexes+=("$index")', enrollment)
        self.assertIn("if ((EUID != 0)); then", client_install)
        self.assertIn("/run/vss-wireguard-client-enrollment", enrollment)
        self.assertIn("/run/vss-wireguard-client-enrollment", client_install)
        self.assertIn("--mode renew", enrollment)
        self.assertIn("masked-runtime", enrollment)
        self.assertIn(
            "'$remote_dir/install-wireguard-client.sh' '$remote_dir' '$operation_id'",
            enrollment,
        )
        self.assertIn(
            'FLEET_LOCK_KEY = "__fleet_enrollment__"',
            _SPEC.loader.get_source(_SPEC.name),
        )
        for installer in (node_install, client_install):
            self.assertNotIn("systemctl mask --now wg-quick@wg-vss.service", installer)
            self.assertLess(
                installer.index("systemctl stop wg-quick@wg-vss.service"),
                installer.index("systemctl mask wg-quick@wg-vss.service"),
            )
            self.assertLess(
                installer.index("systemctl mask wg-quick@wg-vss.service"),
                installer.index('ufw_status="$(sudo ufw status verbose)"'),
            )
            self.assertIn("ip link show dev wg-vss", installer)
            self.assertIn(
                '$0 == expected || index($0, expected " comment ") == 1', installer
            )
        self.assertIn("ufw insert 1 deny in on wg-vss", node_install)
        self.assertIn("ufw insert 1 deny in on wg-vss", client_install)
        self.assertIn("Rollback restored files but left WireGuard masked", enrollment)
        self.assertLess(
            enrollment.index("systemctl unmask wg-quick@wg-vss.service"),
            enrollment.index("systemctl enable wg-quick@wg-vss.service"),
        )
        self.assertLess(
            enrollment.index("Rollback restored files but left WireGuard masked"),
            enrollment.index("--mode unlock"),
        )
        self.assertIn("exec 9>/run/vss-runner-stage-lock/lock", runner_stage)
        self.assertIn("verify_runner_quiescence", runner_stage)
        self.assertLess(
            runner_stage.index('rm -rf "$remote_dir"\nfor service'),
            runner_stage.index('systemctl unmask --runtime "$service"'),
        )

    def test_validation_rejects_duplicate_address_or_key(self):
        with self.assertRaisesRegex(SystemExit, "address"):
            update_peer.validate(
                BASE_CONFIG,
                name="gpu-b",
                public_key=KEY_B,
                address="10.203.142.101/32",
            )
        with self.assertRaisesRegex(SystemExit, "public key"):
            update_peer.validate(
                BASE_CONFIG,
                name="gpu-b",
                public_key=KEY_A,
                address="10.203.142.102/32",
            )

    def test_validation_rejects_malformed_blocks(self):
        malformed = BASE_CONFIG.replace("# END VSS CLIENT gpu-a\n", "")
        with self.assertRaisesRegex(SystemExit, "malformed"):
            update_peer.validate(
                malformed,
                name="gpu-b",
                public_key=KEY_B,
                address="10.203.142.102/32",
            )

    @mock.patch.object(update_peer.subprocess, "run")
    def test_key_rotation_writes_config_before_removing_old_peer(self, run):
        run.return_value = subprocess.CompletedProcess(
            [],
            0,
            stdout="",
            stderr="",
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "wg-vss.conf"
            path.write_text(BASE_CONFIG, encoding="utf-8")
            lock_path = Path(temporary) / "update.lock"
            registry_path = Path(temporary) / "enrollments.json"
            update_peer.update_operation_lock(
                name="gpu-a",
                operation_id=OPERATION_A,
                acquire=True,
                lock_path=lock_path,
                registry_path=registry_path,
            )

            update_peer.update_config(
                path,
                name="gpu-a",
                public_key=KEY_C,
                address="10.203.142.101/32",
                operation_id=OPERATION_A,
                expected_public_key=KEY_A,
                expected_address="10.203.142.101/32",
                lock_path=lock_path,
                registry_path=registry_path,
            )

            text = path.read_text(encoding="utf-8")
            self.assertIn(f"PublicKey = {KEY_C}", text)
            self.assertNotIn(f"PublicKey = {KEY_A}", text)
            self.assertEqual(
                run.call_args_list,
                [
                    mock.call(
                        ["wg", "set", "wg-vss", "peer", KEY_A, "remove"],
                        check=True,
                    ),
                    mock.call(
                        ["ip", "route", "del", "10.203.142.101/32", "dev", "wg-vss"],
                        check=False,
                        capture_output=True,
                        text=True,
                    ),
                    mock.call(
                        [
                            "wg",
                            "set",
                            "wg-vss",
                            "peer",
                            KEY_C,
                            "allowed-ips",
                            "10.203.142.101/32",
                        ],
                        check=True,
                    ),
                    mock.call(
                        [
                            "ip",
                            "route",
                            "replace",
                            "10.203.142.101/32",
                            "dev",
                            "wg-vss",
                        ],
                        check=True,
                    ),
                ],
            )

    @mock.patch.object(update_peer.subprocess, "run")
    def test_distinct_enrollments_are_fleet_serialized(self, run):
        run.return_value = subprocess.CompletedProcess(
            [],
            0,
            stdout="",
            stderr="",
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "wg-vss.conf"
            path.write_text(
                "[Interface]\nAddress = 10.203.142.1/32\n", encoding="utf-8"
            )
            lock = Path(temporary) / "update.lock"
            registry = Path(temporary) / "enrollments.json"
            update_peer.update_operation_lock(
                name="gpu-a",
                operation_id=OPERATION_A,
                acquire=True,
                lock_path=lock,
                registry_path=registry,
            )
            with self.assertRaisesRegex(SystemExit, "another"):
                update_peer.update_operation_lock(
                    name="gpu-b",
                    operation_id=OPERATION_B,
                    acquire=True,
                    lock_path=lock,
                    registry_path=registry,
                )
            update_peer.update_config(
                path,
                name="gpu-a",
                public_key=KEY_A,
                address="10.203.142.101/32",
                operation_id=OPERATION_A,
                lock_path=lock,
                registry_path=registry,
            )
            update_peer.update_operation_lock(
                name="gpu-a",
                operation_id=OPERATION_A,
                acquire=False,
                lock_path=lock,
                registry_path=registry,
            )
            update_peer.update_operation_lock(
                name="gpu-b",
                operation_id=OPERATION_B,
                acquire=True,
                lock_path=lock,
                registry_path=registry,
            )
            update_peer.update_config(
                path,
                name="gpu-b",
                public_key=KEY_B,
                address="10.203.142.102/32",
                operation_id=OPERATION_B,
                lock_path=lock,
                registry_path=registry,
            )

            text = path.read_text(encoding="utf-8")
            self.assertIn("# BEGIN VSS CLIENT gpu-a", text)
            self.assertIn("# BEGIN VSS CLIENT gpu-b", text)

    @mock.patch.object(update_peer.subprocess, "run")
    def test_runtime_failure_restores_previous_config(self, run):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "wg-vss.conf"
            path.write_text(BASE_CONFIG, encoding="utf-8")
            lock_path = Path(temporary) / "update.lock"
            registry_path = Path(temporary) / "enrollments.json"
            update_peer.update_operation_lock(
                name="gpu-a",
                operation_id=OPERATION_A,
                acquire=True,
                lock_path=lock_path,
                registry_path=registry_path,
            )
            completed = subprocess.CompletedProcess(
                [],
                0,
                stdout="",
                stderr="",
            )
            run.side_effect = [
                completed,
                completed,
                subprocess.CalledProcessError(1, ["wg", "set"]),
                completed,
                completed,
                completed,
                completed,
            ]

            with self.assertRaises(subprocess.CalledProcessError):
                update_peer.update_config(
                    path,
                    name="gpu-a",
                    public_key=KEY_C,
                    address="10.203.142.102/32",
                    operation_id=OPERATION_A,
                    expected_public_key=KEY_A,
                    expected_address="10.203.142.101/32",
                    lock_path=lock_path,
                    registry_path=registry_path,
                )

            self.assertEqual(path.read_text(encoding="utf-8"), BASE_CONFIG)

    def test_stale_cleanup_cannot_modify_new_operation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "wg-vss.conf"
            current = BASE_CONFIG.replace(KEY_A, KEY_C)
            path.write_text(current, encoding="utf-8")
            lock_path = Path(temporary) / "update.lock"
            registry_path = Path(temporary) / "enrollments.json"
            update_peer.update_operation_lock(
                name="gpu-a",
                operation_id=OPERATION_B,
                acquire=True,
                lock_path=lock_path,
                registry_path=registry_path,
            )

            with self.assertRaisesRegex(SystemExit, "is not owned"):
                update_peer.restore_config(
                    path,
                    name="gpu-a",
                    public_key=KEY_C,
                    address="10.203.142.101/32",
                    previous_public_key=KEY_A,
                    previous_address="10.203.142.101/32",
                    operation_id=OPERATION_A,
                    lock_path=lock_path,
                    registry_path=registry_path,
                )

            self.assertEqual(path.read_text(encoding="utf-8"), current)

    def test_apply_rejects_peer_changed_after_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "wg-vss.conf"
            current = BASE_CONFIG.replace(KEY_A, KEY_C)
            path.write_text(current, encoding="utf-8")
            lock_path = Path(temporary) / "update.lock"
            registry_path = Path(temporary) / "enrollments.json"
            update_peer.update_operation_lock(
                name="gpu-a",
                operation_id=OPERATION_A,
                acquire=True,
                lock_path=lock_path,
                registry_path=registry_path,
            )

            with self.assertRaisesRegex(SystemExit, "changed after"):
                update_peer.update_config(
                    path,
                    name="gpu-a",
                    public_key=KEY_B,
                    address="10.203.142.102/32",
                    operation_id=OPERATION_A,
                    expected_public_key=KEY_A,
                    expected_address="10.203.142.101/32",
                    lock_path=lock_path,
                    registry_path=registry_path,
                )

            self.assertEqual(path.read_text(encoding="utf-8"), current)

    @mock.patch.object(update_peer.subprocess, "run")
    def test_restore_uses_exact_server_preimage(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "wg-vss.conf"
            path.write_text(BASE_CONFIG, encoding="utf-8")
            lock_path = Path(temporary) / "update.lock"
            registry_path = Path(temporary) / "enrollments.json"
            update_peer.update_operation_lock(
                name="gpu-a",
                operation_id=OPERATION_A,
                acquire=True,
                lock_path=lock_path,
                registry_path=registry_path,
            )
            snapshot = update_peer.peer_snapshot(
                path,
                name="gpu-a",
                public_key=KEY_C,
                address="10.203.142.102/32",
                operation_id=OPERATION_A,
                lock_path=lock_path,
                registry_path=registry_path,
            )
            update_peer.update_config(
                path,
                name="gpu-a",
                public_key=KEY_C,
                address="10.203.142.102/32",
                operation_id=OPERATION_A,
                expected_public_key=KEY_A,
                expected_address="10.203.142.101/32",
                lock_path=lock_path,
                registry_path=registry_path,
            )
            update_peer.restore_config(
                path,
                name="gpu-a",
                public_key=KEY_C,
                address="10.203.142.102/32",
                previous_public_key=str(snapshot["public_key"]),
                previous_address=str(snapshot["address"]),
                operation_id=OPERATION_A,
                lock_path=lock_path,
                registry_path=registry_path,
            )

            restored = path.read_text(encoding="utf-8")
            self.assertIn(f"PublicKey = {KEY_A}", restored)
            self.assertIn("AllowedIPs = 10.203.142.101/32", restored)
            self.assertNotIn(KEY_C, restored)

    def test_fleet_lock_renewal_is_serialized_and_extends_expiry(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "update.lock"
            registry_path = Path(temporary) / "enrollments.json"
            with mock.patch.object(update_peer.time, "time", return_value=100):
                update_peer.update_operation_lock(
                    name="gpu-a",
                    operation_id=OPERATION_A,
                    acquire=True,
                    lock_path=lock_path,
                    registry_path=registry_path,
                )
            with mock.patch.object(update_peer.time, "time", return_value=200):
                update_peer.renew_operation_lock(
                    name="gpu-a",
                    operation_id=OPERATION_A,
                    lock_path=lock_path,
                    registry_path=registry_path,
                )

            records = update_peer.load_operation_records(registry_path, now=200)
            self.assertEqual(
                records[update_peer.FLEET_LOCK_KEY]["expires_at"],
                200 + update_peer.OPERATION_LOCK_TTL_SEC,
            )

    def test_same_worker_enrollment_requires_matching_operation_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "update.lock"
            registry_path = Path(temporary) / "enrollments.json"
            first = OPERATION_A
            second = OPERATION_B
            update_peer.update_operation_lock(
                name="gpu-a",
                operation_id=first,
                acquire=True,
                lock_path=lock_path,
                registry_path=registry_path,
            )
            with self.assertRaisesRegex(SystemExit, "already active"):
                update_peer.update_operation_lock(
                    name="gpu-a",
                    operation_id=second,
                    acquire=True,
                    lock_path=lock_path,
                    registry_path=registry_path,
                )
            with self.assertRaisesRegex(SystemExit, "another enrollment"):
                update_peer.update_operation_lock(
                    name="gpu-a",
                    operation_id=second,
                    acquire=False,
                    lock_path=lock_path,
                    registry_path=registry_path,
                )
            update_peer.update_operation_lock(
                name="gpu-a",
                operation_id=first,
                acquire=False,
                lock_path=lock_path,
                registry_path=registry_path,
            )
            update_peer.update_operation_lock(
                name="gpu-a",
                operation_id=second,
                acquire=True,
                lock_path=lock_path,
                registry_path=registry_path,
            )


if __name__ == "__main__":
    unittest.main()
