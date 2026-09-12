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
"""Network clients used by the primitives: Elastic, Cosmos embed, RTVI CV, VST.

Convention: files in this directory MUST NOT read env directly. Endpoint URLs
and credentials are passed in by SearchRuntime via primitive constructors.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING
from typing import Any

__all__ = [
    "CVTextEmbedder",
    "CosmosEmbedClient",
    "CosmosEmbedder",
    "ElasticClient",
    "ElasticIndex",
    "EmbedClient",
    "ImageEmbedder",
    "RTVICVEmbedClient",
    "TextEmbedder",
    "VideoEmbedder",
]

_LAZY_EXPORTS = {
    "CVTextEmbedder": ".protocols",
    "CosmosEmbedClient": ".cosmos_embed",
    "CosmosEmbedder": ".protocols",
    "ElasticClient": ".elastic",
    "ElasticIndex": ".protocols",
    "EmbedClient": ".embed_base",
    "ImageEmbedder": ".protocols",
    "RTVICVEmbedClient": ".rtvi_cv_embed",
    "TextEmbedder": ".protocols",
    "VideoEmbedder": ".protocols",
}

if TYPE_CHECKING:
    from .cosmos_embed import CosmosEmbedClient
    from .elastic import ElasticClient
    from .embed_base import EmbedClient
    from .protocols import CosmosEmbedder
    from .protocols import CVTextEmbedder
    from .protocols import ElasticIndex
    from .protocols import ImageEmbedder
    from .protocols import TextEmbedder
    from .protocols import VideoEmbedder
    from .rtvi_cv_embed import RTVICVEmbedClient


def __getattr__(name: str) -> Any:
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(_LAZY_EXPORTS[name], __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
