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

"""Chat references (methodology v2.0 §3, R3): question classes, code-computed answers, blind re-derivation, leakage.

Tooling only; generating questions is an approval-gated run (RA2). A chat reference is one question about one
incident or about the library, with its expected answer:

- **Countable answers come from code.** Every class except ``incident.facts`` has an :func:`answer` computed from
  the frozen ground truth (types, severities, windows, people, instruments and their holders, assets, library
  counts and filters). A generator (Claude in Claude Code) only words the question and its paraphrases; every
  countable reference must equal a code recount (:func:`recount`). An instrument's holder is the holder's
  ground-truth description, never its local id (a system corpus numbers people on its own); the scorer judges it.
- **Facts are written, then re-derived blind.** For ``incident.facts`` the generator writes atomic facts from the
  ground-truth description; a validator that sees the question and the incident but never the reference answers it
  again (:func:`validator_packet`, :func:`assert_blind`), and the record is ``ai_validated`` only when they agree.
  A library question's validator sees every report of the oracle corpus, so it can recount or find the targets;
  the generator of a retrieval question sees the target's report, so it can describe it.
- **Leakage is measured, not hidden.** Questions derived from the ground truth can carry its words
  (:func:`leakage`): the report lists, per question, the content-word overlap with the target's description and
  whether the answer appears in the question; a question that states its answer fails its checks.
- **Corpora stay apart.** The oracle corpus is the ground truth's own reports (:func:`oracle_corpus`); the system
  corpus is a frozen analysis run's. Results on the two are never pooled.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from evalv2.core.hashing import sha256_json
from evalv2.datasets.records import new_record, promote_validated

CLASSES: dict[str, str] = {
    "incident.type": "incident",
    "incident.severity": "incident",
    "incident.window": "incident",
    "incident.people_count": "incident",
    "incident.instrument_list": "incident",
    "incident.instrument_holder": "incident",
    "incident.asset_list": "incident",
    "incident.facts": "incident",
    "incident.unanswerable": "incident",
    "incident.false_premise": "incident",
    "library.type_count": "library",
    "library.severity_at_least_count": "library",
    "library.instrument_incidents": "library",
    "library.most_common_type": "library",
    "library.retrieval": "library",
}
REFUSAL_CLASSES = frozenset({"incident.unanswerable", "incident.false_premise"})
WRITTEN_CLASSES = frozenset({"incident.facts"})
FACT_AGREEMENT = 0.6
_STOP = frozenset({
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "at", "by", "for", "with", "from", "is", "are", "was",
    "were", "be", "been", "it", "its", "this", "that", "these", "those", "as", "into", "near", "over", "under",
    "who", "what", "when", "where", "which", "how", "many", "much", "did", "does", "do"
})


class ChatReferenceError(ValueError):
    """An unknown class, missing parameters, a non-blind validator packet, or a malformed answer."""


def words(text: str | None) -> list[str]:
    """Content words: lowercase alphanumeric runs without stop words. The chat scorer normalises answers with it too."""
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _STOP]


def normalise(text: str | None) -> str:
    return " ".join(words(text))


def has_phrase(text_words: Sequence[str], phrase_words: Sequence[str]) -> bool:
    """Whether ``phrase_words`` occur in ``text_words`` as whole, consecutive words (never as a substring)."""
    n = len(phrase_words)
    return n > 0 and any(list(text_words[i:i + n]) == list(phrase_words) for i in range(len(text_words) - n + 1))


def oracle_corpus(gt_index: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The ground truth's reports by incident id (the oracle condition), in the contract's shape."""
    return {incident_id: entry["p1_shaped"] for incident_id, entry in gt_index["by_incident_id"].items()}


def _report(gt_index: Mapping[str, Any], incident_id: str) -> dict[str, Any]:
    entry = gt_index["by_incident_id"].get(incident_id)
    if entry is None:
        raise ChatReferenceError(f"no ground truth for {incident_id!r}")
    report: dict[str, Any] = entry["p1_shaped"]
    return report


