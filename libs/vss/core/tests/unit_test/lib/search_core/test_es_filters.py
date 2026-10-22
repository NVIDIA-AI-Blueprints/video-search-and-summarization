# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the shared Elasticsearch filter builders."""

from __future__ import annotations

import pytest

from vss_core.search_core._internal.es_filters import build_video_sources_filter
from vss_core.search_core._internal.es_filters import escape_wildcard
from vss_core.search_core._internal.es_filters import should_clauses_for_source

_UUID = "8fce43a6-1c35-4d6a-b6e3-391c42090a87"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain", "plain"),
        ("a*b", "a\\*b"),
        ("a?b", "a\\?b"),
        ("a\\b", "a\\\\b"),
    ],
)
def test_escape_wildcard(raw, expected):
    assert escape_wildcard(raw) == expected


def test_should_clauses_no_duplicate_url_keyword():
    clauses = should_clauses_for_source("cam1")
    url_keyword = [c for c in clauses if "wildcard" in c and "sensor.info.url.keyword" in c["wildcard"]]
    assert len(url_keyword) == 1  # single "*name*" clause, no suffix-only duplicate
    # term + 3 wildcard clauses; the buggy regexp clauses were dropped.
    assert len(clauses) == 4


def test_should_clauses_no_regexp():
    clauses = should_clauses_for_source("cam1")
    assert not any("regexp" in c for c in clauses)


def test_should_clauses_escape_metachars():
    clauses = should_clauses_for_source("a*b")
    assert {"wildcard": {"sensor.id.keyword": "*a\\*b*"}} in clauses


@pytest.mark.parametrize("meta", [".", "~", "@", "<", '"', "*"])
def test_should_clauses_lucene_metachars_do_not_raise(meta):
    # A name full of Lucene metacharacters must still produce plain wildcard/term
    # clauses (no regexp), so nothing can be mis-escaped into an invalid ES query.
    name = f"cam{meta}1"
    clauses = should_clauses_for_source(name)
    assert clauses  # non-empty
    assert not any("regexp" in c for c in clauses)
    assert {"term": {"sensor.id.keyword": name}} in clauses


@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n"])
def test_should_clauses_blank_name_yields_nothing(blank):
    assert should_clauses_for_source(blank) == []


def test_build_video_sources_filter_skips_blank_entries():
    # A blank entry must not become a match-all "**" wildcard clause.
    clause = build_video_sources_filter(["", "cam1"], "video_file")
    assert clause is not None
    should = clause["bool"]["should"]
    assert {"wildcard": {"sensor.id.keyword": "**"}} not in should
    assert all("**" not in next(iter(c.get("wildcard", {"": ""}).values())) for c in should if "wildcard" in c)
    # Only cam1's clauses remain (4 of them).
    assert len(should) == 4


def test_build_video_sources_filter_all_blank_returns_none():
    assert build_video_sources_filter(["", "  ", "\t"], "video_file") is None


def test_video_sources_filter_none():
    assert build_video_sources_filter(None, "video_file") is None
    assert build_video_sources_filter([], "video_file") is None


def test_video_sources_filter_uuid_only_uses_terms():
    assert build_video_sources_filter([_UUID], "video_file") == {"terms": {"sensor.id.keyword": [_UUID]}}


def test_video_sources_filter_rtsp_treats_uuid_as_name():
    # rtsp: UUIDs live in the path, not sensor.id, so even a UUID is a name.
    clause = build_video_sources_filter([_UUID], "rtsp")
    assert clause is not None
    assert clause["bool"]["minimum_should_match"] == 1
    assert {"terms": {"sensor.id.keyword": [_UUID]}} not in clause["bool"]["should"]


def test_video_sources_filter_mixed():
    clause = build_video_sources_filter([_UUID, "cam1"], "video_file")
    assert clause is not None
    should = clause["bool"]["should"]
    assert {"terms": {"sensor.id.keyword": [_UUID]}} in should
    assert {"term": {"sensor.id.keyword": "cam1"}} in should


def test_video_sources_filter_names_only():
    clause = build_video_sources_filter(["cam1", "cam2"], "video_file")
    assert clause is not None
    # 4 clauses per name, no terms clause since no UUIDs.
    assert len(clause["bool"]["should"]) == 8
    assert not any("terms" in c for c in clause["bool"]["should"])
