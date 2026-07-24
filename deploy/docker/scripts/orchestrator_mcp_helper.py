# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import re
import shlex
import subprocess
import time
from enum import StrEnum
from pathlib import Path
from typing import Any


class OrchestratorTool(StrEnum):
    PROFILES = "vss_orchestrator__profiles"
    PREREQS = "vss_orchestrator__prereqs"
    DOCKER_GENERATE = "vss_orchestrator__docker_generate"
    DOCKER_READ = "vss_orchestrator__docker_read"
    DOCKER_LIST = "vss_orchestrator__docker_list"
    DOCKER_LOGS = "vss_orchestrator__docker_logs"
    DOCKER_UP = "vss_orchestrator__docker_up"
    DOCKER_DOWN = "vss_orchestrator__docker_down"
    DOCKER_STATUS = "vss_orchestrator__docker_status"


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def read_etc_environment() -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        with open("/etc/environment", encoding="utf-8") as fp:
            for raw in fp:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return env
    return env


def read_brev_environment_context(path: str | None = None) -> dict[str, Any]:
    """Return the parsed Brev environment context, or ``{}`` when unavailable.

    On Brev/launchpad hosts this file is the source of truth for the environment
    id and the per-port secure-link FQDNs, e.g.::

        {"environment_id": "juud6xh3e",
         "ports": [{"destination_port": 18789,
                    "fqdn": "18789-juud6xh3e.stg.apps.launchpad.nvidia.com"}, ...]}

    *path* defaults to ``BREV_ENVIRONMENT_CONTEXT_PATH``. When neither is set,
    returns ``{}`` (no hardcoded path).
    """
    if path is None:
        path = os.environ.get("BREV_ENVIRONMENT_CONTEXT_PATH", "").strip()
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def brev_environment_id() -> str:
    """Best-effort Brev environment id (``BREV_ENV_ID``).

    Precedence: ``BREV_ENV_ID`` env var -> context file
    (``BREV_ENVIRONMENT_CONTEXT_PATH``) -> ``/etc/environment``.
    """
    env_id = os.environ.get("BREV_ENV_ID", "").strip()
    if env_id:
        return env_id
    context_id = str(read_brev_environment_context().get("environment_id", "")).strip()
    if context_id:
        return context_id
    return read_etc_environment().get("BREV_ENV_ID", "").strip()


def brev_secure_link_fqdn(destination_port: int) -> str | None:
    """Return the secure-link FQDN mapped to *destination_port*, if published.

    Reads the exact FQDN from the context file. Requires ``BREV_ENVIRONMENT_CONTEXT_PATH``.
    """
    ports = read_brev_environment_context().get("ports")
    if not isinstance(ports, list):
        return None
    for entry in ports:
        if not isinstance(entry, dict):
            continue
        try:
            if int(entry.get("destination_port")) != int(destination_port):
                continue
        except (TypeError, ValueError):
            continue
        fqdn = str(entry.get("fqdn", "")).strip()
        if fqdn:
            return fqdn
    return None


def _link_domain_from_brev_context() -> str:
    """Derive the secure-link base domain from a port FQDN in the context file.

    e.g. ``18789-juud6xh3e.stg.apps.launchpad.nvidia.com`` (env id ``juud6xh3e``)
    -> ``stg.apps.launchpad.nvidia.com``.
    """
    context = read_brev_environment_context()
    env_id = str(context.get("environment_id", "")).strip().lower()
    ports = context.get("ports")
    if not env_id or not isinstance(ports, list):
        return ""
    marker = f"-{env_id}."
    for entry in ports:
        if not isinstance(entry, dict):
            continue
        fqdn = str(entry.get("fqdn", "")).strip().lower()
        if marker in fqdn:
            return fqdn.split(marker, 1)[1]
    return ""


def detect_brev_link_domain() -> str:
    """Resolve the Brev/launchpad secure-link base domain for this host.

    Precedence:
      1. Explicit ``BREV_LINK_DOMAIN`` override.
      2. Derived from the context file at ``BREV_ENVIRONMENT_CONTEXT_PATH``
         (required; absolute source of truth for secure-link FQDNs).
    """
    explicit_domain = os.environ.get("BREV_LINK_DOMAIN", "").strip()
    if explicit_domain:
        print(f"[brev-link-domain] using BREV_LINK_DOMAIN override: {explicit_domain}")
        return explicit_domain

    context_path = os.environ.get("BREV_ENVIRONMENT_CONTEXT_PATH", "").strip()
    if not context_path:
        _RED, _RESET = "\033[31m", "\033[0m"
        print(
            f"{_RED}[brev-link-domain] WARNING: BREV_ENVIRONMENT_CONTEXT_PATH is not set; "
            f"cannot derive the secure-link domain{_RESET}"
        )
        return ""

    context_domain = _link_domain_from_brev_context()
    if context_domain:
        print(f"[brev-link-domain] derived from {context_path}: {context_domain}")
        return context_domain

    _RED, _RESET = "\033[31m", "\033[0m"
    print(
        f"{_RED}[brev-link-domain] WARNING: could not derive the domain from "
        f"{context_path}; leaving BREV_LINK_DOMAIN unset{_RESET}"
    )
    return ""


