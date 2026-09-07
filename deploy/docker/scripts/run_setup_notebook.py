#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Execute a checked-in VSS setup notebook end to end.

Run the notebook; do not reimplement its cells. Pass overrides through the
environment. Re-inject `NOTEBOOK_PARAMETERS` at the derived-settings marker.
Keep the executed notebook in memory — never write it back.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

# Section 1.3 of every setup notebook opens its derived block with this line.
# It is the first point where all of the settings literals are in scope, which
# is what makes it the correct place to re-read the environment.
DERIVED_SETTINGS_MARKER = (
    "# ================== Derived (no need to touch) =================="
)

# Per-notebook parameter contract: the variables that notebook's settings cells
# assign as literals. Keep an entry here when a new caller needs to override a
# literal; a variable the notebook already reads from `SHELL_ENV` does not
# belong in this table.
NOTEBOOK_PARAMETERS: dict[str, tuple[str, ...]] = {
    "deploy_nemoclaw.ipynb": (
        "NEMOCLAW_PROVIDER",
        "NEMOCLAW_ENDPOINT_URL",
        "NEMOCLAW_MODEL",
        "COMPATIBLE_API_KEY",
    ),
    "deploy_vss_orchestrator.ipynb": (
        "NGC_CLI_API_KEY",
        "NVIDIA_API_KEY",
        "HARDWARE_PROFILE",
        "EXTERNAL_IP",
        "LLM_DEVICE_ID",
        "VLM_DEVICE_ID",
        "LLM_NAME",
        "LLM_ENDPOINT_URL",
        "LLM_MODEL_TYPE",
        "LLM_ENABLE_THINKING",
        "OPENAI_API_KEY",
        "VLM_NAME",
        "VLM_ENDPOINT_URL",
        "VLM_MODEL_TYPE",
    ),
}

_MINIMUM_TIMEOUT_SEC = 60


def repo_root() -> Path:
    """Repository root, resolved from this file's location in the checkout."""

    return Path(__file__).resolve().parents[3]


def parameters_for(path: Path) -> tuple[str, ...]:
    """Parameter contract for *path*, or raise when the notebook has none."""

    parameters = NOTEBOOK_PARAMETERS.get(path.name)
    if parameters is None:
        known = ", ".join(sorted(NOTEBOOK_PARAMETERS))
        raise ValueError(
            f"No parameter contract for notebook {path.name}; known: {known}"
        )
    return parameters


def parameterize_notebook(
    notebook: Any, parameters: Iterable[str], *, label: str = "notebook"
) -> None:
    """Re-read *parameters* from the environment at the derived-settings marker.

    Mutates the in-memory notebook only; the checked-in source is never edited.
    """

    names = tuple(parameters)
    if not names:
        return
    assignments = [
        "# Injected by run_setup_notebook; never persisted.",
        "import os as _vss_setup_os",
        *(
            f"{name} = _vss_setup_os.environ.get({name!r}, {name})"
            for name in names
        ),
    ]
    parameter_source = "\n".join(assignments)

    for cell in notebook.get("cells", []):
        source_value = cell.get("source", "")
        source = (
            source_value if isinstance(source_value, str) else "".join(source_value)
        )
        if DERIVED_SETTINGS_MARKER not in source:
            continue
        cell["source"] = source.replace(
            DERIVED_SETTINGS_MARKER,
            f"{parameter_source}\n\n{DERIVED_SETTINGS_MARKER}",
            1,
        )
        return
    raise RuntimeError(f"Could not locate the derived-settings marker in {label}")


def execute_notebook(
    path: Path,
    *,
    cwd: Path,
    timeout: int,
    parameters: Iterable[str] | None = None,
    kernel_name: str | None = None,
) -> Any:
    """Run *path* end to end and return the executed in-memory notebook."""

    try:
        import nbformat
        from nbclient import NotebookClient
    except ImportError as exc:
        raise RuntimeError(
            "Notebook execution requires nbformat, nbclient, and ipykernel"
        ) from exc

    notebook = nbformat.read(path, as_version=4)
    parameterize_notebook(
        notebook,
        parameters_for(path) if parameters is None else parameters,
        label=path.name,
    )
    client = NotebookClient(
        notebook,
        timeout=timeout,
        kernel_name=(
            kernel_name
            or os.environ.get("VSS_SETUP_NOTEBOOK_KERNEL")
            or "python3"
        ),
        allow_errors=False,
        resources={"metadata": {"path": str(cwd)}},
    )
    executed = client.execute()
    print(f"Executed {path.name} from beginning to end; outputs were not persisted.")
    return executed


def output_text(notebook: Any) -> str:
    """Every stream and result payload the executed notebook produced."""

    chunks: list[str] = []
    for cell in notebook.get("cells", []):
        for output in cell.get("outputs", []):
            if output.get("output_type") == "stream":
                chunks.append(str(output.get("text", "")))
            elif output.get("output_type") in {"display_data", "execute_result"}:
                chunks.append(str(output.get("data", {}).get("text/plain", "")))
    return "\n".join(chunks)


def require_output(notebook: Any, marker: str, *, notebook_name: str) -> None:
    """Fail when *marker* is absent from the executed notebook's output.

    A notebook runs with `allow_errors=False`, so a failed cell already aborts
    the run. This covers the other case: a notebook that completed but skipped
    the step the caller depends on.
    """

    if marker not in output_text(notebook):
        raise RuntimeError(
            f"{notebook_name} completed without readiness marker: {marker}"
        )


def run_notebooks(
    paths: Sequence[Path],
    *,
    cwd: Path,
    timeout: int,
    required_output: Sequence[str] = (),
) -> None:
    """Execute *paths* in order, then assert every required marker was printed."""

    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing setup notebooks: " + ", ".join(missing))

    combined: list[str] = []
    for path in paths:
        executed = execute_notebook(path, cwd=cwd, timeout=timeout)
        combined.append(output_text(executed))

    produced = "\n".join(combined)
    absent = [marker for marker in required_output if marker not in produced]
    if absent:
        raise RuntimeError(
            "Setup completed without readiness marker(s): " + ", ".join(absent)
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--notebook",
        action="append",
        required=True,
        metavar="PATH",
        help="Setup notebook to execute; repeat to run several in order.",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        metavar="PATH",
        help="Working directory for the kernel (default: repository root).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("VSS_SETUP_CELL_TIMEOUT_SEC", "3600")),
        help="Per-cell timeout in seconds (default: 3600).",
    )
    parser.add_argument(
        "--require-output",
        action="append",
        default=[],
        metavar="MARKER",
        help=(
            "Fail unless MARKER appears in the executed output; "
            "repeat to require several."
        ),
    )
    args = parser.parse_args(argv)
    if args.timeout < _MINIMUM_TIMEOUT_SEC:
        parser.error(f"--timeout must be at least {_MINIMUM_TIMEOUT_SEC} seconds")

    root = repo_root()
    paths = [Path(notebook).resolve() for notebook in args.notebook]
    for path in paths:
        # Resolve every contract before a kernel starts: an unknown notebook is
        # a caller mistake, not a run to abandon halfway through.
        try:
            parameters_for(path)
        except ValueError as exc:
            parser.error(str(exc))
        if not path.is_file():
            parser.error(f"No such notebook: {path}")

    run_notebooks(
        paths,
        cwd=Path(args.cwd).resolve() if args.cwd else root,
        timeout=args.timeout,
        required_output=tuple(args.require_output),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
