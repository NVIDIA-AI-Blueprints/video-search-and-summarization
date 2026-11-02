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

"""RA2, the chat-reference run (methodology v2.0 §3, R3): a seeded question plan to blind-validated records.

The run makes no model call itself (:mod:`evalv2.references.passes`) and resumes where it stopped.

- **Halves stay apart.** Each split half is its own library: an incident question is about one incident of the
  half, and a library question counts or searches only the half's reports (its params name ``corpus``). The dev
  questions serve the pilots and the test questions the frozen runs, and no answer about one half depends on the
  other.
- **The plan** (:func:`question_plan`) is deterministic for a seed. Each incident class takes up to ``per_class``
  incidents, in an order that cycles through the categories with a seed of its own, so classes spread over the
  half's incidents. Only an incident with instruments gets an instrument-list question, only one with assets an
  asset-list question, and a holder question names an instrument with a holder. Library questions: a count per
  incident type, at-least counts for severity 2 to 5, the incidents naming each of the most common instruments, the
  most common type, and retrieval of one incident per category.
- **Each question:** the generator words it (and writes atomic facts for ``incident.facts``); the record is built
  with its code-computed answer, support and answer space (:func:`evalv2.references.chat.build_record`); the
  validator packet is checked with ``assert_blind``; the blind re-derivation then decides the tier with the code
  checks (recount, answer not stated in the question, distinct paraphrases). Leakage is part of every record, and
  the validator's own answer is kept in its provenance, so a reviewer of a disagreement sees both.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evalv2.core.hashing import sha256_json
from evalv2.core.private import ensure_private
from evalv2.datasets.records import (
    RecordError,
    split_by_tier,
    validate_record,
    write_records,
)
from evalv2.references import chat
from evalv2.references.passes import Pass, packet_sha256

GENERATOR_PROMPT = "gen-chat-wording-v1"
VALIDATOR_PROMPT = "gen-chat-rederive-v1"
INCIDENT_CLASSES = tuple(c for c, scope in chat.CLASSES.items() if scope == "incident")
LIBRARY_CLASSES = tuple(c for c, scope in chat.CLASSES.items() if scope == "library")
SEVERITY_LEVELS_ASKED = (2, 3, 4, 5)  # "at least 1" counts every incident
DEFAULT_PER_CLASS = {"dev": 5, "test": 10}


def restrict(gt_index: Mapping[str, Any], clips: Sequence[str]) -> dict[str, Any]:
    """The ground-truth index of one half: the library a half's questions are asked about."""
    return {"by_incident_id": {clip: gt_index["by_incident_id"][clip] for clip in sorted(clips)}}


def _order(gt_half: Mapping[str, Any], *, seed: int, half: str, label: str) -> list[str]:
    """The half's incidents in a seeded order cycling through the categories, so every prefix is balanced."""
    by_category: dict[str, list[str]] = {}
    for clip, entry in sorted(gt_half["by_incident_id"].items()):
        by_category.setdefault(str(entry["category"]), []).append(clip)
    queues = []
    for category in sorted(by_category):
        members = list(by_category[category])
        random.Random(f"{seed}:{half}:{label}:{category}").shuffle(members)
        queues.append(members)
    out: list[str] = []
    while any(queues):
        for queue in queues:
            if queue:
                out.append(queue.pop(0))
    return out


def question_plan(gt_half: Mapping[str, Any], *, half: str, seed: int, per_class: int, instruments: int = 5,
                  retrievals: int = 5) -> list[dict[str, Any]]:
    """The questions of one half: ``{"class", "params"}`` in a fixed order."""
    reports = chat.oracle_corpus(gt_half)
    planned: list[dict[str, Any]] = []
    for question_class in INCIDENT_CLASSES:
        order = _order(gt_half, seed=seed, half=half, label=question_class)
        if question_class == "incident.instrument_holder":
            eligible = [{"incident_id": clip, "instrument_id": str(i["instrument_id"])} for clip in order
                        for i in reports[clip]["instruments"] if i.get("entity_id")]
        elif question_class == "incident.instrument_list":
            eligible = [{"incident_id": clip} for clip in order if reports[clip]["instruments"]]
        elif question_class == "incident.asset_list":
            eligible = [{"incident_id": clip} for clip in order if reports[clip]["assets"]]
        else:
            eligible = [{"incident_id": clip} for clip in order]
        planned += [{"class": question_class, "params": params} for params in eligible[:per_class]]
    for kind in sorted({str(r["incident"]["type"]) for r in reports.values() if r["incident"].get("type")}):
        planned.append({"class": "library.type_count", "params": {"type": kind, "corpus": half}})
    for level in SEVERITY_LEVELS_ASKED:
        planned.append({"class": "library.severity_at_least_count", "params": {"level": level, "corpus": half}})
    naming = Counter(name for r in reports.values()
                     for name in {chat.normalise(i.get("name")) for i in r["instruments"]} if name)
    for name, _ in sorted(naming.items(), key=lambda kv: (-kv[1], kv[0]))[:instruments]:
        planned.append({"class": "library.instrument_incidents", "params": {"instrument": name, "corpus": half}})
    planned.append({"class": "library.most_common_type", "params": {"corpus": half}})
    for clip in _order(gt_half, seed=seed, half=half, label="library.retrieval")[:retrievals]:
        planned.append({"class": "library.retrieval", "params": {"targets": [clip], "corpus": half}})
    return planned


