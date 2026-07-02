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
"""lib.search_core — VSS search primitives library.

NAT-free Python library for embed_search, attribute_search, search, and
critic_agent.

Design conventions (not CI-enforced — please respect them in review):
  - No `os.environ` / `os.getenv` / `dotenv.*` under primitives/ or clients/.
    Only runtime.py and cli.py may read env directly.
  - No `from nat.*` / `import nat.*` anywhere under this package.

This package lives in-tree under services/agent/src/lib. It is shipped in the
agent wheel alongside vss_agents, but it must remain independent from NAT and
agent registration code.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

__all__ = [
    "AttributeSearch",
    "BackendUnreachableError",
    "ConfigurationError",
    "CriticAgent",
    "EmbedSearch",
    "ErrorEvent",
    "FinalResultEvent",
    "IndexNotFoundError",
    "InvalidInputError",
    "NoResultsError",
    "PartialResultEvent",
    "RuntimeSnapshot",
    "Search",
    "SearchError",
    "SearchEvent",
    "SearchOptions",
    "SearchRuntime",
    "StatusEvent",
    "VSSSearch",
    "models",
]

_LAZY_EXPORTS = {
    "AttributeSearch": ".primitives.attribute_search",
    "BackendUnreachableError": ".errors",
    "ConfigurationError": ".errors",
    "CriticAgent": ".primitives.critic",
    "EmbedSearch": ".primitives.embed_search",
    "ErrorEvent": ".events",
    "FinalResultEvent": ".events",
    "IndexNotFoundError": ".errors",
    "InvalidInputError": ".errors",
    "NoResultsError": ".errors",
    "PartialResultEvent": ".events",
    "RuntimeSnapshot": ".runtime",
    "Search": ".primitives.search",
    "SearchError": ".errors",
    "SearchEvent": ".events",
    "SearchOptions": ".runtime",
    "SearchRuntime": ".runtime",
    "StatusEvent": ".events",
    "VSSSearch": ".host",
    "models": ".models",
}

if TYPE_CHECKING:
    from . import models as models
    from .errors import BackendUnreachableError
    from .errors import ConfigurationError
    from .errors import IndexNotFoundError
    from .errors import InvalidInputError
    from .errors import NoResultsError
    from .errors import SearchError
    from .events import ErrorEvent
    from .events import FinalResultEvent
    from .events import PartialResultEvent
    from .events import SearchEvent
    from .events import StatusEvent
    from .host import VSSSearch
    from .primitives.attribute_search import AttributeSearch
    from .primitives.critic import CriticAgent
    from .primitives.embed_search import EmbedSearch
    from .primitives.search import Search
    from .runtime import RuntimeSnapshot
    from .runtime import SearchOptions
    from .runtime import SearchRuntime


def __getattr__(name: str):
    """Load public exports on first use.

    This mirrors lib.knowledge's lazy backend discipline without forcing search
    into a backend registry. A bare ``import lib.search_core`` should not import
    Elasticsearch, aiohttp, or LangChain.
    """
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(_LAZY_EXPORTS[name], __name__)
    value = module if name == "models" else getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
