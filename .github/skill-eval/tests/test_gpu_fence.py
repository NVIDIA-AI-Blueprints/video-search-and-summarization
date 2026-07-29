# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the GPU-worker generation fence state machine."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

SKILL_EVAL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_EVAL_ROOT))

from gpu_fence import (
    FenceController,
    FenceError,
    FenceRejectedError,
    LeaseValidation,
    WorkerCleanup,
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Validator:
    def __init__(self) -> None:
        self.responses: dict[tuple[str, int], LeaseValidation | Exception] = {}
        self.calls: list[tuple[str, str, int]] = []

    def set(
        self,
        token: str,
        generation: int,
        response: LeaseValidation | Exception,
    ) -> None:
        self.responses[(str(uuid.UUID(token)), generation)] = response

    def validate(self, gpu_id: str, token: str, generation: int) -> LeaseValidation:
        self.calls.append((gpu_id, token, generation))
        response = self.responses[(str(uuid.UUID(token)), generation)]
        if isinstance(response, Exception):
            raise response
        return response


class Cleanup:
    def __init__(self) -> None:
        self.calls: list[tuple[list[int], str]] = []

    def __call__(self, process_groups, reason: str) -> None:
        self.calls.append((list(process_groups), reason))


def initialize_state(path: Path, generation: int = 0) -> None:
    path.write_text(
        json.dumps(
            {
                "boot_id": Path("/proc/sys/kernel/random/boot_id")
                .read_text(encoding="utf-8")
                .strip(),
                "generation": generation,
                "process_groups": [],
            }
        ),
        encoding="utf-8",
    )


class FenceControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp.name) / "high-water.json"
        self.clock = Clock()
        self.validator = Validator()
        self.cleanup = Cleanup()
        initialize_state(self.state_path)
        self.controller = FenceController(
            "gpu-a",
            self.validator,
            state_path=self.state_path,
            cleanup=self.cleanup,
            monotonic=self.clock,
            shutdown_margin_sec=30,
        )
        self.token1 = str(uuid.uuid4())
        self.token2 = str(uuid.uuid4())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def allow(
        self,
        token: str,
        generation: int,
        remaining: float = 90,
    ) -> None:
        self.validator.set(
            token,
            generation,
            LeaseValidation(True, remaining),
        )

    def test_same_generation_claim_is_idempotent_and_persists_high_water(self):
        self.allow(self.token1, 4)
        first = self.controller.claim(self.token1, 4)
        second = self.controller.claim(self.token1, 4)

        self.assertEqual(first.session_id, second.session_id)
        self.assertEqual(self.cleanup.calls, [])
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state["generation"], 4)
        self.assertEqual(state["process_groups"], [])
        self.assertTrue(state["boot_id"])

        restarted = FenceController(
            "gpu-a",
            self.validator,
            state_path=self.state_path,
            cleanup=self.cleanup,
            monotonic=self.clock,
            shutdown_margin_sec=30,
        )
        self.assertEqual(restarted.status()["high_water_generation"], 4)

    def test_higher_generation_fences_old_session_before_admission(self):
        self.allow(self.token1, 4)
        self.allow(self.token2, 5)
        first = self.controller.claim(self.token1, 4)

        with mock.patch("gpu_fence.os.getpgid", return_value=4321):
            self.controller.register(first.session_id, 4321)
        current = self.controller.claim(self.token2, 5)

        self.assertEqual(current.generation, 5)
        self.assertEqual(self.cleanup.calls[0][0], [4321])
        self.assertIn("superseded", self.cleanup.calls[0][1])

    def test_lower_or_conflicting_generation_is_rejected(self):
        self.allow(self.token1, 4)
        self.allow(self.token2, 4)
        self.controller.claim(self.token1, 4)

        with self.assertRaisesRegex(FenceRejectedError, "does not supersede"):
            self.controller.claim(self.token2, 4)

        self.validator.set(
            self.token2,
            3,
            LeaseValidation(True, 90),
        )
        with self.assertRaisesRegex(FenceRejectedError, "high-water"):
            self.controller.claim(self.token2, 3)

    def test_database_outage_is_tolerated_only_until_local_deadline(self):
        self.allow(self.token1, 1, remaining=60)
        self.controller.claim(self.token1, 1)
        self.validator.set(self.token1, 1, FenceError("database unavailable"))

        self.clock.now = 29
        self.assertTrue(self.controller.poll_once())
        self.assertTrue(self.controller.status()["active"])

        self.clock.now = 31
        self.assertFalse(self.controller.poll_once())
        self.assertFalse(self.controller.status()["active"])
        self.assertIn("local worker safety deadline", self.cleanup.calls[-1][1])

    def test_invalid_or_nearly_expired_lease_fails_closed(self):
        self.validator.set(self.token1, 1, LeaseValidation(False, 0))
        with self.assertRaisesRegex(FenceRejectedError, "invalid"):
            self.controller.claim(self.token1, 1)

        self.validator.set(self.token1, 1, LeaseValidation(True, 30))
        with self.assertRaisesRegex(FenceRejectedError, "too close"):
            self.controller.claim(self.token1, 1)

    def test_startup_cleanup_runs_without_resetting_high_water(self):
        self.allow(self.token1, 7)
        self.controller.claim(self.token1, 7)
        self.controller.startup_cleanup()
        self.assertEqual(
            self.cleanup.calls[-1][1],
            "GPU fence daemon startup cleanup",
        )
        self.assertEqual(self.controller.status()["high_water_generation"], 7)

    def test_restart_recovers_persisted_process_groups_for_cleanup(self):
        self.allow(self.token1, 3)
        session = self.controller.claim(self.token1, 3)
        with mock.patch("gpu_fence.os.getpgid", return_value=4321):
            self.controller.register(session.session_id, 4321)

        restart_cleanup = Cleanup()
        restarted = FenceController(
            "gpu-a",
            self.validator,
            state_path=self.state_path,
            cleanup=restart_cleanup,
            monotonic=self.clock,
            shutdown_margin_sec=30,
        )
        restarted.startup_cleanup()

        self.assertEqual(restart_cleanup.calls[0][0], [4321])
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state["process_groups"], [])

    def test_corrupt_state_still_cleans_worker_then_blocks_startup(self):
        self.state_path.write_text("{not-json")
        cleanup = Cleanup()
        controller = FenceController(
            "gpu-a",
            self.validator,
            state_path=self.state_path,
            cleanup=cleanup,
            monotonic=self.clock,
            shutdown_margin_sec=30,
        )

        with self.assertRaisesRegex(FenceError, "operator repair"):
            controller.startup_cleanup()
        self.assertEqual(len(cleanup.calls), 1)
        self.assertIn("invalid persisted fence state", cleanup.calls[0][1])
        self.assertTrue(controller.status()["blocked"])

    def test_missing_state_cleans_worker_and_requires_explicit_reinitialization(self):
        self.state_path.unlink()
        cleanup = Cleanup()
        controller = FenceController(
            "gpu-a",
            self.validator,
            state_path=self.state_path,
            cleanup=cleanup,
            monotonic=self.clock,
            shutdown_margin_sec=30,
        )

        with self.assertRaisesRegex(FenceError, "operator repair"):
            controller.startup_cleanup()
        self.assertEqual(len(cleanup.calls), 1)
        self.assertIn("missing high-water state", cleanup.calls[0][1])
        self.assertTrue(controller.status()["blocked"])

    def test_unreadable_state_still_cleans_worker_then_blocks_startup(self):
        directory_state = Path(self.temp.name) / "state-is-directory"
        directory_state.mkdir()
        cleanup = Cleanup()
        controller = FenceController(
            "gpu-a",
            self.validator,
            state_path=directory_state,
            cleanup=cleanup,
            monotonic=self.clock,
            shutdown_margin_sec=30,
        )

        with self.assertRaisesRegex(FenceError, "operator repair"):
            controller.startup_cleanup()
        self.assertEqual(len(cleanup.calls), 1)

    def test_invalid_poll_cannot_race_with_stale_readmission(self):
        started = threading.Event()
        release = threading.Event()
        call_count = 0

        class RacingValidator:
            def validate(_self, _gpu_id, _token, _generation):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return LeaseValidation(True, 90)
                if call_count == 2:
                    started.set()
                    release.wait(timeout=5)
                    return LeaseValidation(True, 90)
                return LeaseValidation(False, 0)

        controller = FenceController(
            "gpu-a",
            RacingValidator(),
            state_path=self.state_path,
            cleanup=self.cleanup,
            monotonic=self.clock,
            shutdown_margin_sec=30,
        )
        controller.claim(self.token1, 1)
        errors = []

        def reclaim():
            try:
                controller.claim(self.token1, 1)
            except Exception as exc:  # noqa: BLE001 - asserted below
                errors.append(exc)

        poll_result = []
        claim_thread = threading.Thread(target=reclaim)
        poll_thread = threading.Thread(
            target=lambda: poll_result.append(controller.poll_once())
        )
        claim_thread.start()
        self.assertTrue(started.wait(timeout=2))
        poll_thread.start()
        time.sleep(0.05)
        self.assertTrue(poll_thread.is_alive())
        release.set()
        claim_thread.join(timeout=2)
        poll_thread.join(timeout=2)

        self.assertFalse(errors)
        self.assertEqual(poll_result, [False])
        self.assertFalse(controller.status()["active"])

    def test_independent_deadline_fences_during_blocked_database_read(self):
        started = threading.Event()
        release = threading.Event()

        class BlockingValidator:
            calls = 0

            def validate(_self, _gpu_id, _token, _generation):
                _self.calls += 1
                if _self.calls == 1:
                    return LeaseValidation(True, 60)
                started.set()
                release.wait(timeout=5)
                raise FenceError("stalled read")

        controller = FenceController(
            "gpu-a",
            BlockingValidator(),
            state_path=self.state_path,
            cleanup=self.cleanup,
            monotonic=self.clock,
            shutdown_margin_sec=30,
        )
        controller.claim(self.token1, 1)
        poll_thread = threading.Thread(target=controller.poll_once)
        poll_thread.start()
        self.assertTrue(started.wait(timeout=2))

        self.clock.now = 31
        self.assertFalse(controller.enforce_deadline())
        self.assertFalse(controller.status()["active"])
        release.set()
        poll_thread.join(timeout=2)

    def test_failed_cleanup_blocks_admission_until_verified_retry(self):
        class FailingCleanup:
            fail = True

            def __call__(_self, _groups, _reason):
                if _self.fail:
                    raise FenceError("docker unavailable")

        cleanup = FailingCleanup()
        controller = FenceController(
            "gpu-a",
            self.validator,
            state_path=self.state_path,
            cleanup=cleanup,
            monotonic=self.clock,
            shutdown_margin_sec=30,
        )
        self.allow(self.token1, 1)
        self.allow(self.token2, 2)
        controller.claim(self.token1, 1)

        with self.assertRaisesRegex(FenceError, "worker remains blocked"):
            controller.claim(self.token2, 2)
        self.assertFalse(controller.status()["active"])
        self.assertTrue(controller.status()["blocked"])

        cleanup.fail = False
        current = controller.claim(self.token2, 2)
        self.assertEqual(current.generation, 2)
        self.assertFalse(controller.status()["blocked"])

    def test_state_write_failure_never_skips_deadline_cleanup(self):
        self.allow(self.token1, 1, remaining=60)
        self.controller.claim(self.token1, 1)
        self.clock.now = 31

        with (
            mock.patch.object(
                self.controller,
                "_save_state_locked",
                side_effect=FenceError("disk full"),
            ),
            self.assertLogs("vss-gpu-fence", level="ERROR"),
        ):
            self.assertFalse(self.controller.enforce_deadline())

        self.assertFalse(self.controller.status()["active"])
        self.assertTrue(self.controller.status()["blocked"])
        self.assertIn(
            "local worker safety deadline",
            self.cleanup.calls[-1][1],
        )


