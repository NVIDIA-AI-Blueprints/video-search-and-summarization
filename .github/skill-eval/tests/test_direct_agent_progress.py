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
from types import SimpleNamespace
from unittest import mock

import pytest


_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "direct_agent_progress", _ROOT / "direct_agent_progress.py"
)
progress = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(progress)

_AGENT_SPEC = importlib.util.spec_from_file_location(
    "skills_eval_agent_progress_tests", _ROOT / "skills_eval_agent.py"
)
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
        wall_clock=lambda: datetime.datetime(
            2026, 8, 24, tzinfo=datetime.timezone.utc
        ),
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
    assert tracker.expiration() == (
        "hard-ceiling", "container_transition", 1
    )


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
                    "tool_input": {
                        "command": "docker compose -f resolved.yml up -d"
                    },
                },
                "tool-1",
                None,
            )

    decision = asyncio.run(invoke())
    reason = decision["hookSpecificOutput"]["permissionDecisionReason"]
    assert reason == "deployment blocked: missing expected services: worker"
    assert "api" not in reason


def test_compose_validation_records_names_and_hash_only(tmp_path: Path) -> None:
    compose = tmp_path / "resolved.yml"
    compose.write_text("services:\n  api: {}\n  worker: {}\n")
    completed = SimpleNamespace(returncode=0, stdout="api\nworker\n", stderr="")
    with mock.patch.object(progress.subprocess, "run", return_value=completed):
        missing, digest = progress.validate_compose_services(
            compose, ("api", "worker")
        )
    assert missing == ()
    assert len(digest) == 64
    assert all(char in "0123456789abcdef" for char in digest)


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
        "tool_input": {
            "command": (
                "docker compose -f resolved.yml up -d "
                "--label token=never-persist-this"
            )
        },
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
    assert '"tool_category":"shell"' in body
    assert '"phase":"up"' in body


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
        json.dumps({
            "category": "file_mutation",
            "mutation_kind": "write",
            "target_kind": "compose",
            "raw_command": "secret",
        }) + "\n"
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
    artifact = json.loads(
        (monitor.results_root / "timeout-diagnostics.json").read_text()
    )
    assert len(artifact["services"]) == progress.MAX_DIAGNOSTIC_SERVICES
    assert set(artifact) == {
        "schema", "reason", "last_progress_category",
        "last_progress_elapsed_sec", "compose_sha256",
        "services", "phase_summary",
    }


def test_watchdog_cancels_and_reaps_agent_coroutine() -> None:
    cancelled = asyncio.Event()

    async def hanging_agent() -> int:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    class ImmediateExpiry:
        async def wait_for_expiry(self):
            return "idle", "file_mutation", 1080

    async def exercise() -> None:
        with pytest.raises(progress.DirectAgentWatchdogExpired):
            await progress.run_with_progress_watchdog(
                hanging_agent(), ImmediateExpiry()
            )
        assert cancelled.is_set()
        pending = [
            task for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ]
        assert pending == []

    asyncio.run(exercise())


def test_old_brev_path_does_not_enable_direct_monitor() -> None:
    with mock.patch.dict(os.environ, {}, clear=True):
        assert agent._direct_progress_monitor() is None


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
    task = (
        tmp_path / "alerts_cv/rtxpro6000bw/task.toml"
    ).read_text()
    assert (
        'expected_services = ["vss-agent", "redis", "perception-alerts", '
        '"vss-behavior-analytics-alerts", "alert-bridge", "nvstreamer-alerts"]'
        in task
    )
