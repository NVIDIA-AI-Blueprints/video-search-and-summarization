# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from unittest.mock import AsyncMock

import pytest

from vss_core.search_core.models.attribute_search import AttributeSearchInput
from vss_core.search_core.models.attribute_search import AttributeSearchMetadata
from vss_core.search_core.models.attribute_search import AttributeSearchResult
from vss_core.search_core.primitives import _attribute_helpers as ah


@pytest.mark.asyncio
async def test_exact_pool_is_ranked_before_output_budget(monkeypatch):
    def result(sensor, behavior, frame):
        return AttributeSearchResult(
            metadata=AttributeSearchMetadata(
                sensor_id=sensor, object_id="1", object_type="Person", behavior_score=behavior, frame_score=frame
            )
        )

    search = AsyncMock(return_value=[result("a", 0.9, 0.6), result("b", 0.8, 0.95)])
    monkeypatch.setattr(ah, "search_single_attribute", search)
    found = await ah._append_multi_attribute(
        queries=["red shirt"],
        embeddings=None,
        search_input=AttributeSearchInput(query="red shirt", top_k=1, candidate_limit=50),
        embed_client=None,
        search_index="behaviors",
        search_frames_index=None,
        enable_frame_lookup=False,
        vst_external_url="",
        vst_internal_url=None,
        es=None,
    )
    assert search.call_args.kwargs["search_input"].top_k == 1
    assert search.call_args.kwargs["complete_pool"] is True
    AttributeSearchInput.model_validate(search.call_args.kwargs["search_input"].model_dump())
    assert [r.metadata.sensor_id for r in found] == ["b"]
