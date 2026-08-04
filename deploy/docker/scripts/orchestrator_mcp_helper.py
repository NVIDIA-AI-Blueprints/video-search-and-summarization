# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import re
import shlex
import shutil
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


def ensure_mcp_tls_certs(
    certfile: str | Path,
    keyfile: str | Path,
    *,
    san: str,
    days: int = 365,
    subject: str = "/CN=vss-orchestrator-mcp",
) -> tuple[Path, Path]:
    """Ensure MCP TLS cert/key exist; generate a self-signed pair only if both are missing.

    An existing pair is reused as-is, but if the cert is already expired the call
    raises so the failure is obvious at preflight rather than as a TLS handshake
    error during the health check. Delete the pair and re-run to auto-generate, or
    replace with a valid cert/key.

    Args:
        certfile: Destination PEM certificate path.
        keyfile: Destination PEM private-key path.
        san: OpenSSL ``subjectAltName`` value (e.g. ``DNS:localhost,IP:127.0.0.1``).
        days: Certificate validity in days.
        subject: OpenSSL ``-subj`` value.

    Returns:
        Resolved ``(cert_path, key_path)``.

    Raises:
        FileNotFoundError: if exactly one of cert/key exists (refuses to overwrite
            a partial custom pair by auto-generating).
        RuntimeError: if the existing cert is expired, or if ``openssl`` is not
            available when generation is required.
        ValueError: if ``san`` is empty when generation is required.
        subprocess.CalledProcessError: if ``openssl`` fails.
    """
    cert_path = Path(certfile).expanduser().resolve()
    key_path = Path(keyfile).expanduser().resolve()
    cert_exists = cert_path.is_file()
    key_exists = key_path.is_file()
    if cert_exists and key_exists:
        # -checkend 0 exits non-zero when the cert is already past its notAfter.
        if shutil.which("openssl"):
            expired = subprocess.run(
                ["openssl", "x509", "-checkend", "0", "-noout", "-in", str(cert_path)],
                capture_output=True,
                text=True,
            )
            if expired.returncode != 0:
                raise RuntimeError(
                    f"MCP TLS cert {cert_path} is expired. "
                    "Delete the cert/key pair and re-run to auto-generate, "
                    "or replace them with a valid pair.",
                )
        return cert_path, key_path
    if cert_exists != key_exists:
        raise FileNotFoundError(
            "MCP TLS cert/key must both exist or both be absent for auto-generation."
        )

    san_value = san.strip()
    if not san_value:
        raise ValueError("san is required to auto-generate MCP TLS cert/key.")
    if not shutil.which("openssl"):
        raise RuntimeError("openssl is required to auto-generate MCP TLS cert/key.")

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    # openssl -nodes writes an unencrypted key and respects umask; tighten so the
    # key is never left world-readable even briefly under a default umask 022.
    previous_umask = os.umask(0o077)
    try:
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                str(days),
                "-keyout",
                str(key_path),
                "-out",
                str(cert_path),
                "-subj",
                subject,
                "-addext",
                f"subjectAltName={san_value}",
            ],
            check=True,
        )
    finally:
        os.umask(previous_umask)
    key_path.chmod(0o600)
    return cert_path, key_path


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
