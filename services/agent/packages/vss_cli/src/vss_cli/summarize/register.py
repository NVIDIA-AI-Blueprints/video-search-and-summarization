# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Entry point for the ``vss summarize`` command group.

Importing this module pulls in the group implementation and its memory
adapter so the adapter is available whenever the group runs. Third-party
groups follow the same shape: a ``register`` module that the
``vss.commands`` entry point loads.
"""

from __future__ import annotations

from . import memory_adapter as memory_adapter
from .group import SUMMARIZE
from .group import SummarizeGroup
from .memory_adapter import SummaryAdapter

#: Object mounted by ``[project.entry-points."vss.commands"] summarize = ...``.
GROUP = SUMMARIZE

__all__ = ["GROUP", "SUMMARIZE", "SummarizeGroup", "SummaryAdapter", "memory_adapter"]
