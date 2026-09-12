# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Search primitives: EmbedSearch, AttributeSearch, and Search.

Convention: files in this directory MUST NOT read env directly. Receive
configuration via SearchRuntime from the constructor / from_runtime factory.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING
from typing import Any

__all__ = ["AttributeSearch", "EmbedSearch", "Search", "TagSearch"]

_LAZY_EXPORTS = {
    "AttributeSearch": ".attribute_search",
    "EmbedSearch": ".embed_search",
    "Search": ".search",
    "TagSearch": ".tag_search",
}

if TYPE_CHECKING:
    from .attribute_search import AttributeSearch
    from .embed_search import EmbedSearch
    from .search import Search
    from .tag_search import TagSearch


def __getattr__(name: str) -> Any:
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(_LAZY_EXPORTS[name], __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
