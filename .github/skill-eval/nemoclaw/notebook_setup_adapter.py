#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Execute setup-only subsets of the NemoClaw and VSS notebooks for CI.

The human notebooks remain the source of truth. This adapter composes their
selected stable cell ids into one temporary setup notebook, injects CI
parameter cells that read secrets from the process environment, executes it,
redacts known secret values from outputs, and persists the runtime values
needed by the headless NemoClaw launcher.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

DEFAULT_ENV_OUT = Path("/tmp/skill-eval/nemoclaw/nemoclaw.env")
DEFAULT_OUTPUT = Path("/tmp/skill-eval/nemoclaw/setup.executed.ipynb")
DEFAULT_RTSP_SAMPLE_URL = (
    "rtsp://global.stg.ga.launchpad.nvidia.com:11333/camera03"
)
NEMOCLAW_CI_RTSP_INJECTION_FLAG = "NEMOCLAW_CI_INJECT_RTSP_SAMPLE_URL"
# Covers the supported start budget, the fixed Docker probe, and three
# identity/image attestations without shortening their individual deadlines.
DIRECT_CONTAINER_PREFLIGHT_TIMEOUT_SECONDS = 660
SANDBOX_EXEC_TIMEOUT_SECONDS = 60
MANAGED_MCP_TIMEOUT_SECONDS = 300
SECRET_TEXT_PATTERNS = (
    (
        re.compile(r"(Authorization:\s*Bearer\s+)[A-Za-z0-9._~+/=-]+"),
        r"\1<redacted:OPENCLAW_HOOKS_TOKEN>",
    ),
)

