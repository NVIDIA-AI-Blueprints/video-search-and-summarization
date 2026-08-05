# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for phase-1 lexical VLM tag retrieval."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
import pytest

from vss_core.search_core import TagSearch
from vss_core.search_core.errors import InvalidInputError
from vss_core.search_core.models.tag_search import TagSearchInput
from vss_core.vst import VSTError


class _Es:
    def __init__(self, hits: list[dict[str, Any]]) -> None:
        self.hits = hits
        self.index: str | None = None
        self.body: dict[str, Any] | None = None

    async def search(self, *, index: str, body: dict[str, Any]) -> dict[str, Any]:
        self.index = index
        self.body = body
        return {"hits": {"hits": self.hits}}

    async def aclose(self) -> None:
        return None


class _Vst:
    async def get_name_to_stream_id_map(self) -> dict[str, str]:
        return {"dock camera": "stream-1"}

    async def get_timelines_map(self) -> dict[str, tuple[str, str]]:
        return {}

    def build_screenshot_url(self, *, sensor_id: str, timestamp: str, internal: bool = False) -> str:
        return f"http://vst/{sensor_id}/{timestamp}"


def _hit(
    *,
    text: str = '{"tags":["red forklift","loading dock"],"description":"A red forklift at the loading dock."}',
) -> dict[str, Any]:
    return {
        "_score": 4.25,
        "_source": {
            "text": text,
            "sensor": {"id": "sensor-1", "type": "Video"},
            "metadata": {
                "source": "N/A",
                "content_metadata": {
                    "doc_type": "raw_events",
                    "sensorId": "sensor-1",
                    "streamId": "stream-1",
                    "start_ntp_float": 1_735_689_600.0,
                    "end_ntp_float": 1_735_689_605.0,
                },
            },
        },
    }


@pytest.mark.asyncio
async def test_bm25_query_filters_source_identity_and_overlap() -> None:
    es = _Es([_hit()])
    out = await TagSearch(es=es, vst=_Vst(), tag_index="default_*").run(
        TagSearchInput(
            query="red forklift",
            source_type="video_file",
            video_sources=["dock camera"],
            timestamp_start="2025-01-01T00:00:01Z",
            timestamp_end="2025-01-01T00:00:04Z",
        )
    )

    assert es.index == "default_stream_1"
    assert es.body is not None
    assert es.body["query"]["bool"]["must"][0]["match"]["text"]["operator"] == "and"
    filters = es.body["query"]["bool"]["filter"]
    assert any("stream-1" in str(item) for item in filters)
    assert any("start_ntp_float" in str(item) for item in filters)
    assert any("end_ntp_float" in str(item) for item in filters)
    assert out.results[0].video_name == "dock camera"
    assert out.results[0].tags == ["red forklift", "loading dock"]
    assert out.results[0].description == "A red forklift at the loading dock."
    assert out.results[0].lexical_score == pytest.approx(4.25)


@pytest.mark.asyncio
async def test_malformed_tag_document_is_skipped_per_hit() -> None:
    es = _Es([_hit(text='{"tags":["valid"]}'), _hit(text='{"tags":[],"prose":"bad"}')])
    out = await TagSearch(es=es, vst=_Vst()).run(TagSearchInput(query="valid", video_sources=["dock camera"]))
    assert len(out.results) == 1
    assert out.malformed_documents == 1


@pytest.mark.asyncio
async def test_missing_timestamp_is_malformed() -> None:
    hit = _hit()
    del hit["_source"]["metadata"]["content_metadata"]["start_ntp_float"]
    out = await TagSearch(es=_Es([hit]), vst=_Vst()).run(
        TagSearchInput(query="forklift", video_sources=["dock camera"])
    )
    assert out.results == []
    assert out.malformed_documents == 1


def test_video_sources_are_required() -> None:
    with pytest.raises(ValidationError):
        TagSearchInput(query="forklift")  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_unknown_video_source_is_rejected() -> None:
    with pytest.raises(InvalidInputError, match="Unknown video source"):
        await TagSearch(es=_Es([]), vst=_Vst()).run(TagSearchInput(query="forklift", video_sources=["missing camera"]))


@pytest.mark.asyncio
async def test_vst_resolution_failure_is_not_best_effort() -> None:
    class _BrokenVst(_Vst):
        async def get_name_to_stream_id_map(self) -> dict[str, str]:
            raise VSTError("VST unavailable")

    with pytest.raises(VSTError):
        await TagSearch(es=_Es([]), vst=_BrokenVst()).run(
            TagSearchInput(query="forklift", video_sources=["dock camera"])
        )
