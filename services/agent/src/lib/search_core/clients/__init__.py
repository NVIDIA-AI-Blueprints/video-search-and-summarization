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

__all__ = [
    "CVTextEmbedder",
    "CosmosEmbedClient",
    "CosmosEmbedder",
    "ElasticClient",
    "ElasticIndex",
    "EmbedClient",
    "ImageEmbedder",
    "OpenAIVLMAnalyzer",
    "RTVICVEmbedClient",
    "TextEmbedder",
    "VLMAnalyzer",
    "VSTClient",
    "VSTError",
    "VSTSnapshot",
    "VideoEmbedder",
    "build_screenshot_url",
    "get_name_to_stream_id_map",
    "get_sensor_id_from_stream_id",
    "get_stream_id",
    "get_streams_info",
    "get_timeline",
]

_LAZY_EXPORTS = {
    "CVTextEmbedder": ".protocols",
    "CosmosEmbedClient": ".cosmos_embed",
    "CosmosEmbedder": ".protocols",
    "ElasticClient": ".elastic",
    "ElasticIndex": ".protocols",
    "EmbedClient": ".embed_base",
    "ImageEmbedder": ".protocols",
    "OpenAIVLMAnalyzer": ".vlm_openai",
    "RTVICVEmbedClient": ".rtvi_cv_embed",
    "TextEmbedder": ".protocols",
    "VLMAnalyzer": ".protocols",
    "VSTClient": ".vst",
    "VSTError": ".vst",
    "VSTSnapshot": ".protocols",
    "VideoEmbedder": ".protocols",
    "build_screenshot_url": ".vst",
    "get_name_to_stream_id_map": ".vst",
    "get_sensor_id_from_stream_id": ".vst",
    "get_stream_id": ".vst",
    "get_streams_info": ".vst",
    "get_timeline": ".vst",
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
    from .protocols import VLMAnalyzer
    from .protocols import VSTSnapshot
    from .rtvi_cv_embed import RTVICVEmbedClient
    from .vlm_openai import OpenAIVLMAnalyzer
    from .vst import VSTClient
    from .vst import VSTError
    from .vst import build_screenshot_url
    from .vst import get_name_to_stream_id_map
    from .vst import get_sensor_id_from_stream_id
    from .vst import get_stream_id
    from .vst import get_streams_info
    from .vst import get_timeline


def __getattr__(name: str):
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(_LAZY_EXPORTS[name], __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
