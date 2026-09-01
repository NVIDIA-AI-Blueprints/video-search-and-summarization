# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Trusted, gold-free setup-input projection and prompt rendering."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .spec import SetupInputName


def _render_setup_input(name: SetupInputName, value: Any) -> str:
    if name == "dataset_video_ids":
        if (
            not isinstance(value, tuple)
            or not value
            or not all(isinstance(item, str) and item for item in value)
        ):
            raise ValueError("dataset_video_ids must be a non-empty tuple of strings")
        return f"{name}:\n" + "\n".join(f"- {item}" for item in value)

    if name == "summarization_config":
        if not isinstance(value, Mapping):
            raise ValueError("summarization_config must be a mapping")
        return f"{name}:\n{json.dumps(dict(value), indent=2)}"

    raise ValueError(f"unsupported setup input: {name}")


def render_setup_prompt(
    *,
    preamble: str,
    query: str,
    requested_inputs: tuple[SetupInputName, ...],
    available_inputs: Mapping[SetupInputName, Any],
) -> str:
    """Render a setup prompt containing only explicitly requested safe inputs."""
    sections = [preamble, query]
    if requested_inputs:
        missing = [name for name in requested_inputs if name not in available_inputs]
        if missing:
            raise ValueError(f"unavailable setup inputs: {missing}")
        sections.append("Task inputs:")
        sections.extend(
            _render_setup_input(name, available_inputs[name])
            for name in requested_inputs
        )
    return "\n\n".join(sections) + "\n"


__all__ = ["render_setup_prompt"]
