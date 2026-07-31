#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Collect a credential-free PostgreSQL health snapshot for CI diagnosis."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

CONTAINER_NAME = "vss-vios-postgres"
OUTPUT_NAME = "postgres-health-diagnostic.json"
COMMAND_TIMEOUT_SECONDS = 8
REFERENCE_IMAGE_ID = (
    "sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
)
EXPECTED_HEALTH_COMMANDS = {
    (
        'pg_isready -h /var/run/postgresql -U "${POSTGRES_USER}" '
        '-d "${POSTGRES_DB}" >/dev/null 2>&1'
    ),
    (
        'pg_isready -h /var/run/postgresql -U "$${POSTGRES_USER}" '
        '-d "$${POSTGRES_DB}" >/dev/null 2>&1'
    ),
}
PROBE_KEYS = {
    "pg_isready_present",
    "pg_isready_version",
    "socket_dir_present",
    "socket_dir_readable",
    "socket_dir_searchable",
    "socket_present",
    "exact_socket_probe",
    "minimal_socket_probe",
    "tcp_probe",
}


def _run(
    command: list[str],
    *,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
        timeout=COMMAND_TIMEOUT_SECONDS,
    )


def _probe(command: list[str]) -> dict[str, Any]:
    try:
        result = _run(command, capture_output=False)
    except subprocess.TimeoutExpired:
        return {"completed": False, "timed_out": True}
    except (FileNotFoundError, OSError):
        return {"completed": False, "start_failed": True}
    return {
        "completed": True,
        "timed_out": False,
        "exit_code": result.returncode,
    }


def _parse_probe_lines(raw: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in raw.splitlines():
        key, separator, value = line.partition("=")
        if (
            separator
            and key in PROBE_KEYS
            and value.isdigit()
            and 0 <= int(value) <= 255
        ):
            values[key] = int(value)
    return values


def _write_report(output_path: Path, report: dict[str, Any]) -> None:
    output_path.write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )


def _fixed_status(value: object, allowed: set[str]) -> str:
    return value if isinstance(value, str) and value in allowed else "other"


