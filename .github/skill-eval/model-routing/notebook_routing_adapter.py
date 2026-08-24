#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Execute the checked-in model-routing notebook from beginning to end.

CI inputs are injected in memory just before the notebook's "Derived"
settings; the checked-in source is never modified and the executed copy is
never persisted. The run is real: the router is built from the pinned
Switchyard ref, serves live requests to the upstream, and the VSS repoint
is composed and validated offline. Routing stays disabled by default.

The notebook's code cells are plain Python, so they execute directly in one
shared namespace using only the standard library.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import traceback
from contextlib import redirect_stdout
from pathlib import Path

NOTEBOOK_RELATIVE_PATH = Path("deploy/docker/scripts/deploy_model_routing.ipynb")

_DERIVED_SETTINGS_MARKER = (
    "# ================== Derived (no need to touch) =================="
)

# Notebook variables CI may override through the environment. Everything is a
# string at injection time; the notebook parses booleans and integers itself.
_NOTEBOOK_PARAMETERS = (
    "NVIDIA_API_KEY",
    "UPSTREAM_BASE_URL",
    "UPSTREAM_API_KEY",
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


def prepare_environment(env=None) -> None:
    """Map the CI environment to the notebook's native variables."""
    e = env if env is not None else os.environ
    # CI uses the deployment's existing inference key, never a new one.
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
    # Off the default port so a leftover local router cannot shadow the run;
    # always torn down so the runner is left clean.
    e.setdefault("ROUTER_PORT", "14000")
    e.setdefault("ROUTER_CONTAINER", "vss-model-router-ci")
    e["ROUTER_TEARDOWN"] = "true"
    e.setdefault("MODEL_ROUTING_WORK_DIR", "/tmp/skill-eval/model-routing")


def _cell_source(cell: dict) -> str:
    source = cell.get("source", "")
    return source if isinstance(source, str) else "".join(source)


def _code_cells(notebook: dict) -> list[str]:
    return [
        _cell_source(cell)
        for cell in notebook.get("cells", [])
        if cell.get("cell_type") == "code"
    ]


def _reject_non_plain_python(cells: list[str], name: str) -> None:
    """Refuse notebook-only syntax loudly instead of mis-executing it."""
    for index, source in enumerate(cells):
        for line in source.splitlines():
            stripped = line.lstrip()
            if stripped.startswith(("%", "!")):
                raise RuntimeError(
                    f"{name} cell {index} uses notebook-only syntax "
                    f"({stripped.split()[0]!r}); this adapter executes plain "
                    "Python cells only"
                )


def _parameterize(cells: list[str], name: str) -> list[str]:
    """Apply CI inputs to the in-memory cells without changing the source."""
    assignments = [
        "# Injected by the skill-eval notebook adapter; never persisted.",
        "import os as _skill_eval_os",
        *(
            f"{p} = _skill_eval_os.environ.get({p!r}, {p})"
            for p in _NOTEBOOK_PARAMETERS
        ),
    ]
    parameter_source = "\n".join(assignments)
    for index, source in enumerate(cells):
        if _DERIVED_SETTINGS_MARKER not in source:
            continue
        cells[index] = source.replace(
            _DERIVED_SETTINGS_MARKER,
            f"{parameter_source}\n\n{_DERIVED_SETTINGS_MARKER}",
            1,
        )
        return cells
    raise RuntimeError(f"Could not locate Derived settings in {name}")


def execute_notebook(path: Path, *, cwd: Path) -> str:
    """Run every code cell in order in one namespace; return combined stdout."""
    notebook = json.loads(path.read_text(encoding="utf-8"))
    cells = _code_cells(notebook)
    _reject_non_plain_python(cells, path.name)
    cells = _parameterize(cells, path.name)

    namespace: dict = {"__name__": "__main__"}
    captured = io.StringIO()

    class _Tee(io.TextIOBase):
        def write(self, text: str) -> int:  # stream to the CI log AND capture
            sys.__stdout__.write(text)
            captured.write(text)
            return len(text)

        def flush(self) -> None:
            sys.__stdout__.flush()

    previous_cwd = Path.cwd()
    os.chdir(cwd)
    try:
        for index, source in enumerate(cells):
            code = compile(source, f"{path.name}:cell-{index}", "exec")
            with redirect_stdout(_Tee()):
                try:
                    exec(code, namespace)  # noqa: S102 - checked-in notebook
                except Exception:
                    traceback.print_exc()
                    raise RuntimeError(
                        f"{path.name} failed in code cell {index}"
                    ) from None
    finally:
        os.chdir(previous_cwd)
    print(f"Executed {path.name} from beginning to end; outputs were not persisted.")
    return captured.getvalue()


def run_notebook(*, root: Path) -> None:
    path = root / NOTEBOOK_RELATIVE_PATH
    if not path.is_file():
        raise FileNotFoundError(f"Missing notebook: {path}")
    output = execute_notebook(path, cwd=root)
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
    parser.parse_args(argv)
    prepare_environment()
    run_notebook(root=_repo_root())
    return 0


if __name__ == "__main__":
    sys.exit(main())
