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

"""Track B crop wiring: A8's crop scorers on the evidence each stored pipeline run returned (methodology v2.0 §4 C).

- **Matching.** The evidence entries describe the final report's items, so an entry reaches its ground-truth item
  through the ``final`` stage's item matching. The zoomed second look rewrites descriptions, which can change the
  matching; item ids never change. A run without a final report has no evidence and adds nothing.
- **References.** The box-reference records (A7) of the run's clip, keyed by kind and ground-truth item id. A
  person's rejection or edit applies, because A8 reads the effective payload.
- **Caps.** A capped item is unscorable, not a miss (A8). :func:`cap_summary` reports how often each cap refused
  calls (crop, zoom, the object tracker's detector budget and its time cap), so the pilot can recalibrate the
  provisional profile caps before the freeze. The tracker's summary is the run's top-level ``object_tracking``
  (feat/experiment-profiles keeps ``experiment.object_tracking`` as the request's boolean setting).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from evalv2.tasks.crops import scoring as crop_scoring
from evalv2.tasks.incident_objects import v1

PLURAL = {"entity": "entities", "instrument": "instruments", "asset": "assets"}


def item_matches(scored: Mapping[str, Any] | None) -> dict[str, dict[str, str]]:
    """``{kind: {predicted id: ground-truth id}}`` from one scored stage (A6's ``score_video`` output)."""
    if scored is None:
        return {}
    out = {}
    for kind in v1.KINDS:
        id_field = v1.ID_FIELD[kind]
        out[kind] = {str(m[id_field]): str(m[f"gt_{id_field}"]) for m in (scored.get(kind) or {}).get("matches") or []}
    return out


def reference_index(records: Iterable[Mapping[str, Any]], incident_id: str) -> dict[tuple[str, str], Mapping[str, Any]]:
    """The box references of one clip, keyed ``(kind, ground-truth item id)`` as A8 expects; duplicates refused."""
    index: dict[tuple[str, str], Mapping[str, Any]] = {}
    for record in records:
        payload = record.get("payload") or {}
        if record.get("kind") != "box" or payload.get("clip") != incident_id:
            continue
        key = (PLURAL[payload["kind"]], str(payload["item_id"]))
        if key in index:
            raise ValueError(f"two box references for {incident_id} {key}: {index[key]['record_id']} and "
                             f"{record['record_id']}")
        index[key] = record
    return index


def score_run(artifact: Mapping[str, Any], scored_final: Mapping[str, Any] | None,
              references: Mapping[tuple[str, str], Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every evidence entry of one stored Track B run, scored by A8 through the final stage's matching."""
    if scored_final is None:
        return []
    return crop_scoring.score_video(artifact.get("evidence") or [], references, item_matches(scored_final))


def _budget(summary: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    return summary if isinstance(summary, Mapping) and "cap" in summary else None


def _tracker(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    """The tracker's summary: the run's top-level ``object_tracking``, else an older reply's summary under
    ``experiment``. The request's boolean setting (``experiment.object_tracking: true``) is not a summary."""
    for candidate in (artifact.get("object_tracking"), (artifact.get("experiment") or {}).get("object_tracking")):
        if isinstance(candidate, Mapping):
            return candidate
    return {}


def cap_summary(artifacts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Per cap, over the runs that reported it: how many runs reached it, the calls it refused, and the most calls one
    run made (against the cap)."""
    out: dict[str, Any] = {"runs": 0}
    for artifact in artifacts:
        out["runs"] += 1
        experiment = artifact.get("experiment") or {}
        tracking = _tracker(artifact)
        budgets = {"crop": _budget(experiment.get("crop")), "zoom": _budget(experiment.get("zoom")),
                   "object_tracking_detector": _budget(tracking.get("detector"))}
        for name, summary in budgets.items():
            if summary is None:
                continue
            row = out.setdefault(name, {"runs": 0, "cap_reached": 0, "refused_calls": 0, "max_calls": 0, "caps": []})
            row["runs"] += 1
            row["cap_reached"] += int(bool(summary.get("cap_reached")))
            row["refused_calls"] += int(summary.get("refused_by_cap") or 0)
            row["max_calls"] = max(row["max_calls"], int(summary.get("calls") or 0))
            if summary["cap"] not in row["caps"]:
                row["caps"].append(summary["cap"])
        if tracking:
            row = out.setdefault("object_tracking_time", {"runs": 0, "cap_reached": 0})
            row["runs"] += 1
            row["cap_reached"] += int(bool(tracking.get("time_cap_reached")))
    return out
