# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Brev environment that adds the opt-in NemoClaw notebook setup."""

from __future__ import annotations

import base64
import logging
import os
import shlex
from pathlib import Path

from envs.brev_env import BrevEnvironment, _run_brev_exec

logger = logging.getLogger(__name__)

_SETUP_KEYS = (
    "NGC_CLI_API_KEY",
    "NGC_API_KEY",
    "NVIDIA_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "OPENAI_API_KEY",
    "COMPATIBLE_API_KEY",
    "LLM_REMOTE_URL",
    "LLM_REMOTE_MODEL",
    "VLM_REMOTE_URL",
    "VLM_REMOTE_MODEL",
    "PR_HEAD_SHA",
    "PR_REPO",
    "GITHUB_RUN_ID",
    "NEMOCLAW_INSTALL_REF",
    "NEMOCLAW_SANDBOX_NAME",
    "NEMOCLAW_GATEWAY_PORT",
    "HARDWARE_PROFILE",
    "HOST_INTERNAL_ALIAS",
    "VSS_ORCHESTRATOR_MCP_PORT",
    "VSS_ORCHESTRATOR_MCP_URL",
    "NEMOCLAW_AGENT_TIMEOUT_SEC",
    "RTSP_SAMPLE_URL",
)


def _bounded_setup_timeout() -> int:
    value = int(os.environ.get("NEMOCLAW_SETUP_TIMEOUT_SEC", "5400"))
    if not 300 <= value <= 7200:
        raise ValueError("NEMOCLAW_SETUP_TIMEOUT_SEC must be 300..7200")
    return value


def _forwarded_nemoclaw_env() -> str:
    values: list[tuple[str, str]] = []
    for key in _SETUP_KEYS:
        value = os.environ.get(key)
        if value is not None:
            values.append((key, value))
    # These empty values are intentional on the one-GPU representative task.
    values.extend(
        [
            ("AGENT_RUNTIME", "openclaw"),
            ("ORCHESTRATOR_ENABLE_HTTPS", "false"),
            ("LLM_DEVICE_ID", ""),
            ("VLM_DEVICE_ID", ""),
        ]
    )
    return "\n".join(f"export {key}={shlex.quote(value)}" for key, value in values)


