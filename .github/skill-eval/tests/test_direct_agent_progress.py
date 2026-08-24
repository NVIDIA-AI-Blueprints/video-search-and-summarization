# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deterministic tests for direct OpenShell progress monitoring."""

from __future__ import annotations

import asyncio
import datetime
import importlib.util
import json
import os
from pathlib import Path
from unittest import mock

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("direct_agent_progress", _ROOT / "direct_agent_progress.py")
progress = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(progress)

_AGENT_SPEC = importlib.util.spec_from_file_location("skills_eval_agent_progress_tests", _ROOT / "skills_eval_agent.py")
agent = importlib.util.module_from_spec(_AGENT_SPEC)
assert _AGENT_SPEC.loader is not None
_AGENT_SPEC.loader.exec_module(agent)

_ADAPTER_SPEC = importlib.util.spec_from_file_location(
    "deploy_profile_adapter_progress_tests",
    _ROOT / "adapters/vss-deploy-profile/generate.py",
)
deploy_profile_adapter = importlib.util.module_from_spec(_ADAPTER_SPEC)
assert _ADAPTER_SPEC.loader is not None
_ADAPTER_SPEC.loader.exec_module(deploy_profile_adapter)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _journal(tmp_path: Path, clock: FakeClock, max_events: int = 512):
    return progress.ProgressJournal(
        tmp_path / "journal.jsonl",
        monotonic=clock,
        wall_clock=lambda: datetime.datetime(2026, 8, 24, tzinfo=datetime.UTC),
        max_events=max_events,
    )


def test_progress_reset_and_true_idle_without_sleep() -> None:
    clock = FakeClock()
    tracker = progress.ProgressTracker(
        hard_ceiling_sec=7200,
        cold_start_grace_sec=1200,
        idle_timeout_sec=900,
        monotonic=clock,
    )
    clock.advance(1000)
    tracker.progress("file_mutation")
    clock.advance(899)
    assert tracker.expiration() is None
    clock.advance(1)
    assert tracker.expiration() == ("idle", "file_mutation", 900)


def test_cold_grace_prevents_early_idle_without_sleep() -> None:
    clock = FakeClock()
    tracker = progress.ProgressTracker(
        hard_ceiling_sec=7200,
        cold_start_grace_sec=1500,
        idle_timeout_sec=900,
        monotonic=clock,
    )
    clock.advance(1499)
    assert tracker.expiration() is None
    clock.advance(1)
    assert tracker.expiration() == ("idle", "startup", 1500)


def test_hard_ceiling_wins_even_after_recent_progress() -> None:
    clock = FakeClock()
    tracker = progress.ProgressTracker(
        hard_ceiling_sec=7200,
        cold_start_grace_sec=1500,
        idle_timeout_sec=1080,
        monotonic=clock,
    )
    clock.advance(7199)
    tracker.progress("container_transition")
    clock.advance(1)
    assert tracker.expiration() == ("hard-ceiling", "container_transition", 1)


def test_pull_build_heartbeat_resets_idle_without_sleep(tmp_path: Path) -> None:
    clock = FakeClock()
    tracker = progress.ProgressTracker(
        hard_ceiling_sec=7200,
        cold_start_grace_sec=1200,
        idle_timeout_sec=900,
        monotonic=clock,
    )
    monitor = progress.DirectAgentProgress(
        results_root=tmp_path / "results",
        spec_path=tmp_path / "absent.json",
        repo_root=tmp_path,
        tracker=tracker,
        journal=_journal(tmp_path, clock),
        monotonic=clock,
        activity_heartbeat_sec=300,
    )
    monitor.active_phase = "pull"
    clock.advance(1500)
    with (
        mock.patch.object(monitor, "_sample_images"),
        mock.patch.object(monitor, "_safe_service_rows", return_value=[]),
    ):
        monitor.sample()
    assert tracker.last_progress_category == "image_activity_heartbeat"
    assert tracker.expiration() is None


