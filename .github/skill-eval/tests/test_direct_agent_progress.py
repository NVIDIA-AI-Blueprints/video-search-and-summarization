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

_BUILD_ADAPTER_SPEC = importlib.util.spec_from_file_location(
    "build_vision_adapter_progress_tests",
    _ROOT / "adapters/vss-build-vision-agent/generate.py",
)
build_vision_adapter = importlib.util.module_from_spec(_BUILD_ADAPTER_SPEC)
assert _BUILD_ADAPTER_SPEC.loader is not None
_BUILD_ADAPTER_SPEC.loader.exec_module(build_vision_adapter)

_RESOLVED_VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "resolved_validator_progress_tests",
    Path(__file__).resolve().parents[3]
    / "skills/vss-build-vision-agent/scripts/validate_resolved_yml.py",
)
resolved_validator = importlib.util.module_from_spec(_RESOLVED_VALIDATOR_SPEC)
assert _RESOLVED_VALIDATOR_SPEC.loader is not None
_RESOLVED_VALIDATOR_SPEC.loader.exec_module(resolved_validator)


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
        validator = mock.patch.object(
            progress, "validate_resolved_compose"
        )
        with (
            validator as validate,
            mock.patch.object(
                progress,
                "validate_compose_services",
                return_value=(("worker",), "a" * 64),
            ),
        ):
            decision = await monitor.pre_tool(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": "docker compose -f resolved.yml up -d"},
                },
                "tool-1",
                None,
            )
            validate.assert_called_once_with(compose, tmp_path, ())
            return decision

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


@pytest.mark.parametrize(
    "placeholder",
    [
        "<HOST_IP>",
        "<host_ip>",
        r"\<HOST_IP\>",
        "&lt;HOST_IP&gt;",
        "%3CHOST_IP%3E",
        "%253CHOST_IP%253E",
    ],
)
def test_resolved_validator_rejects_host_ip_escaped_variants(
    tmp_path: Path,
    placeholder: str,
) -> None:
    invalid = {"services": {"api": {"environment": {"HOST": placeholder}}}}
    assert any(
        "<HOST_IP>" in error
        for error in resolved_validator.validate_document(invalid, tmp_path)
    )


def test_resolved_validator_allows_compose_escaped_interpolation(
    tmp_path: Path,
) -> None:
    escaped = {"services": {"api": {"environment": {"HOST": "$${HOST_IP}"}}}}
    assert resolved_validator.validate_document(escaped, tmp_path) == []


def test_resolved_validator_requires_exact_nonbuildable_local_image(
    tmp_path: Path,
) -> None:
    required = {"ds-sop:1.0.0"}
    valid = {
        "services": {
            "ds-sop": {"image": "ds-sop:1.0.0"},
            "optional": {"image": "optional:latest", "build": "."},
        }
    }
    wrong_tag = {
        "services": {"ds-sop": {"image": "ds-sop:wrong"}}
    }
    rebuildable = {
        "services": {
            "ds-sop": {
                "image": "ds-sop:1.0.0",
                "build": {"context": "."},
            }
        }
    }
    assert resolved_validator.validate_document(
        valid,
        tmp_path,
        required_local_images=required,
    ) == []
    assert resolved_validator.validate_document(
        wrong_tag,
        tmp_path,
        required_local_images=required,
    ) == ["a required local image is absent from services"]
    assert resolved_validator.validate_document(
        rebuildable,
        tmp_path,
        required_local_images=required,
    ) == ["a required local image service is buildable"]


def test_required_local_image_presence_and_absence() -> None:
    present = progress.BoundedCommandResult(0, f"sha256:{'a' * 64}\n", False)
    absent = progress.BoundedCommandResult(1, "", False)
    with mock.patch.object(
        progress,
        "_run_bounded",
        side_effect=[present, absent],
    ) as run:
        missing = progress.missing_required_local_images(
            ("ds-sop:1.0.0", "missing:1")
        )
    assert missing == ("missing:1",)
    assert run.call_args_list[0].args[0] == [
        "docker",
        "image",
        "inspect",
        "ds-sop:1.0.0",
        "--format",
        "{{.Id}}",
    ]


