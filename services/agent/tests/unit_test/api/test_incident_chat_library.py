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


def _report(incident_id, kind, severity, title, instruments=()):
    payload = {
        "incident": {"type": kind, "severity_level": severity, "title": title, "description": f"{title}."},
        "entities": [{"entity_id": "E1", "type": "human", "description": "a person"}],
        "instruments": [{"instrument_id": f"I{n}", "name": name} for n, name in enumerate(instruments, 1)],
        "assets": [],
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
        self.last_usage = {"total_tokens": 50}

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
}


def _ask(model, index=None, question="How many assaults involved a bottle?"):
    app = FastAPI()
    app.include_router(
        create_incident_chat_router(chat_model=model, mode="grounded", library_index=index or FakeIndex())
    )
    return TestClient(app).post("/api/v1/incidents/chat", json={"messages": [{"role": "user", "content": question}]})


def test_a_count_question_uses_the_code_count_and_checked_citations():
    answer = {
        "answer": "Two assaults involved a bottle: V1 and V3.",
        "citations": [{"incident_id": "V1", "item_id": "I1"}, {"incident_id": "V9", "item_id": "I1"}],
        "insufficient_evidence": False,
    }
    model = FakeModel(dict(PLAN), answer)
    response = _ask(model)
    assert response.status_code == 200
    body = response.json()
    jsonschema.validate(body, json.loads((CONTRACT / "chat_response.schema.json").read_text()))
    grounding = body["grounding"]
    assert grounding["scope"] == "library" and grounding["matching"] == 2
    assert grounding["candidates"] == ["V1", "V3"] and grounding["dropped_citations"] == ["V9/I1"]
    assert [(c["incident_id"], c["item_id"], c["item_type"]) for c in body["message"]["citations"]] == [
        ("V1", "I1", "instrument")
    ]
    context = json.loads(model.calls[1][0][1]["content"].removeprefix("LIBRARY:\n"))
    assert context["total_matching"] == 2 and {r["incident_id"] for r in context["reports"]} == {"V1", "V3"}
    assert model.calls[0][1] == grounded_chat.PLAN_SCHEMA and model.calls[1][1] == grounded_chat.LIBRARY_ANSWER_SCHEMA


def test_no_match_answers_insufficient_evidence_without_a_second_call():
    model = FakeModel({**PLAN, "instrument": "chainsaw"})
    body = _ask(model).json()
    assert body["message"]["insufficient_evidence"] is True and body["message"]["citations"] == []
    assert len(model.calls) == 1 and body["grounding"]["matching"] == 0


def test_an_unknown_type_or_severity_in_the_plan_is_dropped_not_trusted():
    plan = grounded_chat.check_plan({**PLAN, "incident_type": "robbery", "severity": "extreme", "instrument": None})
    assert plan["incident_type"] is None and plan["severity"] is None
    assert sum(grounded_chat.matches(r, plan) for r in REPORTS) == 3
    high = grounded_chat.check_plan({**PLAN, "incident_type": None, "instrument": None, "severity": "high"})
    assert [r.incident_id for r in REPORTS if grounded_chat.matches(r, high)] == ["V1"]


def test_ranking_prefers_the_query_words():
    ranked = grounded_chat.rank(REPORTS, "shop door")
    assert ranked[0].incident_id == "V2"


@pytest.mark.parametrize(
    ("model", "index"),
    [
        (FakeModel(grounded_chat.ChatModelError("down")), None),
        (FakeModel(dict(PLAN)), FakeIndex(error=RuntimeError("db down"))),
        (FakeModel(dict(PLAN), {"answer": "x", "citations": []}), None),
    ],
)
def test_failures_are_502s(model, index):
    assert _ask(model, index).status_code == 502


@pytest.mark.asyncio
async def test_the_default_index_reads_official_else_newest_and_caches(monkeypatch):
    class FakeDB:
        calls = 0

        async def list_videos(self):
            FakeDB.calls += 1
            return [{"id": "V1", "official_revision_id": 11}, {"id": "V2", "official_revision_id": None}]

        async def get_revision(self, revision_id):
            return {"id": revision_id, "payload": {"incident": {"type": "assault"}}, "evidence": []}

        async def list_revisions(self, video_id):
            return [{"id": 21, "revision_no": 1, "payload": {}}, {"id": 22, "revision_no": 2, "payload": {}}]

    async def fake_get_db():
        return FakeDB()

    monkeypatch.setattr(incident_chat.incident_db, "get_db", fake_get_db)
    now = [0.0]
    index = IncidentDBLibraryIndex(ttl_seconds=60, clock=lambda: now[0])
    reports = await index.reports()
    assert [(r.incident_id, r.version_id) for r in reports] == [("V1", 11), ("V2", 22)]
    await index.reports()
    assert FakeDB.calls == 1, "cached within the TTL"
    now[0] = 61.0
    await index.reports()
    assert FakeDB.calls == 2


def test_the_library_prompt_keeps_incident_and_item_ids_out_of_the_prose():
    from vss_agents import incident_contract

    prompt = incident_contract.prompt(grounded_chat.LIBRARY_PROMPT_ID)
    assert "Refer to an incident by its type and title" in prompt
    assert "never write them in the answer unless the question asks" in prompt


def test_incident_and_item_ids_in_a_library_answer_are_resolved_and_recorded():
    answer = {
        "answer": "Two assaults involved a bottle: V1 (I1) and V3, where I1 was a glass bottle.",
        "citations": [{"incident_id": "V1", "item_id": "I1"}, {"incident_id": "V3", "item_id": "I1"}],
        "insufficient_evidence": False,
    }
    body = _ask(FakeModel(dict(PLAN), answer)).json()
    content = body["message"]["content"]
    assert content == (
        'Two assaults involved a bottle: the assault ("Bottle attack at the bar") and the assault '
        '("Fight with a bottle outside"), where the bottle was a glass bottle.'
    )
    assert body["grounding"]["ids_resolved"] == ["V1", "V3", "I1"]
    assert body["grounding"]["incident_titles"]["V1"] == 'the assault ("Bottle attack at the bar")'
    assert [(c["incident_id"], c["item_id"]) for c in body["message"]["citations"]] == [("V1", "I1"), ("V3", "I1")]


def test_ids_stay_in_a_library_answer_when_the_question_asks_for_them():
    answer = {
        "answer": "The incident ids are V1 and V3.",
        "citations": [{"incident_id": "V1", "item_id": "I1"}],
        "insufficient_evidence": False,
    }
    body = _ask(FakeModel(dict(PLAN), answer), question="What are the incident ids of the bottle assaults?").json()
    assert body["message"]["content"] == "The incident ids are V1 and V3."
    assert body["grounding"]["ids_resolved"] == []
