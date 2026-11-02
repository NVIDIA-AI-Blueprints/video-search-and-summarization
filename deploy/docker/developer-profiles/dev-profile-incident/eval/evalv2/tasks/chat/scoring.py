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

"""Chat scorers: one chatbot answer against one chat reference (methodology v2.0 §4 D).

- **Retrieval** (library questions with targets): incident-level P@K, R@K and MRR from V1's MVP2 ``list_metrics``,
  plus nDCG@K with binary relevance, on the answer's ranked retrieved incidents.
- **Exact answers** for computed references, read with the references' own normaliser (``references.chat.words``:
  stop words dropped, whole words only):
  - a count is the first number in the answer (digits or a number word);
  - a value is the one the answer asserts: the severity level it states (a "1-5" scale or "out of 5" is not a
    level), or the incident type it names among the corpus's types, and naming two is not exact;
  - a list is the set of items the answer asserts: every expected item, and no other item of the reference's
    vocabulary (every instrument or asset name, incident id or type in the corpus). A negated mention ("no
    knife") asserts nothing, and an empty list needs an explicit "none" (or "no ...");
  - a window needs both seconds;
  - a refusal is expected for unanswerable and false-premise questions and nowhere else.
  An item the vocabulary does not know cannot be detected as extra; the unsupported-claim judge covers those.
- **Holder questions** are judged, not matched: the answer must identify the holder the ground truth describes
  (the fact judge on "<holder> held the <instrument>"), since a system corpus numbers people on its own.
- **Citations:** cited incidents against the incidents the expected answer rests on (``support``: the question's
  incident, a count's counted incidents, a filter's listed incidents, a retrieval question's targets): precision
  and recall.
- **Judged checks** (through the judge cache, so a re-score replays them offline): each reference fact stated or not
  (fact recall), any unsupported claim against the retrieved context, any contradiction of the ground truth.

The judge must come from neither the subject's model family nor the reference generator's
(:func:`check_judge_family`): a family grading its own output is biased. Results on the oracle corpus and the
system corpus are aggregated apart and never pooled.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evalv2.datasets.records import effective_payload
from evalv2.references.chat import words
from evalv2.tasks.chat import v1
from evalv2.tasks.judging import Judge, JudgeIdentity

PASS_SCORE = 0.5
K_VALUES = (1, 3, 5, 10)
CORPORA = ("oracle", "system")
JUDGED_KINDS = frozenset({"facts", "holder"})  # answers the judge scores; exact() is None for them
JUDGE_PROMPTS = {name: Path(__file__).with_name(f"judge-{name}-v1.txt") for name in ("fact", "unsupported",
                                                                                      "contradiction")}
NUMBER_WORDS = {"zero": 0, "none": 0, "no": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
NEGATIONS = frozenset({"no", "not", "without", "never", "nor", "neither", "t"})  # "t": the tail of "wasn't"
NONE_WORDS = frozenset({"none", "nothing", "no", "zero", "neither", "nobody", "unarmed"})
_LEVEL = r"([1-5]|one|two|three|four|five)"
_SCALE = re.compile(r"\b(?:1|one)\s*(?:-|–|—|to)\s*(?:5|five)\b|\bout\s+of\s+(?:5|five)\b|/\s*(?:5|five)\b"
                    r"|\bscale\s+of\s+(?:5|five)\b")
_SEVERITY_LEVEL = re.compile(r"\b(?:severity|level)\b\D{0,24}?\b" + _LEVEL + r"\b")
_FAMILIES = (("claude", "claude"), ("anthropic", "claude"), ("nemotron", "nemotron"), ("cosmos", "cosmos"),
             ("llama", "llama"), ("gpt", "gpt"), ("openai", "gpt"), ("qwen", "qwen"), ("gemini", "gemini"),
             ("mistral", "mistral"), ("mixtral", "mistral"))


class JudgeFamilyError(ValueError):
    """A judge from the subject's or the reference generator's model family."""


@dataclass(frozen=True)
class ChatAnswer:
    """A chatbot answer, normalised from whatever contract the subject speaks."""

    text: str
    citations: tuple[str, ...] = ()
    refused: bool = False
    retrieved: tuple[str, ...] = ()
    context: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)


def model_family(model: str) -> str:
    """The model family a model id belongs to (``nvidia/nemotron-3-...`` -> ``nemotron``); the id when unknown."""
    lowered = model.lower()
    return next((family for marker, family in _FAMILIES if marker in lowered), lowered)


def check_judge_family(judge_model: str, *, subjects: Iterable[str], generators: Iterable[str]) -> None:
    judge = model_family(judge_model)
    clashes = sorted({m for m in (*subjects, *generators) if model_family(m) == judge})
    if clashes:
        raise JudgeFamilyError(f"judge {judge_model!r} is of the {judge!r} family, like {clashes}; choose another")


def judge_identity(kind: str, model: str, settings: Mapping[str, Any]) -> JudgeIdentity:
    """The cache identity of one judged check: its model, its prompt file's SHA-256 and the request settings."""
    return JudgeIdentity(model, hashlib.sha256(JUDGE_PROMPTS[kind].read_bytes()).hexdigest(), settings)


