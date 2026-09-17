# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Composition regression for host-CLI analytics service ownership."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILLS_ROOT = Path(__file__).resolve().parents[3]
REPOSITORY = SKILLS_ROOT.parent
BUILD_SKILL = SKILLS_ROOT / "vss-build-vision-ai"
QUERY_SKILL = SKILLS_ROOT / "operations" / "vss-query-analytics"
SCRIPTS = BUILD_SKILL / "scripts"
ALERTS_PROFILE = REPOSITORY / "deploy/docker/developer-profiles/dev-profile-alerts"
BASE_PROFILE = REPOSITORY / "deploy/docker/developer-profiles/dev-profile-base"


def _docker_compose_available() -> bool:
    docker = shutil.which("docker")
    if docker is None:
        return False
    return (
        subprocess.run(
            [docker, "compose", "version"],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


requires_docker_compose = pytest.mark.skipif(
    not _docker_compose_available(),
    reason="docker compose is required for resolved composition tests",
)

sys.path.insert(0, str(SCRIPTS))
from resolve_service_graph import (
    UnexpectedHarnessDeltaError,
    analytics_readiness_targets,
    resolve_service_profiles,
    validate_harness_only_delta,
)


def _env_value(path: Path, key: str) -> str:
    prefix = f"{key}="
    for line in path.read_text().splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip("'\"")
    raise AssertionError(f"{key} is not defined in {path}")


def _alerts_profiles(mode: str = "CV") -> tuple[str, ...]:
    overrides = ALERTS_PROFILE / "overrides.env"
    value = _env_value(overrides, f"COMPOSE_PROFILES_{mode}")
    value = value.replace("${LLM_MODE}", _env_value(overrides, "LLM_MODE"))
    value = value.replace(
        "${LLM_NAME_SLUG}",
        _env_value(overrides, "LLM_NAME_SLUG"),
    )
    return tuple(value.split(","))


def _base_profiles() -> tuple[str, ...]:
    overrides = BASE_PROFILE / "overrides.env"
    value = _env_value(overrides, "COMPOSE_PROFILES")
    value = value.replace("${LLM_MODE}", _env_value(overrides, "LLM_MODE"))
    value = value.replace(
        "${LLM_NAME_SLUG}",
        _env_value(overrides, "LLM_NAME_SLUG"),
    )
    return tuple(value.split(","))


def _compose_config(
    tmp_path: Path,
    profiles: tuple[str, ...],
    foundation_profile: Path = ALERTS_PROFILE,
) -> dict:
    override = tmp_path / "override.env"
    override.write_text(
        "\n".join(
            (
                f"COMPOSE_PROFILES={','.join(profiles)}",
                f"VSS_APPS_DIR={REPOSITORY / 'deploy/docker'}",
                f"VSS_DATA_DIR={tmp_path / 'data'}",
                "HOST_IP=127.0.0.1",
            )
        )
        + "\n"
    )
    command = [
        "docker",
        "compose",
        "--env-file",
        str(REPOSITORY / "deploy/docker/containers.env"),
        "--env-file",
        str(foundation_profile / ".env"),
        "--env-file",
        str(foundation_profile / "overrides.env"),
        "--env-file",
        str(override),
        "-f",
        str(REPOSITORY / "deploy/docker/compose.yml"),
        "config",
        "--format",
        "json",
    ]
    result = subprocess.run(
        command,
        cwd=REPOSITORY,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("mode", ("CV", "VLM"))
def test_stock_alerts_keeps_va_mcp_with_the_in_stack_agent(mode: str) -> None:
    profiles = _alerts_profiles(mode)
    assert "vss-agent" in profiles
    assert "vss-va-mcp" in profiles


def test_host_cli_honors_explicit_legacy_mcp_delta() -> None:
    profiles = resolve_service_profiles(
        ("alert-bridge", "vss-agent", "vss-va-mcp"),
        requested_profiles=("vss-va-mcp",),
        host_cli=True,
    )
    assert profiles == ("alert-bridge", "vss-va-mcp")


def test_base_no_harness_delta_removes_only_agent() -> None:
    foundation = _base_profiles()
    profiles = resolve_service_profiles(foundation, host_cli=True)

    validate_harness_only_delta(foundation, profiles)
    assert set(foundation) - set(profiles) == {"vss-agent"}
    assert {
        "vss-ui",
        "phoenix",
        "vss-haproxy-ingress",
        "redis",
        "centralizedb",
        "vst-ingress",
        "sensor-ms",
        "streamprocessing-ms",
        "rtvi-vlm",
    } <= set(profiles)
    assert any(profile.startswith("llm_") for profile in profiles)


def test_harness_only_validation_rejects_unrequested_pruning() -> None:
    foundation = _base_profiles()
    over_pruned = resolve_service_profiles(
        foundation,
        excluded_profiles=(
            "vss-ui",
            "phoenix",
            "vss-haproxy-ingress",
            "redis",
        ),
        host_cli=True,
    )

    with pytest.raises(
        UnexpectedHarnessDeltaError,
        match="unexpected removals: phoenix, redis, vss-haproxy-ingress, vss-ui",
    ):
        validate_harness_only_delta(foundation, over_pruned)


def test_explicit_headless_capability_removals_remain_allowed() -> None:
    foundation = _base_profiles()
    profiles = resolve_service_profiles(
        foundation,
        excluded_profiles=("vss-ui", "phoenix", "vss-haproxy-ingress"),
        host_cli=True,
    )

    assert {"vss-agent", "vss-ui", "phoenix", "vss-haproxy-ingress"}.isdisjoint(
        profiles
    )
    assert {
        "redis",
        "centralizedb",
        "vst-ingress",
        "sensor-ms",
        "streamprocessing-ms",
        "rtvi-vlm",
    } <= set(profiles)


def test_active_build_flow_guards_harness_only_deltas() -> None:
    skill = (BUILD_SKILL / "SKILL.md").read_text()
    step_five = skill[skill.index("5. Determine the effective service set.") :]
    step_five = step_five[: step_five.index("6. Before writing delta artifacts")]

    assert "Harness-only delta invariant" in step_five
    assert "ADDED_PROFILES=∅" in step_five
    assert "REMOVED_PROFILES" in step_five
    assert "validate_harness_only_delta" in step_five
    assert "bypass generic forward-closure/unused-service pruning" in step_five


@requires_docker_compose
def test_base_harness_only_compose_keeps_foundation_services(tmp_path: Path) -> None:
    profiles = resolve_service_profiles(_base_profiles(), host_cli=True)
    document = _compose_config(tmp_path, profiles, BASE_PROFILE)
    services = set(document["services"])

    assert "vss-agent" not in services
    assert {
        "vss-ui",
        "phoenix",
        "vss-haproxy-ingress",
        "redis",
        "centralizedb",
        "vst-ingress",
        "sensor-ms",
        "streamprocessing-ms",
        "rtvi-vlm",
        "nemotron-3.5-lightning-30b-a3b-shared-gpu",
    } <= services


@requires_docker_compose
def test_stock_alerts_compose_includes_agent_mcp_tools(tmp_path: Path) -> None:
    document = _compose_config(tmp_path, _alerts_profiles())
    services = set(document["services"])
    assert {"vss-agent", "vss-va-mcp"} <= services
    assert document["services"]["vss-va-mcp"]["command"][0:3] == [
        "mcp",
        "serve",
        "--config_file",
    ]


@requires_docker_compose
def test_nemoclaw_alerts_lvs_resolves_without_agent_or_va_mcp(
    tmp_path: Path,
) -> None:
    profiles = resolve_service_profiles(
        _alerts_profiles(),
        requested_profiles=("lvs-server",),
        host_cli=True,
    )
    document = _compose_config(tmp_path, profiles)
    services = set(document["services"])

    assert {
        "alert-bridge",
        "vss-video-analytics-api",
        "lvs-server",
        "rtvi-vlm",
        "elasticsearch",
        "kafka",
        "redis",
        "centralizedb",
        "vst-ingress",
        "sensor-ms",
        "streamprocessing-ms",
        "vss-ui",
        "phoenix",
        "vss-haproxy-ingress",
    } <= services
    assert any("nemotron-3.5-lightning-30b-a3b" in name for name in services)
    assert {"vss-agent", "vss-va-mcp"}.isdisjoint(services)
    assert "VSS_VA_MCP_CONFIG_FILE" not in json.dumps(document)

    targets = analytics_readiness_targets(services)
    assert {target.service for target in targets} == {
        "alert-bridge",
        "vss-video-analytics-api",
    }
    assert all("9901" not in target.url for target in targets)


@requires_docker_compose
def test_explicit_legacy_mcp_selection_remains_available(tmp_path: Path) -> None:
    profiles = resolve_service_profiles(
        _alerts_profiles(),
        requested_profiles=("vss-va-mcp",),
        host_cli=True,
    )
    document = _compose_config(tmp_path, profiles)
    services = set(document["services"])

    assert "vss-va-mcp" in services
    assert "vss-agent" not in services
    assert document["services"]["vss-va-mcp"]["command"][0:3] == [
        "mcp",
        "serve",
        "--config_file",
    ]
    assert any(
        target.service == "vss-va-mcp" and "9901" in target.url
        for target in analytics_readiness_targets(services)
    )


def test_query_skill_uses_cli_without_legacy_endpoint_commands() -> None:
    skill = (QUERY_SKILL / "SKILL.md").read_text()

    assert "vss analytics incidents" in skill
    assert "vss analytics sensors" in skill
    assert "vss vios list" in skill
    for forbidden in ("9901", "/va-mcp", "VA_MCP_URL", "method=initialize"):
        assert forbidden not in skill


def test_nemoclaw_workspace_and_eval_adapter_route_to_cli() -> None:
    paths = [
        REPOSITORY / ".openclaw/workspace/AGENTS.md",
        REPOSITORY / ".openclaw/workspace/_nemoclaw/AGENTS.md",
        REPOSITORY / ".github/skill-eval/adapters/vss-query-analytics/generate.py",
    ]
    for path in paths:
        content = path.read_text()
        assert "vss-query-analytics" in content
        assert "vss analytics" in content
        for forbidden in ("http://${HOST_IP:-localhost}:9901/mcp", "method=initialize"):
            assert forbidden not in content


def test_incident_report_mode_b_uses_cli_and_mode_c_keeps_legacy_mcp() -> None:
    report = SKILLS_ROOT / "operations/vss-generate-video-report"
    mode_b = (report / "references/report-types/incident-range.md").read_text()
    mode_c = (report / "references/report-types/sop-compliance.md").read_text()

    assert "vss analytics incidents" in mode_b
    assert "video_analytics__get_incidents" not in mode_b
    assert "video_analytics__get_sop_report" in mode_c
    assert "VA_MCP_URL" in mode_c
