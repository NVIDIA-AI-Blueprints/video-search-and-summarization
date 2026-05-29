#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Execute the setup-only subset of deploy_nemoclaw_vss.ipynb for CI.

The human notebook remains the source of truth.  This adapter builds a
temporary notebook from stable cell ids, injects a CI parameter cell that
reads secrets from the process environment, executes the notebook, redacts
known secret values from outputs, and persists the runtime values needed by
the headless NemoClaw launcher.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

DEFAULT_ENV_OUT = Path("/tmp/skill-eval/nemoclaw/nemoclaw.env")
DEFAULT_OUTPUT = Path("/tmp/skill-eval/nemoclaw/deploy_nemoclaw_vss.executed.ipynb")

PARAMETER_SOURCE = r'''
# Injected by .github/skill-eval/nemoclaw/notebook_setup_adapter.py.
# Keep values in environment variables so the executed notebook source does
# not contain API keys.
import os

def _openai_base_url(url):
    url = (url or "").strip().rstrip("/")
    if url and not url.endswith("/v1"):
        url = f"{url}/v1"
    return url

NGC_CLI_API_KEY = os.environ.get("NGC_CLI_API_KEY", "")
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
HARDWARE_PROFILE = os.environ.get("HARDWARE_PROFILE", HARDWARE_PROFILE).strip()
NEMOCLAW_ENDPOINT_URL = os.environ.get("NEMOCLAW_ENDPOINT_URL", NEMOCLAW_ENDPOINT_URL).strip()
NEMOCLAW_MODEL = os.environ.get("NEMOCLAW_MODEL", NEMOCLAW_MODEL).strip()
COMPATIBLE_API_KEY = os.environ.get("COMPATIBLE_API_KEY", COMPATIBLE_API_KEY).strip()
if not NEMOCLAW_ENDPOINT_URL:
    NEMOCLAW_ENDPOINT_URL = (
        os.environ.get("NEMOCLAW_FALLBACK_ENDPOINT_URL")
        or os.environ.get("LLM_REMOTE_URL")
        or ""
    ).strip()
NEMOCLAW_ENDPOINT_URL = _openai_base_url(NEMOCLAW_ENDPOINT_URL)
if not NEMOCLAW_MODEL:
    NEMOCLAW_MODEL = (
        os.environ.get("NEMOCLAW_FALLBACK_MODEL")
        or os.environ.get("LLM_REMOTE_MODEL")
        or NEMOCLAW_MODEL
        or ""
    ).strip()
if NEMOCLAW_ENDPOINT_URL and not COMPATIBLE_API_KEY:
    COMPATIBLE_API_KEY = (
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("NVIDIA_API_KEY")
        or ""
    ).strip()
NEMOCLAW_INSTALL_REF = os.environ.get("NEMOCLAW_INSTALL_REF", NEMOCLAW_INSTALL_REF).strip()
NEMOCLAW_SANDBOX_NAME = os.environ.get("NEMOCLAW_SANDBOX_NAME", "demo").strip()
OPENCLAW_HOOKS_ENABLED = os.environ.get("OPENCLAW_HOOKS_ENABLED", "1").lower() not in ("0", "false", "no")
OPENCLAW_HOOKS_PATH = os.environ.get("OPENCLAW_HOOKS_PATH", OPENCLAW_HOOKS_PATH).strip() or "/hooks"
OPENCLAW_DISABLE_STREAMING_TOOL_CALLS = os.environ.get("OPENCLAW_DISABLE_STREAMING_TOOL_CALLS", "1").strip() or "1"
os.environ["OPENCLAW_DISABLE_STREAMING_TOOL_CALLS"] = OPENCLAW_DISABLE_STREAMING_TOOL_CALLS
VSS_ORCHESTRATOR_MCP_SSE_PORT = int(os.environ.get("VSS_ORCHESTRATOR_MCP_SSE_PORT", "9989"))
VSS_ORCHESTRATOR_MCP_URL = os.environ.get(
    "VSS_ORCHESTRATOR_MCP_URL",
    f"http://host.openshell.internal:{VSS_ORCHESTRATOR_MCP_SSE_PORT}/sse",
).strip()
VSS_ORCHESTRATOR_MCP_TYPE = os.environ.get("VSS_ORCHESTRATOR_MCP_TYPE", "sse").strip() or "sse"
os.environ["VSS_ORCHESTRATOR_MCP_URL"] = VSS_ORCHESTRATOR_MCP_URL
os.environ["VSS_ORCHESTRATOR_MCP_TYPE"] = VSS_ORCHESTRATOR_MCP_TYPE
os.environ["VSS_ORCHESTRATOR_MCP_SSE_PORT"] = str(VSS_ORCHESTRATOR_MCP_SSE_PORT)
if NEMOCLAW_ENDPOINT_URL:
    os.environ["NEMOCLAW_ENDPOINT_URL"] = NEMOCLAW_ENDPOINT_URL
if NEMOCLAW_MODEL:
    os.environ["NEMOCLAW_MODEL"] = NEMOCLAW_MODEL
if COMPATIBLE_API_KEY:
    os.environ["COMPATIBLE_API_KEY"] = COMPATIBLE_API_KEY

# Optional VSS endpoint/model overrides used by the orchestrator MCP server.
VSS_LLM_NAME = os.environ.get("VSS_LLM_NAME", VSS_LLM_NAME).strip()
VSS_LLM_ENDPOINT_URL = os.environ.get("VSS_LLM_ENDPOINT_URL", VSS_LLM_ENDPOINT_URL).strip()
VSS_LLM_MODEL_TYPE = os.environ.get("VSS_LLM_MODEL_TYPE", VSS_LLM_MODEL_TYPE).strip()
VSS_LLM_ENABLE_THINKING = os.environ.get("VSS_LLM_ENABLE_THINKING", VSS_LLM_ENABLE_THINKING).strip()
VSS_OPENAI_API_KEY = os.environ.get("VSS_OPENAI_API_KEY", VSS_OPENAI_API_KEY).strip()
VSS_VLM_NAME = os.environ.get("VSS_VLM_NAME", VSS_VLM_NAME).strip()
VSS_VLM_ENDPOINT_URL = os.environ.get("VSS_VLM_ENDPOINT_URL", VSS_VLM_ENDPOINT_URL).strip()
VSS_VLM_MODEL_TYPE = os.environ.get("VSS_VLM_MODEL_TYPE", VSS_VLM_MODEL_TYPE).strip()
LLM_DEVICE_ID = os.environ.get("LLM_DEVICE_ID", LLM_DEVICE_ID).strip()
VLM_DEVICE_ID = os.environ.get("VLM_DEVICE_ID", VLM_DEVICE_ID).strip()
EXTERNAL_IP = os.environ.get("EXTERNAL_IP", EXTERNAL_IP).strip()
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
    "OPENCLAW_HOOKS_PATH",
    "OPENCLAW_DISABLE_STREAMING_TOOL_CALLS",
    "MCP_URL",
    "MCP_PORT",
    "MCP_SSE_URL",
    "MCP_SSE_PORT",
    "OPENCLAW_MCP_URL",
    "OPENCLAW_MCP_TYPE",
    "VSS_ORCHESTRATOR_MCP_URL",
    "VSS_ORCHESTRATOR_MCP_TYPE",
    "VSS_ORCHESTRATOR_MCP_SSE_PORT",
    "HOST_INTERNAL_ALIAS",
    "HARDWARE_PROFILE",
    "NEMOCLAW_HOOKS_TOKEN_FILE",
]
NEMOCLAW_HOOKS_TOKEN_FILE = str(_token_file)
if "OPENCLAW_HOOKS_TOKEN" in globals() and OPENCLAW_HOOKS_TOKEN:
    _token_file.write_text(str(OPENCLAW_HOOKS_TOKEN), encoding="utf-8")
    _token_file.chmod(0o600)
with _env_out.open("w", encoding="utf-8") as fp:
    for _key in _keys:
        if _key in globals():
            fp.write(f"export {_key}={shlex.quote(str(globals()[_key]))}\n")
print(f"Wrote NemoClaw CI env: {_env_out}")
print(f"Wrote NemoClaw hook token file: {_token_file}")
'''.strip() + "\n"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


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
        output["cells"].append(_normalize_cell_source(cells_by_id[cell_id]))

    if not inserted:
        output["cells"].append(_code_cell(nbformat, PARAMETER_SOURCE, "ci-parameters"))
    output["cells"].append(_code_cell(nbformat, PERSIST_SOURCE, "ci-persist-env"))
    return output


