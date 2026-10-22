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

"""Entry point for the ``vss search`` command group.

Importing this module pulls in the group implementation and its memory
adapter so the adapter is available whenever the group runs. Third-party
groups follow the same shape: a ``register`` module that the
``vss.commands`` entry point loads.
"""

from __future__ import annotations

from . import memory_adapter as memory_adapter
from .group import SEARCH
from .group import SearchGroup
from .memory_adapter import SearchAdapter

#: Object mounted by ``[project.entry-points."vss.commands"] search = ...``.
GROUP = SEARCH

__all__ = ["GROUP", "SEARCH", "SearchAdapter", "SearchGroup", "memory_adapter"]
