# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the fusion subject check (attribute person must be the one acting)."""

from __future__ import annotations

from typing import Any
import urllib.parse

import cv2
import httpx
import numpy as np
import pytest

from vss_core._foundation.time_measure import collect_timings
from vss_core.search_core import subject_check as sc
from vss_core.search_core.models.attribute_search import AttributeSearchMetadata
from vss_core.search_core.models.attribute_search import AttributeSearchResult
from vss_core.search_core.models.embed_search import EmbedSearchOutput
from vss_core.search_core.models.embed_search import EmbedSearchResultItem
from vss_core.search_core.models.search import SearchInput
from vss_core.search_core.models.search import SearchResult
from vss_core.search_core.primitives import _fusion
from vss_core.search_core.primitives._search_helpers import _subject_lookup
from vss_core.search_core.primitives._search_helpers import execute_core_search_wrapper
from vss_core.search_core.subject_check import Subject
from vss_core.search_core.subject_check import SubjectChecker
from vss_core.search_core.subject_check import SubjectVerdict

_BOX = {"leftX": 0.1, "rightX": 0.5, "topY": 0.1, "bottomY": 0.9}


def _jpeg(width: int = 200, height: int = 100) -> bytes:
    ok, encoded = cv2.imencode(".jpg", np.zeros((height, width, 3), dtype=np.uint8))
    assert ok
    return bytes(encoded.tobytes())


def _width(image: bytes) -> int:
    frame = cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_COLOR)
    return int(frame.shape[1])


def _attr(
    object_id: str,
    *,
    sensor_id: str = "cam1",
    frame_score: float | None = 0.6,
    bbox: dict[str, Any] | None = None,
    frame_timestamp: str | None = "2025-01-01T00:00:02Z",
) -> AttributeSearchResult:
    return AttributeSearchResult(
        metadata=AttributeSearchMetadata(
            sensor_id=sensor_id,
            object_id=object_id,
            object_type="person",
            frame_timestamp=frame_timestamp,
            start_time="2025-01-01T00:00:00Z",
            end_time="2025-01-01T00:00:05Z",
            bbox=_BOX if bbox is None else bbox,
            behavior_score=0.55,
            frame_score=frame_score,
        )
    )


def _result(name: str, sensor_id: str = "cam1") -> SearchResult:
    return SearchResult(
        video_name=name,
        description="",
        start_time="2025-01-01T00:00:00Z",
        end_time="2025-01-01T00:00:05Z",
        sensor_id=sensor_id,
        screenshot_url="",
        similarity=0.5,
    )


def _subject(object_id: str, sensor_id: str = "cam1") -> Subject:
    return Subject(
        sensor_id=sensor_id,
        object_id=object_id,
        frame_timestamp="2025-01-01T00:00:02Z",
        bbox=_BOX,
        score=0.6,
    )


class _VLM:
    """Answers by the frame width, which the fake fetch derives from the object id."""

    def __init__(self, answers_by_width: dict[int, str], error: Exception | None = None) -> None:
        self.answers_by_width = answers_by_width
        self.error = error
        self.prompts: list[str] = []

    async def analyze_image(self, *, image_bytes: bytes, prompt: str, mime_type: str = "image/jpeg") -> str:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.answers_by_width[_width(image_bytes)]


class _VST:
    def __init__(self) -> None:
        self.resolve_calls: list[str] = []
        self.timeline_calls: list[str] = []

    async def resolve_stream_id(self, sensor_id: str) -> str:
        self.resolve_calls.append(sensor_id)
        return f"stream-{sensor_id}"

    async def get_timeline(self, sensor_id: str) -> tuple[str, str]:
        self.timeline_calls.append(sensor_id)
        return "2026-11-10T08:00:00.000Z", "2026-11-10T08:10:00.000Z"