PARAMETER_SOURCE = r'''
# Injected by .github/skill-eval/nemoclaw/notebook_setup_adapter.py.
# Keep values in environment variables so the executed notebook source does
# not contain API keys.
import os

def _endpoint_base_url(url):
    url = (url or "").strip().rstrip("/")
    for suffix in ("/v1/models", "/v1"):
        if url.endswith(suffix):
            url = url[:-len(suffix)].rstrip("/")
            break
    return url

def _openai_base_url(url):
    url = _endpoint_base_url(url)
    return f"{url}/v1" if url else ""

def _first_nonempty(*values):
    for value in values:
        value = str(value or "").strip()
        if value:
            return value
    return ""

def _notebook_default(name, fallback=""):
    return globals().get(name, fallback)

NGC_CLI_API_KEY = os.environ.get("NGC_CLI_API_KEY") or os.environ.get("NGC_API_KEY", "")
if NGC_CLI_API_KEY:
    os.environ.setdefault("NGC_CLI_API_KEY", NGC_CLI_API_KEY)
    os.environ.setdefault("NGC_API_KEY", NGC_CLI_API_KEY)
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
HARDWARE_PROFILE = os.environ.get(
    "HARDWARE_PROFILE",
    _notebook_default("HARDWARE_PROFILE", "RTXPRO6000BW"),
).strip()
NEMOCLAW_ENDPOINT_URL = os.environ.get(
    "NEMOCLAW_ENDPOINT_URL",
    _notebook_default("NEMOCLAW_ENDPOINT_URL", ""),
).strip()
NEMOCLAW_MODEL = os.environ.get("NEMOCLAW_MODEL", _notebook_default("NEMOCLAW_MODEL", "")).strip()
COMPATIBLE_API_KEY = os.environ.get("COMPATIBLE_API_KEY", _notebook_default("COMPATIBLE_API_KEY", "")).strip()
if not NEMOCLAW_ENDPOINT_URL:
    NEMOCLAW_ENDPOINT_URL = (
        os.environ.get("NEMOCLAW_FALLBACK_ENDPOINT_URL")
        or os.environ.get("ANTHROPIC_BASE_URL")
        or os.environ.get("LLM_REMOTE_URL")
        or ""
    ).strip()
NEMOCLAW_ENDPOINT_URL = _openai_base_url(NEMOCLAW_ENDPOINT_URL)
if not NEMOCLAW_MODEL:
    NEMOCLAW_MODEL = (
        os.environ.get("NEMOCLAW_FALLBACK_MODEL")
        or os.environ.get("ANTHROPIC_MODEL")
        or os.environ.get("LLM_REMOTE_MODEL")
        or NEMOCLAW_MODEL
        or ""
    ).strip()
if NEMOCLAW_ENDPOINT_URL and not COMPATIBLE_API_KEY:
    COMPATIBLE_API_KEY = (
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("NVIDIA_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()
NEMOCLAW_INSTALL_REF = os.environ.get(
    "NEMOCLAW_INSTALL_REF",
    _notebook_default("NEMOCLAW_INSTALL_REF", ""),
).strip()
NEMOCLAW_SANDBOX_NAME = os.environ.get("NEMOCLAW_SANDBOX_NAME", "demo").strip()
NEMOCLAW_GATEWAY_PORT = os.environ.get("NEMOCLAW_GATEWAY_PORT", "8080").strip() or "8080"
if (
    not NEMOCLAW_GATEWAY_PORT.isascii()
    or not NEMOCLAW_GATEWAY_PORT.isdigit()
    or len(NEMOCLAW_GATEWAY_PORT) > 5
    or not 1024 <= int(NEMOCLAW_GATEWAY_PORT) <= 65535
):
    raise ValueError(
        "NEMOCLAW_GATEWAY_PORT must be an integer from 1024 to 65535"
    )
NEMOCLAW_GATEWAY_NAME = (
    "nemoclaw"
    if int(NEMOCLAW_GATEWAY_PORT) == 8080
    else f"nemoclaw-{int(NEMOCLAW_GATEWAY_PORT)}"
)
os.environ["NEMOCLAW_GATEWAY_PORT"] = NEMOCLAW_GATEWAY_PORT
OPENSHELL_DOCKER_NETWORK_NAME = (
    os.environ.get("OPENSHELL_DOCKER_NETWORK_NAME", "openshell-docker").strip()
    or "openshell-docker"
)
os.environ["OPENSHELL_DOCKER_NETWORK_NAME"] = OPENSHELL_DOCKER_NETWORK_NAME
NEMOCLAW_RECREATE_SANDBOX = os.environ.get("NEMOCLAW_RECREATE_SANDBOX", "1").strip() or "1"
os.environ["NEMOCLAW_RECREATE_SANDBOX"] = NEMOCLAW_RECREATE_SANDBOX
RTSP_SAMPLE_URL = os.environ.get("RTSP_SAMPLE_URL", "").strip()
_ci_rtsp_injection = os.environ.get("NEMOCLAW_CI_INJECT_RTSP_SAMPLE_URL", "").strip()
if _ci_rtsp_injection not in ("", "1"):
    raise ValueError("NEMOCLAW_CI_INJECT_RTSP_SAMPLE_URL must be empty or 1")
if _ci_rtsp_injection == "1" and RTSP_SAMPLE_URL != "rtsp://global.stg.ga.launchpad.nvidia.com:11333/camera03":
    raise ValueError("NemoClaw CI RTSP injection requires the fixed public relay")
NEMOCLAW_CI_INJECT_RTSP_SAMPLE_URL = _ci_rtsp_injection == "1"
OPENCLAW_HOOKS_ENABLED = os.environ.get(
    "OPENCLAW_HOOKS_ENABLED",
    "0",
).lower() not in ("0", "false", "no")
OPENCLAW_HOOKS_PATH = os.environ.get(
    "OPENCLAW_HOOKS_PATH",
    os.environ.get("AGENT_HOOKS_PATH", _notebook_default("AGENT_HOOKS_PATH", "/hooks")),
).strip() or "/hooks"
AGENT_HOOKS_ENABLED = OPENCLAW_HOOKS_ENABLED
AGENT_HOOKS_PATH = OPENCLAW_HOOKS_PATH
OPENCLAW_DISABLE_STREAMING_TOOL_CALLS = os.environ.get("OPENCLAW_DISABLE_STREAMING_TOOL_CALLS", "1").strip() or "1"
os.environ["OPENCLAW_DISABLE_STREAMING_TOOL_CALLS"] = OPENCLAW_DISABLE_STREAMING_TOOL_CALLS
ORCHESTRATOR_ENABLE_HTTPS = (
    os.environ.get(
        "ORCHESTRATOR_ENABLE_HTTPS",
        str(_notebook_default("ORCHESTRATOR_ENABLE_HTTPS", False)),
    ).strip().lower()
    == "true"
)
MCP_SCHEME = "https" if ORCHESTRATOR_ENABLE_HTTPS else "http"
_orchestrator_mcp_port = os.environ.get("VSS_ORCHESTRATOR_MCP_PORT", "9988")
_orchestrator_host_alias = os.environ.get(
    "HOST_INTERNAL_ALIAS",
    str(_notebook_default("HOST_INTERNAL_ALIAS", "host.openshell.internal")),
).strip() or "host.openshell.internal"
VSS_ORCHESTRATOR_MCP_URL = os.environ.get(
    "VSS_ORCHESTRATOR_MCP_URL",
    f"{MCP_SCHEME}://{_orchestrator_host_alias}:{_orchestrator_mcp_port}/mcp",
).strip()
VSS_ORCHESTRATOR_MCP_CREDENTIAL_ENV = os.environ.get(
    "VSS_ORCHESTRATOR_MCP_CREDENTIAL_ENV",
    "",
).strip()
VSS_ORCHESTRATOR_MCP_TYPE = (
    os.environ.get("VSS_ORCHESTRATOR_MCP_TYPE", "streamable-http").strip()
    or "streamable-http"
)
MCP_URL = _notebook_default(
    "MCP_URL",
    f"{MCP_SCHEME}://127.0.0.1:{_orchestrator_mcp_port}/mcp",
)
os.environ["ORCHESTRATOR_ENABLE_HTTPS"] = (
    "true" if ORCHESTRATOR_ENABLE_HTTPS else "false"
)
os.environ["VSS_ORCHESTRATOR_MCP_URL"] = VSS_ORCHESTRATOR_MCP_URL
os.environ["VSS_ORCHESTRATOR_MCP_TYPE"] = VSS_ORCHESTRATOR_MCP_TYPE
if NEMOCLAW_ENDPOINT_URL:
    os.environ["NEMOCLAW_ENDPOINT_URL"] = NEMOCLAW_ENDPOINT_URL
if NEMOCLAW_MODEL:
    os.environ["NEMOCLAW_MODEL"] = NEMOCLAW_MODEL
if COMPATIBLE_API_KEY:
    os.environ["COMPATIBLE_API_KEY"] = COMPATIBLE_API_KEY

# Optional VSS endpoint/model overrides used by the orchestrator MCP server.
# Accept both the legacy VSS_* names and the skill-eval coordinator's
# *_REMOTE_* names while populating the names used by the split
# deploy_vss_orchestrator.ipynb notebook.
LLM_NAME = _first_nonempty(
    os.environ.get("LLM_NAME"),
    os.environ.get("VSS_LLM_NAME"),
    os.environ.get("LLM_REMOTE_MODEL"),
    _notebook_default("LLM_NAME", ""),
)
LLM_ENDPOINT_URL = _endpoint_base_url(_first_nonempty(
    os.environ.get("LLM_ENDPOINT_URL"),
    os.environ.get("VSS_LLM_ENDPOINT_URL"),
    os.environ.get("LLM_REMOTE_URL"),
    _notebook_default("LLM_ENDPOINT_URL", ""),
))
LLM_MODEL_TYPE = os.environ.get(
    "LLM_MODEL_TYPE",
    os.environ.get("VSS_LLM_MODEL_TYPE", _notebook_default("LLM_MODEL_TYPE", "")),
).strip()
LLM_ENABLE_THINKING = os.environ.get(
    "LLM_ENABLE_THINKING",
    os.environ.get("VSS_LLM_ENABLE_THINKING", _notebook_default("LLM_ENABLE_THINKING", "")),
).strip()
OPENAI_API_KEY = os.environ.get(
    "OPENAI_API_KEY",
    os.environ.get("VSS_OPENAI_API_KEY", _notebook_default("OPENAI_API_KEY", "")),
).strip()
VLM_NAME = _first_nonempty(
    os.environ.get("VLM_NAME"),
    os.environ.get("VSS_VLM_NAME"),
    os.environ.get("VLM_REMOTE_MODEL"),
    _notebook_default("VLM_NAME", ""),
)
VLM_ENDPOINT_URL = _endpoint_base_url(_first_nonempty(
    os.environ.get("VLM_ENDPOINT_URL"),
    os.environ.get("VSS_VLM_ENDPOINT_URL"),
    os.environ.get("VLM_REMOTE_URL"),
    _notebook_default("VLM_ENDPOINT_URL", ""),
))
VLM_MODEL_TYPE = os.environ.get(
    "VLM_MODEL_TYPE",
    os.environ.get("VSS_VLM_MODEL_TYPE", _notebook_default("VLM_MODEL_TYPE", "")),
).strip()
LLM_DEVICE_ID = os.environ.get("LLM_DEVICE_ID", _notebook_default("LLM_DEVICE_ID", "")).strip()
VLM_DEVICE_ID = os.environ.get("VLM_DEVICE_ID", _notebook_default("VLM_DEVICE_ID", "")).strip()
EXTERNAL_IP = os.environ.get("EXTERNAL_IP", _notebook_default("EXTERNAL_IP", "")).strip()
'''.strip() + "\n"

