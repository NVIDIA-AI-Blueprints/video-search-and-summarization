# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for agent/orchestrator/storage.py."""

from pathlib import Path

import pytest

from vss_agents.orchestrator.storage import ensure_alerts_engine_directories
from vss_agents.orchestrator.storage import ensure_permissions
from vss_agents.orchestrator.storage import resolve_config_path


def test_resolve_config_path_expands_tilde(monkeypatch, tmp_path: Path):
    fake_home = tmp_path / "home" / "user"
    fake_home.mkdir(parents=True)
    repo = fake_home / "video-search-and-summarization" / "deploy" / "docker"
    repo.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    assert resolve_config_path("~/video-search-and-summarization/deploy/docker") == repo.resolve()


def test_ensure_permissions_skips_broken_symlink_when_best_effort(tmp_path: Path):
    data_log = tmp_path / "data_log" / "vst" / "temp_files"
    data_log.mkdir(parents=True)
    dangling = data_log / "stale_clip.mp4"
    dangling.symlink_to("/home/vst/vst_release/streamer_videos/missing.mp4")
    regular = data_log / "ok.txt"
    regular.write_text("ok")
    regular.chmod(0o644)

    touched = ensure_permissions(tmp_path, relative_roots=("data_log",), best_effort=True)

    assert dangling.is_symlink() and not dangling.exists()
    assert regular in touched
    assert dangling not in touched


def test_ensure_permissions_raises_for_broken_symlink_when_not_best_effort(tmp_path: Path):
    data_log = tmp_path / "data_log" / "vst" / "temp_files"
    data_log.mkdir(parents=True)
    dangling = data_log / "stale_clip.mp4"
    dangling.symlink_to("/home/vst/vst_release/streamer_videos/missing.mp4")

    with pytest.raises(RuntimeError, match=r"Broken symlink under data directory.*stale_clip\.mp4"):
        ensure_permissions(tmp_path, relative_roots=("data_log",), best_effort=False)


def test_ensure_alerts_engine_directories_creates_writable_engine_dirs(tmp_path: Path):
    deployments_dir = tmp_path / "deploy" / "docker"

    created_paths = ensure_alerts_engine_directories(deployments_dir)

    assert created_paths == [
        deployments_dir / "engines" / "gdino",
        deployments_dir / "engines" / "rtdetr-its",
    ]
    assert (deployments_dir / "engines").stat().st_mode & 0o777 == 0o777
    assert (deployments_dir / "engines" / "gdino").stat().st_mode & 0o777 == 0o777
    assert (deployments_dir / "engines" / "rtdetr-its").stat().st_mode & 0o777 == 0o777


def test_ensure_alerts_engine_directories_repairs_existing_engine_permissions(tmp_path: Path):
    deployments_dir = tmp_path / "deploy" / "docker"
    gdino_dir = deployments_dir / "engines" / "gdino"
    rtdetr_dir = deployments_dir / "engines" / "rtdetr-its"
    gdino_dir.mkdir(parents=True)
    rtdetr_dir.mkdir(parents=True)
    (gdino_dir / "existing.plan").write_text("engine")
    (deployments_dir / "engines").chmod(0o755)
    gdino_dir.chmod(0o755)
    rtdetr_dir.chmod(0o755)

    ensure_alerts_engine_directories(deployments_dir)

    assert (deployments_dir / "engines").stat().st_mode & 0o777 == 0o777
    assert gdino_dir.stat().st_mode & 0o777 == 0o777
    assert rtdetr_dir.stat().st_mode & 0o777 == 0o777
    assert (gdino_dir / "existing.plan").read_text() == "engine"
