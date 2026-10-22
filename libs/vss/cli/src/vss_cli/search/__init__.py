# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""``vss search`` command group.

Layout for first-party groups (copy this when adding a new one)::

    vss_cli/<group>/
      register.py       # entry-point object (imports group + memory_adapter)
      group.py          # CommandGroup implementation
      memory_adapter.py # domain → nv.vss.memory/1.0 mapper (optional)

The entry point in ``pyproject.toml`` targets :mod:`vss_cli.search.register`.
"""

from __future__ import annotations

from .register import GROUP
from .register import SEARCH

__all__ = ["GROUP", "SEARCH"]