def _redaction_values() -> dict[str, str]:
    keys = (
        "NGC_CLI_API_KEY",
        "NVIDIA_API_KEY",
        "COMPATIBLE_API_KEY",
        "OPENAI_API_KEY",
        "VSS_OPENAI_API_KEY",
        "OPENCLAW_HOOKS_TOKEN",
    )
    return {key: value for key in keys if (value := os.environ.get(key))}


def _redact(obj: Any, values: dict[str, str]) -> Any:
    if isinstance(obj, str):
        redacted = obj
        for key, value in values.items():
            if value:
                redacted = redacted.replace(value, f"<redacted:{key}>")
        return redacted
    if isinstance(obj, list):
        return [_redact(item, values) for item in obj]
    if isinstance(obj, dict):
        return {key: _redact(value, values) for key, value in obj.items()}
    return obj


def execute_notebook(notebook: dict[str, Any], *, cwd: Path, timeout: int) -> dict[str, Any]:
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
    client.execute()
    return json.loads(nbformat.writes(nb))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = _repo_root()
    default_manifest = root / ".github" / "skill-eval" / "nemoclaw" / "notebook_cells.json"
    parser.add_argument("--notebook", default=None, help="Source notebook path")
    parser.add_argument("--manifest", default=str(default_manifest), help="Cell sidecar manifest")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Executed notebook output path")
    parser.add_argument("--env-out", default=str(DEFAULT_ENV_OUT), help="Runtime env file written by the injected persist cell")
    parser.add_argument("--execute", action="store_true", help="Execute the temporary notebook")
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("NEMOCLAW_SETUP_CELL_TIMEOUT", "3600")))
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest).resolve()
    manifest = _load_json(manifest_path)
    notebook_path = Path(args.notebook or (root / manifest["notebook"])).resolve()
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("VSS_REPO_DIR", str(root))
    os.environ["NEMOCLAW_CI_ENV_OUT"] = str(Path(args.env_out).resolve())

    source_nb = _load_json(notebook_path)
    temp_nb = build_notebook(source_nb, manifest)
    if args.execute:
        temp_nb = execute_notebook(temp_nb, cwd=root, timeout=args.timeout)
    temp_nb = _redact(temp_nb, _redaction_values())
    output_path.write_text(json.dumps(temp_nb, indent=1), encoding="utf-8")
    print(f"Wrote setup notebook: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
