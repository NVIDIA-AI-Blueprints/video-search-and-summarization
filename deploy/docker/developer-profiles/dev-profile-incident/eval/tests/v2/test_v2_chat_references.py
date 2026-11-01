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

"""Chat references: every countable answer equals a code recount, re-derivation is blind, leakage is reported."""

from __future__ import annotations

import copy

import pytest

from evalv2.datasets import generation_prompts as gp
from evalv2.datasets.records import validate_record
from evalv2.references import chat


def _incident(kind, severity, start, end, description, entities=(), instruments=(), assets=()):
    return {"p1_shaped": {
        "incident": {"type": kind, "severity_level": severity, "start_timestamp": start, "end_timestamp": end,
                     "description": description},
        "entities": [{"entity_id": e, "type": t, "description": d} for e, t, d in entities],
        "instruments": [{"instrument_id": i, "entity_id": h, "name": n, "description": n, "threat_level": 3}
                        for i, h, n in instruments],
        "assets": [{"asset_id": a, "name": n, "description": n} for a, n in assets]}}


GT = {"by_incident_id": {
    "Assault001": _incident("assault", 4, 2, 9, "A man in red hits a man in blue with a wooden stick near a door.",
                            entities=[("E1", "human", "man in red"), ("E2", "human", "man in blue"),
                                      ("E3", "animal", "dog")],
                            instruments=[("I1", "E1", "Wooden stick")]),
    "Assault002": _incident("assault", 2, 0, 5, "Two people push each other outside a bar.",
                            entities=[("E1", "human", "woman"), ("E2", "human", "man")]),
    "Burglary003": _incident("burglary", 3, 1, 20, "A person forces a shop door open with a crowbar.",
                             entities=[("E1", "human", "person in hoodie")], instruments=[("I1", "E1", "crowbar")],
                             assets=[("A1", "shop door")]),
}}
GEN = {"prompt": "gen-chat-wording-v1", "model": "claude"}
VAL = {"id": "gen-chat-rederive-v1"}


@pytest.mark.parametrize(("question_class", "params", "expected"), [
    ("incident.type", {"incident_id": "Assault001"}, {"kind": "value", "value": "assault"}),
    ("incident.severity", {"incident_id": "Burglary003"}, {"kind": "value", "value": 3}),
    ("incident.window", {"incident_id": "Assault001"}, {"kind": "window", "value": [2, 9]}),
    ("incident.people_count", {"incident_id": "Assault001"}, {"kind": "count", "value": 2}),
    ("incident.instrument_list", {"incident_id": "Assault001"}, {"kind": "list", "value": ["wooden stick"]}),
    ("incident.instrument_holder", {"incident_id": "Burglary003", "instrument_id": "I1"},
     {"kind": "value", "value": "E1"}),
    ("incident.asset_list", {"incident_id": "Burglary003"}, {"kind": "list", "value": ["shop door"]}),
    ("incident.unanswerable", {"incident_id": "Assault002"}, {"kind": "refusal", "value": None}),
    ("library.type_count", {"type": "assault"}, {"kind": "count", "value": 2}),
    ("library.severity_at_least_count", {"level": 3}, {"kind": "count", "value": 2}),
    ("library.instrument_incidents", {"instrument": "crowbar"}, {"kind": "list", "value": ["Burglary003"]}),
    ("library.most_common_type", {}, {"kind": "list", "value": ["assault"]}),
    ("library.retrieval", {"targets": ["Burglary003"]}, {"kind": "list", "value": ["Burglary003"]}),
])
def test_countable_answers_are_computed_from_the_ground_truth(question_class, params, expected):
    assert chat.answer(question_class, GT, params) == expected


def test_written_facts_are_not_computed_and_unknown_classes_are_refused():
    with pytest.raises(chat.ChatReferenceError, match="written facts"):
        chat.answer("incident.facts", GT, {"incident_id": "Assault001"})
    with pytest.raises(chat.ChatReferenceError, match="unknown"):
        chat.answer("incident.weather", GT, {"incident_id": "Assault001"})


def test_every_countable_reference_equals_a_code_recount():
    record = chat.build_record("library.type_count", {"type": "assault"}, GT, question="How many fights are filed?",
                               paraphrases=["Count the fight reports.", "What number of fight cases exist?"],
                               dataset="chat-references-v0", generator=GEN)
    assert chat.recount(record, GT) == []
    tampered = copy.deepcopy(record)
    tampered["payload"]["answer"]["value"] = 3
    assert chat.recount(tampered, GT) and "stored" in chat.recount(tampered, GT)[0]
    record_ids = {chat.build_record("library.type_count", {"type": "assault"}, GT,
                                    question="How many fights are filed?", paraphrases=["a", "b"], dataset="d",
                                    generator=GEN)["record_id"] for _ in range(3)}
    assert len(record_ids) == 1, "record ids are stable across runs"


