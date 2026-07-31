# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time

try:
    from enum import StrEnum
except ImportError:  # Python 3.10 on some eval workers.
    from enum import Enum

    class StrEnum(str, Enum):
        pass


from pathlib import Path
from typing import Any


def _listening_socket_inodes(port: int, proc_root: Path = Path("/proc")) -> set[str]:
    """Return Linux socket inodes listening on *port* in this network namespace."""

    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")

    inodes: set[str] = set()
    for table_name in ("tcp", "tcp6"):
        table_path = proc_root / "net" / table_name
        try:
            lines = table_path.read_text(encoding="utf-8").splitlines()
        except (FileNotFoundError, PermissionError):
            continue
        for line in lines[1:]:
            fields = line.split()
            if len(fields) < 10 or fields[3] != "0A":
                continue
            _, separator, port_hex = fields[1].rpartition(":")
            if not separator:
                continue
            try:
                local_port = int(port_hex, 16)
            except ValueError:
                continue
            if local_port == port:
                inodes.add(fields[9])
    return inodes


def _listening_pids(port: int, proc_root: Path = Path("/proc")) -> set[int]:
    """Return process IDs owning listening sockets on *port*."""

    socket_inodes = _listening_socket_inodes(port, proc_root)
    socket_targets = {f"socket:[{inode}]" for inode in socket_inodes}
    if not socket_targets:
        return set()

    pids: set[int] = set()
    matched_socket_targets: set[str] = set()
    try:
        process_dirs = list(proc_root.iterdir())
    except (FileNotFoundError, PermissionError):
        return pids
    for process_dir in process_dirs:
        if not process_dir.name.isdigit():
            continue
        try:
            file_descriptors = list((process_dir / "fd").iterdir())
        except (FileNotFoundError, PermissionError):
            continue
        for file_descriptor in file_descriptors:
            try:
                target = os.readlink(file_descriptor)
            except (FileNotFoundError, PermissionError, OSError):
                continue
            if target in socket_targets:
                pids.add(int(process_dir.name))
                matched_socket_targets.add(target)
    live_socket_targets = {
        f"socket:[{inode}]" for inode in _listening_socket_inodes(port, proc_root)
    }
    unmatched_socket_targets = (
        socket_targets & live_socket_targets
    ) - matched_socket_targets
    if unmatched_socket_targets:
        raise RuntimeError(
            f"Port {port} has listening sockets whose owning processes cannot "
            f"be inspected: {sorted(unmatched_socket_targets)}"
        )
    return pids


def _read_process_identity(
    pid: int,
    proc_root: Path = Path("/proc"),
) -> tuple[int, Path, str, int, tuple[str, ...]]:
    """Return UID, cwd, start time, process group, and command line."""

    process_dir = proc_root / str(pid)
    uid = process_dir.stat().st_uid
    cwd = Path(os.readlink(process_dir / "cwd")).resolve()
    stat_text = (process_dir / "stat").read_text(encoding="utf-8")
    _, separator, stat_fields = stat_text.rpartition(")")
    fields_after_command = stat_fields.split()
    if not separator or len(fields_after_command) < 20:
        raise RuntimeError(f"Could not parse process identity for PID {pid}")
    start_time = fields_after_command[19]
    process_group_id = os.getpgid(pid)
    raw_command = (process_dir / "cmdline").read_bytes()
    command = tuple(os.fsdecode(part) for part in raw_command.split(b"\0") if part)
    return uid, cwd, start_time, process_group_id, command


def _is_expected_orchestrator_mcp_process(
    command: tuple[str, ...],
    config_path: str | Path,
    port: int,
) -> bool:
    """Return whether *command* is the VSS ``nat mcp serve`` process."""

    if not command:
        return False
    has_nat = any(Path(argument).name == "nat" for argument in command)
    has_mcp_serve = any(
        command[index : index + 2] == ("mcp", "serve")
        for index in range(len(command) - 1)
    )

    configured_path: str | None = None
    for index, argument in enumerate(command):
        if argument == "--config_file" and index + 1 < len(command):
            configured_path = command[index + 1]
            break
        if argument.startswith("--config_file="):
            configured_path = argument.partition("=")[2]
            break
    if configured_path is None:
        return False

    configured_port: str | None = None
    for index, argument in enumerate(command):
        if argument == "--port" and index + 1 < len(command):
            configured_port = command[index + 1]
            break
        if argument.startswith("--port="):
            configured_port = argument.partition("=")[2]
            break

    return (
        has_nat
        and has_mcp_serve
        and Path(configured_path).resolve() == Path(config_path).resolve()
        and configured_port == str(port)
    )


