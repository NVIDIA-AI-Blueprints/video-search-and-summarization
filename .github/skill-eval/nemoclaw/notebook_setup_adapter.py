#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compose and execute the setup cells from the checked-in NemoClaw notebooks.

The source notebooks stay unchanged. Stable cell ids select the setup path,
and an injected parameter cell reads credentials and CI overrides only from
the process environment. Executed notebooks are intentionally not persisted.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from copy import deepcopy
from pathlib import Path
from typing import Any

DEFAULT_ENV_OUT = Path("/tmp/skill-eval/nemoclaw/nemoclaw.env")

PARAMETER_SOURCE = (
    r"""
# Injected by the NemoClaw Harbor harness. Values remain in process env.
import os

def _endpoint_base_url(url):
    value = (url or "").strip().rstrip("/")
    for suffix in ("/v1/models", "/v1"):
        if value.endswith(suffix):
            value = value[:-len(suffix)].rstrip("/")
            break
    return value

def _openai_base_url(url):
    value = _endpoint_base_url(url)
    return f"{value}/v1" if value else ""

def _first_nonempty(*values):
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""

NGC_CLI_API_KEY = (
    os.environ.get("NGC_CLI_API_KEY")
    or os.environ.get("NGC_API_KEY")
    or ""
).strip()
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "").strip()
HARDWARE_PROFILE = os.environ.get("HARDWARE_PROFILE", "RTXPRO6000BW").strip()

NEMOCLAW_ENDPOINT_URL = _openai_base_url(_first_nonempty(
    os.environ.get("NEMOCLAW_ENDPOINT_URL"),
    os.environ.get("ANTHROPIC_BASE_URL"),
    os.environ.get("LLM_REMOTE_URL"),
))
NEMOCLAW_MODEL = _first_nonempty(
    os.environ.get("NEMOCLAW_MODEL"),
    os.environ.get("ANTHROPIC_MODEL"),
    os.environ.get("LLM_REMOTE_MODEL"),
)
COMPATIBLE_API_KEY = _first_nonempty(
    os.environ.get("COMPATIBLE_API_KEY"),
    os.environ.get("ANTHROPIC_API_KEY"),
    os.environ.get("OPENAI_API_KEY"),
    NVIDIA_API_KEY,
)
NEMOCLAW_INSTALL_REF = os.environ.get("NEMOCLAW_INSTALL_REF", "v0.0.103").strip()
NEMOCLAW_SANDBOX_NAME = os.environ.get(
    "NEMOCLAW_SANDBOX_NAME", "skill-eval-nemoclaw"
).strip()
NEMOCLAW_GATEWAY_PORT = os.environ.get("NEMOCLAW_GATEWAY_PORT", "8080").strip()
if not NEMOCLAW_GATEWAY_PORT.isdigit() or not 1024 <= int(NEMOCLAW_GATEWAY_PORT) <= 65535:
    raise ValueError("NEMOCLAW_GATEWAY_PORT must be between 1024 and 65535")
NEMOCLAW_GATEWAY_NAME = (
    "nemoclaw"
    if int(NEMOCLAW_GATEWAY_PORT) == 8080
    else f"nemoclaw-{int(NEMOCLAW_GATEWAY_PORT)}"
)
AGENT_RUNTIME = "openclaw"
ORCHESTRATOR_ENABLE_HTTPS = False
HOST_INTERNAL_ALIAS = os.environ.get(
    "HOST_INTERNAL_ALIAS", "host.openshell.internal"
).strip()
VSS_ORCHESTRATOR_MCP_PORT = os.environ.get(
    "VSS_ORCHESTRATOR_MCP_PORT", "9988"
).strip()
VSS_ORCHESTRATOR_MCP_URL = os.environ.get(
    "VSS_ORCHESTRATOR_MCP_URL",
    f"http://{HOST_INTERNAL_ALIAS}:{VSS_ORCHESTRATOR_MCP_PORT}/mcp",
).strip()
VSS_ORCHESTRATOR_MCP_TYPE = "streamable-http"

LLM_NAME = _first_nonempty(
    os.environ.get("LLM_NAME"),
    os.environ.get("LLM_REMOTE_MODEL"),
)
LLM_ENDPOINT_URL = _endpoint_base_url(_first_nonempty(
    os.environ.get("LLM_ENDPOINT_URL"),
    os.environ.get("LLM_REMOTE_URL"),
))
LLM_MODEL_TYPE = os.environ.get("LLM_MODEL_TYPE", "").strip()
LLM_ENABLE_THINKING = os.environ.get("LLM_ENABLE_THINKING", "").strip()
OPENAI_API_KEY = _first_nonempty(
    os.environ.get("OPENAI_API_KEY"),
    NVIDIA_API_KEY,
)
VLM_NAME = _first_nonempty(
    os.environ.get("VLM_NAME"),
    os.environ.get("VLM_REMOTE_MODEL"),
)
VLM_ENDPOINT_URL = _endpoint_base_url(_first_nonempty(
    os.environ.get("VLM_ENDPOINT_URL"),
    os.environ.get("VLM_REMOTE_URL"),
))
VLM_MODEL_TYPE = os.environ.get("VLM_MODEL_TYPE", "").strip()
# The representative RTX PRO task reserves one GPU. Empty overrides let the
# profile choose its supported shared placement rather than requesting GPU 1.
LLM_DEVICE_ID = os.environ.get("LLM_DEVICE_ID", "").strip()
VLM_DEVICE_ID = os.environ.get("VLM_DEVICE_ID", "").strip()
EXTERNAL_IP = os.environ.get("EXTERNAL_IP", "").strip()

for _key, _value in {
    "NGC_CLI_API_KEY": NGC_CLI_API_KEY,
    "NVIDIA_API_KEY": NVIDIA_API_KEY,
    "NEMOCLAW_ENDPOINT_URL": NEMOCLAW_ENDPOINT_URL,
    "NEMOCLAW_MODEL": NEMOCLAW_MODEL,
    "COMPATIBLE_API_KEY": COMPATIBLE_API_KEY,
    "NEMOCLAW_INSTALL_REF": NEMOCLAW_INSTALL_REF,
    "NEMOCLAW_SANDBOX_NAME": NEMOCLAW_SANDBOX_NAME,
    "NEMOCLAW_GATEWAY_PORT": NEMOCLAW_GATEWAY_PORT,
    "AGENT_RUNTIME": AGENT_RUNTIME,
    "ORCHESTRATOR_ENABLE_HTTPS": "false",
    "HOST_INTERNAL_ALIAS": HOST_INTERNAL_ALIAS,
    "VSS_ORCHESTRATOR_MCP_URL": VSS_ORCHESTRATOR_MCP_URL,
    "VSS_ORCHESTRATOR_MCP_TYPE": VSS_ORCHESTRATOR_MCP_TYPE,
    "LLM_DEVICE_ID": LLM_DEVICE_ID,
    "VLM_DEVICE_ID": VLM_DEVICE_ID,
    "RTSP_SAMPLE_URL": os.environ.get("RTSP_SAMPLE_URL", "").strip(),
}.items():
    os.environ[_key] = _value
""".strip()
    + "\n"
)