def _setup_command(timeout: int, required_tools: list[str]) -> str:
    # Reserve 10 minutes for the venv and 10 for readiness, plus five
    # minutes of command/transport headroom inside the total setup budget.
    adapter_timeout = max(300, timeout - 1500)
    required_tools_csv = ",".join(required_tools)
    return f"""
set -e
set +u
. "$HOME/.profile" 2>/dev/null || true
set -u
. "$HOME/.eval_env"
cd "$HOME/video-search-and-summarization"
scratch=/tmp/skill-eval/nemoclaw
venv="$scratch/notebook-venv"
rm -rf "$venv"
mkdir -p "$scratch"
# Reuse #925's narrow warm-worker repair: a prior sudo-run gateway can leave
# only its SQLite files root-owned. Refuse symlinks and unexpected owners;
# never recurse through the user's state tree.
gateway_state="$HOME/.local/state/nemoclaw/openshell-docker-gateway"
if [ -d "$gateway_state" ]; then
  for state_path in \
    "$HOME/.local" \
    "$HOME/.local/state" \
    "$HOME/.local/state/nemoclaw" \
    "$gateway_state"; do
    [ ! -L "$state_path" ] || {{
      echo "Refusing symlinked OpenShell state path: $state_path" >&2
      exit 1
    }}
  done
  current_uid=$(id -u)
  current_gid=$(id -g)
  for db_name in openshell.db openshell.db-wal openshell.db-shm; do
    db_path="$gateway_state/$db_name"
    [ -e "$db_path" ] || continue
    [ ! -L "$db_path" ] || {{
      echo "Refusing symlinked OpenShell database: $db_path" >&2
      exit 1
    }}
    db_uid=$(stat -c %u -- "$db_path")
    [ "$db_uid" != "0" ] || \
      sudo -n chown --no-dereference "$current_uid:$current_gid" -- "$db_path"
    db_uid=$(stat -c %u -- "$db_path")
    [ "$db_uid" = "$current_uid" ] || {{
      echo "Unexpected OpenShell database owner $db_uid: $db_path" >&2
      exit 1
    }}
  done
fi
export PATH="$HOME/.local/bin:$PATH"
# The OpenShell gateway is a host process. A previous generic Docker reset
# may have pruned its bridge while leaving the listener alive. Reuse #925's
# scoped lifecycle helper so onboarding can recreate the bridge; a free port
# needs no repair.
gateway_port="${{NEMOCLAW_GATEWAY_PORT:-8080}}"
case "$gateway_port" in
  ""|*[!0-9]*)
    echo "Invalid NEMOCLAW_GATEWAY_PORT: $gateway_port" >&2
    exit 1
    ;;
esac
if [ "$gateway_port" -lt 1024 ] || [ "$gateway_port" -gt 65535 ]; then
  echo "NEMOCLAW_GATEWAY_PORT is outside 1024-65535: $gateway_port" >&2
  exit 1
fi
gateway_port_is_free() {{
  python3 - "$gateway_port" <<'__NEMOCLAW_PORT_PROBE__'
import socket
import sys

with socket.socket() as probe:
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("127.0.0.1", int(sys.argv[1])))
    except OSError:
        raise SystemExit(1)
__NEMOCLAW_PORT_PROBE__
}}
openshell_network_names="$(docker network ls \
  --filter type=custom --format '{{{{.Name}}}}')" || {{
  echo "Failed to inspect the OpenShell Docker network before setup" >&2
  exit 1
}}
if ! printf '%s\n' "$openshell_network_names" | grep -Fxq openshell-docker; then
  if gateway_port_is_free; then
    echo "OpenShell bridge is absent; fresh onboarding will recreate it"
  else
    gateway_release_module="$HOME/.nemoclaw/source/dist/lib/tunnel/gateway-port-release.js"
    gateway_release_status=0
    if command -v node >/dev/null 2>&1 && \
       [ -f "$gateway_release_module" ]; then
      (
        GATEWAY_RELEASE_MODULE="$gateway_release_module" \
        GATEWAY_RELEASE_PORT="$gateway_port" \
          node <<'__NEMOCLAW_GATEWAY_RELEASE__'
const modulePath = process.env.GATEWAY_RELEASE_MODULE;
const port = Number(process.env.GATEWAY_RELEASE_PORT);
const runtime = require(modulePath);
const result = runtime.releaseManagedGatewayPort({{
  port,
  confirmTimeoutMs: 5000,
  confirmPollIntervalMs: 100,
}});
if (result && result.skipped === true) {{
  process.exit(42);
}}
if (!result || result.released !== true) {{
  console.error(
    `Scoped NemoClaw gateway release failed for port ${{port}}: ` +
      JSON.stringify(result),
  );
  process.exit(1);
}}
__NEMOCLAW_GATEWAY_RELEASE__
      ) || gateway_release_status=$?
    else
      gateway_release_status=1
    fi
    if [ "$gateway_release_status" -eq 42 ]; then
      echo "Cannot safely release stale OpenShell gateway: lifecycle authority refused" >&2
      exit 1
    fi
    if [ "$gateway_release_status" -ne 0 ]; then
      if ! [ -x /usr/bin/lsof ] && ! [ -x /usr/sbin/lsof ] && \
         ! [ -x /bin/lsof ] && ! [ -x /sbin/lsof ]; then
        if ! command -v apt-get >/dev/null 2>&1 || \
           ! sudo -n true >/dev/null 2>&1; then
          echo "Cannot install trusted lsof for scoped gateway recovery" >&2
          exit 1
        fi
        sudo -n apt-get update -qq
        sudo -n /usr/bin/env DEBIAN_FRONTEND=noninteractive \
          apt-get install -y -qq lsof
      fi
      /usr/bin/python3 \
        .github/skill-eval/nemoclaw/release_gateway_port.py \
        --port "$gateway_port"
    fi
    gateway_port_is_free || {{
      echo "OpenShell gateway port $gateway_port remains busy after recovery" >&2
      exit 1
    }}
    echo "Released stale OpenShell gateway; onboarding will recreate its bridge"
  fi
fi
if ! command -v uv >/dev/null 2>&1; then
  timeout --signal=TERM --kill-after=30 300s \
    sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
  export PATH="$HOME/.local/bin:$PATH"
fi
# Some Brev images still default to Python 3.10, while the current notebook
# helpers use StrEnum. Run the notebook with the repo's CI Python contract.
# Reinstall without uv's download cache because warm workers can retain a
# partially written managed interpreter after an interrupted prior setup.
timeout --signal=TERM --kill-after=30 600s \
  uv python install --reinstall --force --no-cache 3.12
timeout --signal=TERM --kill-after=30 600s \
  uv venv --managed-python --python 3.12 --clear "$venv"
timeout --signal=TERM --kill-after=30 600s \
  uv pip install --python "$venv/bin/python" \
    nbformat nbclient ipykernel
"$venv/bin/python" -m ipykernel install --user \
  --name nemoclaw-skill-eval --display-name "NemoClaw skill eval"
export NEMOCLAW_CI_KERNEL=nemoclaw-skill-eval
export NEMOCLAW_SETUP_CELL_TIMEOUT_SEC={adapter_timeout}
timeout --signal=TERM --kill-after=120 {adapter_timeout}s \
  "$venv/bin/python" \
  .github/skill-eval/nemoclaw/notebook_setup_adapter.py \
  --execute \
  --env-out "$scratch/nemoclaw.env" \
  --timeout "$NEMOCLAW_SETUP_CELL_TIMEOUT_SEC"
timeout --signal=TERM --kill-after=30 600s \
  "$venv/bin/python" \
  .github/skill-eval/nemoclaw/readiness.py \
  --env-file "$scratch/nemoclaw.env" \
  --required-tools {shlex.quote(required_tools_csv)}
""".strip()


