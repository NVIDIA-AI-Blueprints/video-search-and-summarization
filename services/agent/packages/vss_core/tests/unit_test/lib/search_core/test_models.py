# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for search_core input-model validation and shared models."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime

from pydantic import ValidationError
import pytest

from vss_core.critic import VideoInfo
from vss_core.search_core.errors import InvalidInputError
from vss_core.search_core.models.attribute_search import AttributeSearchInput
from vss_core.search_core.models.embed_search import EmbedSearchInput
from vss_core.search_core.models.search import SearchInput
from vss_core.search_core.models.search import SearchResult


def _valid_search_input(**overrides: object) -> SearchInput:
    kwargs: dict[str, object] = {"query": "q", "source_type": "video_file"}
    kwargs.update(overrides)
    return SearchInput(**kwargs)  # type: ignore[arg-type]


def test_search_input_forbids_extra():
    with pytest.raises(ValidationError):
        _valid_search_input(unknown_field="x")


def test_search_result_defaults_to_null_critic_result() -> None:
    result = SearchResult(
        video_name="video.mp4",
        description="candidate",
        start_time="2025-01-01T00:00:00Z",
        end_time="2025-01-01T00:00:05Z",
        sensor_id="cam-1",
        screenshot_url="https://vss.example/frame.jpg",
        similarity=0.8,
    )

    assert result.critic_result is None
    assert result.model_dump()["critic_result"] is None
    assert result.model_dump()["start_offset"] is None
    assert result.model_dump()["end_offset"] is None


def test_embed_input_forbids_extra():
    with pytest.raises(ValidationError):
        EmbedSearchInput(source_type="video_file", bogus=1)  # type: ignore[call-arg]


@pytest.mark.parametrize("field", ["precomputed_embedding", "image_url", "video_url"])
def test_embed_input_rejects_unsupported_embedding_sources(field: str) -> None:
    with pytest.raises(ValidationError):
        EmbedSearchInput(query="q", **{field: "unsupported"})


def test_attribute_input_forbids_extra():
    with pytest.raises(ValidationError):
        AttributeSearchInput(query="q", bogus=1)  # type: ignore[call-arg]


@pytest.mark.parametrize("bad_top_k", [0, 5000])
def test_search_input_top_k_out_of_bounds_rejected(bad_top_k):
    with pytest.raises(ValidationError):
        _valid_search_input(top_k=bad_top_k)


def test_search_input_top_k_within_bounds_accepted():
    assert _valid_search_input(top_k=5).top_k == 5
    assert SearchInput(query="q").top_k is None


def test_search_mode_requires_matching_explicit_inputs() -> None:
    with pytest.raises(InvalidInputError, match="requires at least one attribute"):
        _valid_search_input(search_mode="fusion").validate_semantics()
    with pytest.raises(InvalidInputError, match="attributes require"):
        _valid_search_input(attributes=["red"]).validate_semantics()
    with pytest.raises(InvalidInputError, match="object_ids require"):
        _valid_search_input(object_ids=[42]).validate_semantics()
    with pytest.raises(InvalidInputError, match="does not accept attributes"):
        _valid_search_input(search_mode="object", object_ids=[42], attributes=["red"]).validate_semantics()


@pytest.mark.parametrize("bad_top_k", [0, 5000])
def test_embed_input_top_k_out_of_bounds_rejected(bad_top_k):
    with pytest.raises(ValidationError):
        EmbedSearchInput(source_type="video_file", top_k=bad_top_k)


@pytest.mark.parametrize("bad_top_k", [0, 5000])
def test_attribute_input_top_k_out_of_bounds_rejected(bad_top_k):
    with pytest.raises(ValidationError):
        AttributeSearchInput(query="q", top_k=bad_top_k)


def test_search_input_source_type_literal_rejected():
    with pytest.raises(ValidationError):
        _valid_search_input(source_type="webcam")


def test_embed_input_source_type_literal_rejected():
    with pytest.raises(ValidationError):
        EmbedSearchInput(source_type="webcam")  # type: ignore[arg-type]


def test_min_cosine_similarity_bounds():
    assert EmbedSearchInput(source_type="video_file", min_cosine_similarity=-1.0).min_cosine_similarity == -1.0
    with pytest.raises(ValidationError):
        EmbedSearchInput(source_type="video_file", min_cosine_similarity=-1.5)
    with pytest.raises(ValidationError):
        EmbedSearchInput(source_type="video_file", min_cosine_similarity=1.5)


def test_video_info_hashable_and_equal():
    start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
    end = datetime(2025, 1, 1, 0, 0, 5, tzinfo=UTC)
    a = VideoInfo(sensor_id="s1", start_timestamp=start, end_timestamp=end)
    b = VideoInfo(sensor_id="s1", start_timestamp=start, end_timestamp=end)
    c = VideoInfo(sensor_id="s2", start_timestamp=start, end_timestamp=end)
    assert a == b
    assert hash(a) == hash(b)
    assert a != c
    # Works as a set/dict member (frozen=True).
    assert len({a, b, c}) == 2


def test_video_info_frozen():
    vi = VideoInfo(
        sensor_id="s1",
        start_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        end_timestamp=datetime(2025, 1, 1, 0, 0, 5, tzinfo=UTC),
    )
    with pytest.raises(ValidationError):
        vi.sensor_id = "s2"


def test_video_info_coerces_iso_string():
    vi = VideoInfo(
        sensor_id="s1",
        start_timestamp="2025-01-01T00:00:00Z",  # type: ignore[arg-type]
        end_timestamp="2025-01-01T00:00:05Z",  # type: ignore[arg-type]
    )
    assert vi.start_timestamp.tzinfo is not None
