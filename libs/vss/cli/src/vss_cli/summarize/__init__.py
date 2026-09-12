# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""``vss summarize`` command group.

Layout for first-party groups (copy this when adding a new one)::

    vss_cli/<group>/
      register.py       # entry-point object (imports group + memory_adapter)
      group.py          # CommandGroup implementation
      memory_adapter.py # domain → nv.vss.memory/1.0 mapper (optional)

The entry point in ``pyproject.toml`` targets :mod:`vss_cli.summarize.register`.
"""

from __future__ import annotations

from .register import GROUP
from .register import SUMMARIZE

__all__ = ["GROUP", "SUMMARIZE"]