class NemoClawBrevEnvironment(BrevEnvironment):
    """Run normal Brev preparation, then the notebook-derived setup once."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._nemoclaw_ready = False

    async def _reset_docker_runtime(self) -> None:
        """Reset the worker while retaining a valid OpenShell bridge.

        OpenShell's Docker gateway runs on the host. The base reset removes
        unused custom networks, which can strand that live gateway with a
        recorded route to a deleted ``openshell-docker`` bridge. This is the
        scoped preservation behavior already proven in #925: only the exact
        OpenShell-managed IPv4 bridge is allowed to survive.
        """
        cmd = r"""set -euo pipefail
docker info >/dev/null 2>&1 || { echo "docker daemon unreachable" >&2; exit 1; }
cids=$(docker ps -aq) || { echo "failed to enumerate docker containers" >&2; exit 1; }
if [ -n "$cids" ]; then
  docker rm -f $cids >/dev/null || {
    echo "failed to remove docker containers during reset" >&2
    exit 1
  }
fi
vols=$(docker volume ls -q) || { echo "failed to enumerate docker volumes" >&2; exit 1; }
if [ -n "$vols" ]; then
  docker volume rm -f $vols >/dev/null || {
    echo "failed to remove docker volumes during reset" >&2
    exit 1
  }
fi
validate_openshell_network() {
  network_id="$1"
  network_driver=$(docker network inspect --format '{{.Driver}}' "$network_id") || {
    echo "failed to inspect driver for OpenShell network $network_id" >&2
    return 1
  }
  network_owner=$(docker network inspect \
    --format '{{index .Labels "openshell.ai/managed-by"}}' "$network_id") || {
    echo "failed to inspect owner for OpenShell network $network_id" >&2
    return 1
  }
  network_gateways=$(docker network inspect \
    --format '{{range .IPAM.Config}}{{.Gateway}} {{end}}' "$network_id") || {
    echo "failed to inspect IPAM for OpenShell network $network_id" >&2
    return 1
  }
  [ "$network_driver" = "bridge" ] || {
    echo "refusing to preserve OpenShell network with driver $network_driver" >&2
    return 1
  }
  [ "$network_owner" = "openshell" ] || {
    echo "refusing to preserve OpenShell network without managed ownership label" >&2
    return 1
  }
  has_ipv4_gateway=0
  for network_gateway in $network_gateways; do
    if python3 -c \
      'import ipaddress,sys; raise SystemExit(ipaddress.ip_address(sys.argv[1]).version != 4)' \
      "$network_gateway"; then
      has_ipv4_gateway=1
      break
    fi
  done
  [ "$has_ipv4_gateway" = "1" ] || {
    echo "refusing to preserve OpenShell network without an IPv4 IPAM gateway" >&2
    return 1
  }
}
network_ids=$(docker network ls --filter type=custom -q) || {
  echo "failed to enumerate docker networks during reset" >&2
  exit 1
}
for network_id in $network_ids; do
  network_name=$(docker network inspect --format '{{.Name}}' "$network_id") || {
    echo "failed to inspect docker network $network_id during reset" >&2
    exit 1
  }
  if [ "$network_name" = "openshell-docker" ]; then
    validate_openshell_network "$network_id"
    continue
  fi
  docker network rm "$network_id" >/dev/null || {
    echo "failed to remove docker network $network_name ($network_id)" >&2
    exit 1
  }
