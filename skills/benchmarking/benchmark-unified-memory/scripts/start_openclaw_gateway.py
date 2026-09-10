#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Start the benchmark's shared OpenClaw judge Gateway and configure VSS."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path

CONTAINER_NAME = "openclaw-gateway"
IMAGE = (
    "ghcr.io/openclaw/openclaw:2026.7.1-2@"
    "sha256:8789721d2e9b24b780a1504b56deb4c6bd5c7dbf96a1dd117e7c45c2ed72c8ac"
)
GATEWAY_BASE_URL = "http://127.0.0.1:18789/v1"
REQUIRED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL")


def build_gateway_config(model: str) -> dict[str, object]:
    """Return a secret-free OpenClaw Gateway configuration."""
    if not model.strip():
        raise ValueError("ANTHROPIC_MODEL must not be empty")
    return {
        "models": {
            "providers": {
                "anthropic": {
                    "baseUrl": "${ANTHROPIC_BASE_URL}",
                    "apiKey": "${ANTHROPIC_API_KEY}",
                }
            }
        },
        "agents": {
            "defaults": {"model": {"primary": f"anthropic/{model}"}},
        },
        "gateway": {
            "mode": "local",
            "bind": "loopback",
            "port": 18789,
            "auth": {"mode": "none"},
            "http": {
                "endpoints": {"chatCompletions": {"enabled": True}},
            },
        },
    }


def require_environment(env: Mapping[str, str]) -> None:
    missing = [name for name in REQUIRED_ENV if not env.get(name)]
    if missing:
        raise RuntimeError(
            f"missing required environment variables: {', '.join(missing)}"
        )


def docker_create_command() -> list[str]:
    """Build the container command without embedding credential values."""
    return [
        "docker",
        "create",
        "--name",
        CONTAINER_NAME,
        "--network",
        "host",
        "--restart",
        "no",
        "--env",
        "ANTHROPIC_API_KEY",
        "--env",
        "ANTHROPIC_BASE_URL",
        "--env",
        "OPENCLAW_CONFIG_PATH=/tmp/openclaw.json",
        "--env",
        "OPENCLAW_STATE_DIR=/home/node/.openclaw",
        IMAGE,
    ]


def run(
    command: Sequence[str], *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True, capture_output=True)


def request_json(
    url: str,
    *,
    payload: dict[str, object] | None = None,
    timeout: float = 10.0,
) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {} if data is None else {"Content-Type": "application/json"}
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise TypeError(f"Gateway returned a non-object response from {url}")
    return value


def wait_until_ready(*, timeout: float = 120.0, interval: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            request_json(f"{GATEWAY_BASE_URL}/models")
            return
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = exc
            time.sleep(interval)
    raise RuntimeError(f"OpenClaw Gateway did not become ready: {last_error}")


def smoke_test() -> None:
    response = request_json(
        f"{GATEWAY_BASE_URL}/chat/completions",
        payload={
            "model": "openclaw/default",
            "messages": [{"role": "user", "content": "Reply exactly READY"}],
            "temperature": 0,
        },
        timeout=120.0,
    )
    try:
        content = response["choices"][0]["message"]["content"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(
            "Gateway smoke test returned an unexpected response"
        ) from exc
    if not isinstance(content, str) or content.strip() != "READY":
        raise RuntimeError("Gateway smoke test did not return exactly READY")


def vss_command(repo_root: Path) -> list[str]:
    return [
        "uv",
        "run",
        "--project",
        str(repo_root / "services" / "agent"),
        "--no-dev",
        "--extra",
        "cli",
        "vss",
    ]


def configure_vss(repo_root: Path) -> None:
    vss = vss_command(repo_root)
    run(
        [
            *vss,
            "configure",
            "memory",
            "introspection",
            "--judge-endpoint",
            GATEWAY_BASE_URL,
            "--judge-model",
            "openclaw/default",
            "--clear-judge-api-key-env",
            "--clear-judge-backend-model",
        ]
    )
    run([*vss, "configure", "memory", "show"])
    run([*vss, "configure", "memory", "check"])


def print_gateway_logs() -> None:
    result = run(
        ["docker", "logs", "--tail", "200", CONTAINER_NAME],
        check=False,
    )
    if result.stdout:
        print(result.stdout, file=sys.stderr, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")


def main() -> int:
    try:
        require_environment(os.environ)
        tmpdir = Path(os.environ.get("TMPDIR", ""))
        if not tmpdir.is_dir():
            raise RuntimeError("TMPDIR must name an existing task-private directory")
        repo_root = Path(
            os.environ.get(
                "VSS_REPO_ROOT",
                str(Path.home() / "video-search-and-summarization"),
            )
        )
        config_path = tmpdir / "openclaw.json"
        config_path.write_text(
            json.dumps(build_gateway_config(os.environ["ANTHROPIC_MODEL"]), indent=2)
            + "\n",
            encoding="utf-8",
        )
        config_path.chmod(0o444)

        run(["docker", "rm", "--force", CONTAINER_NAME], check=False)
        run(docker_create_command())
        run(["docker", "cp", str(config_path), f"{CONTAINER_NAME}:/tmp/openclaw.json"])
        run(["docker", "start", CONTAINER_NAME])
        wait_until_ready()
        smoke_test()
        configure_vss(repo_root)
        print("OpenClaw Gateway is ready and VSS memory introspection is configured.")
        return 0
    except (
        OSError,
        ValueError,
        RuntimeError,
        subprocess.SubprocessError,
        urllib.error.URLError,
    ) as exc:
        print(f"Failed to configure the OpenClaw Gateway: {exc}", file=sys.stderr)
        print_gateway_logs()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
