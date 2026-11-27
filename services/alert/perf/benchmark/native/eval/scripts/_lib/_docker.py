"""Shared Docker helpers for eval scripts.

Provides low-level wrappers around ``docker ps``, ``docker inspect``,
and ``docker logs`` used by :mod:`check_health`, :mod:`collect_logs`,
and other modules that need to query container state.
"""

from __future__ import annotations

import json
import subprocess


def docker_ps_json(
    *,
    project: str | None = None,
    all_states: bool = False,
) -> list[dict]:
    """Return ``docker ps`` output as a list of dicts.

    When *all_states* is True, includes stopped containers (``-a``).
    When *project* is given, filters by Docker Compose project label.
    """
    cmd = ["docker", "ps", "--no-trunc", "--format", "{{json .}}"]
    if all_states:
        cmd.insert(2, "-a")
    if project:
        cmd.extend(["--filter", f"label=com.docker.compose.project={project}"])
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    containers: list[dict] = []
    for line in result.stdout.strip().splitlines():
        if line:
            containers.append(json.loads(line))
    return containers


def inspect_health(container_id: str) -> str | None:
    """Return the health status string for a container, or *None* if no healthcheck."""
    result = subprocess.run(
        [
            "docker", "inspect", "--format",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
            container_id,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    status = result.stdout.strip()
    return None if status == "none" else status


def inspect_exit_code(container_id: str) -> int:
    """Return the exit code of a container."""
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.ExitCode}}", container_id],
        capture_output=True,
        text=True,
        check=True,
    )
    try:
        return int(result.stdout.strip())
    except ValueError:
        return -1


def container_logs(container_id: str, tail: int = 50000) -> str:
    """Fetch the last *tail* lines of stdout+stderr from a container."""
    result = subprocess.run(
        ["docker", "logs", "--tail", str(tail), "--timestamps", container_id],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout + result.stderr
