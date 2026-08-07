# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Harness-native Markdown memory note sinks.

Elasticsearch remains the authoritative structured VSS memory store. A sink
writes only the initial human-readable addendum that a harness memory plugin
(OpenClaw ``memory-core`` by default) later indexes, consolidates, and may
promote. VSS never writes ``MEMORY.md``, never manages Markdown TTLs/GC, and
never imports OpenClaw or NAT.
"""

from __future__ import annotations

from collections.abc import Callable
import contextlib
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from enum import StrEnum
import os
from pathlib import Path
import re
import tempfile
from typing import Protocol
from urllib.parse import urlparse

from .models import MemoryGroup
from .models import UnifiedMemoryRecord

DEFAULT_NOTE_PATH_TEMPLATE = "memory/{date}-vss.md"
SUPPORTED_HARNESS_PLUGINS: frozenset[tuple[str, str]] = frozenset({("openclaw", "memory-core")})

_BLOCK_OPEN = "<!-- vss-job:{job_id} -->"
_BLOCK_CLOSE = "<!-- /vss-job:{job_id} -->"

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MemoryNoteStatus(StrEnum):
    """Outcome of a harness memory note write."""

    WRITTEN = "written"
    REPLACED = "replaced"
    SKIPPED = "skipped"
    UNCHANGED = "unchanged"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MemoryNoteWriteResult:
    """Structured result from :class:`MemoryNoteSink.write`."""

    status: MemoryNoteStatus
    path: str | None = None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in {
            MemoryNoteStatus.WRITTEN,
            MemoryNoteStatus.REPLACED,
            MemoryNoteStatus.SKIPPED,
            MemoryNoteStatus.UNCHANGED,
        }

    @property
    def wrote(self) -> bool:
        return self.status in {MemoryNoteStatus.WRITTEN, MemoryNoteStatus.REPLACED}


class MemoryNoteSink(Protocol):
    """Provider boundary for harness-native episodic memory addenda."""

    def write(self, record: UnifiedMemoryRecord, *, persisted: bool = True) -> MemoryNoteWriteResult:
        """Persist a readable note for a validated ``nv.vss.memory/1.0`` record.

        ``persisted`` controls whether the Markdown includes an ES retrieval
        pointer. Callers must pass ``False`` when the authoritative store write
        failed so the note never points at a missing record.
        """


def is_supported_harness_plugin(harness: str, plugin: str) -> bool:
    return (harness.strip().lower(), plugin.strip().lower()) in SUPPORTED_HARNESS_PLUGINS


def expand_workspace(workspace: str | Path) -> Path:
    """Expand ``~`` and environment variables; do not resolve symlinks yet."""
    return Path(os.path.expandvars(os.path.expanduser(str(workspace))))


def resolve_note_path(
    workspace: str | Path,
    *,
    template: str = DEFAULT_NOTE_PATH_TEMPLATE,
    clock: Clock | None = None,
    timezone_name: str = "UTC",
) -> Path:
    """Resolve ``template`` under ``workspace`` with workspace confinement.

    ``{date}`` expands to ``YYYY-MM-DD`` in the configured timezone (UTC when
    unknown). Absolute templates and ``..`` segments are rejected. Symlink
    escapes out of the workspace are rejected after resolution.
    """
    root = expand_workspace(workspace).resolve(strict=False)
    rendered = _render_path_template(template, clock=clock or _utc_now, timezone_name=timezone_name)
    candidate = Path(rendered)
    if candidate.is_absolute():
        raise ValueError(f"note path template must be relative to the workspace, got {template!r}")
    if any(part == ".." for part in candidate.parts):
        raise ValueError(f"note path template must not contain '..': {template!r}")
    target = (root / candidate).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError(f"resolved note path escapes workspace {root}: {target}") from error
    return target


def render_memory_note(record: UnifiedMemoryRecord, *, persisted: bool = True) -> str:
    """Render one human-readable Markdown block for a unified memory record."""
    job_id = record.job.job_id
    group = record.job.group
    title = _group_title(group)
    lines = [
        _BLOCK_OPEN.format(job_id=job_id),
        f"## VSS {title} — `{job_id}`",
        "",
    ]
    if record.job.status and record.job.status != "completed":
        lines.append(f"**Status:** `{record.job.status}`")
        lines.append("")

    query = (record.input.query or "").strip()
    if query:
        lines.append(f"**Request:** {query}")
        lines.append("")

    context_lines = _context_lines(record)
    if context_lines:
        lines.append("**Context:**")
        lines.extend(f"- {item}" for item in context_lines)
        lines.append("")

    answer = (record.output.answer or "").strip()
    if answer:
        lines.append("**Answer:**")
        lines.append("")
        lines.append(answer)
        lines.append("")

    extras = _group_extra_lines(record)
    if extras:
        lines.extend(extras)
        lines.append("")

    if persisted:
        lines.append(f"**Structured record:** `vss memory get {job_id}`")
    else:
        lines.append("**Structured record:** not persisted")
    lines.append(_BLOCK_CLOSE.format(job_id=job_id))
    lines.append("")
    return "\n".join(lines)


@dataclass(slots=True)
class OpenClawMarkdownSink:
    """Write VSS episodic addenda into OpenClaw ``memory/*.md`` files.

    Default organization::

        <workspace>/
        ├── MEMORY.md                 # never written by VSS
        └── memory/
            └── YYYY-MM-DD-vss.md     # VSS programmatic addenda
    """

    workspace: Path
    note_path_template: str = DEFAULT_NOTE_PATH_TEMPLATE
    clock: Clock = field(default=_utc_now)
    timezone_name: str = "UTC"
    harness: str = "openclaw"
    plugin: str = "memory-core"

    def __post_init__(self) -> None:
        if not is_supported_harness_plugin(self.harness, self.plugin):
            supported = ", ".join(sorted(f"{h}/{p}" for h, p in SUPPORTED_HARNESS_PLUGINS))
            raise ValueError(
                f"unsupported harness/plugin combination {self.harness!r}/{self.plugin!r}; supported: {supported}"
            )
        self.workspace = expand_workspace(self.workspace)

    def write(self, record: UnifiedMemoryRecord, *, persisted: bool = True) -> MemoryNoteWriteResult:
        try:
            path = resolve_note_path(
                self.workspace,
                template=self.note_path_template,
                clock=self.clock,
                timezone_name=self.timezone_name,
            )
            if path.name.upper() in {"MEMORY.MD", "DREAMS.MD", "USER.MD"}:
                return MemoryNoteWriteResult(
                    status=MemoryNoteStatus.FAILED,
                    path=str(path),
                    detail=f"refusing to write harness-owned file {path.name}",
                )
            block = render_memory_note(record, persisted=persisted)
            return _upsert_block(
                path=path,
                workspace=self.workspace.resolve(strict=False),
                block=block,
                job_id=record.job.job_id,
            )
        except Exception as error:
            return MemoryNoteWriteResult(status=MemoryNoteStatus.FAILED, detail=str(error))


def _render_path_template(template: str, *, clock: Clock, timezone_name: str) -> str:
    now = clock()
    if timezone_name.upper() != "UTC":
        try:
            from zoneinfo import ZoneInfo

            now = now.astimezone(ZoneInfo(timezone_name))
        except Exception:
            now = now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    else:
        now = now.astimezone(UTC)
    return template.format(date=now.date().isoformat())


def _group_title(group: MemoryGroup | str) -> str:
    return {
        "summary": "summary",
        "search": "search",
        "alert": "alert",
        "media": "media",
        "vlm": "VLM",
    }.get(str(group), str(group))


def _context_lines(record: UnifiedMemoryRecord) -> list[str]:
    lines: list[str] = []
    for sensor in record.input.sensors:
        label = sensor.id or sensor.type or "sensor"
        kind = f" ({sensor.type})" if sensor.type and sensor.id else ""
        lines.append(f"Sensor: `{label}`{kind}")
    window = record.input.window
    if window is not None:
        lines.append(f"Window: {window.start.timestamp} to {window.end.timestamp}")
    category = record.input.params.get("category") or record.input.params.get("alert_category")
    if category:
        lines.append(f"Category: `{category}`")
    severity = record.input.params.get("severity") or record.output.ext.get("severity")
    if severity:
        lines.append(f"Severity: `{severity}`")
    return lines


def _group_extra_lines(record: UnifiedMemoryRecord) -> list[str]:
    group = record.job.group
    ext = record.output.ext
    handles = record.output.handles
    lines: list[str] = []

    if group == "search":
        count = ext.get("result_count")
        if count is None and isinstance(ext.get("results"), list):
            count = len(ext["results"])
        if count is not None:
            lines.append(f"**Result count:** {count}")
    if group == "alert":
        for key in ("incident_ids", "object_ids"):
            values = _stable_ids(ext.get(key))
            if values:
                lines.append(f"**{key.replace('_', ' ').title()}:** {', '.join(f'`{v}`' for v in values[:8])}")
    if group == "media":
        description = ext.get("description") or ext.get("clip_description") or ext.get("media_description")
        if description:
            lines.append(f"**Media:** {description}")
        media_handles = _stable_ids(ext.get("media_ids") or ext.get("clip_ids"))
        if media_handles:
            lines.append(f"**Media handles:** {', '.join(f'`{v}`' for v in media_handles[:8])}")
    if group == "vlm":
        related = _stable_ids(handles.related_job_ids) or _stable_ids(ext.get("related_job_ids"))
        if related:
            lines.append(f"**Related jobs:** {', '.join(f'`{v}`' for v in related[:8])}")
        media_handles = _stable_ids(ext.get("media_ids"))
        if media_handles:
            lines.append(f"**Media handles:** {', '.join(f'`{v}`' for v in media_handles[:8])}")

    if group == "summary":
        event_ids = _stable_ids(ext.get("event_ids"))
        if event_ids:
            lines.append(f"**Event IDs:** {', '.join(f'`{v}`' for v in event_ids[:8])}")

    related = _stable_ids(handles.related_job_ids)
    if related and group != "vlm":
        lines.append(f"**Related jobs:** {', '.join(f'`{v}`' for v in related[:8])}")

    # Explicitly never emit ephemeral media URLs from handles.media_urls.
    _ = [_is_ephemeral_url(url) for url in handles.media_urls]
    return lines


def _stable_ids(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _is_ephemeral_url(url: str) -> bool:
    parsed = urlparse(url)
    if not parsed.scheme:
        return False
    query = parsed.query or ""
    return any(marker in query or marker in url for marker in (
        "X-Amz-",
        "X-Goog-",
        "Signature=",
        "Expires=",
        "AWSAccessKeyId=",
        "se=",
        "sig=",
        "token=",
    ))


def _upsert_block(*, path: Path, workspace: Path, block: str, job_id: str) -> MemoryNoteWriteResult:
    import fcntl

    _ensure_parent_within_workspace(path, workspace)
    lock_path = path.with_name(path.name + ".lock")
    _ensure_parent_within_workspace(lock_path, workspace)
    path.parent.mkdir(parents=True, exist_ok=True)

    parent_resolved = path.parent.resolve(strict=False)
    if not _is_within(parent_resolved, workspace):
        raise ValueError(f"note directory escapes workspace via symlink: {path.parent}")

    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            existing = ""
            if path.exists():
                if path.is_symlink():
                    resolved = path.resolve(strict=False)
                    if not _is_within(resolved, workspace):
                        raise ValueError(f"refusing to follow symlink escaping workspace: {path} -> {resolved}")
                    if resolved.is_file():
                        existing = resolved.read_text(encoding="utf-8")
                elif path.is_file():
                    existing = path.read_text(encoding="utf-8")

            open_tag = _BLOCK_OPEN.format(job_id=job_id)
            close_tag = _BLOCK_CLOSE.format(job_id=job_id)
            pattern = re.compile(re.escape(open_tag) + r"\n.*?" + re.escape(close_tag) + r"\n?", re.DOTALL)
            match = pattern.search(existing)
            if match is not None:
                current = match.group(0)
                if current.rstrip("\n") == block.rstrip("\n"):
                    return MemoryNoteWriteResult(status=MemoryNoteStatus.UNCHANGED, path=str(path))
                updated = existing[: match.start()] + block + existing[match.end() :]
                status = MemoryNoteStatus.REPLACED
            else:
                if existing and not existing.endswith("\n"):
                    existing += "\n"
                updated = existing + block
                status = MemoryNoteStatus.WRITTEN
            _atomic_write(path, updated, workspace=workspace)
            return MemoryNoteWriteResult(status=status, path=str(path))
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _ensure_parent_within_workspace(path: Path, workspace: Path) -> None:
    parent = path.parent
    # Allow creating not-yet-existing directories under the workspace.
    try:
        tentative = parent if parent.is_absolute() else (workspace / parent)
        tentative.resolve(strict=False).relative_to(workspace.resolve(strict=False))
    except ValueError as error:
        raise ValueError(f"path parent escapes workspace {workspace}: {parent}") from error


def _is_within(path: Path, workspace: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(workspace.resolve(strict=False))
        return True
    except ValueError:
        return False


def _atomic_write(path: Path, content: str, *, workspace: Path) -> None:
    directory = path.parent
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=directory)
    tmp_path = Path(tmp_name)
    try:
        if not _is_within(tmp_path, workspace):
            raise ValueError("temporary note file escaped workspace")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise


__all__ = [
    "DEFAULT_NOTE_PATH_TEMPLATE",
    "SUPPORTED_HARNESS_PLUGINS",
    "MemoryNoteSink",
    "MemoryNoteStatus",
    "MemoryNoteWriteResult",
    "OpenClawMarkdownSink",
    "expand_workspace",
    "is_supported_harness_plugin",
    "render_memory_note",
    "resolve_note_path",
]
