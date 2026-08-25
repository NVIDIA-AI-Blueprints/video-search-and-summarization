# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deterministic contracts recovered from PR 1751 failed-job evidence."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[3]
_PLAN_SPEC = importlib.util.spec_from_file_location(
    "pr1751_plan_matrix", ROOT / ".github/skill-eval/plan_matrix.py"
)
plan_matrix = importlib.util.module_from_spec(_PLAN_SPEC)
assert _PLAN_SPEC.loader is not None
_PLAN_SPEC.loader.exec_module(plan_matrix)
_PROGRESS_SPEC = importlib.util.spec_from_file_location(
    "pr1751_direct_progress",
    ROOT / ".github/skill-eval/direct_agent_progress.py",
)
direct_progress = importlib.util.module_from_spec(_PROGRESS_SPEC)
assert _PROGRESS_SPEC.loader is not None
_PROGRESS_SPEC.loader.exec_module(direct_progress)


def _json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text())


def _contract(relative: str) -> str:
    data = _json(relative)
    return "\n".join(
        item for step in data["expects"] for item in (step["query"], *step["checks"])
    )


def test_mv3dt_primary_procedure_distinguishes_expected_build_from_timeout() -> None:
    contract = _contract(
        "skills/vss-deploy-detection-tracking-3d/evals/sample-deployment.json"
    )
    exact = "First-run TensorRT engine build is expected to take 5–10 minutes."
    assert exact in contract
    assert "Before container launch" in contract
    assert "bounded readiness timeout ceiling" in contract


def test_profile_contracts_encode_the_observed_deterministic_fixes() -> None:
    combined = _contract(
        "skills/vss-build-vision-agent/eval/profile_combined_alerts_search_harbor.json"
    )
    assert "`RTSP -> VIOS`" in combined
    assert "`upload/on-demand -> VIOS`" in combined
    assert "`VIOS -> RT-CV + RT-Embed`" in combined

    in2 = _contract(
        "skills/vss-build-vision-agent/eval/"
        "profile_in_2_rt_cv_person_detection_harbor.json"
    )
    assert "does not copy unchanged model, device, source-count" in in2
    assert "normalized `resolved.yml`" in in2

    at1 = _contract(
        "skills/vss-build-vision-agent/eval/profile_at_1_alert_verification.json"
    )
    for phrase in (
        "exact stock `alerts` / `2d_cv` identity",
        "apply no service patches",
        "file-vs-directory type check",
        "provisioned through VIOS",
        "end offsets are recorded before and after",
        "both Kafka and Elasticsearch",
    ):
        assert phrase in at1

    in3 = _contract(
        "skills/vss-build-vision-agent/eval/"
        "profile_in_3_ingestion_detection_embeddings_harbor.json"
    )
    for phrase in (
        "captured before recreation/retry",
        "PID-to-container mapping",
        "RT-CV exit 100",
        "NvStreamer exit 1",
        "not guessed root causes",
    ):
        assert phrase in in3


def test_search_init_builds_use_host_network_without_default_bridge() -> None:
    def service_block(path: Path, service: str) -> str:
        text = path.read_text()
        start = text.index(f"  {service}:")
        rest = text[start + 1 :]
        match = re.search(r"\n  [A-Za-z0-9_.-]+:\s*(?:#.*)?\n", rest)
        return (
            text[start:] if match is None else text[start : start + 1 + match.start()]
        )

    kafka = service_block(
        ROOT / "deploy/docker/services/infra/compose.yml",
        "kafka-topic-init-container",
    )
    kibana = service_block(
        ROOT / "deploy/docker/developer-profiles/dev-profile-search/compose.yml",
        "kibana-init-container-search",
    )
    for block in (kafka, kibana):
        assert "\n    build:\n" in block
        assert "\n      network: host\n" in block
        assert "network: default" not in block