def test_missing_expected_services_fail_before_compose_up(
    tmp_path: Path,
) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"expected_services": ["api", "worker"]}))
    compose = tmp_path / "resolved.yml"
    compose.write_text("services: {}\n")
    monitor = progress.DirectAgentProgress(
        results_root=tmp_path / "results",
        spec_path=spec,
        repo_root=tmp_path,
    )

    async def invoke():
        with mock.patch.object(
            progress,
            "validate_compose_services",
            return_value=(("worker",), "a" * 64),
        ):
            return await monitor.pre_tool(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": "docker compose -f resolved.yml up -d"},
                },
                "tool-1",
                None,
            )

    decision = asyncio.run(invoke())
    reason = decision["hookSpecificOutput"]["permissionDecisionReason"]
    assert reason == "deployment blocked: 1 expected service(s) missing"
    assert "api" not in reason
    assert "worker" not in reason


def test_compose_validation_accepts_optional_services(tmp_path: Path) -> None:
    compose = tmp_path / "resolved.yml"
    compose.write_text("services:\n  api: {}\n  worker: {}\n")
    completed = progress.BoundedCommandResult(0, "api\nworker\noptional\n", False)
    with mock.patch.object(progress, "_run_bounded", return_value=completed):
        missing, digest = progress.validate_compose_services(compose, ("api", "worker"))
    assert missing == ()
    assert len(digest) == 64
    assert all(char in "0123456789abcdef" for char in digest)


def test_repeated_non_progress_events_do_not_extend_idle(tmp_path: Path) -> None:
    clock = FakeClock()
    tracker = progress.ProgressTracker(
        hard_ceiling_sec=7200,
        cold_start_grace_sec=1500,
        idle_timeout_sec=1080,
        monotonic=clock,
    )
    monitor = progress.DirectAgentProgress(
        results_root=tmp_path / "results",
        spec_path=tmp_path / "absent.json",
        repo_root=tmp_path,
        tracker=tracker,
        journal=_journal(tmp_path, clock),
        monotonic=clock,
    )

    async def repeated_writes() -> None:
        event = {
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/secret-bearing-name.env"},
        }
        await monitor.pre_tool(event, "one", None)
        clock.advance(1079)
        await monitor.pre_tool(event, "two", None)

    asyncio.run(repeated_writes())
    clock.advance(421)
    assert tracker.expiration() == ("idle", "file_mutation", 1500)
    assert "secret-bearing-name" not in monitor.journal.path.read_text()


def test_phase_heartbeats_are_rate_bounded(tmp_path: Path) -> None:
    clock = FakeClock()
    tracker = progress.ProgressTracker(
        hard_ceiling_sec=20000,
        cold_start_grace_sec=1500,
        idle_timeout_sec=1080,
        monotonic=clock,
    )
    monitor = progress.DirectAgentProgress(
        results_root=tmp_path / "results",
        spec_path=tmp_path / "absent.json",
        repo_root=tmp_path,
        tracker=tracker,
        journal=_journal(tmp_path, clock),
        monotonic=clock,
        activity_heartbeat_sec=300,
    )
    monitor.active_phase = "pull"
    with (
        mock.patch.object(monitor, "_sample_images"),
        mock.patch.object(monitor, "_safe_service_rows", return_value=[]),
    ):
        for _ in range(progress.MAX_PHASE_HEARTBEATS + 4):
            clock.advance(300)
            monitor.sample()
    assert monitor._phase_heartbeat_count == progress.MAX_PHASE_HEARTBEATS
    clock.advance(1080)
    assert tracker.expiration() == ("idle", "image_activity_heartbeat", 2280)


