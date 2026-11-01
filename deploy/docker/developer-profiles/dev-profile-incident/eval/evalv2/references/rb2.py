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

"""RB2, the dashboard-reference run ("Ask for a chart", methodology v2.0 §4 E, R4): chosen specs to request records.

The run makes no model call itself (:mod:`evalv2.references.passes`) and resumes where it stopped.

- **The plan** (:func:`spec_plan`) is deterministic for a seed. Every allowlisted dimension gets one spec per filter
  pattern (none, an incident type, a severity, both; a dimension that takes no filter gets two unfiltered specs),
  with the periods cycled so each appears about equally often. Unsupported requests make up ``unsupported_share``
  of the set. Each carries its own hint (``unsupported_kind`` in the generator packet), one of
  :data:`UNSUPPORTED_HINTS`, which spread over every kind the prompt lists; distinct hints keep two packets from being
  the same, so two sessions never write one request twice by construction.
- **Each request:** the generator words it for its code-chosen spec; the record is built
  (:func:`evalv2.references.dashboard.build_record`); the validator packet (the request and the allowlist only) is
  checked with ``assert_blind``; the blind re-derived spec must equal the expected one exactly. The validator's
  answer is kept in its provenance for review.
- **Figures:** :func:`corpus_figures` builds the evaluation corpus as a dashboard on a frozen clock
  (:func:`evalv2.references.dashboard_snapshot.corpus_snapshot`, private) and counts the planned specs whose
  reference chart has rows there, so a spec that can only ever show an empty chart is visible before the run.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from evalv2.core.hashing import canonical_json, sha256_json
from evalv2.core.private import ensure_private
from evalv2.datasets.records import (
    RecordError,
    split_by_tier,
    validate_record,
    write_records,
)
from evalv2.references import dashboard, dashboard_figures, dashboard_snapshot
from evalv2.references.passes import Pass, packet_sha256

GENERATOR_PROMPT = "gen-dashboard-request-v1"
VALIDATOR_PROMPT = "gen-dashboard-respec-v1"
PATTERNS = ("none", "type", "severity", "both")
# One hint per unsupported request, over every kind gen-dashboard-request-v1 lists as unanswerable with the
# allowlist (another period; a location, camera or person filter; a field not listed; when incidents happened; a
# request to change or invent figures; not a chart request). Cycled when more requests are asked for.
UNSUPPORTED_HINTS = (
    "another period: a named month", "another period: a specific year", "another period: an exact date range",
    "a location or site filter", "a camera filter", "a filter on a described person",
    "a field the allowlist does not list, such as the weather",
    "a field the allowlist does not list, such as a vehicle's colour",
    "when incidents happened: their time of day", "when incidents happened: their own date",
    "a request to change the figures", "a request to invent figures", "not a chart request: a written summary",
    "not a chart request: an action on reports", "a filter on model confidence")
DEFAULT_UNSUPPORTED_SHARE = 0.2


def spec_plan(allowlist: Mapping[str, Any], *, seed: int,
              unsupported_share: float = DEFAULT_UNSUPPORTED_SHARE) -> list[dict[str, Any]]:
    """The requests to make: ``{"expected", "unsupported_kind"}``, every expected spec valid and distinct."""
    if not 0 <= unsupported_share < 1:
        raise ValueError("unsupported_share must be in [0, 1)")
    periods, types, severities = list(allowlist["periods"]), allowlist["incidentTypes"], allowlist["severities"]
    planned: list[dict[str, Any]] = []
    seen: set[str] = set()
    turn = 0
    for name, dimension in allowlist["dimensions"].items():
        patterns = PATTERNS if dimension["filterable"] else ("none", "none")
        for index, pattern in enumerate(patterns):
            key = sha256_json({"seed": seed, "dimension": name, "pattern": pattern, "index": index})
            kind = types[int(key[:8], 16) % len(types)] if pattern in ("type", "both") else None
            severity = severities[int(key[8:16], 16) % len(severities)] if pattern in ("severity", "both") else None
            for _ in periods:  # the next period that gives a spec not yet planned
                spec = {"metric": dimension["metric"], "dimension": name, "period": periods[turn % len(periods)],
                        "incidentType": kind, "severity": severity}
                turn += 1
                if dashboard.parse_spec(spec, allowlist) == spec and sha256_json(spec) not in seen:
                    seen.add(sha256_json(spec))
                    planned.append({"expected": spec, "unsupported_kind": None})
                    break
    unsupported = round(len(planned) * unsupported_share / (1 - unsupported_share))
    planned += [{"expected": dict(dashboard.UNSUPPORTED),
                 "unsupported_kind": UNSUPPORTED_HINTS[i % len(UNSUPPORTED_HINTS)], "index": i}
                for i in range(unsupported)]
    return planned


def _generator_packet(entry: Mapping[str, Any]) -> dict[str, Any]:
    packet = dashboard.generator_packet(entry["expected"], prompt_id=GENERATOR_PROMPT)
    if entry.get("unsupported_kind"):
        packet["unsupported_kind"] = entry["unsupported_kind"]
    return packet


def run(allowlist: Mapping[str, Any], planned: Sequence[Mapping[str, Any]], *, generator: Pass, validator: Pass,
        out: Path, dataset: str) -> dict[str, Any]:
    """Advance every planned request as far as the answers allow; write the records and the summary."""
    out = Path(out)
    ensure_private(out)
    records: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    problems: list[dict[str, str]] = []
    ids: set[str] = set()
    for entry in planned:
        gen_packet = _generator_packet(entry)
        key = sha256_json({k: entry.get(k) for k in ("expected", "unsupported_kind", "index")})[:12]
        reply = generator.answer(f"rb2-gen-{key}", gen_packet)
        if reply is None:
            counts["pending_generator"] += 1
            continue
        generated = {**dict(generator.identity), "packet_sha256": packet_sha256(gen_packet)}
        try:
            if not isinstance(reply, Mapping) or not isinstance(reply.get("request"), str):
                raise dashboard.DashboardReferenceError("a generator answer is {\"request\", \"paraphrases\"}")
            request, paraphrases = reply["request"], [str(p) for p in reply.get("paraphrases") or []]
            proposed = dashboard.build_record(request, entry["expected"], allowlist, paraphrases=paraphrases,
                                              dataset=dataset, generator=generated)
            val_packet = dashboard.validator_packet(proposed, allowlist, prompt_id=VALIDATOR_PROMPT)
            dashboard.assert_blind(val_packet, proposed)
        except (dashboard.DashboardReferenceError, RecordError) as exc:
            problems.append({"request": key, "stage": "generator_answer", "problem": str(exc)[:200]})
            continue
        counts["blind_checks_passed"] += 1
        rederived = validator.answer(f"rb2-val-{sha256_json(proposed['record_id'])[:12]}", val_packet)
        if rederived is None:
            counts["pending_validator"] += 1
            continue
        record = dashboard.build_record(request, entry["expected"], allowlist, paraphrases=paraphrases,
                                        dataset=dataset, generator=generated, rederived=rederived,
                                        validator={**dict(validator.identity), "answer": rederived,
                                                   "packet_sha256": packet_sha256(val_packet)})
        if record["record_id"] in ids:  # two generators wrote the same request for the same expectation
            problems.append({"request": record["record_id"], "stage": "record", "problem": "duplicate request"})
            continue
        ids.add(record["record_id"])
        records.append(record)
    invalid = [p for record in records for p in validate_record(record)]
    if invalid:
        raise ValueError("; ".join(invalid[:10]))
    digest = write_records(records, out / "records.jsonl") if records else None
    specs = [e["expected"] for e in planned if not dashboard.is_refusal(e["expected"])]
    summary = {
        "run": "RB2", "dataset": dataset, "records_sha256": digest,
        "planned": {"specs": len(specs), "unsupported": len(planned) - len(specs), "total": len(planned),
                    "valid_specs_on_allowlist": len(dashboard.all_specs(allowlist))},
        "planned_specs": {"by_dimension": dict(Counter(s["dimension"] for s in specs)),
                          "by_period": dict(Counter(s["period"] for s in specs)),
                          "with_type_filter": sum(1 for s in specs if s["incidentType"]),
                          "with_severity_filter": sum(1 for s in specs if s["severity"])},
        "unsupported_kinds": dict(Counter(e["unsupported_kind"] for e in planned if e.get("unsupported_kind"))),
        "records": len(records),
        "tiers": {tier: sum(1 for r in records if r["tier"] == tier)
                  for tier in ("ai_proposed", "ai_validated", "human_verified")},
        "report_groups": {group: len(rows) for group, rows in split_by_tier(records).items()},
        "flags": dict(Counter(flag for record in records for flag in record["flags"])),
        "counts": dict(counts), "problems": problems,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def corpus_figures(reports: Mapping[str, Mapping[str, Any]], specs: Sequence[Mapping[str, Any]],
                   allowlist: Mapping[str, Any], *, taken_at: str, model_name: str, out: Path,
                   timezone: str = "UTC") -> dict[str, Any]:
    """Write the corpus snapshot (private) and count the specs whose reference bar chart has rows on it."""
    snapshot = dashboard_snapshot.corpus_snapshot(reports, taken_at=taken_at, model_name=model_name)
    path = Path(out) / "corpus-snapshot.json"
    ensure_private(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(snapshot))
    now = datetime.fromisoformat(taken_at)

    def fetch(query: Mapping[str, Any]) -> dict[str, Any]:
        return dashboard_figures.dashboard(snapshot, query, now=now)

    kinds: Counter[str] = Counter()
    for spec in specs:
        chart = dashboard_figures.chart(spec, chart_type="bar", timezone=timezone, now=now, allowlist=allowlist,
                                        fetch=fetch)
        if chart["kind"] != "chart":
            kinds[chart["kind"]] += 1
        else:
            kinds["chart_with_rows" if chart["rows"] else "chart_empty"] += 1
    return {"snapshot_sha256": sha256_json(snapshot), "taken_at": taken_at,
            "rows": {table: len(rows) for table, rows in snapshot["tables"].items()}, "reference_charts": dict(kinds)}
