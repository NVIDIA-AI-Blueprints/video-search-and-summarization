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
    assert ra.resolve_references("E10 left", {"E1": "x"}) == "A person left", "whole tokens: E1 never matches in E10"
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


def test_ids_the_report_no_longer_holds_read_neutrally_and_other_tokens_stay():
    mentions = ra.Mentions({"E1": "the guard", "new-E2": "the courier"})
    mentions.retired = frozenset({"A2"})
    assert ra.resolve_references("new-E2 hands E1 a parcel.", mentions) == "The courier hands the guard a parcel."
    assert ra.resolve_references("new-E3 waits; E7 drops I9.", mentions) == "A person waits; a person drops an object."
    assert ra.resolve_references("E1 takes A2 from the A1 shelf of A4 paper.", mentions) == (
        "The guard takes an item from the A1 shelf of A4 paper."
    )
    assert ra.resolve_references("xE1, E1x and E1-2 are not ids", mentions) == "XE1, E1x and E1-2 are not ids"
    assert ra.replaced_ids("new-E2 and E7 meet near A4 paper", mentions) == ["new-E2", "E7"]
    assert ra.referenced_ids("new-E2 hands E1 a parcel", ["E1", "E2", "new-E2"]) == ["new-E2", "E1"]


def test_a_draft_is_overlaid_on_its_base_version():
    base = _report()
    draft = {
        "incident": {"type": "robbery", "description": "E1 forces the door."},
        "entities": [{"entity_id": "E1", "type": "person", "description": "Person in a dark hood"}],
        "instruments": [],
        "assets": [],
    }
    report = ra.overlay_draft(base, draft)
    assert report["incident"]["type"] == "robbery" and report["incident"]["title"] == "Shop door forced"
    assert report["timeline"] == base["timeline"] and report["instruments"] == []
    retired = ra.item_ids(base) - ra.item_ids(report)
    assert retired == {"I1", "A1"}
    view = ra.report_view(report, retired=retired)
    answer = ra.compose("What happened, in order?", view)
    assert "At 0:05, the person in a dark hood walks to an item." in answer.text
    assert "the person in a dark hood forces an item open with an object" in answer.text
    assert not ra._ID.search(answer.text)


@pytest.mark.parametrize(
    ("question", "start", "kind"),
    [
        (
            "Did the hooded person steal anything?",
            "Here is what the report records about the hooded person, in order:",
            "chronology",
        ),
        ("Was the door broken?", "Here is what the report records about the shop door, in order:", "chronology"),
        ("Is there a crowbar?", "Yes: the report records a crowbar (pry bar)", "objects"),
        ("Any crowbars?", "Yes: the report records a crowbar (pry bar)", "objects"),
    ],
)
def test_yes_is_said_only_to_a_bare_existence_question(question, start, kind):
    answer = ra.compose(question, ra.report_view(_report()))
    assert answer.text.startswith(start) and answer.kind == kind, answer.text
    if kind == "chronology":
        assert "Yes" not in answer.text


def test_a_yes_no_question_about_an_item_with_no_event_says_the_report_does_not_answer():
    report = _report(timeline=[{"start_seconds": 5, "description": "E1 walks in."}])
    answer = ra.compose("Was the door broken?", ra.report_view(report))
    assert answer.text == "The report records the shop door but says nothing more about it that answers this."
    assert answer.insufficient_evidence and answer.item_ids == ()


def test_plurals_match_singulars_and_missing_words_are_quoted_as_asked():
    report = _report(instruments=[{"instrument_id": "I1", "entity_id": "E1", "name": "knife", "threat_level": 4}])
    found = ra.compose("Were there any knives?", ra.report_view(report))
    assert found.text.startswith("Yes: the report records a knife"), found.text
    missing = ra.compose("Were there any witnesses?", ra.report_view(report))
    assert missing.text.startswith('This report doesn\'t mention "witnesses".'), missing.text


def test_labels_that_name_each_other_never_put_an_id_in_the_prose():
    report = _report(
        entities=[
            {"entity_id": "E1", "type": "person", "label": "man beside E2", "description": "Man beside E2"},
            {"entity_id": "E2", "type": "person", "label": "man beside E1", "description": "Man beside E1"},
        ]
    )
    view = ra.report_view(report)
    assert all(not ra._ID.search(m) for m in view.mentions().values()), view.mentions()
    for question in ("Who is involved?", "What happened, in order?", "Is there a dog?"):
        assert not ra._ID.search(ra.compose(question, view).text), question
