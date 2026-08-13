#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Execute the checked-in NemoClaw setup notebooks from beginning to end.

Phase 1 deliberately keeps the existing skill-eval model/provider contract.
The coordinator's Anthropic-compatible NVIDIA inference settings are mapped to
the notebooks' native variables before both checked-in notebooks are executed
in their documented order. Executed notebooks are never persisted.
"""

from __future__ import annotations

import argparse
import os
import shlex
import signal
import socket
import sys
import time
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DEFAULT_ENV_OUT = Path("/tmp/skill-eval/nemoclaw/nemoclaw.env")
NOTEBOOK_RELATIVE_PATHS = (
    Path("deploy/docker/scripts/deploy_nemoclaw.ipynb"),
    Path("deploy/docker/scripts/deploy_vss_orchestrator.ipynb"),
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def notebook_paths(root: Path | None = None) -> tuple[Path, ...]:
    base = (root or _repo_root()).resolve()
    return tuple(base / relative for relative in NOTEBOOK_RELATIVE_PATHS)


def _first_nonempty(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _endpoint_base_url(url: str) -> str:
    value = (url or "").strip().rstrip("/")
    for suffix in ("/v1/models", "/v1"):
        if value.endswith(suffix):
            value = value[: -len(suffix)].rstrip("/")
            break
    return value


def _openai_base_url(url: str) -> str:
    value = _endpoint_base_url(url)
    return f"{value}/v1" if value else ""


def prepare_environment(
    environment: MutableMapping[str, str] | None = None,
    *,
    root: Path | None = None,
) -> MutableMapping[str, str]:
    """Map the current CI provider contract to notebook-native variables."""

    env = environment if environment is not None else os.environ
    repo_root = (root or _repo_root()).resolve()
    env.setdefault("VSS_REPO_DIR", str(repo_root))
    env.setdefault("AGENT_RUNTIME", "openclaw")
    env.setdefault("ORCHESTRATOR_ENABLE_HTTPS", "false")
    env.setdefault("HOST_INTERNAL_ALIAS", "host.openshell.internal")
    env.setdefault("VSS_ORCHESTRATOR_MCP_PORT", "9988")
    env.setdefault("NEMOCLAW_SANDBOX_NAME", "demo")
    env.setdefault("NEMOCLAW_GATEWAY_PORT", "8080")
    if not env["NEMOCLAW_GATEWAY_PORT"].isdigit() or not (
        1024 <= int(env["NEMOCLAW_GATEWAY_PORT"]) <= 65535
    ):
        raise ValueError("NEMOCLAW_GATEWAY_PORT must be between 1024 and 65535")

    ngc_key = _first_nonempty(env.get("NGC_CLI_API_KEY"), env.get("NGC_API_KEY"))
    if not ngc_key:
        raise RuntimeError("NGC_CLI_API_KEY is required for notebook setup")
    env["NGC_CLI_API_KEY"] = ngc_key

    endpoint = _openai_base_url(
        _first_nonempty(
            env.get("NEMOCLAW_ENDPOINT_URL"),
            env.get("ANTHROPIC_BASE_URL"),
            env.get("LLM_REMOTE_URL"),
        )
    )
    model = _first_nonempty(
        env.get("NEMOCLAW_MODEL"),
        env.get("ANTHROPIC_MODEL"),
        env.get("LLM_REMOTE_MODEL"),
    )
    compatible_key = _first_nonempty(
        env.get("COMPATIBLE_API_KEY"),
        env.get("ANTHROPIC_API_KEY"),
        env.get("OPENAI_API_KEY"),
        env.get("NVIDIA_API_KEY"),
    )
    missing = [
        name
        for name, value in (
            ("NEMOCLAW_ENDPOINT_URL/ANTHROPIC_BASE_URL", endpoint),
            ("NEMOCLAW_MODEL/ANTHROPIC_MODEL", model),
            ("COMPATIBLE_API_KEY/ANTHROPIC_API_KEY", compatible_key),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Phase 1 requires the current NVIDIA inference configuration: "
            + ", ".join(missing)
        )
    env["NEMOCLAW_ENDPOINT_URL"] = endpoint
    env["NEMOCLAW_MODEL"] = model
    env["COMPATIBLE_API_KEY"] = compatible_key

    llm_url = _endpoint_base_url(
        _first_nonempty(env.get("LLM_ENDPOINT_URL"), env.get("LLM_REMOTE_URL"))
    )
    llm_model = _first_nonempty(env.get("LLM_NAME"), env.get("LLM_REMOTE_MODEL"))
    vlm_url = _endpoint_base_url(
        _first_nonempty(env.get("VLM_ENDPOINT_URL"), env.get("VLM_REMOTE_URL"))
    )
    vlm_model = _first_nonempty(env.get("VLM_NAME"), env.get("VLM_REMOTE_MODEL"))
    if llm_url:
        env["LLM_ENDPOINT_URL"] = llm_url
    if llm_model:
        env["LLM_NAME"] = llm_model
    if vlm_url:
        env["VLM_ENDPOINT_URL"] = vlm_url
    if vlm_model:
        env["VLM_NAME"] = vlm_model
    openai_key = _first_nonempty(env.get("OPENAI_API_KEY"), env.get("NVIDIA_API_KEY"))
    if openai_key:
        env["OPENAI_API_KEY"] = openai_key

    mcp_port = env["VSS_ORCHESTRATOR_MCP_PORT"]
    host_alias = env["HOST_INTERNAL_ALIAS"]
    env.setdefault("VSS_ORCHESTRATOR_MCP_URL", f"http://{host_alias}:{mcp_port}/mcp")
    env.setdefault("VSS_ORCHESTRATOR_MCP_TYPE", "streamable-http")
    return env


def _owned_mcp_process(pid: int, *, config_path: Path, port: int) -> list[str] | None:
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
        Path(arg).name == "nat" and argv[index + 1 : index + 3] == ["mcp", "serve"]
        for index, arg in enumerate(argv[:-2])
    )
    try:
        process_config = Path(argv[argv.index("--config_file") + 1]).resolve()
        process_port = int(argv[argv.index("--port") + 1])
    except (ValueError, IndexError):
        return None
    if is_nat_mcp and process_config == config_path.resolve() and process_port == port:
        return argv
    return None


def stop_owned_mcp_processes(*, root: Path, port: int) -> int:
    """Stop only same-user MCP processes for this checkout and port."""

    config_path = root / "deploy/docker/scripts/vss_orchestrator_mcp_config.yml"
    owned = sorted(
        int(path.name)
        for path in Path("/proc").iterdir()
        if path.name.isdecimal()
        and _owned_mcp_process(int(path.name), config_path=config_path, port=port)
    )
    for pid in owned:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not any(
            _owned_mcp_process(pid, config_path=config_path, port=port) for pid in owned
        ):
            break
        time.sleep(0.2)
    for pid in owned:
        if _owned_mcp_process(pid, config_path=config_path, port=port):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("0.0.0.0", port))
        except OSError as exc:
            raise RuntimeError(
                f"MCP port {port} remains occupied after scoped cleanup"
            ) from exc
    return len(owned)


def execute_notebook(path: Path, *, cwd: Path, timeout: int) -> Any:
    try:
        import nbformat
        from nbclient import NotebookClient
    except ImportError as exc:
        raise RuntimeError(
            "Notebook execution requires nbformat, nbclient, and ipykernel"
        ) from exc

    notebook = nbformat.read(path, as_version=4)
    client = NotebookClient(
        notebook,
        timeout=timeout,
        kernel_name=os.environ.get("NEMOCLAW_CI_KERNEL", "python3"),
        allow_errors=False,
        resources={"metadata": {"path": str(cwd)}},
    )
    executed = client.execute()
    print(f"Executed {path.name} from beginning to end; outputs were not persisted.")
    return executed


def _output_text(notebook: Any) -> str:
    chunks: list[str] = []
    for cell in notebook.get("cells", []):
        for output in cell.get("outputs", []):
            if output.get("output_type") == "stream":
                chunks.append(str(output.get("text", "")))
            elif output.get("output_type") in {"display_data", "execute_result"}:
                chunks.append(str(output.get("data", {}).get("text/plain", "")))
    return "\n".join(chunks)


def _require_output(notebook: Any, marker: str, *, notebook_name: str) -> None:
    if marker not in _output_text(notebook):
        raise RuntimeError(
            f"{notebook_name} completed without readiness marker: {marker}"
        )


def write_runtime_environment(
    path: Path, environment: MutableMapping[str, str]
) -> None:
    port = environment["VSS_ORCHESTRATOR_MCP_PORT"]
    values = {
        "NEMOCLAW_SANDBOX_NAME": environment["NEMOCLAW_SANDBOX_NAME"],
        "NEMOCLAW_GATEWAY_PORT": environment["NEMOCLAW_GATEWAY_PORT"],
        "NEMOCLAW_DASHBOARD_PORT": environment["NEMOCLAW_DASHBOARD_PORT"],
        "ORCHESTRATOR_ENABLE_HTTPS": environment["ORCHESTRATOR_ENABLE_HTTPS"],
        "MCP_URL": f"http://127.0.0.1:{port}/mcp",
        "VSS_ORCHESTRATOR_MCP_URL": environment["VSS_ORCHESTRATOR_MCP_URL"],
        "VSS_ORCHESTRATOR_MCP_TYPE": environment["VSS_ORCHESTRATOR_MCP_TYPE"],
        "HOST_INTERNAL_ALIAS": environment["HOST_INTERNAL_ALIAS"],
        "HARDWARE_PROFILE": environment.get("HARDWARE_PROFILE", "RTXPRO6000BW"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            f"export {key}={shlex.quote(value)}\n" for key, value in values.items()
        ),
        encoding="utf-8",
    )


def run_notebooks(*, root: Path, env_out: Path, timeout: int) -> None:
    paths = notebook_paths(root)
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing setup notebooks: " + ", ".join(missing))

    stopped = stop_owned_mcp_processes(
        root=root,
        port=int(os.environ["VSS_ORCHESTRATOR_MCP_PORT"]),
    )
    print(f"Scoped MCP cleanup stopped {stopped} prior process(es).")

    nemoclaw = execute_notebook(paths[0], cwd=root, timeout=timeout)
    _require_output(
        nemoclaw,
        "NEMOCLAW_PROVIDER: custom",
        notebook_name=paths[0].name,
    )
    _require_output(
        nemoclaw,
        f"NEMOCLAW_MODEL: {os.environ['NEMOCLAW_MODEL']}",
        notebook_name=paths[0].name,
    )

    orchestrator = execute_notebook(paths[1], cwd=root, timeout=timeout)
    _require_output(
        orchestrator,
        "MCP health check passed:",
        notebook_name=paths[1].name,
    )
    write_runtime_environment(env_out, os.environ)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-out", default=str(DEFAULT_ENV_OUT))
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("NEMOCLAW_SETUP_CELL_TIMEOUT_SEC", "3600")),
    )
    args = parser.parse_args(argv)
    if args.timeout < 60:
        parser.error("--timeout must be at least 60 seconds")

    root = _repo_root()
    prepare_environment(os.environ, root=root)
    endpoint_host = urlsplit(os.environ["NEMOCLAW_ENDPOINT_URL"]).netloc
    print(
        "Phase 1 provider: nvidia-inference "
        f"(notebook provider=custom, endpoint={endpoint_host}, "
        f"model={os.environ['NEMOCLAW_MODEL']})"
    )
    run_notebooks(
        root=root,
        env_out=Path(args.env_out).resolve(),
        timeout=args.timeout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