PERSIST_SOURCE = r'''
# Persist runtime values for the headless Harbor/NemoClaw launcher.
import os
import shlex
from pathlib import Path

_env_out = Path(os.environ.get("NEMOCLAW_CI_ENV_OUT", "/tmp/skill-eval/nemoclaw/nemoclaw.env"))
_env_out.parent.mkdir(parents=True, exist_ok=True)
_token_file = Path(os.environ.get("NEMOCLAW_HOOKS_TOKEN_FILE", str(Path.home() / ".cache/vss-skill-eval/nemoclaw/hooks_token")))
_token_file.parent.mkdir(parents=True, exist_ok=True)
_keys = [
    "NEMOCLAW_SANDBOX_NAME",
    "NEMOCLAW_GATEWAY_PORT",
    "NEMOCLAW_RECREATE_SANDBOX",
    "OPENSHELL_DOCKER_NETWORK_NAME",
    "OPENCLAW_HOOKS_PATH",
    "OPENCLAW_DISABLE_STREAMING_TOOL_CALLS",
    "ORCHESTRATOR_ENABLE_HTTPS",
    "MCP_URL",
    "MCP_PORT",
    "MCP_SSE_URL",
    "MCP_SSE_PORT",
    "OPENCLAW_MCP_URL",
    "OPENCLAW_MCP_TYPE",
    "VSS_ORCHESTRATOR_MCP_URL",
    "VSS_ORCHESTRATOR_MCP_CREDENTIAL_ENV",
    "VSS_ORCHESTRATOR_MCP_TYPE",
    "HOST_INTERNAL_ALIAS",
    "HARDWARE_PROFILE",
    "UV_NO_SYNC",
    "NEMOCLAW_HOOKS_TOKEN_FILE",
]
NEMOCLAW_HOOKS_TOKEN_FILE = str(_token_file)
_hooks_token = globals().get("AGENT_HOOKS_TOKEN") or globals().get("OPENCLAW_HOOKS_TOKEN")
if _hooks_token:
    _token_file.write_text(str(_hooks_token), encoding="utf-8")
    _token_file.chmod(0o600)
with _env_out.open("w", encoding="utf-8") as fp:
    for _key in _keys:
        if _key in globals():
            fp.write(f"export {_key}={shlex.quote(str(globals()[_key]))}\n")
print(f"Wrote NemoClaw CI env: {_env_out}")
print(f"Wrote NemoClaw hook token file: {_token_file}")
'''.strip() + "\n"

