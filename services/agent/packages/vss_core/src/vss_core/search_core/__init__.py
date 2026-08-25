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
"""vss_core.search_core — VSS search primitives library.

NAT-free Python library for embed_search, attribute_search, and search.

Design conventions (not CI-enforced — please respect them in review):
  - No `os.environ` / `os.getenv` / `dotenv.*` under primitives/ or clients/.
    Only explicit runtime builders may read an environment mapping.
  - No `from nat.*` / `import nat.*` anywhere under this package.

This package lives under the shared ``services/agent/src/lib`` namespace so
``agent`` code can import it directly. The base ``nvidia-vss`` distribution
(no extras) makes this shared code usable without the NAT stack that the
``agent`` extra installs. The executable remains ``vss`` and the public
Python namespace remains ``lib.*``; the package must stay independent from
NAT and agent registration code.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING
from typing import Any

__all__ = [
    "AttributeSearch",
    "BackendUnreachableError",
    "ConfigurationError",
    "EmbedSearch",
    "ErrorEvent",
    "FinalResultEvent",
    "IndexNotFoundError",
    "InvalidInputError",
    "NoFinalResultError",
    "PartialResultEvent",
    "Search",
    "SearchError",
    "SearchEvent",
    "SearchRuntime",
    "StatusEvent",
    "TagIngestor",
    "TagSearch",
    "VSSSearch",
    "fuse_ranked_union",
    "models",
]

_LAZY_EXPORTS = {
    "AttributeSearch": ".primitives.attribute_search",
    "BackendUnreachableError": ".errors",
    "ConfigurationError": ".errors",
    "EmbedSearch": ".primitives.embed_search",
    "ErrorEvent": ".events",
    "FinalResultEvent": ".events",
    "fuse_ranked_union": ".primitives._fusion",
    "IndexNotFoundError": ".errors",
    "InvalidInputError": ".errors",
    "NoFinalResultError": ".errors",
    "PartialResultEvent": ".events",
    "Search": ".primitives.search",
    "SearchError": ".errors",
    "SearchEvent": ".events",
    "SearchRuntime": ".runtime",
    "StatusEvent": ".events",
    "TagSearch": ".primitives.tag_search",
    "TagIngestor": ".tag_ingest",
    "VSSSearch": ".host",
    "models": ".models",
}

if TYPE_CHECKING:
    from . import models as models
    from .errors import BackendUnreachableError
    from .errors import ConfigurationError
    from .errors import IndexNotFoundError
    from .errors import InvalidInputError
    from .errors import NoFinalResultError
    from .errors import SearchError
    from .events import ErrorEvent
    from .events import FinalResultEvent
    from .events import PartialResultEvent
    from .events import SearchEvent
    from .events import StatusEvent
    from .host import VSSSearch
    from .memory_adapter import SearchAdapter
    from .primitives._fusion import fuse_ranked_union
    from .primitives.attribute_search import AttributeSearch
    from .primitives.embed_search import EmbedSearch
    from .primitives.search import Search
    from .primitives.tag_search import TagSearch
    from .runtime import SearchRuntime
    from .tag_ingest import TagIngestor


def __getattr__(name: str) -> Any:
    """Load public exports on first use.

    This keeps the shared search package lightweight without forcing it into a
    backend registry. A bare ``import lib.search_core`` should not import
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
