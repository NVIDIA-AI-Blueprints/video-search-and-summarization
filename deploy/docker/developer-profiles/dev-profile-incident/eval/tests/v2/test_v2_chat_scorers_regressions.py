# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regressions from the Evaluation V2 review of the chat scorers: exact means exact, one normaliser, citations, holders."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from evalv2.references import chat as refs
from evalv2.tasks.chat import scoring as sc
from evalv2.tasks.judging import JudgeVerdict

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v2_chat_references import GEN, _incident  # noqa: E402
from test_v2_chat_references_regressions import GT as BASE_GT  # noqa: E402

GT = {"by_incident_id": {
    **BASE_GT["by_incident_id"],
    "Assault002": _incident("assault", 2, 0, 5, "Two people push each other outside a bar.",
                            entities=[("E1", "human", "woman"), ("E2", "human", "man")]),
    "Assault007": _incident("assault", 5, 0, 9, "A man throws a bottle of water at a woman.",
                            entities=[("E1", "human", "man in white"), ("E2", "human", "woman")],
                            instruments=[("I1", "E1", "Bottle of water")]),
}}


def _record(question_class, params, **kwargs):
    return refs.build_record(question_class, params, GT, question=f"Q {question_class} {sorted(params.items())}",
                             paraphrases=[f"P1 {question_class}", f"P2 {question_class}"], dataset="chat-v0",
                             generator=GEN, **kwargs)


def _exact(question_class, params, text, **answer):
    record = _record(question_class, params)
    return sc.score_answer(record, sc.ChatAnswer(text=text, **answer), corpus="oracle", ground_truth_text="")["exact"]


# --- B1: a list answer is the set it asserts; a value is the one it asserts ---------------------------------------


@pytest.mark.parametrize(("text", "expected"), [
    ("A knife and a gun were used.", False),        # the review's case: no instrument in the ground truth
    ("He pushed her.", False),                      # an empty list needs an explicit none
    ("No instruments were used.", True),
    ("None.", True),
    ("There wasn't any weapon.", True),
    ("No weapon, though a baseball bat was nearby.", False),  # names a vocabulary item it should not
])
def test_an_empty_expected_list_needs_an_explicit_none_and_no_item(text, expected):
    assert _exact("incident.instrument_list", {"incident_id": "Assault002"}, text) is expected


@pytest.mark.parametrize(("text", "expected"), [
    ("He used a wooden stick.", True),
    ("A wooden stick and a crowbar.", False),       # a superset is not exact
    ("A wooden stick, a combat knife and a baton.", False),
    ("No knife; he used a wooden stick.", True),    # a negated mention asserts nothing
    ("Not a wooden stick, a crowbar.", False),
    ("A stick.", False),                            # the expected item is "wooden stick"
])
def test_a_list_answer_must_assert_exactly_the_expected_items(text, expected):
    assert _exact("incident.instrument_list", {"incident_id": "Assault001"}, text) is expected


@pytest.mark.parametrize(("text", "expected"), [
    ("Severity 2 on a 1-5 scale.", False),          # the review's case: the reference is 5
    ("Severity 2 on a 1–5 scale.", False),
    ("It is a 5 out of 5.", True),
    ("The severity is 5.", True),
    ("A severity of five.", True),
    ("Severity: 5/5", True),
    ("Severity 4, on a scale of 5.", False),
])
def test_a_severity_answer_is_the_level_it_states(text, expected):
    assert _exact("incident.severity", {"incident_id": "Assault007"}, text) is expected


@pytest.mark.parametrize(("text", "expected"), [
    ("It is a burglary at a shop.", True),
    ("This is a burglary, not an assault.", True),
    ("Either an assault or a burglary.", False),    # two types asserted
    ("An assault.", False),
])
def test_a_type_answer_is_the_one_type_it_names(text, expected):
    assert _exact("incident.type", {"incident_id": "Burglary003"}, text) is expected


def test_a_library_list_of_incidents_fails_with_an_extra_incident():
    params = {"instrument": "crowbar"}
    assert _exact("library.instrument_incidents", params, "Only Burglary003.") is True
    assert _exact("library.instrument_incidents", params, "Burglary003 and Assault001.") is False


