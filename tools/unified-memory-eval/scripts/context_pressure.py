#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Synthetic context-pressure filler for OpenClaw eval sessions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

CONTEXT_PRESSURE_PLACEMENTS = ("none", "before_locator", "after_locator", "before_each_turn")

RunOpenClawJson = Callable[[str, str, str, int, Path], tuple[dict[str, Any], int, int]]


def build_context_pressure_message(turn_index: int, chars: int) -> str:
    filler = (
        "CONTEXT PRESSURE FILLER. This text is unrelated to the BWC eval. "
        "It must be ignored when answering later questions. "
        "Do not save it as memory. "
    )
    body = (filler * ((chars // len(filler)) + 1))[:chars]
    return (
        f"Context pressure turn {turn_index}. "
        "Ignore this filler for all future BWC questions.\n\n"
        f"{body}\n\n"
        'Reply only with valid JSON: {"pressure_ack": true}'
    )


def context_pressure_settings(turns: int, chars: int, placement: str = "none") -> dict[str, Any]:
    return {
        "context_pressure_turns": turns,
        "context_pressure_chars": chars,
        "context_pressure_total_chars": turns * chars if turns > 0 and chars > 0 else 0,
        "context_pressure_placement": placement,
    }


def apply_context_pressure(
    turns: int,
    chars: int,
    session_key: str,
    model: str,
    timeout: int,
    log_path: Path,
    run_openclaw_json: RunOpenClawJson,
) -> None:
    if turns <= 0 or chars <= 0:
        return
    for turn_index in range(1, turns + 1):
        message = build_context_pressure_message(turn_index, chars)
        parsed, _, _ = run_openclaw_json(message, session_key, model, timeout, log_path)
        if parsed.get("pressure_ack") is not True:
            raise RuntimeError(
                f"OpenClaw did not acknowledge context pressure turn {turn_index}: {parsed}"
            )


def should_apply_context_pressure(placement: str, turn_id: int) -> bool:
    if placement == "none":
        return False
    if placement == "before_locator":
        return turn_id == 1
    if placement == "after_locator":
        return turn_id == 2
    if placement == "before_each_turn":
        return True
    raise ValueError(f"Unknown context_pressure_placement: {placement!r}")
