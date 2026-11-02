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

"""Scoring a chat run (J3): harness answer rows to three separate result blocks, per corpus, never pooled.

Each row of ``answers.jsonl`` is read into a :class:`~evalv2.tasks.chat.scoring.ChatAnswer` (:func:`chat_answer`)
and scored by the existing scorers (:func:`~evalv2.tasks.chat.scoring.score_answer`). Then, per corpus and per
reference tier group (``all``, ``ai_validated``, ``ai_proposed``; no reference is ``human_verified`` yet):

- **coverage:** planned, answered, ``no_report`` (the system corpus has no report for the incident) and
  ``http_error`` items, each counted; only answered items are scored.
- **retrieval** (library questions only): the ranked list is every report matching the router's plan, in its rank
  order; the relevant incidents are the reference's ``support`` (a count's counted incidents, a filter's listed
  incidents, a retrieval question's targets). Precision, recall, MRR and nDCG at K = 1, 3, 5, 10 and V1's
  ``coverage`` (|L| / K), plus the matching set's precision and recall (what a count is computed over).
- **answers:** the exact rate over answers with a code-derived reference (counts, values, lists, windows), by
  class; refusal correctness (refusal recall on the unanswerable and false-premise questions, the false-refusal
  rate on the answerable ones); judge-needed checks (written facts, holders, unsupported claims, contradictions)
  stay **pending** and are counted, never scored as 0, until a judge cache holds their verdicts.
- **citations:** incident-level precision and recall against ``support`` (a library answer cites (incident, item)
  pairs; a code-composed count names incidents in its prose instead, which count as its citations), the share of
  model citations the router kept (its ``dropped_citations`` are ids the context does not hold), and answers that
  cite nothing.

A list of incidents (``library.instrument_incidents``, ``library.retrieval``) is read from the incidents the answer
cites or names: the router writes incidents as type and title, never as ids. Intervals are a cluster bootstrap
(fixed seed): an incident question's cluster is its incident, a library question's its record, and repeats of one
question share a cluster.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from evalv2.datasets.records import effective_payload
from evalv2.tasks.chat import scoring, v1
from evalv2.tasks.chat.harness import latest_rows

INCIDENT_LIST_CLASSES = frozenset({"library.instrument_incidents", "library.retrieval"})
K_VALUES = (1, 3, 5, 10)
RESAMPLES, SEED, CONFIDENCE = 2000, 20261027, 0.95
TIER_GROUPS = ("all", "ai_validated", "ai_proposed")
JUDGE_CHECKS = ("facts", "holder_identified", "unsupported_claim", "contradiction")


def cited_incidents(row: Mapping[str, Any]) -> list[str]:
    """The incidents an answer cites, in order: its citations' incidents, then a code answer's named incidents."""
    out: list[str] = []
    for citation in (row.get("answer") or {}).get("citations") or []:
        incident = str(citation.get("incident_id") or "")
        if incident and incident not in out:
            out.append(incident)
    for incident in (row.get("library") or {}).get("named") or []:
        if incident not in out:
            out.append(incident)
    return out


def chat_answer(row: Mapping[str, Any], question_class: str) -> scoring.ChatAnswer:
    answer = row["answer"]
    cited = cited_incidents(row)
    refused = bool(answer.get("insufficient_evidence"))
    text = answer["content"]
    if question_class in INCIDENT_LIST_CLASSES and not refused:
        text = " ".join(cited) if cited else "none"
    retrieved = (row.get("library") or {}).get("ranked") or ([row["incident_id"]] if row.get("incident_id") else [])
    context = "\n".join(call.get("context") or "" for call in row.get("calls") or [])
    return scoring.ChatAnswer(text=text, citations=tuple(cited), refused=refused, retrieved=tuple(retrieved),
                              context=context, extra={"content": answer["content"]})


def ground_truth_text(payload: Mapping[str, Any], gt_index: Mapping[str, Any] | None) -> str:
    """What the contradiction judge compares against: the ground-truth reports the expected answer rests on."""
    if not gt_index:
        return ""
    support = scoring.expected_citations(payload)
    reports = {i: gt_index["by_incident_id"][i]["p1_shaped"] for i in support if i in gt_index["by_incident_id"]}
    return json.dumps(reports, sort_keys=True, ensure_ascii=False)


def retrieval_metrics(ranked: Sequence[str], relevant: Sequence[str]) -> dict[str, Any]:
    pool = [v1.Candidate(key=item, similarity=0.0, verdict="unverified") for item in ranked]
    out: dict[str, Any] = {}
    for k in K_VALUES:
        metrics = v1.list_metrics(pool[:k], relevant, k)
        out[f"@{k}"] = {"precision": metrics.standard_precision, "recall": metrics.recall, "mrr": metrics.mrr,
                        "ndcg": scoring.ndcg(list(ranked), relevant, k), "coverage": metrics.coverage}
    found, wanted = set(ranked), set(relevant)
    out["set"] = {"precision": len(found & wanted) / len(found) if found else None,
                  "recall": len(found & wanted) / len(wanted) if wanted else None,
                  "matching": len(found), "relevant": len(wanted)}
    return out


def score_row(row: Mapping[str, Any], record: Mapping[str, Any], gt_index: Mapping[str, Any] | None = None,
              judges: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One answer row's scores (``status`` ``no_report`` or ``http_error`` rows are counted, not scored)."""
    base = {"key": row["key"], "record_id": row["record_id"], "corpus": row["corpus"], "repeat": row["repeat"],
            "subject": row["subject"], "class": row["class"], "scope": row["scope"], "tier": record["tier"],
            "cluster": row.get("incident_id") if row["scope"] == "incident" and row.get("incident_id")
            else row["record_id"]}
    if row["outcome"] != "answered":
        return {**base, "status": row["outcome"]}
    payload = effective_payload(record)
    if payload is None:
        return {**base, "status": "reference_rejected"}
    answer = chat_answer(row, payload["class"])
    scored = scoring.score_answer(record, answer, corpus=row["corpus"],
                                  ground_truth_text=ground_truth_text(payload, gt_index), judges=judges)
    if row["scope"] == "incident" and row.get("incident_id"):
        scored["cluster"] = row["incident_id"]
    calls = row.get("calls") or []
    kept = len((row.get("answer") or {}).get("citations") or [])
    dropped = len((row.get("grounding") or {}).get("dropped_citations") or [])
    out = {**base, **scored, "status": "scored",
           "citation_validity": kept / (kept + dropped) if kept + dropped else None,
           "cites_nothing": not answer.refused and not answer.citations,
           "answered_by": (row.get("grounding") or {}).get("answered_by", "model"),
           "usage": row.get("usage") or {}, "elapsed_ms": row.get("elapsed_ms"),
           "calls": len(calls)}
    if row["scope"] == "library":
        out["retrieval"] = retrieval_metrics((row.get("library") or {}).get("ranked") or [],
                                             scoring.expected_citations(payload))
    return out