@pytest.mark.parametrize(
    "result",
    [
        progress.BoundedCommandResult(1, "", False),
        progress.BoundedCommandResult(0, "sha256:not-the-right-id\n", False),
        progress.BoundedCommandResult(0, f"sha256:{'a' * 64}\n", True),
    ],
)
def test_wrong_or_missing_exact_local_tag_is_rejected(result) -> None:
    with mock.patch.object(progress, "_run_bounded", return_value=result) as run:
        assert progress.missing_required_local_images(
            ("ds-sop:1.0.0",)
        ) == ("ds-sop:1.0.0",)
    assert "ds-sop:1.0.0" in run.call_args.args[0]


def test_declared_prebuilt_image_rebuild_is_blocked_without_argument_leak(
    tmp_path: Path,
) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps({"required_local_images": ["ds-sop:1.0.0"]})
    )
    with mock.patch.object(
        progress,
        "required_local_image_ids",
        return_value={"ds-sop:1.0.0": "a" * 64},
    ):
        monitor = progress.DirectAgentProgress(
            results_root=tmp_path / "results",
            spec_path=spec,
            repo_root=tmp_path,
        )
    secret = "never-persist-build-context"

    async def invoke():
        return await monitor.pre_tool(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "docker build --tag=ds-sop:1.0.0 "
                        f"https://example.invalid/{secret}"
                    )
                },
            },
            "tool-secret",
            None,
        )

    decision = asyncio.run(invoke())
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "prebuilt image rebuild" in decision[
        "hookSpecificOutput"
    ]["permissionDecisionReason"]
    assert secret not in monitor.journal.path.read_text()