def answer(question_class: str, gt_index: Mapping[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
    """The expected answer computed from the ground truth: ``{"kind": value|count|list|window|refusal, "value"}``."""
    if question_class not in CLASSES:
        raise ChatReferenceError(f"unknown question class {question_class!r}")
    if question_class in REFUSAL_CLASSES:
        return {"kind": "refusal", "value": None}
    if question_class in WRITTEN_CLASSES:
        raise ChatReferenceError(f"{question_class} answers are written facts, not computed")
    if CLASSES[question_class] == "incident":
        report = _report(gt_index, params["incident_id"])
        incident = report["incident"]
        if question_class == "incident.type":
            return {"kind": "value", "value": incident.get("type")}
        if question_class == "incident.severity":
            return {"kind": "value", "value": incident.get("severity_level")}
        if question_class == "incident.window":
            return {"kind": "window", "value": [incident.get("start_timestamp"), incident.get("end_timestamp")]}
        if question_class == "incident.people_count":
            return {"kind": "count", "value": sum(1 for e in report["entities"] if (e.get("type") or "") == "human")}
        if question_class == "incident.instrument_list":
            return {"kind": "list", "value": sorted({normalise(i.get("name")) for i in report["instruments"]})}
        if question_class == "incident.asset_list":
            return {"kind": "list", "value": sorted({normalise(a.get("name")) for a in report["assets"]})}
        if question_class == "incident.instrument_holder":
            instrument = next((i for i in report["instruments"] if i["instrument_id"] == params["instrument_id"]), None)
            if instrument is None:
                raise ChatReferenceError(f"{params['incident_id']} has no instrument {params['instrument_id']!r}")
            holder = next((e for e in report["entities"] if e["entity_id"] == instrument.get("entity_id")), None)
            # The holder as the ground truth describes them: an answer is judged on whether it identifies this person.
            # The local id ("E1") is kept for the validator, never matched in an answer.
            return {"kind": "holder", "value": holder.get("description") if holder else None,
                    "entity_id": holder["entity_id"] if holder else None, "instrument": instrument.get("name")}
    corpus = oracle_corpus(gt_index)
    if question_class == "library.type_count":
        matching = sum(1 for r in corpus.values() if r["incident"].get("type") == params["type"])
        return {"kind": "count", "value": matching}
    if question_class == "library.severity_at_least_count":
        level = int(params["level"])
        return {"kind": "count", "value": sum(1 for r in corpus.values()
                                              if (r["incident"].get("severity_level") or 0) >= level)}
    if question_class == "library.instrument_incidents":
        name = words(params["instrument"])  # whole words: "bat" never matches "baton" or "combat knife"
        holders = [i for i, r in corpus.items()
                   if any(has_phrase(words(x.get("name")), name) for x in r["instruments"])]
        return {"kind": "list", "value": sorted(holders)}
    if question_class == "library.most_common_type":
        counts = Counter(r["incident"].get("type") for r in corpus.values())
        top = max(counts.values())
        return {"kind": "list", "value": sorted(t for t, n in counts.items() if n == top)}
    if question_class == "library.retrieval":
        return {"kind": "list", "value": sorted(params["targets"])}
    raise ChatReferenceError(f"no computation for {question_class}")  # pragma: no cover


def recount(record: Mapping[str, Any], gt_index: Mapping[str, Any]) -> list[str]:
    """Problems when a countable reference's stored answer differs from the code recount (empty when they agree)."""
    payload = record["payload"]
    if payload["class"] in WRITTEN_CLASSES:
        return []
    fresh = answer(payload["class"], gt_index, payload["params"])
    return [] if fresh == payload["answer"] else [f"{record['record_id']}: stored {payload['answer']} != {fresh}"]


def generator_packet(question_class: str, params: Mapping[str, Any], gt_index: Mapping[str, Any], *,
                     prompt_id: str) -> dict[str, Any]:
    """What the generator sees: the class, the incident (or library summary) and the computed answer to word.

    A retrieval question describes its target the way a searcher would, so its generator also sees the target's
    report (``target_reports``); the other library classes need only the computed answer and their parameters.
    """
    if CLASSES[question_class] == "incident":
        context = _report(gt_index, params["incident_id"])
    else:
        context = {"incidents": len(gt_index["by_incident_id"])}
        if question_class == "library.retrieval":
            context["target_reports"] = {target: _report(gt_index, target) for target in params["targets"]}
    computed = None if question_class in WRITTEN_CLASSES else answer(question_class, gt_index, params)
    return {"role": "generator", "prompt": prompt_id, "class": question_class, "params": dict(params),
            "context": context, "computed_answer": computed}


def validator_packet(record: Mapping[str, Any], gt_index: Mapping[str, Any], *, prompt_id: str) -> dict[str, Any]:
    """The blind pass: the question as worded and its context, never the reference answer or facts.

    An incident question comes with its incident's report. A library question comes with every report of the oracle
    corpus, ordered by incident id (``reports``): the validator recounts types, severities and instruments, or finds
    the incidents a retrieval question describes, and nothing in the packet marks a target or the answer.
    """
    payload = record["payload"]
    params = payload["params"]
    if payload["scope"] == "incident":
        context = _report(gt_index, params["incident_id"])
    else:
        context = {"incidents": len(gt_index["by_incident_id"]),
                   "reports": dict(sorted(oracle_corpus(gt_index).items()))}
    return {"role": "validator", "prompt": prompt_id, "record_id": record["record_id"], "class": payload["class"],
            "question": payload["question"], "context": context}


def assert_blind(validator: Mapping[str, Any], record: Mapping[str, Any]) -> None:
    """Refuse a validator packet that holds the reference answer, facts, paraphrases or another question.

    The validator sees the same incident context as the generator (the oracle corpus), so a fact the generator took
    from that context may legitimately appear in it; what it must never see is the generator's output.
    """
    if validator.get("role") != "validator":
        raise ChatReferenceError("not a validator packet")
    forbidden = {"answer", "facts", "paraphrases", "computed_answer", "leakage"}
    if forbidden & set(validator):
        raise ChatReferenceError(f"the validator packet holds {sorted(forbidden & set(validator))}")
    if validator.get("question") != record["payload"]["question"] or validator.get("record_id") != record["record_id"]:
        raise ChatReferenceError("the validator packet is for another question")


def _jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


def facts_agree(reference: Sequence[str], rederived: Sequence[str], *, threshold: float = FACT_AGREEMENT) -> bool:
    """Every reference fact has a re-derived fact with content-word Jaccard >= ``threshold``."""
    return all(any(_jaccard(words(f), words(g)) >= threshold for g in rederived) for f in reference)


def answers_agree(reference: Mapping[str, Any], rederived: Mapping[str, Any]) -> bool:
    """Exact agreement for computed answers; fact agreement for written ones; both refusals agree.

    A holder agrees when the validator names the holder's ground-truth id or describes them in agreeing words.
    """
    if reference["kind"] == "facts":
        return rederived.get("kind") == "facts" and facts_agree(reference["facts"], rederived.get("facts", []))
    if reference["kind"] == "holder":
        given = rederived.get("value")
        if rederived.get("kind") not in ("value", "holder") or given is None or reference.get("value") is None:
            return False
        return str(given).strip() == str(reference.get("entity_id")) or facts_agree([reference["value"]], [str(given)])
    if reference["kind"] == "list":
        return sorted(map(str, reference["value"])) == sorted(map(str, rederived.get("value") or []))
    return reference == {"kind": rederived.get("kind"), "value": rederived.get("value")}


def leakage(record: Mapping[str, Any], gt_index: Mapping[str, Any]) -> dict[str, Any]:
    """How much of the target's ground truth the question's wording carries."""
    payload = record["payload"]
    texts = [payload["question"], *payload.get("paraphrases", [])]
    targets = ([payload["params"]["incident_id"]] if payload["scope"] == "incident"
               else list(payload["params"].get("targets", [])))
    description = " ".join(_report(gt_index, t)["incident"].get("description") or "" for t in targets)
    overlap = max((_jaccard(words(t), words(description)) for t in texts), default=0.0)
    value = payload["answer"].get("value")
    values = value if isinstance(value, list) else [value]
    stated = [str(v) for v in values if v not in (None, "") and payload["answer"]["kind"] in ("value", "list", "holder")
              and any(normalise(str(v)) and normalise(str(v)) in normalise(t) for t in texts)]
    return {"description_overlap": round(overlap, 4), "answer_in_question": stated}


def build_record(question_class: str, params: Mapping[str, Any], gt_index: Mapping[str, Any], *, question: str,
                 paraphrases: Sequence[str], dataset: str, generator: Mapping[str, Any],
                 facts: Sequence[str] | None = None, rederived: Mapping[str, Any] | None = None,
                 validator: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One question record; ``ai_validated`` when the code checks pass and the blind re-derivation agrees."""
    if question_class not in CLASSES:
        raise ChatReferenceError(f"unknown question class {question_class!r}")
    scope = CLASSES[question_class]
    if question_class in WRITTEN_CLASSES:
        if not facts:
            raise ChatReferenceError(f"{question_class} needs written facts")
        expected = {"kind": "facts", "facts": [str(f) for f in facts]}
    else:
        expected = answer(question_class, gt_index, params)
    subject = params.get("incident_id") or "library"
    record_id = f"chat:{question_class}:{subject}:{sha256_json({'question': question, 'params': dict(params)})[:12]}"
    payload = {"class": question_class, "scope": scope, "params": dict(params), "question": question,
               "paraphrases": list(paraphrases), "answer": expected}
    leak = leakage({"record_id": record_id, "payload": payload}, gt_index)
    record = new_record(record_id=record_id, dataset=dataset, kind="chat_question", clip=params.get("incident_id"),
                        strata={"class": question_class, "scope": scope}, payload={**payload, "leakage": leak},
                        generator=dict(generator))
    if rederived is None or validator is None:
        return record
    checks = [{"name": "code_recount", "passed": not recount(record, gt_index)},
              {"name": "answer_not_in_question", "passed": not leak["answer_in_question"]},
              {"name": "paraphrases_distinct", "passed": len({normalise(t) for t in [question, *paraphrases]})
               == 1 + len(paraphrases)}]
    return promote_validated(record, checks=checks,
                             validator={**dict(validator), "agrees": answers_agree(expected, rederived)})