def _wording(reply: Any) -> tuple[str, list[str], list[str] | None]:
    if not isinstance(reply, Mapping) or not isinstance(reply.get("question"), str):
        raise chat.ChatReferenceError("a generator answer is {\"question\", \"paraphrases\", \"facts\"?}")
    paraphrases = reply.get("paraphrases") or []
    facts = reply.get("facts")
    if not isinstance(paraphrases, list) or (facts is not None and not isinstance(facts, list)):
        raise chat.ChatReferenceError("paraphrases and facts are lists")
    return reply["question"], [str(p) for p in paraphrases], [str(f) for f in facts] if facts is not None else None


def run(gt_halves: Mapping[str, Mapping[str, Any]], plans: Mapping[str, Sequence[Mapping[str, Any]]], *,
        generator: Pass, validator: Pass, out: Path, dataset: str) -> dict[str, Any]:
    """Advance every planned question as far as the answers allow; write the records and the summary."""
    out = Path(out)
    ensure_private(out)
    records: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    problems: list[dict[str, str]] = []
    record_half: dict[str, str] = {}
    for half, planned in plans.items():
        gt_half = gt_halves[half]
        for entry in planned:
            question_class, params = entry["class"], dict(entry["params"])
            gen_packet = chat.generator_packet(question_class, params, gt_half, prompt_id=GENERATOR_PROMPT)
            key = sha256_json({"class": question_class, "params": params})[:12]
            reply = generator.answer(f"ra2-gen-{half}-{key}", gen_packet)
            if reply is None:
                counts["pending_generator"] += 1
                continue
            generated = {**dict(generator.identity), "packet_sha256": packet_sha256(gen_packet)}
            try:
                question, paraphrases, facts = _wording(reply)
                proposed = chat.build_record(question_class, params, gt_half, question=question,
                                             paraphrases=paraphrases, dataset=dataset, generator=generated, facts=facts)
                val_packet = chat.validator_packet(proposed, gt_half, prompt_id=VALIDATOR_PROMPT)
                chat.assert_blind(val_packet, proposed)
            except (chat.ChatReferenceError, RecordError) as exc:
                problems.append({"question": f"{half}:{question_class}:{key}", "stage": "generator_answer",
                                 "problem": str(exc)[:200]})
                continue
            counts["blind_checks_passed"] += 1
            rederived = validator.answer(f"ra2-val-{half}-{sha256_json(proposed['record_id'])[:12]}", val_packet)
            if rederived is None:
                counts["pending_validator"] += 1
                continue
            if not isinstance(rederived, Mapping):
                problems.append({"question": proposed["record_id"], "stage": "validator_answer",
                                 "problem": "a re-derivation is an object {\"kind\", \"value\" or \"facts\"}"})
                continue
            record = chat.build_record(question_class, params, gt_half, question=question, paraphrases=paraphrases,
                                       dataset=dataset, generator=generated, facts=facts, rederived=rederived,
                                       validator={**dict(validator.identity), "answer": dict(rederived),
                                                  "packet_sha256": packet_sha256(val_packet)})
            if record["record_id"] in record_half:
                problems.append({"question": record["record_id"], "stage": "record", "problem": "duplicate record id"})
                continue
            record_half[record["record_id"]] = half
            records.append(record)
    invalid = [p for record in records for p in validate_record(record)]
    recount = [p for record in records for p in chat.recount(record, gt_halves[record_half[record["record_id"]]])]
    if invalid or recount:
        raise ValueError("; ".join((invalid + recount)[:10]))
    digest = write_records(records, out / "records.jsonl") if records else None
    by_half_class: dict[str, dict[str, dict[str, int]]] = {}
    for record in records:
        row = by_half_class.setdefault(record_half[record["record_id"]], {}).setdefault(
            record["payload"]["class"], {"records": 0, "ai_validated": 0, "ai_proposed": 0, "human_verified": 0})
        row["records"] += 1
        row[record["tier"]] += 1
    planned_counts = {half: dict(Counter(e["class"] for e in planned)) for half, planned in plans.items()}
    checks = Counter(check["name"] for record in records for check in record["provenance"].get("checks", [])
                     if check.get("passed") is not True)
    overlaps = [record["payload"]["leakage"]["description_overlap"] for record in records]
    summary = {
        "run": "RA2", "dataset": dataset, "records_sha256": digest,
        "planned": planned_counts,
        "planned_by_scope": {half: dict(Counter(chat.CLASSES[e["class"]] for e in planned))
                             for half, planned in plans.items()},
        "records": len(records), "by_half_class": by_half_class,
        "answer_kinds": dict(Counter(record["payload"]["answer"]["kind"] for record in records)),
        "tiers": {tier: sum(1 for r in records if r["tier"] == tier)
                  for tier in ("ai_proposed", "ai_validated", "human_verified")},
        "report_groups": {group: len(rows) for group, rows in split_by_tier(records).items()},
        "flags": dict(Counter(flag for record in records for flag in record["flags"])),
        "failed_checks": dict(checks),
        "validator_disagreed": sum(1 for r in records if r["provenance"]["validator"].get("agrees") is not True),
        "recount_problems": len(recount),
        "leakage": {"answer_in_question": sum(1 for r in records if r["payload"]["leakage"]["answer_in_question"]),
                    "description_overlap_mean": round(sum(overlaps) / len(overlaps), 4) if overlaps else None,
                    "description_overlap_max": max(overlaps) if overlaps else None},
        "counts": dict(counts), "problems": problems,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary
