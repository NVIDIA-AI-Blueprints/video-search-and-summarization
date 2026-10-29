# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import subprocess
from pathlib import Path


PERF_ROOT = Path(__file__).resolve().parents[1]
COMMON = PERF_ROOT / "scripts" / "common.sh"
PRE_FLIGHT = PERF_ROOT / "scripts" / "pre_flight.sh"


def test_common_exports_required_lifecycle_helpers(tmp_path: Path) -> None:
    required = (
        "ensure_host_dir",
        "prepare_benchmark_dir",
        "remove_root_tree",
        "resolve_repo_root",
        "resolve_apps_dir",
        "resolve_image",
        "write_compose_env",
        "load_compose_env",
        "compose",
        "wait_for_elasticsearch",
        "dump_es_debug",
        "va_api_compose",
        "va_api_wait_for_elasticsearch",
        "va_api_load_elasticsearch_dumps",
        "cleanup_docker_environment",
    )
    env = os.environ.copy()
    env.update(
        {
            "COMMON": str(COMMON),
            "TEST_DIR": str(tmp_path),
            "REQUIRED_HELPERS": " ".join(required),
        }
    )
    script = """
        source "$COMMON"
        for helper in $REQUIRED_HELPERS; do
            declare -F "$helper" >/dev/null
        done
        ensure_host_dir "$TEST_DIR/nested/path"
        test -d "$TEST_DIR/nested/path"
        ln -s "$TEST_DIR/nested/path" "$TEST_DIR/link"
        if ensure_host_dir "$TEST_DIR/link"; then
            echo "ensure_host_dir accepted a symlink" >&2
            exit 1
        fi
    """
    subprocess.run(["bash", "-c", script], env=env, check=True)


def test_pre_flight_runs_with_mocked_docker(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n")
    docker.chmod(0o755)
    output = tmp_path / "results"
    env = os.environ.copy()
    env.update(
        {
            "ARTIFACT_REF": "nvcr.io/example/video-analytics-api:test",
            "OUTPUT_DIR": str(output),
            "PATH": os.pathsep.join((str(fake_bin), env["PATH"])),
        }
    )
    subprocess.run(["bash", str(PRE_FLIGHT)], env=env, check=True)
    assert output.is_dir()
