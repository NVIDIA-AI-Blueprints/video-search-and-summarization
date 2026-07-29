#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[4]
HELPER_PATH = (
    REPO_ROOT
    / ".github"
    / "skill-eval"
    / "nemoclaw"
    / "repair_legacy_state.py"
)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


legacy_repair = load_module("nemoclaw_legacy_state_repair", HELPER_PATH)
setup_failure = load_module(
    "nemoclaw_setup_failure_for_legacy_repair",
    REPO_ROOT
    / ".github"
    / "skill-eval"
    / "nemoclaw"
    / "setup_failure.py",
)


class LegacyStateRepairTest(unittest.TestCase):
    SECRET = "must-never-reach-an-artifact"

    @staticmethod
    def _write_json(path: Path, value: object, mode: int = 0o600) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(mode)

    def _state(
        self,
        root: Path,
        *,
        session_updates: dict[str, object] | None = None,
        row_updates: dict[str, object] | None = None,
    ) -> tuple[Path, Path]:
        home = root / "home"
        home.mkdir(mode=0o700)
        state_root = home / ".nemoclaw"
        state_root.mkdir(mode=0o700)
        session = {
            "sandboxName": "demo",
            "status": "in_progress",
            "metadata": {"gatewayName": "nemoclaw"},
            "credentialEnv": self.SECRET,
        }
        session.update(session_updates or {})
        row = {
            "name": "demo",
            "gatewayName": "nemoclaw-19080",
            "gatewayPort": 19080,
        }
        row.update(row_updates or {})
        self._write_json(state_root / "onboard-session.json", session)
        self._write_json(
            state_root / "sandboxes.json",
            {
                "defaultSandbox": "demo",
                "sandboxes": {"demo": row},
            },
        )
        return home, state_root

    @staticmethod
    def _env(**updates: str) -> dict[str, str]:
        env = {
            "SKILL_EVAL_NEMOCLAW_CI": "1",
            "NEMOCLAW_RECREATE_SANDBOX": "1",
            "NEMOCLAW_SANDBOX_NAME": "demo",
            "NEMOCLAW_GATEWAY_PORT": "19080",
        }
        env.update(updates)
        return env

    def test_quarantines_only_the_exact_default_session_target_row_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            home, state_root = self._state(Path(td))
            source = state_root / "onboard-session.json"
            original = source.read_text(encoding="utf-8")

            result = legacy_repair.repair_legacy_state(
                home=home,
                environ=self._env(),
            )

            self.assertEqual(result, "quarantined")
            self.assertFalse(source.exists())
            quarantine = state_root / legacy_repair.QUARANTINE_DIR_NAME
            self.assertEqual(quarantine.stat().st_mode & 0o777, 0o700)
            quarantined = list(quarantine.iterdir())
            self.assertEqual(len(quarantined), 1)
            self.assertEqual(quarantined[0].stat().st_mode & 0o777, 0o600)
            self.assertEqual(quarantined[0].read_text(encoding="utf-8"), original)
            self.assertFalse(
                (state_root / legacy_repair.MIGRATION_LOCK_NAME).exists()
            )
            self.assertEqual(
                json.loads(
                    (state_root / "sandboxes.json").read_text(encoding="utf-8")
                )["sandboxes"]["demo"]["gatewayPort"],
                19080,
            )

    def test_cli_output_is_fixed_and_never_echoes_session_values(self):
        with tempfile.TemporaryDirectory() as td:
            home, _ = self._state(Path(td))
            stdout = io.StringIO()
            stderr = io.StringIO()
            env = {**self._env(), "HOME": str(home)}

            with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(
                stdout
            ), contextlib.redirect_stderr(stderr):
                rc = legacy_repair.main([])

            self.assertEqual(rc, 0)
            self.assertIn("quarantined the stale demo session", stdout.getvalue())
            self.assertNotIn(self.SECRET, stdout.getvalue())
            self.assertNotIn(self.SECRET, stderr.getvalue())

    def test_refusal_is_classified_without_copying_raw_state(self):
        diagnostic = setup_failure.classify_setup_failure(
            "credential=must-never-reach-an-artifact\n"
            "NemoClaw legacy-state repair refused: state_permissions_unsafe\n",
            1,
        )

        self.assertEqual(
            diagnostic["categories"],
            ["legacy_state_repair_refused"],
        )
        self.assertNotIn(self.SECRET, json.dumps(diagnostic))

    def test_activation_requires_ci_recreate_demo_and_the_dedicated_port(self):
        cases = (
            {"SKILL_EVAL_NEMOCLAW_CI": "0"},
            {"NEMOCLAW_RECREATE_SANDBOX": "false"},
            {"NEMOCLAW_SANDBOX_NAME": "other"},
            {"NEMOCLAW_GATEWAY_PORT": "8080"},
        )
        for updates in cases:
            with self.subTest(updates=updates), tempfile.TemporaryDirectory() as td:
                home, state_root = self._state(Path(td))

                result = legacy_repair.repair_legacy_state(
                    home=home,
                    environ=self._env(**updates),
                )

                self.assertIn(result, {"disabled", "not_applicable"})
                self.assertTrue((state_root / "onboard-session.json").exists())
                self.assertFalse(
                    (state_root / legacy_repair.QUARANTINE_DIR_NAME).exists()
                )

    def test_does_not_touch_other_or_nonconflicting_session_identities(self):
        cases = (
            (
                {"sandboxName": "other"},
                {},
            ),
            (
                {"metadata": {"gatewayName": "nemoclaw-19080"}},
                {},
            ),
            (
                {},
                {
                    "gatewayName": "nemoclaw",
                    "gatewayPort": 8080,
                },
            ),
        )
        for session_updates, row_updates in cases:
            with (
                self.subTest(
                    session_updates=session_updates,
                    row_updates=row_updates,
                ),
                tempfile.TemporaryDirectory() as td,
            ):
                home, state_root = self._state(
                    Path(td),
                    session_updates=session_updates,
                    row_updates=row_updates,
                )

                result = legacy_repair.repair_legacy_state(
                    home=home,
                    environ=self._env(),
                )

                self.assertEqual(result, "not_applicable")
                self.assertTrue((state_root / "onboard-session.json").exists())

    def test_rejects_symlinked_and_permissive_session_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home, state_root = self._state(root)
            source = state_root / "onboard-session.json"
            outside = root / "outside-session.json"
            source.replace(outside)
            source.symlink_to(outside)

            with self.assertRaisesRegex(
                legacy_repair.LegacyStateRepairError,
                "state_file_unsafe",
            ):
                legacy_repair.repair_legacy_state(
                    home=home,
                    environ=self._env(),
                )

            self.assertTrue(source.is_symlink())
            self.assertIn(self.SECRET, outside.read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as td:
            home, state_root = self._state(Path(td))
            source = state_root / "onboard-session.json"
            source.chmod(0o640)

            with self.assertRaisesRegex(
                legacy_repair.LegacyStateRepairError,
                "state_permissions_unsafe",
            ):
                legacy_repair.repair_legacy_state(
                    home=home,
                    environ=self._env(),
                )

            self.assertTrue(source.exists())

    def test_rejects_state_not_owned_by_the_effective_user(self):
        with tempfile.TemporaryDirectory() as td:
            home, state_root = self._state(Path(td))
            with mock.patch.object(
                legacy_repair.os,
                "getuid",
                return_value=os.getuid() + 1,
            ):
                with self.assertRaisesRegex(
                    legacy_repair.LegacyStateRepairError,
                    "state_owner_mismatch",
                ):
                    legacy_repair.repair_legacy_state(
                        home=home,
                        environ=self._env(),
                    )

            self.assertTrue((state_root / "onboard-session.json").exists())

    def test_rejects_active_or_incomplete_lifecycle_state(self):
        blockers = (
            ("onboard.lock", False),
            (legacy_repair.MIGRATION_INTENT_NAME, True),
        )
        for name, is_directory in blockers:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                home, state_root = self._state(Path(td))
                blocker = state_root / name
                if is_directory:
                    blocker.mkdir(mode=0o700)
                else:
                    blocker.write_text("active", encoding="utf-8")
                    blocker.chmod(0o600)

                with self.assertRaises(
                    legacy_repair.LegacyStateRepairError
                ):
                    legacy_repair.repair_legacy_state(
                        home=home,
                        environ=self._env(),
                    )

                self.assertTrue((state_root / "onboard-session.json").exists())

    def test_rejects_station_and_recreate_recovery_state(self):
        protected_updates = (
            {"stationExpressIntent": {"version": 1}},
            {"stationExpressReceiptRetirement": "generation"},
            {"machine": {"recoveryReceipt": {"id": "receipt"}}},
            {"checkpoint": {"sandboxRecreate": {"phase": "deleting"}}},
        )
        for updates in protected_updates:
            with self.subTest(updates=updates), tempfile.TemporaryDirectory() as td:
                home, state_root = self._state(
                    Path(td),
                    session_updates=updates,
                )

                with self.assertRaisesRegex(
                    legacy_repair.LegacyStateRepairError,
                    "protected_recovery_state_present",
                ):
                    legacy_repair.repair_legacy_state(
                        home=home,
                        environ=self._env(),
                    )

                self.assertTrue((state_root / "onboard-session.json").exists())

    def test_rejects_station_receipts_and_existing_selected_session(self):
        with tempfile.TemporaryDirectory() as td:
            home, state_root = self._state(Path(td))
            receipt = state_root / "station-express-resume"
            receipt.write_text("generation=keep", encoding="utf-8")
            receipt.chmod(0o600)

            with self.assertRaisesRegex(
                legacy_repair.LegacyStateRepairError,
                "protected_recovery_state_present",
            ):
                legacy_repair.repair_legacy_state(
                    home=home,
                    environ=self._env(),
                )

            self.assertTrue((state_root / "onboard-session.json").exists())

        with tempfile.TemporaryDirectory() as td:
            home, state_root = self._state(Path(td))
            selected_session = (
                state_root / "gateways" / "19080" / "onboard-session.json"
            )
            self._write_json(selected_session, {"sandboxName": "demo"})

            with self.assertRaisesRegex(
                legacy_repair.LegacyStateRepairError,
                "selected_session_present",
            ):
                legacy_repair.repair_legacy_state(
                    home=home,
                    environ=self._env(),
                )

            self.assertTrue((state_root / "onboard-session.json").exists())

    def test_rejects_symlinked_quarantine_without_touching_target(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home, state_root = self._state(root)
            outside = root / "outside"
            outside.mkdir(mode=0o700)
            quarantine = state_root / legacy_repair.QUARANTINE_DIR_NAME
            quarantine.symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(
                legacy_repair.LegacyStateRepairError,
                "state_directory_unsafe",
            ):
                legacy_repair.repair_legacy_state(
                    home=home,
                    environ=self._env(),
                )

            self.assertTrue((state_root / "onboard-session.json").exists())
            self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
