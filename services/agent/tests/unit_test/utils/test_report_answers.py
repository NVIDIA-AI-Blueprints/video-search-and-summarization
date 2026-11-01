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
"""Answers composed from one incident report: references resolved, kinds of question, no ids in the prose."""

from __future__ import annotations

import pytest

from vss_agents.utils import report_answers as ra


def _report(**overrides):
    report = {
        "incident": {
            "type": "burglary",
            "title": "Shop door forced",
            "start_timestamp": 5,
            "end_timestamp": 65,
            "description": "E1 forces the door with I1.",
            "severity_level": 3,
            "severity_reason": "Property damage only.",
        },
        "entities": [
            {
                "entity_id": "E1",
                "type": "person",
                "description": "Person in a dark hood",
                "label": "hooded person",
                "first_seen_seconds": 5,
                "last_seen_seconds": 60,
            }
        ],
        "instruments": [
            {
                "instrument_id": "I1",
                "entity_id": "E1",
                "name": "crowbar",
                "description": "Pry bar",
                "threat_level": 3,
                "timestamp_seconds": 12,
            }
        ],
        "assets": [
            {"asset_id": "A1", "name": "shop door", "description": "Glass door of the shop", "timestamp_seconds": 12}
        ],
        "timeline": [
            {"start_seconds": 12, "end_seconds": 20, "description": "E1 forces A1 open with I1."},
            {"start_seconds": 5, "end_seconds": None, "description": "E1 walks to A1."},
        ],
    }
    report.update(overrides)
    return report


def test_clock_matches_the_console():
    assert [ra.clock(v) for v in (0, 9.9, 61, 3600, 3725, None, "7", True)] == [
        "0:00",
        "0:09",
        "1:01",
        "1:00:00",
        "1:02:05",
        None,
        None,
        None,
    ]


def test_references_resolve_to_descriptions_and_parenthesised_ids_are_dropped():
    mentions = {"E1": "the hooded person", "I1": "the crowbar"}
    assert ra.resolve_references("E1 (E1) forces the door with I1.", mentions) == (
        "The hooded person forces the door with the crowbar."
    )
    assert ra.resolve_references("a person (E1, I1) waits", mentions) == "A person waits"
    assert ra.resolve_references("Q9 stays", mentions) == "Q9 stays", "an id the report does not hold is kept"
    assert ra.resolve_references("E10 left", {"E1": "x"}) == "E10 left", "ids match whole tokens only"
    assert ra.referenced_ids("E1 hands I1 to E1", ["E1", "I1"]) == ["E1", "I1"]


def test_the_timeline_is_sorted_by_start_and_ties_keep_their_order():
    view = ra.report_view(
        _report(
            timeline=[
                {"start_seconds": 9, "description": "b"},
                {"start_seconds": None, "description": "z"},
                {"start_seconds": 3, "description": "a"},
                {"start_seconds": 9, "description": "c"},
                {"description": " "},
            ]
        )
    )
    assert [e["description"] for e in view.timeline] == ["a", "b", "c", "z"]


def test_a_revision_saved_without_its_report_falls_back_to_its_evidence():
    view = ra.report_view(
        None,
        [
            {
                "item_type": "instrument",
                "item_id": "I1",
                "item_name": "crowbar",
                "description": "Pry bar",
                "timestamp_seconds": 8,
            },
            {
                "item_type": "entity",
                "item_id": "E1",
                "item_name": "person",
                "label": "hooded person",
                "box_seconds": 12,
            },
        ],
    )
    assert [(i.kind, i.item_id, i.seconds) for i in view.items] == [("instrument", "I1", 8.0), ("entity", "E1", 12.0)]
    assert view.items[1].is_person and view.mentions() == {"I1": "the crowbar", "E1": "the hooded person"}


@pytest.mark.parametrize(
    ("question", "kind", "start"),
    [
        (
            "What happened, in order?",
            "chronology",
            "The report describes a burglary from 0:05 to 1:05. In order: At 0:05, "
            "the hooded person walks to the shop door.",
        ),
        ("When did it start?", "time", "The incident runs from 0:05 to 1:05 in the video (60 seconds)."),
        ("Who is involved?", "people", "One person is involved: a person in a dark hood (seen from 0:05 to 1:00)."),
        ("Which tools were used?", "objects", "Objects used: a crowbar (pry bar), held by the hooded person"),
        ("Who held the crowbar?", "people", "The crowbar is held by a person in a dark hood."),
        ("How serious was it?", "severity", "The report rates the severity 3 of 5 (medium): property damage only."),
        ("What type of incident is this?", "type", "It is a burglary: Shop door forced."),
        ("Is there a dog?", "absent", 'This report doesn\'t mention "dog". It records one person and two objects'),
        ("What colour was the sky?", "unanswerable", 'This report doesn\'t mention "colour" and "sky".'),
    ],
)
def test_each_kind_of_question_gets_its_answer(question, kind, start):
    answer = ra.compose(question, ra.report_view(_report()))
    assert answer.kind == kind and answer.text.startswith(start), answer.text
    assert not ra._ID.search(answer.text)
    assert answer.insufficient_evidence == (kind in ("absent", "unanswerable"))
    assert (answer.item_ids == ()) == (kind in ("absent", "unanswerable", "time", "type", "severity"))


def test_without_a_timeline_the_description_answers_and_without_either_the_report_cannot():
    described = ra.compose("What happened?", ra.report_view(_report(timeline=[])))
    assert described.text.startswith(
        "The report has no event-by-event timeline. It describes the incident as follows: "
        "The hooded person forces the door with the crowbar."
    )
    assert described.item_ids == ("E1", "I1")
    bare = ra.compose("What happened?", ra.report_view({"incident": {"type": "burglary"}}))
    assert bare.insufficient_evidence and bare.item_ids == ()


def test_a_long_timeline_is_told_up_to_eight_events():
    events = [{"start_seconds": s, "description": f"Step {s}."} for s in range(10)]
    answer = ra.compose("What happened?", ra.report_view(_report(timeline=events)))
    assert "At 0:07, step 7." in answer.text and "Step 8" not in answer.text
    assert answer.text.endswith("The timeline records two more events.")


def test_search_hits_are_grouped_by_incident_without_ids():
    one = ra.compose_hits(
        [
            {
                "item": {"item_type": "instrument", "name": "crowbar", "timestamp_seconds": 8},
                "incident": {"incident_id": "V1", "incident_type": "burglary"},
            },
            {"item": {"item_type": "asset", "name": "door"}, "incident": {"incident_id": "V1"}},
        ]
    )
    assert one == "I found two matching items in a burglary: a crowbar (seen at 0:08) and a door."