def _tokens(text: str | None) -> list[str]:
    """Every lowercase alphanumeric run, stop words kept (numbers and negations need them)."""
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _mentions(text_words: Sequence[str], phrase: str) -> list[int]:
    """Where ``phrase`` starts in ``text_words`` as whole words, by the references' own normaliser."""
    target = words(phrase)
    n = len(target)
    return [i for i in range(len(text_words) - n + 1) if n and list(text_words[i:i + n]) == target]


def _asserted(text_words: Sequence[str], phrase: str) -> bool:
    """A mention not negated by one of the two words before it ("no knife", "not a knife": "a" is a stop word)."""
    return any(not NEGATIONS & set(text_words[max(0, i - 2):i]) for i in _mentions(text_words, phrase))


def says_none(text: str) -> bool:
    """An explicit "none": a none word, or a negation followed closely by "any" ("wasn't any", "without any")."""
    tokens = _tokens(text)
    if NONE_WORDS & set(tokens):
        return True
    return any(token in NEGATIONS and "any" in tokens[i + 1:i + 4] for i, token in enumerate(tokens))


def first_number(text: str) -> int | None:
    for word in _tokens(text):
        if word.isdigit():
            return int(word)
        if word in NUMBER_WORDS:
            return NUMBER_WORDS[word]
    return None


def asserted_level(text: str) -> int | None:
    """The severity level an answer states: the level after "severity" or "level", else its only number 1-5.

    A scale ("1-5", "1 to 5", "out of 5", "/5") is removed first, so "severity 2 on a 1-5 scale" states 2.
    """
    lowered = _SCALE.sub(" ", (text or "").lower())
    stated = _SEVERITY_LEVEL.search(lowered)
    if stated:
        token = stated.group(1)
        return int(token) if token.isdigit() else NUMBER_WORDS[token]
    levels = {int(t) if t.isdigit() else NUMBER_WORDS[t] for t in _tokens(lowered)
              if t in ("1", "2", "3", "4", "5", "one", "two", "three", "four", "five")}
    return levels.pop() if len(levels) == 1 else None


def asserted_value(text: str, value: Any, space: Mapping[str, Any]) -> Any:
    """The value an answer asserts, or ``None`` when it asserts none or several."""
    if isinstance(value, int) and not isinstance(value, bool):
        return asserted_level(text)
    text_words = words(text)
    candidates = [str(c) for c in space.get("candidates") or [value]]
    named = {c for c in candidates if _asserted(text_words, c)}
    return named.pop() if len(named) == 1 else None


def asserted_items(text: str, expected: Sequence[Any], space: Mapping[str, Any]) -> bool:
    """Whether the answer asserts exactly the expected items: all of them, and no other vocabulary item.

    Expected mentions are masked first, so "stick" inside an expected "wooden stick" is not an extra item.
    """
    if not expected:
        return says_none(text) and not any(_asserted(words(text), str(item)) for item in space.get("vocabulary") or [])
    text_words = list(words(text))
    for item in sorted((str(i) for i in expected), key=lambda i: -len(words(i))):
        starts = _mentions(text_words, item) if _asserted(text_words, item) else []
        if not starts:
            return False
        for start in starts:
            text_words[start:start + len(words(item))] = ["\x00"] * len(words(item))
    wanted = {" ".join(words(str(i))) for i in expected}
    extras = [v for v in space.get("vocabulary") or [] if " ".join(words(str(v))) not in wanted]
    return not any(_asserted(text_words, str(extra)) for extra in extras)


