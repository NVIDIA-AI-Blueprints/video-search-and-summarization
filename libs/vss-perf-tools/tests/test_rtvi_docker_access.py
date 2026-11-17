# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise the actual RTVI prerequisites with isolated Docker/PATH stand-ins."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("service", ["rt-vlm", "rt-embed"])
@pytest.mark.parametrize("groups,mode,expected_error", [
    ("metroadmin users", "ok", None),
    ("metroadmin docker", "ok", None),
    ("metroadmin users", "denied", "permission denied connecting to Docker"),
    ("metroadmin docker", "denied", "permission denied connecting to Docker"),
    ("metroadmin docker", "unavailable", "Cannot connect to the Docker daemon"),
    ("metroadmin users", "no-runtime", "nvidia Docker runtime required"),
    ("metroadmin users", "no-compose", "Docker Compose v2 is required"),
])
def test_setup_checks_capability_not_group_name(service, groups, mode, expected_error, tmp_path):
    source = (ROOT / "services/rtvi" / service / "perf/scripts/native_setup_perf_env.sh").read_text()
    compose_check = next(line for line in source.splitlines() if line.startswith("docker compose version "))
    start = source.index("# Check Docker access through the current PATH.")
    last_line = 'log "  ${_C_GREEN}nvidia Docker runtime is configured.${_C_RESET}"'
    end = source.index(last_line, start) + len(last_line)
    checks = compose_check + "\n" + source[start:end]

    binary = tmp_path / "bin"
    binary.mkdir()
    # This executable represents either the host Docker CLI or Jenkins' guarded
    # wrapper. It must stay first on PATH; setup must not add its own sudo prefix.
    tools = {
        "id": '#!/bin/sh\nprintf "%s\\n" "$TEST_GROUP_LIST"\n',
        "sudo": '#!/bin/sh\necho "unexpected service-specific sudo" >&2\nexit 97\n',
        "docker": '''#!/bin/bash
set -eu
printf '%s\n' "$*" >> "$TEST_DOCKER_CALLS"
if [[ "$1 ${2:-}" == "compose version" ]]; then
    [[ "$TEST_DOCKER_MODE" != no-compose ]] || exit 1
elif [[ "$1" == info ]]; then
    case "$TEST_DOCKER_MODE" in
        denied) echo 'permission denied connecting to Docker' >&2; exit 1 ;;
        unavailable) echo 'Cannot connect to the Docker daemon' >&2; exit 1 ;;
    esac
    if [[ "$#" -gt 1 ]]; then
        if [[ "$TEST_DOCKER_MODE" == no-runtime ]]; then echo runc; else echo 'nvidia runc'; fi
    fi
else
    exit 98
fi
''',
    }
    for name, text in tools.items():
        executable = binary / name
        executable.write_text(text)
        executable.chmod(0o755)
    calls = tmp_path / "docker-calls"
    script = '''set -euo pipefail
_C_RED='' _C_RESET='' _C_YELLOW='' _C_GREEN=''
die() { printf '%s\n' "$*" >&2; exit 1; }
log() { printf '%s\n' "$*"; }
''' + checks
    result = subprocess.run(["bash", "-c", script], text=True, capture_output=True, timeout=10,
                            env=dict(os.environ, PATH=str(binary) + ":/usr/bin:/bin",
                                     TEST_GROUP_LIST=groups, TEST_DOCKER_MODE=mode,
                                     TEST_DOCKER_CALLS=str(calls)))
    if expected_error:
        assert result.returncode != 0
        assert expected_error in result.stderr
        if mode in {"denied", "unavailable"}:
            assert "Docker daemon is not accessible from this execution context" in result.stderr
            assert calls.read_text().splitlines() == ["compose version", "info"]
    else:
        assert result.returncode == 0, result.stderr
        assert "Docker daemon is accessible" in result.stdout
        assert "nvidia Docker runtime is configured" in result.stdout
        assert calls.read_text().splitlines()[1] == "info"
    assert "unexpected service-specific sudo" not in result.stderr
