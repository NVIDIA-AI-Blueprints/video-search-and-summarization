#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Readiness checks for headless NemoClaw skill evaluation."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

COMMANDS = ("nemoclaw", "openshell", "docker", "curl", "uv")
MCP_SERVER_NAME = "vss_orchestrator"
SUMMARY_SCHEMA_VERSION = 1
_SANDBOX_MCP_ERROR_CATEGORIES = frozenset(
    {
        "sandbox_mcp_command_failed",
        "sandbox_mcp_discovery_timeout",
        "sandbox_mcp_invalid_response",
        "sandbox_mcp_missing_required_tools",
        "sandbox_mcp_unavailable",
    }
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or not line.startswith("export ") or "=" not in line:
            continue
        key, value = line[len("export ") :].split("=", 1)
        parsed = shlex.split(value) if value else [""]
        os.environ.setdefault(key, parsed[0] if parsed else "")


def _run(cmd: list[str], *, timeout: int = 30, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)


def _check_cmd(name: str) -> dict[str, Any]:
    path = shutil.which(name)
    return {"name": name, "ok": bool(path), "path": path or ""}


def _check_sandbox(name: str) -> dict[str, Any]:
    if not shutil.which("openshell"):
        return {"name": name, "ok": False, "error": "openshell not found"}
    sandbox_result = _run(["openshell", "sandbox", "get", name], timeout=60)
    gateway_result = None
    gateway_error = ""
    if sandbox_result.returncode == 0:
        try:
            gateway_result = _run(
                [
                    "openshell",
                    "sandbox",
                    "exec",
                    "-n",
                    name,
                    "--",
                    "sh",
                    "-lc",
                    "curl -fsS http://127.0.0.1:18789/health >/dev/null",
                ],
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            gateway_error = (
                f"in-sandbox OpenClaw gateway health check timed out "
                f"after {exc.timeout}s"
            )
    gateway_ok = gateway_result is not None and gateway_result.returncode == 0
    gateway_detail = gateway_error
    if gateway_result is not None:
        gateway_detail = (gateway_result.stderr or gateway_result.stdout or "")[-1000:]
    return {
        "name": name,
        "ok": sandbox_result.returncode == 0 and gateway_ok,
        "stdout_tail": sandbox_result.stdout[-1000:],
        "stderr_tail": sandbox_result.stderr[-1000:],
        "gateway_ok": gateway_ok,
        "gateway_stderr_tail": gateway_detail,
    }


def _check_sandbox_mcp(name: str, required_tools: list[str]) -> dict[str, Any]:
    if not shutil.which("nemoclaw"):
        return {
            "ok": False,
            "error": "nemoclaw not found",
            "error_category": "sandbox_mcp_unavailable",
            "required_tools": required_tools,
            "missing_tools": required_tools,
        }

    cmd = [
        "nemoclaw",
        "sandbox",
        "exec",
        name,
        "--",
        "mcporter",
        "list",
        MCP_SERVER_NAME,
        "--json",
    ]
    try:
        result = _run(cmd, timeout=90)
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": f"in-sandbox MCP discovery timed out after {exc.timeout}s",
            "error_category": "sandbox_mcp_discovery_timeout",
            "required_tools": required_tools,
            "missing_tools": required_tools,
        }

    discovered_tools: set[str] = set()
    error_category = ""
    if result.returncode == 0:
        try:
            payload = json.loads(result.stdout)
            if (
                not isinstance(payload, dict)
                or payload.get("mode") != "server"
                or payload.get("name") != MCP_SERVER_NAME
                or payload.get("status") != "ok"
            ):
                raise ValueError("unexpected mcporter server envelope")
            tools = payload.get("tools")
            if not isinstance(tools, list):
                raise ValueError("tools must be a list")
            for tool in tools:
                if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                    raise ValueError("tool entries must contain string names")
                discovered_tools.add(tool["name"])
        except (json.JSONDecodeError, ValueError):
            error_category = "sandbox_mcp_invalid_response"
    else:
        error_category = "sandbox_mcp_command_failed"

    prefix = f"{MCP_SERVER_NAME}__"

    def is_discovered(required_tool: str) -> bool:
        raw_tool = (
            required_tool[len(prefix) :]
            if required_tool.startswith(prefix)
            else required_tool
        )
        return required_tool in discovered_tools or raw_tool in discovered_tools

    missing_tools = [tool for tool in required_tools if not is_discovered(tool)]
    if not error_category and missing_tools:
        error_category = "sandbox_mcp_missing_required_tools"
    return {
        "ok": result.returncode == 0 and not error_category and not missing_tools,
        "returncode": result.returncode,
        "required_tools": required_tools,
        "missing_tools": missing_tools,
        "discovered_tools": sorted(discovered_tools),
        "error_category": error_category,
        "stdout_tail": (result.stdout or "")[-4000:],
        "stderr_tail": (result.stderr or "")[-2000:],
        "note": (
            "Runs mcporter tool discovery through the sandbox egress policy. "
            "mcporter reports raw server tool names; required OpenClaw tool "
            "names are normalized from <server>__<tool> before comparison."
        ),
    }


def _check_mcp(repo_root: Path, mcp_url: str, required_tools: list[str]) -> dict[str, Any]:
    helper_path = repo_root / "deploy" / "docker" / "scripts" / "orchestrator_mcp_helper.py"
    agent_dir = repo_root / "services" / "agent"
    if not helper_path.exists():
        return {"ok": False, "error": f"missing helper: {helper_path}"}
    if not agent_dir.is_dir():
        return {"ok": False, "error": f"missing agent dir: {agent_dir}"}

    import importlib.util

    spec = importlib.util.spec_from_file_location("orchestrator_mcp_helper", helper_path)
    if spec is None or spec.loader is None:
        return {"ok": False, "error": f"cannot load helper: {helper_path}"}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    healthy, message = module.check_mcp_health(mcp_url, agent_dir)
    return {
        "ok": bool(healthy),
        "message": message,
        "mcp_url": mcp_url,
        "required_tools": required_tools,
        "note": "Health uses the read-only profiles tool; side-effect tools are checked from trajectory after the scenario.",
    }


def _build_safe_summary(report: dict[str, Any]) -> dict[str, Any]:
    command_status = {name: False for name in COMMANDS}
    for item in report.get("commands", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if name in command_status:
            command_status[name] = item.get("ok") is True

    sandbox = report.get("sandbox")
    sandbox = sandbox if isinstance(sandbox, dict) else {}
    mcp = report.get("mcp")
    mcp = mcp if isinstance(mcp, dict) else {}
    sandbox_mcp = report.get("sandbox_mcp")
    sandbox_mcp = sandbox_mcp if isinstance(sandbox_mcp, dict) else {}
    missing_tool_values = sandbox_mcp.get("missing_tools")
    missing_tool_count = (
        len(missing_tool_values) if isinstance(missing_tool_values, list) else 0
    )

    categories: list[str] = []
    if not all(command_status.values()):
        categories.append("required_commands_unavailable")
    if sandbox.get("ok") is not True:
        categories.append("sandbox_unavailable")
    if sandbox.get("gateway_ok") is not True:
        categories.append("sandbox_gateway_unhealthy")
    if mcp.get("ok") is not True:
        categories.append("host_mcp_unhealthy")
    if sandbox_mcp.get("ok") is not True:
        error_category = sandbox_mcp.get("error_category")
        categories.append(
            error_category
            if error_category in _SANDBOX_MCP_ERROR_CATEGORIES
            else "sandbox_mcp_unavailable"
        )

    returncode = sandbox_mcp.get("returncode")
    if not isinstance(returncode, int) or isinstance(returncode, bool):
        returncode = None

    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "ok": report.get("ok") is True,
        "categories": categories,
        "commands": command_status,
        "sandbox": {
            "ok": sandbox.get("ok") is True,
            "gateway_ok": sandbox.get("gateway_ok") is True,
        },
        "host_mcp": {"ok": mcp.get("ok") is True},
        "sandbox_mcp": {
            "ok": sandbox_mcp.get("ok") is True,
            "return_code": returncode,
            "missing_required_tool_count": missing_tool_count,
        },
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(
            f"refusing existing readiness output: {path}"
        ) from exc
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if fd >= 0:
            os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default="/tmp/skill-eval/nemoclaw/nemoclaw.env")
    parser.add_argument("--mcp-url", default=None)
    parser.add_argument("--sandbox-name", default=None)
    parser.add_argument("--required-tools", default="")
    parser.add_argument("--output", default="/tmp/skill-eval/nemoclaw/readiness.json")
    parser.add_argument(
        "--summary-output",
        default="/tmp/skill-eval/nemoclaw/readiness-summary.json",
    )
    args = parser.parse_args(argv)

    _load_env_file(Path(args.env_file))
    repo_root = _repo_root()
    sandbox_name = args.sandbox_name or os.environ.get("NEMOCLAW_SANDBOX_NAME", "demo")
    mcp_url = args.mcp_url or os.environ.get("MCP_URL", "http://localhost:9988/mcp")
    required_tools = [item.strip() for item in args.required_tools.split(",") if item.strip()]

    report = {
        "commands": [_check_cmd(name) for name in COMMANDS],
        "sandbox": _check_sandbox(sandbox_name),
        "mcp": _check_mcp(repo_root, mcp_url, required_tools),
        "sandbox_mcp": _check_sandbox_mcp(sandbox_name, required_tools),
    }
    ok = (
        all(item["ok"] for item in report["commands"])
        and report["sandbox"]["ok"]
        and report["mcp"]["ok"]
        and report["sandbox_mcp"]["ok"]
    )
    report["ok"] = ok
    summary = _build_safe_summary(report)

    _write_json(Path(args.output), report)
    _write_json(Path(args.summary_output), summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not ok:
        categories = ",".join(summary["categories"]) or "unclassified"
        print(
            f"NemoClaw readiness failed: categories={categories}",
            file=sys.stderr,
        )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