PERSIST_SOURCE = (
    r"""
# Persist non-secret runtime coordinates for the Harbor launcher.
import os
import shlex
from pathlib import Path

_env_out = Path(os.environ["NEMOCLAW_CI_ENV_OUT"])
_env_out.parent.mkdir(parents=True, exist_ok=True)
_keys = (
    "NEMOCLAW_SANDBOX_NAME",
    "NEMOCLAW_GATEWAY_PORT",
    "ORCHESTRATOR_ENABLE_HTTPS",
    "MCP_URL",
    "VSS_ORCHESTRATOR_MCP_URL",
    "VSS_ORCHESTRATOR_MCP_TYPE",
    "HOST_INTERNAL_ALIAS",
    "HARDWARE_PROFILE",
    "UV_NO_SYNC",
)
with _env_out.open("w", encoding="utf-8") as _handle:
    for _key in _keys:
        _value = globals().get(_key, os.environ.get(_key))
        if _value is not None:
            _handle.write(f"export {_key}={shlex.quote(str(_value))}\n")
print(f"Wrote NemoClaw runtime coordinates: {_env_out}")
""".strip()
    + "\n"
)

MCP_RESTART_SOURCE = (
    r"""
import os
import signal
import socket
import time
from pathlib import Path


_mcp_pid_file = Path("/tmp/skill-eval/nemoclaw/orchestrator-mcp.pid")


def _mcp_process(pid):
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
        uid_line = next(line for line in status.splitlines() if line.startswith("Uid:"))
        real_uid = int(uid_line.split()[1])
        raw_argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        argv = [part.decode("utf-8") for part in raw_argv if part]
    except (OSError, StopIteration, UnicodeDecodeError, ValueError):
        return None
    if real_uid != os.getuid():
        return None
    is_nat_mcp = any(
        Path(arg).name == "nat" and argv[index + 1:index + 3] == ["mcp", "serve"]
        for index, arg in enumerate(argv[:-2])
    )
    try:
        config_arg = argv[argv.index("--config_file") + 1]
        port_arg = argv[argv.index("--port") + 1]
    except (ValueError, IndexError):
        return None
    same_config = Path(config_arg).resolve() == Path(MCP_CONFIG_PATH).resolve()
    return argv if is_nat_mcp and same_config and port_arg == str(MCP_PORT) else None


_candidate_pids = set()
if _mcp_pid_file.is_file():
    try:
        _candidate_pids.add(int(_mcp_pid_file.read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        pass
# Migration for workers prepared before the PID record existed. Scan only
# same-UID processes with the exact `nat mcp serve` argument contract.
for _proc in Path("/proc").iterdir():
    if _proc.name.isdecimal() and _mcp_process(int(_proc.name)):
        _candidate_pids.add(int(_proc.name))

_owned_pids = sorted(pid for pid in _candidate_pids if _mcp_process(pid))
_mcp_pid_file.unlink(missing_ok=True)
for _pid in _owned_pids:
    try:
        os.kill(_pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
_deadline = time.monotonic() + 10
while time.monotonic() < _deadline:
    if not any(_mcp_process(pid) for pid in _owned_pids):
        break
    time.sleep(0.2)
for _pid in _owned_pids:
    if _mcp_process(_pid):
        try:
            os.kill(_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

with socket.socket() as _probe:
    _probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        _probe.bind((MCP_HOST, int(MCP_PORT)))
    except OSError as exc:
        raise RuntimeError(
            f"MCP port {MCP_HOST}:{MCP_PORT} remains occupied after scoped restart"
        ) from exc
print(
    f"Prepared MCP port for the current checkout; stopped {len(_owned_pids)} "
    "owned prior process(es).",
    flush=True,
)
""".strip()
    + "\n"
)

