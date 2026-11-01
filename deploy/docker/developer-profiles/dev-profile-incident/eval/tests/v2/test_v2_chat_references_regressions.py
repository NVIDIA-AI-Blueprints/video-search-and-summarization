# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regressions from the Evaluation V2 review of chat references: library packets, holders, whole-word instruments."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from evalv2.references import chat

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v2_chat_references import GEN, VAL, _incident  # noqa: E402

GT = {"by_incident_id": {
    "Assault001": _incident("assault", 4, 2, 9, "A man in red hits a man in blue with a wooden stick near a door.",
                            entities=[("E1", "human", "man in red"), ("E2", "human", "man in blue")],
                            instruments=[("I1", "E1", "Wooden stick")]),
    "Assault004": _incident("assault", 3, 0, 8, "A guard swings a baton at a man.",
                            entities=[("E1", "human", "guard in uniform"), ("E2", "human", "man")],
                            instruments=[("I1", "E1", "baton")]),
    "Assault005": _incident("assault", 5, 1, 6, "A man threatens another with a combat knife.",
                            entities=[("E1", "human", "man in black")], instruments=[("I1", "E1", "combat knife")]),
    "Assault006": _incident("assault", 4, 3, 12, "A man hits a car with a baseball bat.",
                            entities=[("E1", "human", "man in grey")], instruments=[("I1", "E1", "baseball bat")],
                            assets=[("A1", "car")]),
    "Burglary003": _incident("burglary", 3, 1, 20, "A person forces a shop door open with a crowbar.",
                             entities=[("E1", "human", "person in hoodie")], instruments=[("I1", "E1", "crowbar")],
                             assets=[("A1", "shop door")]),
}}


# --- B10: instrument names match whole words, never substrings ----------------------------------------------------


def test_bat_matches_a_baseball_bat_but_not_a_baton_or_a_combat_knife():
    assert chat.answer("library.instrument_incidents", GT, {"instrument": "bat"}) == {
        "kind": "list", "value": ["Assault006"]}
    assert chat.answer("library.instrument_incidents", GT, {"instrument": "knife"})["value"] == ["Assault005"]
    assert chat.answer("library.instrument_incidents", GT, {"instrument": "Baseball bat"})["value"] == ["Assault006"]
    assert chat.answer("library.instrument_incidents", GT, {"instrument": "stick"})["value"] == ["Assault001"]
    assert chat.answer("library.instrument_incidents", GT, {"instrument": ""})["value"] == []


# --- B7: an instrument's holder is the holder's ground-truth description ------------------------------------------


def test_the_holder_answer_is_the_ground_truth_description_not_a_local_id():
    expected = chat.answer("incident.instrument_holder", GT, {"incident_id": "Assault001", "instrument_id": "I1"})
    assert expected == {"kind": "holder", "value": "man in red", "entity_id": "E1", "instrument": "Wooden stick"}
    assert expected["value"] != "E1", "a system corpus numbers its people independently, so E1 names nobody"


def _holder_record(rederived):
    return chat.build_record("incident.instrument_holder", {"incident_id": "Assault001", "instrument_id": "I1"}, GT,
                             question="Who was holding the stick?",
                             paraphrases=["Whose hand held the stick?", "Which person carried the stick?"],
                             dataset="chat-v0", generator=GEN, rederived=rederived, validator=VAL)


def test_a_holder_is_validated_by_id_or_by_agreeing_description():
    assert _holder_record({"kind": "value", "value": "E1"})["tier"] == "ai_validated"
    assert _holder_record({"kind": "value", "value": "The man in red"})["tier"] == "ai_validated"
    wrong = _holder_record({"kind": "value", "value": "the man in blue"})
    assert wrong["tier"] == "ai_proposed" and "validators_disagree" in wrong["flags"]
    assert _holder_record({"kind": "refusal", "value": None})["tier"] == "ai_proposed"


def test_a_holder_question_that_names_the_holder_leaks_its_answer():
    record = chat.build_record("incident.instrument_holder", {"incident_id": "Assault001", "instrument_id": "I1"},
                               GT, question="Did the man in red hold the stick?", paraphrases=["a", "b"],
                               dataset="chat-v0", generator=GEN)
    assert record["payload"]["leakage"]["answer_in_question"] == ["man in red"]


# --- B6: library packets carry what each pass needs ---------------------------------------------------------------


def test_the_retrieval_generator_sees_its_targets_report():
    packet = chat.generator_packet("library.retrieval", {"targets": ["Burglary003"]}, GT,
                                   prompt_id="gen-chat-wording-v1")
    assert packet["context"]["target_reports"] == {"Burglary003": GT["by_incident_id"]["Burglary003"]["p1_shaped"]}
    other = chat.generator_packet("library.type_count", {"type": "assault"}, GT, prompt_id="gen-chat-wording-v1")
    assert other["context"] == {"incidents": 5} and other["computed_answer"] == {"kind": "count", "value": 4}


def test_a_library_validator_sees_the_whole_library_and_stays_blind():
    record = chat.build_record("library.retrieval", {"targets": ["Burglary003"]}, GT,
                               question="Find the break-in where a shop door was forced.",
                               paraphrases=["Which clip shows a forced shop entry?", "Show me the shop break-in."],
                               dataset="chat-v0", generator=GEN)
    packet = chat.validator_packet(record, GT, prompt_id="gen-chat-rederive-v1")
    chat.assert_blind(packet, record)
    reports = packet["context"]["reports"]
    assert list(reports) == sorted(GT["by_incident_id"]), "every incident, in id order: nothing marks the target"
    assert set(packet) == {"role", "prompt", "record_id", "class", "question", "context"}
    assert "targets" not in json.dumps({k: v for k, v in packet.items() if k != "context"})


def test_a_library_count_can_be_recounted_from_the_validator_packet():
    record = chat.build_record("library.severity_at_least_count", {"level": 4}, GT,
                               question="How many incidents are rated four or above?", paraphrases=["a", "b"],
                               dataset="chat-v0", generator=GEN)
    reports = chat.validator_packet(record, GT, prompt_id="gen-chat-rederive-v1")["context"]["reports"]
    recount = sum(1 for r in reports.values() if r["incident"]["severity_level"] >= 4)
    assert recount == record["payload"]["answer"]["value"] == 3
