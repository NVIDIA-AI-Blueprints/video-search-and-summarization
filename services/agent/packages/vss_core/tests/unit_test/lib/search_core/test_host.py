# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the retrieval-only VSSSearch lifecycle."""

from __future__ import annotations

import asyncio

from vss_core.search_core import SearchRuntime
from vss_core.search_core import VSSSearch


def _runtime() -> SearchRuntime:
    return SearchRuntime.from_kwargs(
        es_endpoint="http://es:9200",
        cosmos_embed_endpoint="http://embed:8017",
        rtvi_cv_endpoint="http://cv:9000",
        vst_internal_url="http://vst:30888",
        vst_external_url="http://vst:7777",
    )


class _RecordingPrimitive:
    def __init__(self) -> None:
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


def test_aclose_closes_primitives_and_is_idempotent() -> None:
    vss = VSSSearch.from_runtime(_runtime())
    primitive = _RecordingPrimitive()
    vss._search = primitive  # type: ignore[assignment]

    asyncio.run(vss.aclose())
    asyncio.run(vss.aclose())

    assert primitive.close_calls == 1
    assert vss._search is None