def resolve_openshell_gateway_container(sandbox_name: str) -> str | None:
    """Return the running OpenShell sandbox container name for *sandbox_name*.

    Uses OpenShell owner labels instead of the container name prefix/format
    (``openshell-<name>-<id>``), which is an implementation detail.
    """
    result = subprocess.run(
        [
            "docker",
            "ps",
            "--no-trunc",
            "--filter",
            "label=openshell.ai/managed-by=openshell",
            "--filter",
            f"label=openshell.ai/sandbox-name={sandbox_name}",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return names[0] if names else None


def build_vss_ui_url(port: int = 7777) -> str | None:
    # Prefer the exact secure-link FQDN published in the context file.
    fqdn = brev_secure_link_fqdn(port)
    if fqdn:
        return f"https://{fqdn}/"

    brev_env_id = brev_environment_id()
    link_domain = detect_brev_link_domain()
    if not brev_env_id or not link_domain:
        return None
    link_prefix = os.environ.get("BREV_LINK_PREFIX", "").strip() or str(port)
    return f"https://{link_prefix}-{brev_env_id}.{link_domain}/"


def tool_call(
    name: str | OrchestratorTool,
    *,
    mcp_url: str,
    agent_dir: str | Path,
    arguments: dict[str, Any] | None = None,
    show_response: bool = True,
    response_prefix: str | None = None,
) -> dict[str, Any]:
    cmd = [
        "uv",
        "run",
        "nat",
        "mcp",
        "client",
        "tool",
        "call",
        name,
        "--url",
        mcp_url,
        "--transport",
        "streamable-http",
    ]
    if arguments:
        cmd.extend(["--json-args", json.dumps(arguments, indent=2)])

    print("$", shlex.join(cmd))

    result = subprocess.run(
        cmd,
        cwd=str(agent_dir),
        capture_output=True,
        text=True,
    )
    stdout = _strip_ansi(result.stdout).strip()
    stderr = _strip_ansi(result.stderr).strip()
    if result.returncode != 0:
        raise RuntimeError(f"{name} failed with exit code {result.returncode}\nSTDERR:\n{stderr}\nSTDOUT:\n{stdout}")
    if not stdout:
        raise RuntimeError(f"{name} returned no stdout. STDERR:\n{stderr}")

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} returned invalid JSON.\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}") from exc
    if show_response:
        if response_prefix:
            print(response_prefix)
        print(json.dumps(payload, indent=2))
    return payload


def check_mcp_health(mcp_url: str, agent_dir: str | Path, timeout_s: int = 15) -> tuple[bool, str]:
    cmd = [
        "uv",
        "run",
        "nat",
        "mcp",
        "client",
        "tool",
        "call",
        OrchestratorTool.PROFILES,
        "--url",
        mcp_url,
        "--transport",
        "streamable-http",
    ]
    result = subprocess.run(
        cmd,
        cwd=str(agent_dir),
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    stdout = _strip_ansi(result.stdout).strip()
    stderr = _strip_ansi(result.stderr).strip()
    if result.returncode != 0:
        return False, f"health command exited {result.returncode}: {(stderr or stdout).strip()}"
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return False, f"health command returned invalid JSON: {exc}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
    if payload.get("status") == "error":
        return False, f"VSS Orchestrator MCP health check failed: {payload.get('error', payload)}"
    return True, "VSS Orchestrator MCP health check succeeded"


def require_success(result: dict[str, Any], label: str) -> dict[str, Any]:
    if result.get("status") == "error":
        raise RuntimeError(f"{label} failed: {result.get('error', json.dumps(result, indent=2))}")
    return result


def poll_compose_op(
    docker_compose_ops_id: str,
    *,
    mcp_url: str,
    agent_dir: str | Path,
    tail_lines: int = 200,
    sleep_s: int = 30,
    show_response: bool = True,
    response_prefix: str | None = None,
) -> dict[str, Any]:
    while True:
        status_result = require_success(
            tool_call(
                OrchestratorTool.DOCKER_STATUS,
                mcp_url=mcp_url,
                agent_dir=agent_dir,
                arguments={
                    "docker_compose_ops_id": docker_compose_ops_id,
                    "tail_lines": tail_lines,
                },
                show_response=show_response,
                response_prefix=response_prefix,
            ),
            "docker_status",
        )
        if not status_result.get("running", False):
            return status_result
        time.sleep(sleep_s)
