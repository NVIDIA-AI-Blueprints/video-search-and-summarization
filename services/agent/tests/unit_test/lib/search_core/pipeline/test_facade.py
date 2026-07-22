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
"""Facade routing tests against fake synchronous executors.

These assert parity with the retired ``execute_core_search``: mode routing,
the embed-confidence fallback, fusion-preserves-empty, degradation messages,
object dedup, and shared post-processing (top-percent, merge, truncation).
"""

import pytest

from lib.search_core.errors import BackendUnreachableError
from lib.search_core.errors import ConfigurationError
from lib.search_core.errors import InvalidInputError
from lib.search_core.models.attribute_search import AttributeSearchMetadata
from lib.search_core.models.attribute_search import AttributeSearchResult
from lib.search_core.models.embed_search import EmbedSearchOutput
from lib.search_core.models.embed_search import EmbedSearchResultItem
from lib.search_core.models.search import SearchInput
from lib.search_core.pipeline.facade import SearchDeps
from lib.search_core.pipeline.facade import SearchParams
from lib.search_core.pipeline.facade import run_search


def _embed_item(
    name: str,
    score: float,
    *,
    sensor: str = "cam1",
    start: str = "2025-01-01T00:00:00Z",
    end: str = "2025-01-01T00:00:05Z",
) -> EmbedSearchResultItem:
    return EmbedSearchResultItem(
        video_name=name,
        description=f"clip {name}",
        start_time=start,
        end_time=end,
        sensor_id=sensor,
        screenshot_url=f"{name}.jpg",
        similarity_score=score,
    )


def _attr_result(
    *,
    object_id: str | None = "7",
    behavior: float = 0.6,
    frame: float | None = None,
    sensor: str = "cam1",
    screenshot: str | None = "attr.jpg",
) -> AttributeSearchResult:
    return AttributeSearchResult(
        screenshot_url=screenshot,
        metadata=AttributeSearchMetadata(
            sensor_id=sensor,
            object_id=object_id,
            object_type="person",
            frame_timestamp="2025-01-01T00:00:02Z",
            start_time="2025-01-01T00:00:01Z",
            end_time="2025-01-01T00:00:03Z",
            behavior_score=behavior,
            frame_score=frame,
        ),
    )


def _embed_exec(items):
    def _exec(request):
        return EmbedSearchOutput(results=list(items))

    return _exec


class TestEmbedMode:
    def test_plain_embed_search(self):
        deps = SearchDeps(
            embed_exec=_embed_exec(
                [_embed_item("a", 0.9), _embed_item("b", 0.4, start="2025-01-01T01:00:00Z", end="2025-01-01T01:00:05Z")]
            )
        )
        out = run_search(SearchInput(query="red car"), deps)
        assert [r.video_name for r in out.data] == ["a", "b"]
        assert out.data[0].similarity == pytest.approx(0.9)
        assert out.search_messages == []

    def test_top_k_truncation(self):
        items = [
            _embed_item(str(i), 1.0 - i / 100, start=f"2025-01-01T0{i}:00:00Z", end=f"2025-01-01T0{i}:00:05Z")
            for i in range(5)
        ]
        deps = SearchDeps(embed_exec=_embed_exec(items))
        out = run_search(SearchInput(query="q", top_k=2), deps)
        assert len(out.data) == 2

    def test_invalid_input_raises(self):
        deps = SearchDeps(embed_exec=_embed_exec([]))
        with pytest.raises(InvalidInputError):
            run_search(SearchInput(query="   "), deps)

    def test_backend_errors_propagate(self):
        def _boom(request):
            raise BackendUnreachableError("embed_search", "down")

        with pytest.raises(BackendUnreachableError):
            run_search(SearchInput(query="q"), SearchDeps(embed_exec=_boom))


