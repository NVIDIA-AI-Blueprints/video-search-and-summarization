# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Safety and command-order checks; no Docker daemon or deployment required."""

import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "evaluation_reset", Path(__file__).parents[1] / "scripts/evaluation_reset.py"
)
reset = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reset)


def config(root):
    return {
        "services": {
            "elasticsearch": {"container_name": "elasticsearch"},
            "kafka": {},
            "redis": {},
            "elasticsearch-init-container": {
                "environment": {
                    "ELASTICSEARCH_URL": "http://elasticsearch:9200",
                    "ELASTICSEARCH_ILM_MIN_AGE": "336h",
                }
            },
        },
        "volumes": {
            "elastic-data": {
                "name": "vss-eval-test_elastic-data",
                "driver_opts": {
                    "type": "none",
                    "o": "bind",
                    "device": str(root / "data_log/elastic/data"),
                },
            }
        },
    }


def test_marker_refuses_takeover_and_symlinks(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    (old / "database").write_text("live")
    with pytest.raises(ValueError, match="empty"):
        reset.owned_directory(old, "vss-eval-test", True)
    link = tmp_path / "link"
    link.symlink_to(old)
    with pytest.raises(ValueError, match="symlink"):
        reset.owned_directory(link, "vss-eval-test", True)
    with pytest.raises(ValueError, match="dedicated"):
        reset.owned_directory(tmp_path / "new", "vss", True)


def test_marker_project_cannot_be_reassigned(tmp_path):
    root = reset.owned_directory(tmp_path / "owned", "vss-eval-test", True)
    assert reset.owned_directory(root, "vss-eval-test") == root
    with pytest.raises(ValueError, match="does not match"):
        reset.owned_directory(root, "vss-eval-other")


def test_rejects_external_state_and_short_retention(tmp_path):
    cfg = config(tmp_path)
    reset.validate_config(cfg, tmp_path, 336)
    with pytest.raises(ValueError, match="retention"):
        reset.validate_config(cfg, tmp_path, 72)
    cfg["volumes"]["elastic-data"]["external"] = True
    with pytest.raises(ValueError, match="External"):
        reset.validate_config(cfg, tmp_path, 336)
    del cfg["volumes"]["elastic-data"]["external"]
    cfg["volumes"]["elastic-data"]["driver_opts"]["device"] = "/production/es"
    with pytest.raises(ValueError, match="outside"):
        reset.validate_config(cfg, tmp_path, 336)


def test_rejects_shared_containers_and_unowned_volumes(tmp_path):
    cfg = config(tmp_path)
    for container in (
        {"Name": "/elasticsearch"},
        {"Mounts": [{"Source": str(tmp_path / "data_log")}]},
    ):
        with pytest.raises(ValueError, match="shares"):
            reset.validate_resources(cfg, [container], [], "vss-eval-test", tmp_path)
    with pytest.raises(ValueError, match="not owned"):
        reset.validate_resources(
            cfg, [], [{"Name": "vss-eval-test_elastic-data"}], "vss-eval-test", tmp_path
        )


def run_reset(root, response=None, remaining=""):
    calls = []

    def run(command):
        calls.append(command)
        if command[:3] == ["docker", "ps", "-aq"]:
            return remaining
        if "--request PUT" in command[-1]:
            index = command[-1].split("/")[-1].rstrip(chr(34))
            return json.dumps(
                {"acknowledged": True, "shards_acknowledged": True, "index": index}
            )
        if "curl --fail" in command[-1]:
            return json.dumps(
                response
                or {"count": 0, "_shards": {"total": 0, "successful": 0, "failed": 0}}
            )
        return ""

    result = reset.execute_reset(
        ["docker", "compose"], root, "vss-eval-test", config(root), run
    )
    return calls, result


def test_reset_stops_before_removing_state_and_preserves_inputs(tmp_path):
    root = reset.owned_directory(tmp_path / "owned", "vss-eval-test", True)
    for name in ("data_log", "agent_eval", "models", "videos"):
        (root / name).mkdir()
        (root / name / "sentinel").write_text("original")
    calls, result = run_reset(root)
    assert calls[0][2:4] == ["build", "elasticsearch-init-container"]
    assert calls[1][2:4] == ["down", "--volumes"]
    assert not (root / "data_log/sentinel").exists()
    assert not (root / "agent_eval/sentinel").exists()
    assert (root / "models/sentinel").read_text() == "original"
    assert (root / "videos/sentinel").read_text() == "original"
    assert result["count"] == 0
    count_positions = [i for i, call in enumerate(calls) if "_count?" in call[-1]]
    full_start = next(i for i, call in enumerate(calls) if call[-1] == "900")
    assert count_positions[0] < full_start < count_positions[1]


def test_remaining_container_prevents_deletion(tmp_path):
    (tmp_path / "data_log").mkdir()
    sentinel = tmp_path / "data_log/sentinel"
    sentinel.write_text("keep")
    with pytest.raises(ValueError, match="remain"):
        run_reset(tmp_path, remaining="container-id")
    assert sentinel.read_text() == "keep"


def test_nonempty_or_partial_count_prevents_startup(tmp_path):
    for response in ({"count": 1}, {"count": 0, "_shards": {"failed": 1}}):
        with pytest.raises(ValueError, match="remain or count failed"):
            run_reset(tmp_path, response=response)


@pytest.mark.parametrize(
    "result",
    [
        None,
        {"count": False},
        {"count": 0},
        {"count": 0, "_shards": {"total": 1, "successful": 0, "failed": 0}},
    ],
)
def test_count_requires_complete_typed_response(result):
    with pytest.raises(ValueError):
        reset.validate_empty_count(result)


def test_state_under_preserved_assets_is_rejected(tmp_path):
    cfg = config(tmp_path)
    cfg["services"]["redis"]["volumes"] = [
        {"type": "bind", "source": str(tmp_path / "videos"), "target": "/data"}
    ]
    with pytest.raises(ValueError, match="runtime directory"):
        reset.validate_config(cfg, tmp_path, 336)


def test_after_start_failure_stops_project(tmp_path):
    calls = []
    counts = iter(
        [
            {"count": 0, "_shards": {"total": 0, "successful": 0, "failed": 0}},
            {"count": 4},
        ]
    )

    def run(command):
        calls.append(command)
        if "--request PUT" in command[-1]:
            index = command[-1].split("/")[-1].rstrip(chr(34))
            return json.dumps(
                {"acknowledged": True, "shards_acknowledged": True, "index": index}
            )
        if "curl --fail" in command[-1]:
            return json.dumps(next(counts))
        return ""

    with pytest.raises(ValueError):
        reset.execute_reset(
            ["docker", "compose"], tmp_path, "vss-eval-test", config(tmp_path), run
        )
    assert calls[-1][2:] == ["stop", "--timeout", "60"]


def test_real_search_compose_config_is_accepted(tmp_path):
    """Render only: catches drift in the shipped profile without a Docker daemon."""
    import os
    import shutil
    import subprocess

    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI unavailable")
    version = subprocess.run(
        ["docker", "compose", "version"], check=False, capture_output=True, timeout=10
    )
    if version.returncode:
        pytest.skip("Docker Compose plugin unavailable")
    deploy = Path(__file__).resolve().parents[1]
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "VSS_APPS_DIR": str(deploy),
        "VSS_DATA_DIR": str(tmp_path),
        "HOST_IP": "127.0.0.1",
        "ELASTICSEARCH_ILM_MIN_AGE": "336h",
        "VSS_EVAL_REVISION": "test",
        "VSS_EVAL_REPO_ROOT": str(deploy.parents[1]),
    }
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "compose.yml",
            "-f",
            "developer-profiles/dev-profile-search/compose.eval.yml",
            "--env-file",
            "containers.env",
            "--env-file",
            "developer-profiles/dev-profile-search/.env",
            "--env-file",
            "developer-profiles/dev-profile-search/overrides.env",
            "config",
            "--format",
            "json",
        ],
        check=False,
        cwd=deploy,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    rendered = json.loads(result.stdout)
    reset.validate_config(rendered, tmp_path, 336)
    assert "streamprocessing-ms" in rendered["services"]
    assert "vss-search-analytics-2d-fusion" in rendered["services"]


