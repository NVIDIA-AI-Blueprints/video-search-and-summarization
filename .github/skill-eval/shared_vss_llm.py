# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify a deployed VSS LLM NIM and write the NemoClaw shared route."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


def _compose_config(resolved: Path) -> dict:
    result = subprocess.run(
        ["docker", "compose", "-f", str(resolved), "config", "--format", "json"],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def _local_llm_mode(build_dir: Path) -> str:
    override = build_dir / "override.env"
    if not override.is_file():
        raise ValueError(f"VSS build override is missing: {override}")
    values = {}
    for line in override.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    mode = values.get("LLM_MODE", "")
    if mode not in {"local", "local_shared"}:
        raise ValueError(f"VSS LLM_MODE must be local or local_shared, got {mode!r}")
    return mode


def resolve_route(resolved: Path, expected_model: str) -> tuple[str, str, str]:
    """Return container, served model and NemoClaw endpoint for one VSS NIM."""
    resolved = resolved.resolve(strict=True)
    _local_llm_mode(resolved.parent)
    config = _compose_config(resolved)
    matches = []
    for service in config.get("services", {}).values():
        environment = service.get("environment") or {}
        if isinstance(environment, list):
            environment = dict(item.split("=", 1) for item in environment if "=" in item)
        served = environment.get("NIM_SERVED_MODEL_NAME")
        if served == expected_model:
            matches.append(service)
    if len(matches) != 1:
        raise ValueError(
            f"Expected one VSS NIM serving {expected_model!r}; found {len(matches)}"
        )
    service = matches[0]
    container = service.get("container_name")
    ports = [
        int(p["published"])
        for p in service.get("ports", [])
        if isinstance(p, dict) and int(p.get("target", 0)) == 8000
        and str(p.get("published", "")).isdigit()
    ]
    if not container or len(ports) != 1:
        raise ValueError("VSS LLM NIM needs one named container and one host port")
    state = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", container],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if state != "true":
        raise ValueError(f"VSS LLM NIM container {container} is not running")
    local_base = f"http://127.0.0.1:{ports[0]}/v1"
    deadline = time.monotonic() + 120
    while True:
        try:
            with urlopen(f"{local_base}/health/ready", timeout=5) as response:
                if response.status != 200:
                    raise ValueError(f"VSS NIM readiness returned HTTP {response.status}")
            with urlopen(f"{local_base}/models", timeout=5) as response:
                models = json.load(response)
            served_models = [item.get("id") for item in models.get("data", [])]
            if served_models != [expected_model]:
                raise ValueError(
                    f"VSS NIM advertises {served_models!r}, expected {expected_model!r}"
                )
            break
        except (URLError, TimeoutError):
            if time.monotonic() >= deadline:
                raise ValueError("VSS LLM NIM did not become ready within 120 seconds") from None
            time.sleep(5)
    return container, expected_model, f"http://host.openshell.internal:{ports[0]}/v1"


def write_env(path: Path, model: str, endpoint: str) -> None:
    values = {
        "NEMOCLAW_PROVIDER": "custom",
        "NEMOCLAW_ENDPOINT_URL": endpoint,
        "NEMOCLAW_MODEL": model,
        "COMPATIBLE_API_KEY": "EMPTY",
        "NEMOCLAW_INFERENCE_PROXY": "0",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"export {key}={shlex.quote(value)}\n" for key, value in values.items()))
    path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolved", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args()
    container, model, endpoint = resolve_route(args.resolved, args.model)
    write_env(args.env_file, model, endpoint)
    print(f"shared-vss-llm: container={container} model={model} endpoint={endpoint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
