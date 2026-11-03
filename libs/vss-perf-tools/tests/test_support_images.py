# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "libs/vss-perf-tools"))
from support_images import apply_bindings, environment, load, prepare_infra, render  # noqa: E402

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


def source_root(service, scope):
    if scope == "deployment":
        return ROOT / "deploy/docker"
    if scope == "infra":
        return ROOT / "deploy/docker/services/infra"
    if scope == "vios":
        return ROOT / "services/vios/deployment/stream-processing/docker-compose"
    root = config_path(service).parents[1]
    return root / "compose" if scope == "compose" else root


@pytest.mark.parametrize("service", SERVICES)
def test_all_declared_bindings_render_and_leave_originals_untouched(service, tmp_path):
    config = load(config_path(service))
    for scope, files in config["scopes"].items():
        source = source_root(service, scope)
        fixture = tmp_path / scope / "source"
        originals = {}
        # Copy only bound source files: no runtime state or unrelated datasets.
        for relative in files:
            path = fixture / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            originals[relative] = (source / relative).read_text()
            path.write_text(originals[relative])
        destination = tmp_path / scope / "rendered"
        render(config, scope, fixture, destination)
        for relative, fields in files.items():
            assert (source / relative).read_text() == originals[relative]
            text = (destination / relative).read_text()
            for image_id in fields.get("services", {}).values():
                assert config["images"][image_id]["ref"] in text
            bases = re.findall(r"(?m)^FROM\s+(\S+)", text)
            for index, image_id in fields.get("build_bases", {}).items():
                assert bases[int(index)] == config["images"][image_id]["ref"]


def test_image_edit_changes_deployment_but_preserves_tested_image_and_mounts(tmp_path):
    config = load(config_path("rtvi/rt-vlm"))
    key = config["scopes"]["compose"]["compose.perf.yaml"]["services"]["prometheus"]
    config["images"][key]["ref"] = "registry.example/prometheus@sha256:" + "a" * 64
    source = source_root("rtvi/rt-vlm", "compose")
    destination = tmp_path / "rendered"
    render(config, "compose", source, destination)
    text = (destination / "compose.perf.yaml").read_text()
    assert config["images"][key]["ref"] in text
    assert "${RTVI_IMAGE:-" in text
    original = (source / "compose.perf.yaml").read_text()
    assert re.findall(r"(?m)^      - .*", text) == re.findall(
        r"(?m)^      - .*", original
    )


def test_private_dockerfiles_are_used_and_credentials_not_copied(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(ROOT / "deploy/docker/services/infra", source)
    (source / ".env").write_text("SECRET=do-not-copy")
    output = tmp_path / "output"
    output.mkdir()
    destination = prepare_infra(
        config_path("analytics/behavior-analytics"), source, output
    )
    assert not (destination / ".env").exists()
    assert f"context: {destination}" in (destination / "compose.yml").read_text()
    assert "support-images.json" in [p.name for p in output.iterdir()]


def test_missing_bindings_and_symlinks_fail(tmp_path):
    config = {
        "images": {"helper": {"ref": "alpine:3.24.1"}},
        "scopes": {"compose": {"compose.yml": {"services": {"helper": "helper"}}}},
    }
    source = tmp_path / "source"
    source.mkdir()
    file = source / "compose.yml"
    file.write_text("services:\n  unexpected:\n    image: alpine:old\n")
    with pytest.raises(ValueError, match="Expected one image"):
        apply_bindings(config, "compose", source)
    file.unlink()
    outside = tmp_path / "outside"
    outside.write_text("preserve")
    file.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        apply_bindings(config, "compose", source)
    assert outside.read_text() == "preserve"


def test_environment_and_invalid_references(tmp_path):
    config = load(config_path("rtvi/rt-vlm"))
    assert (
        environment(config)["VST_SENSOR_IMAGE"]
        == "nvcr.io/nvidia/vss-core/vss-vios-sensor:3.2.0"
    )
    for value in [
        " image:bad",
        "repo/image:tag\nexport SECRET=bad",
        "$(touch /tmp/bad)",
    ]:
        config["images"]["bad"] = {"ref": value}
        path = tmp_path / "images.yaml"
        path.write_text(yaml.safe_dump(config))
        with pytest.raises(ValueError, match="Invalid image reference"):
            load(path)


def test_shell_interface_preserves_explicit_legacy_override():
    import os

    env = dict(os.environ, VST_SENSOR_IMAGE="registry.example/custom-sensor:tag")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "libs/vss-perf-tools/support_images.py"),
            "--config",
            str(config_path("rtvi/rt-vlm")),
            "--shell",
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "export VST_SENSOR_IMAGE=registry.example/custom-sensor:tag" in result.stdout


def test_shared_tools_are_available_in_every_sparse_checkout():
    for service in SERVICES:
        path = ROOT / "services" / service / "perf/configs/perf-configs.yaml"
        for row in yaml.safe_load(path.read_text())["perf_configs"]:
            assert "libs/vss-perf-tools/" in row["checkoutPaths"]


def test_renderer_rejects_source_or_nested_destination(tmp_path):
    config = load(config_path("rtvi/rt-vlm"))
    for destination in (tmp_path, tmp_path / "nested"):
        with pytest.raises(ValueError, match="private destination"):
            render(config, "compose", tmp_path, destination)
    assert list(tmp_path.iterdir()) == []