def ndcg(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float | None:
    relevant = set(relevant)
    if not relevant:
        return None
    seen: set[str] = set()
    gain = 0.0
    for position, item in enumerate(retrieved[:k], start=1):
        if item in relevant and item not in seen:
            gain += 1 / math.log2(position + 1)
        seen.add(item)
    ideal = sum(1 / math.log2(position + 1) for position in range(1, min(k, len(relevant)) + 1))
    return gain / ideal


def retrieval(answer: ChatAnswer, targets: Sequence[str], k_values: Sequence[int] = K_VALUES) -> dict[str, Any]:
    pool = [v1.Candidate(key=item, similarity=0.0, verdict="unverified") for item in answer.retrieved]
    out = {}
    for k in k_values:
        metrics = v1.list_metrics(pool[:k], targets, k)
        out[f"@{k}"] = {"precision": metrics.standard_precision, "recall": metrics.recall, "mrr": metrics.mrr,
                        "ndcg": ndcg(list(answer.retrieved), targets, k)}
    return out


# A question about the most common type is answered by the type(s) the answer calls the most common; an answer may go
# on to rank the others ("assault is the most common type, followed by burglary (1)"), which names no extra answer.
RANKED_CLASSES = frozenset({"library.most_common_type"})
_SUPERLATIVE = re.compile(r"\b(?:most|commonest|top|leading)\b")
_CLAUSE_BREAK = re.compile(r"[.;:!?,()]|\b(?:followed by|then|while|whereas|but|ahead of)\b")


def superlative_clauses(text: str | None) -> str | None:
    """The clauses of an answer that state a superlative ("assault is the most common type"), joined; ``None`` when
    no clause does. Clauses break at punctuation and at words that move on to the rest of a ranking."""
    clauses = [c for c in _CLAUSE_BREAK.split((text or "").lower()) if _SUPERLATIVE.search(c)]
    return " ".join(clauses) if clauses else None


def exact(
    answer: ChatAnswer,
    expected: Mapping[str, Any],
    space: Mapping[str, Any] | None = None,
    *,
    question_class: str | None = None,
) -> bool | None:
    """Whether the answer gives exactly the computed answer; ``None`` for facts and holders (judged instead).

    ``space`` is the reference's ``answer_space`` (the candidates or vocabulary an answer could name instead);
    without it a value or list can only be checked for the expected words, never for an extra one. For a ranked
    class (the most common type) the answer is what its superlative clauses name, when they name any vocabulary item.
    """
    kind, value = expected["kind"], expected.get("value")
    space = space or {}
    if kind == "refusal":
        return answer.refused
    if kind in JUDGED_KINDS:  # never in the exact rate, refused or not; a refusal shows in the refusal metrics
        return None
    if answer.refused:
        return False
    if kind == "count":
        return first_number(answer.text) == value
    if kind == "value":
        return value is not None and asserted_value(answer.text, value, space) == value
    if kind == "list":
        text = answer.text
        if question_class in RANKED_CLASSES:
            stated = superlative_clauses(text)
            if stated and any(_asserted(words(stated), str(item)) for item in space.get("vocabulary") or []):
                text = stated
        return asserted_items(text, list(value or []), space)
    if kind == "window":
        numbers = [int(w) for w in _tokens(answer.text) if w.isdigit()]
        return value is not None and all(v in numbers for v in value)
    return None


def citations(answer: ChatAnswer, expected: Sequence[str]) -> dict[str, float | None]:
    cited, wanted = set(answer.citations), set(expected)
    hits = len(cited & wanted)
    return {"precision": hits / len(cited) if cited else None, "recall": hits / len(wanted) if wanted else None}


def _judged(judge: Judge, expected: str, predicted: str) -> bool | None:
    verdict = judge(expected, predicted)
    return None if not verdict.ok or verdict.score is None else verdict.score >= PASS_SCORE


def holder_fact(expected: Mapping[str, Any]) -> str:
    """The atomic fact a holder answer must state, for the fact judge."""
    return f"{expected['value']} held the {expected.get('instrument') or 'instrument'}"


def expected_citations(payload: Mapping[str, Any]) -> list[str]:
    """The incidents a grounded answer cites: the reference's ``support``, else its incident or targets."""
    if payload.get("support") is not None:
        return list(payload["support"])
    if payload["scope"] == "library":
        return list(payload["params"].get("targets", []))
    return [payload["params"]["incident_id"]]


def score_answer(record: Mapping[str, Any], answer: ChatAnswer, *, corpus: str, ground_truth_text: str,
                 judges: Mapping[str, Judge] | None = None) -> dict[str, Any]:
    """Every check for one answer; judged checks are ``None`` (unscored) without a judge or a cached result."""
    if corpus not in CORPORA:
        raise ValueError(f"corpus must be one of {CORPORA}")
    payload = effective_payload(record)  # a person's edit replaces the reference; a rejected one is not scored
    if payload is None:
        return {"record_id": record["record_id"], "status": "reference_rejected", "corpus": corpus}
    expected = payload["answer"]
    out: dict[str, Any] = {"record_id": record["record_id"], "status": "scored", "class": payload["class"],
                           "scope": payload["scope"],
                           "tier": record["tier"], "corpus": corpus, "refusal_expected": expected["kind"] == "refusal",
                           "refused": answer.refused, "exact": exact(answer, expected, payload.get("answer_space"), question_class=payload["class"]),
                           "citations": (citations(answer, expected_citations(payload))
                                         if expected["kind"] != "refusal" else None)}
    if payload["class"] == "library.retrieval":
        out["retrieval"] = retrieval(answer, list(payload["params"].get("targets", [])))
    judges = judges or {}
    if expected["kind"] == "holder":
        if answer.refused:
            out["holder_identified"] = False
        else:
            out["holder_identified"] = (_judged(judges["fact"], holder_fact(expected), answer.text)
                                        if "fact" in judges and expected.get("value") else None)
    if expected["kind"] == "facts":
        verdicts = [_judged(judges["fact"], fact, answer.text) if "fact" in judges else None
                    for fact in expected["facts"]]
        scored = [v for v in verdicts if v is not None]
        out["facts"] = {"stated": sum(scored), "scored": len(scored), "total": len(verdicts),
                        "recall": sum(scored) / len(scored) if scored else None}
    if not answer.refused:
        out["unsupported_claim"] = (_judged(judges["unsupported"], answer.context, answer.text)
                                    if "unsupported" in judges and answer.context else None)
        out["contradiction"] = (_judged(judges["contradiction"], ground_truth_text, answer.text)
                                if "contradiction" in judges else None)
    return out


def _mean(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(sum(present) / len(present), 4) if present else None


def _summary(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    refused = [s for s in rows if s["refused"]]
    expected = [s for s in rows if s["refusal_expected"]]
    retrieval_rows = [s["retrieval"] for s in rows if "retrieval" in s]
    return {
        "answers": len(rows),
        "exact_rate": _mean(float(s["exact"]) for s in rows if s["exact"] is not None),
        "refusal_precision": _mean(float(s["refusal_expected"]) for s in refused) if refused else None,
        "refusal_recall": _mean(float(s["refused"]) for s in expected) if expected else None,
        "citation_precision": _mean((s["citations"] or {}).get("precision") for s in rows),
        "citation_recall": _mean((s["citations"] or {}).get("recall") for s in rows),
        "fact_recall": _mean((s.get("facts") or {}).get("recall") for s in rows),
        "unsupported_claim_rate": _mean(float(s["unsupported_claim"]) for s in rows
                                        if s.get("unsupported_claim") is not None),
        "contradiction_rate": _mean(float(s["contradiction"]) for s in rows if s.get("contradiction") is not None),
        "holder_identified_rate": _mean(float(s["holder_identified"]) for s in rows
                                        if s.get("holder_identified") is not None),
        "retrieval": {at: {metric: _mean(r[at][metric] for r in retrieval_rows)
                           for metric in ("precision", "recall", "mrr", "ndcg")}
                      for at in (retrieval_rows[0] if retrieval_rows else {})},
        "by_class": {cls: {"answers": len(group), "exact_rate": _mean(float(s["exact"]) for s in group
                                                                   if s["exact"] is not None),
                           **({"holder_identified_rate": _mean(float(s["holder_identified"]) for s in group
                                                               if s.get("holder_identified") is not None)}
                              if any("holder_identified" in s for s in group) else {})}
                     for cls in sorted({s["class"] for s in rows})
                     for group in [[s for s in rows if s["class"] == cls]]},
    }


def aggregate(scores: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Per corpus (never pooled) and per reference tier: ``primary`` is ``human_verified``; the AI tiers beside it.

    Each group reports the exact rate, refusal precision and recall, citation means, fact recall, the judged rates
    and retrieval means, overall and per class. Rejected references are counted, not scored.
    """
    scores = list(scores)
    out: dict[str, Any] = {"rejected_references": sum(1 for s in scores if s.get("status") == "reference_rejected")}
    for corpus in CORPORA:
        rows = [s for s in scores if s["corpus"] == corpus and s.get("status") == "scored"]
        groups = {"primary": [s for s in rows if s["tier"] == "human_verified"],
                  "ai_validated": [s for s in rows if s["tier"] == "ai_validated"],
                  "ai_proposed": [s for s in rows if s["tier"] == "ai_proposed"]}
        summaries = {name: _summary(group) for name, group in groups.items() if group}
        if summaries:
            out[corpus] = summaries
    return out
