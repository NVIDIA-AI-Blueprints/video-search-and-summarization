#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Execute the checked-in model-routing notebook from beginning to end.

Same contract as the NemoClaw notebook adapter: CI inputs are mapped to the
notebook's native variables and injected in memory just before the notebook's
own "Derived" settings; the checked-in notebook source is never modified and
the executed copy is never persisted. The run is end to end and real — the
router is built from the pinned Switchyard ref, serves live requests to the
upstream, and the VSS repoint is composed and validated offline. Routing
stays disabled by default: the notebook deploys nothing.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

NOTEBOOK_RELATIVE_PATH = Path("deploy/docker/scripts/deploy_model_routing.ipynb")

_DERIVED_SETTINGS_MARKER = (
    "# ================== Derived (no need to touch) =================="
)

# Notebook variables CI may override through the environment. Everything is a
# string at injection time; the notebook's derived section parses booleans and
# integers itself.
_NOTEBOOK_PARAMETERS = (
    "NVIDIA_API_KEY",
    "UPSTREAM_BASE_URL",
    "ROUTER_TARGET_CAPABLE",
    "ROUTER_TARGET_EFFICIENT",
    "ROUTER_PORT",
    "ROUTER_CONTAINER",
    "ROUTER_TEARDOWN",
)

# Output lines the executed notebook must have printed for the run to count.
_READINESS_MARKERS = (
    "ROUTER_VERIFIED:",
    "VSS_ROUTING_COMPOSE: valid",
    "ROUTER_TEARDOWN: done",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def prepare_environment(env: os._Environ | dict | None = None) -> None:
    """Map the CI provider contract to the notebook's native variables."""
    e = env if env is not None else os.environ
    # The coordinator's inference credential is the same NVIDIA hub key under
    # its Anthropic-compatible name; use the existing key, never a new one.
    key = (
        e.get("NVIDIA_API_KEY")
        or e.get("ANTHROPIC_API_KEY")
        or e.get("OPENAI_API_KEY")
        or ""
    ).strip()
    if not key:
        raise RuntimeError(
            "NVIDIA_API_KEY is required: the router forwards to the NVIDIA "
            "inference hub during the end-to-end verification"
        )
    e["NVIDIA_API_KEY"] = key
    # Off the default 4000 so a leftover local router can't shadow the run,
    # and always torn down so the runner is left clean.
    e.setdefault("ROUTER_PORT", "14000")
    e.setdefault("ROUTER_CONTAINER", "vss-model-router-ci")
    e["ROUTER_TEARDOWN"] = "true"
    e.setdefault(
        "MODEL_ROUTING_WORK_DIR", "/tmp/skill-eval/model-routing"
    )


def _parameterize_notebook(notebook: Any, name: str) -> None:
    """Apply CI inputs to the in-memory notebook without changing its source."""
    assignments = [
        "# Injected by the skill-eval notebook adapter; never persisted.",
        "import os as _skill_eval_os",
        *(
            f"{p} = _skill_eval_os.environ.get({p!r}, {p})"
            for p in _NOTEBOOK_PARAMETERS
        ),
    ]
    parameter_source = "\n".join(assignments)
    for cell in notebook.get("cells", []):
        source_value = cell.get("source", "")
        source = (
            source_value if isinstance(source_value, str) else "".join(source_value)
        )
        if _DERIVED_SETTINGS_MARKER not in source:
            continue
        cell["source"] = source.replace(
            _DERIVED_SETTINGS_MARKER,
            f"{parameter_source}\n\n{_DERIVED_SETTINGS_MARKER}",
            1,
        )
        return
    raise RuntimeError(f"Could not locate Derived settings in {name}")


def _output_text(notebook: Any) -> str:
    chunks: list[str] = []
    for cell in notebook.get("cells", []):
        for output in cell.get("outputs", []):
            if "text" in output:
                text = output["text"]
                chunks.append(text if isinstance(text, str) else "".join(text))
            for value in output.get("data", {}).values():
                chunks.append(value if isinstance(value, str) else "".join(value))
    return "\n".join(chunks)


def execute_notebook(path: Path, *, cwd: Path, timeout: int) -> Any:
    try:
        import nbformat
        from nbclient import NotebookClient
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(
            "Notebook execution requires nbformat, nbclient, and ipykernel"
        ) from exc

    notebook = nbformat.read(path, as_version=4)
    _parameterize_notebook(notebook, path.name)
    client = NotebookClient(
        notebook,
        timeout=timeout,
        kernel_name=os.environ.get("MODEL_ROUTING_CI_KERNEL", "python3"),
        allow_errors=False,
        resources={"metadata": {"path": str(cwd)}},
    )
    executed = client.execute()
    print(f"Executed {path.name} from beginning to end; outputs were not persisted.")
    return executed


def run_notebook(*, root: Path, timeout: int) -> None:
    path = root / NOTEBOOK_RELATIVE_PATH
    if not path.is_file():
        raise FileNotFoundError(f"Missing notebook: {path}")
    executed = execute_notebook(path, cwd=root, timeout=timeout)
    output = _output_text(executed)
    missing = [marker for marker in _READINESS_MARKERS if marker not in output]
    if missing:
        raise RuntimeError(
            f"{path.name} completed without readiness marker(s): "
            + ", ".join(missing)
        )
    for line in output.splitlines():
        if any(marker in line for marker in _READINESS_MARKERS):
            print(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("MODEL_ROUTING_CELL_TIMEOUT_SEC", "1800")),
    )
    args = parser.parse_args(argv)
    if args.timeout < 60:
        parser.error("--timeout must be at least 60 seconds")
    prepare_environment()
    run_notebook(root=_repo_root(), timeout=args.timeout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
