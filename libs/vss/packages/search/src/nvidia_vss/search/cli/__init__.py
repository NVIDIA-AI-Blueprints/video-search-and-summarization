# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""``vss search`` command group, shipped as a plugin of the nvidia-vss-search wheel.

Layout for a group that lives in its own library distribution::

    nvidia_vss/<module>/cli/
      register.py       # entry-point object (imports group + memory_adapter)
      group.py          # CommandGroup implementation
      memory_adapter.py # domain → nv.vss.memory/1.0 mapper (optional)

The ``vss.commands`` entry point in this distribution's ``pyproject.toml``
targets :mod:`nvidia_vss.search.cli.register`. Only the ``vss`` CLI imports
this subpackage, so ``click`` and ``vss_cli`` are its dependencies, not the
library's.
"""

from __future__ import annotations

from .register import GROUP
from .register import SEARCH

__all__ = ["GROUP", "SEARCH"]