def _checker(vlm: _VLM, monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> SubjectChecker:
    """A checker whose frame fetch returns a frame 100 + 100*object_id pixels wide."""

    async def fetch(self: SubjectChecker, subject: Subject, *_args: Any) -> bytes:
        return _jpeg(width=100 + 100 * int(subject.object_id))

    monkeypatch.setattr(SubjectChecker, "_fetch_frame", fetch)
    return SubjectChecker(image_analyzer=vlm, vst=_VST(), vst_internal_url="http://vst", **kwargs)  # type: ignore[arg-type]


# ------------------------------------------------------------------ pure helpers


@pytest.mark.parametrize(
    ("text", "verdict"),
    [
        ('{"answer": "yes"}', SubjectVerdict.YES),
        ('```json\n{"answer": "No"}\n```', SubjectVerdict.NO),
        ('<answer>{"answer": "unsure"}</answer>', SubjectVerdict.UNSURE),
        ("no.", SubjectVerdict.NO),
        ("Yes, the boxed person pushes the cart", SubjectVerdict.YES),
        ('{"answer": "maybe"}', SubjectVerdict.UNSURE),
        ("", SubjectVerdict.UNSURE),
    ],
)
def test_parse_verdict(text: str, verdict: SubjectVerdict) -> None:
    assert sc.parse_verdict(text) == verdict


def test_box_to_pixels_scales_normalised_boxes() -> None:
    assert sc.box_to_pixels(_BOX, 200, 100) == (20, 10, 100, 90)


def test_box_to_pixels_keeps_pixel_boxes() -> None:
    box = {"leftX": 20, "rightX": 120, "topY": 5, "bottomY": 95}
    assert sc.box_to_pixels(box, 200, 100) == (20, 5, 120, 95)


@pytest.mark.parametrize(
    "box",
    [
        {"leftX": 20, "rightX": 900, "topY": 5, "bottomY": 95},  # detector ran at another resolution
        {"leftX": 50, "rightX": 20, "topY": 5, "bottomY": 95},  # inverted
        {"leftX": -5, "rightX": 20, "topY": 5, "bottomY": 95},  # negative
        {"leftX": 1, "rightX": 2},  # incomplete
        {"leftX": "a", "rightX": 2, "topY": 1, "bottomY": 3},  # not numeric
    ],
)
def test_box_to_pixels_rejects_unplaceable_boxes(box: dict[str, Any]) -> None:
    assert sc.box_to_pixels(box, 200, 100) is None


def test_draw_subject_box_returns_a_jpeg_with_a_green_box() -> None:
    drawn = sc.draw_subject_box(_jpeg(), _BOX)
    assert drawn is not None
    frame = cv2.imdecode(np.frombuffer(drawn, dtype=np.uint8), cv2.IMREAD_COLOR)
    blue, green, red = (int(v) for v in frame[10, 60])  # on the top edge of the box
    assert green > 200 and blue < 80 and red < 80


def test_draw_subject_box_downscales_large_frames() -> None:
    drawn = sc.draw_subject_box(_jpeg(width=2560, height=1440), _BOX)
    assert drawn is not None
    assert _width(drawn) == 1280


def test_draw_subject_box_rejects_undecodable_bytes_and_bad_boxes() -> None:
    assert sc.draw_subject_box(b"not an image", _BOX) is None
    assert sc.draw_subject_box(_jpeg(), {"leftX": 20, "rightX": 900, "topY": 5, "bottomY": 95}) is None


def test_subject_requires_a_frame_level_box() -> None:
    subject = sc.subject_from_attribute_result(_attr("7"))
    assert subject == Subject(
        sensor_id="cam1", object_id="7", frame_timestamp="2025-01-01T00:00:02Z", bbox=_BOX, score=0.6
    )
    # A behavior-level box describes another moment: drawing it could outline someone else.
    assert sc.subject_from_attribute_result(_attr("7", frame_score=None)) is None
    assert sc.subject_from_attribute_result(_attr("7", bbox={})) is None
    assert sc.subject_from_attribute_result(_attr("7", frame_timestamp=None)) is None
    assert sc.subject_from_attribute_result(_attr("unknown")) is None


# ------------------------------------------------------------------ rerank


@pytest.mark.asyncio
async def test_rerank_moves_rejected_results_below_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    vlm = _VLM({200: '{"answer": "no"}', 300: '{"answer": "yes"}', 400: '{"answer": "unsure"}'})
    checker = _checker(vlm, monkeypatch)
    results = [_result("a"), _result("b"), _result("c"), _result("d")]
    subjects = {"a": _subject("1"), "b": _subject("2"), "c": _subject("3")}  # "d" has no subject

    reranked = await checker.rerank(
        results,
        lambda r: subjects.get(r.video_name),
        query="person pushing a cart wearing a white shirt",
        attributes=["person in white shirt"],
    )

    assert [r.video_name for r in reranked] == ["b", "c", "d", "a"]
    # The results themselves are untouched: same objects, no new fields.
    assert reranked[-1] is results[0]
    assert '"person pushing a cart wearing a white shirt"' in vlm.prompts[0]
    assert '"person in white shirt"' in vlm.prompts[0]


@pytest.mark.asyncio
async def test_rerank_checks_only_up_to_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    vlm = _VLM({200: '{"answer": "no"}', 300: '{"answer": "no"}'})
    checker = _checker(vlm, monkeypatch)
    results = [_result("a"), _result("b")]
    subjects = {"a": _subject("1"), "b": _subject("2")}

    reranked = await checker.rerank(
        results, lambda r: subjects[r.video_name], query="q", attributes=["white shirt"], limit=1
    )

    assert len(vlm.prompts) == 1
    assert [r.video_name for r in reranked] == ["b", "a"]


@pytest.mark.asyncio
async def test_rerank_failures_leave_the_order_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    vlm = _VLM({}, error=RuntimeError("VLM down"))
    checker = _checker(vlm, monkeypatch)
    results = [_result("a"), _result("b")]

    reranked = await checker.rerank(results, lambda _r: _subject("1"), query="q", attributes=["white shirt"])

    assert reranked == results


@pytest.mark.asyncio
async def test_rerank_without_attributes_or_subjects_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    vlm = _VLM({})
    checker = _checker(vlm, monkeypatch)
    results = [_result("a")]

    assert await checker.rerank(results, lambda _r: _subject("1"), query="q", attributes=[" "]) == results
    assert await checker.rerank(results, lambda _r: None, query="q", attributes=["white shirt"]) == results
    assert vlm.prompts == []


@pytest.mark.asyncio
async def test_rerank_reports_its_own_timing_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    checker = _checker(_VLM({200: '{"answer": "yes"}'}), monkeypatch)
    with collect_timings() as stages:
        await checker.rerank([_result("a")], lambda _r: _subject("1"), query="q", attributes=["white shirt"])

    assert {sc.TIMER_TOTAL, sc.TIMER_FETCH, sc.TIMER_DRAW, sc.TIMER_VLM} <= set(stages)
    assert "vlm: inference" not in stages  # kept apart from the critic's VLM stage


@pytest.mark.asyncio
async def test_frame_fetch_maps_file_time_onto_the_vst_timeline(monkeypatch: pytest.MonkeyPatch) -> None:
    requested: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url)
        return httpx.Response(200, content=_jpeg(), request=request)

    real_client = httpx.AsyncClient

    def client_factory(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(sc.httpx, "AsyncClient", client_factory)
    vst = _VST()
    vlm = _VLM({200: '{"answer": "yes"}'})
    checker = SubjectChecker(image_analyzer=vlm, vst=vst, vst_internal_url="http://vst")  # type: ignore[arg-type]

    await checker.rerank(
        [_result("a"), _result("b")],
        lambda _r: _subject("1", sensor_id="cam1"),
        query="q",
        attributes=["white shirt"],
    )

    assert len(vlm.prompts) == 2
    # One stream-id and one timeline lookup per sensor, shared by both results.
    assert vst.resolve_calls == ["cam1"]
    assert vst.timeline_calls == ["stream-cam1"]
    url = requested[0]
    assert url.path == "/vst/api/v1/replay/stream/stream-cam1/picture"
    # 2s into the file, rebased from the synthetic file epoch onto the recording.
    assert urllib.parse.parse_qs(url.query.decode())["startTime"] == ["2026-11-10T08:00:02.000Z"]


@pytest.mark.asyncio
async def test_frame_fetch_http_error_is_unsure(monkeypatch: pytest.MonkeyPatch) -> None:
    real_client = httpx.AsyncClient

    def client_factory(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(lambda r: httpx.Response(500, request=r)), **kwargs)

    monkeypatch.setattr(sc.httpx, "AsyncClient", client_factory)
    vlm = _VLM({})
    checker = SubjectChecker(image_analyzer=vlm, vst=_VST(), vst_internal_url="http://vst")  # type: ignore[arg-type]
    results = [_result("a")]

    assert await checker.rerank(results, lambda _r: _subject("1"), query="q", attributes=["shirt"]) == results
    assert vlm.prompts == []


# ------------------------------------------------------------------ lookup + orchestrator


def test_subject_lookup_matches_merged_results_by_sensor_and_overlap() -> None:
    def candidate(start: str, end: str, subject: AttributeSearchResult | None) -> _fusion.FusionCandidate:
        embed = _result("v")
        embed = embed.model_copy(update={"start_time": start, "end_time": end})
        return _fusion.FusionCandidate(embed, 0.5, 0.5, "", [], subject)

    weak = _attr("1")
    strong = _attr("2").model_copy(update={"metadata": _attr("2").metadata.model_copy(update={"frame_score": 0.9})})
    lookup = _subject_lookup(
        [
            candidate("2025-01-01T00:00:00Z", "2025-01-01T00:00:05Z", weak),
            candidate("2025-01-01T00:00:05Z", "2025-01-01T00:00:10Z", strong),
            candidate("2025-01-01T00:10:00Z", "2025-01-01T00:10:05Z", None),
        ]
    )

    merged = _result("v").model_copy(update={"end_time": "2025-01-01T00:00:10Z"})
    picked = lookup(merged)
    assert picked is not None and picked.object_id == "2"
    assert lookup(_result("v", sensor_id="other")) is None
    later = _result("v").model_copy(update={"start_time": "2025-01-01T00:10:00Z", "end_time": "2025-01-01T00:10:05Z"})
    assert lookup(later) is None


class _Embed:
    async def ainvoke(self, _payload: Any) -> EmbedSearchOutput:
        def item(name: str, sensor: str, similarity: float) -> EmbedSearchResultItem:
            return EmbedSearchResultItem(
                video_name=name,
                description="",
                start_time="2025-01-01T00:00:00Z",
                end_time="2025-01-01T00:00:05Z",
                sensor_id=sensor,
                screenshot_url="",
                similarity_score=similarity,
            )

        return EmbedSearchOutput(results=[item("bystander_clip", "camA", 0.9), item("true_clip", "camB", 0.8)])


class _Attr:
    async def ainvoke(self, payload: dict[str, Any]) -> list[AttributeSearchResult]:
        source = (payload.get("video_sources") or [""])[0]
        object_id = {"bystander_clip": "1", "true_clip": "2"}[source]
        return [_attr(object_id, sensor_id=source)]


def _config(subject_checker: SubjectChecker | None) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        embed_confidence_threshold=0.1,
        default_max_results=5,
        fusion_method="rrf",
        w_attribute=0.55,
        w_embed=0.35,
        w_tag=0.0,
        rrf_k=60,
        rrf_w=0.5,
        top_percent_filter=None,
        vst_internal_url="",
        vst_external_url="",
        behavior_index="behavior_index",
        subject_checker=subject_checker,
    )


@pytest.mark.asyncio
async def test_fusion_demotes_clip_where_the_attribute_person_is_not_acting(monkeypatch: pytest.MonkeyPatch) -> None:
    inp = SearchInput(
        query="person pushing a cart",
        original_query="person in a white shirt pushing a cart",
        attributes=["person in white shirt"],
        source_type="video_file",
        search_mode="fusion",
    )

    without = await execute_core_search_wrapper(
        search_input=inp, embed_search=_Embed(), config=_config(None), attribute_search_fn=_Attr()
    )
    assert [r.video_name for r in without.data] == ["bystander_clip", "true_clip"]

    vlm = _VLM({200: '{"answer": "no"}', 300: '{"answer": "yes"}'})
    checked = await execute_core_search_wrapper(
        search_input=inp,
        embed_search=_Embed(),
        config=_config(_checker(vlm, monkeypatch)),
        attribute_search_fn=_Attr(),
    )

    assert [r.video_name for r in checked.data] == ["true_clip", "bystander_clip"]
    # The VLM is asked the user's own wording, and the results carry no new fields.
    assert '"person in a white shirt pushing a cart"' in vlm.prompts[0]
    assert [r.model_dump().keys() for r in checked.data] == [r.model_dump().keys() for r in without.data]
