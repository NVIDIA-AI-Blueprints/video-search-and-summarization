#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Create a compact RTVI BYOM evidence report from observed JSON facts."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


GATES = [
    ("no_eager_by_default", "Eager mode is not used unless forced"),
    ("platform_agnostic", "Implementation is not locked to one platform"),
    ("shim_before_patch", "Shim/plugin was attempted before vLLM patching"),
    ("legible_output", "Output is legible"),
    ("grounded_output", "Output is grounded in the prompt/media"),
]


def _status(value: Any, evidence: Any = None) -> str:
    if value is True and str(evidence or "").strip():
        return "PASS"
    if value is False:
        return "FAIL"
    return "UNKNOWN"


def _one_line(value: Any, limit: int | None = None) -> str:
    text = " ".join(str(value).splitlines())
    return text[:limit] if limit is not None else text


def _md_text(value: Any, limit: int | None = None) -> str:
    return re.sub(r"([\\`*_{}\[\]()<>#+\-.!|~&])", r"\\\1", _one_line(value, limit))


def _md_code(value: Any) -> str:
    text = _one_line(value)
    if not text:
        return "<code></code>"
    fence = "`" * (max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    padding = (
        " "
        if not text.isspace() and (text.startswith(("`", " ")) or text.endswith(("`", " ")))
        else ""
    )
    return f"{fence}{padding}{text}{padding}{fence}"


def build_report(data: dict[str, Any]) -> str:
    model = data.get("model", {})
    if not isinstance(model, dict):
        model = {}
    validation = data.get("validation", {})
    if not isinstance(validation, dict):
        validation = {}

    lines = [
        f"# RTVI BYOM Port Report: {_md_text(model.get('name', 'unknown model'))}",
        "",
        "## Model",
        "",
        f"- Source: {_md_code(model.get('source', 'unknown'))}",
        f"- Revision/tag: {_md_code(model.get('revision', 'unknown'))}",
        f"- Backend: {_md_code(model.get('backend', 'unknown'))}",
        f"- Container: {_md_code(model.get('container', 'unknown'))}",
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
        evidence = _md_text(item.get("evidence", ""))
        lines.append(
            f"| {label} | {_status(item.get('status'), evidence)} | {evidence or '-'} |"
        )

    lines.extend(["", "## Smoke Results", ""])
    smoke = data.get("smoke", [])
    if not isinstance(smoke, list):
        smoke = []
    smoke_rows = [row for row in smoke if isinstance(row, dict)]
    if smoke_rows:
        lines.extend(["| Prompt | Status | Output sample |", "|---|---:|---|"])
        for row in smoke_rows:
            name = _md_text(row.get("name", "unnamed"))
            sample = _md_text(row.get("sample", ""), 240)
            lines.append(f"| {name} | {_status(row.get('status'), sample)} | {sample or '-'} |")
    else:
        lines.append("- No smoke results provided.")

    integration = data.get("integration", {})
    if not isinstance(integration, dict):
        integration = {}
    performance = data.get("performance", {})
    if not isinstance(performance, dict):
        performance = {}
    lines.extend(
        [
            "",
            "## Runtime Evidence",
            "",
            f"- Integration path: {_md_text(integration.get('path', 'unknown'))}",
            f"- Image digest: {_md_code(integration.get('image_digest', 'unknown'))}",
            f"- Eager mode: {_md_text(integration.get('eager_mode', 'unknown'))}",
            f"- Eager reason: {_md_text(integration.get('eager_reason', 'not recorded'))}",
            f"- Platform evidence: {_md_text(integration.get('platforms', 'not recorded'))}",
            f"- Custom kernels: {_md_text(integration.get('custom_kernels', 'not recorded'))}",
            f"- Accuracy: {_md_text(performance.get('accuracy', 'not recorded'))}",
            f"- Latency: {_md_text(performance.get('latency', 'not recorded'))}",
            f"- Throughput: {_md_text(performance.get('throughput', 'not recorded'))}",
            f"- GPU utilization: {_md_text(performance.get('gpu_utilization', 'not recorded'))}",
            f"- GPU memory: {_md_text(performance.get('gpu_memory', 'not recorded'))}",
        ]
    )

    caveats = data.get("caveats", [])
    if not isinstance(caveats, list):
        caveats = [str(caveats)]
    lines.extend(["", "## Caveats", ""])
    lines.extend([f"- {_md_text(c)}" for c in caveats] or ["- None recorded."])

    next_step = data.get(
        "next_step", "Run a bounded smoke and quality validation and retain raw responses."
    )
    lines.extend(["", "## Next Step", "", f"- {_md_text(next_step)}", ""])
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
