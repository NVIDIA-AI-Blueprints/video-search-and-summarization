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

"""Chat-reference readiness (offline): is each RA2 record consistent with the frozen ground truth, and who reviews it.

For every record, against its half's ground truth (an incident question's half is its clip's; a library question's
is ``params.corpus``):

- **code recount** (:func:`evalv2.references.chat.recount`): the stored answer (except written facts), support and
  answer space equal a fresh computation. A record that fails is ``inconsistent``.
- **what cannot be recounted:** written facts (``incident.facts``) and a retrieval question's relevance (only its
  target is labelled relevant; whether another incident also fits the description needs a person).
- **review list:** every ``ai_proposed`` record (the blind validator disagreed) plus a 20% sample of the
  ``ai_validated`` ones, stratified by class (the review sampler, seeded), each with the metrics that depend on it.

The report names record ids and classes only, never question or answer text, but it still goes under the private
data root.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from evalv2.references import chat
from evalv2.review.sampler import sample_for_review

REVIEW_STRATA = ("kind", "tier", "strata.class")
REVIEW_FRACTION = 0.2
METRICS_BY_CLASS: dict[str, tuple[str, ...]] = {
    "incident.type": ("answers.exact", "citations"),
    "incident.severity": ("answers.exact", "citations"),
    "incident.window": ("answers.exact", "citations"),
    "incident.people_count": ("answers.exact", "citations"),
    "incident.instrument_list": ("answers.exact", "citations"),
    "incident.asset_list": ("answers.exact", "citations"),
    "incident.instrument_holder": ("answers.judged.holder_identified", "citations"),
    "incident.facts": ("answers.judged.fact_recall", "citations"),
    "incident.unanswerable": ("answers.refusal",),
    "incident.false_premise": ("answers.refusal",),
    "library.type_count": ("answers.exact", "retrieval", "citations"),
    "library.severity_at_least_count": ("answers.exact", "retrieval", "citations"),
    "library.instrument_incidents": ("answers.exact", "retrieval", "citations"),
    "library.most_common_type": ("answers.exact", "retrieval", "citations"),
    "library.retrieval": ("retrieval", "answers.exact", "citations"),
}
NOT_RECOUNTABLE = {"incident.facts": "written facts (re-derived blind, never recounted)",
                   "library.retrieval": "relevance beyond the labelled target (another incident may also fit)"}


def record_half(record: Mapping[str, Any], halves: Mapping[str, Sequence[str]]) -> str | None:
    payload = record["payload"]
    if payload["scope"] == "library":
        return str(payload["params"].get("corpus") or "") or None
    incident = str(payload["params"].get("incident_id"))
    return next((name for name, clips in halves.items() if incident in clips), None)


def readiness(records: Sequence[Mapping[str, Any]], gt_index: Mapping[str, Any], halves: Mapping[str, Sequence[str]],
              *, seed: int, pilot_half: str = "dev") -> dict[str, Any]:
    """The readiness report: consistency per record and class, and the records people must review."""
    gt_halves = {name: {"by_incident_id": {c: gt_index["by_incident_id"][c] for c in sorted(clips)}}
                 for name, clips in halves.items()}
    rows = []
    for record in records:
        payload = record["payload"]
        half = record_half(record, halves)
        problems = (chat.recount(record, gt_halves[half]) if half in gt_halves
                    else [f"{record['record_id']}: no split half"])
        rows.append({"record_id": record["record_id"], "class": payload["class"], "scope": payload["scope"],
                     "half": half, "tier": record["tier"], "flags": list(record.get("flags") or []),
                     "consistent": not problems, "problems": problems,
                     "validator_agrees": (record.get("provenance") or {}).get("validator", {}).get("agrees"),
                     "answer_in_question": bool(payload.get("leakage", {}).get("answer_in_question")),
                     "not_recountable": NOT_RECOUNTABLE.get(payload["class"]),
                     "metrics": list(METRICS_BY_CLASS.get(payload["class"], ()))})
    sample = sample_for_review(records, seed=seed, fraction=REVIEW_FRACTION, strata=REVIEW_STRATA)
    proposed = {r["record_id"] for r in records if r["tier"] == "ai_proposed"}
    review_ids = sorted(set(sample.ids) | proposed)
    by_id = {r["record_id"]: r for r in rows}
    review = [{"record_id": rid, "class": by_id[rid]["class"], "half": by_id[rid]["half"], "tier": by_id[rid]["tier"],
               "reason": ("ai_proposed: validator disagreed" if rid in proposed
                          else "ai_validated: stratified 20% sample"),
               "consistent": by_id[rid]["consistent"], "metrics": by_id[rid]["metrics"]} for rid in review_ids]
    by_class: dict[str, dict[str, int]] = {}
    for row in rows:
        entry = by_class.setdefault(row["class"], Counter())
        entry["records"] += 1
        entry["consistent" if row["consistent"] else "inconsistent"] += 1
        entry[row["tier"]] += 1
        entry[f"{row['half']}_half"] += 1
    review_by_class = Counter(r["class"] for r in review)
    for cls, n in review_by_class.items():
        by_class[cls]["needs_review"] = n
    pilot = [r for r in rows if r["half"] == pilot_half]
    pilot_review = [r for r in review if r["half"] == pilot_half]
    return {
        "records": len(rows),
        "tiers": dict(Counter(r["tier"] for r in rows)),
        "human_verified": sum(1 for r in rows if r["tier"] == "human_verified"),
        "consistent": sum(1 for r in rows if r["consistent"]),
        "inconsistent": [r for r in rows if not r["consistent"]],
        "not_recountable": dict(Counter(r["class"] for r in rows if r["not_recountable"])),
        "answer_in_question": sum(1 for r in rows if r["answer_in_question"]),
        "by_class": {cls: dict(sorted(counts.items())) for cls, counts in sorted(by_class.items())},
        "needs_review": {"total": len(review), "ai_proposed": len(proposed),
                         "ai_validated_sample": len(review) - len(proposed),
                         "sample": {"seed": seed, "fraction": REVIEW_FRACTION, "strata": list(REVIEW_STRATA),
                                    "sha256": sample.sha256},
                         "by_class": dict(sorted(review_by_class.items())),
                         "by_metric": dict(sorted(Counter(m for r in review for m in r["metrics"]).items())),
                         "records": review},
        "pilot_half": {"half": pilot_half, "records": len(pilot),
                       "consistent": sum(1 for r in pilot if r["consistent"]),
                       "needs_review": len(pilot_review),
                       "needs_review_by_class": dict(sorted(Counter(r["class"] for r in pilot_review).items()))},
        "disclosure": "Every reference is AI-generated (ai_validated or ai_proposed; 0 human_verified), and every "
                      "question is derived from the ground truth it is scored against (a disclosed limitation).",
    }
