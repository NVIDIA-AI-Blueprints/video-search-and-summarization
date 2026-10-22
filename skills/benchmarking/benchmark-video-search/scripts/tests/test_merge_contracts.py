# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Behavioral boundaries retained when integrating develop's benchmark fixes."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flows.query import CliQueryBackend, QueryUnanswerableError
from flows.routing import InvalidDecomposition, effective_attributes, route


@pytest.mark.parametrize(
    "has_action,expected", [(True, "fusion"), (False, "attribute")]
)
def test_single_word_attributes_survive_routing_and_cli(has_action, expected):
    saved = {"attributes": ["red"], "has_action": has_action}
    backend = CliQueryBackend(["vss"], decompositions={"q": saved})
    argv = backend.build_argv("q")
    assert argv[3] == expected
    assert argv[argv.index("--attribute") + 1] == "red"
    assert effective_attributes(saved) == ["red"]


def test_omitted_action_preserves_fusion_default():
    assert route({"attributes": ["red"]}) == "fusion"


@pytest.mark.parametrize("bad", [None, "true", 1])
def test_explicit_invalid_action_is_rejected(bad):
    with pytest.raises(InvalidDecomposition, match="has_action"):
        route({"attributes": ["red"], "has_action": bad})


def test_explicit_object_ids_still_override_visual_route():
    assert (
        route({"object_ids": [7], "attributes": ["red"], "has_action": True})
        == "object"
    )
    backend = CliQueryBackend(["vss"], decompositions={"q": {"object_ids": [7]}})
    backend.require_decomposition = True
    with pytest.raises(QueryUnanswerableError, match="outside the benchmark"):
        backend.build_argv("q")


def test_replayed_attributes_do_not_expand_gallery_scope():
    saved = {
        "attributes": ["red"],
        "has_action": True,
        "object_ids": [7],
        "video_sources": ["another-source"],
        "source_type": "rtsp",
        "top_k": 1,
        "timestamp_start": "2025-01-01T00:00:00Z",
        "timestamp_end": "2025-01-01T00:00:05Z",
    }
    backend = CliQueryBackend(
        ["vss"], attribute_input="decomposed", top_k=20, decompositions={"q": saved}
    )
    argv = backend.build_argv("q")
    assert argv[3] == "attribute"
    assert argv[argv.index("--attribute") + 1] == "red"
    assert argv[argv.index("--source-type") + 1] == "video_file"
    assert argv[argv.index("--top-k") + 1] == "20"
    assert not any(
        flag in argv
        for flag in (
            "--video-source",
            "--timestamp-start",
            "--timestamp-end",
            "--object-id",
        )
    )
    assert "--no-merge-adjacent" in argv


@pytest.mark.parametrize(
    "saved",
    [
        None,
        {},
        {"attributes": []},
        {"attributes": "red"},
        {"attributes": ["red"], "_decomposition_error": "failed"},
    ],
)
def test_bad_replay_is_not_replaced_with_original_query(saved):
    backend = CliQueryBackend(
        ["vss"], attribute_input="decomposed", decompositions={"q": saved}
    )
    with pytest.raises(QueryUnanswerableError):
        backend.build_argv("q")


def test_legacy_decomposition_keeps_explicit_source_and_time_filters():
    saved = {
        "query": "rewritten",
        "attributes": ["red"],
        "has_action": True,
        "video_sources": ["source"],
        "timestamp_start": "2025-01-01T00:00:00Z",
        "timestamp_end": "2025-01-01T00:00:05Z",
    }
    argv = CliQueryBackend(["vss"], decompositions={"q": saved}).build_argv("q")
    assert argv[argv.index("--video-source") + 1] == "source"
    assert argv[argv.index("--timestamp-end") + 1] == saved["timestamp_end"]
    assert argv[argv.index("--original-query") + 1] == "q"
