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
"""Single source of truth and registration point for fusable embedding spaces.

Each entry maps an embedding space name to two functions used for search:

- `request_builder`: prepares the request for that space.
- `output_factory`: converts the raw search response to a standard output.

To add a new space, simply add one line to `EMBEDDING_SPACE_ADAPTERS`.
"""

from collections.abc import Callable
from typing import Any
from typing import NamedTuple
from typing import get_args

from pydantic import BaseModel

from vss_agents.data_models.ranking import EmbeddingSpaceName
from vss_agents.data_models.ranking import FusableSearchOutput
from vss_agents.data_models.search import DecomposedQuery
from vss_agents.data_models.search import SearchInput
from vss_agents.tools.attribute_search import AttributeSearchOutput
from vss_agents.tools.attribute_search import build_attribute_request

# ---------------------------------------------------------------------------
# Anchor embedding space
# ---------------------------------------------------------------------------
# Note: "embed" is not in the registry. It serves as the anchor and is handled
# separately before dispatcher logic, so no request builder is needed here.
ANCHOR_EMBEDDING_SPACE: EmbeddingSpaceName = "embed"


class EmbeddingSpaceAdapter(NamedTuple):
    """Embedding space wiring required to invoke a space in a generic way (input and output)."""

    request_builder: Callable[[DecomposedQuery | None, SearchInput, int], BaseModel | None]
    output_factory: Callable[[Any], FusableSearchOutput]


EMBEDDING_SPACE_ADAPTERS: dict[EmbeddingSpaceName, EmbeddingSpaceAdapter] = {
    "attribute": EmbeddingSpaceAdapter(
        request_builder=build_attribute_request,
        output_factory=AttributeSearchOutput.from_raw,
    ),
    # Add new embedding spaces here with all the necessary wiring
    # e.g.
    # "caption": EmbeddingSpaceAdapter(
    #     request_builder=build_caption_request,
    #     output_factory=CaptionSearchOutput.from_raw,
    # ),
}


def _check_registry_exhaustive() -> None:
    """Startup-time exhaustiveness check.

    Ensures every embedding space name must be registered in the registry, or import will fail early.
    Guides consumers extending the registry with a new space to set up all the associated wiring.
    """
    expected = set(get_args(EmbeddingSpaceName)) - {ANCHOR_EMBEDDING_SPACE}
    registered = set(EMBEDDING_SPACE_ADAPTERS.keys())
    missing = expected - registered
    extra = registered - expected
    if missing or extra:
        raise RuntimeError(
            f"EMBEDDING_SPACE_ADAPTERS out of sync with EmbeddingSpaceName Literal. "
            f"Missing entries: {sorted(missing) or 'none'}. "
            f"Unknown entries: {sorted(extra) or 'none'}."
        )


_check_registry_exhaustive()


__all__ = [
    "ANCHOR_EMBEDDING_SPACE",
    "EMBEDDING_SPACE_ADAPTERS",
    "EmbeddingSpaceAdapter",
]