def stop_existing_orchestrator_mcp_listener(
    port: int,
    config_path: str | Path,
    agent_dir: str | Path,
    *,
    timeout_s: float = 15,
    poll_interval_s: float = 0.2,
) -> list[int]:
    """Stop a stale VSS orchestrator listener left by an earlier notebook run.

    Warm eval workers execute each setup notebook in a fresh kernel, so the
    prior kernel's in-memory PID is unavailable even though its host-side MCP
    server can still own the fixed port. Refuse to terminate an unrelated
    listener: every PID must be a ``nat mcp serve`` process using the expected
    checked-out config file.
    """

    deadline = time.monotonic() + timeout_s
    signalled: set[int] = set()
    signalled_groups: set[int] = set()
    while True:
        listening_pids = _listening_pids(port)
        if not listening_pids:
            return sorted(signalled)

        validated: dict[
            int,
            tuple[
                tuple[int, Path, str, int, tuple[str, ...]],
                tuple[int, Path, str, int, tuple[str, ...]],
            ],
        ] = {}
        for pid in sorted(listening_pids - signalled):
            try:
                listener_identity = _read_process_identity(pid)
            except FileNotFoundError:
                continue
            uid, cwd, _start_time, process_group_id, command = listener_identity
            if (
                uid != os.geteuid()
                or cwd != Path(agent_dir).resolve()
                or not _is_expected_orchestrator_mcp_process(
                    command,
                    config_path,
                    port,
                )
            ):
                executable_name = Path(command[0]).name if command else "<unavailable>"
                raise RuntimeError(
                    "Refusing to stop an unrelated process listening on "
                    f"orchestrator MCP port {port}: PID {pid}, UID {uid}, "
                    f"PGID {process_group_id}, cwd {cwd}, executable "
                    f"{executable_name}"
                )
            try:
                leader_identity = (
                    listener_identity
                    if process_group_id == pid
                    else _read_process_identity(process_group_id)
                )
            except FileNotFoundError as exc:
                raise RuntimeError(
                    f"Refusing to stop MCP listener PID {pid}: "
                    f"process-group leader {process_group_id} is unavailable"
                ) from exc
            leader_uid, leader_cwd, _leader_start_time, leader_pgid, leader_command = (
                leader_identity
            )
            if (
                leader_uid != os.geteuid()
                or leader_cwd != Path(agent_dir).resolve()
                or leader_pgid != process_group_id
                or not _is_expected_orchestrator_mcp_process(
                    leader_command,
                    config_path,
                    port,
                )
            ):
                leader_executable_name = (
                    Path(leader_command[0]).name if leader_command else "<unavailable>"
                )
                raise RuntimeError(
                    "Refusing to stop an unrelated process group owning "
                    f"orchestrator MCP port {port}: leader PID {process_group_id}, "
                    f"UID {leader_uid}, PGID {leader_pgid}, cwd {leader_cwd}: "
                    f"executable {leader_executable_name}"
                )
            validated[pid] = (listener_identity, leader_identity)

        # Validate every listener before signaling any of them, then re-read
        # each process identity at the signal boundary to guard against PID
        # reuse between inspection and termination.
        if _listening_pids(port) != listening_pids:
            raise RuntimeError(
                f"Refusing to stop orchestrator MCP listeners on port {port}: "
                "the listener set changed during validation"
            )
        for pid, (listener_identity, leader_identity) in validated.items():
            try:
                current_listener_identity = _read_process_identity(pid)
            except FileNotFoundError:
                continue
            if current_listener_identity != listener_identity:
                raise RuntimeError(
                    f"Refusing to signal PID {pid}: process identity changed during validation"
                )
            process_group_id = listener_identity[3]
            try:
                current_leader_identity = _read_process_identity(process_group_id)
            except FileNotFoundError as exc:
                raise RuntimeError(
                    f"Refusing to signal process group {process_group_id}: "
                    "leader disappeared during validation"
                ) from exc
            if current_leader_identity != leader_identity:
                raise RuntimeError(
                    f"Refusing to signal process group {process_group_id}: "
                    "leader identity changed during validation"
                )
            if process_group_id in signalled_groups:
                signalled.add(pid)
                continue
            try:
                os.killpg(process_group_id, signal.SIGTERM)
            except ProcessLookupError:
                continue
            signalled.add(pid)
            signalled_groups.add(process_group_id)

        if time.monotonic() >= deadline:
            remaining = sorted(_listening_pids(port))
            raise RuntimeError(
                "Timed out waiting for the previous orchestrator MCP listener "
                f"to release port {port}; remaining PIDs: {remaining}"
            )
        time.sleep(poll_interval_s)


class OrchestratorTool(StrEnum):
    PROFILES = "vss_orchestrator__profiles"
    PREREQS = "vss_orchestrator__prereqs"
    RTSP_SAMPLE_PROBE = "vss_orchestrator__rtsp_sample_probe"
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


def check_mcp_health(
    mcp_url: str,
    agent_dir: str | Path,
    timeout_s: int = 15,
    *,
    expected_instance_id: str | None = None,
    expected_source_sha256: str | None = None,
    expected_git_sha: str | None = None,
) -> tuple[bool, str]:
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
        return (
            False,
            f"health command exited {result.returncode}: {(stderr or stdout).strip()}",
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return (
            False,
            f"health command returned invalid JSON: {exc}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}",
        )
    if payload.get("status") == "error":
        return (
            False,
            f"VSS Orchestrator MCP health check failed: {payload.get('error', payload)}",
        )
    expected_provenance = {
        "runtime_instance_id": expected_instance_id,
        "runtime_source_sha256": expected_source_sha256,
        "runtime_git_sha": expected_git_sha,
    }
    mismatches = [
        f"{key}={payload.get(key)!r}, expected {expected!r}"
        for key, expected in expected_provenance.items()
        if expected is not None and payload.get(key) != expected
    ]
    if mismatches:
        return (
            False,
            (
                "VSS Orchestrator MCP health check reached a stale or unexpected "
                f"runtime: {'; '.join(mismatches)}"
            ),
        )
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
