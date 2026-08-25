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
"""Unit tests for the Agent VLM tag-search adapter."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from vss_agents.tools.tag_search import TagSearchConfig
from vss_agents.tools.tag_search import tag_search
from vss_core.search_core.models import TagSearchInput
from vss_core.search_core.models import TagSearchOutput


@pytest.mark.asyncio
async def test_adapter_invokes_core_primitive_and_closes_clients() -> None:
    es = MagicMock()
    es.aclose = AsyncMock()
    vst = MagicMock()
    vst.aclose = AsyncMock()
    primitive = MagicMock()
    primitive.run = AsyncMock(return_value=TagSearchOutput())
    config = TagSearchConfig(
        es_endpoint="http://elasticsearch:9200",
        vst_internal_url="http://vst:30888",
        vst_external_url="http://localhost:30888",
    )

    with (
        patch("vss_agents.tools.tag_search.ElasticClient.from_endpoint", return_value=es),
        patch("vss_agents.tools.tag_search.VSTClient", return_value=vst),
        patch("vss_agents.tools.tag_search.TagSearch", return_value=primitive) as primitive_type,
    ):
        generator = tag_search.__wrapped__(config, AsyncMock())
        function_info = await generator.__anext__()
        search_input = TagSearchInput(query="forklift", video_sources=["warehouse"])
        result = await function_info.single_fn(search_input)
        await generator.aclose()

    assert result == TagSearchOutput()
    primitive.run.assert_awaited_once_with(search_input)
    primitive_type.assert_called_once_with(
        es=es,
        vst=vst,
        tag_index="default_*",
        default_max_results=100,
    )
    es.aclose.assert_awaited_once_with()
    vst.aclose.assert_awaited_once_with()