def collect_postgres_health_diagnostic(log_dir: Path) -> dict[str, Any]:
    """Write only fixed booleans, exit codes, hashes, and numeric metadata."""
    report: dict[str, Any] = {
        "schema_version": 1,
        "container": CONTAINER_NAME,
    }
    output_path = log_dir / OUTPUT_NAME

    try:
        inspect_result = _run(["docker", "inspect", CONTAINER_NAME])
    except subprocess.TimeoutExpired:
        report["inspect"] = {"completed": False, "timed_out": True}
        _write_report(output_path, report)
        return report
    except (FileNotFoundError, OSError):
        report["inspect"] = {"completed": False, "start_failed": True}
        _write_report(output_path, report)
        return report

    if inspect_result.returncode != 0:
        report["inspect"] = {
            "completed": True,
            "exit_code": inspect_result.returncode,
            "container_found": False,
        }
        _write_report(output_path, report)
        return report

    try:
        documents = json.loads(inspect_result.stdout)
        container = documents[0]
    except (json.JSONDecodeError, IndexError, KeyError, TypeError):
        report["inspect"] = {
            "completed": True,
            "exit_code": 0,
            "valid_json": False,
        }
        _write_report(output_path, report)
        return report

    config = container.get("Config") if isinstance(container, dict) else {}
    state = container.get("State") if isinstance(container, dict) else {}
    config = config if isinstance(config, dict) else {}
    state = state if isinstance(state, dict) else {}
    health = state.get("Health")
    health = health if isinstance(health, dict) else {}
    health_logs = health.get("Log")
    health_logs = health_logs if isinstance(health_logs, list) else []
    test = config.get("Healthcheck", {}).get("Test")
    test = test if isinstance(test, list) else []
    test_strings = [item for item in test if isinstance(item, str)]
    test_text = "\0".join(test_strings)
    env = config.get("Env")
    env = env if isinstance(env, list) else []
    env_keys = {
        item.partition("=")[0]
        for item in env
        if isinstance(item, str) and "=" in item
    }
    socket_mounts = [
        mount
        for mount in (container.get("Mounts") or [])
        if isinstance(mount, dict)
        and mount.get("Destination") == "/var/run/postgresql"
    ]
    report["inspect"] = {
        "completed": True,
        "exit_code": 0,
        "container_found": True,
        "state": _fixed_status(
            state.get("Status"),
            {"created", "running", "restarting", "exited", "paused", "dead"},
        ),
        "running": state.get("Running") is True,
        "restarting": state.get("Restarting") is True,
        "configured_user_is_root": config.get("User") in {"0", "0:0", ""},
        "matches_reference_image_id": (
            container.get("Image") == REFERENCE_IMAGE_ID
        ),
        "health": {
            "status": _fixed_status(
                health.get("Status"),
                {"none", "starting", "healthy", "unhealthy"},
            ),
            "failing_streak": (
                health.get("FailingStreak")
                if type(health.get("FailingStreak")) is int
                and 0 <= health["FailingStreak"] <= 1_000_000
                else None
            ),
            "log_count_capped": min(len(health_logs), 100),
            "exit_codes": [
                entry.get("ExitCode")
                for entry in health_logs[-5:]
                if isinstance(entry, dict)
                and type(entry.get("ExitCode")) is int
                and -255 <= entry["ExitCode"] <= 255
            ],
            "uses_cmd_shell": test[:1] == ["CMD-SHELL"],
            "matches_expected_command": (
                len(test) == 2
                and test[0] == "CMD-SHELL"
                and test[1] in EXPECTED_HEALTH_COMMANDS
            ),
            "mentions_socket_path": "/var/run/postgresql" in test_text,
            "mentions_postgres_user_variable": "POSTGRES_USER" in test_text,
            "mentions_postgres_db_variable": "POSTGRES_DB" in test_text,
        },
        "required_env_keys_present": {
            "POSTGRES_USER": "POSTGRES_USER" in env_keys,
            "POSTGRES_DB": "POSTGRES_DB" in env_keys,
        },
        "socket_mount": {
            "count": len(socket_mounts),
            "is_bind": (
                socket_mounts[0].get("Type") == "bind"
                if len(socket_mounts) == 1
                else False
            ),
            "read_write": (
                socket_mounts[0].get("RW") is True
                if len(socket_mounts) == 1
                else None
            ),
        },
    }
    _write_report(output_path, report)

    fixed_probe_script = r"""
set +e
command -v pg_isready >/dev/null 2>&1
printf 'pg_isready_present=%s\n' "$?"
pg_isready --version >/dev/null 2>&1
printf 'pg_isready_version=%s\n' "$?"
test -d /var/run/postgresql
printf 'socket_dir_present=%s\n' "$?"
test -r /var/run/postgresql
printf 'socket_dir_readable=%s\n' "$?"
test -x /var/run/postgresql
printf 'socket_dir_searchable=%s\n' "$?"
test -S /var/run/postgresql/.s.PGSQL.5432
printf 'socket_present=%s\n' "$?"
pg_isready -h /var/run/postgresql -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1
printf 'exact_socket_probe=%s\n' "$?"
pg_isready -q -h /var/run/postgresql -p 5432 -t 2
printf 'minimal_socket_probe=%s\n' "$?"
pg_isready -q -h 127.0.0.1 -p 5432 -t 2
printf 'tcp_probe=%s\n' "$?"
"""
    try:
        fixed_probe = _run(
            [
                "docker",
                "exec",
                CONTAINER_NAME,
                "sh",
                "-c",
                fixed_probe_script,
            ]
        )
        report["fixed_probes"] = {
            "completed": True,
            "wrapper_exit_code": fixed_probe.returncode,
            "exit_codes": _parse_probe_lines(fixed_probe.stdout),
        }
    except subprocess.TimeoutExpired:
        report["fixed_probes"] = {"completed": False, "timed_out": True}
    except (FileNotFoundError, OSError):
        report["fixed_probes"] = {"completed": False, "start_failed": True}
    _write_report(output_path, report)

    report["postgres_user_socket_probe"] = _probe(
        [
            "docker",
            "exec",
            "--user",
            "postgres",
            CONTAINER_NAME,
            "sh",
            "-c",
            (
                'pg_isready -q -h /var/run/postgresql -p 5432 -t 2'
            ),
        ]
    )
    _write_report(output_path, report)
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", required=True)
    options = parser.parse_args()
    collect_postgres_health_diagnostic(Path(options.log_dir))
