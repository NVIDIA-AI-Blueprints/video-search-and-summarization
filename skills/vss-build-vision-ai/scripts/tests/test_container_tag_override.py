# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Resolved Compose contracts for container image tags and Alerts UI modes."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILLS_ROOT = Path(__file__).resolve().parents[3]
REPOSITORY = SKILLS_ROOT.parent
BUILD_SKILL = SKILLS_ROOT / "vss-build-vision-ai"
SCRIPTS = BUILD_SKILL / "scripts"
BASE_PROFILE = REPOSITORY / "deploy/docker/developer-profiles/dev-profile-base"
ALERTS_PROFILE = REPOSITORY / "deploy/docker/developer-profiles/dev-profile-alerts"
CV_FLAG = "NEXT_PUBLIC_ALERTS_TAB_MANAGE_ALERTS_SUB_TAB_ENABLE_CV_ALERTS_VERIFICATION"
REALTIME_FLAG = "NEXT_PUBLIC_ALERTS_TAB_MANAGE_ALERTS_SUB_TAB_ENABLE_REALTIME_ALERTS"
DEFAULT_TAG = "develop-latest"
RELEASE_TAG = "3.4.0-test"
# One service per tag shape containers.env produces: the agent UI reads
# VSS_CONTAINER_TAG straight from its Compose default, analytics reads a plain
# derived tag, and RT-VLM reads a derived tag plus VSS_CONTAINER_TAG_SUFFIX.
PROBE_PROFILES = ("vss-ui", "vss-video-analytics-api", "rtvi-vlm")
PROBE_IMAGES = ("vss-agent-ui", "vss-video-analytics-api", "vss-rt-vlm")

sys.path.insert(0, str(SCRIPTS))
from validate_resolved_yml import (
    MANAGED_IMAGE_NAMES,
    alerts_ui_errors,
    container_tag_errors,
    validate_document,
)


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


