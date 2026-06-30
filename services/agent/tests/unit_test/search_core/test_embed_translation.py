# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the QueryInput-style params -> EmbedSearchInput translator."""

from __future__ import annotations

from lib.search_core._internal.embed_translation import params_to_embed_input


def test_basic_query():
    out = params_to_embed_input({"query": "red car"}, "video_file")
    assert out.query == "red car"
    assert out.source_type == "video_file"
    assert out.video_sources is None
    assert out.top_k is None
    assert out.min_cosine_similarity == 0.0


def test_video_sources_json_array():
    out = params_to_embed_input({"query": "q", "video_sources": '["cam1", "cam2"]'}, "rtsp")
    assert out.video_sources == ["cam1", "cam2"]


def test_video_sources_csv():
    out = params_to_embed_input({"query": "q", "video_sources": "cam1, cam2 ,cam3"}, "rtsp")
    assert out.video_sources == ["cam1", "cam2", "cam3"]


def test_video_sources_json_scalar_string():
    # A JSON scalar string decodes to a single name without literal quotes.
    out = params_to_embed_input({"query": "q", "video_sources": '"cam1"'}, "rtsp")
    assert out.video_sources == ["cam1"]


def test_top_k_coercion():
    out = params_to_embed_input({"query": "q", "top_k": "7"}, "video_file")
    assert out.top_k == 7


def test_malformed_timestamp_becomes_none():
    out = params_to_embed_input(
        {"query": "q", "timestamp_start": "not-a-date", "timestamp_end": "2025-01-01T00:00:00Z"},
        "video_file",
    )
    assert out.timestamp_start is None
    assert out.timestamp_end is not None


def test_min_cosine_similarity_parsed():
    out = params_to_embed_input({"query": "q", "min_cosine_similarity": "0.25"}, "video_file")
    assert out.min_cosine_similarity == 0.25


def test_forwarded_precomputed_and_exclude():
    out = params_to_embed_input(
        {"query": "q"},
        "video_file",
        precomputed_embedding=[0.1, 0.2],
        exclude_videos=[{"sensor_id": "x", "start_timestamp": "s", "end_timestamp": "e"}],
    )
    assert out.precomputed_embedding == [0.1, 0.2]
    assert out.exclude_videos == [{"sensor_id": "x", "start_timestamp": "s", "end_timestamp": "e"}]