MCP_PERSIST_PID_SOURCE = (
    r"""
from pathlib import Path


_mcp_pid_file = Path("/tmp/skill-eval/nemoclaw/orchestrator-mcp.pid")
_mcp_pid_file.parent.mkdir(parents=True, exist_ok=True)
_mcp_pid_file.write_text(f"{VSS_ORCHESTRATOR_MCP_PID}\n", encoding="utf-8")
print(f"Recorded VSS Orchestrator MCP PID {VSS_ORCHESTRATOR_MCP_PID}")
""".strip()
    + "\n"
)

OPENCLAW_MCP_SOURCE = (
    r"""
import json
import shlex
import subprocess

def _run_in_sandbox(*args):
    command = [
        "openshell", "sandbox", "exec", "--name", NEMOCLAW_SANDBOX_NAME,
        "-g", NEMOCLAW_GATEWAY_NAME, "--", *args,
    ]
    print("$", shlex.join(command))
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )

_registration = _run_in_sandbox(
    "mcporter", "config", "get", "vss_orchestrator", "--json"
)
if _registration.returncode != 0:
    _added = _run_in_sandbox(
        "mcporter", "config", "add", "vss_orchestrator",
        "--url", VSS_ORCHESTRATOR_MCP_URL,
        "--scope", "home",
    )
    if _added.returncode != 0:
        raise RuntimeError(
            f"mcporter config add failed with exit {_added.returncode}: "
            f"{_added.stderr[-500:]}"
        )
    _registration = _run_in_sandbox(
        "mcporter", "config", "get", "vss_orchestrator", "--json"
    )
if _registration.returncode != 0:
    raise RuntimeError("mcporter config get failed")
try:
    _registered = json.loads(_registration.stdout)
except json.JSONDecodeError as exc:
    raise RuntimeError("mcporter returned invalid registration JSON") from exc
if (
    _registered.get("name") != "vss_orchestrator"
    or _registered.get("baseUrl") != VSS_ORCHESTRATOR_MCP_URL
):
    raise RuntimeError(f"Unexpected mcporter registration: {_registered!r}")
print("MCP server 'vss_orchestrator' is registered in OpenClaw.")
""".strip()
    + "\n"
)