class TestFusionMode:
    def test_fusion_scores_and_reranks(self):
        deps = SearchDeps(
            embed_exec=_embed_exec(
                [
                    _embed_item("weak_embed_strong_attr", 0.3),
                    _embed_item(
                        "strong_embed_no_attr",
                        0.9,
                        sensor="cam2",
                        start="2025-01-01T02:00:00Z",
                        end="2025-01-01T02:00:05Z",
                    ),
                ]
            ),
            attribute_exec=lambda req: [_attr_result()] if req.video_sources == ["weak_embed_strong_attr"] else [],
        )
        params = SearchParams(fusion_method="weighted_linear", w_embed=0.1, w_attribute=0.9)
        out = run_search(SearchInput(query="q", search_mode="fusion", attributes=["person"]), deps, params)
        # weak embed hit gets attribute evidence 0.6 -> 0.1*0.3 + 0.9*0.6 = 0.57
        # strong embed hit gets none              -> 0.1*0.9           = 0.09
        assert [r.video_name for r in out.data] == ["weak_embed_strong_attr", "strong_embed_no_attr"]
        assert out.data[0].similarity == pytest.approx(0.57)
        assert out.data[0].object_ids == ["7"]
        assert out.data[0].screenshot_url == "attr.jpg"  # attribute screenshot preferred

    def test_fusion_with_no_embed_candidates_stays_empty(self):
        deps = SearchDeps(embed_exec=_embed_exec([]), attribute_exec=lambda _req: [_attr_result()])
        out = run_search(SearchInput(query="q", search_mode="fusion", attributes=["person"]), deps)
        assert out.data == []
        assert any("no semantic candidates" in m for m in out.search_messages)

    def test_fusion_below_confidence_still_fuses(self):
        deps = SearchDeps(
            embed_exec=_embed_exec([_embed_item("a", 0.1)]),
            attribute_exec=lambda _req: [],
        )
        params = SearchParams(embed_confidence_threshold=0.9, fusion_method="rrf")
        out = run_search(SearchInput(query="q", search_mode="fusion", attributes=["person"]), deps, params)
        assert len(out.data) == 1  # fused (rrf of rank 1), not dropped, not attribute-fallback

    def test_systemic_attribute_failure_aborts(self):
        def _attr_boom(req):
            raise BackendUnreachableError("attribute_search", "down")

        deps = SearchDeps(embed_exec=_embed_exec([_embed_item("a", 0.9)]), attribute_exec=_attr_boom)
        with pytest.raises(BackendUnreachableError):
            run_search(SearchInput(query="q", search_mode="fusion", attributes=["person"]), deps)

    def test_per_hit_attribute_failure_degrades_to_embed_score(self):
        def _attr_flaky(req):
            raise ValueError("odd payload")  # not a LibraryError -> per-hit degrade

        deps = SearchDeps(embed_exec=_embed_exec([_embed_item("a", 0.9)]), attribute_exec=_attr_flaky)
        params = SearchParams(fusion_method="weighted_linear", w_embed=1.0, w_attribute=1.0)
        out = run_search(SearchInput(query="q", search_mode="fusion", attributes=["person"]), deps, params)
        assert out.data[0].similarity == pytest.approx(0.9)  # attribute contributed 0.0


class TestAttributeMode:
    def test_attribute_only(self):
        deps = SearchDeps(
            embed_exec=_embed_exec([]),
            attribute_exec=lambda _req: [
                _attr_result(behavior=0.8),
                _attr_result(object_id="9", behavior=0.5, sensor="cam2"),
            ],
        )
        out = run_search(SearchInput(query="q", search_mode="attribute", attributes=["person"]), deps)
        assert len(out.data) == 2
        assert out.data[0].similarity == pytest.approx(0.8)
        assert out.data[0].object_ids == ["7"]

    def test_frame_score_preferred_when_positive(self):
        deps = SearchDeps(
            embed_exec=_embed_exec([]),
            attribute_exec=lambda _req: [_attr_result(behavior=0.2, frame=0.9)],
        )
        out = run_search(SearchInput(query="q", search_mode="attribute", attributes=["person"]), deps)
        assert out.data[0].similarity == pytest.approx(0.9)

    def test_unexpected_failure_degrades_with_message(self):
        def _attr_boom(req):
            raise RuntimeError("weird")

        deps = SearchDeps(embed_exec=_embed_exec([]), attribute_exec=_attr_boom)
        out = run_search(SearchInput(query="q", search_mode="attribute", attributes=["person"]), deps)
        assert out.data == []
        assert any("degraded" in m for m in out.search_messages)

    def test_library_failure_propagates(self):
        def _attr_boom(req):
            raise BackendUnreachableError("attribute_search", "down")

        deps = SearchDeps(embed_exec=_embed_exec([]), attribute_exec=_attr_boom)
        with pytest.raises(BackendUnreachableError):
            run_search(SearchInput(query="q", search_mode="attribute", attributes=["person"]), deps)


