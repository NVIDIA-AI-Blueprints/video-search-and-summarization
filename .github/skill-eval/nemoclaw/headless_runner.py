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
import base64
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

OPENCLAW_RUN_DIR = "/tmp/vss-skill-eval-openclaw"


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


def _sandbox_exec(sandbox_name: str, script: str, *, timeout: int) -> subprocess.CompletedProcess[str]:
    encoded_script = base64.b64encode(script.encode("utf-8")).decode("ascii")
    wrapper = f"printf %s {shlex.quote(encoded_script)} | base64 -d | sh"
    if shutil_which("openshell"):
        return _run(
            ["openshell", "sandbox", "exec", "-n", sandbox_name, "--", "sh", "-lc", wrapper],
            timeout=timeout,
        )
    return _run(
        ["nemoclaw", sandbox_name, "exec", "--no-tty", "--", "sh", "-lc", wrapper],
        timeout=timeout,
    )


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


def _gateway_reachable(sandbox_name: str) -> bool:
    try:
        result = _sandbox_exec(
            sandbox_name,
            "curl -fsS http://127.0.0.1:18789/health >/dev/null",
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def ensure_openclaw_gateway(sandbox_name: str, log_dir: Path) -> None:
    if _gateway_reachable(sandbox_name):
        return

    attempts: list[str] = []
    try:
        restart = _run(
            ["nemoclaw", "sandbox", "gateway", "restart", sandbox_name],
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        restart = None
        attempts.append(f"managed restart timed out: {exc}")
    else:
        attempts.append(
            "managed restart\n"
            f"returncode={restart.returncode}\n"
            f"stdout:\n{restart.stdout or ''}\n"
            f"stderr:\n{restart.stderr or ''}"
        )

    if restart is not None and restart.returncode == 0:
        for _ in range(10):
            if _gateway_reachable(sandbox_name):
                (log_dir / "openclaw_gateway_recover.log").write_text(
                    "\n\n".join(attempts) + "\n",
                    encoding="utf-8",
                )
                return
            time.sleep(3)

    try:
        recover = _run(
            ["nemoclaw", "sandbox", "recover", sandbox_name],
            timeout=300,
        )
    except subprocess.TimeoutExpired as exc:
        attempts.append(f"sandbox recover timed out: {exc}")
    else:
        attempts.append(
            "sandbox recover\n"
            f"returncode={recover.returncode}\n"
            f"stdout:\n{recover.stdout or ''}\n"
            f"stderr:\n{recover.stderr or ''}"
        )

    for _ in range(20):
        if _gateway_reachable(sandbox_name):
            (log_dir / "openclaw_gateway_recover.log").write_text(
                "\n\n".join(attempts) + "\n",
                encoding="utf-8",
            )
            return
        time.sleep(3)
    (log_dir / "openclaw_gateway_recover.log").write_text(
        "\n\n".join(attempts) + "\n",
        encoding="utf-8",
    )
    raise RuntimeError(f"OpenClaw gateway in sandbox {sandbox_name} is not reachable")


def _openclaw_cli_command(prompt: str, timeout_s: int) -> str:
    session_id = os.environ.get("GITHUB_RUN_ID", f"ci-{int(time.time())}")
    no_proxy = "localhost,127.0.0.1,::1,10.200.0.1"
    ca_path = "/etc/openshell-tls/ca-bundle.pem"
    return (
        "unset BREV_INSTANCE NEMOCLAW_BREV_INSTANCE; "
        f"export NO_PROXY={shlex.quote(no_proxy)}; "
        f"export no_proxy={shlex.quote(no_proxy)}; "
        f"export NODE_EXTRA_CA_CERTS={shlex.quote(ca_path)}; "
        "export OPENCLAW_DISABLE_STREAMING_TOOL_CALLS=1; "
        "openclaw agent --agent main --thinking off "
        "--json "
        f"--timeout {int(timeout_s)} "
        f"--session-id {shlex.quote(session_id)} "
        f"--message {shlex.quote(prompt)}"
    )


def collect_openclaw_cli_log(sandbox_name: str, log_dir: Path) -> None:
    result = _sandbox_exec(
        sandbox_name,
        f"cat {OPENCLAW_RUN_DIR}/openclaw-agent.log 2>/dev/null || true",
        timeout=30,
    )
    (log_dir / "openclaw-agent.log").write_text(result.stdout or "", encoding="utf-8")


def _agent_json_documents(raw: str) -> list[Any]:
    """Decode OpenClaw's plain JSON or log-prefixed JSON output."""
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    documents: list[Any] = []
    index = 0
    while index < len(raw):
        if raw[index] not in "[{":
            index += 1
            continue
        try:
            parsed, end = decoder.raw_decode(raw, index)
        except json.JSONDecodeError:
            index += 1
            continue
        documents.extend(parsed if isinstance(parsed, list) else [parsed])
        index = end
    return documents


def _openclaw_visible_text(raw: str) -> list[str]:
    """Return user-visible assistant payload text from supported envelopes."""
    text: list[str] = []
    for document in _agent_json_documents(raw):
        if not isinstance(document, dict):
            continue
        status = document.get("status")
        if isinstance(status, str) and status.lower() in {"error", "failed", "failure"}:
            continue
        if document.get("error"):
            continue
        payloads = document.get("payloads")
        result = document.get("result")
        if payloads is None and isinstance(result, dict):
            result_status = result.get("status")
            if (
                isinstance(result_status, str)
                and result_status.lower() in {"error", "failed", "failure"}
            ):
                continue
            if result.get("error"):
                continue
            payloads = result.get("payloads")
        if not isinstance(payloads, list):
            continue
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            if payload.get("isError") is True or payload.get("error"):
                continue
            value = payload.get("text")
            if isinstance(value, str) and value.strip():
                text.append(value.strip())
    return text


def _openclaw_log_completed(log_dir: Path) -> bool:
    try:
        text = (log_dir / "openclaw-agent.log").read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(_openclaw_visible_text(text))


def _openclaw_cli_state(sandbox_name: str) -> str:
    result = _sandbox_exec(
        sandbox_name,
        f"if [ ! -f {OPENCLAW_RUN_DIR}/openclaw-agent.pid ]; then "
        "echo missing; "
        "else "
        f"pid=$(cat {OPENCLAW_RUN_DIR}/openclaw-agent.pid); "
        "if kill -0 \"$pid\" 2>/dev/null; then echo running; else echo stopped; fi; "
        "fi",
        timeout=30,
    )
    lines = (result.stdout or "").strip().splitlines()
    state = lines[-1].strip() if lines else "unknown"
    return state if state in {"missing", "running", "stopped"} else "unknown"


def _openclaw_cli_returncode(sandbox_name: str) -> int | None:
    result = _sandbox_exec(
        sandbox_name,
        f"cat {OPENCLAW_RUN_DIR}/openclaw-agent.rc 2>/dev/null || true",
        timeout=30,
    )
    lines = (result.stdout or "").strip().splitlines()
    if not lines:
        return None
    try:
        return int(lines[-1].strip())
    except ValueError:
        return None


def stop_openclaw_cli(sandbox_name: str) -> None:
    _sandbox_exec(
        sandbox_name,
        f"if [ -f {OPENCLAW_RUN_DIR}/openclaw-agent.pid ]; then "
        f"pid=$(cat {OPENCLAW_RUN_DIR}/openclaw-agent.pid); "
        "kill \"$pid\" 2>/dev/null || true; "
        "sleep 5; "
        "kill -9 \"$pid\" 2>/dev/null || true; "
        "fi",
        timeout=30,
    )


def _start_openclaw_cli_async(sandbox_name: str, prompt: str, timeout_s: int, log_dir: Path) -> dict[str, Any]:
    ensure_openclaw_gateway(sandbox_name, log_dir)
    inner = _openclaw_cli_command(prompt, timeout_s)
    launcher = (
        "set -u; "
        f"mkdir -p {OPENCLAW_RUN_DIR}; "
        f"rm -f {OPENCLAW_RUN_DIR}/openclaw-agent.log "
        f"{OPENCLAW_RUN_DIR}/openclaw-agent.pid {OPENCLAW_RUN_DIR}/openclaw-agent.rc "
        f"{OPENCLAW_RUN_DIR}/openclaw-agent.rc.tmp; "
        f"( sh -lc {shlex.quote(inner)}; rc=$?; "
        f"printf '%s\\n' \"$rc\" > {OPENCLAW_RUN_DIR}/openclaw-agent.rc.tmp; "
        f"mv {OPENCLAW_RUN_DIR}/openclaw-agent.rc.tmp "
        f"{OPENCLAW_RUN_DIR}/openclaw-agent.rc; "
        f"exit \"$rc\" ) > {OPENCLAW_RUN_DIR}/openclaw-agent.log 2>&1 & "
        "pid=$!; "
        f"echo $pid > {OPENCLAW_RUN_DIR}/openclaw-agent.pid; "
        "echo started"
    )
    result = _sandbox_exec(sandbox_name, launcher, timeout=60)
    (log_dir / "openclaw-launch.log").write_text(
        f"returncode={result.returncode}\nstdout:\n{result.stdout or ''}\nstderr:\n{result.stderr or ''}\n"
        "mode=async\n",
        encoding="utf-8",
    )
    return {
        "status": 200 if result.returncode == 0 else 500,
        "body": {
            "ok": result.returncode == 0,
            "mode": "cli-async",
            "returncode": result.returncode,
        },
        "stdout_tail": (result.stdout or "")[-4000:],
        "stderr_tail": (result.stderr or "")[-4000:],
        "error": "",
        "error_type": "",
    }


def run_openclaw_cli(
    sandbox_name: str,
    prompt: str,
    timeout_s: int,
    log_dir: Path,
    wait_profile: str = "",
) -> dict[str, Any]:
    fast_readiness = (
        os.environ.get("NEMOCLAW_FAST_READINESS_MODE", "").strip().lower()
        in {"1", "true", "yes"}
    )
    if wait_profile and fast_readiness:
        return _start_openclaw_cli_async(sandbox_name, prompt, timeout_s, log_dir)

    start = _start_openclaw_cli_async(sandbox_name, prompt, timeout_s, log_dir)
    if not _response_ok(start):
        start["body"]["mode"] = "cli"
        return start

    poll_sec = max(5, int(os.environ.get("NEMOCLAW_OPENCLAW_POLL_SEC", "15")))
    deadline = time.time() + timeout_s
    returncode = 124
    stdout = start.get("stdout_tail", "")
    stderr = start.get("stderr_tail", "")
    error = "OpenClaw final output was not emitted before timeout"
    error_type = "Timeout"
    completed = False
    state = "unknown"

    while time.time() < deadline:
        try:
            state = _openclaw_cli_state(sandbox_name)
        except subprocess.TimeoutExpired:
            time.sleep(poll_sec)
            continue
        if state == "stopped":
            collect_openclaw_cli_log(sandbox_name, log_dir)
            cli_returncode = _openclaw_cli_returncode(sandbox_name)
            if cli_returncode == 0 and _openclaw_log_completed(log_dir):
                returncode = 0
                error = ""
                error_type = ""
                completed = True
            elif cli_returncode == 0:
                returncode = 1
                error = "OpenClaw process exited successfully without assistant payload text"
                error_type = "OpenClawMissingOutput"
            elif cli_returncode is None:
                returncode = 1
                error = "OpenClaw process stopped without recording its exit status"
                error_type = "OpenClawMissingExitStatus"
            else:
                returncode = cli_returncode
                error = f"OpenClaw process exited with status {cli_returncode}"
                error_type = "OpenClawStopped"
            break
        if state == "missing":
            returncode = 1
            error = "OpenClaw process state files are missing"
            error_type = "OpenClawMissingState"
            break
        time.sleep(poll_sec)

    collect_openclaw_cli_log(sandbox_name, log_dir)

    with (log_dir / "openclaw-launch.log").open("a", encoding="utf-8") as handle:
        handle.write(
            f"mode=blocking-poll\nreturncode={returncode}\ncompleted={str(completed).lower()}\n"
            f"last_state={state}\nerror_type={error_type}\nerror={error}\n"
        )
    return {
        "status": 200 if returncode == 0 else 500,
        "body": {
            "ok": returncode == 0,
            "mode": "cli",
            "returncode": returncode,
        },
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
        "error": error,
        "error_type": error_type,
    }


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


def _vss_lvs_ready() -> tuple[bool, str]:
    base_ok, base_message = _vss_base_ready()
    if not base_ok:
        return base_ok, base_message
    probes = [
        ["curl", "-sf", "--max-time", "15", "http://localhost:38111/v1/ready"],
    ]
    for probe in probes:
        result = _run(probe, timeout=20)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or f"exit {result.returncode}")[-300:]
            return False, f"{' '.join(probe)} failed: {detail}"

    result = _run(["docker", "ps", "--format", "{{.Names}}"], timeout=20)
    if result.returncode != 0:
        return False, f"docker ps failed: {(result.stderr or result.stdout)[-300:]}"
    names = set(result.stdout.splitlines())
    if "vss-lvs" not in names:
        return False, "missing containers: vss-lvs"
    return True, "VSS lvs readiness probes passed"


def _profile_ready(profile: str) -> tuple[bool, str]:
    if profile == "lvs":
        return _vss_lvs_ready()
    return _vss_base_ready()


def wait_for_profile(profile: str, timeout_s: int, log_dir: Path) -> dict[str, Any]:
    if not profile:
        return {"waited": False, "reason": "no profile requested"}

    deadline = time.time() + timeout_s
    attempts: list[dict[str, Any]] = []
    while time.time() < deadline:
        ok, message = _profile_ready(profile)
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
    parser.add_argument("--launch-mode", choices=("hook", "cli"), default="hook")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--wait-profile", default="", help="Wait for the live VSS profile to become ready after hook launch")
    parser.add_argument("--expected-skill", default="", help="Fail fast if the prompt file does not reference this skill")
    args = parser.parse_args(argv)

    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    _load_env_file(Path(args.env_file))

    sandbox_name = os.environ.get("NEMOCLAW_SANDBOX_NAME", "demo")
    hooks_path = "/" + os.environ.get("OPENCLAW_HOOKS_PATH", "/hooks").strip("/")
    hook_url = f"http://127.0.0.1:{args.dashboard_port}{hooks_path}/agent"
    started = time.time()
    prompt = ""
    response: dict[str, Any] = {"status": 0, "body": "", "error": ""}
    wait_report = {"waited": False}
    try:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
        (log_dir / "nemoclaw_prompt.md").write_text(prompt, encoding="utf-8")
        expected_skill = args.expected_skill.strip()
        if expected_skill and f"`/{expected_skill}`" not in prompt and f"/{expected_skill}" not in prompt:
            raise RuntimeError(
                f"prompt file {args.prompt_file} does not reference expected "
                f"skill /{expected_skill}; refusing to launch a stale NemoClaw task"
            )

        if args.launch_mode == "cli":
            response = run_openclaw_cli(
                sandbox_name,
                prompt,
                args.timeout,
                log_dir,
                wait_profile=args.wait_profile,
            )
            try:
                if _response_ok(response):
                    wait_report = wait_for_profile(args.wait_profile, args.timeout, log_dir)
            finally:
                if args.wait_profile:
                    stop_openclaw_cli(sandbox_name)
                    collect_openclaw_cli_log(sandbox_name, log_dir)
                else:
                    collect_openclaw_cli_log(sandbox_name, log_dir)
                    stop_openclaw_cli(sandbox_name)
        else:
            hooks_token = _read_hooks_token()
            if not hooks_token:
                response["error"] = "OpenClaw hooks token is not available; run the notebook setup adapter first"
            else:
                payload = {"name": args.name, "message": prompt}
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
        "launch_mode": args.launch_mode,
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