def _mean(values: Iterable[float | None]) -> float | None:
    present = [float(v) for v in values if v is not None]
    return round(sum(present) / len(present), 4) if present else None


def _rate(rows: Sequence[Mapping[str, Any]], key: Callable[[Mapping[str, Any]], float | None]) -> dict[str, Any]:
    values = [key(r) for r in rows]
    present = [v for v in values if v is not None]
    return {"value": _mean(present), "n": len(present)}


def bootstrap(rows: Sequence[Mapping[str, Any]], metric: Callable[[Sequence[Mapping[str, Any]]], float | None], *,
              resamples: int = RESAMPLES, seed: int = SEED) -> list[float] | None:
    """A percentile interval of ``metric`` over cluster resamples (``None`` with fewer than two clusters)."""
    clusters: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        clusters.setdefault(str(row["cluster"]), []).append(row)
    keys = sorted(clusters)
    if len(keys) < 2:
        return None
    rng = random.Random(seed)
    values = []
    for _ in range(resamples):
        sample = [row for _ in keys for row in clusters[keys[rng.randrange(len(keys))]]]
        value = metric(sample)
        if value is not None:
            values.append(value)
    if not values:
        return None
    values.sort()
    low = values[int((1 - CONFIDENCE) / 2 * (len(values) - 1))]
    high = values[int((1 + CONFIDENCE) / 2 * (len(values) - 1))]
    return [round(low, 4), round(high, 4)]


def _with_ci(rows: Sequence[Mapping[str, Any]], key: Callable[[Mapping[str, Any]], float | None],
             resamples: int) -> dict[str, Any]:
    out = _rate(rows, key)
    out["ci95"] = bootstrap(rows, lambda sample: _mean(key(r) for r in sample), resamples=resamples) \
        if out["n"] else None
    return out


def _retrieval_block(rows: Sequence[Mapping[str, Any]], resamples: int) -> dict[str, Any]:
    library = [r for r in rows if "retrieval" in r]
    block: dict[str, Any] = {"questions": len(library), "relevant_from": "reference support", "by_k": {}}
    for k in K_VALUES:
        block["by_k"][f"@{k}"] = {metric: _with_ci(library, lambda r, m=metric, k=k: r["retrieval"][f"@{k}"][m],
                                                   resamples) for metric in ("precision", "recall", "mrr", "ndcg",
                                                                             "coverage")}
    block["matching_set"] = {metric: _rate(library, lambda r, m=metric: r["retrieval"]["set"][m])
                             for metric in ("precision", "recall")}
    block["by_class"] = {cls: {"questions": len(group),
                               "recall@5": _rate(group, lambda r: r["retrieval"]["@5"]["recall"])["value"],
                               "ndcg@5": _rate(group, lambda r: r["retrieval"]["@5"]["ndcg"])["value"],
                               "mrr@10": _rate(group, lambda r: r["retrieval"]["@10"]["mrr"])["value"],
                               "set_recall": _rate(group, lambda r: r["retrieval"]["set"]["recall"])["value"]}
                         for cls in sorted({r["class"] for r in library})
                         for group in [[r for r in library if r["class"] == cls]]}
    return block


