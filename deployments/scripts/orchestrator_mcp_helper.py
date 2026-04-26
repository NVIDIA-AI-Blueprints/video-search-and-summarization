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


def build_vss_ui_url(port: int = 7777) -> str | None:
    brev_env_id = os.environ.get("BREV_ENV_ID", "").strip() or read_etc_environment().get("BREV_ENV_ID", "").strip()
    if not brev_env_id:
        return None
    link_prefix = os.environ.get("BREV_LINK_PREFIX", "").strip() or f"{port}0"
    return f"https://{link_prefix}-{brev_env_id}.brevlab.com/"


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

    payload = json.loads(stdout)
    if show_response:
        if response_prefix:
            print(response_prefix)
        print(json.dumps(payload, indent=2))
    return payload


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
