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

"""One canonical result shape, whichever flow produced it.

The two query flows disagree on exactly one field name, and the disagreement is
dangerous rather than merely annoying:

    legacy REST      result["critic_result"]["result"]
    search_core CLI  result["verification"]["result"]

Both carry the same vocabulary ("confirmed" / "rejected" / "unverified"), so a
reader that knows only the old name does not crash against the new flow -- it
silently sees zero rejections and scores an UNFILTERED result set as if it were
critic-filtered. That produces plausible numbers which are not comparable to the
historical baseline, which is the worst failure mode an eval can have.

:func:`normalize_result` therefore records *which* field it found, and the runner
refuses to emit critic-filtered metrics when the answer is "neither".
"""

from __future__ import annotations

from typing import Any

#: Sentinel for "this hit carried no verification block at all", as distinct
#: from "the critic ran and returned unverified".
VERIFICATION_ABSENT = "absent"


def normalize_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Map one wire-format hit to the canonical shape the metrics consume.

    Accepts either flow's spelling of the verification block and always emits
    ``verification``. ``_verification_source`` records which spelling was found.
    """
    # A present-but-null block is how the agent flow spells "the critic did not
    # run for this hit" (search.py sets critic_result=None on the merge path).
    # It must normalize to ABSENT, not to an empty verdict -- otherwise a
    # response where the critic never ran would still enable critic-filtered
    # metrics in the summary. So test the raw value, not an `or {}` coercion.
    raw_verification = raw.get("verification", raw.get("critic_result"))
    if isinstance(raw_verification, dict):
        source = "verification" if "verification" in raw else "critic_result"
        verification = raw_verification
    else:
        source = VERIFICATION_ABSENT
        verification = {}

    return {
        "video_name": raw.get("video_name") or "",
        "description": raw.get("description") or "",
        "start_time": raw.get("start_time") or "",
        "end_time": raw.get("end_time") or "",
        "sensor_id": raw.get("sensor_id") or "",
        "screenshot_url": raw.get("screenshot_url") or "",
        # search_core names this `similarity`; the embed primitive names it
        # `similarity_score`. Only the unified SearchOutput reaches us today,
        # but accept both so a primitive-level backend needs no change here.
        "similarity": raw.get("similarity", raw.get("similarity_score", 0.0)),
        "object_ids": raw.get("object_ids") or [],
        "verification": {
            "result": verification.get("result", "unverified"),
            "criteria_met": verification.get("criteria_met") or {},
        },
        "_verification_source": source,
        "_raw": raw,
    }


def normalize_results(raw_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize a whole result list."""
    return [normalize_result(r) for r in raw_results]


def verification_sources(results: list[dict[str, Any]]) -> set[str]:
    """Distinct verification field spellings seen across normalized results."""
    return {r.get("_verification_source", VERIFICATION_ABSENT) for r in results}


def has_verification(results: list[dict[str, Any]]) -> bool:
    """True when at least one hit carried a real verification block."""
    return bool(verification_sources(results) - {VERIFICATION_ABSENT})


def filter_rejected(results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Drop results the critic rejected. Operates on NORMALIZED results.

    Deliberately not the same function as ``run_eval.filter_rejected``, which
    reads the legacy field name straight off the wire format.
    """
    kept: list[dict[str, Any]] = []
    rejected = 0
    for r in results:
        if (r.get("verification") or {}).get("result") == "rejected":
            rejected += 1
        else:
            kept.append(r)
    return kept, rejected


def for_scoring(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop bookkeeping keys before the metric code deep-copies each hit."""
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]
