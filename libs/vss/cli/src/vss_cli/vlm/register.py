# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Entry point for the ``vss vlm`` command group.

Importing this module makes the group and its memory adapter available.
Third-party groups follow the same shape: a ``register`` module that the
``vss.commands`` entry point loads.
"""

from __future__ import annotations

from . import memory_adapter as memory_adapter
from .group import VLM
from .group import VlmGroup
from .memory_adapter import VlmAdapter

#: Object mounted by ``[project.entry-points."vss.commands"] vlm = ...``.
GROUP = VLM

__all__ = ["GROUP", "VLM", "VlmAdapter", "VlmGroup", "memory_adapter"]