class WorkerCleanupProcessTests(unittest.TestCase):
    def test_cleanup_kills_registered_process_group(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        calls = []

        def fake_run(command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="")

        cleanup = WorkerCleanup(
            termination_grace_sec=0,
            run=fake_run,
        )
        try:
            cleanup([process.pid], "test takeover")
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

        self.assertNotEqual(process.returncode, 0)
        self.assertIn(["docker", "ps", "-aq"], calls)

    def test_startup_discovers_marked_process_without_persisted_group(self):
        environment = dict(os.environ)
        environment["VSS_GPU_FENCE_SESSION_ID"] = "orphaned-session"
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
            env=environment,
        )

        def fake_run(command, **_kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="")

        cleanup = WorkerCleanup(termination_grace_sec=0, run=fake_run)
        try:
            cleanup([], "daemon restart")
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        self.assertNotEqual(process.returncode, 0)

    def test_failed_container_removal_fails_closed(self):
        calls = 0

        def fake_run(command, **_kwargs):
            nonlocal calls
            calls += 1
            if command[:3] == ["docker", "ps", "-aq"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="container-a\n" if calls < 4 else "",
                )
            if command[:3] == ["docker", "rm", "-f"]:
                return subprocess.CompletedProcess(command, 1, stdout="")
            return subprocess.CompletedProcess(command, 1, stdout="")

        cleanup = WorkerCleanup(termination_grace_sec=0, run=fake_run)
        with self.assertRaisesRegex(FenceError, "container cleanup failed"):
            cleanup([], "test failure")


if __name__ == "__main__":
    unittest.main(verbosity=2)