def _resolved_tags(
    tmp_path: Path,
    exported: dict[str, str] | None = None,
    override_lines: tuple[str, ...] = (),
) -> dict[str, str]:
    """Resolve the probe service set and return image name -> resolved tag."""
    override = tmp_path / "override.env"
    override.write_text(
        "\n".join(
            (
                f"COMPOSE_PROFILES={','.join(PROBE_PROFILES)}",
                f"VSS_APPS_DIR={REPOSITORY / 'deploy/docker'}",
                f"VSS_DATA_DIR={tmp_path / 'data'}",
                "HOST_IP=127.0.0.1",
                *override_lines,
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
        str(BASE_PROFILE / ".env"),
        "--env-file",
        str(BASE_PROFILE / "overrides.env"),
        "--env-file",
        str(override),
        "-f",
        str(REPOSITORY / "deploy/docker/compose.yml"),
        "config",
        "--images",
        "--no-consistency",
    ]
    # A tag exported into the test runner's own shell would decide the result.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("VSS_CONTAINER_TAG", "VSS_CONTAINER_TAG_SUFFIX")
    }
    environment.update(exported or {})
    result = subprocess.run(
        command,
        cwd=REPOSITORY,
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    assert result.returncode == 0, result.stderr

    tags = {}
    for image in result.stdout.split():
        repository, _, tag = image.rpartition(":")
        tags[repository.rsplit("/", 1)[-1]] = tag
    assert set(PROBE_IMAGES) <= set(tags), tags
    return tags


def _document(**images: str) -> dict:
    return {"services": {name: {"image": image} for name, image in images.items()}}


@requires_docker_compose
def test_exported_tag_reaches_every_managed_image(tmp_path: Path) -> None:
    tags = _resolved_tags(tmp_path, {"VSS_CONTAINER_TAG": RELEASE_TAG})

    assert {tags[image] for image in PROBE_IMAGES} == {RELEASE_TAG}
    assert (
        container_tag_errors(
            _document(
                **{name: f"registry/{name}:{tags[name]}" for name in PROBE_IMAGES}
            ),
            RELEASE_TAG,
        )
        == []
    )


@requires_docker_compose
def test_sbsa_suffix_augments_the_common_tag(tmp_path: Path) -> None:
    tags = _resolved_tags(
        tmp_path,
        {"VSS_CONTAINER_TAG": RELEASE_TAG, "VSS_CONTAINER_TAG_SUFFIX": "-sbsa"},
    )

    assert tags["vss-rt-vlm"] == f"{RELEASE_TAG}-sbsa"
    # The suffix applies to the SBSA-built services only.
    assert tags["vss-agent-ui"] == RELEASE_TAG
    assert tags["vss-video-analytics-api"] == RELEASE_TAG


@requires_docker_compose
def test_concrete_sbsa_tag_supersedes_the_common_tag(tmp_path: Path) -> None:
    # sizing.md writes the derived <sbsa-tag> per service instead of the suffix;
    # override.env is read after containers.env, which is what makes a pin win.
    tags = _resolved_tags(
        tmp_path,
        {"VSS_CONTAINER_TAG": RELEASE_TAG},
        (f"VSS_RT_VLM_TAG={RELEASE_TAG}-sbsa",),
    )

    assert tags["vss-rt-vlm"] == f"{RELEASE_TAG}-sbsa"
    assert tags["vss-video-analytics-api"] == RELEASE_TAG


@requires_docker_compose
def test_no_override_keeps_the_containers_env_default(tmp_path: Path) -> None:
    tags = _resolved_tags(tmp_path)

    assert {tags[image] for image in PROBE_IMAGES} == {DEFAULT_TAG}


@requires_docker_compose
def test_recording_the_tag_without_exporting_it_is_caught(tmp_path: Path) -> None:
    tags = _resolved_tags(
        tmp_path, override_lines=(f"VSS_CONTAINER_TAG={RELEASE_TAG}",)
    )

    # containers.env derived the per-service tags before this layer was read, so
    # only the UI — whose Compose default reads the common knob — moved.
    assert tags["vss-agent-ui"] == RELEASE_TAG
    assert tags["vss-rt-vlm"] == DEFAULT_TAG
    assert tags["vss-video-analytics-api"] == DEFAULT_TAG

    errors = container_tag_errors(
        _document(**{name: f"registry/{name}:{tags[name]}" for name in PROBE_IMAGES}),
        RELEASE_TAG,
    )
    assert len(errors) == 2
    assert all(RELEASE_TAG in error and DEFAULT_TAG in error for error in errors)


def test_container_tag_check_is_inert_without_a_selection() -> None:
    document = _document(**{"vss-ui": f"registry/vss-agent-ui:{DEFAULT_TAG}"})

    assert container_tag_errors(document, None) == []
    assert container_tag_errors(document, "") == []
    assert container_tag_errors(document, DEFAULT_TAG) == []


def test_container_tag_check_accepts_pins_suffixes_and_third_parties() -> None:
    document = _document(
        **{
            "vss-ui": f"registry/vss-agent-ui:{RELEASE_TAG}",
            "rtvi-vlm": f"registry/vss-rt-vlm:{RELEASE_TAG}-sbsa",
            "vss-auto-calibration": "registry/vss-auto-calibration:3.3.0-5",
            "redis": "redis:7",
            "local-registry": "localhost:5000/vss-agent-ui",
        }
    )

    assert container_tag_errors(document, RELEASE_TAG) == []


def test_container_tag_check_ignores_unmanaged_develop_latest_images() -> None:
    document = _document(
        **{
            "custom": f"ghcr.io/example/custom-service:{DEFAULT_TAG}",
            "custom-arm": f"ghcr.io/example/custom-service:{DEFAULT_TAG}-sbsa",
            "rtvi-vlm": f"registry/vss-rt-vlm:{DEFAULT_TAG}-sbsa",
        }
    )

    errors = container_tag_errors(document, RELEASE_TAG)
    assert len(errors) == 1
    assert "'rtvi-vlm'" in errors[0]


def test_managed_image_names_cover_containers_env() -> None:
    text = (REPOSITORY / "deploy/docker/containers.env").read_text()
    assignments = dict(re.findall(r'^(\w+)="(.*)"$', text, re.MULTILINE))
    governed = set()
    for key, value in assignments.items():
        if not key.endswith("_IMAGE"):
            continue
        tag = assignments.get(f"{key[: -len('_IMAGE')]}_TAG", "")
        if "${VSS_CONTAINER_TAG" in value or "${VSS_CONTAINER_TAG" in tag:
            governed.add(re.search(r"}/([\w.-]+)", value)[1])

    assert governed
    assert governed <= MANAGED_IMAGE_NAMES


def test_empty_expect_container_tag_is_rejected(tmp_path: Path) -> None:
    resolved = tmp_path / "resolved.yml"
    resolved.write_text("services:\n  redis:\n    image: redis:7\n")

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "validate_resolved_yml.py"),
            str(resolved),
            "--repo-root",
            str(REPOSITORY),
            "--expect-container-tag",
            "   ",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "non-empty tag" in completed.stderr


def test_skill_documents_the_common_tag_input() -> None:
    skill = (BUILD_SKILL / "SKILL.md").read_text()
    prose = " ".join(skill.split())

    assert "### Container image tag" in skill
    assert "export VSS_CONTAINER_TAG=<tag>" in skill
    assert "**Never edit `containers.env`**" in prose
    assert "`VSS_CONTAINER_TAG_SUFFIX`" in skill


def test_every_resolve_block_exports_the_selected_tag() -> None:
    for reference in (
        "references/composition.md",
        "references/deployment.md",
        "references/profiles/warehouse.md",
    ):
        text = (BUILD_SKILL / reference).read_text()

        assert "export VSS_CONTAINER_TAG" in text, reference
        assert 'tag_args=(--expect-container-tag "$VSS_CONTAINER_TAG")' in text, (
            reference
        )
        assert '"${tag_args[@]}"' in text, reference


@pytest.mark.parametrize("value", ["true", True, "FALSE", "False", "0", "", None])
def test_validator_rejects_cv_editor_on_always_on_alerts(value, tmp_path):
    ui_env = {} if value is None else {CV_FLAG: value}
    document = {
        "services": {
            "alert-bridge": {"environment": {"ALERT_AGENT_ALWAYS_ON": "true"}},
            "vss-ui": {"environment": ui_env},
        }
    }
    assert any(CV_FLAG in error for error in validate_document(document, tmp_path))


@pytest.mark.parametrize(
    "ui_env", [{CV_FLAG: "false"}, {CV_FLAG: False}, [f"{CV_FLAG}=false"]]
)
def test_validator_accepts_hidden_cv_editor(ui_env):
    document = {
        "services": {
            "alert-bridge": {"environment": ["ALERT_AGENT_ALWAYS_ON=true"]},
            "vss-ui": {"environment": ui_env},
        }
    }
    assert alerts_ui_errors(document) == []


@pytest.mark.parametrize("bridge_env", [{"ALERT_AGENT_ALWAYS_ON": "false"}, {}])
def test_verification_mode_and_other_profiles_keep_cv_editor(bridge_env):
    document = {
        "services": {
            "alert-bridge": {"environment": bridge_env},
            "vss-ui": {"environment": {CV_FLAG: "true"}},
        }
    }
    assert alerts_ui_errors(document) == []


@pytest.mark.parametrize("services", [{}, {"alert-bridge": {}}, {"vss-ui": {}}])
def test_headless_and_non_alerts_builds_are_unaffected(services):
    assert alerts_ui_errors({"services": services}) == []


def _compose_config(override: Path) -> dict:
    command = ["docker", "compose"]
    for env_file in (
        REPOSITORY / "deploy/docker/containers.env",
        ALERTS_PROFILE / ".env",
        ALERTS_PROFILE / "overrides.env",
        override,
    ):
        command.extend(["--env-file", str(env_file)])
    command.extend(
        [
            "-f",
            str(REPOSITORY / "deploy/docker/compose.yml"),
            "config",
            "--no-consistency",
            "--format",
            "json",
        ]
    )
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@requires_docker_compose
@pytest.mark.parametrize("mode,expected_cv", [("2d_vlm", "false"), ("2d_cv", "true")])
def test_compose_flags_follow_explicit_alerts_mode_contract(
    tmp_path, mode, expected_cv
):
    backend_enabled = "true" if mode == "2d_vlm" else "false"
    message_bus = "kafka" if mode == "2d_vlm" else ""
    override = tmp_path / "override.env"
    override.write_text(
        f"MODE={mode}\nCOMPOSE_PROFILES=vss-ui,alert-bridge,rtvi-vlm\n"
        f"VSS_APPS_DIR={REPOSITORY / 'deploy/docker'}\n"
        f"ALERT_AGENT_ALWAYS_ON={backend_enabled}\n"
    )
    # Reproduce the omission with the real Compose env layers before writing
    # the build's explicit settings. MODE alone leaves the CV editor and
    # generated-message output on, despite the legacy KAFKA_ENABLED=false.
    omitted = _compose_config(override)
    assert omitted["services"]["vss-ui"]["environment"][CV_FLAG] == "true"
    assert bool(alerts_ui_errors(omitted)) == (mode == "2d_vlm")
    assert omitted["services"]["rtvi-vlm"]["environment"]["KAFKA_ENABLED"] == "false"
    assert omitted["services"]["rtvi-vlm"]["environment"]["MESSAGE_BUS"] == "kafka"

    # Apply the supported build override contract directly. Neither resolving
    # Compose nor validating its UI contract depends on a deployment launcher.
    with override.open("a") as env_file:
        env_file.write(
            f"{REALTIME_FLAG}=true\n{CV_FLAG}={expected_cv}\n"
            f"RTVI_VLM_KAFKA_ENABLED={backend_enabled}\n"
            f"RTVI_VLM_MESSAGE_BUS={message_bus}\n"
        )
    configured = _compose_config(override)
    ui_env = configured["services"]["vss-ui"]["environment"]
    assert ui_env[REALTIME_FLAG] == "true"
    assert ui_env[CV_FLAG] == expected_cv
    assert alerts_ui_errors(configured) == []
    assert (
        configured["services"]["alert-bridge"]["environment"]["ALERT_AGENT_ALWAYS_ON"]
        == backend_enabled
    )
    rt_vlm_env = configured["services"]["rtvi-vlm"]["environment"]
    assert rt_vlm_env["KAFKA_ENABLED"] == backend_enabled
    assert rt_vlm_env["MESSAGE_BUS"] == message_bus
    if message_bus == "kafka":
        assert rt_vlm_env["KAFKA_BOOTSTRAP_SERVERS"].strip()
