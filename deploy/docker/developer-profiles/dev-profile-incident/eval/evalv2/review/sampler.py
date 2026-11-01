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

"""Which records people review: every flagged record plus a stratified sample of the rest (methodology v2.0 §3).

The sample is deterministic: within each stratum, records are ordered by ``sha256(seed:record_id)`` and the
first ``max(min_per_stratum, ceil(fraction * n))`` are taken, so the same seed always picks the same records and
adding a record never reshuffles the others. Strata include the tier by default, so ``ai_validated`` records are
sampled at random too: that is what measures the AI tiers' error rate (:func:`ai_error_rate`). Records already
``human_verified`` are never re-sampled.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from evalv2.core.hashing import sha256_json

DEFAULT_STRATA = ("kind", "tier", "strata.category")


def field_value(record: Mapping[str, Any], dotted: str) -> Any:
    """``record["a"]["b"]`` for ``"a.b"``; ``None`` when any step is missing."""
    value: Any = record
    for part in dotted.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _order(seed: int, record_id: str) -> str:
    return hashlib.sha256(f"{seed}:{record_id}".encode()).hexdigest()


@dataclass(frozen=True)
class ReviewSample:
    """The records chosen for review and the inclusion probability of each stratum."""

    ids: list[str]
    flagged: list[str]
    strata: dict[str, dict[str, Any]]
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def sha256(self) -> str:
        return sha256_json({"ids": self.ids, "flagged": self.flagged, "strata": self.strata, "params": self.params})

    def to_json(self) -> dict[str, Any]:
        return {"ids": self.ids, "flagged": self.flagged, "strata": self.strata, "params": self.params,
                "sha256": self.sha256}


def sample_for_review(records: Sequence[Mapping[str, Any]], *, seed: int, fraction: float = 0.2,
                      strata: Sequence[str] = DEFAULT_STRATA, min_per_stratum: int = 1,
                      include_flagged: bool = True) -> ReviewSample:
    """Every flagged record, plus a stratified sample of the unflagged AI-tier records."""
    if not 0 < fraction <= 1:
        raise ValueError(f"fraction must be in (0, 1], got {fraction}")
    pending = [r for r in records if r["tier"] != "human_verified"]
    flagged = sorted(r["record_id"] for r in pending if include_flagged and r["flags"])
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in pending:
        if record["record_id"] not in flagged:
            key = " | ".join(f"{name}={field_value(record, name)}" for name in strata)
            groups[key].append(record)
    chosen: list[str] = []
    report: dict[str, dict[str, Any]] = {}
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda r: _order(seed, r["record_id"]))
        take = min(len(members), max(min_per_stratum, math.ceil(fraction * len(members))))
        picked = [r["record_id"] for r in members[:take]]
        chosen += picked
        report[key] = {"population": len(members), "sampled": take, "inclusion": take / len(members),
                       "ids": picked}
    params = {"seed": seed, "fraction": fraction, "strata": list(strata), "min_per_stratum": min_per_stratum,
              "include_flagged": include_flagged, "population": len(pending)}
    return ReviewSample(ids=sorted(set(chosen) | set(flagged)), flagged=flagged, strata=report, params=params)


def ai_error_rate(sample: ReviewSample, decisions: Mapping[str, str]) -> dict[str, Any]:
    """The estimated share of unflagged AI-tier records a person would not accept, from the reviewed sample.

    ``decisions`` maps record ids to ``accept``/``edit``/``reject``. Each stratum's error share among its reviewed
    records is weighted by the stratum's population (a stratified estimate); strata with no decided record are
    listed as unestimated. Flagged records were all reviewed, so they are counted, not estimated.
    """
    population = errors = 0.0
    unestimated: list[str] = []
    per_stratum: dict[str, dict[str, Any]] = {}
    for key, stratum in sample.strata.items():
        decided = [decisions[i] for i in stratum["ids"] if i in decisions]
        if not decided:
            unestimated.append(key)
            continue
        share = sum(1 for d in decided if d != "accept") / len(decided)
        per_stratum[key] = {"reviewed": len(decided), "error_share": share, "population": stratum["population"]}
        population += stratum["population"]
        errors += share * stratum["population"]
    flagged_decided = [decisions[i] for i in sample.flagged if i in decisions]
    return {
        "estimated_error_rate": errors / population if population else None,
        "estimated_population": population,
        "per_stratum": per_stratum,
        "unestimated_strata": unestimated,
        "flagged": {"reviewed": len(flagged_decided),
                    "not_accepted": sum(1 for d in flagged_decided if d != "accept"),
                    "total": len(sample.flagged)},
    }