def _answers_block(rows: Sequence[Mapping[str, Any]], resamples: int) -> dict[str, Any]:
    exact_rows = [r for r in rows if r["exact"] is not None and not r["refusal_expected"]]
    refusal = [r for r in rows if r["refusal_expected"]]
    answerable = [r for r in rows if not r["refusal_expected"]]
    pending = Counter()
    for r in rows:
        if r.get("facts") is not None and r["facts"]["recall"] is None and r["facts"]["total"]:
            pending["facts"] += 1
        for check in ("holder_identified", "unsupported_claim", "contradiction"):
            if check in r and r[check] is None:
                pending[check] += 1
    return {
        "exact": _with_ci(exact_rows, lambda r: float(r["exact"]), resamples),
        "exact_by_class": {cls: _rate(group, lambda r: float(r["exact"]))
                           for cls in sorted({r["class"] for r in exact_rows})
                           for group in [[r for r in exact_rows if r["class"] == cls]]},
        "refusal": {"recall": _with_ci(refusal, lambda r: float(r["refused"]), resamples),
                    "false_refusal_rate": _rate(answerable, lambda r: float(r["refused"])),
                    "by_class": {cls: _rate(group, lambda r: float(r["refused"]))
                                 for cls in sorted({r["class"] for r in refusal})
                                 for group in [[r for r in refusal if r["class"] == cls]]}},
        "judged": {"fact_recall": _rate(rows, lambda r: (r.get("facts") or {}).get("recall")),
                   "holder_identified": _rate(rows, lambda r: None if r.get("holder_identified") is None
                                              else float(r["holder_identified"])),
                   "unsupported_claim_rate": _rate(rows, lambda r: None if r.get("unsupported_claim") is None
                                                   else float(r["unsupported_claim"])),
                   "contradiction_rate": _rate(rows, lambda r: None if r.get("contradiction") is None
                                               else float(r["contradiction"])),
                   "pending": dict(sorted(pending.items()))},
    }


def _citations_block(rows: Sequence[Mapping[str, Any]], resamples: int) -> dict[str, Any]:
    cited = [r for r in rows if r.get("citations") is not None]
    return {
        "level": "incident",
        "precision": _with_ci(cited, lambda r: r["citations"]["precision"], resamples),
        "recall": _with_ci(cited, lambda r: r["citations"]["recall"], resamples),
        "validity": _rate(rows, lambda r: r.get("citation_validity")),
        "cites_nothing": sum(1 for r in rows if r.get("cites_nothing")),
        "by_scope": {scope: {"precision": _rate(group, lambda r: r["citations"]["precision"])["value"],
                             "recall": _rate(group, lambda r: r["citations"]["recall"])["value"],
                             "answers": len(group)}
                     for scope in ("incident", "library")
                     for group in [[r for r in cited if r["scope"] == scope]] if group},
    }


def _cost(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    usage = Counter()
    for r in rows:
        for key in ("calls", "prompt_tokens", "completion_tokens", "total_tokens"):
            value = (r.get("usage") or {}).get(key)
            if isinstance(value, int | float):
                usage[key] += value
    latencies = sorted(r["elapsed_ms"] for r in rows if isinstance(r.get("elapsed_ms"), int | float))
    return {**dict(usage), "latency_ms_median": latencies[len(latencies) // 2] if latencies else None,
            "latency_ms_max": latencies[-1] if latencies else None}


def report(scores: Sequence[Mapping[str, Any]], *, resamples: int = RESAMPLES) -> dict[str, Any]:
    """Per corpus, per tier group: coverage, then retrieval, answers and citations as separate blocks."""
    out: dict[str, Any] = {"bootstrap": {"resamples": resamples, "seed": SEED, "confidence": CONFIDENCE,
                                         "cluster": "incident (incident questions), record (library questions)"},
                           "corpora": {}}
    for corpus in scoring.CORPORA:
        rows = [s for s in scores if s["corpus"] == corpus]
        if not rows:
            continue
        coverage = {"planned": len(rows), **dict(Counter(s["status"] for s in rows))}
        scored = [s for s in rows if s["status"] == "scored"]
        groups = {}
        for group in TIER_GROUPS:
            chosen = scored if group == "all" else [s for s in scored if s["tier"] == group]
            if chosen:
                groups[group] = {"answered": len(chosen), "retrieval": _retrieval_block(chosen, resamples),
                                 "answers": _answers_block(chosen, resamples),
                                 "citations": _citations_block(chosen, resamples)}
        out["corpora"][corpus] = {"coverage": coverage, "cost": _cost(scored), "tiers": groups}
    return out


def score_run(rows: Iterable[Mapping[str, Any]], records: Mapping[str, Mapping[str, Any]],
              gt_index: Mapping[str, Any] | None = None, judges: Mapping[str, Any] | None = None
              ) -> list[dict[str, Any]]:
    """Every work item's latest row, scored."""
    return [score_row(row, records[row["record_id"]], gt_index, judges)
            for row in latest_rows(rows).values()]