done
docker info >/dev/null 2>&1 || { echo "docker daemon died during reset" >&2; exit 1; }
rc=$(docker ps -aq | wc -l | tr -d ' ')
rv=$(docker volume ls -q | wc -l | tr -d ' ')
rn=0
surviving_network_ids=$(docker network ls --filter type=custom -q) || {
  echo "failed to enumerate surviving docker networks" >&2
  exit 1
}
for network_id in $surviving_network_ids; do
  network_name=$(docker network inspect --format '{{.Name}}' "$network_id") || {
    echo "failed to inspect surviving docker network $network_id" >&2
    exit 1
  }
  if [ "$network_name" = "openshell-docker" ]; then
    validate_openshell_network "$network_id"
  else
    rn=$((rn + 1))
  fi
done
if [ "$rc" != "0" ] || [ "$rv" != "0" ] || [ "$rn" != "0" ]; then
  echo "docker runtime reset incomplete: ${rc} containers, ${rv} volumes, ${rn} unexpected user-defined networks remain" >&2
  exit 1
fi
echo "docker runtime reset OK; images and valid OpenShell bridge preserved when present ($(docker images -q | wc -l | tr -d ' ') layers)"
"""
        logger.info(
            "Resetting Docker runtime while preserving the validated "
            "OpenShell bridge on %s",
            self._instance_name,
        )
        result = await _run_brev_exec(self._instance_name, cmd, timeout=300)
        if result.return_code != 0:
            tail = (result.stderr or result.stdout or "")[-500:]
            raise RuntimeError(
                f"Docker runtime reset failed on {self._instance_name}: "
                f"exit {result.return_code}; tail:\n{tail}"
            )
        logger.info(
            "Docker reset on %s: %s",
            self._instance_name,
            (result.stdout or "").strip().splitlines()[-1]
            if result.stdout
            else "<no output>",
        )

    async def start(self, force_build: bool) -> None:
        if self._nemoclaw_ready:
            return
        await super().start(force_build)
        if self._instance_name is None:
            raise RuntimeError("NemoClaw setup requires an explicit Brev instance")

        metadata = self._read_task_metadata()
        if metadata.get("runner") != "nemoclaw":
            raise RuntimeError(
                "NemoClawBrevEnvironment requires metadata.runner='nemoclaw'"
            )

        env_block = _forwarded_nemoclaw_env()
        append = (
            "cat >> \"$HOME/.eval_env\" <<'__NEMOCLAW_ENV__'\n"
            f"{env_block}\n"
            "__NEMOCLAW_ENV__"
        )
        written = await _run_brev_exec(self._instance_name, append, timeout=30)
        if written.return_code != 0:
            raise RuntimeError("Could not forward NemoClaw setup environment")

        timeout = _bounded_setup_timeout()
        required_tools = metadata.get("required_mcp_tools") or []
        if not isinstance(required_tools, list) or not all(
            isinstance(tool, str) and tool for tool in required_tools
        ):
            raise RuntimeError(
                "NemoClaw required_mcp_tools metadata must be a list of names"
            )
        logger.info(
            "Running notebook-derived NemoClaw setup on %s (timeout=%ss)",
            self._instance_name,
            timeout,
        )
        result = await _run_brev_exec(
            self._instance_name,
            _setup_command(timeout, required_tools),
            timeout=timeout + 60,
        )
        if result.return_code != 0:
            detail = (result.stderr or result.stdout or "")[-2000:]
            raise RuntimeError(
                "NemoClaw notebook setup/readiness failed "
                f"(exit {result.return_code}):\n{detail}"
            )
        self._nemoclaw_ready = True
        logger.info("NemoClaw is ready on %s", self._instance_name)

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ):
        metadata = self._read_task_metadata()
        is_nemoclaw = metadata.get("runner") == "nemoclaw"
        is_claude_agent = "claude --verbose --output-format=stream-json" in command
        if not is_nemoclaw or not is_claude_agent:
            return await super().exec(
                command,
                cwd=cwd,
                env=env,
                timeout_sec=timeout_sec,
                user=user,
            )

        instructions = [
            value
            for key, value in (env or {}).items()
            if key.startswith("HARBOR_CLAUDE_CODE_INSTRUCTION_")
        ]
        if "headless_runner.py" not in command and not any(
            "headless_runner.py" in value for value in instructions
        ):
            raise RuntimeError(
                "NemoClaw task is missing the expected Harbor launcher instruction"
            )
        prompt_path = Path(self.environment_dir).parent / "tests" / "nemoclaw_prompt.md"
        if not prompt_path.is_file():
            raise RuntimeError(
                "NemoClaw prompt is unavailable; refusing to run outer Claude"
            )
        prompt_b64 = base64.b64encode(prompt_path.read_bytes()).decode("ascii")
        agent_timeout = int(os.environ.get("NEMOCLAW_AGENT_TIMEOUT_SEC", "3300"))
        launcher = f"""set -euo pipefail
cd "$HOME/video-search-and-summarization"
mkdir -p /tmp/skill-eval/nemoclaw /logs/agent /logs/artifacts/nemoclaw
printf %s {shlex.quote(prompt_b64)} | base64 -d > /tmp/skill-eval/nemoclaw/current_prompt.md
cat > /logs/agent/claude-code.txt <<'__NEMOCLAW__'
Harbor intentionally bypassed outer Claude for this opt-in NemoClaw task.
The task ran through OpenClaw with repository skills and VSS Orchestrator MCP.
__NEMOCLAW__
python3 .github/skill-eval/nemoclaw/headless_runner.py \
  --prompt-file /tmp/skill-eval/nemoclaw/current_prompt.md \
  --log-dir /logs/artifacts/nemoclaw \
  --agent-log-dir /logs/agent \
  --timeout {agent_timeout}
"""
        clean_env = {
            key: value
            for key, value in (env or {}).items()
            if not key.startswith("HARBOR_CLAUDE_CODE_INSTRUCTION_")
        }
        return await super().exec(
            launcher,
            cwd=cwd,
            env=clean_env,
            timeout_sec=max(timeout_sec or 0, agent_timeout + 180),
            user=user,
        )
