#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

# Resolve the actual stock includes with dummy inputs. Docker Compose config
# neither starts containers nor downloads weights. Keep its resolved env private.
python3 - <<'PY'
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory


def resolve(env_dir, data_dir, config_files=()):
    args = ["docker", "compose", "-f", "compose.yml"]
    for config in config_files:
        args.extend(("-f", f"{env_dir}/{config}"))
    args.extend((
        "--env-file", "containers.env",
        "--env-file", f"{env_dir}/.env",
        "--env-file", f"{env_dir}/overrides.env",
        "config", "--format", "json",
    ))
    env = {name: os.environ[name] for name in ("PATH", "HOME", "DOCKER_CONFIG") if name in os.environ}
    env.update(
        VSS_APPS_DIR=str(Path.cwd()),
        VSS_DATA_DIR=data_dir,
        D1_FLOWING_RTSP_URL="rtsp://fixture.invalid/flowing",
        D1_QUEUED_RTSP_URL="rtsp://fixture.invalid/queued",
        D1_FLOWING_MP4=str(Path(data_dir) / "flowing.mp4"),
        D1_QUEUED_MP4=str(Path(data_dir) / "queued.mp4"),
    )
    result = subprocess.run(args, env=env, text=True, capture_output=True, check=False)
    if result.returncode:
        raise SystemExit(result.stderr)
    return json.loads(result.stdout)


example = "examples/decision-model-alerts"
example_path = Path.cwd() / example
repo_root = Path.cwd().parents[1]

with TemporaryDirectory(prefix="vss-d1-compose-test-") as data_dir:
    d1 = resolve(example, data_dir, ("compose.yml",))
    fixtures = resolve(example, data_dir, ("compose.yml", "fixtures.compose.yml"))
    no_overlay = resolve(example, data_dir)
    assert not any(name.startswith("d1-") for name in no_overlay["services"]), (
        "The example env files activated D1 without the example Compose overlay"
    )
    for profile in ("base", "lvs", "alerts", "search"):
        stock = resolve(f"developer-profiles/dev-profile-{profile}", data_dir)
        assert d1["name"] != stock["name"], "The demo must not reuse the stock project"
        assert not any(name.startswith("d1-") for name in stock["services"]), f"{profile} activated D1"

services = d1["services"]
assert {"rtvi-vlm", "elasticsearch", "kafka", "vst-ingress", "vss-haproxy-ingress"} <= services.keys()
assert {
    "d1-model", "d1-ingest", "d1-analytics", "d1-indexer", "d1-topics", "d1-bootstrap", "d1-ui"
} <= services.keys()
assert not any(name.startswith("d1-fixture-") for name in services), "RTSP mode activated fixture services"
for name in ("d1-model", "d1-ingest"):
    build = services[name]["build"]
    assert Path(build["context"]) == repo_root, f"{name} build context misses the repository root"
    assert (repo_root / build["dockerfile"]).is_file(), f"{name} Dockerfile is missing"
ui = services["d1-ui"]
assert Path(ui["build"]["context"]) == example_path / "ui", "The UI must build from the example only"
assert (example_path / "ui/Dockerfile").is_file(), "The standalone UI Dockerfile is missing"
assert not (example_path / "ui/home-integration.patch").exists(), "The example must not patch the core UI"
assert not any(key.startswith("NEXT_PUBLIC_ENABLE_") for key in ui["environment"]), (
    "The standalone UI must not rely on core tab flags"
)
bootstrap_mount = services["d1-bootstrap"]["volumes"][0]
assert Path(bootstrap_mount["source"]) == example_path / "bootstrap.py"
assert bootstrap_mount["target"] == "/profile/bootstrap.py"
assert bootstrap_mount["read_only"]
assert not {"vss-ui", "vss-agent", "rtvi-cv"} & services.keys(), "The demo activated unrelated workloads"
assert (
    d1["networks"]["default"]["driver_opts"]["com.docker.network.bridge.host_binding_ipv4"] == "127.0.0.1"
), "Published stock dependencies need a loopback default"
for service in services.values():
    assert service.get("network_mode") != "host", "A host-networked service bypasses the loopback network"
    for port in service.get("ports", ()):
        assert port.get("host_ip") in (None, "127.0.0.1", "::1"), "Unexpected public host binding"

ingress = services["vss-haproxy-ingress"]["environment"]
assert ingress["VSS_UI_SERVICE_HOST"] == "d1-ui", "Ingress misses the custom UI"
assert services["d1-ui"]["environment"]["D1_ANALYTICS_URL"] == "http://d1-analytics:8021"
assert services["d1-bootstrap"]["depends_on"]["streamprocessing-ms"]["condition"] == "service_healthy"
assert services["d1-ui"]["depends_on"]["d1-bootstrap"]["condition"] == "service_completed_successfully"
assert "/v1/health/live" in " ".join(services["d1-ingest"]["healthcheck"]["test"]), "Bootstrap needs ingest before its first frame"

fixture_services = fixtures["services"]
media_config = fixture_services["d1-fixture-rtsp"]["volumes"][0]
assert Path(media_config["source"]) == example_path / "sample-publisher/mediamtx.yml"
assert media_config["read_only"]
for label in ("flowing", "queued"):
    publisher = f"d1-fixture-{label}"
    expected_url = f"rtsp://d1-fixture-rtsp:8554/{label}"
    assert Path(fixture_services[publisher]["build"]["context"]) == example_path / "sample-publisher"
    assert not fixture_services[publisher].get("ports"), "Fixture publishers must stay on the private network"
    assert fixture_services[publisher]["volumes"][0]["read_only"]
    assert fixture_services["d1-bootstrap"]["depends_on"][publisher]["condition"] == "service_healthy"
    for consumer in ("d1-analytics", "d1-bootstrap"):
        env = fixture_services[consumer]["environment"]
        assert env[f"D1_{label.upper()}_RTSP_URL"] == expected_url
        assert env[f"D1_{label.upper()}_KIND"] == "recorded"

print("D1 RTSP and MP4 examples resolve, and the stock profiles leave D1 disabled.")
PY