def test_an_agreeing_blind_rederivation_validates_a_record():
    record = chat.build_record("incident.people_count", {"incident_id": "Assault001"}, GT,
                               question="How many people take part?",
                               paraphrases=["How many humans are involved?", "Count the people in the clip."],
                               dataset="chat-references-v0", generator=GEN,
                               rederived={"kind": "count", "value": 2}, validator=VAL)
    assert record["tier"] == "ai_validated" and validate_record(record) == []
    disagreeing = chat.build_record("incident.people_count", {"incident_id": "Assault001"}, GT,
                                    question="How many people take part?",
                                    paraphrases=["How many humans are involved?", "Count the people in the clip."],
                                    dataset="chat-references-v0", generator=GEN,
                                    rederived={"kind": "count", "value": 3}, validator=VAL)
    assert disagreeing["tier"] == "ai_proposed" and "validators_disagree" in disagreeing["flags"]


def test_facts_are_written_then_rederived_and_compared_by_content_words():
    facts = ["man in red hits man in blue", "a wooden stick is used"]
    agreeing = chat.build_record("incident.facts", {"incident_id": "Assault001"}, GT, question="What happened here?",
                                 paraphrases=["Describe the incident.", "What went on in the clip?"],
                                 dataset="d", generator=GEN, facts=facts,
                                 rederived={"kind": "facts", "facts": ["The man in red hits the man in blue.",
                                                                       "A wooden stick is used as a weapon."]},
                                 validator=VAL)
    assert agreeing["tier"] == "ai_validated"
    assert not chat.facts_agree(facts, ["A dog barks."])
    with pytest.raises(chat.ChatReferenceError, match="written facts"):
        chat.build_record("incident.facts", {"incident_id": "Assault001"}, GT, question="q", paraphrases=[],
                          dataset="d", generator=GEN)


def test_a_question_that_states_its_answer_fails_its_checks_and_leakage_is_reported():
    leaky = chat.build_record("incident.instrument_list", {"incident_id": "Assault001"}, GT,
                              question="Was a wooden stick used as the weapon?",
                              paraphrases=["Which weapon appears?", "What was used to hit?"], dataset="d",
                              generator=GEN, rederived={"kind": "list", "value": ["wooden stick"]}, validator=VAL)
    assert leaky["payload"]["leakage"]["answer_in_question"] == ["wooden stick"]
    assert leaky["tier"] == "ai_proposed" and "check_failed" in leaky["flags"]
    clean = chat.build_record("incident.type", {"incident_id": "Burglary003"}, GT,
                              question="What kind of incident is it?",
                              paraphrases=["Which category fits this clip?", "How would you classify the event?"],
                              dataset="d", generator=GEN)
    assert clean["payload"]["leakage"]["answer_in_question"] == []
    assert 0 <= clean["payload"]["leakage"]["description_overlap"] < 0.2


def test_the_validator_packet_is_blind():
    record = chat.build_record("incident.people_count", {"incident_id": "Assault001"}, GT,
                               question="How many people take part?", paraphrases=["p1", "p2"], dataset="d",
                               generator=GEN)
    packet = chat.validator_packet(record, GT, prompt_id="gen-chat-rederive-v1")
    chat.assert_blind(packet, record)
    assert set(packet) == {"role", "prompt", "record_id", "class", "question", "context"}
    with pytest.raises(chat.ChatReferenceError, match="holds"):
        chat.assert_blind({**packet, "answer": {"kind": "count", "value": 2}}, record)
    with pytest.raises(chat.ChatReferenceError, match="another question"):
        chat.assert_blind({**packet, "question": "How many dogs?"}, record)
    generator = chat.generator_packet("incident.people_count", {"incident_id": "Assault001"}, GT,
                                      prompt_id="gen-chat-wording-v1")
    assert generator["computed_answer"] == {"kind": "count", "value": 2}


def test_the_oracle_corpus_is_the_ground_truths_own_reports():
    corpus = chat.oracle_corpus(GT)
    assert set(corpus) == {"Assault001", "Assault002", "Burglary003"}
    assert corpus["Assault001"]["incident"]["type"] == "assault"


def test_the_chat_prompts_are_registered():
    for prompt_id, role in (("gen-chat-wording-v1", "generator"), ("gen-chat-rederive-v1", "validator")):
        entry = next(p for p in gp.load()["prompts"] if p["id"] == prompt_id)
        assert entry["role"] == role and entry["purpose"] == "chat_reference"
    assert "Never state the answer" in gp.prompt_text("gen-chat-wording-v1")