class TestObjectMode:
    def test_dedup_keeps_best_score_per_object(self):
        def _object_exec(oid):
            return [_attr_result(object_id="7", behavior=0.4), _attr_result(object_id="7", behavior=0.9)]

        deps = SearchDeps(embed_exec=_embed_exec([]), object_exec=_object_exec)
        out = run_search(SearchInput(query="q", search_mode="object", object_ids=[7]), deps)
        assert len(out.data) == 1
        assert out.data[0].similarity == pytest.approx(0.9)

    def test_unknown_ids_not_collapsed_across_windows(self):
        # "unknown" cannot prove two hits are the same object, so hits at
        # *different* windows never dedup against each other. (Intentional delta
        # from the retired path: literally identical rows — same sensor, same
        # window, same unknown id — DO merge, keeping the best score, instead of
        # duplicating.)
        def _object_exec(oid):
            first = _attr_result(object_id="unknown", behavior=0.4)
            second = _attr_result(object_id=None, behavior=0.3)
            third = AttributeSearchResult(
                screenshot_url="attr.jpg",
                metadata=first.metadata.model_copy(
                    update={
                        "start_time": "2025-01-01T05:00:00Z",
                        "end_time": "2025-01-01T05:00:03Z",
                        "behavior_score": 0.2,
                    }
                ),
            )
            return [first, second, third]

        deps = SearchDeps(embed_exec=_embed_exec([]), object_exec=_object_exec)
        out = run_search(SearchInput(query="q", search_mode="object", object_ids=[1]), deps)
        assert len(out.data) == 3

    def test_identical_unknown_rows_merge_keeping_best_score(self):
        def _object_exec(oid):
            return [_attr_result(object_id="unknown", behavior=0.4), _attr_result(object_id="unknown", behavior=0.2)]

        deps = SearchDeps(embed_exec=_embed_exec([]), object_exec=_object_exec)
        out = run_search(SearchInput(query="q", search_mode="object", object_ids=[1]), deps)
        assert len(out.data) == 1
        assert out.data[0].similarity == pytest.approx(0.4)

    def test_enrich_hook_called_after_dedup(self):
        calls: list[int] = []

        def _object_exec(oid):
            return [_attr_result(object_id="7", behavior=0.4), _attr_result(object_id="7", behavior=0.9)]

        def _enrich(results):
            calls.append(len(results))
            return results

        deps = SearchDeps(embed_exec=_embed_exec([]), object_exec=_object_exec, object_enrich=_enrich)
        run_search(SearchInput(query="q", search_mode="object", object_ids=[7]), deps)
        assert calls == [1]  # after dedup: one result

    def test_missing_object_exec_is_configuration_error(self):
        deps = SearchDeps(embed_exec=_embed_exec([]))
        with pytest.raises(ConfigurationError):
            run_search(SearchInput(query="q", search_mode="object", object_ids=[7]), deps)


class TestPostProcessing:
    def test_top_percent_applies_before_merge(self):
        items = [_embed_item("a", 1.0), _embed_item("b", 0.2, start="2025-01-01T03:00:00Z", end="2025-01-01T03:00:05Z")]
        deps = SearchDeps(embed_exec=_embed_exec(items))
        out = run_search(SearchInput(query="q"), deps, SearchParams(top_percent_filter=0.5))
        assert [r.video_name for r in out.data] == ["a"]

    def test_consecutive_chunks_merge(self):
        items = [
            _embed_item("v", 0.80, start="2025-01-01T00:00:00Z", end="2025-01-01T00:00:05Z"),
            _embed_item("v", 0.82, start="2025-01-01T00:00:04Z", end="2025-01-01T00:00:09Z"),
        ]
        deps = SearchDeps(embed_exec=_embed_exec(items))
        out = run_search(SearchInput(query="q"), deps)
        assert len(out.data) == 1
        assert out.data[0].similarity == pytest.approx(0.81)