DIRECT_CONTAINER_PREFLIGHT_SOURCE = rf'''
# CI-only guard for the pinned NemoClaw release's direct Docker mutation target.
import subprocess as _ci_subprocess
import sys as _ci_sys

_ci_preflight = _ci_subprocess.run(
    [
        _ci_sys.executable,
        ".github/skill-eval/nemoclaw/direct_container_preflight.py",
        "--sandbox-name",
        NEMOCLAW_SANDBOX_NAME,
    ],
    stdin=_ci_subprocess.DEVNULL,
    capture_output=True,
    text=True,
    check=False,
    timeout={DIRECT_CONTAINER_PREFLIGHT_TIMEOUT_SECONDS},
)
if _ci_preflight.returncode != 0:
    raise RuntimeError(
        (_ci_preflight.stderr or _ci_preflight.stdout or
         "NemoClaw direct-container preflight failed").strip()
    )
print(_ci_preflight.stdout.strip())
'''.strip() + "\n"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _prepare_ci_nemoclaw_environment() -> None:
    """Confirm migration only for the managed sandbox CI already recreates."""
    recreate = os.environ.get("NEMOCLAW_RECREATE_SANDBOX", "1").strip() or "1"
    os.environ["NEMOCLAW_RECREATE_SANDBOX"] = recreate
    if recreate.lower() not in ("1", "true", "yes"):
        return

    sandbox_name = os.environ.get("NEMOCLAW_SANDBOX_NAME", "demo").strip()
    confirmation_key = "NEMOCLAW_CONFIRM_LEGACY_MANAGED_RECREATE"
    if sandbox_name and not os.environ.get(confirmation_key, "").strip():
        # NemoClaw v0.0.88 requires this exact JSON list before migrating a
        # legacy managed sandbox. Confirm only CI's selected sandbox so an
        # unexpected second legacy sandbox still causes the installer to stop.
        os.environ[confirmation_key] = json.dumps([sandbox_name], separators=(",", ":"))


def _validate_ci_rtsp_environment() -> bool:
    """Validate the internal opt-in before composing or executing a notebook."""
    enabled = os.environ.get(NEMOCLAW_CI_RTSP_INJECTION_FLAG, "").strip()
    if enabled not in ("", "1"):
        raise ValueError(
            f"{NEMOCLAW_CI_RTSP_INJECTION_FLAG} must be empty or 1"
        )
    if enabled == "1" and os.environ.get("RTSP_SAMPLE_URL", "").strip() != (
        DEFAULT_RTSP_SAMPLE_URL
    ):
        raise ValueError(
            "NemoClaw CI RTSP injection requires the fixed public relay"
        )
    return enabled == "1"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fp:
        return json.load(fp)


def _code_cell(nbformat: int, source: str, cell_id: str) -> dict[str, Any]:
    cell: dict[str, Any] = {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source,
    }
    if nbformat >= 4:
        cell["id"] = cell_id
    return cell


def _normalize_cell_source(cell: dict[str, Any]) -> dict[str, Any]:
    output = deepcopy(cell)
    source = output.get("source")
    if isinstance(source, list):
        output["source"] = "".join(str(line) for line in source)
    return output