def test_anchor_creation_requires_acknowledged_index(tmp_path):
    calls = []

    def run(command):
        calls.append(command)
        if "--request PUT" in command[-1]:
            return json.dumps({"acknowledged": True, "shards_acknowledged": False})
        return ""

    with pytest.raises(ValueError, match="anchor index"):
        reset.execute_reset(
            ["docker", "compose"], tmp_path, "vss-eval-test", config(tmp_path), run
        )
    assert not any(call[-1] == "900" for call in calls)


def test_foreign_local_bind_volume_alias_is_rejected(tmp_path):
    foreign = {
        "Name": "production-alias",
        "Options": {"device": str(tmp_path / "data_log")},
        "Labels": {"com.docker.compose.project": "production"},
    }
    with pytest.raises(ValueError, match="Foreign volume aliases"):
        reset.validate_resources(
            config(tmp_path), [], [foreign], "vss-eval-test", tmp_path
        )


def test_foreign_container_parent_mount_is_rejected(tmp_path):
    foreign = {"Name": "/other", "Mounts": [{"Source": str(tmp_path.parent)}]}
    with pytest.raises(ValueError, match="shares"):
        reset.validate_resources(
            config(tmp_path), [foreign], [], "vss-eval-test", tmp_path
        )


def test_writable_media_storage_must_be_deleted_state(tmp_path):
    cfg = config(tmp_path)
    media = {
        "type": "bind",
        "source": str(tmp_path / "videos"),
        "target": "/home/vst/vst_release/streamer_videos",
    }
    cfg["services"]["media"] = {"volumes": [media]}
    with pytest.raises(ValueError, match="Media storage"):
        reset.validate_config(cfg, tmp_path, 336)
    media["read_only"] = True
    reset.validate_config(cfg, tmp_path, 336)


def test_own_orphan_volume_mounted_by_foreign_container_is_shared(tmp_path):
    cfg = config(tmp_path)
    alias = {
        "Name": "old-eval-alias",
        "Labels": {"com.docker.compose.project": "vss-eval-test"},
        "Options": {"device": str(tmp_path / "data_log/es")},
    }
    foreign = {
        "Name": "/foreign",
        "Config": {"Labels": {"com.docker.compose.project": "other"}},
        "Mounts": [
            {
                "Name": "old-eval-alias",
                "Source": "/var/lib/docker/volumes/old-eval-alias/_data",
            }
        ],
    }
    with pytest.raises(ValueError, match="shares evaluation"):
        reset.validate_resources(cfg, [foreign], [alias], "vss-eval-test", tmp_path)
