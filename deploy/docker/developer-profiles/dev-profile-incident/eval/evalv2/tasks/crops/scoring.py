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

"""Crop scorers: one predicted evidence entry against the box reference of the ground-truth item it matched.

For each evidence entry of a predicted item that matched a ground-truth item (Track A's item matching), and whose
item has a box reference, three questions are scored:

- **operational:** did the evidence step locate a box at all? An entry refused by the crop-call cap, or skipped for
  a missing detector or image store, is *unscorable*, never a miss (that is a budget or setup fact, not a model
  error); "not located at any second tried" is a miss.
- **temporal:** is the evidence second (``box_seconds``, else ``timestamp_seconds``) one where the reference sees
  the item?
- **same-second IoU:** the predicted box against the reference box at that same second; correct evidence is a
  located box at a visible second with IoU >= 0.5.

The agent stores the detector box padded by 5% (``bounding_box``). A recorded unpadded ``detected_box`` is used
when present; otherwise the padding is inverted (exact away from the frame edges) and the score says so.
Results are kept per reference tier: primary metrics use ``human_verified`` references only.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from evalv2.datasets.records import effective_payload
from evalv2.references.boxes import AGREEMENT_IOU, SCALE, iou, valid_box

PADDING = 0.05  # crop_evidence.apply_box_padding's default for the stored evidence box
UNSCORABLE_GAPS = ("crop-call cap reached", "no evidence detector is configured", "no evidence image store",
                   "crop failed")
TIERS = ("human_verified", "ai_validated", "ai_proposed")


def unpad(box: Sequence[float], padding: float = PADDING) -> list[float]:
    """The detector box inside a box padded by ``padding`` of its size on each side, clamped to the frame.

    A side that does not touch the frame edge was padded by ``padding * size``; a side on the edge may have been
    clamped, so only the other side is undone there.
    """
    ymin, xmin, ymax, xmax = (float(v) for v in box)
    out = []
    for low, high in ((ymin, ymax), (xmin, xmax)):
        at_low, at_high = low <= 0, high >= SCALE
        if not at_low and not at_high:
            size = (high - low) / (1 + 2 * padding)
            pad = size * padding
            out.append((low + pad, high - pad))
        elif at_low and at_high:
            out.append((low, high))
        else:
            size = (high - low) / (1 + padding)
            pad = size * padding
            out.append((low, high - pad) if at_low else (low + pad, high))
    (y0, y1), (x0, x1) = out
    return [y0, x0, y1, x1]


def predicted_box(entry: Mapping[str, Any]) -> tuple[list[float] | None, str | None]:
    """The box to score and where it came from: ``detected``, ``unpadded_estimate``, or ``None``."""
    if entry.get("detected_box") and valid_box(entry["detected_box"]):
        return [float(v) for v in entry["detected_box"]], "detected"
    if entry.get("bounding_box") and valid_box(entry["bounding_box"]):
        return unpad(entry["bounding_box"]), "unpadded_estimate"
    return None, None


def evidence_second(entry: Mapping[str, Any]) -> int | None:
    second = entry.get("box_seconds")
    second = entry.get("timestamp_seconds") if second is None else second
    return None if second is None else round(float(second))


def score_entry(entry: Mapping[str, Any], reference: Mapping[str, Any] | None) -> dict[str, Any]:
    """One evidence entry against its reference record (``None`` when the matched item has no reference)."""
    base = {"item_type": entry.get("item_type"), "item_id": entry.get("item_id")}
    gap = str(entry.get("image_gap") or "")
    if any(gap.startswith(prefix) for prefix in UNSCORABLE_GAPS) and entry.get("located") is not True:
        return {**base, "status": "unscorable", "reason": gap}
    if reference is None:
        return {**base, "status": "no_reference"}
    payload = effective_payload(reference)
    if payload is None:
        return {**base, "status": "reference_rejected", "tier": reference["tier"]}
    tier, boxes = reference["tier"], payload.get("boxes") or {}
    second = evidence_second(entry)
    visible = second is not None and str(second) in boxes
    box, source = predicted_box(entry)
    if entry.get("located") is False or box is None:
        return {**base, "status": "scored", "tier": tier, "located": False, "temporal_hit": visible,
                "iou": None, "correct": False, "second": second}
    overlap = iou(box, boxes[str(second)]) if visible else None
    return {**base, "status": "scored", "tier": tier, "located": True, "temporal_hit": visible, "iou": overlap,
            "correct": bool(visible and overlap is not None and overlap >= AGREEMENT_IOU), "second": second,
            "box_source": source}


def score_video(evidence: Iterable[Mapping[str, Any]], references: Mapping[tuple[str, str], Mapping[str, Any]],
                item_matches: Mapping[str, Mapping[str, str]]) -> list[dict[str, Any]]:
    """Every evidence entry of one video run.

    ``item_matches`` maps each kind (``entities``, ``instruments``, ``assets``) to ``{predicted id: ground-truth
    id}``, from the incident-and-object scoring; ``references`` maps ``(kind, ground-truth id)`` to a record. An
    entry whose item matched no ground-truth item is ``unmatched`` (scored by the object metrics, not here).
    """
    kind_of = {"entity": "entities", "instrument": "instruments", "asset": "assets"}
    out = []
    for entry in evidence:
        kind = kind_of.get(str(entry.get("item_type")))
        gt_id = (item_matches.get(kind) or {}).get(str(entry.get("item_id"))) if kind else None
        if gt_id is None:
            out.append({"item_type": entry.get("item_type"), "item_id": entry.get("item_id"), "status": "unmatched"})
            continue
        out.append({**score_entry(entry, references.get((kind, gt_id))), "gt_item_id": gt_id})
    return out


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def aggregate(scores: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Per reference tier: located, temporal and correct-evidence rates and the mean same-second IoU.

    ``primary`` is the ``human_verified`` tier; the AI tiers are reported beside it and never pooled with it.
    Unscorable, unmatched, rejected and unreferenced entries are counted, not scored.
    """
    scores = list(scores)
    status = Counter(str(s["status"]) for s in scores)
    by_tier: dict[str, Any] = {}
    for tier in TIERS:
        rows = [s for s in scores if s["status"] == "scored" and s.get("tier") == tier]
        ious = [s["iou"] for s in rows if s.get("iou") is not None]
        by_tier[tier] = {
            "n": len(rows), "located_rate": _rate(sum(1 for s in rows if s["located"]), len(rows)),
            "temporal_hit_rate": _rate(sum(1 for s in rows if s["temporal_hit"]), len(rows)),
            "correct_evidence_rate": _rate(sum(1 for s in rows if s["correct"]), len(rows)),
            "mean_iou_same_second": round(sum(ious) / len(ious), 4) if ious else None,
            "estimated_boxes": sum(1 for s in rows if s.get("box_source") == "unpadded_estimate"),
        }
    return {"entries": len(scores), "status": dict(status), "primary": by_tier["human_verified"],
            "by_tier": by_tier, "unscorable_reasons": dict(Counter(s.get("reason", "") for s in scores
                                                                  if s["status"] == "unscorable"))}
