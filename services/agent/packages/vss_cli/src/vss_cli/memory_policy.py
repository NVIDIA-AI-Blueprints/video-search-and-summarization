# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Resolve static memory policy and safe per-request opt-outs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Deployment


def effective_persist(deployment: Deployment | None, *, no_persist: bool) -> bool:
    """Whether this job should initialize and write authoritative memory."""
    if no_persist or deployment is None:
        return False
    memory_config = deployment.memory
    return bool(memory_config and memory_config.enabled and memory_config.persist_by_default)


__all__ = ["effective_persist"]
