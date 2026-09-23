#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Create a compact RTVI BYOM evidence report from observed JSON facts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


GATES = [
    ("no_eager_by_default", "Eager mode is not used unless forced"),
    ("platform_agnostic", "Implementation is not locked to one platform"),
    ("shim_before_patch", "Shim/plugin was attempted before vLLM patching"),
    ("legible_output", "Output is legible"),
    ("grounded_output", "Output is grounded in the prompt/media"),
]


def _status(value: Any) -> str:
    if value is True:
        return "PASS"
    if value is False:
        return "FAIL"
    return "UNKNOWN"


def _md_cell(value: Any, limit: int | None = None) -> str:
    text = str(value).replace("\n", " ").replace("|", r"\|")
    return text[:limit] if limit else text


def build_report(data: dict[str, Any]) -> str:
    model = data.get("model", {})
    if not isinstance(model, dict):
        model = {}
    validation = data.get("validation", {})
    if not isinstance(validation, dict):
        validation = {}

    lines = [
        f"# RTVI BYOM Port Report: {_md_cell(model.get('name', 'unknown model'))}",
        "",
        "## Model",
        "",
        f"- Source: `{model.get('source', 'unknown')}`",
        f"- Revision/tag: `{model.get('revision', 'unknown')}`",
        f"- Backend: `{model.get('backend', 'unknown')}`",
        f"- Container: `{model.get('container', 'unknown')}`",
        "",
        "## Gates",
        "",
        "| Gate | Status | Evidence |",
        "|---|---:|---|",
    ]
    for key, label in GATES:
        item = validation.get(key, {})
        if not isinstance(item, dict):
            item = {"status": item}
        evidence = _md_cell(item.get("evidence", ""))
        lines.append(f"| {label} | {_status(item.get('status'))} | {evidence or '-'} |")

    lines.extend(["", "## Smoke Results", ""])
    smoke = data.get("smoke", [])
    if not isinstance(smoke, list):
        smoke = []
    smoke_rows = [row for row in smoke if isinstance(row, dict)]
    if smoke_rows:
        lines.extend(["| Prompt | Status | Output sample |", "|---|---:|---|"])
        for row in smoke_rows:
            name = _md_cell(row.get("name", "unnamed"))
            sample = _md_cell(row.get("sample", ""), 240)
            lines.append(f"| {name} | {_status(row.get('status'))} | {sample} |")
    else:
        lines.append("- No smoke results provided.")

    caveats = data.get("caveats", [])
    if not isinstance(caveats, list):
        caveats = [str(caveats)]
    lines.extend(["", "## Caveats", ""])
    lines.extend([f"- {c}" for c in caveats] or ["- None recorded."])

    next_step = data.get(
        "next_step", "Run a bounded smoke and quality validation and retain raw responses."
    )
    lines.extend(["", "## Next Step", "", f"- {next_step}", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-json", required=True, help="Observed BYOM facts as JSON")
    parser.add_argument("--write-markdown", required=True, help="Markdown report path")
    parser.add_argument("--write-json", help="Normalized JSON output path")
    args = parser.parse_args()

    data = json.loads(Path(args.input_json).read_text())
    if not isinstance(data, dict):
        raise SystemExit("input JSON must be an object")
    Path(args.write_markdown).write_text(build_report(data))
    if args.write_json:
        Path(args.write_json).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