OPENCLAW_RTSP_SOURCE = (
    r"""
# The dense-captioning eval needs the fixed public relay inside OpenClaw.
_rtsp_sample_url = os.environ.get("RTSP_SAMPLE_URL", "").strip()
_expected_rtsp_sample_url = (
    "rtsp://global.stg.ga.launchpad.nvidia.com:11333/camera03"
)
if _rtsp_sample_url != _expected_rtsp_sample_url:
    raise ValueError("NemoClaw CI RTSP setup requires the fixed public relay")
_rtsp_config_cmd = AGENT_CONFIG_SET_CMD.format(
    sandbox=NEMOCLAW_SANDBOX_NAME,
    key="env.vars.RTSP_SAMPLE_URL",
    value=shlex.quote(_rtsp_sample_url),
)
!{_rtsp_config_cmd}
assert _exit_code == 0, "config set failed: env.vars.RTSP_SAMPLE_URL"
_gateway_restart_cmd = AGENT_GATEWAY_RESTART_CMD.format(
    sandbox=NEMOCLAW_SANDBOX_NAME
)
!{_gateway_restart_cmd}
if _exit_code != 0:
    _recover_cmd = AGENT_RECOVER_CMD.format(sandbox=NEMOCLAW_SANDBOX_NAME)
    !{_recover_cmd}
    assert _exit_code == 0, "sandbox recover failed after RTSP config"
print("Applied the fixed CI RTSP sample to the OpenClaw sandbox.", flush=True)
""".strip()
    + "\n"
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


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


def _normalize_cell(cell: dict[str, Any]) -> dict[str, Any]:
    output = deepcopy(cell)
    if isinstance(output.get("source"), list):
        output["source"] = "".join(str(line) for line in output["source"])
    return output


def _with_shields_window(source: str, *, reason: str, activity: str) -> str:
    guarded_source = textwrap.indent(source.rstrip() + "\n", "    ")
    return (
        "# A reused sandbox is normally locked after its prior run. Open only a\n"
        "# bounded maintenance window for this notebook mutation.\n"
        "try:\n"
        "    _shields_down_cmd = (\n"
        '        f"{AGENT_CLI} {NEMOCLAW_SANDBOX_NAME} shields down "\n'
        f"        '--timeout 15m --reason \"{reason}\"'\n"
        "    )\n"
        "    !{_shields_down_cmd}\n"
        f'    assert _exit_code == 0, "shields down failed before {activity}"\n'
        + guarded_source
        + "finally:\n"
        "    _shields_up_cmd = (\n"
        '        f"{AGENT_CLI} {NEMOCLAW_SANDBOX_NAME} shields up"\n'
        "    )\n"
        "    !{_shields_up_cmd}\n"
        "    if _exit_code != 0:\n"
        f'        raise RuntimeError("shields up failed after {activity}")\n'
    )


def _patch_ci_cell(cell_id: str, cell: dict[str, Any]) -> dict[str, Any]:
    source = cell.get("source")
    if not isinstance(source, str):
        return cell

    patched = deepcopy(cell)
    if cell_id == "e67f6da4":
        anchor = (
            "AGENT_HOOKS_ENABLED = True     # enable agent inbound webhooks "
            "when the harness supports them"
        )
        if anchor not in source:
            raise ValueError("NemoClaw settings cell changed: hooks anchor missing")
        patched["source"] = source.replace(
            anchor,
            "AGENT_HOOKS_ENABLED = False    # headless CI uses the OpenClaw CLI",
            1,
        )
    elif cell_id == "c13aaf5e":
        env_helper = """def uv_env_for_agent() -> dict[str, str]:
    env = os.environ.copy()
    # Do not inherit the notebook kernel venv; uv should use services/agent/.venv.
    env.pop("VIRTUAL_ENV", None)
    return env
"""
        if env_helper not in source:
            raise ValueError(
                "Orchestrator setup cell changed: uv environment anchor missing"
            )
        source = source.replace(
            env_helper,
            env_helper.replace(
                "    return env\n",
                '    env.pop("UV_PROJECT_ENVIRONMENT", None)\n    return env\n',
            ),
            1,
        )
        sync_anchor = "\ndef run_uv_sync() -> subprocess.CompletedProcess[str]:\n"
        if sync_anchor not in source:
            raise ValueError(
                "Orchestrator setup cell changed: sync function anchor missing"
            )
        ensure_venv = """
def ensure_agent_venv() -> None:
    venv_python = ORCHESTRATOR_MCP_VENV_DIR / "bin" / "python"
    if ORCHESTRATOR_MCP_VENV_DIR.is_symlink():
        raise RuntimeError(
            f"Refusing to replace symlinked orchestrator environment: {ORCHESTRATOR_MCP_VENV_DIR}"
        )
    if venv_python.is_file() and os.access(venv_python, os.X_OK):
        return

    command = ["uv", "venv"]
    if ORCHESTRATOR_MCP_VENV_DIR.exists():
        print(f"Replacing invalid orchestrator environment in {ORCHESTRATOR_MCP_VENV_DIR} ...")
        command.append("--clear")
        uv_venv_help = subprocess.run(
            ["uv", "venv", "--help"],
            cwd=str(AGENT_DIR),
            env=uv_env_for_agent(),
            check=True,
            capture_output=True,
            text=True,
        )
        if "--force" in uv_venv_help.stdout:
            command.append("--force")
    else:
        print(
            f"Creating Python {ORCHESTRATOR_MCP_PYTHON_VERSION} venv "
            f"in {ORCHESTRATOR_MCP_VENV_DIR} ..."
        )
    command.extend(
        [
            "--python",
            ORCHESTRATOR_MCP_PYTHON_VERSION,
            str(ORCHESTRATOR_MCP_VENV_DIR),
        ]
    )
    subprocess.run(
        command,
        cwd=str(AGENT_DIR),
        env=uv_env_for_agent(),
        check=True,
    )
    if not (venv_python.is_file() and os.access(venv_python, os.X_OK)):
        raise RuntimeError(
            "uv venv completed without creating an executable Python at "
            f"{venv_python}"
        )

"""
        source = source.replace(sync_anchor, ensure_venv + sync_anchor, 1)
        create_venv = """if not ORCHESTRATOR_MCP_VENV_DIR.is_dir():
    print(f"Creating Python {ORCHESTRATOR_MCP_PYTHON_VERSION} venv in {ORCHESTRATOR_MCP_VENV_DIR} ...")
    subprocess.run(
        ["uv", "venv", "--python", ORCHESTRATOR_MCP_PYTHON_VERSION],
        cwd=str(AGENT_DIR),
        check=True,
    )
"""
        if create_venv not in source:
            raise ValueError(
                "Orchestrator setup cell changed: venv creation anchor missing"
            )
        source = source.replace(create_venv, "ensure_agent_venv()\n", 1)
        sync = '["uv", "sync", "--no-dev", "--extra", "agent"]'
        if sync not in source:
            raise ValueError("Orchestrator setup cell changed: uv sync anchor missing")
        source = source.replace(
            sync,
            '["uv", "sync", "--no-dev", "--extra", "agent", '
            '"--no-install-package", "torch"]',
            1,
        )
        anchor = "agent_env = uv_env_for_agent()\n"
        if anchor not in source:
            raise ValueError(
                "Orchestrator setup cell changed: agent env anchor missing"
            )
        patched["source"] = source.replace(
            anchor,
            'UV_NO_SYNC = "1"\nos.environ["UV_NO_SYNC"] = UV_NO_SYNC\n' + anchor,
            1,
        )
    elif cell_id == "s34-code":
        anchors = (
            "_skill_install_cmd = AGENT_SKILL_INSTALL_CMD.format(",
            "_gateway_restart_cmd = AGENT_GATEWAY_RESTART_CMD.format(",
        )
        missing = [anchor for anchor in anchors if anchor not in source]
        if missing:
            raise ValueError(
                "NemoClaw skill cell changed: maintenance anchors missing: "
                + ", ".join(missing)
            )
        patched["source"] = _with_shields_window(
            source,
            reason="skill-eval skill setup",
            activity="skill install",
        )
    elif cell_id == "s35-code":
        anchor = "!openshell sandbox exec -n {NEMOCLAW_SANDBOX_NAME} -- sh -c"
        if anchor in source:
            source = source.replace(
                anchor,
                "!openshell sandbox exec --name {NEMOCLAW_SANDBOX_NAME} "
                "-g {NEMOCLAW_GATEWAY_NAME} -- sh -c",
                1,
            )
        loop_anchor = """    for doc in docs:
        # dest is a DIRECTORY: the OpenShell transport does mkdir + tar-extract
"""
        if loop_anchor not in source:
            raise ValueError(
                "NemoClaw workspace cell changed: upload loop anchor missing"
            )
        cleanup = """    for doc in docs:
        # OpenShell upload is create-only. Remove exactly the selected target
        # from a prior eval before re-uploading the notebook-selected document.
        _remote_doc = f"{WORKSPACE_REMOTE_DIR.rstrip('/')}/{doc.name}"
        _cleanup_script = shlex.quote(
            f"rm -rf -- {shlex.quote(_remote_doc)}"
        )
        !openshell sandbox exec --name {NEMOCLAW_SANDBOX_NAME} -g {NEMOCLAW_GATEWAY_NAME} -- sh -c {_cleanup_script}
        assert _exit_code == 0, f"workspace cleanup failed: {doc.name}"
        # dest is a DIRECTORY: the OpenShell transport does mkdir + tar-extract
"""
        patched["source"] = _with_shields_window(
            source.replace(loop_anchor, cleanup, 1),
            reason="skill-eval workspace setup",
            activity="workspace upload",
        )
    elif cell_id == "s36-code":
        patched["source"] = OPENCLAW_MCP_SOURCE
    elif cell_id == "s37-code":
        patched["source"] = source.rstrip() + "\n\n" + OPENCLAW_RTSP_SOURCE
    elif cell_id == "042eabd1":
        anchor = 'existing_pid = globals().get("VSS_ORCHESTRATOR_MCP_PID")\n'
        if anchor not in source:
            raise ValueError(
                "Orchestrator MCP start cell changed: process anchor missing"
            )
        patched["source"] = (
            MCP_RESTART_SOURCE
            + "\n\n"
            + source.rstrip()
            + "\n"
            + MCP_PERSIST_PID_SOURCE
        )
    return patched


def build_notebook(
    source_nb: dict[str, Any], manifest: dict[str, Any]
) -> dict[str, Any]:
    """Build one selected notebook section from stable cell ids."""
    cells_by_id = {cell.get("id"): cell for cell in source_nb.get("cells", [])}
    missing = [cell_id for cell_id in manifest["cells"] if cell_id not in cells_by_id]
    if missing:
        raise ValueError(
            "Notebook is missing configured cell ids: " + ", ".join(missing)
        )

    output = deepcopy(source_nb)
    output["cells"] = []
    inserted = False
    insert_before = manifest.get("insert_parameters_before")
    nbformat = int(output.get("nbformat", 4))
    for cell_id in manifest["cells"]:
        if cell_id == insert_before and not inserted:
            output["cells"].append(
                _code_cell(nbformat, PARAMETER_SOURCE, "ci-parameters")
            )
            inserted = True
        selected = _normalize_cell(cells_by_id[cell_id])
        output["cells"].append(_patch_ci_cell(cell_id, selected))
    if not inserted:
        raise ValueError(
            f"Parameter insertion anchor is not selected: {insert_before!r}"
        )
    return output


def build_notebooks(
    source_nbs: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Combine selected cells from the ordered source notebooks."""
    sections = manifest.get("notebooks")
    if not isinstance(sections, list) or len(sections) != len(source_nbs):
        raise ValueError("Manifest notebooks do not match source notebooks")
    output = deepcopy(source_nbs[0])
    output["cells"] = []
    for index, (source_nb, section) in enumerate(zip(source_nbs, sections), 1):
        selected = build_notebook(source_nb, section)
        for cell in selected["cells"]:
            copied = deepcopy(cell)
            if copied.get("id") == "ci-parameters":
                copied["id"] = f"ci-parameters-{index}"
            output["cells"].append(copied)
    output["cells"].append(
        _code_cell(int(output.get("nbformat", 4)), PERSIST_SOURCE, "ci-persist-env")
    )
    return output


def execute_notebook(
    notebook: dict[str, Any],
    *,
    cwd: Path,
    timeout: int,
) -> None:
    try:
        import nbformat
        from nbclient import NotebookClient
    except ImportError as exc:
        raise RuntimeError(
            "Notebook execution requires nbformat, nbclient, and ipykernel"
        ) from exc

    client = NotebookClient(
        nbformat.from_dict(notebook),
        timeout=timeout,
        kernel_name=os.environ.get("NEMOCLAW_CI_KERNEL", "python3"),
        allow_errors=False,
        resources={"metadata": {"path": str(cwd)}},
    )
    client.execute()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = _repo_root()
    parser.add_argument(
        "--manifest",
        default=str(root / ".github/skill-eval/nemoclaw/notebook_cells.json"),
    )
    parser.add_argument("--env-out", default=str(DEFAULT_ENV_OUT))
    parser.add_argument(
        "--output", default="", help="Write a composed dry-run notebook"
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("NEMOCLAW_SETUP_CELL_TIMEOUT_SEC", "3600")),
    )
    args = parser.parse_args(argv)

    manifest = _load_json(Path(args.manifest).resolve())
    sections = manifest.get("notebooks")
    if not isinstance(sections, list) or not sections:
        raise ValueError("Manifest must contain a non-empty notebooks list")
    source_nbs = [_load_json((root / item["notebook"]).resolve()) for item in sections]
    composed = build_notebooks(source_nbs, manifest)
    os.environ.setdefault("VSS_REPO_DIR", str(root))
    os.environ["NEMOCLAW_CI_ENV_OUT"] = str(Path(args.env_out).resolve())

    if args.execute:
        execute_notebook(composed, cwd=root, timeout=args.timeout)
        print(
            "Executed NemoClaw setup notebook cells; executed notebook was not persisted."
        )
    elif not args.output:
        parser.error("--output is required unless --execute is used")

    if args.output:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(composed, indent=1), encoding="utf-8")
        print(f"Wrote composed setup notebook: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
