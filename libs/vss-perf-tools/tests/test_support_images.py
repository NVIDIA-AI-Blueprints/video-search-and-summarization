# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "libs/vss-perf-tools"))
from support_images import environment, load, save_snapshot  # noqa: E402

SERVICES = [
    "video-summarization",
    "analytics/behavior-analytics",
    "analytics/video-analytics-api",
    "rtvi/rt-vlm",
    "rtvi/rt-embed",
    "alert",
]


def config_path(service):
    return ROOT / "services" / service / "perf/setup/configs/images.yaml"


@pytest.mark.parametrize("service", SERVICES)
def test_service_configs_are_flat_and_do_not_configure_target_image(service):
    config = load(config_path(service))
    assert environment(config, {}) == config["images"]
    assert (
        not {"ARTIFACT_REF", "IMAGE_TAG", "LVS_IMAGE", "RTVI_IMAGE", "PERF_ALERT_IMAGE"}
        & config["images"].keys()
    )
    assert set(config) <= {"schema_version", "images", "package_managed_images"}


@pytest.mark.parametrize(
    "name,value",
    [
        ("PATH", "alpine:3.24.1"),
        ("PERF_ALERT_IMAGE", "example/target:tag"),
        ("PERF_HELPER_IMAGE", {"ref": "alpine:3.24.1"}),
        ("PERF_HELPER_IMAGE", " image:bad"),
        ("PERF_HELPER_IMAGE", "repo/image:tag\nexport SECRET=bad"),
        ("PERF_HELPER_IMAGE", "$(touch /tmp/bad)"),
    ],
)
def test_invalid_names_references_and_old_nested_format_fail(tmp_path, name, value):
    path = tmp_path / "images.yaml"
    path.write_text(yaml.safe_dump({"schema_version": 1, "images": {name: value}}))
    with pytest.raises(ValueError):
        load(path)


def test_overrides_are_validated_and_snapshot_records_only_effective_images(tmp_path):
    config = load(config_path("rtvi/rt-vlm"))
    effective = environment(
        config,
        {"VST_SENSOR_IMAGE": "example/sensor@sha256:" + "a" * 64, "TOKEN": "secret"},
    )
    assert effective["VST_SENSOR_IMAGE"].startswith("example/sensor@sha256:")
    path = tmp_path / "support-images.json"
    save_snapshot(path, effective)
    assert json.loads(path.read_text())["images"] == effective
    assert "secret" not in path.read_text()
    with pytest.raises(ValueError):
        environment(config, {"VST_SENSOR_IMAGE": "malformed image"})


def test_shell_loader_exports_variables_without_changing_setup_files(tmp_path):
    config = config_path("rtvi/rt-vlm")
    before = config.read_bytes()
    snapshot = tmp_path / "snapshot.json"
    env = dict(os.environ, VST_SENSOR_IMAGE="registry.example/custom-sensor:tag")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "libs/vss-perf-tools/support_images.py"),
            "--config",
            str(config),
            "--shell",
            "--snapshot",
            str(snapshot),
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "export VST_SENSOR_IMAGE=registry.example/custom-sensor:tag" in result.stdout
    assert config.read_bytes() == before
    assert (
        json.loads(snapshot.read_text())["images"]["VST_SENSOR_IMAGE"]
        == env["VST_SENSOR_IMAGE"]
    )


def test_shared_tools_are_available_in_every_sparse_checkout():
    for service in SERVICES:
        path = ROOT / "services" / service / "perf/configs/perf-configs.yaml"
        for row in yaml.safe_load(path.read_text())["perf_configs"]:
            assert "libs/vss-perf-tools/" in row["checkoutPaths"]


