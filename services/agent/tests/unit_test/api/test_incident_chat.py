# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``POST /api/v1/incidents/chat`` with SearchClient fakes."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from pydantic import ValidationError
import pytest

from vss_agents.api.incident_chat import NOT_SEARCHED
from vss_agents.api.incident_chat import AssistantMessage
from vss_agents.api.incident_chat import ChatRequest
from vss_agents.api.incident_chat import ChatResponse
from vss_agents.api.incident_chat import InMemorySearchClient
from vss_agents.api.incident_chat import RevisionStoreUnavailableError
from vss_agents.api.incident_chat import register_incident_chat_routes

CONTRACTS_DIR = Path(__file__).resolve().parents[5] / "deploy/docker/developer-profiles/dev-profile-incident/contracts"
CHAT_RESPONSE_SCHEMA = json.loads(
    (CONTRACTS_DIR / "chat-contract-v1" / "chat_response.schema.json").read_text(encoding="utf-8")
)
response_validator = Draft202012Validator(CHAT_RESPONSE_SCHEMA)


class FakeSearchClient:
    """Configurable fake SearchClient for unit tests."""

    def __init__(
        self,
        hits: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.hits = hits if hits is not None else []
        self.error = error
        self.last_query: str | None = None
        self.last_filters: dict[str, Any] | None = None

    async def search(
        self,
        query: str,
        top_k: int = 5,
        item_types: list[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        self.last_query = query
        self.last_filters = filters
        if self.error:
            raise self.error
        return self.hits[:top_k]


class FakeRevisionStore:
    """Revisions keyed by id; ``official`` maps video_id to its official revision id."""

    def __init__(
        self,
        revisions: dict[int, dict[str, Any]] | None = None,
        official: dict[str, int] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.revisions = revisions or {}
        self.official = official or {}
        self.error = error

    async def get_revision(self, revision_id: int) -> dict[str, Any] | None:
        if self.error:
            raise self.error
        return self.revisions.get(revision_id)

    async def get_official_revision_id(self, video_id: str) -> int | None:
        return self.official.get(video_id)


def _make_client(search_client=None, revision_store=None) -> TestClient:
    app = FastAPI()
    register_incident_chat_routes(app, search_client=search_client, revision_store=revision_store)
    return TestClient(app)


def _revision(revision_id: int, video_id: str = "V1") -> dict[str, Any]:
    return {
        "id": revision_id,
        "video_id": video_id,
        "evidence": [
            {
                "item_type": "instrument",
                "item_id": "I1",
                "item_name": "crowbar",
                "label": "crowbar",
                "description": "Pry bar on the door",
                "timestamp_seconds": 8,
                "image": f"evidence/{video_id}/batch{revision_id}/I1.jpg",
            },
            {
                "item_type": "entity",
                "item_id": "E1",
                "item_name": "person",
                "label": "person",
                "description": "Person in a dark hood",
                "timestamp_seconds": 12,
                "image": f"evidence/{video_id}/batch{revision_id}/E1.jpg",
            },
        ],
    }


def _ask(client: TestClient, **extra: Any):
    return client.post(
        "/api/v1/incidents/chat",
        json={"messages": [{"role": "user", "content": "Is there a crowbar?"}], **extra},
    )


def test_chat_minimal_request_returns_grounded_answer():
    client = _make_client(InMemorySearchClient())  # the demo hits are a test fixture, never the default
    resp = client.post(
        "/api/v1/incidents/chat",
        json={"messages": [{"role": "user", "content": "What weapon was brandished in the altercation?"}]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert data["message"]["role"] == "assistant"
    assert len(data["message"]["citations"]) > 0
    cit = data["message"]["citations"][0]
    assert cit["item_id"] == "I1"
    assert cit["item_type"] == "instrument"
    assert cit["incident_id"] == "Assault007"
    assert "machete" in data["message"]["content"].lower()

    # Validate against chat-contract-v1 JSON Schema
    errors = list(response_validator.iter_errors(data))
    assert not errors, f"Response failed chat-contract-v1 schema: {errors}"


def test_chat_with_custom_search_client_fake():
    fake_hit = {
        "result_id": "res_custom_1",
        "rank": 1,
        "similarity_score": 0.95,
        "item": {
            "item_type": "instrument",
            "item_id": "I99",
            "name": "hammer",
            "label": "hammer",
            "description": "Claw hammer dropped on sidewalk",
            "threat_level": 3,
            "crop_image_url": "https://pub-r2.example.com/evidence/V1/MR1/instruments/I99.jpg",
            "timestamp_seconds": 42.0,
        },
        "incident": {
            "incident_id": "V1",
            "incident_type": "burglary",
            "title": "Hardware store break-in",
        },
    }
    fake_client = FakeSearchClient(hits=[fake_hit])
    client = _make_client(search_client=fake_client)

    resp = client.post(
        "/api/v1/incidents/chat",
        json={
            "session_id": "session_test_abc",
            "messages": [{"role": "user", "content": "What tools were found?"}],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "session_test_abc"
    assert fake_client.last_query == "What tools were found?"
    assert len(data["message"]["citations"]) == 1
    assert data["message"]["citations"][0]["item_id"] == "I99"
    assert data["message"]["citations"][0]["timestamp_seconds"] == 42.0
    assert "hammer" in data["message"]["content"]


def test_chat_empty_results_cleanly_handled():
    fake_client = FakeSearchClient(hits=[])
    client = _make_client(search_client=fake_client)

    resp = client.post(
        "/api/v1/incidents/chat",
        json={"messages": [{"role": "user", "content": "Did an extraterrestrial land?"}]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["message"]["citations"] == []
    assert "No matching incidents or evidence items were found" in data["message"]["content"]

    # Validate against chat-contract-v1 schema
    errors = list(response_validator.iter_errors(data))
    assert not errors, f"Empty response failed schema: {errors}"


def test_chat_incident_scope_filters_by_incident_id():
    fake_client = FakeSearchClient(hits=[])
    client = _make_client(search_client=fake_client)

    resp = client.post(
        "/api/v1/incidents/chat",
        json={
            "messages": [{"role": "user", "content": "What did you see?"}],
            "context": {"scope": "incident", "incident_id": "Assault007"},
        },
    )
    assert resp.status_code == 200
    assert fake_client.last_filters == {"incident_id": "Assault007"}


def test_chat_search_client_error_returns_502():
    fake_client = FakeSearchClient(error=RuntimeError("Connection refused to vector store"))
    client = _make_client(search_client=fake_client)

    resp = client.post(
        "/api/v1/incidents/chat",
        json={"messages": [{"role": "user", "content": "Any knives?"}]},
    )
    assert resp.status_code == 502
    assert "Evidence search error" in resp.json()["detail"]


def test_chat_missing_user_message_returns_400():
    client = _make_client()
    resp = client.post(
        "/api/v1/incidents/chat",
        json={"messages": [{"role": "assistant", "content": "Hello user"}]},
    )
    assert resp.status_code == 400
    assert "At least one message with role 'user' is required" in resp.json()["detail"]


def test_chat_empty_user_message_returns_400():
    client = _make_client()
    resp = client.post(
        "/api/v1/incidents/chat",
        json={"messages": [{"role": "user", "content": "   "}]},
    )
    assert resp.status_code == 400
    assert "must not be empty" in resp.json()["detail"]


def test_chat_invalid_scope_returns_422():
    client = _make_client()
    resp = client.post(
        "/api/v1/incidents/chat",
        json={
            "messages": [{"role": "user", "content": "weapons?"}],
            "context": {"scope": "invalid_scope"},
        },
    )
    assert resp.status_code == 422


def test_chat_multi_turn_history_uses_latest_user_message():
    fake_client = FakeSearchClient(hits=[])
    client = _make_client(search_client=fake_client)

    resp = client.post(
        "/api/v1/incidents/chat",
        json={
            "messages": [
                {"role": "user", "content": "First question about cars"},
                {"role": "assistant", "content": "No cars found"},
                {"role": "user", "content": "Second question about crowbars"},
            ]
        },
    )
    assert resp.status_code == 200
    assert fake_client.last_query == "Second question about crowbars"


FIXTURES_DIR = CONTRACTS_DIR / "chat-contract-v1" / "fixtures"


def _fixtures(kind: str, prefix: str) -> list[Path]:
    return sorted((FIXTURES_DIR / kind).glob(f"{prefix}*.json"))


def _fixture_id(path: Path) -> str:
    return path.name


@pytest.mark.parametrize(
    "path", _fixtures("valid", "full-request") + _fixtures("valid", "minimal-request"), ids=_fixture_id
)
def test_models_accept_valid_request_fixtures(path: Path):
    ChatRequest.model_validate(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("path", _fixtures("invalid", "request-"), ids=_fixture_id)
def test_models_reject_invalid_request_fixtures(path: Path):
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    "path", _fixtures("valid", "full-response") + _fixtures("valid", "empty-response"), ids=_fixture_id
)
def test_models_accept_valid_response_fixtures(path: Path):
    ChatResponse.model_validate(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("path", _fixtures("invalid", "response-"), ids=_fixture_id)
def test_models_reject_invalid_response_fixtures(path: Path):
    with pytest.raises(ValidationError):
        ChatResponse.model_validate(json.loads(path.read_text(encoding="utf-8")))


def test_assistant_message_requires_citations():
    with pytest.raises(ValidationError):
        AssistantMessage.model_validate({"role": "assistant", "content": "hi"})


def test_chat_non_official_version_cites_its_own_saved_crops():
    search = FakeSearchClient(hits=[])
    store = FakeRevisionStore(revisions={2: _revision(2), 3: _revision(3)}, official={"V1": 3})
    resp = _ask(_make_client(search, store), version_id=2)
    assert resp.status_code == 200
    data = resp.json()
    assert search.last_query is None  # evidence_crops is never searched for a non-official revision
    first = data["message"]["citations"][0]
    assert (first["incident_id"], first["item_id"], first["item_type"]) == ("V1", "I1", "instrument")
    assert first["timestamp_seconds"] == 8
    assert first["crop_image_url"].endswith("evidence/V1/batch2/I1.jpg")
    assert not list(response_validator.iter_errors(data))


def test_chat_official_version_uses_search_index():
    search = FakeSearchClient(hits=[])
    store = FakeRevisionStore(revisions={3: _revision(3)}, official={"V1": 3})
    resp = _ask(_make_client(search, store), version_id=3)
    assert resp.status_code == 200
    assert search.last_filters == {"incident_id": "V1"}


def test_chat_draft_uses_draft_items_with_crops_from_base_version():
    search = FakeSearchClient(hits=[])
    store = FakeRevisionStore(revisions={3: _revision(3)}, official={"V1": 3})
    draft = {
        "instruments": [
            {"instrument_id": "I1", "name": "lockpick", "description": "Thin tool", "timestamp_seconds": 9},
            {"instrument_id": "I2", "name": "torch", "description": "Handheld torch", "timestamp_seconds": 11},
        ]
    }
    resp = _make_client(search, store).post(
        "/api/v1/incidents/chat",
        json={
            "messages": [{"role": "user", "content": "Which tools are there?"}],
            "version_id": 3,
            "draft_report": draft,
        },
    )
    assert resp.status_code == 200
    assert search.last_query is None
    assert "lockpick" in resp.json()["message"]["content"] and "torch" in resp.json()["message"]["content"]
    cits = {c["item_id"]: c for c in resp.json()["message"]["citations"]}
    assert set(cits) == {"I1", "I2"}
    assert cits["I1"]["timestamp_seconds"] == 9
    assert cits["I1"]["crop_image_url"].endswith("evidence/V1/batch3/I1.jpg")
    assert cits["I2"]["crop_image_url"] == ""


def test_chat_version_ranks_keyword_matches_first():
    store = FakeRevisionStore(revisions={2: _revision(2)})
    resp = _make_client(FakeSearchClient(), store).post(
        "/api/v1/incidents/chat",
        json={"messages": [{"role": "user", "content": "who wears a hood?"}], "version_id": 2},
    )
    assert resp.json()["message"]["citations"][0]["item_id"] == "E1"


def test_chat_unknown_version_returns_404():
    resp = _ask(_make_client(FakeSearchClient(), FakeRevisionStore()), version_id=99)
    assert resp.status_code == 404


def test_chat_version_from_other_incident_returns_400():
    store = FakeRevisionStore(revisions={2: _revision(2, "V1")})
    resp = _ask(
        _make_client(FakeSearchClient(), store),
        version_id=2,
        context={"scope": "incident", "incident_id": "V2"},
    )
    assert resp.status_code == 400


def test_chat_version_store_unavailable_returns_503():
    store = FakeRevisionStore(error=RevisionStoreUnavailableError("not configured"))
    assert _ask(_make_client(FakeSearchClient(), store), version_id=2).status_code == 503


def test_chat_version_store_error_returns_502():
    store = FakeRevisionStore(error=RuntimeError("boom"))
    assert _ask(_make_client(FakeSearchClient(), store), version_id=2).status_code == 502


def test_chat_draft_without_version_returns_422():
    resp = _ask(_make_client(FakeSearchClient(), FakeRevisionStore()), draft_report={"entities": []})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Template answers composed from the report (regression: the item-list template)
# ---------------------------------------------------------------------------

VIDEO = "vc8328cb9cdcdfa99fa8"
ID_TOKEN = re.compile(r"(?<![A-Za-z0-9])[EIA][0-9]+(?![A-Za-z0-9])")


def _robbery_revision(revision_id: int = 7) -> dict[str, Any]:
    """A report shaped like the one that drew the item-list answer: three people and a till that is both."""
    payload = {
        "incident": {
            "type": "robbery",
            "title": "Counter robbery at a convenience store",
            "start_timestamp": 130,
            "end_timestamp": 180,
            "description": "E1 approaches the counter while E2 serves; E1 grabs cash from I1 and leaves with E3.",
            "severity_level": 4,
            "severity_reason": "E1 threatens E2 and takes cash.",
            "confidence_score": 0.8,
            "location": "shop",
        },
        "entities": [
            {
                "entity_id": "E1",
                "type": "human",
                "description": "Man in a black hoodie and grey jeans",
                "label": "man in black hoodie",
                "timestamp_seconds": 134,
                "first_seen_seconds": 130,
                "last_seen_seconds": 175,
            },
            {
                "entity_id": "E2",
                "type": "human",
                "description": "Cashier in a red apron behind the counter",
                "label": "cashier in red apron",
                "timestamp_seconds": 141,
                "first_seen_seconds": 130,
                "last_seen_seconds": 180,
            },
            {
                "entity_id": "E3",
                "type": "human",
                "description": "Second man in a white cap waiting by the door",
                "label": "man in white cap",
                "timestamp_seconds": 173,
                "first_seen_seconds": 160,
                "last_seen_seconds": 178,
            },
        ],
        "instruments": [
            {
                "instrument_id": "I1",
                "entity_id": "E1",
                "name": "cash register",
                "description": "Cash register drawer pulled open by E1",
                "threat_level": 2,
                "timestamp_seconds": 134,
                "label": "cash register",
            },
        ],
        "assets": [
            {
                "asset_id": "A1",
                "name": "cash register",
                "description": "Till on the shop counter",
                "timestamp_seconds": 142,
                "label": "cash register",
            },
        ],
        "timeline": [  # stored out of order on purpose: the answer must sort it
            {
                "start_seconds": 141,
                "end_seconds": 150,
                "description": "E1 pulls open I1 and takes cash while E2 steps back.",
            },
            {
                "start_seconds": 134,
                "end_seconds": 140,
                "description": "E1 (E1) walks up to the counter and confronts E2.",
            },
            {"start_seconds": 170, "end_seconds": None, "description": "E1 leaves through the door with E3."},
        ],
        "uncertainties": [],
    }
    evidence = [
        {
            "item_type": kind,
            "item_id": item_id,
            "item_name": name,
            "label": name,
            "timestamp_seconds": seconds,
            "image": f"evidence/{VIDEO}/run1/{item_id}.jpg",
        }
        for kind, item_id, name, seconds in (
            ("entity", "E1", "human", 134),
            ("entity", "E2", "human", 141),
            ("entity", "E3", "human", 173),
            ("instrument", "I1", "cash register", 134),
            ("asset", "A1", "cash register", 142),
        )
    ]
    return {"id": revision_id, "video_id": VIDEO, "payload": payload, "evidence": evidence}


def _ask_version(question: str, *, official: bool = False, search=None) -> dict[str, Any]:
    store = FakeRevisionStore(revisions={7: _robbery_revision()}, official={VIDEO: 7} if official else {})
    resp = _make_client(search or FakeSearchClient(), store).post(
        "/api/v1/incidents/chat",
        json={
            "messages": [{"role": "user", "content": question}],
            "version_id": 7,
            "context": {"scope": "incident", "incident_id": VIDEO},
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert not list(response_validator.iter_errors(data))
    return data["message"]


def test_a_chronological_question_gets_the_timeline_in_order_not_an_item_list():
    message = _ask_version("What happened, in order?")
    content = message["content"]
    assert "relevant item(s) were identified" not in content, "the old item-list template is gone"
    assert content.index("2:14") < content.index("2:21") < content.index("2:50"), content
    assert "walks up to the counter" in content and "leaves through the door" in content
    assert "the man in black hoodie" in content and "the cashier in red apron" in content
    assert [c["item_id"] for c in message["citations"]] == ["E1", "E2", "I1", "E3"]
    assert message["citations"][0]["crop_image_url"].endswith(f"evidence/{VIDEO}/run1/E1.jpg")


def test_an_object_question_describes_the_objects_and_cites_them():
    message = _ask_version("Which objects were involved?")
    assert "cash register" in message["content"] and "held by the man in black hoodie" in message["content"]
    assert [c["item_id"] for c in message["citations"]] == ["I1", "A1"]
    assert {c["item_type"] for c in message["citations"]} == {"instrument", "asset"}


def test_a_harm_question_names_the_instruments_with_their_threat_levels():
    message = _ask_version("What could cause harm here?")
    assert message["content"].startswith(
        "The report lists one object that could be used to cause harm: a cash register (cash register drawer pulled "
        "open by the man in black hoodie), held by the man in black hoodie (threat level 2 of 5)"
    ), message["content"]
    assert [c["item_id"] for c in message["citations"]] == ["I1"]


def test_what_was_taken_or_could_cause_harm_answers_both_parts_from_the_report():
    message = _ask_version("What was taken, or what could cause harm?")
    content = message["content"]
    assert "relevant item(s) were identified" not in content, "never the old item-list template"
    assert content.startswith(
        "The report records this taking: From 2:21 to 2:30, the man in black hoodie pulls open the cash register and "
        "takes cash while the cashier in red apron steps back."
    ), content
    assert "could be used to cause harm: a cash register" in content and "(threat level 2 of 5)" in content
    assert not ID_TOKEN.search(content) and VIDEO not in content
    assert [c["item_id"] for c in message["citations"]] == ["E1", "I1", "E2"]


def _burglary_revision(revision_id: int = 8) -> dict[str, Any]:
    """Shaped like the reported Burglary061 version: a bag placed and handled, nothing recorded as taken."""
    payload = {
        "incident": {
            "type": "burglary",
            "title": "Person places bag on counter and exits",
            "start_timestamp": 134,
            "end_timestamp": 183,
            "description": "A person places a black bag on the counter and exits; another person handles the bag.",
            "severity_level": 1,
        },
        "entities": [
            {
                "entity_id": "E1",
                "type": "human",
                "description": "A person in a light shirt",
                "label": "person in light shirt",
                "timestamp_seconds": 134,
            },
            {
                "entity_id": "E2",
                "type": "human",
                "description": "A second person seen from behind",
                "label": "second person",
                "timestamp_seconds": 141,
            },
        ],
        "instruments": [
            {
                "instrument_id": "I1",
                "entity_id": "E1",
                "name": "cash register",
                "label": "black bag",
                "description": "A black cash register with a digital display",
                "threat_level": 1,
                "timestamp_seconds": 134,
            },
        ],
        "assets": [
            {
                "asset_id": "A1",
                "name": "cash register",
                "label": "cash register",
                "description": "Till on the counter",
                "timestamp_seconds": 142,
            },
            {
                "asset_id": "A2",
                "name": "black bag",
                "label": "black bag",
                "description": "A black bag on the counter",
                "timestamp_seconds": 142,
            },
        ],
        "timeline": [
            {"start_seconds": 134, "end_seconds": 140, "description": "E1 places a black bag on the counter."},
            {"start_seconds": 141, "end_seconds": 165, "description": "E2 enters and interacts with the bag."},
        ],
    }
    return {"id": revision_id, "video_id": VIDEO, "payload": payload, "evidence": []}


def _ask_burglary(question: str) -> dict[str, Any]:
    store = FakeRevisionStore(revisions={8: _burglary_revision()})
    resp = _make_client(FakeSearchClient(), store).post(
        "/api/v1/incidents/chat",
        json={
            "messages": [{"role": "user", "content": question}],
            "version_id": 8,
            "context": {"scope": "incident", "incident_id": VIDEO},
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["message"]


def test_nothing_recorded_as_taken_is_said_plainly_and_the_harm_part_still_answered():
    content = _ask_burglary("What was taken, or what could cause harm?")["content"]
    assert content.startswith(
        "The report doesn't say anything was taken. The property it records is the cash register and the black bag."
    ), content
    assert "one object that could be used to cause harm: a cash register" in content
    assert "(threat level 1 of 5)" in content and "Threat levels run from 1 (lowest) to 5 (highest)." in content
    assert not ID_TOKEN.search(content) and "relevant item(s)" not in content


def test_a_question_about_taking_a_named_thing_says_what_the_report_records_about_it():
    message = _ask_burglary("Did anyone steal the bag?")
    assert message["content"].startswith(
        "The report doesn't say the black bag was taken. What it records about the black bag, in order: From 2:14 to "
        "2:20, the person in light shirt places a black bag on the counter."
    ), message["content"]
    assert [c["item_id"] for c in message["citations"]] == ["A2"]


def test_a_people_question_describes_the_people():
    message = _ask_version("Who is involved?")
    assert message["content"].startswith("Three people are involved: a man in a black hoodie")
    assert [c["item_id"] for c in message["citations"]] == ["E1", "E2", "E3"]


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What is the weather like?", 'This report doesn\'t mention "weather".'),
        ("Is there a knife?", 'This report doesn\'t mention "knife".'),
    ],
)
def test_a_question_the_report_cannot_answer_says_so_and_cites_nothing(question, expected):
    message = _ask_version(question)
    assert message["content"].startswith(expected) and message["citations"] == []


@pytest.mark.parametrize(
    "question",
    [
        "What happened, in order?",
        "Who is involved?",
        "What could cause harm here?",
        "When did it happen?",
        "How serious is it?",
        "What kind of incident is this?",
        "What happened to the cash register?",
        "Who held the cash register?",
        "When is the cash register seen?",
        "Was anyone carrying a gun?",
        "Describe the incident",
        "What is the weather like?",
        "What was taken, or what could cause harm?",
        "Was anything stolen?",
        "Which weapons were used?",
    ],
)
def test_no_answer_writes_an_internal_id_unless_asked(question):
    message = _ask_version(question)
    assert not ID_TOKEN.search(message["content"]), message["content"]
    assert VIDEO not in message["content"]
    for citation in message["citations"]:
        assert citation["incident_id"] == VIDEO and ID_TOKEN.fullmatch(citation["item_id"])


def test_ids_are_written_only_when_the_question_asks_for_them():
    message = _ask_version("What are the item ids of the people?")
    assert "E1 is the man in black hoodie" in message["content"]


def test_the_default_client_never_serves_demo_hits_for_a_library_question():
    resp = _make_client().post(
        "/api/v1/incidents/chat",
        json={"messages": [{"role": "user", "content": "What weapon was brandished in the altercation?"}]},
    )
    message = resp.json()["message"]
    assert message["content"] == NOT_SEARCHED["library"] and message["citations"] == []
    assert "Assault007" not in resp.text and "machete" not in resp.text


def test_the_official_version_keeps_the_decision_and_says_nothing_was_searched():
    search = FakeSearchClient()  # an enabled client: the official version is searched (decision 2026-10-24)
    message = _ask_version("What happened, in order?", official=True, search=search)
    assert search.last_filters == {"incident_id": VIDEO}
    assert message["content"] == (
        "No matching incidents or evidence items were found for 'What happened, in order?' in this incident."
    )
    store = FakeRevisionStore(revisions={7: _robbery_revision()}, official={VIDEO: 7})
    resp = _make_client(revision_store=store).post(
        "/api/v1/incidents/chat",
        json={"messages": [{"role": "user", "content": "What happened?"}], "version_id": 7},
    )
    assert resp.json()["message"] == {"role": "assistant", "content": NOT_SEARCHED["official"], "citations": []}


def test_search_hits_read_as_prose_by_incident_with_ids_only_in_citations():
    hits = [
        {
            "item": {
                "item_type": "instrument",
                "item_id": "I1",
                "name": "crowbar",
                "timestamp_seconds": 8,
                "crop_image_url": "https://r2.example/a.jpg",
            },
            "incident": {
                "incident_id": "va11111111111111111a",
                "incident_type": "burglary",
                "title": "Shop door forced",
            },
        },
        {
            "item": {
                "item_type": "entity",
                "item_id": "E2",
                "name": "person",
                "label": "man in a hood",
                "timestamp_seconds": 61,
                "crop_image_url": "https://r2.example/b.jpg",
            },
            "incident": {"incident_id": "vb22222222222222222b", "incident_type": "assault"},
        },
    ]
    resp = _make_client(FakeSearchClient(hits=hits)).post(
        "/api/v1/incidents/chat", json={"messages": [{"role": "user", "content": "Any crowbars or hoods?"}]}
    )
    message = resp.json()["message"]
    assert message["content"] == (
        'I found matching evidence in two incidents. In a burglary ("Shop door forced"): a crowbar (seen at 0:08). '
        "In an assault: a man in a hood (seen at 1:01)."
    )
    assert not ID_TOKEN.search(message["content"]) and "va111" not in message["content"]
    assert [(c["incident_id"], c["item_id"]) for c in message["citations"]] == [
        ("va11111111111111111a", "I1"),
        ("vb22222222222222222b", "E2"),
    ]


def test_a_console_draft_keeps_the_base_timeline_and_title():
    """The console's draftReport carries only the edited fields; the agent overlays it on the base version."""
    draft = {
        "incident": {
            "type": "robbery",
            "start_timestamp": 130,
            "end_timestamp": 180,
            "description": "The man in black takes cash from the till.",
        },
        "entities": [
            {
                "entity_id": "E1",
                "type": "human",
                "description": "Man in a black hoodie and grey jeans",
                "label": "man in black hoodie",
                "timestamp_seconds": 134,
            },
            {
                "entity_id": "new-E4",
                "type": "human",
                "description": "Security guard",
                "label": "security guard",
                "timestamp_seconds": 175,
            },
        ],
        "instruments": [
            {
                "instrument_id": "I1",
                "entity_id": "E1",
                "name": "cash register",
                "description": "Till drawer",
                "label": "cash register",
                "timestamp_seconds": 134,
            }
        ],
        "assets": [],
    }
    store = FakeRevisionStore(revisions={7: _robbery_revision()})
    resp = _make_client(FakeSearchClient(), store).post(
        "/api/v1/incidents/chat",
        json={
            "messages": [{"role": "user", "content": "What happened, in order?"}],
            "version_id": 7,
            "draft_report": draft,
        },
    )
    content = resp.json()["message"]["content"]
    assert content.startswith("The report describes a robbery from 2:10 to 3:00. In order:"), content
    assert "confronts a person" in content, "E2 was removed in the draft, so the base timeline names a person"
    assert not ID_TOKEN.search(content) and "new-E" not in content
