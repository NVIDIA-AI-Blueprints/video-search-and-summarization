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
- **Exact answers** for computed references: a count is the first number in the answer (digits or a number word),
  a value or list item must appear in it (normalised words), a window needs both seconds; a refusal is expected for
  unanswerable and false-premise questions and nowhere else.
- **Citations:** cited incidents against the question's incident (or targets): precision and recall.
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
from evalv2.tasks.chat import v1
from evalv2.tasks.judging import Judge, JudgeIdentity

PASS_SCORE = 0.5
K_VALUES = (1, 3, 5, 10)
CORPORA = ("oracle", "system")
JUDGE_PROMPTS = {name: Path(__file__).with_name(f"judge-{name}-v1.txt") for name in ("fact", "unsupported",
                                                                                      "contradiction")}
NUMBER_WORDS = {"zero": 0, "none": 0, "no": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
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


def _words(text: str | None) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _contains(text: str, phrase: str) -> bool:
    words, target = _words(text), _words(phrase)
    if not target:
        return False
    return any(words[i:i + len(target)] == target for i in range(len(words) - len(target) + 1))


def first_number(text: str) -> int | None:
    for word in _words(text):
        if word.isdigit():
            return int(word)
        if word in NUMBER_WORDS:
            return NUMBER_WORDS[word]
    return None


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


def exact(answer: ChatAnswer, expected: Mapping[str, Any]) -> bool | None:
    """Whether the answer gives the computed answer; ``None`` for written facts (judged instead)."""
    kind, value = expected["kind"], expected.get("value")
    if kind == "refusal":
        return answer.refused
    if answer.refused:
        return False
    if kind == "count":
        return first_number(answer.text) == value
    if kind == "value":
        return value is not None and _contains(answer.text, str(value))
    if kind == "list":
        return all(_contains(answer.text, str(item)) for item in value or [])
    if kind == "window":
        numbers = [int(w) for w in _words(answer.text) if w.isdigit()]
        return value is not None and all(v in numbers for v in value)
    return None


def citations(answer: ChatAnswer, expected: Sequence[str]) -> dict[str, float | None]:
    cited, wanted = set(answer.citations), set(expected)
    hits = len(cited & wanted)
    return {"precision": hits / len(cited) if cited else None, "recall": hits / len(wanted) if wanted else None}


def _judged(judge: Judge, expected: str, predicted: str) -> bool | None:
    verdict = judge(expected, predicted)
    return None if not verdict.ok or verdict.score is None else verdict.score >= PASS_SCORE


def score_answer(record: Mapping[str, Any], answer: ChatAnswer, *, corpus: str, ground_truth_text: str,
                 judges: Mapping[str, Judge] | None = None) -> dict[str, Any]:
    """Every check for one answer; judged checks are ``None`` (unscored) without a judge or a cached result."""
    if corpus not in CORPORA:
        raise ValueError(f"corpus must be one of {CORPORA}")
    payload = effective_payload(record)  # a person's edit replaces the reference; a rejected one is not scored
    if payload is None:
        return {"record_id": record["record_id"], "status": "reference_rejected", "corpus": corpus}
    expected = payload["answer"]
    targets = (list(payload["params"].get("targets", [])) if payload["scope"] == "library"
               else [payload["params"]["incident_id"]])
    out: dict[str, Any] = {"record_id": record["record_id"], "status": "scored", "class": payload["class"],
                           "scope": payload["scope"],
                           "tier": record["tier"], "corpus": corpus, "refusal_expected": expected["kind"] == "refusal",
                           "refused": answer.refused, "exact": exact(answer, expected),
                           "citations": citations(answer, targets) if expected["kind"] != "refusal" else None}
    if payload["class"] == "library.retrieval":
        out["retrieval"] = retrieval(answer, targets)
    judges = judges or {}
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
        "retrieval": {at: {metric: _mean(r[at][metric] for r in retrieval_rows)
                           for metric in ("precision", "recall", "mrr", "ndcg")}
                      for at in (retrieval_rows[0] if retrieval_rows else {})},
        "by_class": {cls: {"answers": len(group), "exact_rate": _mean(float(s["exact"]) for s in group
                                                                   if s["exact"] is not None)}
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
