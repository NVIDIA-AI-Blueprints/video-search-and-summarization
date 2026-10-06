# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Read-only discovery of an existing NIM in this checkout's VSS deployment.

No placement changes, model substitutions or container ownership transfers.
A missing match permits provisioning; a broken match must not create a duplicate.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def docker_json(*args: str):
    result = subprocess.run(
        ["docker", *args], check=True, capture_output=True, text=True, timeout=30,
    )
    return json.loads(result.stdout)


def _labels(container: dict) -> dict:
    return container.get("Config", {}).get("Labels") or {}


def deployment_containers(repo: Path) -> list[dict]:
    """Use the running ingress's Compose labels, including stopped NIMs."""
    result = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project"],
        check=True, capture_output=True, text=True, timeout=30,
    )
    containers = docker_json("inspect", *result.stdout.split()) if result.stdout.strip() else []
    repo = repo.resolve()
    projects = set()
    for container in containers:
        labels = _labels(container)
        files = labels.get("com.docker.compose.project.config_files", "")
        if (
            container.get("State", {}).get("Running")
            and labels.get("com.docker.compose.service") == "vss-haproxy-ingress"
            and any(Path(path).resolve().is_relative_to(repo) for path in files.split(",") if path)
        ):
            projects.add((labels["com.docker.compose.project"], files))
    if len(projects) != 1:
        raise ValueError(
            f"Expected one running VSS deployment from {repo}; found {len(projects)}. "
            "Deploy VSS before preparing the local operational model."
        )
    project, files = projects.pop()
    return [container for container in containers if (
        _labels(container).get("com.docker.compose.project") == project
        and _labels(container).get("com.docker.compose.project.config_files") == files
    )]


def _environment(container: dict) -> dict:
    return dict(entry.split("=", 1) for entry in container.get("Config", {}).get("Env", []) if "=" in entry)


def _endpoint(container: dict) -> str:
    bindings = container.get("NetworkSettings", {}).get("Ports", {}).get("8000/tcp") or []
    ports = {str(binding.get("HostPort", "")) for binding in bindings}
    if len(ports) != 1 or not next(iter(ports), "").isdigit():
        raise ValueError("Matching VSS NIM needs one published host port for port 8000")
    port = int(ports.pop())
    if not 1 <= port <= 65535:
        raise ValueError("Matching VSS NIM has an invalid host port")
    host = bindings[0].get("HostIp") or "127.0.0.1"
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}/v1"


def discover(repo: Path, expected_model: str) -> dict | None:
    canonical = expected_model.removeprefix("nvidia_nim/")
    matches = []
    for container in deployment_containers(repo):
        image = container.get("Config", {}).get("Image", "")
        repository = image.split("@")[0].split(":")[0]
        env = _environment(container)
        if (
            repository in (f"nvcr.io/nim/{canonical}", f"nvcr.io/nim/{canonical}-dgx-spark")
            and env.get("NIM_MODEL_NAME", canonical) == canonical
        ):
            matches.append(container)
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError(f"Found {len(matches)} VSS NIMs for {canonical}; refusing an ambiguous model route")
    container = matches[0]
    image = docker_json("image", "inspect", container["Image"])[0]
    binding = {
        "model": canonical,
        "served_model": _environment(container).get("NIM_SERVED_MODEL_NAME") or expected_model,
        "source": "vss",
        "container": container["Id"],
        "container_name": container["Name"].lstrip("/"),
        "project": _labels(container)["com.docker.compose.project"],
        "service": _labels(container)["com.docker.compose.service"],
        "image_id": container["Image"],
        "image": (image.get("RepoDigests") or [container["Config"]["Image"]])[0],
        "architecture": image["Architecture"],
        "endpoint": _endpoint(container),
    }
    verify(binding)
    return binding


def verify(binding: dict, timeout: int = 120) -> None:
    """Recheck the recorded container and model; never silently rebind it."""
    deadline = time.monotonic() + timeout
    while True:
        container = docker_json("inspect", binding["container"])[0]
        if not container.get("State", {}).get("Running"):
            raise ValueError("Matching VSS NIM is stopped; refusing to provision a duplicate")
        if (
            container["Id"] != binding["container"]
            or container["Image"] != binding["image_id"]
            or _labels(container).get("com.docker.compose.project") != binding["project"]
            or _labels(container).get("com.docker.compose.service") != binding["service"]
            or _endpoint(container) != binding["endpoint"]
        ):
            raise ValueError("Recorded VSS NIM identity or endpoint changed")
        try:
            with urlopen(f"{binding['endpoint']}/health/ready", timeout=5):
                pass
            with urlopen(f"{binding['endpoint']}/models", timeout=5) as response:
                models = json.load(response)
            names = [entry.get("id") for entry in models.get("data", [])]
            if names != [binding["served_model"]]:
                raise ValueError(f"VSS NIM advertises {names!r}, expected {binding['served_model']!r}")
            return
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504):
                raise ValueError(f"Matching VSS NIM readiness failed: HTTP {error.code}") from None
        except (URLError, TimeoutError):
            pass
        if time.monotonic() >= deadline:
            raise ValueError("Matching VSS NIM did not become ready; refusing to provision a duplicate")
        time.sleep(3)
