#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Readiness checks for headless NemoClaw skill evaluation."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


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
        os.environ.setdefault(key, value.strip().strip("'\""))


def _run(cmd: list[str], *, timeout: int = 30, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)


def _check_cmd(name: str) -> dict[str, Any]:
    path = shutil.which(name)
    return {"name": name, "ok": bool(path), "path": path or ""}


def _check_sandbox(name: str) -> dict[str, Any]:
    if not shutil.which("openshell"):
        return {"name": name, "ok": False, "error": "openshell not found"}
    result = _run(["openshell", "sandbox", "get", name], timeout=60)
    return {
        "name": name,
        "ok": result.returncode == 0,
        "stdout_tail": result.stdout[-1000:],
        "stderr_tail": result.stderr[-1000:],
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default="/tmp/skill-eval/nemoclaw/nemoclaw.env")
    parser.add_argument("--mcp-url", default=None)
    parser.add_argument("--sandbox-name", default=None)
    parser.add_argument("--required-tools", default="")
    parser.add_argument("--output", default="/tmp/skill-eval/nemoclaw/readiness.json")
    args = parser.parse_args(argv)

    _load_env_file(Path(args.env_file))
    repo_root = _repo_root()
    sandbox_name = args.sandbox_name or os.environ.get("NEMOCLAW_SANDBOX_NAME", "demo")
    mcp_url = args.mcp_url or os.environ.get("MCP_URL", "http://localhost:9988/mcp")
    required_tools = [item.strip() for item in args.required_tools.split(",") if item.strip()]

    report = {
        "commands": [_check_cmd(name) for name in ("nemoclaw", "openshell", "docker", "curl", "uv")],
        "sandbox": _check_sandbox(sandbox_name),
        "mcp": _check_mcp(repo_root, mcp_url, required_tools),
    }
    ok = all(item["ok"] for item in report["commands"]) and report["sandbox"]["ok"] and report["mcp"]["ok"]
    report["ok"] = ok

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
