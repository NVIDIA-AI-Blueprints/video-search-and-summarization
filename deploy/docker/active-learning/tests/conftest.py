# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for the AOI active-learning deployment tests.

Expectations are read from the resolved Compose model (``docker compose config``)
rather than hardcoded, so the checks follow the deployment when ports, replicas,
images or bootstrap settings change.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest

DEPLOY_DIR = Path(__file__).resolve().parents[1]
COMPOSE_FILE = "compose.yml"
# The documented run command passes only .env.secrets; each bundle loads its own
# includes/<bundle>/.env.deft-aoi-* through `include: env_file:`.
ENV_FILES = (".env.secrets",)
# Host where published ports are reachable. Unset means the address each port is
# bound to (127.0.0.1 by default). Set it to verify a remote Docker host.
HOST = os.environ.get("AL_VERIFY_HOST")
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
LONG_RUNNING_RESTART_POLICIES = {"always", "unless-stopped"}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: needs the stack to be running (docker compose up)")
    config.addinivalue_line("markers", "registry: needs network access and an nvcr.io login")


def compose_cmd(*args: str) -> list[str]:
    cmd = ["docker", "compose"]
    for env_file in ENV_FILES:
        if (DEPLOY_DIR / env_file).is_file():
            cmd += ["--env-file", env_file]
    return [*cmd, "-f", COMPOSE_FILE, *args]


def run(cmd: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=DEPLOY_DIR, capture_output=True, text=True, timeout=timeout)


def http_get(url: str, *, timeout: float = 10) -> tuple[int, str]:
    """Return (status, body); connection failures come back as status 0."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        return 0, str(exc)


def mount_source(service: dict, target: str) -> str:
    """Host path (or volume name) mounted at ``target`` in a resolved service."""
    return next(v["source"] for v in service.get("volumes") or [] if v["target"] == target)


def is_long_running(service: dict) -> bool:
    return service.get("restart", "no") in LONG_RUNNING_RESTART_POLICIES


def replicas(service: dict) -> int:
    return int(service.get("scale") or (service.get("deploy") or {}).get("replicas") or 1)


@dataclass(frozen=True)
class Container:
    name: str
    service: str
    image: str
    status: str
    health: str | None
    restart_count: int
    exit_code: int

    @classmethod
    def from_inspect(cls, data: dict) -> Container:
        state = data["State"]
        return cls(
            name=data["Name"].lstrip("/"),
            service=data["Config"]["Labels"].get("com.docker.compose.service", ""),
            image=data["Config"]["Image"],
            status=state["Status"],
            health=(state.get("Health") or {}).get("Status"),
            restart_count=data["RestartCount"],
            exit_code=state["ExitCode"],
        )


@pytest.fixture(scope="session")
def compose_config() -> dict:
    result = run(compose_cmd("config", "--format", "json"))
    if result.returncode != 0:
        pytest.fail(f"docker compose config failed:\n{result.stderr}", pytrace=False)
    return json.loads(result.stdout)


@pytest.fixture(scope="session")
def services(compose_config: dict) -> dict[str, dict]:
    return compose_config["services"]


@pytest.fixture(scope="session")
def containers(compose_config: dict) -> list[Container]:
    project = compose_config["name"]
    ids = run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"])
    if ids.returncode != 0:
        pytest.fail(f"docker ps failed:\n{ids.stderr}", pytrace=False)
    if not ids.stdout.split():
        pytest.fail(
            f"No containers found for Compose project '{project}'. "
            "Deploy first: ./deploy-and-verify.sh",
            pytrace=False,
        )
    inspected = run(["docker", "inspect", *ids.stdout.split()])
    if inspected.returncode != 0:
        pytest.fail(f"docker inspect failed:\n{inspected.stderr}", pytrace=False)
    return [Container.from_inspect(c) for c in json.loads(inspected.stdout)]


@pytest.fixture(scope="session")
def containers_by_service(containers: list[Container]) -> dict[str, list[Container]]:
    grouped: dict[str, list[Container]] = {}
    for container in containers:
        grouped.setdefault(container.service, []).append(container)
    return grouped


@pytest.fixture(scope="session")
def published_url(services: dict[str, dict]):
    """Build ``http://<host>:<published port>`` for a service's container port."""

    def _url(service: str, container_port: int, path: str = "") -> str:
        for port in services[service].get("ports", []):
            if int(port["target"]) != container_port or not port.get("published"):
                continue
            bind = port.get("host_ip") or "0.0.0.0"
            host = HOST or (bind if bind not in ("0.0.0.0", "::") else "localhost")
            if bind in LOOPBACK and host not in LOOPBACK:
                pytest.fail(
                    f"{service} publishes port {port['published']} on {bind} only, so {host} "
                    "cannot reach it. Run the checks on the Docker host, or set the port's bind "
                    "variable (README.md, 'Published ports') and redeploy.",
                    pytrace=False,
                )
            if ":" in host:
                host = f"[{host}]"
            return f"http://{host}:{port['published']}{path}"
        pytest.fail(f"{service} does not publish container port {container_port}", pytrace=False)

    return _url
