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

def _openai_base_url(url):
    url = (url or "").strip().rstrip("/")
    if url and not url.endswith("/v1"):
        url = f"{url}/v1"
    return url

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
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("NVIDIA_API_KEY")
        or ""
    ).strip()
NEMOCLAW_INSTALL_REF = os.environ.get(
    "NEMOCLAW_INSTALL_REF",
    _notebook_default("NEMOCLAW_INSTALL_REF", ""),
).strip()
NEMOCLAW_SANDBOX_NAME = os.environ.get("NEMOCLAW_SANDBOX_NAME", "demo").strip()
NEMOCLAW_GATEWAY_PORT = os.environ.get("NEMOCLAW_GATEWAY_PORT", "8080").strip() or "8080"
os.environ["NEMOCLAW_GATEWAY_PORT"] = NEMOCLAW_GATEWAY_PORT
OPENSHELL_DOCKER_NETWORK_NAME = (
    os.environ.get("OPENSHELL_DOCKER_NETWORK_NAME", "openshell-docker").strip()
    or "openshell-docker"
)
os.environ["OPENSHELL_DOCKER_NETWORK_NAME"] = OPENSHELL_DOCKER_NETWORK_NAME
NEMOCLAW_RECREATE_SANDBOX = os.environ.get("NEMOCLAW_RECREATE_SANDBOX", "1").strip() or "1"
os.environ["NEMOCLAW_RECREATE_SANDBOX"] = NEMOCLAW_RECREATE_SANDBOX
OPENCLAW_HOOKS_ENABLED = os.environ.get(
    "OPENCLAW_HOOKS_ENABLED",
    os.environ.get("AGENT_HOOKS_ENABLED", "1"),
).lower() not in ("0", "false", "no")
OPENCLAW_HOOKS_PATH = os.environ.get(
    "OPENCLAW_HOOKS_PATH",
    os.environ.get("AGENT_HOOKS_PATH", _notebook_default("AGENT_HOOKS_PATH", "/hooks")),
).strip() or "/hooks"
AGENT_HOOKS_ENABLED = OPENCLAW_HOOKS_ENABLED
AGENT_HOOKS_PATH = OPENCLAW_HOOKS_PATH
OPENCLAW_DISABLE_STREAMING_TOOL_CALLS = os.environ.get("OPENCLAW_DISABLE_STREAMING_TOOL_CALLS", "1").strip() or "1"
os.environ["OPENCLAW_DISABLE_STREAMING_TOOL_CALLS"] = OPENCLAW_DISABLE_STREAMING_TOOL_CALLS
VSS_ORCHESTRATOR_MCP_URL = os.environ.get(
    "VSS_ORCHESTRATOR_MCP_URL",
    "http://host.openshell.internal:9988/mcp",
).strip()
VSS_ORCHESTRATOR_MCP_TYPE = (
    os.environ.get("VSS_ORCHESTRATOR_MCP_TYPE", "streamable-http").strip()
    or "streamable-http"
)
MCP_URL = _notebook_default(
    "MCP_URL",
    f"http://127.0.0.1:{os.environ.get('VSS_ORCHESTRATOR_MCP_PORT', '9988')}/mcp",
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
# Accept the legacy VSS_* names while populating the names used by the split
# deploy_vss_orchestrator.ipynb notebook.
LLM_NAME = os.environ.get(
    "LLM_NAME",
    os.environ.get("VSS_LLM_NAME", _notebook_default("LLM_NAME", "")),
).strip()
LLM_ENDPOINT_URL = os.environ.get(
    "LLM_ENDPOINT_URL",
    os.environ.get("VSS_LLM_ENDPOINT_URL", _notebook_default("LLM_ENDPOINT_URL", "")),
).strip()
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
VLM_NAME = os.environ.get(
    "VLM_NAME",
    os.environ.get("VSS_VLM_NAME", _notebook_default("VLM_NAME", "")),
).strip()
VLM_ENDPOINT_URL = os.environ.get(
    "VLM_ENDPOINT_URL",
    os.environ.get("VSS_VLM_ENDPOINT_URL", _notebook_default("VLM_ENDPOINT_URL", "")),
).strip()
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
    "MCP_URL",
    "MCP_PORT",
    "MCP_SSE_URL",
    "MCP_SSE_PORT",
    "OPENCLAW_MCP_URL",
    "OPENCLAW_MCP_TYPE",
    "VSS_ORCHESTRATOR_MCP_URL",
    "VSS_ORCHESTRATOR_MCP_TYPE",
    "HOST_INTERNAL_ALIAS",
    "HARDWARE_PROFILE",
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
        # NemoClaw v0.0.80 requires this exact JSON list before migrating a
        # legacy managed sandbox. Confirm only CI's selected sandbox so an
        # unexpected second legacy sandbox still causes the installer to stop.
        os.environ[confirmation_key] = json.dumps([sandbox_name], separators=(",", ":"))


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
    if cell_id == "4c91fd59":
        return _patch_docker_login_cell(cell)
    if cell_id == "s31-code":
        onboard = "!cd ~ && nemoclaw onboard --non-interactive --agent {AGENT_RUNTIME}"
        if onboard not in source:
            raise ValueError("NemoClaw setup cell is missing the expected onboard command")
        patched = deepcopy(cell)
        patched["source"] = source.replace(
            onboard,
            "!cd ~ && nemoclaw onboard --fresh --non-interactive "
            "--agent {AGENT_RUNTIME}",
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
        output["cells"].append(_patch_ci_cell(cell_id, _normalize_cell_source(cells_by_id[cell_id])))

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
    )
    return {key: value for key in keys if (value := os.environ.get(key))}


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
