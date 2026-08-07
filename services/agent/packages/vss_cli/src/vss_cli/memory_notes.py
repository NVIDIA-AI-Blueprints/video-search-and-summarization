# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared harness memory-note decision + invocation for job-producing groups.

``--write-memory-note`` / ``--no-write-memory-note`` is owned here (and exposed
through :func:`vss_cli.params.job_memory_options`) so every future group
inherits the same flag without reimplementing the provider boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any

from vss_core.memory.notes import MemoryNoteStatus
from vss_core.memory.notes import MemoryNoteWriteResult
from vss_core.memory.notes import OpenClawMarkdownSink
from vss_core.memory.notes import expand_workspace
from vss_core.memory.notes import is_supported_harness_plugin

from . import config as config_mod
from .group import InvalidInput

if TYPE_CHECKING:
    from vss_core.memory.models import UnifiedMemoryRecord


@dataclass(frozen=True, slots=True)
class MemoryNoteDecision:
    """Resolved three-state write-memory-note preference for one invocation."""

    enabled: bool
    forced: bool
    """True when the caller passed ``--write-memory-note`` or ``--no-write-memory-note``."""


def resolve_write_memory_note(extra: dict[str, Any], *, config: config_mod.MemoryConfig | None = None) -> MemoryNoteDecision:
    """Resolve ``--write-memory-note`` against ``~/.vss/config.json`` defaults."""
    memory = config if config is not None else config_mod.load_memory_config()
    flag = extra.get("write_memory_note")
    if flag is True:
        return MemoryNoteDecision(enabled=True, forced=True)
    if flag is False:
        return MemoryNoteDecision(enabled=False, forced=True)
    default = bool(memory.harness_sink.enabled and memory.harness_sink.write_memory_notes_default)
    return MemoryNoteDecision(enabled=default, forced=False)


def preflight_memory_note(
    *,
    persist: bool,
    decision: MemoryNoteDecision,
    config: config_mod.MemoryConfig | None = None,
) -> None:
    """Fail before the expensive backend when the note write cannot succeed.

    ``--no-persist --write-memory-note`` is invalid: no ES pointer can be
    produced. An explicit ``--write-memory-note`` also requires a configured,
    supported harness sink.
    """
    if decision.enabled and not persist:
        raise InvalidInput("cannot combine --no-persist with --write-memory-note (no ES pointer can be produced)")
    if not decision.enabled:
        return
    if not decision.forced and not persist:
        return
    memory = config if config is not None else config_mod.load_memory_config()
    sink = memory.harness_sink
    if decision.forced:
        if not sink.enabled:
            raise config_mod.ConfigError(
                "harness memory notes are not configured; run "
                "`vss configure memory --harness openclaw --plugin memory-core "
                "--workspace <path> --enable-memory-notes`"
            )
        if not is_supported_harness_plugin(sink.harness, sink.plugin):
            raise config_mod.ConfigError(
                f"unsupported harness memory provider {sink.harness!r}/{sink.plugin!r}; "
                f"configure an openclaw/memory-core sink via `vss configure memory`"
            )
        workspace = str(sink.workspace or "").strip()
        if not workspace:
            raise config_mod.ConfigError("harness memory sink requires --workspace")


def build_openclaw_sink(config: config_mod.MemoryConfig | None = None) -> OpenClawMarkdownSink:
    memory = config if config is not None else config_mod.load_memory_config()
    sink = memory.harness_sink
    if not is_supported_harness_plugin(sink.harness, sink.plugin):
        raise config_mod.ConfigError(
            f"unsupported harness memory provider {sink.harness!r}/{sink.plugin!r}"
        )
    return OpenClawMarkdownSink(
        workspace=expand_workspace(sink.workspace),
        note_path_template=sink.note_path_template,
        timezone_name=sink.timezone,
        harness=sink.harness,
        plugin=sink.plugin,
    )


def write_memory_note(
    record: UnifiedMemoryRecord,
    *,
    persisted: bool,
    config: config_mod.MemoryConfig | None = None,
) -> MemoryNoteWriteResult:
    """Write the harness Markdown addendum after a successful ES persist."""
    if not persisted:
        return MemoryNoteWriteResult(
            status=MemoryNoteStatus.SKIPPED,
            detail="structured record was not persisted; refusing ES pointer",
        )
    try:
        sink = build_openclaw_sink(config)
    except (config_mod.ConfigError, ValueError) as error:
        return MemoryNoteWriteResult(status=MemoryNoteStatus.FAILED, detail=str(error))
    return sink.write(record, persisted=True)


def note_result_payload(result: MemoryNoteWriteResult) -> dict[str, Any]:
    return {
        "status": str(result.status),
        "written": result.wrote,
        "path": result.path,
        "detail": result.detail,
    }


__all__ = [
    "MemoryNoteDecision",
    "build_openclaw_sink",
    "note_result_payload",
    "preflight_memory_note",
    "resolve_write_memory_note",
    "write_memory_note",
]