def test_origin_selector_canonicalizes_once_and_preserves_selected_host(
    tmp_path: Path,
) -> None:
    fake_curl = tmp_path / "curl"
    fake_curl.write_text(
        "#!/bin/sh\n"
        "out=\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  [ "$1" = -o ] && { shift; out=$1; }\n'
        "  shift\n"
        "done\n"
        ': > "$out"\n'
        "printf 503\n"
    )
    fake_curl.chmod(0o755)
    script = ROOT / "skills/vss-search-archive/scripts/select_brev_origin.sh"
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}

    result = subprocess.run(
        [
            str(script),
            "https://public.example",
            "http://http://127.0.0.1:7777/",
        ],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    assert json.loads(result.stdout) == {
        "origin": "http://127.0.0.1:7777",
        "media_scope": "host-local",
    }

    preserved = subprocess.run(
        [str(script), "https://public.example", "http://localhost:7777"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    assert json.loads(preserved.stdout)["origin"] == "http://localhost:7777"


def test_codec_dependent_lvs_is_explicitly_rtx_only() -> None:
    spec = _json("skills/vss-summarize-video/evals/lvs_profile_summarize.json")
    assert spec["requires_video_codec"] is True
    assert spec["resources"]["platforms"] == {"RTXPRO6000BW": {"gpu_count": 1}}
    setup = "\n".join([spec["expects"][0]["query"], *spec["expects"][0]["checks"]])
    assert "vss-vios-streamprocessing" in setup
    assert "restart count exactly zero" in setup
    assert "bounded VIOS upload/timeline probe" in setup


def test_openshell_matrix_keeps_codec_lvs_on_rtx_without_duplicates() -> None:
    old = os.environ.get("OPENSHELL_GPU_FLEET")
    os.environ["OPENSHELL_GPU_FLEET"] = "1"
    try:
        lvs = plan_matrix.build_matrix(
            ["skills/vss-summarize-video/evals/lvs_profile_summarize.json"]
        )
        compatible = plan_matrix.build_matrix(
            ["skills/vss-deploy-profile/evals/base.json"]
        )
        two_gpu = plan_matrix.build_matrix(
            [
                "skills/vss-build-vision-agent/eval/profile_combined_alerts_search_harbor.json"
            ]
        )
    finally:
        if old is None:
            os.environ.pop("OPENSHELL_GPU_FLEET", None)
        else:
            os.environ["OPENSHELL_GPU_FLEET"] = old

    assert len(lvs) == 1
    assert lvs[0]["platform"] == "RTXPRO6000BW"
    assert "gpus-1" in lvs[0]["runs_on"]
    assert len({leg["slug"] for leg in lvs}) == len(lvs)

    assert len(compatible) == 1
    assert compatible[0]["platform"] == "H200"
    assert "gpus-1" in compatible[0]["runs_on"]

    assert len(two_gpu) == 1
    assert two_gpu[0]["platform"] == "RTXPRO6000BW"
    assert "gpus-2" in two_gpu[0]["runs_on"]


def test_ineligible_changed_skill_is_a_failing_not_run_leg() -> None:
    old = os.environ.get("OPENSHELL_GPU_FLEET")
    os.environ["OPENSHELL_GPU_FLEET"] = "1"
    original = plan_matrix.spec_platform_config
    try:
        plan_matrix.spec_platform_config = lambda _path: {"L40S": {"gpu_count": 1}}
        legs = plan_matrix.build_matrix(["skills/vss-search-archive/evals/search.json"])
    finally:
        plan_matrix.spec_platform_config = original
        if old is None:
            os.environ.pop("OPENSHELL_GPU_FLEET", None)
        else:
            os.environ["OPENSHELL_GPU_FLEET"] = old

    assert len(legs) == 1
    assert legs[0]["kind"] == "not_run_infra_acquisition"
    assert legs[0]["skip_reason"].startswith("NOT_RUN_INFRA_ACQUISITION:")


def test_sop_prerequisites_are_machine_readable_and_fail_fast(
    tmp_path: Path, monkeypatch
) -> None:
    spec_path = (
        ROOT / "skills/vss-build-vision-agent/eval/"
        "profile_sop_1_compliance_monitoring.json"
    )
    spec = json.loads(spec_path.read_text())
    assert spec["required_local_images"] == ["ds-sop:1.0.0"]
    assert spec["required_docker_build_network"] == "host"
    assert spec["required_resolved_env"] == ["HOST_IP"]
    path_vars = tuple(spec["required_local_path_env"])
    assert path_vars == (
        "SOP_MODEL_DIR",
        "SOP_CONFIG_PATH",
        "SOP_TEST_VIDEO_PATH",
    )
    for name in path_vars:
        monkeypatch.delenv(name, raising=False)
    assert direct_progress.missing_required_local_paths(path_vars) == path_vars

    for name in path_vars:
        path = tmp_path / name.lower()
        path.mkdir()
        monkeypatch.setenv(name, str(path))
    assert direct_progress.missing_required_local_paths(path_vars) == ()
    assert direct_progress.load_required_docker_build_network(spec_path) == "host"

    gate_spec = tmp_path / "gate.json"
    gate_spec.write_text(
        json.dumps(
            {
                "required_resolved_env": ["HOST_IP"],
                "required_docker_build_network": "host",
            }
        )
    )
    monkeypatch.setenv("HOST_IP", "<HOST_IP>")
    with (
        mock.patch.object(
            direct_progress,
            "docker_build_network_available",
            return_value=True,
        ),
        pytest.raises(RuntimeError, match=r"BLOCKED:.*unresolved env=HOST_IP"),
    ):
        direct_progress.DirectAgentProgress(
            results_root=tmp_path / "placeholder-results",
            spec_path=gate_spec,
            repo_root=tmp_path,
        )

    monkeypatch.setenv("HOST_IP", "192.0.2.10")
    with (
        mock.patch.object(
            direct_progress,
            "docker_build_network_available",
            return_value=False,
        ),
        pytest.raises(RuntimeError, match=r"BLOCKED:.*docker build network=host"),
    ):
        direct_progress.DirectAgentProgress(
            results_root=tmp_path / "network-results",
            spec_path=gate_spec,
            repo_root=tmp_path,
        )


def test_model_exit_fails_immediately_and_archives_before_waiting(
    tmp_path: Path,
) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "expected_services": ["rtvi-vlm"],
                "fail_fast_services": ["rtvi-vlm"],
            }
        )
    )
    monitor = direct_progress.DirectAgentProgress(
        results_root=tmp_path / "results",
        spec_path=spec,
        repo_root=tmp_path,
    )
    monitor.compose_file = tmp_path / "resolved.yml"
    monitor.compose_file.write_text("services: {}\n")
    failed = {
        "service": "rtvi-vlm",
        "state": "exited",
        "health": "unhealthy",
        "exit_code": 137,
        "restart_count": 0,
    }
    with (
        mock.patch.object(monitor, "_sample_images"),
        mock.patch.object(monitor, "_safe_service_rows", return_value=[failed]),
        mock.patch.object(monitor, "_archive_failed_service_evidence") as archive,
        pytest.raises(
            direct_progress.DirectAgentWatchdogExpired,
            match="fail-fast dependency",
        ),
    ):
        monitor.sample()
    archive.assert_called_once_with([failed])