def test_missing_prebuilt_image_blocks_compose_up(tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "expected_services": ["ds-sop"],
                "required_local_images": ["ds-sop:1.0.0"],
            }
        )
    )
    compose = tmp_path / "resolved.yml"
    compose.write_text("services:\n  ds-sop: {}\n")
    with mock.patch.object(
        progress,
        "required_local_image_ids",
        return_value={"ds-sop:1.0.0": "a" * 64},
    ):
        monitor = progress.DirectAgentProgress(
            results_root=tmp_path / "results",
            spec_path=spec,
            repo_root=tmp_path,
        )

    async def invoke():
        with (
            mock.patch.object(progress, "validate_resolved_compose"),
            mock.patch.object(
                progress,
                "required_local_image_ids",
                return_value={},
            ),
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
    assert (
        decision["hookSpecificOutput"]["permissionDecisionReason"]
        == "deployment blocked: required local image missing or changed"
    )
    assert "ds-sop" not in json.dumps(decision)


def test_changed_prebuilt_image_id_blocks_compose_up(tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps({"required_local_images": ["ds-sop:1.0.0"]})
    )
    compose = tmp_path / "resolved.yml"
    compose.write_text(
        "services:\n  ds-sop:\n    image: ds-sop:1.0.0\n"
    )
    with mock.patch.object(
        progress,
        "required_local_image_ids",
        return_value={"ds-sop:1.0.0": "a" * 64},
    ):
        monitor = progress.DirectAgentProgress(
            results_root=tmp_path / "results",
            spec_path=spec,
            repo_root=tmp_path,
        )

    async def invoke():
        with (
            mock.patch.object(progress, "validate_resolved_compose"),
            mock.patch.object(
                progress,
                "required_local_image_ids",
                return_value={"ds-sop:1.0.0": "b" * 64},
            ),
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
    assert (
        decision["hookSpecificOutput"]["permissionDecisionReason"]
        == "deployment blocked: required local image missing or changed"
    )


def test_oversize_resolved_compose_is_rejected_before_validator(
    tmp_path: Path,
) -> None:
    compose = tmp_path / "resolved.yml"
    compose.write_bytes(b"x" * (progress.MAX_COMPOSE_BYTES + 1))
    with (
        mock.patch.object(progress, "_run_bounded") as run,
        pytest.raises(ValueError, match="size limit"),
    ):
        progress.validate_resolved_compose(compose, tmp_path)
    run.assert_not_called()


@pytest.mark.parametrize(
    "command",
    [
        "docker compose build",
        "docker compose -f resolved.yml build ds-sop",
        "docker compose up -d --build",
        "docker compose up -d --build=true",
        "docker-compose -f resolved.yml build ds-sop",
        "docker --context default compose build ds-sop",
        "sudo -u root docker compose build ds-sop",
        "env -u UNUSED docker compose build ds-sop",
        "time -f '%e' docker compose build ds-sop",
        "nice -n 5 docker compose build ds-sop",
        "timeout -k 5 60 docker compose build ds-sop",
        "docker compose build --with-dependencies unrelated",
        "docker buildx build -tds-sop:1.0.0 .",
        "docker image build --tag ds-sop:1.0.0 .",
        "docker build --tag \"$IMAGE_TAG\" .",
        "docker tag unrelated:1 ds-sop:1.0.0",
        "docker image pull ds-sop:1.0.0",
        "docker load --input unknown-tags.tar",
        "command docker tag unrelated:1 ds-sop:1.0.0",
        "printf '%s' unrelated:1 | xargs docker tag ds-sop:1.0.0",
        "bash -c 'docker build -t ds-sop:1.0.0 .'",
        "bash -c '$DOCKER build -t ds-sop:1.0.0 .'",
        "./build-image.sh ds-sop:1.0.0",
        "make docker-build IMAGE=$IMAGE",
    ],
)
def test_compose_rebuild_paths_for_prebuilt_image_are_blocked(
    command: str,
) -> None:
    assert progress._rebuilds_required_image(
        command,
        ("ds-sop:1.0.0",),
    )


@pytest.mark.parametrize(
    "command",
    [
        "pytest -t ds-sop:1.0.0",
        "docker build --tag unrelated:1 .",
        "docker compose -p build -f resolved.yml build elasticsearch",
        "docker compose build --builder ds-sop unrelated",
        "docker-compose build elasticsearch",
        "docker compose up -d --build=false",
        "bash -c 'docker build -t unrelated:1 .'",
        "./build-image.sh unrelated:1",
    ],
)
def test_unrelated_builds_are_not_false_blocked(command: str) -> None:
    assert not progress._rebuilds_required_image(
        command,
        ("ds-sop:1.0.0",),
    )


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


def test_repeated_compose_up_and_identical_snapshots_do_not_refresh_idle(
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
    rows = [{
        "service": "rtvi-embed",
        "state": "running",
        "health": "starting",
        "exit_code": 0,
        "restart_count": 0,
    }]

    async def invoke_up(tool_id: str) -> None:
        await monitor.pre_tool(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "docker compose up -d"},
            },
            tool_id,
            None,
        )

    with (
        mock.patch.object(monitor, "_sample_images"),
        mock.patch.object(monitor, "_safe_service_rows", return_value=rows),
    ):
        asyncio.run(invoke_up("one"))
        monitor.sample()
        clock.advance(1200)
        asyncio.run(invoke_up("two"))
        monitor.sample()
        clock.advance(300)
    assert tracker.expiration() == (
        "idle",
        "container_transition",
        1500,
    )


def test_rt_embed_long_health_transition_is_meaningful_progress(
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
    )
    starting = [{
        "service": "rtvi-embed",
        "state": "running",
        "health": "starting",
        "exit_code": 0,
        "restart_count": 0,
    }]
    healthy = [{**starting[0], "health": "healthy"}]
    with mock.patch.object(monitor, "_sample_images"):
        with mock.patch.object(
            monitor, "_safe_service_rows", return_value=starting
        ):
            monitor.sample()
        clock.advance(1079)
        with mock.patch.object(
            monitor, "_safe_service_rows", return_value=healthy
        ):
            monitor.sample()
    clock.advance(1079)
    assert tracker.expiration() is None


def test_restart_count_churn_does_not_refresh_stuck_search(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    tracker = progress.ProgressTracker(
        hard_ceiling_sec=7200,
        cold_start_grace_sec=1,
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

    def row(restart_count: int) -> list[dict]:
        return [{
            "service": "rtvi-embed",
            "state": "restarting",
            "health": "unhealthy",
            "exit_code": 1,
            "restart_count": restart_count,
        }]

    with mock.patch.object(monitor, "_sample_images"):
        for restart_count in range(4):
            with mock.patch.object(
                monitor,
                "_safe_service_rows",
                return_value=row(restart_count),
            ):
                monitor.sample()
            clock.advance(360)
    assert tracker.expiration() == (
        "idle",
        "container_transition",
        1440,
    )


def test_image_removal_and_reappearance_do_not_churn_progress(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    tracker = progress.ProgressTracker(
        hard_ceiling_sec=7200,
        cold_start_grace_sec=1,
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
    image = "a" * 64
    results = [
        progress.BoundedCommandResult(0, f"sha256:{image}\n", False),
        progress.BoundedCommandResult(0, "", False),
        progress.BoundedCommandResult(0, f"sha256:{image}\n", False),
    ]
    with mock.patch.object(progress, "_run_bounded", side_effect=results):
        monitor._sample_images()
        clock.advance(600)
        monitor._sample_images()
        clock.advance(600)
        monitor._sample_images()
    assert tracker.expiration() == ("idle", "startup", 1200)


def test_image_progress_identity_set_is_bounded(tmp_path: Path) -> None:
    monitor = progress.DirectAgentProgress(
        results_root=tmp_path / "results",
        spec_path=tmp_path / "absent.json",
        repo_root=tmp_path,
    )
    output = "\n".join(
        f"sha256:{index:064x}"
        for index in range(progress.MAX_SEEN_IMAGE_IDS + 2)
    )
    result = progress.BoundedCommandResult(0, output, False)
    with mock.patch.object(progress, "_run_bounded", return_value=result):
        monitor._sample_images()
    assert len(monitor._seen_image_ids) == progress.MAX_SEEN_IMAGE_IDS
    assert monitor._image_tracking_saturated


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
        mock.patch.object(progress, "validate_resolved_compose"),
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
            "health": "unhealthy",
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
    assert all(
        row["service_id"].startswith("svc-")
        for row in artifact["services"]
    )
    assert '"svc-0"' not in json.dumps(artifact)
    assert set(artifact) == {
        "schema",
        "reason",
        "last_progress_category",
        "last_progress_elapsed_sec",
        "compose_sha256",
        "services",
        "phase_summary",
    }


def test_service_pseudonyms_are_run_scoped_stable_and_non_leaking(
    tmp_path: Path,
) -> None:
    row = {
        "service": "secret-bearing-service-name",
        "state": "running",
        "health": "unhealthy",
        "exit_code": 0,
        "restart_count": 0,
    }
    with mock.patch.object(
        progress.os,
        "urandom",
        side_effect=[b"a" * 32, b"b" * 32],
    ):
        first = progress.DirectAgentProgress(
            results_root=tmp_path / "first",
            spec_path=tmp_path / "absent.json",
            repo_root=tmp_path,
        )
        second = progress.DirectAgentProgress(
            results_root=tmp_path / "second",
            spec_path=tmp_path / "absent.json",
            repo_root=tmp_path,
        )
    with (
        mock.patch.object(first, "_safe_service_rows", return_value=[row]),
        mock.patch.object(second, "_safe_service_rows", return_value=[row]),
    ):
        first.archive_timeout(("idle", "container_transition", 1080))
        first_id = json.loads(
            (first.results_root / "timeout-diagnostics.json").read_text()
        )["services"][0]["service_id"]
        first.archive_timeout(("idle", "container_transition", 1080))
        repeated_id = json.loads(
            (first.results_root / "timeout-diagnostics.json").read_text()
        )["services"][0]["service_id"]
        second.archive_timeout(("idle", "container_transition", 1080))
        second_payload = (
            second.results_root / "timeout-diagnostics.json"
        ).read_text()
        second_id = json.loads(second_payload)["services"][0]["service_id"]
    assert first_id == repeated_id
    assert first_id != second_id
    assert len(first_id) == len("svc-") + 12
    assert row["service"] not in second_payload


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


def test_search_expected_services_block_transitive_agent_ui_gap(
    tmp_path: Path,
) -> None:
    compose = tmp_path / "resolved.yml"
    compose.write_text("services: {}\n")
    expected = (
        "vss-agent",
        "vss-ui",
        "vss-video-analytics-api-fusion",
        "rtvi-vlm",
        "rtvi-embed",
        "redis",
        "phoenix",
    )
    completed = progress.BoundedCommandResult(
        0,
        "vss-video-analytics-api-fusion\nrtvi-vlm\nrtvi-embed\n"
        "redis\nphoenix\noptional\n",
        False,
    )
    with mock.patch.object(progress, "_run_bounded", return_value=completed):
        missing, _ = progress.validate_compose_services(compose, expected)
    assert missing == ("vss-agent", "vss-ui")


def test_search_adapter_carries_subset_expected_services(
    tmp_path: Path,
) -> None:
    skill_dir = Path(__file__).resolve().parents[3] / "skills/vss-deploy-profile"
    deploy_profile_adapter.generate_task(
        "search",
        "RTXPRO6000BW",
        deploy_profile_adapter.PROFILES["search"],
        tmp_path,
        skill_dir,
        2,
    )
    task = (tmp_path / "search/rtxpro6000bw/task.toml").read_text()
    assert (
        'expected_services = ["vss-agent", "vss-ui", '
        '"vss-video-analytics-api-fusion", "rtvi-vlm", "rtvi-embed", '
        '"redis", "phoenix"]'
        in task
    )


def test_sop_adapter_carries_services_and_prebuilt_image_metadata(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    spec_path = (
        repo_root
        / "skills/vss-build-vision-agent/eval/"
        "profile_sop_1_compliance_monitoring.json"
    )
    spec = json.loads(spec_path.read_text())
    spec["_source_path"] = str(spec_path)
    build_vision_adapter.generate_task(
        "RTXPRO6000BW",
        spec,
        tmp_path,
        repo_root / "skills/vss-build-vision-agent",
        None,
        None,
        None,
        None,
        None,
        repo_root / "skills/vss-generate-video-report",
    )
    task = (
        tmp_path
        / "profile_sop_1_compliance_monitoring/rtxpro6000bw/task.toml"
    ).read_text()
    assert '"ds-sop"' in task
    assert 'required_local_images = ["ds-sop:1.0.0"]' in task


def test_sop_local_image_digest_limitation_is_documented() -> None:
    reference = (
        Path(__file__).resolve().parents[3]
        / "skills/vss-build-vision-agent/references/services/sop/"
        "build-ds-sop.md"
    ).read_text()
    assert "no registry or source digest pinned" in reference
    assert "does **not** prove source provenance" in reference


def test_complete_healthy_stack_has_no_nonhealthy_diagnostics(
    tmp_path: Path,
) -> None:
    monitor = progress.DirectAgentProgress(
        results_root=tmp_path / "results",
        spec_path=tmp_path / "absent.json",
        repo_root=tmp_path,
    )
    rows = [
        {
            "service": service,
            "state": "running",
            "health": "healthy",
            "exit_code": 0,
            "restart_count": 0,
        }
        for service in (
            "vss-agent",
            "vss-ui",
            "vss-video-analytics-api-fusion",
            "rtvi-vlm",
            "rtvi-embed",
        )
    ]
    with mock.patch.object(monitor, "_safe_service_rows", return_value=rows):
        monitor.archive_timeout(("idle", "container_transition", 1080))
    artifact = json.loads(
        (monitor.results_root / "timeout-diagnostics.json").read_text()
    )
    assert artifact["services"] == []
