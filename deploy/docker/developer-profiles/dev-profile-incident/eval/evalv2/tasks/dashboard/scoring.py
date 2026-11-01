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

"""Spec scorers for "Ask for a chart": a subject's interpreter reply against a dashboard reference.

Each reply is one outcome:

- ``exact`` (the expected spec) or ``refusal_correct`` (an unsupported request refused);
- ``valid_but_wrong`` (an allowlisted spec, not the expected one), ``false_refusal`` (a supported request refused),
  ``false_accept`` (an unsupported request given a spec);
- ``parse_failure`` (no object, unknown keys or values, or an inconsistent spec).

Per-field accuracy compares the five spec fields when the reply is a valid spec; stability is the share of requests
whose repeats all give the same outcome and spec. Results are kept per reference tier (``primary`` is
``human_verified``). The chart type is not scored here: the user picks it, so it is a rule with unit tests.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from evalv2.datasets.records import effective_payload
from evalv2.references.dashboard import SPEC_KEYS, is_refusal, parse_spec

OUTCOMES = ("exact", "refusal_correct", "valid_but_wrong", "false_refusal", "false_accept", "parse_failure")
TIER_GROUPS = {"human_verified": "primary", "ai_validated": "ai_validated", "ai_proposed": "ai_proposed"}


def outcome(reply: Any, expected: Mapping[str, Any], allowlist: Mapping[str, Any]) -> tuple[str, dict[str, Any] | None]:
    spec = parse_spec(reply, allowlist)
    if is_refusal(expected):
        if is_refusal(reply):
            return "refusal_correct", None
        return ("false_accept", spec) if spec is not None else ("parse_failure", None)
    if is_refusal(reply):
        return "false_refusal", None
    if spec is None:
        return "parse_failure", None
    return ("exact" if spec == dict(expected) else "valid_but_wrong"), spec


def score_reply(record: Mapping[str, Any], reply: Any, allowlist: Mapping[str, Any], *, repeat: int = 1
                ) -> dict[str, Any]:
    payload = effective_payload(record)
    if payload is None:
        return {"record_id": record["record_id"], "status": "reference_rejected"}
    expected = payload["expected"]
    result, spec = outcome(reply, expected, allowlist)
    fields = ({key: spec[key] == expected[key] for key in SPEC_KEYS}
              if spec is not None and not is_refusal(expected) else None)
    return {"record_id": record["record_id"], "status": "scored", "tier": record["tier"], "repeat": repeat,
            "class": "unsupported" if is_refusal(expected) else "spec", "outcome": result, "spec": spec,
            "fields": fields}


def _rate(rows: list[Mapping[str, Any]], *names: str) -> float | None:
    return round(sum(1 for r in rows if r["outcome"] in names) / len(rows), 4) if rows else None


def _summary(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    supported = [r for r in rows if r["class"] == "spec"]
    unsupported = [r for r in rows if r["class"] == "unsupported"]
    refused = [r for r in rows if r["outcome"] in ("refusal_correct", "false_refusal")]
    with_fields = [r for r in supported if r["fields"] is not None]
    by_record: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        by_record[r["record_id"]].add(f"{r['outcome']}|{sorted((r['spec'] or {}).items())}")
    repeated = {k: v for k, v in by_record.items() if sum(1 for r in rows if r["record_id"] == k) > 1}
    return {
        "replies": len(rows),
        "exact_spec_accuracy": _rate(supported, "exact"),
        "valid_but_wrong_rate": _rate(supported, "valid_but_wrong"),
        "parse_failure_rate": _rate(rows, "parse_failure"),
        "refusal_precision": round(sum(1 for r in refused if r["outcome"] == "refusal_correct") / len(refused), 4)
        if refused else None,
        "refusal_recall": _rate(unsupported, "refusal_correct"),
        "field_accuracy": {key: round(sum(1 for r in with_fields if r["fields"][key]) / len(with_fields), 4)
                           for key in SPEC_KEYS} if with_fields else None,
        "stability": round(sum(1 for v in repeated.values() if len(v) == 1) / len(repeated), 4) if repeated else None,
        "outcomes": dict(Counter(r["outcome"] for r in rows)),
    }


def aggregate(scores: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    scores = list(scores)
    rows = [s for s in scores if s["status"] == "scored"]
    out: dict[str, Any] = {"rejected_references": sum(1 for s in scores if s["status"] == "reference_rejected")}
    for tier, group in TIER_GROUPS.items():
        members = [r for r in rows if r["tier"] == tier]
        if members:
            out[group] = _summary(members)
    return out
