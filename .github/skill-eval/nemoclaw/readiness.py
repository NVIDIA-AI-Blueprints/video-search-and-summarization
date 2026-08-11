#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bounded readiness checks for the NemoClaw Harbor environment."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REQUIRED_COMMANDS = ("nemoclaw", "openshell", "docker", "curl", "uv")
DEFAULT_TOOLS = (
    "vss_orchestrator__prereqs,"
    "vss_orchestrator__docker_generate,"
    "vss_orchestrator__docker_up,"
    "vss_orchestrator__docker_status"
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("export ") or "=" not in line:
            continue
        key, value = line[7:].split("=", 1)
        parsed = shlex.split(value) if value else [""]
        os.environ.setdefault(key, parsed[0] if parsed else "")


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _gateway_name(port: str) -> str:
    if not port.isdigit() or not 1024 <= int(port) <= 65535:
        raise ValueError("invalid NemoClaw gateway port")
    return "nemoclaw" if int(port) == 8080 else f"nemoclaw-{int(port)}"


def _sandbox_command(
    sandbox: str,
    gateway: str,
    *args: str,
) -> list[str]:
    return [
        "openshell",
        "sandbox",
        "exec",
        "--name",
        sandbox,
        "-g",
        gateway,
        "--",
        *args,
    ]


def _check_sandbox(sandbox: str, gateway: str) -> dict[str, Any]:
    lookup = _run(
        ["openshell", "sandbox", "get", "-g", gateway, sandbox],
        timeout=60,
    )
    if lookup.returncode != 0:
        return {"ok": False, "lookup": False, "gateway": False}

    health_cmd = _sandbox_command(
        sandbox,
        gateway,
        "curl",
        "--noproxy",
        "*",
        "-sS",
        "--connect-timeout",
        "3",
        "--max-time",
        "10",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "http://127.0.0.1:18789/health",
    )
    health = _run(health_cmd, timeout=30)
    status = (health.stdout or "").strip()
    gateway_ok = health.returncode == 0 and status in {"200", "401"}
    if not gateway_ok:
        recovery = _run(
            ["nemoclaw", "sandbox", "recover", sandbox],
            timeout=360,
        )
        if recovery.returncode == 0:
            health = _run(health_cmd, timeout=30)
            status = (health.stdout or "").strip()
            gateway_ok = health.returncode == 0 and status in {"200", "401"}
    return {
        "ok": gateway_ok,
        "lookup": True,
        "gateway": gateway_ok,
        "http_status": status if status.isdigit() else "",
    }


def _check_host_mcp(root: Path) -> dict[str, Any]:
    helper_path = root / "deploy/docker/scripts/orchestrator_mcp_helper.py"
    agent_dir = root / "services/agent"
    spec = importlib.util.spec_from_file_location(
        "orchestrator_mcp_helper", helper_path
    )
    if spec is None or spec.loader is None:
        return {"ok": False, "message": "MCP health helper is unavailable"}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    healthy, message = module.check_mcp_health(
        os.environ.get("MCP_URL", "http://127.0.0.1:9988/mcp"),
        agent_dir,
    )
    return {"ok": bool(healthy), "message": str(message)[-500:]}


def _check_sandbox_mcp(
    sandbox: str,
    gateway: str,
    required_tools: list[str],
) -> dict[str, Any]:
    result = _run(
        _sandbox_command(
            sandbox,
            gateway,
            "mcporter",
            "list",
            "vss_orchestrator",
            "--json",
        ),
        timeout=90,
    )
    discovered: set[str] = set()
    if result.returncode == 0:
        try:
            payload = json.loads(result.stdout)
            for tool in payload.get("tools", []):
                if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                    discovered.add(tool["name"])
        except (json.JSONDecodeError, AttributeError):
            pass

    prefix = "vss_orchestrator__"
    missing = [
        tool
        for tool in required_tools
        if tool not in discovered
        and tool.removeprefix(prefix) not in discovered
    ]
    return {
        "ok": result.returncode == 0 and not missing,
        "tool_count": len(discovered),
        "missing": missing,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        default="/tmp/skill-eval/nemoclaw/nemoclaw.env",
    )
    parser.add_argument("--sandbox-name", default="")
    parser.add_argument("--gateway-port", default="")
    parser.add_argument("--required-tools", default=DEFAULT_TOOLS)
    args = parser.parse_args(argv)

    _load_env_file(Path(args.env_file))
    sandbox = args.sandbox_name or os.environ.get(
        "NEMOCLAW_SANDBOX_NAME", "skill-eval-nemoclaw"
    )
    port = args.gateway_port or os.environ.get("NEMOCLAW_GATEWAY_PORT", "8080")
    gateway = _gateway_name(port)
    required_tools = [
        item.strip() for item in args.required_tools.split(",") if item.strip()
    ]

    commands = {name: shutil.which(name) is not None for name in REQUIRED_COMMANDS}
    report: dict[str, Any] = {
        "commands": commands,
        "sandbox": _check_sandbox(sandbox, gateway),
        "host_mcp": _check_host_mcp(_repo_root()),
        "sandbox_mcp": _check_sandbox_mcp(
            sandbox, gateway, required_tools
        ),
    }
    report["ok"] = (
        all(commands.values())
        and report["sandbox"]["ok"]
        and report["host_mcp"]["ok"]
        and report["sandbox_mcp"]["ok"]
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