def test_failed_service_evidence_is_bounded_redacted_and_structural(
    tmp_path: Path,
) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"expected_services": ["rtvi-vlm"]}))
    monitor = direct_progress.DirectAgentProgress(
        results_root=tmp_path / "results",
        spec_path=spec,
        repo_root=tmp_path,
    )
    monitor.compose_file = tmp_path / "resolved.yml"
    monitor.compose_file.write_text("services: {}\n")
    bounded = direct_progress.BoundedCommandResult
    inspect = json.dumps(
        [
            {
                "Id": "a" * 64,
                "Name": "/vss-rtvi-vlm",
                "Config": {
                    "Image": "rtvi-vlm:pinned",
                    "Env": ["API_KEY=must-not-survive"],
                },
                "State": {
                    "Status": "exited",
                    "ExitCode": 137,
                    "OOMKilled": True,
                    "Pid": 4242,
                    "Health": {"Status": "unhealthy"},
                },
                "RestartCount": 0,
                "HostConfig": {"NetworkMode": "host"},
                "Mounts": [{"Source": "/secret", "Destination": "/models"}],
            }
        ]
    )
    results = [
        bounded(
            0,
            json.dumps(
                {
                    "services": {
                        "rtvi-vlm": {
                            "image": "rtvi-vlm:pinned",
                            "network_mode": "host",
                            "build": {
                                "network": "host",
                                "args": {"API_KEY": "compose-secret"},
                            },
                            "depends_on": {},
                            "volumes": [{"target": "/models", "source": "/secret"}],
                        }
                    }
                }
            ),
            False,
        ),
        bounded(0, "a" * 64 + "\n", False),
        bounded(0, inspect, False),
        bounded(
            0,
            "API_KEY=supersecret\n"
            "Authorization: Bearer headersecret\n"
            "hf_abcdefghijk\n"
            "https://user:pass@example.test/path\n"
            "model exited\n",
            False,
        ),
        bounded(0, '{"status":"die","token=hidden","ghp_abcdefghijk"}\n', False),
    ]
    with mock.patch.object(direct_progress, "_run_bounded", side_effect=results):
        monitor._archive_failed_service_evidence(
            [
                {
                    "service": "rtvi-vlm",
                    "state": "exited",
                    "health": "unhealthy",
                    "exit_code": 137,
                    "restart_count": 0,
                }
            ]
        )
    artifact = (monitor.results_root / "failed-service-evidence.json").read_text()
    assert "must-not-survive" not in artifact
    assert "supersecret" not in artifact
    assert "headersecret" not in artifact
    assert "abcdefghijk" not in artifact
    assert "user:pass" not in artifact
    assert "compose-secret" not in artifact
    assert "hidden" not in artifact
    payload = json.loads(artifact)
    service = payload["services"][0]
    assert service["inspect"]["oom_killed"] is True
    assert service["pid_to_container"] == {
        "pid": 4242,
        "container_id": "a" * 12,
        "service": "rtvi-vlm",
    }
    assert payload["retention"]["max_artifact_bytes"] == (
        direct_progress.MAX_FAILURE_EVIDENCE_BYTES
    )