def test_active_long_pull_reaches_hard_ceiling_without_early_idle(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    tracker = progress.ProgressTracker(
        hard_ceiling_sec=7200,
        cold_start_grace_sec=1500,
        idle_timeout_sec=1080,
        monotonic=clock,
    )
    monitor = progress.DirectAgentProgress(
        results_root=tmp_path / "results",
        spec_path=tmp_path / "absent.json",
        repo_root=tmp_path,
        tracker=tracker,
        journal=_journal(tmp_path, clock),
        monotonic=clock,
        activity_heartbeat_sec=300,
    )
    monitor.active_phase = "build"
    with (
        mock.patch.object(monitor, "_sample_images"),
        mock.patch.object(monitor, "_safe_service_rows", return_value=[]),
    ):
        for _ in range(progress.MAX_PHASE_HEARTBEATS):
            clock.advance(300)
            monitor.sample()
    clock.advance(899)
    assert tracker.expiration() is None
    clock.advance(1)
    assert tracker.expiration() == ("hard-ceiling", "image_activity_heartbeat", 900)


def test_oscillating_container_states_are_coalesced(tmp_path: Path) -> None:
    clock = FakeClock()
    tracker = progress.ProgressTracker(
        hard_ceiling_sec=7200,
        cold_start_grace_sec=1500,
        idle_timeout_sec=1080,
        monotonic=clock,
    )
    monitor = progress.DirectAgentProgress(
        results_root=tmp_path / "results",
        spec_path=tmp_path / "absent.json",
        repo_root=tmp_path,
        tracker=tracker,
        journal=_journal(tmp_path, clock),
        monotonic=clock,
    )
    running = [
        {
            "service": "credential_backup_service",
            "state": "running",
            "health": "healthy",
            "exit_code": 0,
            "restart_count": 0,
        }
    ]
    stopped = [
        {
            **running[0],
            "state": "exited",
            "health": "none",
        }
    ]
    with mock.patch.object(monitor, "_sample_images"):
        for rows in (running, stopped, running, stopped):
            with mock.patch.object(monitor, "_safe_service_rows", return_value=rows):
                monitor.sample()
                clock.advance(400)
    clock.advance(280)
    assert tracker.expiration() == ("idle", "container_transition", 1480)
    assert "credential" not in monitor.journal.path.read_text()


def test_bounded_command_discards_oversize_output() -> None:
    result = progress._run_bounded(
        [
            os.environ.get("PYTHON", os.sys.executable),
            "-c",
            "import sys; sys.stdout.write('x' * 10000)",
        ],
        timeout=5,
        output_limit=128,
    )
    assert result.returncode == 0
    assert result.truncated
    assert len(result.stdout.encode()) == 128


def test_oversize_spec_and_compose_are_rejected(tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_bytes(b"{" + b"x" * progress.MAX_SPEC_BYTES + b"}")
    assert progress.load_expected_services(spec) == ()
    compose = tmp_path / "resolved.yml"
    compose.write_bytes(b"x" * (progress.MAX_COMPOSE_BYTES + 1))
    with pytest.raises(ValueError, match="size limit"):
        progress.validate_compose_services(compose, ())


def test_expected_service_cardinality_is_bounded(tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps({"expected_services": [f"service-{index}" for index in range(progress.MAX_EXPECTED_SERVICES + 1)]})
    )
    with pytest.raises(ValueError, match="service limit"):
        progress.load_expected_services(spec)


def test_journal_allowlist_rejects_secret_bearing_fields_and_is_bounded(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    journal = _journal(tmp_path, clock, max_events=2)
    assert journal.record("tool", tool_category="shell")
    assert journal.record("file_mutation", mutation_kind="edit", target_kind="config")
    assert not journal.record("tool", tool_category="read")
    with pytest.raises(ValueError):
        journal.record(
            "tool",
            tool_category="shell",
            command="curl https://example.invalid -H 'token: secret'",
        )
    body = journal.path.read_text()
    assert "https://" not in body
    assert "secret" not in body
    assert len(body.splitlines()) == 2


def test_inner_hook_blocks_missing_service_without_persisting_arguments(
    tmp_path: Path,
) -> None:
    inner_journal = tmp_path / "inner.jsonl"
    inner_state = tmp_path / "state.json"
    inner_config = tmp_path / "config.json"
    compose = tmp_path / "resolved.yml"
    compose.write_text("services: {}\n")
    inner_state.write_text(json.dumps({"compose_file": str(compose)}))
    inner_config.write_text(json.dumps({"expected_services": ["api", "worker"]}))
    payload = {
        "tool_name": "Bash",
        "tool_use_id": "tool-secret",
        "tool_input": {"command": ("docker compose -f resolved.yml up -d --label token=never-persist-this")},
    }
    with (
        mock.patch.object(progress, "_INNER_JOURNAL", inner_journal),
        mock.patch.object(progress, "_INNER_STATE", inner_state),
        mock.patch.object(progress, "_INNER_CONFIG", inner_config),
        mock.patch.object(
            progress,
            "validate_compose_services",
            return_value=(("worker",), "a" * 64),
        ),
        mock.patch.object(Path, "home", return_value=tmp_path.parent),
    ):
        assert progress.run_inner_hook("pre", payload) == 2
    body = inner_journal.read_text()
    assert "never-persist-this" not in body
    assert "tool-secret" not in body
    assert "worker" not in body
    assert "api" not in body
    assert '"tool_category":"shell"' in body
    assert '"phase":"up"' in body


def test_malicious_synthetic_names_and_control_chars_never_persist(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    journal = _journal(tmp_path, clock)
    with pytest.raises(ValueError):
        journal.record(
            "container_transition",
            service_index=0,
            previous_state="running",
            state="running\nhttps://secret.invalid",
            health="healthy",
            exit_code=0,
            restart_count=0,
        )
    with pytest.raises(ValueError):
        journal.record(
            "tool",
            tool_category="shell\x1b[2Jcredential_name",
        )
    body = journal.path.read_text() if journal.path.exists() else ""
    assert "secret" not in body
    assert "credential" not in body
    assert "\x1b" not in body


def test_outer_monitor_consumes_only_allowlisted_inner_progress(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    tracker = progress.ProgressTracker(
        hard_ceiling_sec=7200,
        cold_start_grace_sec=1200,
        idle_timeout_sec=900,
        monotonic=clock,
    )
    monitor = progress.DirectAgentProgress(
        results_root=tmp_path / "results",
        spec_path=tmp_path / "absent.json",
        repo_root=tmp_path,
        tracker=tracker,
        journal=_journal(tmp_path, clock),
        monotonic=clock,
    )
    monitor.inner_journal_path = tmp_path / "inner.jsonl"
    monitor.inner_journal_path.write_text(
        json.dumps(
            {
                "category": "file_mutation",
                "mutation_kind": "write",
                "target_kind": "compose",
                "raw_command": "secret",
            }
        )
        + "\n"
    )
    clock.advance(1300)
    with (
        mock.patch.object(monitor, "_sample_images"),
        mock.patch.object(monitor, "_safe_service_rows", return_value=[]),
        mock.patch.object(progress, "_INNER_STATE", tmp_path / "no-state.json"),
    ):
        monitor.sample()
    assert tracker.last_progress_category == "file_mutation"
    assert "secret" not in monitor.journal.path.read_text()


def test_timeout_diagnostics_are_bounded_and_structural(tmp_path: Path) -> None:
    monitor = progress.DirectAgentProgress(
        results_root=tmp_path / "results",
        spec_path=tmp_path / "absent.json",
        repo_root=tmp_path,
    )
    rows = [
        {
            "service": f"svc-{index}",
            "state": "running",
            "health": "healthy",
            "exit_code": 0,
            "restart_count": 0,
        }
        for index in range(100)
    ]
    with mock.patch.object(monitor, "_safe_service_rows", return_value=rows):
        monitor.archive_timeout(("idle", "compose_phase", 1080))
    artifact = json.loads((monitor.results_root / "timeout-diagnostics.json").read_text())
    assert len(artifact["services"]) == progress.MAX_DIAGNOSTIC_SERVICES
    assert all("service" not in row for row in artifact["services"])
    assert all("service_index" in row for row in artifact["services"])
    assert "svc-" not in json.dumps(artifact)
    assert set(artifact) == {
        "schema",
        "reason",
        "last_progress_category",
        "last_progress_elapsed_sec",
        "compose_sha256",
        "services",
        "phase_summary",
    }


def test_timeout_cleanup_falls_back_to_bounded_container_removal(
    tmp_path: Path,
) -> None:
    monitor = progress.DirectAgentProgress(
        results_root=tmp_path / "results",
        spec_path=tmp_path / "absent.json",
        repo_root=tmp_path,
    )
    monitor.compose_file = tmp_path / "resolved.yml"
    monitor.compose_file.write_text("services: {}\n")
    container_id = "a" * 64
    results = [
        progress.BoundedCommandResult(1, "", False),
        progress.BoundedCommandResult(0, f"{container_id}\n", False),
        progress.BoundedCommandResult(0, container_id, False),
    ]
    with mock.patch.object(progress, "_run_bounded", side_effect=results) as run:
        assert monitor.cleanup_after_timeout()
    assert run.call_count == 3
    body = monitor.journal.path.read_text()
    assert container_id not in body
    assert '"category":"cleanup"' in body
    assert '"outcome":"success"' in body


def test_timeout_still_expires_when_diagnostics_sampling_fails(
    tmp_path: Path,
) -> None:
    monitor = progress.DirectAgentProgress(
        results_root=tmp_path / "results",
        spec_path=tmp_path / "absent.json",
        repo_root=tmp_path,
    )
    with mock.patch.object(
        monitor,
        "_safe_service_rows",
        side_effect=OSError("synthetic private path"),
    ):
        monitor.archive_timeout(("idle", "startup", 1500))
    artifact = json.loads((monitor.results_root / "timeout-diagnostics.json").read_text())
    assert artifact["services"] == []
    assert "private path" not in json.dumps(artifact)


def test_watchdog_cancels_and_reaps_agent_coroutine() -> None:
    cancelled = asyncio.Event()

    async def hanging_agent() -> int:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    class ImmediateExpiry:
        cleaned = False

        async def wait_for_expiry(self):
            return "idle", "file_mutation", 1080

        def cleanup_after_timeout(self):
            self.cleaned = True

    async def exercise() -> None:
        with pytest.raises(progress.DirectAgentWatchdogExpired):
            expiry = ImmediateExpiry()
            await progress.run_with_progress_watchdog(hanging_agent(), expiry)
        assert cancelled.is_set()
        assert expiry.cleaned
        pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()]
        assert pending == []

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["success", "exception", "cancel"])
def test_watchdog_reaps_monitor_on_every_outer_exit(mode: str) -> None:
    monitor_cancelled = asyncio.Event()

    class NeverExpires:
        cleaned = False

        async def wait_for_expiry(self):
            try:
                await asyncio.Event().wait()
            finally:
                monitor_cancelled.set()

        def cleanup_after_timeout(self):
            self.cleaned = True

    async def agent_body() -> int:
        if mode == "exception":
            raise RuntimeError("synthetic")
        if mode == "cancel":
            await asyncio.Event().wait()
        return 0

    async def exercise() -> None:
        expiry = NeverExpires()
        task = asyncio.create_task(progress.run_with_progress_watchdog(agent_body(), expiry))
        await asyncio.sleep(0)
        if mode == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif mode == "exception":
            with pytest.raises(RuntimeError, match="synthetic"):
                await task
        else:
            assert await task == 0
        assert expiry.cleaned is (mode != "success")
        assert monitor_cancelled.is_set()
        assert [
            pending for pending in asyncio.all_tasks() if pending is not asyncio.current_task() and not pending.done()
        ] == []

    asyncio.run(exercise())


def test_old_brev_path_does_not_enable_direct_monitor() -> None:
    with mock.patch.dict(os.environ, {}, clear=True):
        assert agent._direct_progress_monitor() is None


def test_shutdown_signals_install_interrupting_handlers() -> None:
    with mock.patch.object(agent.signal, "signal") as install:
        agent._install_shutdown_handlers()
    assert install.call_args_list == [
        mock.call(agent.signal.SIGTERM, agent._interrupt_for_shutdown),
        mock.call(agent.signal.SIGHUP, agent._interrupt_for_shutdown),
    ]
    with pytest.raises(KeyboardInterrupt, match="received signal"):
        agent._interrupt_for_shutdown(agent.signal.SIGTERM, None)


def test_adapter_carries_spec_expected_services_into_task_metadata(
    tmp_path: Path,
) -> None:
    skill_dir = Path(__file__).resolve().parents[3] / "skills/vss-deploy-profile"
    deploy_profile_adapter.generate_task(
        "alerts_cv",
        "RTXPRO6000BW",
        deploy_profile_adapter.PROFILES["alerts_cv"],
        tmp_path,
        skill_dir,
        2,
    )
    task = (tmp_path / "alerts_cv/rtxpro6000bw/task.toml").read_text()
    assert (
        'expected_services = ["vss-agent", "redis", "perception-alerts", '
        '"vss-behavior-analytics-alerts", "alert-bridge", "nvstreamer-alerts"]' in task
    )
