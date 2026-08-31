# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Invoke the statically configured agent-facing Markdown cache."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import config as config_mod

if TYPE_CHECKING:
    from vss_core.memory.models import UnifiedMemoryRecord
    from vss_core.memory.notes import MemoryNoteWriteResult


def write(record: UnifiedMemoryRecord, deployment: config_mod.Deployment) -> MemoryNoteWriteResult:
    """Write one parent note using the configured OpenClaw workspace."""
    memory_config = deployment.memory
    if memory_config is None or not memory_config.markdown.enabled:
        raise config_mod.ConfigError(
            "Markdown memory is not enabled; run `vss configure memory --markdown "
            "--workspace /absolute/openclaw/workspace`"
        )
    if memory_config.markdown.harness != "openclaw":
        raise config_mod.ConfigError(
            f"unsupported Markdown memory harness {memory_config.markdown.harness!r}; "
            "run `vss configure memory --harness openclaw`"
        )
    from vss_core.memory.notes import OpenClawDailyNoteStore

    return OpenClawDailyNoteStore(memory_config.markdown.workspace or "").write(record)


__all__ = ["write"]