# --- B2: the scorer reads answers with the references' own normaliser ---------------------------------------------


def test_a_stop_word_inside_an_item_name_no_longer_misses_it():
    record = _record("incident.instrument_list", {"incident_id": "Assault007"})
    assert record["payload"]["answer"]["value"] == ["bottle water"], "the reference drops stop words"
    assert _exact("incident.instrument_list", {"incident_id": "Assault007"}, "He threw a bottle of water.") is True


# --- B3: library citations score against the incidents the answer rests on ----------------------------------------


def test_a_correct_count_citing_the_counted_incidents_has_full_precision():
    record = _record("library.type_count", {"type": "assault"})
    assaults = sorted(i for i, entry in GT["by_incident_id"].items() if entry["p1_shaped"]["incident"]["type"] == "assault")
    answer = sc.ChatAnswer(text=f"There are {len(assaults)} assaults.", citations=tuple(assaults))
    scored = sc.score_answer(record, answer, corpus="oracle", ground_truth_text="")
    assert scored["exact"] is True and scored["citations"] == {"precision": 1.0, "recall": 1.0}
    stray = sc.score_answer(record, sc.ChatAnswer(text="6", citations=(assaults[0], "Burglary003")),
                            corpus="oracle", ground_truth_text="")
    assert stray["citations"]["precision"] == 0.5


def test_every_library_class_names_the_incidents_it_rests_on():
    assert refs.support("library.severity_at_least_count", GT, {"level": 5}) == ["Assault005", "Assault007"]
    assert refs.support("library.instrument_incidents", GT, {"instrument": "bat"}) == ["Assault006"]
    assert refs.support("library.most_common_type", GT, {}) == sorted(
        i for i in GT["by_incident_id"] if i.startswith("Assault"))
    assert refs.support("library.retrieval", GT, {"targets": ["Burglary003"]}) == ["Burglary003"]
    assert refs.support("incident.type", GT, {"incident_id": "Assault001"}) == ["Assault001"]


def test_a_tampered_support_fails_the_recount():
    record = _record("library.type_count", {"type": "assault"})
    record["payload"]["support"] = ["Burglary003"]
    assert any("support" in problem for problem in refs.recount(record, GT))


# --- B7: a holder answer is judged against the ground truth's description -----------------------------------------


class _DescriptionJudge:
    """Stands in for the fact judge: does PREDICTED describe the expected holder (its first two content words)?"""

    def __init__(self):
        self.calls = []

    def __call__(self, expected, predicted):
        self.calls.append(expected)
        holder = refs.words(expected.split(" held the ")[0])
        return JudgeVerdict(1.0 if refs.has_phrase(refs.words(predicted), holder) else 0.0)


def _holder(text, *, refused=False, judges=None):
    record = _record("incident.instrument_holder", {"incident_id": "Assault001", "instrument_id": "I1"})
    return sc.score_answer(record, sc.ChatAnswer(text=text, refused=refused), corpus="system", ground_truth_text="",
                           judges=judges)


def test_a_system_corpus_answer_naming_its_own_id_is_judged_on_the_person():
    judge = _DescriptionJudge()
    right = _holder("The man in red (E2) was holding it.", judges={"fact": judge})
    assert right["holder_identified"] is True and right["exact"] is None
    assert judge.calls == ["man in red held the Wooden stick"]
    wrong = _holder("E1 held the stick.", judges={"fact": judge})
    assert wrong["holder_identified"] is False, "a bare ground-truth id no longer passes"


def test_holder_answers_without_a_judge_are_unscored_and_refusals_are_wrong():
    assert _holder("The man in red.")["holder_identified"] is None
    refused = _holder("I cannot tell.", refused=True)
    assert refused["holder_identified"] is False and refused["exact"] is None, "a judged kind is never in exact_rate"
    summary = sc.aggregate([_holder("The man in red.", judges={"fact": _DescriptionJudge()})])
    assert summary["system"]["ai_proposed"]["holder_identified_rate"] == 1.0
    assert summary["system"]["ai_proposed"]["by_class"]["incident.instrument_holder"]["holder_identified_rate"] == 1.0
