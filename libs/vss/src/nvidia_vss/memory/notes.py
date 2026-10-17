# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compact Markdown rendering for agent-facing memory caches."""

from __future__ import annotations

from collections.abc import Callable
import contextlib
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
import html
import os
from pathlib import Path
import re
import shlex
import tempfile
from typing import Literal

from .models import UnifiedMemoryRecord

MAX_MEMORY_NOTE_CHARS = 8_192
MAX_REQUEST_CHARS = 1_024
MAX_ANSWER_CHARS = 6_000
MAX_SENSORS = 8

_BLOCK_OPEN = "<!-- vss-job:{job_id} -->"
_BLOCK_CLOSE = "<!-- /vss-job:{job_id} -->"

Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class MemoryNoteWriteResult:
    """Outcome of one idempotent OpenClaw daily-note update."""

    written: bool
    path: str
    job_id: str
    status: Literal["written", "replaced", "unchanged"]


class OpenClawDailyNoteStore:
    """Atomically upsert bounded VSS blocks in ``memory/YYYY-MM-DD-vss.md``."""

    def __init__(self, workspace: str | Path, *, clock: Clock | None = None) -> None:
        supplied = Path(workspace)
        if not supplied.is_absolute():
            raise ValueError("OpenClaw workspace must be an absolute path")
        if ".." in supplied.parts:
            raise ValueError("OpenClaw workspace must not contain '..'")
        try:
            root = supplied.resolve(strict=True)
        except OSError as error:
            raise ValueError(f"OpenClaw workspace does not exist: {supplied}") from error
        if not root.is_dir():
            raise ValueError(f"OpenClaw workspace is not a directory: {root}")
        self._workspace = root
        self._clock = clock or (lambda: datetime.now(UTC))
        self._note_path()

    def write(self, record: UnifiedMemoryRecord) -> MemoryNoteWriteResult:
        if record.job.is_child:
            raise ValueError("Markdown memory notes are parent-job summaries, not child records")
        path = self._note_path()
        relative = path.relative_to(self._workspace).as_posix()
        block = render_memory_note(record)
        status = _upsert_block(path, workspace=self._workspace, block=block, job_id=record.job.job_id)
        return MemoryNoteWriteResult(
            written=status != "unchanged",
            path=relative,
            job_id=record.job.job_id,
            status=status,
        )

    def _note_path(self) -> Path:
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        else:
            now = now.astimezone(UTC)
        path = self._workspace / "memory" / f"{now.date().isoformat()}-vss.md"
        resolved_parent = path.parent.resolve(strict=False)
        try:
            resolved_parent.relative_to(self._workspace)
        except ValueError as error:
            raise ValueError(f"OpenClaw memory path escapes workspace: {resolved_parent}") from error
        return path


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


def _upsert_block(
    path: Path,
    *,
    workspace: Path,
    block: str,
    job_id: str,
) -> Literal["written", "replaced", "unchanged"]:
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    resolved_parent = path.parent.resolve(strict=True)
    try:
        resolved_parent.relative_to(workspace)
    except ValueError as error:
        raise ValueError(f"OpenClaw memory directory escapes workspace: {resolved_parent}") from error

    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            open_marker, close_marker = block_markers(job_id)
            pattern = re.compile(
                re.escape(open_marker) + r"\n.*?" + re.escape(close_marker) + r"\n?",
                re.DOTALL,
            )
            match = pattern.search(existing)
            if match is not None:
                if match.group(0).rstrip("\n") == block.rstrip("\n"):
                    return "unchanged"
                updated = existing[: match.start()] + block + existing[match.end() :]
                status: Literal["written", "replaced", "unchanged"] = "replaced"
            else:
                if open_marker in existing or close_marker in existing:
                    raise ValueError(f"malformed existing memory-note block for job_id={job_id!r}")
                separator = "" if not existing or existing.endswith("\n") else "\n"
                updated = existing + separator + block
                status = "written"
            _atomic_write(path, updated, workspace=workspace)
            return status
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: Path, content: str, *, workspace: Path) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        temporary.resolve(strict=True).relative_to(workspace)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


__all__ = [
    "MAX_ANSWER_CHARS",
    "MAX_MEMORY_NOTE_CHARS",
    "MAX_REQUEST_CHARS",
    "MemoryNoteWriteResult",
    "OpenClawDailyNoteStore",
    "block_markers",
    "render_memory_note",
]