def _patch_ci_cell(cell_id: str, cell: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(cell.get("source"), str):
        return cell
    source = cell["source"]
    if cell_id == "e67f6da4":
        hooks_default = "AGENT_HOOKS_ENABLED = True     # agent webhooks (/hooks)"
        if hooks_default not in source:
            raise ValueError(
                "NemoClaw settings cell is missing the expected hooks default"
            )
        patched = deepcopy(cell)
        patched["source"] = source.replace(
            hooks_default,
            (
                "AGENT_HOOKS_ENABLED = OPENCLAW_HOOKS_ENABLED"
                "  # CLI evals do not require webhooks"
            ),
            1,
        )
        return patched
    if cell_id == "4c91fd59":
        return _patch_docker_login_cell(cell)
    if cell_id == "c13aaf5e":
        # The CI notebook starts only the VSS Orchestrator MCP component. Its
        # import graph does not use the inference-time torch dependency, whose
        # locked wheel is served from a PyTorch R2 host that the managed Brev
        # workers cannot reach. Keep the human notebook's full environment
        # intact while omitting that unused package from this bounded CI venv.
        sync_command = '["uv", "sync", "--no-dev", "--extra", "agent"]'
        if sync_command not in source:
            raise ValueError(
                "VSS orchestrator setup cell is missing the expected uv sync command"
            )
        agent_env_anchor = "agent_env = uv_env_for_agent()\n"
        if source.count(agent_env_anchor) != 1:
            raise ValueError(
                "VSS orchestrator setup cell is missing the expected agent env anchor"
            )
        patched = deepcopy(cell)
        patched_source = source.replace(
            sync_command,
            (
                '["uv", "sync", "--no-dev", "--extra", "agent", '
                '"--no-install-package", "torch"]'
            ),
            1,
        )
        # The notebook health helper uses `uv run nat ...`; freeze the venv
        # after the bounded sync so that command does not restore torch. Keep
        # the named value in notebook globals so the persisted readiness env
        # receives the same contract in its separate process.
        patched["source"] = patched_source.replace(
            agent_env_anchor,
            (
                'UV_NO_SYNC = "1"\n'
                'os.environ["UV_NO_SYNC"] = UV_NO_SYNC\n'
                + agent_env_anchor
            ),
            1,
        )
        return patched
    if cell_id == "s31-code":
        runtime_onboard = (
            'onboard_cmd = "nemohermes onboard --non-interactive" '
            'if AGENT_RUNTIME == "hermes" else '
            'f"nemoclaw onboard --non-interactive --agent {AGENT_RUNTIME}"'
        )
        runtime_fresh = runtime_onboard.replace(
            "nemoclaw onboard --non-interactive",
            "nemoclaw onboard --fresh --non-interactive",
        )
        legacy_onboard = (
            "!cd ~ && nemoclaw onboard --non-interactive --agent {AGENT_RUNTIME}"
        )
        legacy_fresh = (
            "!cd ~ && nemoclaw onboard --fresh --non-interactive "
            "--agent {AGENT_RUNTIME}"
        )
        patched = deepcopy(cell)
        if runtime_onboard in source:
            patched["source"] = source.replace(
                runtime_onboard,
                runtime_fresh,
                1,
            )
        elif legacy_onboard in source:
            patched["source"] = source.replace(
                legacy_onboard,
                legacy_fresh,
                1,
            )
        else:
            raise ValueError(
                "NemoClaw setup cell is missing the expected onboard command"
            )
        return patched
    if cell_id == "s35-code":
        legacy_workspace_cleanup = (
            '    !nemoclaw sandbox exec {NEMOCLAW_SANDBOX_NAME} -- sh -c '
            '"find /sandbox/.openclaw/workspace -mindepth 1 -maxdepth 1 '
            "-type d -name '*.md' -exec rm -rf '{{}}' ';'\"\n"
        )
        runtime_workspace_cleanup = (
            "    !openshell sandbox exec -n {NEMOCLAW_SANDBOX_NAME} -- sh -c "
            '"mkdir -p {WORKSPACE_REMOTE_DIR} && find {WORKSPACE_REMOTE_DIR} '
            "-mindepth 1 -maxdepth 1 -type d -name '*.md' "
            "-exec rm -rf '{{}}' ';'\"\n"
        )
        if runtime_workspace_cleanup in source:
            workspace_cleanup = runtime_workspace_cleanup
        elif legacy_workspace_cleanup in source:
            workspace_cleanup = legacy_workspace_cleanup
        else:
            raise ValueError(
                "NemoClaw workspace cell is missing the expected sandbox exec"
            )
        patched = deepcopy(cell)
        patched["source"] = source.replace(
            workspace_cleanup,
            "\n".join(
                [
                    "    import subprocess as _ci_subprocess",
                    "    _ci_workspace_cleanup = _ci_subprocess.run(",
                    "        [",
                    '            "openshell", "sandbox", "exec", "--name",',
                    "            NEMOCLAW_SANDBOX_NAME,",
                    '            "-g", NEMOCLAW_GATEWAY_NAME, "--",',
                    '            "sh", "-c",',
                    "            'mkdir -p \"$1\" && find \"$1\" '",
                    "            '-mindepth 1 -maxdepth 1 -type d '",
                    "            '-name \"*.md\" -exec rm -rf \"{}\" \";\"',",
                    '            "workspace-cleanup", str(WORKSPACE_REMOTE_DIR),',
                    "        ],",
                    "        stdin=_ci_subprocess.DEVNULL,",
                    "        capture_output=True,",
                    "        text=True,",
                    "        check=False,",
                    f"        timeout={SANDBOX_EXEC_TIMEOUT_SECONDS},",
                    "    )",
                    "    if _ci_workspace_cleanup.returncode != 0:",
                    "        raise RuntimeError(",
                    '            "sandbox workspace cleanup failed with exit "',
                    "            f\"{_ci_workspace_cleanup.returncode}\"",
                    "        )",
                    "",
                ]
            ),
            1,
        )
        return patched
    if cell_id == "s36-code":
        exec_command = (
            'cmd = ["nemoclaw", "sandbox", "exec", '
            'NEMOCLAW_SANDBOX_NAME, "--", *args]'
        )
        if exec_command not in source:
            raise ValueError(
                "NemoClaw MCP cell is missing the expected sandbox exec command"
            )
        patched = deepcopy(cell)
        patched_source = source.replace(
            exec_command,
            'cmd = ["openshell", "sandbox", "exec", "--name", '
            'NEMOCLAW_SANDBOX_NAME,\n'
            '           "-g", NEMOCLAW_GATEWAY_NAME, "--", *args]',
            1,
        )
        exec_runner = (
            "result = subprocess.run(cmd, capture_output=True, text=True)"
        )
        if exec_runner not in patched_source:
            raise ValueError(
                "NemoClaw MCP cell is missing the expected subprocess runner"
            )
        patched_source = patched_source.replace(
            exec_runner,
            "\n".join(
                [
                    "result = subprocess.run(",
                    "        cmd,",
                    "        stdin=subprocess.DEVNULL,",
                    "        capture_output=True,",
                    "        text=True,",
                    "        check=False,",
                    f"        timeout={SANDBOX_EXEC_TIMEOUT_SECONDS},",
                    "    )",
                ]
            ),
            1,
        )
        runtime_anchors = (
            'if AGENT_RUNTIME == "hermes":',
            'elif AGENT_RUNTIME == "openclaw":',
            'cmd = ["nemohermes", NEMOCLAW_SANDBOX_NAME, *args]',
            'def classify_hermes_mcp_status(',
            '"--json", "--no-probe",',
            '"mcp", "add", "vss_orchestrator",',
            '"--env", credential_env,',
            '"shields", "down",',
            'finally:',
        )
        missing_runtime_anchors = [
            anchor for anchor in runtime_anchors if anchor not in patched_source
        ]
        if missing_runtime_anchors:
            raise ValueError(
                "NemoClaw MCP cell is missing the native Hermes/OpenClaw "
                f"runtime anchors: {missing_runtime_anchors}"
            )
        patched["source"] = patched_source
        return patched
    if cell_id == "s37-code":
        patched = deepcopy(cell)
        legacy_header = (
            "# Only the webhook keys need config set:\n"
            "# - gateway.* is off-limits (auth tokens) — UI allowedOrigins come from\n"
            "#   CHAT_UI_URL baked at onboard (section 3.1);\n"
            "# - agents.defaults.workspace already defaults to ~/.openclaw/workspace,\n"
            "#   which is /sandbox/.openclaw/workspace under the sandbox HOME.\n"
            "# Write keys first, then restart separately. `config set --restart` can update\n"
            "# the config and still exit non-zero with SUPERVISOR_UNAVAILABLE when the\n"
            "# in-sandbox supervisor is gone; recover relaunches it.\n"
        )
        runtime_header = (
            "# Only the webhook keys need config set:\n"
            "# - gateway.* is off-limits (auth tokens) — UI allowedOrigins come from\n"
            "#   CHAT_UI_URL baked at onboard (section 3.1);\n"
            "# - workspace docs are uploaded in section 3.4.\n"
            "# Write keys first, then restart separately. `config set --restart` can update\n"
            "# the config and still exit non-zero with SUPERVISOR_UNAVAILABLE when the\n"
            "# in-sandbox supervisor is gone; recover relaunches it.\n"
        )
        config_anchor = "config_sets = []\n"
        if runtime_header in source:
            header = runtime_header
        elif legacy_header in source:
            header = legacy_header
        else:
            header = ""
        if not header or config_anchor not in source:
            raise ValueError(
                "NemoClaw config cell is missing the expected config anchors"
            )
        patched_source = source.replace(
            header,
            (
                "# Apply supported CI sandbox settings through NemoClaw's managed\n"
                "# config path, then restart the gateway once after all writes.\n"
            ),
            1,
        ).replace(
            config_anchor,
            (
                "config_sets = []\n"
                "if NEMOCLAW_CI_INJECT_RTSP_SAMPLE_URL:\n"
                "    config_sets.append((\"env.vars.RTSP_SAMPLE_URL\", RTSP_SAMPLE_URL))\n"
            ),
            1,
        )
        no_config_statuses = (
            "No sandbox config changes needed (webhooks disabled or Hermes runtime).",
            "No sandbox config changes needed (webhooks disabled).",
        )
        for old in no_config_statuses:
            if old in patched_source:
                patched_source = patched_source.replace(
                    old,
                    "No sandbox config changes needed.",
                    1,
                )
                break
        else:
            raise ValueError(
                "NemoClaw config cell is missing expected status text"
            )
        status_replacements = (
            (
                "Restarting gateway to apply webhook config...",
                "Restarting gateway to apply sandbox config...",
            ),
            (
                "sandbox recover failed after webhook config",
                "sandbox recover failed after sandbox config",
            ),
        )
        for old, new in status_replacements:
            if old not in patched_source:
                raise ValueError(
                    "NemoClaw config cell is missing expected status text"
                )
            patched_source = patched_source.replace(old, new, 1)
        config_loop = "else:\n    for _key, _value in config_sets:\n"
        if config_loop not in patched_source:
            raise ValueError(
                "NemoClaw config cell is missing the expected config write loop"
            )
        patched["source"] = patched_source.replace(
            config_loop,
            "\n".join(
                [
                    "else:",
                    "    # Re-attest and reactivate the exact container before",
                    "    # NemoClaw's direct privileged config mutation path.",
                    "    import subprocess as _ci_subprocess",
                    "    import sys as _ci_sys",
                    "    _ci_config_preflight = _ci_subprocess.run(",
                    "        [",
                    "            _ci_sys.executable,",
                    '            ".github/skill-eval/nemoclaw/'
                    'direct_container_preflight.py",',
                    '            "--sandbox-name",',
                    "            NEMOCLAW_SANDBOX_NAME,",
                    "        ],",
                    "        stdin=_ci_subprocess.DEVNULL,",
                    "        capture_output=True,",
                    "        text=True,",
                    "        check=False,",
                    "        timeout="
                    f"{DIRECT_CONTAINER_PREFLIGHT_TIMEOUT_SECONDS},",
                    "    )",
                    "    if _ci_config_preflight.returncode != 0:",
                    "        raise RuntimeError(",
                    "            (_ci_config_preflight.stderr or",
                    "             _ci_config_preflight.stdout or",
                    '             "NemoClaw config preflight failed").strip()',
                    "        )",
                    "    for _key, _value in config_sets:",
                    "",
                ]
            ),
            1,
        )
        return patched
    if cell_id != "run-code":
        return cell
    optional_forward = "ensure_openshell_forward(9090, NEMOCLAW_SANDBOX_NAME)"
    if optional_forward not in source:
        return cell
    patched = deepcopy(cell)
    patched["source"] = source.replace(
        optional_forward,
        "\n".join(
            [
                "try:",
                "    ensure_openshell_forward(9090, NEMOCLAW_SANDBOX_NAME)",
                "except RuntimeError as exc:",
                "    print(f\"WARNING: optional OpenShell forward 9090 skipped in CI: {exc}\", flush=True)",
            ]
        ),
    )
    return patched


def _patch_docker_login_cell(cell: dict[str, Any]) -> dict[str, Any]:
    source = cell["source"]
    strict_block = (
        'if login_result.returncode != 0:\n'
        '    raise RuntimeError(f"Docker login to nvcr.io failed\\n{login_result.stderr}")\n'
        '\n'
        'print("Docker login to nvcr.io: OK")'
    )
    if strict_block not in source:
        return cell
    patched = deepcopy(cell)
    patched["source"] = source.replace(
        strict_block,
        "\n".join(
            [
                "if login_result.returncode != 0:",
                '    print(f"WARNING: Docker login to nvcr.io failed; continuing in CI. stderr tail:\\n{login_result.stderr[-1000:]}")',
                '    print("The deployment step will still use cached images or fail with a concrete pull error if registry access is required.")',
                "else:",
                '    print("Docker login to nvcr.io: OK")',
            ]
        ),
    )
    return patched


def build_notebook(source_nb: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a setup-only notebook assembled from stable cell ids."""
    cells_by_id = {cell.get("id"): cell for cell in source_nb.get("cells", [])}
    missing = [cell_id for cell_id in manifest["cells"] if cell_id not in cells_by_id]
    if missing:
        raise ValueError(f"Notebook is missing configured cell ids: {', '.join(missing)}")

    output = deepcopy(source_nb)
    output["cells"] = []
    insert_before = manifest.get("insert_parameters_before")
    inserted = False
    nbformat = int(output.get("nbformat", 4))

    for cell_id in manifest["cells"]:
        if cell_id == insert_before and not inserted:
            output["cells"].append(_code_cell(nbformat, PARAMETER_SOURCE, "ci-parameters"))
            inserted = True
        output["cells"].append(
            _patch_ci_cell(
                cell_id,
                _normalize_cell_source(cells_by_id[cell_id]),
            )
        )
        if cell_id == "s31-code":
            output["cells"].append(
                _code_cell(
                    nbformat,
                    DIRECT_CONTAINER_PREFLIGHT_SOURCE,
                    "ci-direct-container-preflight",
                )
            )

    if not inserted:
        output["cells"].append(_code_cell(nbformat, PARAMETER_SOURCE, "ci-parameters"))
    output["cells"].append(_code_cell(nbformat, PERSIST_SOURCE, "ci-persist-env"))
    return output


def build_notebooks(source_nbs: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, Any]:
    """Combine selected cells from an ordered list of source notebooks."""
    notebook_manifests = manifest.get("notebooks", [])
    if len(source_nbs) != len(notebook_manifests):
        raise ValueError(
            "Source notebook count does not match manifest: "
            f"{len(source_nbs)} != {len(notebook_manifests)}"
        )
    if not source_nbs:
        raise ValueError("Manifest must select at least one notebook")

    output = deepcopy(source_nbs[0])
    output["cells"] = []
    nbformat = int(output.get("nbformat", 4))
    for index, (source_nb, notebook_manifest) in enumerate(
        zip(source_nbs, notebook_manifests, strict=True),
        start=1,
    ):
        selected = build_notebook(source_nb, notebook_manifest)
        for cell in selected["cells"]:
            cell_id = cell.get("id")
            if cell_id == "ci-persist-env":
                continue
            if cell_id == "ci-parameters":
                cell = deepcopy(cell)
                cell["id"] = f"ci-parameters-{index}"
            output["cells"].append(cell)
    output["cells"].append(_code_cell(nbformat, PERSIST_SOURCE, "ci-persist-env"))
    return output


def _redaction_values() -> dict[str, str]:
    keys = (
        "NGC_CLI_API_KEY",
        "NVIDIA_API_KEY",
        "COMPATIBLE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "VSS_OPENAI_API_KEY",
        "OPENCLAW_HOOKS_TOKEN",
        "RTSP_SAMPLE_URL",
    )
    values = {key: value for key in keys if (value := os.environ.get(key))}
    credential_env = os.environ.get(
        "VSS_ORCHESTRATOR_MCP_CREDENTIAL_ENV",
        "",
    ).strip()
    if credential_env and (credential_value := os.environ.get(credential_env)):
        values[credential_env] = credential_value
    return values


def _redact(obj: Any, values: dict[str, str]) -> Any:
    if isinstance(obj, str):
        redacted = obj
        for key, value in values.items():
            if value:
                redacted = redacted.replace(value, f"<redacted:{key}>")
        for pattern, replacement in SECRET_TEXT_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        return redacted
    if isinstance(obj, list):
        return [_redact(item, values) for item in obj]
    if isinstance(obj, dict):
        return {key: _redact(value, values) for key, value in obj.items()}
    return obj


def _write_notebook_snapshot(notebook: Any, output_path: Path) -> None:
    try:
        import nbformat
    except ImportError:
        output = notebook
    else:
        if not isinstance(notebook, dict):
            output = json.loads(nbformat.writes(notebook))
        else:
            output = notebook
    output_path.write_text(
        json.dumps(_redact(output, _redaction_values()), indent=1),
        encoding="utf-8",
    )


def execute_notebook(
    notebook: dict[str, Any],
    *,
    cwd: Path,
    timeout: int,
    output_path: Path | None = None,
) -> dict[str, Any]:
    try:
        import nbformat
        from nbclient import NotebookClient
    except ImportError as exc:
        raise RuntimeError(
            "Notebook execution requires nbformat and nbclient. Install with: "
            "python3 -m pip install nbformat nbclient ipykernel"
        ) from exc

    nb = nbformat.from_dict(notebook)
    client = NotebookClient(
        nb,
        timeout=timeout,
        kernel_name=os.environ.get("NEMOCLAW_CI_KERNEL", "python3"),
        allow_errors=False,
        resources={"metadata": {"path": str(cwd)}},
    )
    try:
        client.execute()
    except Exception as exc:
        if output_path is not None:
            _write_notebook_snapshot(nb, output_path)
            print(
                f"Wrote partial setup notebook after {type(exc).__name__}: {output_path}",
                file=sys.stderr,
                flush=True,
            )
        raise
    return json.loads(nbformat.writes(nb))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = _repo_root()
    default_manifest = root / ".github" / "skill-eval" / "nemoclaw" / "notebook_cells.json"
    parser.add_argument(
        "--notebook",
        default=None,
        help="Override the source path for a legacy single-notebook manifest",
    )
    parser.add_argument("--manifest", default=str(default_manifest), help="Cell sidecar manifest")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Executed notebook output path")
    parser.add_argument("--env-out", default=str(DEFAULT_ENV_OUT), help="Runtime env file written by the injected persist cell")
    parser.add_argument("--execute", action="store_true", help="Execute the temporary notebook")
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("NEMOCLAW_SETUP_CELL_TIMEOUT", "3600")))
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest).resolve()
    manifest = _load_json(manifest_path)
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("VSS_REPO_DIR", str(root))
    os.environ["NEMOCLAW_CI_ENV_OUT"] = str(Path(args.env_out).resolve())
    _validate_ci_rtsp_environment()
    _prepare_ci_nemoclaw_environment()

    if "notebooks" in manifest:
        if args.notebook:
            raise ValueError("--notebook cannot override a multi-notebook manifest")
        source_nbs = [
            _load_json((root / item["notebook"]).resolve())
            for item in manifest["notebooks"]
        ]
        temp_nb = build_notebooks(source_nbs, manifest)
    else:
        notebook_path = Path(args.notebook or (root / manifest["notebook"])).resolve()
        temp_nb = build_notebook(_load_json(notebook_path), manifest)
    if args.execute:
        temp_nb = execute_notebook(
            temp_nb,
            cwd=root,
            timeout=args.timeout,
            output_path=output_path,
        )
    _write_notebook_snapshot(temp_nb, output_path)
    print(f"Wrote setup notebook: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
