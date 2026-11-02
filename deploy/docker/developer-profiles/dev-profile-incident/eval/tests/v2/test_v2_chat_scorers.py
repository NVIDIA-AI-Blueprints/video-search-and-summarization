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

"""Chat scorers: retrieval, exact answers, citations, refusals and judged checks, replayable offline."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from evalv2.core.netguard import no_network
from evalv2.datasets import records as rec
from evalv2.references import chat as refs
from evalv2.tasks.chat import scoring as sc
from evalv2.tasks.judging import CachedJudge, JudgeCache, JudgeVerdict, RecordingJudge

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v2_chat_references import GT  # noqa: E402

GEN = {"prompt": "gen-chat-wording-v1", "model": "claude-opus"}


def _record(question_class, params, **kwargs):
    return refs.build_record(question_class, params, GT, question=f"Q {question_class} {sorted(params.items())}",
                             paraphrases=[f"P1 {question_class}", f"P2 {question_class}"], dataset="chat-v0",
                             generator=GEN, **kwargs)


def test_the_judge_may_not_share_a_family_with_a_subject_or_the_generator():
    sc.check_judge_family("openai/gpt-oss-120b", subjects=["nvidia/nemotron-3-ultra"], generators=["claude-opus"])
    with pytest.raises(sc.JudgeFamilyError, match="nemotron"):
        sc.check_judge_family("nvidia/nemotron-3-nano-30b-a3b", subjects=["nvidia/nemotron-3-ultra"], generators=[])
    with pytest.raises(sc.JudgeFamilyError, match="claude"):
        sc.check_judge_family("anthropic/claude-sonnet", subjects=[], generators=["claude-opus"])
    assert sc.model_family("nvidia/cosmos-3-super-reasoner") == "cosmos"


@pytest.mark.parametrize(("question_class", "params", "text", "expected"), [
    ("incident.people_count", {"incident_id": "Assault001"}, "Two people are involved.", True),
    ("incident.people_count", {"incident_id": "Assault001"}, "3 people were there.", False),
    ("incident.type", {"incident_id": "Burglary003"}, "It is a burglary at a shop.", True),
    ("incident.instrument_list", {"incident_id": "Assault001"}, "He used a wooden stick.", True),
    ("incident.instrument_list", {"incident_id": "Assault001"}, "He used a stick.", False),
    ("incident.window", {"incident_id": "Assault001"}, "It runs from second 2 to second 9.", True),
])
def test_exact_answers_for_computed_references(question_class, params, text, expected):
    record = _record(question_class, params)
    assert sc.exact(sc.ChatAnswer(text=text), record["payload"]["answer"]) is expected


def test_refusals_are_expected_only_where_the_report_cannot_answer():
    unanswerable = _record("incident.unanswerable", {"incident_id": "Assault002"})
    assert sc.exact(sc.ChatAnswer(text="The report does not say.", refused=True), unanswerable["payload"]["answer"])
    answerable = _record("incident.type", {"incident_id": "Assault002"})
    assert sc.exact(sc.ChatAnswer(text="I cannot tell.", refused=True), answerable["payload"]["answer"]) is False


def test_retrieval_uses_v1s_list_metrics_plus_ndcg():
    record = _record("library.retrieval", {"targets": ["Burglary003"]})
    answer = sc.ChatAnswer(text="Burglary003 matches.", retrieved=("Assault001", "Burglary003", "Assault002"),
                           citations=("Burglary003",))
    result = sc.score_answer(record, answer, corpus="oracle", ground_truth_text="")["retrieval"]
    assert result["@1"]["recall"] == 0.0 and result["@3"]["recall"] == 1.0
    assert result["@3"]["mrr"] == 0.5 and result["@3"]["ndcg"] == pytest.approx(1 / 1.5849625, rel=1e-6)
    assert sc.ndcg([], ["x"], 5) == 0.0 and sc.ndcg(["x"], [], 5) is None


def test_citations_score_against_the_questions_incident():
    record = _record("incident.type", {"incident_id": "Assault001"})
    scored = sc.score_answer(record, sc.ChatAnswer(text="assault", citations=("Assault001", "Assault002")),
                             corpus="system", ground_truth_text="")
    assert scored["citations"] == {"precision": 0.5, "recall": 1.0}


def test_judged_checks_are_cached_and_replay_offline(tmp_path):
    facts = ["man in red hits man in blue", "a wooden stick is used"]
    record = _record("incident.facts", {"incident_id": "Assault001"}, facts=facts)
    answer = sc.ChatAnswer(text="The man in red hits the man in blue.", context="Assault001: a fight near a door.")

    def live(expected, predicted):
        words = set(re.findall(r"[a-z]+", (predicted or "").lower()))
        return JudgeVerdict(1.0 if (expected or "x").split()[-1].lower() in words else 0.0)

    identities = {kind: sc.judge_identity(kind, "openai/gpt-oss-120b", {"temperature": 0})
                  for kind in ("fact", "unsupported", "contradiction")}
    judges = {kind: RecordingJudge(JudgeCache(tmp_path / f"{kind}.jsonl", identity), live)
              for kind, identity in identities.items()}
    first = sc.score_answer(record, answer, corpus="oracle", ground_truth_text="ground truth", judges=judges)
    assert first["facts"] == {"stated": 1, "scored": 2, "total": 2, "recall": 0.5}
    offline = {kind: CachedJudge(JudgeCache(tmp_path / f"{kind}.jsonl", identity))
               for kind, identity in identities.items()}
    with no_network():
        second = sc.score_answer(record, answer, corpus="oracle", ground_truth_text="ground truth", judges=offline)
    assert second == first and all(not j.misses for j in offline.values())


def test_without_a_judge_the_judged_checks_are_unscored_not_failed():
    record = _record("incident.facts", {"incident_id": "Assault001"}, facts=["a fact"])
    scored = sc.score_answer(record, sc.ChatAnswer(text="anything"), corpus="oracle", ground_truth_text="gt")
    assert scored["facts"]["recall"] is None and scored["contradiction"] is None


def test_a_rejected_reference_is_counted_not_scored_and_an_edit_replaces_it():
    record = _record("incident.people_count", {"incident_id": "Assault001"})
    rejected = rec.apply_human_decision(record, rec.HumanDecision(
        record["record_id"], "reject", "Reviewer One", "2026-11-02T09:00:00+00:00", rec.payload_sha256(record),
        "f" * 64))
    assert sc.score_answer(rejected, sc.ChatAnswer(text="two"), corpus="oracle",
                           ground_truth_text="")["status"] == "reference_rejected"
    edited_payload = {**record["payload"], "answer": {"kind": "count", "value": 3}}
    edited = rec.apply_human_decision(record, rec.HumanDecision(
        record["record_id"], "edit", "Reviewer One", "2026-11-02T09:00:00+00:00", rec.payload_sha256(record),
        "f" * 64, edited_payload=edited_payload))
    assert sc.score_answer(edited, sc.ChatAnswer(text="three"), corpus="oracle", ground_truth_text="")["exact"]


def test_aggregates_keep_corpora_and_tiers_apart():
    scores = [
        sc.score_answer(_record("incident.type", {"incident_id": "Assault001"}), sc.ChatAnswer(text="an assault"),
                        corpus="oracle", ground_truth_text=""),
        sc.score_answer(_record("incident.type", {"incident_id": "Burglary003"}), sc.ChatAnswer(text="an assault"),
                        corpus="system", ground_truth_text=""),
        sc.score_answer(_record("incident.unanswerable", {"incident_id": "Assault002"}),
                        sc.ChatAnswer(text="not recorded", refused=True), corpus="oracle", ground_truth_text=""),
    ]
    summary = sc.aggregate(scores)
    assert summary["oracle"]["ai_proposed"]["exact_rate"] == 1.0 and summary["oracle"]["ai_proposed"]["answers"] == 2
    assert summary["system"]["ai_proposed"]["exact_rate"] == 0.0, "the two corpora are never pooled"
    assert summary["oracle"]["ai_proposed"]["refusal_precision"] == 1.0
    assert summary["oracle"]["ai_proposed"]["refusal_recall"] == 1.0
    assert "primary" not in summary["oracle"], "no human_verified reference yet, so no primary figure"
