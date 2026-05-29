#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Launch a NemoClaw/OpenClaw scenario from a Harbor trial.

Harbor remains the result owner. The Harbor agent only invokes this script;
this script sends the real prompt to the OpenClaw hooks endpoint so the VSS
skills run inside NemoClaw/OpenClaw with the VSS Orchestrator MCP available.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or not line.startswith("export ") or "=" not in line:
            continue
        key, value = line[len("export ") :].split("=", 1)
        os.environ.setdefault(key, shlex.split(value)[0] if value else "")


def _read_hooks_token() -> str:
    token = os.environ.get("OPENCLAW_HOOKS_TOKEN", "")
    if token:
        return token

    token_file = os.environ.get("NEMOCLAW_HOOKS_TOKEN_FILE", "")
    if not token_file:
        return ""
    try:
        return Path(token_file).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _run(cmd: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _forward_running(port: str, sandbox_name: str) -> bool:
    result = _run(["openshell", "forward", "list"], timeout=30)
    combined = f"{result.stdout}\n{result.stderr}"
    for raw in combined.splitlines():
        parts = raw.split()
        if len(parts) >= 5 and parts[0] == sandbox_name and parts[2] == port and parts[-1].lower() == "running":
            return True
    result = _run(["ps", "-eo", "args="], timeout=10)
    if result.returncode != 0:
        return False
    needles = (
        f"openshell forward start {port} {sandbox_name}",
        f"openshell forward start --background {port} {sandbox_name}",
    )
    return any(any(needle in line for needle in needles) for line in result.stdout.splitlines())


def _dashboard_healthy(port: str) -> bool:
    result = _run(["curl", "-fsS", f"http://127.0.0.1:{port}/health"], timeout=10)
    return result.returncode == 0


def ensure_forward(port: str, sandbox_name: str) -> None:
    if _dashboard_healthy(port):
        return
    _run(["openshell", "forward", "stop", port, sandbox_name], timeout=30)
    if shutil_which("setsid"):
        subprocess.run(
            ["setsid", "-f", "openshell", "forward", "start", port, sandbox_name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        _run(["openshell", "forward", "start", "--background", port, sandbox_name], timeout=60)
    for _ in range(30):
        if _dashboard_healthy(port):
            return
        time.sleep(2)
    raise RuntimeError(f"OpenClaw forward {port} for sandbox {sandbox_name} is not healthy")


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)


def post_hook(url: str, token: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                parsed: Any = json.loads(body) if body else {}
            except json.JSONDecodeError:
                parsed = body
            return {"status": response.status, "body": parsed}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"status": exc.code, "body": body, "error": str(exc)}
    except urllib.error.URLError as exc:
        return {"status": 0, "body": "", "error": str(exc)}


def _response_ok(response: dict[str, Any]) -> bool:
    body = response.get("body")
    return 200 <= int(response.get("status", 0)) < 300 and isinstance(body, dict) and bool(body.get("ok"))


def _vss_base_ready() -> tuple[bool, str]:
    probes = [
        ["curl", "-sf", "--max-time", "15", "http://localhost:8000/docs"],
        ["curl", "-sf", "--max-time", "15", "http://localhost:3000/"],
    ]
    for probe in probes:
        result = _run(probe, timeout=20)
        if result.returncode != 0:
            return False, f"{' '.join(probe)} failed: {(result.stderr or result.stdout)[-300:]}"

    result = _run(["docker", "ps", "--format", "{{.Names}}"], timeout=20)
    if result.returncode != 0:
        return False, f"docker ps failed: {(result.stderr or result.stdout)[-300:]}"
    names = set(result.stdout.splitlines())
    missing = sorted({"vss-agent", "vss-agent-ui", "redis"} - names)
    if missing:
        return False, "missing containers: " + ", ".join(missing)
    return True, "VSS base readiness probes passed"


def wait_for_profile(profile: str, timeout_s: int, log_dir: Path) -> dict[str, Any]:
    if not profile:
        return {"waited": False, "reason": "no profile requested"}

    deadline = time.time() + timeout_s
    attempts: list[dict[str, Any]] = []
    while time.time() < deadline:
        ok, message = _vss_base_ready()
        attempts.append({"t": round(time.time(), 3), "ok": ok, "message": message})
        (log_dir / "nemoclaw_wait.json").write_text(json.dumps(attempts, indent=2), encoding="utf-8")
        if ok:
            return {"waited": True, "ok": True, "profile": profile, "message": message}
        time.sleep(30)
    return {
        "waited": True,
        "ok": False,
        "profile": profile,
        "message": attempts[-1]["message"] if attempts else "no readiness attempts ran",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--env-file", default="/tmp/skill-eval/nemoclaw/nemoclaw.env")
    parser.add_argument("--log-dir", default="/logs/agent")
    parser.add_argument("--name", default="NemoClaw Harbor skill evaluation")
    parser.add_argument("--dashboard-port", default=os.environ.get("NEMOCLAW_DASHBOARD_PORT", "18789"))
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--wait-profile", default="", help="Wait for the live VSS profile to become ready after hook launch")
    args = parser.parse_args(argv)

    _load_env_file(Path(args.env_file))
    prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "nemoclaw_prompt.md").write_text(prompt, encoding="utf-8")

    sandbox_name = os.environ.get("NEMOCLAW_SANDBOX_NAME", "demo")
    hooks_token = _read_hooks_token()
    hooks_path = "/" + os.environ.get("OPENCLAW_HOOKS_PATH", "/hooks").strip("/")
    hook_url = f"http://127.0.0.1:{args.dashboard_port}{hooks_path}/agent"
    payload = {"name": args.name, "message": prompt}
    started = time.time()
    response: dict[str, Any] = {"status": 0, "body": "", "error": ""}
    wait_report = {"waited": False}
    if not hooks_token:
        response["error"] = "OpenClaw hooks token is not available; run the notebook setup adapter first"
    else:
        try:
            ensure_forward(str(args.dashboard_port), sandbox_name)
            response = post_hook(hook_url, hooks_token, payload, timeout=60)
            if _response_ok(response):
                wait_report = wait_for_profile(args.wait_profile, args.timeout, log_dir)
        except Exception as exc:  # Keep Harbor artifacts structured on setup failures.
            response = {
                "status": 0,
                "body": "",
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
    elapsed = time.time() - started

    report = {
        "hook_url": hook_url,
        "sandbox": sandbox_name,
        "elapsed_s": round(elapsed, 3),
        "response": response,
        "wait": wait_report,
    }
    (log_dir / "nemoclaw_hooks_response.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (log_dir / "agent.log").write_text(
        "NemoClaw/OpenClaw headless launch\n"
        f"sandbox: {sandbox_name}\n"
        f"hook_url: {hook_url}\n"
        f"response: {json.dumps(response, sort_keys=True)}\n"
        f"prompt:\n{prompt}\n",
        encoding="utf-8",
    )

    ok = _response_ok(response)
    if wait_report.get("waited"):
        ok = ok and bool(wait_report.get("ok"))
    print(json.dumps(report, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
