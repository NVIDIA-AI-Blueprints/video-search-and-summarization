# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compact Markdown rendering for agent-facing memory caches."""

from __future__ import annotations

import html
import re
import shlex

from .models import UnifiedMemoryRecord

MAX_MEMORY_NOTE_CHARS = 8_192
MAX_REQUEST_CHARS = 1_024
MAX_ANSWER_CHARS = 6_000
MAX_SENSORS = 8

_BLOCK_OPEN = "<!-- vss-job:{job_id} -->"
_BLOCK_CLOSE = "<!-- /vss-job:{job_id} -->"


def render_memory_note(record: UnifiedMemoryRecord) -> str:
    """Render one deterministic, bounded Markdown block for a parent record."""
    job_id = _one_line(record.job.job_id)
    marker_id = html.escape(job_id, quote=True)
    lines = [
        _BLOCK_OPEN.format(job_id=marker_id),
        f"## VSS {record.job.group} — {_inline_code(job_id)}",
        "",
    ]
    if record.job.status != "completed":
        lines.extend((f"**Status:** {_inline_code(record.job.status)}", ""))

    input_data = record.input
    if input_data is not None:
        request = _truncate((input_data.query or "").strip(), MAX_REQUEST_CHARS)
        if request:
            lines.extend(("**Request:**", "", _fenced(request), ""))

        context: list[str] = []
        for sensor in (input_data.sensors or [])[:MAX_SENSORS]:
            label = _one_line(sensor.id)
            if sensor.type:
                label = f"{label} ({_one_line(sensor.type)})"
            context.append(f"Sensor: {_inline_code(label)}")
        if input_data.window is not None and input_data.window.start is not None and input_data.window.end is not None:
            context.append(
                "Window: "
                f"{_inline_code(str(input_data.window.start.timestamp))} to "
                f"{_inline_code(str(input_data.window.end.timestamp))}"
            )
        if context:
            lines.append("**Context:**")
            lines.extend(f"- {item}" for item in context)
            lines.append("")

    output = record.output
    if output is not None:
        answer = _truncate((output.answer or "").strip(), MAX_ANSWER_CHARS)
        if answer:
            lines.extend(("**Answer:**", "", _fenced(answer), ""))
        counts = _count_lines(output.ext or {})
        if counts:
            lines.extend(counts)
            lines.append("")

    pointer = f"vss memory get --job-id {shlex.quote(job_id)}"
    query_pointer = f"vss memory query --job-id {shlex.quote(job_id)}"
    lines.extend(
        (
            "**Authoritative Elasticsearch record:**",
            "",
            "```console",
            pointer,
            query_pointer,
            "```",
            _BLOCK_CLOSE.format(job_id=marker_id),
            "",
        )
    )
    rendered = "\n".join(lines)
    if len(rendered) > MAX_MEMORY_NOTE_CHARS:
        raise ValueError(f"memory note exceeds {MAX_MEMORY_NOTE_CHARS} characters")
    return rendered


def block_markers(job_id: str) -> tuple[str, str]:
    """Stable, comment-safe markers used by note storage."""
    marker_id = html.escape(_one_line(job_id), quote=True)
    return (
        _BLOCK_OPEN.format(job_id=marker_id),
        _BLOCK_CLOSE.format(job_id=marker_id),
    )


def _count_lines(ext: dict[str, object]) -> list[str]:
    labels = (
        ("event_count", "Event count"),
        ("result_count", "Result count"),
        ("incident_count", "Incident count"),
    )
    return [f"**{label}:** {value}" for key, label in labels if isinstance((value := ext.get(key)), int)]


def _one_line(value: object) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def _inline_code(value: object) -> str:
    return f"`{html.escape(_one_line(value), quote=True).replace('`', '&#96;')}`"


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _fenced(value: str) -> str:
    """Fence user-controlled text with a delimiter it cannot close."""
    safe = value.replace("<!--", "&lt;!--").replace("-->", "--&gt;")
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", safe)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{safe}\n{fence}"


__all__ = [
    "MAX_ANSWER_CHARS",
    "MAX_MEMORY_NOTE_CHARS",
    "MAX_REQUEST_CHARS",
    "block_markers",
    "render_memory_note",
]
