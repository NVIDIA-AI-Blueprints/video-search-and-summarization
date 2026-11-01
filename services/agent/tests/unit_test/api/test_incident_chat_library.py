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

"""Library chat (grounded mode, no incident): a checked plan, an exact count, and incident-level citations."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import jsonschema
import pytest

from vss_agents.api import incident_chat
from vss_agents.api.incident_chat import IncidentDBLibraryIndex
from vss_agents.api.incident_chat import create_incident_chat_router
from vss_agents.utils import grounded_chat
from vss_agents.utils.grounded_chat import LibraryReport

CONTRACT = (
    Path(__file__).resolve().parents[5]
    / "deploy/docker/developer-profiles/dev-profile-incident/contracts/chat-contract-v2"
)


def _report(incident_id, kind, severity, title, instruments=(), assets=()):
    payload = {
        "incident": {"type": kind, "severity_level": severity, "title": title, "description": f"{title}."},
        "entities": [{"entity_id": "E1", "type": "human", "description": "a person"}],
        "instruments": [{"instrument_id": f"I{n}", "name": name} for n, name in enumerate(instruments, 1)],
        "assets": [{"asset_id": f"A{n}", "name": name} for n, name in enumerate(assets, 1)],
    }
    evidence = [
        {"item_type": "instrument", "item_id": "I1", "image": f"evidence/{incident_id}/I1.jpg", "box_seconds": 4}
    ]
    return LibraryReport(incident_id=incident_id, version_id=int(incident_id[1:]), payload=payload, evidence=evidence)


REPORTS = [
    _report("V1", "assault", 4, "Bottle attack at the bar", ["bottle"]),
    _report("V2", "burglary", 2, "Shop door forced", ["crowbar"]),
    _report("V3", "assault", 3, "Fight with a bottle outside", ["glass bottle"]),
]


class FakeIndex:
    def __init__(self, reports=REPORTS, error=None):
        self._reports, self.error = reports, error

    async def reports(self):
        if self.error:
            raise self.error
        return list(self._reports)


class FakeModel:
    model = "nvidia/nemotron-3-ultra"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[tuple[list[dict[str, str]], dict[str, Any]]] = []
        self.last_usage = {"total_tokens": 50, "elapsed_ms": 7}

    async def complete(self, messages, schema):
        self.calls.append(([dict(m) for m in messages], dict(schema)))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


PLAN = {
    "incident_type": "assault",
    "severity": None,
    "instrument": "bottle",
    "asset": None,
    "query": "",
    "wants_count": True,
    "group_by": None,
}
ASK = {**PLAN, "wants_count": False}


def _ask(model, index=None, question="How many assaults involved a bottle?"):
    app = FastAPI()
    app.include_router(
        create_incident_chat_router(chat_model=model, mode="grounded", library_index=index or FakeIndex())
    )
    return TestClient(app).post("/api/v1/incidents/chat", json={"messages": [{"role": "user", "content": question}]})


def _valid(body):
    jsonschema.validate(body, json.loads((CONTRACT / "chat_response.schema.json").read_text()))
    return body


# -- counts and breakdowns: composed by code from exact counts, never by the model ------------------------------------


def test_a_count_is_composed_by_code_from_the_exact_count_with_no_second_model_call():
    model = FakeModel(dict(PLAN))
    body = _valid(_ask(model).json())
    assert body["message"] == {
        "role": "assistant",
        "content": 'The library holds two assault reports involving a bottle: the assault ("Bottle attack at the bar") '
        'and the assault ("Fight with a bottle outside").',
        "citations": [],
        "insufficient_evidence": False,
    }
    grounding = body["grounding"]
    assert len(model.calls) == 1 and model.calls[0][1] == grounded_chat.PLAN_SCHEMA
    assert grounding["answered_by"] == "code" and grounding["count_exact"] is True and grounding["matching"] == 2
    assert grounding["candidates"] == ["V1", "V3"] and grounding["versions"] == {"V1": 1, "V3": 3}
    assert grounding["incident_titles"]["V3"] == 'the assault ("Fight with a bottle outside")'
    assert grounding["usage"] == {"calls": 1, "total_tokens": 50, "elapsed_ms": 7}
    assert "prompt_sha256" not in grounding and len(grounding["plan_prompt_sha256"]) == 64


def test_an_exact_count_of_none_is_an_answer_not_missing_evidence():
    body = _valid(_ask(FakeModel({**PLAN, "instrument": "chainsaw"})).json())
    assert body["message"]["content"] == "The library holds no assault reports involving a chainsaw."
    assert body["message"]["insufficient_evidence"] is False and body["grounding"]["matching"] == 0


def test_a_long_count_names_only_the_first_reports():
    reports = [_report(f"V{n}", "assault", 4, f"Fight {n}", ["bottle"]) for n in range(1, 8)]
    body = _ask(FakeModel(dict(PLAN)), FakeIndex(reports)).json()
    assert body["message"]["content"] == (
        'The library holds seven assault reports involving a bottle, including the assault ("Fight 1"), the assault '
        '("Fight 2") and the assault ("Fight 3").'
    )


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        (
            {**PLAN, "incident_type": None, "instrument": None, "query": "most often", "group_by": "incident_type"},
            "Of the three reports in the library, assault is the most common type, with two reports, followed by "
            "burglary (1).",
        ),
        (
            {**PLAN, "incident_type": None, "instrument": None, "query": "", "group_by": "severity"},
            "Of the three reports in the library, one is high severity (levels 4-5), one medium severity (level 3) and "
            "one low severity (levels 1-2).",
        ),
        (
            {**PLAN, "group_by": "severity"},
            "Of the two assault reports in the library involving a bottle, one is high severity (levels 4-5) and one "
            "medium severity (level 3).",
        ),
    ],
)
def test_a_breakdown_is_composed_by_code(plan, expected):
    body = _valid(_ask(FakeModel(plan), question="What happened most often?").json())
    assert body["message"]["content"] == expected and body["message"]["insufficient_evidence"] is False


def test_tied_types_are_named_together():
    reports = [*REPORTS, _report("V4", "burglary", 2, "Window smashed", ["brick"])]
    plan = {**PLAN, "incident_type": None, "instrument": None, "group_by": "incident_type"}
    body = _ask(FakeModel(plan), FakeIndex(reports)).json()
    assert body["message"]["content"] == (
        "Of the four reports in the library, assault and burglary are the most common types, with two reports each."
    )


@pytest.mark.parametrize(
    ("plan", "reason"),
    [
        ({**PLAN, "query": "at night"}, 'not by "at night"'),
        ({**PLAN, "query": "more than two people"}, 'not by "more than two people"'),
        ({**PLAN, "incident_type": "robbery"}, 'not by "robbery" as an incident type'),
        ({**PLAN, "group_by": "location"}, 'not by "location" as a breakdown'),
    ],
)
def test_a_count_the_filters_cannot_capture_is_refused_not_estimated(plan, reason):
    model = FakeModel(plan)
    body = _valid(_ask(model).json())
    assert body["message"]["insufficient_evidence"] is True and body["message"]["citations"] == []
    assert reason in body["message"]["content"] and "exact number" in body["message"]["content"]
    assert body["grounding"]["count_exact"] is False and len(model.calls) == 1
    if plan["incident_type"] == "robbery":
        assert (
            "The incident types are road accident, burglary, explosion, assault and animal attack."
            in (body["message"]["content"])
        )


@pytest.mark.parametrize("query", ["", "incidents", "assault incidents with a bottle", "how many were recorded"])
def test_generic_words_and_the_filters_own_words_keep_a_count_exact(query):
    assert grounded_chat.count_exact(grounded_chat.check_plan({**PLAN, "query": query}))


def test_an_unknown_type_or_severity_in_the_plan_is_dropped_and_recorded():
    plan = grounded_chat.check_plan({**PLAN, "incident_type": "robbery", "severity": "extreme", "instrument": None})
    assert plan["incident_type"] is None and plan["severity"] is None
    assert plan["dropped"] == {"incident_type": "robbery", "severity": "extreme"}
    assert sum(grounded_chat.matches(r, plan) for r in REPORTS) == 3 and not grounded_chat.count_exact(plan)
    high = grounded_chat.check_plan({**PLAN, "incident_type": None, "instrument": None, "severity": "high"})
    assert [r.incident_id for r in REPORTS if grounded_chat.matches(r, high)] == ["V1"]


def test_type_spelling_is_normalised_not_dropped():
    plan = grounded_chat.check_plan({**PLAN, "incident_type": "Road_Accident"})
    assert plan["incident_type"] == "road accident" and plan["dropped"] == {}


# -- matching: whole words over an object's name, label and description -----------------------------------------------


@pytest.mark.parametrize(
    ("phrase", "names", "expected"),
    [
        ("bat", ["bathroom door"], False),
        ("bat", ["baseball bat"], True),
        ("gun", ["handgun"], True),
        ("knives", ["kitchen knife"], True),
        ("glass bottle", ["bottle"], False),
        ("glass bottle", ["green glass bottle"], True),
        ("tv", ["TV set"], True),
    ],
)
def test_objects_match_by_whole_word_or_compound_head(phrase, names, expected):
    report = _report("V9", "assault", 3, "Test", names)
    assert grounded_chat.involves(report, phrase) is expected


def test_an_instrument_filter_also_finds_an_object_filed_as_an_asset():
    report = _report("V9", "burglary", 2, "Till taken", assets=["cash register"])
    plan = grounded_chat.check_plan({**PLAN, "incident_type": None, "instrument": "cash register"})
    assert grounded_chat.matches(report, plan)


def test_an_objects_label_and_description_are_searched():
    report = _report("V9", "assault", 3, "Test")
    report.payload["instruments"].append(
        {"instrument_id": "I1", "name": "weapon", "description": "a long kitchen knife"}
    )
    assert grounded_chat.involves(report, "knife")


def test_ranking_prefers_the_query_words():
    ranked = grounded_chat.rank(REPORTS, "shop door")
    assert ranked[0].incident_id == "V2"


# -- other questions: the model answers from the matching reports ---------------------------------------------------


def test_a_question_that_is_not_a_count_is_answered_by_the_model_with_checked_citations():
    answer = {
        "answer": "The fight outside involved a glass bottle.",
        "citations": [{"incident_id": "V3", "item_id": "I1"}, {"incident_id": "V9", "item_id": "I1"}],
        "insufficient_evidence": False,
    }
    model = FakeModel({**ASK, "query": "outside"}, answer)
    body = _valid(_ask(model, question="Which bottle assault happened outside?").json())
    grounding = body["grounding"]
    assert grounding["answered_by"] == "model" and grounding["dropped_citations"] == ["V9/I1"]
    assert grounding["candidates"] == ["V3", "V1"], "ranked by the query words"
    assert [(c["incident_id"], c["item_id"], c["item_type"]) for c in body["message"]["citations"]] == [
        ("V3", "I1", "instrument")
    ]
    assert grounding["usage"] == {"calls": 2, "total_tokens": 100, "elapsed_ms": 14}
    assert len(grounding["prompt_sha256"]) == 64 and model.calls[1][1] == grounded_chat.LIBRARY_ANSWER_SCHEMA
    context = json.loads(model.calls[1][0][1]["content"].removeprefix("LIBRARY:\n"))
    assert context["total_matching"] == 2 and context["count_exact"] is False, '"outside" is not a filter'
    assert context["counts_by_type"] == {"assault": 2} and context["counts_by_severity"] == {"high": 1, "medium": 1}
    assert context["dropped_filters"] == {}


def test_no_match_for_a_question_that_is_not_a_count_is_insufficient_without_a_second_call():
    model = FakeModel({**ASK, "instrument": "chainsaw"})
    body = _valid(_ask(model, question="Describe the chainsaw assault").json())
    assert body["message"]["insufficient_evidence"] is True and body["message"]["citations"] == []
    assert len(model.calls) == 1 and body["grounding"]["matching"] == 0


@pytest.mark.parametrize(
    ("model", "index"),
    [
        (FakeModel(grounded_chat.ChatModelError("down")), None),
        (FakeModel(dict(PLAN)), FakeIndex(error=RuntimeError("db down"))),
        (FakeModel(dict(ASK), {"answer": "x", "citations": []}), None),
        (FakeModel(dict(ASK), {"answer": "  ", "citations": [], "insufficient_evidence": False}), None),
        (FakeModel({"query": "x"}), None),
    ],
)
def test_failures_are_502s(model, index):
    assert _ask(model, index).status_code == 502


def test_the_library_prompt_keeps_incident_and_item_ids_out_of_the_prose():
    from vss_agents import incident_contract

    prompt = incident_contract.prompt(grounded_chat.LIBRARY_PROMPT_ID)
    assert "Refer to an incident by its type and title" in prompt
    assert "never write them in the answer unless the question asks" in prompt
    assert "only when count_exact is true" in prompt


def test_incident_and_item_ids_in_a_library_answer_are_resolved_and_recorded():
    answer = {
        "answer": "Two assaults involved a bottle: V1 (I1) and V3, where I1 was a glass bottle. E9 ran.",
        "citations": [{"incident_id": "V1", "item_id": "I1"}, {"incident_id": "V3", "item_id": "I1"}],
        "insufficient_evidence": False,
    }
    body = _ask(FakeModel(dict(ASK), answer), question="Which assaults involved a bottle?").json()
    # I1 is a bottle in one incident and a glass bottle in the other: it reads neutrally, never as the wrong one
    # (and a bracketed id after a name is dropped, as the name already says what it is).
    assert body["message"]["content"] == (
        'Two assaults involved a bottle: the assault ("Bottle attack at the bar") and the assault '
        '("Fight with a bottle outside"), where an object was a glass bottle. A person ran.'
    )
    assert body["grounding"]["ids_resolved"] == ["V1", "V3", "I1", "E9"]
    assert [(c["incident_id"], c["item_id"]) for c in body["message"]["citations"]] == [("V1", "I1"), ("V3", "I1")]


def test_an_item_id_cited_with_one_incident_names_that_incidents_item():
    answer = {
        "answer": "In the fight outside, I1 was swung.",
        "citations": [{"incident_id": "V3", "item_id": "I1"}],
        "insufficient_evidence": False,
    }
    body = _ask(FakeModel(dict(ASK), answer), question="What was swung outside?").json()
    assert body["message"]["content"] == "In the fight outside, the glass bottle was swung."


def test_a_title_with_a_backslash_is_written_literally():
    reports = [_report("V1", "assault", 4, r"Fight at C:\bar \1 \g<0>", ["bottle"])]
    answer = {"answer": "V1 involved a bottle.", "citations": [], "insufficient_evidence": False}
    body = _ask(FakeModel(dict(ASK), answer), FakeIndex(reports), question="Which assault had a bottle?").json()
    assert body["message"]["content"] == r'The assault ("Fight at C:\bar \1 \g<0>") involved a bottle.'


def test_ids_stay_in_a_library_answer_when_the_question_asks_for_them():
    answer = {
        "answer": "The incident ids are V1 and V3.",
        "citations": [{"incident_id": "V1", "item_id": "I1"}],
        "insufficient_evidence": False,
    }
    body = _ask(FakeModel(dict(ASK), answer), question="What are the incident ids of the bottle assaults?").json()
    assert body["message"]["content"] == "The incident ids are V1 and V3."
    assert body["grounding"]["ids_resolved"] == []


# -- the default index: official else newest, in a fixed number of reads, shared by concurrent turns ----------------


class FakeDB:
    def __init__(self):
        self.requests: list[str] = []

    async def list_videos(self):
        self.requests.append("videos")
        await asyncio.sleep(0)
        return [
            {"id": "V1", "official_revision_id": 11},
            {"id": "V2", "official_revision_id": None},
            {"id": "V3", "official_revision_id": None},
        ]

    async def newest_revision_ids(self, video_ids):
        self.requests.append(f"newest:{','.join(video_ids)}")
        return {"V2": 22}

    async def get_revisions(self, revision_ids):
        self.requests.append(f"revisions:{','.join(map(str, revision_ids))}")
        return [{"id": i, "payload": {"incident": {"type": "assault"}}, "evidence": []} for i in revision_ids]


@pytest.mark.asyncio
async def test_the_default_index_reads_official_else_newest_in_three_requests_and_caches(monkeypatch):
    db = FakeDB()

    async def fake_get_db():
        return db

    monkeypatch.setattr(incident_chat.incident_db, "get_db", fake_get_db)
    now = [0.0]
    index = IncidentDBLibraryIndex(ttl_seconds=60, clock=lambda: now[0])
    reports = await index.reports()
    assert [(r.incident_id, r.version_id) for r in reports] == [("V1", 11), ("V2", 22)], "V3 has no revision"
    assert db.requests == ["videos", "newest:V2,V3", "revisions:11,22"]
    await index.reports()
    assert len(db.requests) == 3, "cached within the TTL"
    now[0] = 61.0
    await index.reports()
    assert len(db.requests) == 6


@pytest.mark.asyncio
async def test_concurrent_turns_share_one_library_read(monkeypatch):
    db = FakeDB()

    async def fake_get_db():
        return db

    monkeypatch.setattr(incident_chat.incident_db, "get_db", fake_get_db)
    index = IncidentDBLibraryIndex(ttl_seconds=60, clock=lambda: 0.0)
    first, second = await asyncio.gather(index.reports(), index.reports())
    assert first == second and db.requests.count("videos") == 1
