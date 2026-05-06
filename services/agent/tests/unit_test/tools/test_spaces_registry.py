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
"""Drift checks between ``EmbeddingSpaceName`` and ``EMBEDDING_SPACE_ADAPTERS``."""

from typing import get_args

from vss_agents.data_models.ranking import EmbeddingSpaceName
from vss_agents.tools.spaces_registry import ANCHOR_EMBEDDING_SPACE
from vss_agents.tools.spaces_registry import EMBEDDING_SPACE_ADAPTERS


def test_registry_matches_embedding_space_literal():
    """``EMBEDDING_SPACE_ADAPTERS`` must register every non-anchor ``EmbeddingSpaceName``.

    Catches both directions of drift:
    - new literal added without wiring the adapter,
    - adapter left in the registry after the literal was removed.
    """
    expected = set(get_args(EmbeddingSpaceName)) - {ANCHOR_EMBEDDING_SPACE}
    registered = set(EMBEDDING_SPACE_ADAPTERS.keys())

    missing = expected - registered
    extra = registered - expected

    assert not missing, f"Missing adapter entries for: {sorted(missing)}"
    assert not extra, f"Unknown adapter entries: {sorted(extra)}"
