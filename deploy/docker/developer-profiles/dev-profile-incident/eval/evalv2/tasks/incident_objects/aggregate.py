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

"""Track A metrics for incident fields and objects over scored video records (methodology v2.0 §4 A).

- ``continuity_v1`` is computed by V1's own aggregators (field accuracy with its intervals, pooled item P/R/F1,
  the severity summary), so a V2 result can be set beside Stage 3's without a definitional gap.
- The rest is new in V2: type macro-F1 and confusion matrix, severity quadratic-weighted κ, timestamp errors with
  a tolerance curve and the temporal IoU of the incident window, and attribute accuracies.

The rules are V1's: a run without model output (timeout, rate limit, host suspended, HTTP error) is left out and
counted; an invalid output stays in, scored as a miss; the type of a confirmed out-of-taxonomy clip is not an
eligible field. Values are point estimates with their counts; intervals come with the report.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from statistics import median
from typing import Any

from evalv2.review.kappa import weighted_kappa
from evalv2.tasks.incident_objects import v1

TOLERANCES_S = (2, 5, 10)
SEVERITY_LEVELS = (1, 2, 3, 4, 5)
ATTRIBUTES = {"entities": ("type", "description"), "instruments": ("name", "description", "threat_level"),
              "assets": ("name", "description")}


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _seconds(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def type_metrics(videos: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Accuracy, macro-F1 and the confusion matrix (rows: ground truth, columns: predicted).

    Macro-F1 averages the labels with ground-truth support only. A label no clip has (an explosion-free pilot
    subset, say) has no defined recall: its F1 is ``None`` and it is left out of the average, so perfect predictions
    score 1.0 on any subset. A wrong prediction of such a label still costs the true label's recall.
    """
    labels = list(v1.INCIDENT_TYPES)
    confusion = {gt: Counter() for gt in labels}
    for video in videos:
        if not v1.has_model_output(video) or v1.is_out_of_taxonomy(video):
            continue
        entry = (video.get("incident_field_scores") or {}).get("type") or {}
        expected = v1.canonical_incident_type(entry.get("expected"))
        if expected not in confusion:
            continue
        predicted = " ".join(str(entry.get("predicted") or "").split()).lower() or "missing"
        column = predicted if predicted in labels or predicted == "missing" else "other"
        confusion[expected][column] += 1
    n = sum(sum(row.values()) for row in confusion.values())
    support = {label: sum(confusion[label].values()) for label in labels}
    f1s: dict[str, float | None] = {}
    for label in labels:
        tp = confusion[label][label]
        fp = sum(confusion[other][label] for other in labels if other != label)
        fn = support[label] - tp
        f1s[label] = 2 * tp / (2 * tp + fp + fn) if support[label] else None
    supported = [f for f in f1s.values() if f is not None]
    correct = sum(confusion[label][label] for label in labels)
    return {"n": n, "accuracy": _ratio(correct, n),
            "macro_f1": round(sum(supported) / len(supported), 4) if supported else None,
            "macro_f1_labels": [label for label in labels if f1s[label] is not None],
            "per_class_f1": {label: None if f is None else round(f, 4) for label, f in f1s.items()},
            "support": support, "confusion": {gt: dict(row) for gt, row in confusion.items()}}


def severity_metrics(videos: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Quadratic-weighted κ over valid predictions (a level 1-5 on a run with model output)."""
    expected, predicted = {}, {}
    for index, video in enumerate(videos):
        if not v1.has_model_output(video):
            continue
        entry = (video.get("incident_field_scores") or {}).get("severity_level") or {}
        gt, pred = entry.get("expected"), entry.get("predicted")
        if gt in SEVERITY_LEVELS and pred in SEVERITY_LEVELS:
            key = f"{video.get('incident_id')}#{video.get('repeat', 1)}#{index}"
            expected[key], predicted[key] = gt, pred
    kappa = weighted_kappa(expected, predicted, categories=list(SEVERITY_LEVELS), weights="quadratic")
    return {"valid": len(expected), "qwk": round(kappa["kappa"], 4) if kappa["kappa"] is not None else None}


def timestamp_metrics(videos: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Absolute start and end errors (mean, median, share within each tolerance) and the window's temporal IoU."""
    errors: dict[str, list[float]] = {"start_timestamp": [], "end_timestamp": []}
    ious: list[float] = []
    for video in videos:
        if not v1.has_model_output(video):
            continue
        fields = video.get("incident_field_scores") or {}
        pair = {}
        for name in errors:
            entry = fields.get(name) or {}
            gt, pred = _seconds(entry.get("expected")), _seconds(entry.get("predicted"))
            pair[name] = (gt, pred)
            if gt is not None and pred is not None:
                errors[name].append(abs(pred - gt))
        (gs, ps), (ge, pe) = pair["start_timestamp"], pair["end_timestamp"]
        if None not in (gs, ps, ge, pe) and ge > gs and pe >= ps:  # type: ignore[operator]
            overlap = max(0.0, min(ge, pe) - max(gs, ps))  # type: ignore[type-var]
            union = max(ge, pe) - min(gs, ps)  # type: ignore[type-var]
            ious.append(overlap / union if union else 0.0)
    out: dict[str, Any] = {}
    for name, values in errors.items():
        out[name] = {"n": len(values), "mean_abs_error_s": _ratio(sum(values), len(values)),
                     "median_abs_error_s": round(median(values), 4) if values else None,
                     "within": {f"{t}s": _ratio(sum(1 for v in values if v <= t), len(values)) for t in TOLERANCES_S}}
    out["window_iou"] = {"n": len(ious), "mean": _ratio(sum(ious), len(ious))}
    return out


def attribute_metrics(videos: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Per kind and attribute, the pass share over accepted pairs (unscored left out and counted); holder status."""
    out: dict[str, Any] = {}
    for kind, names in ATTRIBUTES.items():
        tallies = {name: Counter() for name in names}
        holder: Counter[str] = Counter()
        for video in videos:
            if not v1.has_model_output(video):
                continue
            for match in (video.get(kind) or {}).get("matches", []):
                scores = match.get("attribute_scores") or {}
                for name in names:
                    passed = (scores.get(name) or {}).get("pass")
                    tallies[name]["unscored" if passed is None else ("pass" if passed else "fail")] += 1
                if kind == "instruments":
                    holder[(scores.get("holder") or {}).get("status", "unresolved")] += 1
        out[kind] = {name: {"pass_rate": _ratio(t["pass"], t["pass"] + t["fail"]), **dict(t)}
                     for name, t in tallies.items()}
        if kind == "instruments":
            out[kind]["holder"] = {**dict(holder), "correct_rate_resolved":
                                   _ratio(holder["correct"], holder["correct"] + holder["incorrect"])}
    return out


def aggregate(videos: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Every Track A incident-and-object metric for one subject's scored video records."""
    videos = list(videos)
    plain = [dict(v) for v in videos]
    return {
        "videos": len(videos),
        "with_model_output": sum(1 for v in videos if v1.has_model_output(v)),
        "outcomes": dict(Counter(str(v.get("outcome")) for v in videos)),
        "continuity_v1": {"fields": v1.field_intervals(plain), "items": v1.pooled_items({"videos": plain}),
                          "severity": v1.severity_summary(plain)},
        "type": type_metrics(videos),
        "severity": severity_metrics(videos),
        "timestamps": timestamp_metrics(videos),
        "attributes": attribute_metrics(videos),
    }
