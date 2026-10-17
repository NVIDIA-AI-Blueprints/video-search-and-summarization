# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pure-logic tests for the frame-supported PAS ground-truth derivation.

These exercise ``flows.scoring.pas_ground_truth`` in isolation: no capture
harness, no retrieval, no pipeline. The integration with the clip-relevance
scoring backend is covered in ``test_scoring_clip_drive.py``.

    python3 -m pytest scripts/tests/test_pas_ground_truth.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flows.scoring.pas_ground_truth import VERSION, frame_supported_queries, supports


def _fixture():
    """Two eligible tracks; t2's representative frame is 'not visible' (unsupported)."""
    tracks = [
        {
            "track_uid": uid,
            "chunk_id": clip,
            "eligible_for_gt": True,
            "representative_frame": {"frame_attribute": {"shoe color": color}},
        }
        for uid, clip, color in [("t1", "a", "white"), ("t2", "b", "not visible")]
    ]
    pas = {
        "query_id": "pas1",
        "query_domain": "pas",
        "query": "white shoes",
        "resolved_attributes": {"shoe_color": "white"},
        "relevant_track_uids": ["t1", "t2"],
        "relevant_clip_ids": ["a", "b"],
        "segments": [],
    }
    empty = {**pas, "query_id": "pas2", "relevant_track_uids": ["t2"]}
    event = {
        "query_id": "event1",
        "query_domain": "event",
        "query": "an event query",
        "relevant_clip_ids": ["b"],
    }
    return [pas, empty, event], tracks


def test_protocol_version_is_declared() -> None:
    assert VERSION == "physicalai-pas-frame-supported-v1"


def test_frame_support_excludes_and_preserves_input() -> None:
    queries, tracks = _fixture()
    original = json.dumps(queries)
    kept, excluded = frame_supported_queries(queries, tracks, {"a", "b"})

    # pas1: only t1 (white) supports 'white shoes' -> clip 'a'.
    assert kept[0]["relevant_clip_ids"] == ["a"]
    assert kept[0]["n_relevant"] == 1
    assert kept[0]["relevant_track_uids"] == ["t1"]
    assert "segments" not in kept[0]  # old temporal GT dropped for derived GT
    # event query passes through untouched (and as a copy, not the same dict).
    assert kept[1] == queries[2]
    # pas2: t2 is 'not visible' -> no supported positive -> excluded.
    assert excluded == [
        {
            "query_id": "pas2",
            "query_domain": "pas",
            "reason": "no_frame_supported_positives",
        }
    ]
    # The input list is never mutated.
    assert json.dumps(queries) == original


def test_all_values_directional_matching_and_unknowns() -> None:
    # Substring, directional: a required 'hat' is supported by 'black hat'; a
    # required ['Hat',' BAG '] by ['black hat','white bag'] (whitespace-collapsed).
    assert supports(
        {"accessories[2]": ["Hat", " BAG "]},
        {"accessories": ["black hat", "white bag"]},
    )
    # A requirement of two values is not satisfied by one annotation.
    assert not supports({"accessories": ["hat", "bag"]}, {"accessories": ["hat"]})
    # Substring is directional: 'white shoes' is not satisfied by 'white' alone.
    assert not supports({"shoe_color": "white shoes"}, {"shoe color": "white"})
    # An 'unknown' annotation can never support a requirement.
    assert not supports({"shoe_color": "unknown"}, {"shoe color": "unknown"})
    # A missing annotation key raises (not silently returns False).
    with pytest.raises(KeyError):
        supports({"shoe_color": "white"}, {})


def test_invalid_tracks_fail() -> None:
    queries, tracks = _fixture()
    # Duplicate track_uid.
    with pytest.raises(ValueError, match="Duplicate PAS track IDs"):
        frame_supported_queries(queries, tracks + tracks[:1], {"a", "b"})
    # A released positive that is not eligible_for_gt.
    tracks[0]["eligible_for_gt"] = False
    with pytest.raises(ValueError, match="Invalid released PAS positive tracks"):
        frame_supported_queries(queries, tracks, {"a", "b"})


def test_derived_positives_outside_gallery_raise() -> None:
    queries, _tracks = _fixture()
    # pas1 supports t1 -> clip 'a'; restrict the gallery to {'b'} so 'a' is outside it.
    tracks = [
        {
            "track_uid": "t1",
            "chunk_id": "a",
            "eligible_for_gt": True,
            "representative_frame": {"frame_attribute": {"shoe color": "white"}},
        }
    ]
    queries = [
        {
            "query_id": "pas1",
            "query_domain": "pas",
            "query": "white shoes",
            "resolved_attributes": {"shoe_color": "white"},
            "relevant_track_uids": ["t1"],
            "relevant_clip_ids": ["a"],
        }
    ]
    with pytest.raises(ValueError, match="PAS positives outside gallery"):
        frame_supported_queries(queries, tracks, {"b"})
