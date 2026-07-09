# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for lib.search_core.host.VSSSearch lifecycle.

Focus: aclose() closes lazily-built primitives AND the injected VLM analyzer
(the facade is the only owner that knows when critic work is done), is
idempotent, and tolerates an analyzer that does not expose aclose().
"""

from __future__ import annotations

import asyncio

from lib.search_core import SearchRuntime, VSSSearch


def _runtime() -> SearchRuntime:
    return SearchRuntime.from_kwargs(
        es_endpoint="http://es:9200",
        cosmos_embed_endpoint="http://embed:8017",
        rtvi_cv_endpoint="http://cv:9000",
        vst_internal_url="http://vst:30888",
        vst_external_url="http://vst:7777",
    )


class _RecordingAnalyzer:
    def __init__(self) -> None:
        self.close_calls = 0

    async def analyze(self, **_kw: object) -> str:
        return ""

    async def aclose(self) -> None:
        self.close_calls += 1


class _RecordingPrimitive:
    def __init__(self) -> None:
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


class _AnalyzerWithoutClose:
    async def analyze(self, **_kw: object) -> str:
        return ""


def test_aclose_closes_injected_analyzer_and_primitives_and_nulls_refs() -> None:
    analyzer = _RecordingAnalyzer()
    vss = VSSSearch.from_runtime(_runtime(), vlm_analyzer=analyzer)
    primitive = _RecordingPrimitive()
    vss._search = primitive  # type: ignore[assignment]  test injects a fake primitive

    asyncio.run(vss.aclose())

    assert analyzer.close_calls == 1
    assert primitive.close_calls == 1
    assert vss._search is None
    assert vss._vlm_analyzer is None


def test_aclose_is_idempotent() -> None:
    analyzer = _RecordingAnalyzer()
    vss = VSSSearch.from_runtime(_runtime(), vlm_analyzer=analyzer)

    asyncio.run(vss.aclose())
    asyncio.run(vss.aclose())

    # Second close must be a no-op — the analyzer is not double-closed.
    assert analyzer.close_calls == 1


def test_aclose_tolerates_analyzer_without_aclose() -> None:
    vss = VSSSearch.from_runtime(_runtime(), vlm_analyzer=_AnalyzerWithoutClose())
    # Must not raise even though the analyzer has no aclose().
    asyncio.run(vss.aclose())
    assert vss._vlm_analyzer is None


def test_context_manager_closes_injected_analyzer() -> None:
    analyzer = _RecordingAnalyzer()

    async def _run() -> None:
        async with VSSSearch.from_runtime(_runtime(), vlm_analyzer=analyzer):
            pass

    asyncio.run(_run())
    assert analyzer.close_calls == 1
