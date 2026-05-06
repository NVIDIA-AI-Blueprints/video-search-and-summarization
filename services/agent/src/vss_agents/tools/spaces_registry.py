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
"""Source of truth and registration point for fusable embedding spaces.

Each entry maps an embedding space name to two functions used for search:

- `build_request`: prepares the request for that space.
- `coerce_output`: converts the raw search response to a standard output.

To add a new space, simply add its wiring to ``EMBEDDING_SPACE_ADAPTERS``.
"""

from collections.abc import Callable
from typing import Any
from typing import NamedTuple

from nat.data_models.component_ref import FunctionRef
from pydantic import BaseModel

from vss_agents.data_models.ranking import EmbeddingSpaceName
from vss_agents.data_models.ranking import FusableSearchOutput
from vss_agents.data_models.search import DecomposedQuery
from vss_agents.data_models.search import SearchInput
from vss_agents.tools.attribute_search import AttributeSearchOutput
from vss_agents.tools.attribute_search import build_attribute_request

# Note: "embed" is not in the registry. It serves as the anchor
# i.e. always runs, handled separately before dispatcher logic, so no request builder is needed here.
ANCHOR_EMBEDDING_SPACE: EmbeddingSpaceName = "embed"


class EmbeddingSpaceAdapter(NamedTuple):
    """Embedding space wiring required to invoke a space in a generic way (input and output).

    Gotchas:
    - All tools in ``allowed_tools`` must share the same Pydantic input shape (consumed by ``build_request``)
      and the same Pydantic output shape (consumed by ``coerce_output``)
    - A tool with a different I/O shape needs its own space entry, not an additional ``allowed_tools`` entry

    TODO: In the future, define a proper location for input/output shapes, ``build_request``, ``coerce_output`` of these tools
    This will be to cater better for multiple ``allowed_tools`` sharing the same I/O e.g. attribute_search, attribute_search_v2
    (for now they are all placed in the same tool definition file)
    """

    allowed_tools: frozenset[FunctionRef]
    build_request: Callable[[DecomposedQuery | None, SearchInput, int], BaseModel | None]
    coerce_output: Callable[[Any], FusableSearchOutput]


EMBEDDING_SPACE_ADAPTERS: dict[EmbeddingSpaceName, EmbeddingSpaceAdapter] = {
    "attribute": EmbeddingSpaceAdapter(
        allowed_tools=frozenset[str]({"attribute_search"}),
        build_request=build_attribute_request,
        coerce_output=AttributeSearchOutput.from_raw,
    ),
    # Each time a new embedding space is created:
    # - Add it to the registry here with all the necessary wiring
    # - Add it to the ``EmbeddingSpaceName`` literal to allow it in this config
    # This enables the config to use it as part of ``ranking_spaces`` field
    # e.g.
    # "caption": EmbeddingSpaceAdapter(
    #     allowed_tools=frozenset({"caption_search"}),
    #     build_request=build_caption_request,
    #     coerce_output=CaptionSearchOutput.from_raw,
    # ),
}


__all__ = [
    "ANCHOR_EMBEDDING_SPACE",
    "EMBEDDING_SPACE_ADAPTERS",
    "EmbeddingSpaceAdapter",
]