def compose(files, extra):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required for offline interpolation checks")
    version = subprocess.run(["docker", "compose", "version"], capture_output=True)
    if version.returncode:
        pytest.skip("Docker Compose CLI is unavailable")
    command = ["docker", "compose"]
    for file in files:
        command += ["-f", str(file)]
    env = {k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ}
    env.update(extra)
    result = subprocess.run(
        command + ["config", "--format", "json"],
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        pytest.fail("Compose configuration failed: " + result.stderr)
    return json.loads(result.stdout)["services"]


def infra_environment(tmp_path):
    return {
        "VSS_APPS_DIR": str(ROOT / "deploy/docker"),
        "VSS_DATA_DIR": str(tmp_path),
        "HOST_IP": "localhost",
        "STREAM_TYPE": "kafka",
        "COMPOSE_PROFILES": "elasticsearch,elasticsearch-init-container,kibana,logstash,broker-health-check,kafka,kafka-topic-init-container",
    }


def test_shared_blueprint_defaults_work_without_perf_variables(tmp_path):
    services = compose(
        [ROOT / "deploy/docker/services/infra/compose.yml"], infra_environment(tmp_path)
    )
    assert services["kafka"]["image"] == "confluentinc/cp-kafka:8.3.0"
    assert services["elasticsearch"]["image"] == "elasticsearch"
    assert services["logstash"]["image"] == "docker.elastic.co/logstash/logstash:9.4.4"
    assert (
        services["elasticsearch"]["build"]
        .get("args", {})
        .get("PERF_ELASTICSEARCH_BASE_IMAGE")
        is None
    )


def test_image_edit_propagates_to_compose_and_build_arguments(tmp_path):
    config = load(config_path("analytics/behavior-analytics"))
    config["images"]["PERF_KAFKA_IMAGE"] = "registry.example/kafka:custom"
    config["images"]["PERF_ELASTICSEARCH_BASE_IMAGE"] = (
        "registry.example/es@sha256:" + "b" * 64
    )
    env = {**infra_environment(tmp_path), **environment(config, {})}
    services = compose([ROOT / "deploy/docker/services/infra/compose.yml"], env)
    assert services["kafka"]["image"] == config["images"]["PERF_KAFKA_IMAGE"]
    assert (
        services["elasticsearch"]["build"]["args"]["PERF_ELASTICSEARCH_BASE_IMAGE"]
        == config["images"]["PERF_ELASTICSEARCH_BASE_IMAGE"]
    )
    assert (
        services["broker-health-check"]["build"]["args"]["PERF_KAFKA_IMAGE"]
        == config["images"]["PERF_KAFKA_IMAGE"]
    )


@pytest.mark.parametrize("service", ["rt-vlm", "rt-embed"])
def test_rtvi_compose_uses_config_and_preserves_target_artifact(service):
    perf = ROOT / "services/rtvi" / service / "perf"
    config = load(perf / "setup/configs/images.yaml")
    config["images"]["PERF_PROMETHEUS_IMAGE"] = "registry.example/prometheus:custom"
    target = "registry.example/target@sha256:" + "c" * 64
    services = compose(
        [perf / "setup/compose/compose.perf.yaml"],
        {**environment(config, {}), "RTVI_IMAGE": target, "BACKEND_PORT": "8010"},
    )
    assert services["rtvi-server"]["image"] == target
    assert services["prometheus"]["image"] == "registry.example/prometheus:custom"


@pytest.mark.parametrize(
    "file",
    sorted(
        p
        for p in (ROOT / "services/video-summarization/perf/setup/compose").glob(
            "*.yaml"
        )
        if p.name.startswith(("x86-", "spark-", "thor-", "orin-"))
    ),
)
def test_lvs_configs_resolve_from_original_files(file):
    config = load(config_path("video-summarization"))
    target = "registry.example/lvs:target"
    env = {
        **environment(config, {}),
        "LVS_IMAGE": target,
        "COMPOSE_PROFILES": "media",
        "REMOTE_LLM_BASE_URL": "http://example.invalid:8000/v1",
        "REMOTE_LLM_MODEL": "example",
    }
    services = compose([file], env)
    assert services["lvs"]["image"] == target
    assert services["media-server"]["image"] == config["images"]["PERF_NGINX_IMAGE"]


def test_shared_vss_registry_and_tag_selection_still_works(tmp_path):
    file = ROOT / "deploy/docker/services/analytics/behavior-analytics/compose.yml"
    env = {
        **infra_environment(tmp_path),
        "VSS_CONTAINER_REGISTRY": "registry.example/vss",
        "VSS_BEHAVIOR_ANALYTICS_TAG": "release-test",
    }
    services = compose([file], env)
    assert (
        services["vss-behavior-analytics-base"]["image"]
        == "registry.example/vss/vss-behavior-analytics:release-test"
    )
    env["PERF_VSS_BEHAVIOR_ANALYTICS_IMAGE"] = (
        "registry.example/override@sha256:" + "d" * 64
    )
    services = compose([file], env)
    assert (
        services["vss-behavior-analytics-base"]["image"]
        == env["PERF_VSS_BEHAVIOR_ANALYTICS_IMAGE"]
    )


def test_alerts_target_artifact_and_eval_images_interpolate_without_rewriting(tmp_path):
    config = load(config_path("alert"))
    env = {
        **infra_environment(tmp_path),
        **environment(config, {}),
        "COMPOSE_PROFILES": "alert-bridge,elasticsearch,elasticsearch-init-container,kafka,kafka-topic-init-container",
        "PERF_ALERT_IMAGE": "registry.example/alerts@sha256:" + "e" * 64,
    }
    # This fragment's optional dependencies normally come from other blueprint
    # includes. Define inactive placeholders to validate the real alert image
    # interpolation without starting or configuring those unrelated services.
    optional = tmp_path / "optional.yml"
    optional.write_text(
        yaml.safe_dump(
            {
                "services": {
                    name: {
                        "image": "example/inactive:test",
                        "profiles": ["inactive-test"],
                    }
                    for name in (
                        "nvstreamer-alerts",
                        "cosmos3-reasoner",
                        "cosmos3-reasoner-shared-gpu",
                        "rtvi-vlm",
                    )
                }
            }
        )
    )
    services = compose(
        [
            ROOT / "deploy/docker/services/infra/compose.yml",
            ROOT / "deploy/docker/services/alert/compose.yml",
            optional,
        ],
        env,
    )
    assert services["alert-bridge"]["image"] == env["PERF_ALERT_IMAGE"]
    env.update(
        {
            "COMPOSE_PROFILES": "alerts,alerts-verification-latency-prometheus",
            "CI_VSS_OSS_DIR": str(tmp_path),
            "EVAL_VIDEOS_PATH": str(tmp_path / "videos"),
            "EVAL_RESULT_DIR_PATH": str(tmp_path / "results"),
            "BLUEPRINTS_DIR": str(ROOT),
            "EVAL_TYPE": "latency",
            "BLUEPRINT": "alerts",
            "DOCKER_UID": "1000",
            "DOCKER_GID": "1000",
        }
    )
    services = compose(
        [ROOT / "services/alert/perf/setup/native-eval/compose.yml"], env
    )
    assert "alert-bridge-evaluator" not in services
    assert "eval-orchestrator" not in services
    assert services["prometheus"]["image"] == config["images"]["PERF_PROMETHEUS_IMAGE"]


@pytest.mark.parametrize("service", ["behavior-analytics", "video-analytics-api"])
def test_analytics_original_compose_stacks_use_configured_dependencies(
    service, tmp_path
):
    root = ROOT / "services/analytics" / service
    config = load(config_path("analytics/" + service))
    env = {
        **infra_environment(tmp_path),
        **environment(config, {}),
        "INFRA_DIR": str(ROOT / "deploy/docker/services/infra"),
        "BEHAVIOR_ANALYTICS_IMAGE": "registry.example/ba:target",
        "VIDEO_ANALYTICS_API_IMAGE": "registry.example/api:target",
    }
    if service == "behavior-analytics":
        integration = root / "tests/integration/docker_compose"
        env.update(
            {
                "MDX_SAMPLE_APPS_DIR": str(integration),
                "MDX_DATA_DIR": str(integration / "apps_data"),
                "APP_NAME": "warehouse",
                "APP_MODE": "_2d",
                "STREAMING_SERVICE": "kafka",
            }
        )
        files = [integration / "infra/compose.yml", integration / "apps/mdx-apps.yml"]
        target, image = "analytics-stream", env["BEHAVIOR_ANALYTICS_IMAGE"]
    else:
        integration = root / "test/integration-test/docker_compose"
        env.update(
            {
                "COMPOSE_PROFILES": "elasticsearch,elasticsearch-init-container",
                "ES_CONTAINER": "test-es",
                "ES_INIT_CONTAINER": "test-es-init",
                "APP_CONTAINER": "test-api",
            }
        )
        files = [
            ROOT / "deploy/docker/services/infra/compose.yml",
            integration / "infra/video-analytics-api-infra.yml",
            integration / "apps/video-analytics-api-app.yml",
            root / "perf/setup/compose-names.yml",
            root / "perf/setup/compose-es.yml",
        ]
        target, image = "video-analytics-api", env["VIDEO_ANALYTICS_API_IMAGE"]
    services = compose(files, env)
    assert services[target]["image"] == image
    assert (
        services["elasticsearch"]["build"]["args"]["PERF_ELASTICSEARCH_BASE_IMAGE"]
        == config["images"]["PERF_ELASTICSEARCH_BASE_IMAGE"]
    )
