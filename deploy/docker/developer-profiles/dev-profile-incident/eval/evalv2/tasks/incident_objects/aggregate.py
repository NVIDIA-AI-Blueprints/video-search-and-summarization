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


SEVERITY_METRIC_VERSION = "severity-v2-mae"
INVALID_OUTCOMES = frozenset({"contract_invalid", "parse_failure"})
SEVERITY_STATUSES = ("valid", "no_model_output", "missing_ground_truth", "invalid_output", "invalid_label",
                     "missing_prediction")


def _level(value: Any) -> int | None:
    """A level on the canonical 1-5 scale (an integral number or numeric string), else ``None`` (V1's rule)."""
    if isinstance(value, bool):
        return None
    try:
        level = float(value)
    except (TypeError, ValueError):
        return None
    return int(level) if level.is_integer() and int(level) in SEVERITY_LEVELS else None


def severity_status(video: Mapping[str, Any]) -> tuple[str, int | None, int | None]:
    """``(status, expected, predicted)`` of one video-run's severity (see :data:`SEVERITY_STATUSES`).

    ``valid`` needs a model output, a ground-truth level and a predicted level on the 1-5 scale. Otherwise, in this
    order: the run had no model output (request failure); the clip has no ground-truth level; the output was invalid
    (``contract_invalid`` or ``parse_failure``: no severity is read from it); the predicted value is not a 1-5 level;
    no severity was predicted.
    """
    entry = (video.get("incident_field_scores") or {}).get("severity_level") or {}
    expected, raw = _level(entry.get("expected")), entry.get("predicted")
    predicted = _level(raw)
    if not v1.has_model_output(video):
        return "no_model_output", expected, None
    if expected is None:
        return "missing_ground_truth", None, predicted
    if str(video.get("outcome")) in INVALID_OUTCOMES:
        return "invalid_output", expected, None
    if predicted is not None:
        return "valid", expected, predicted
    if raw is not None and str(raw).strip():
        return "invalid_label", expected, None
    return "missing_prediction", expected, None


def _severity_core(pairs: list[tuple[int, int]]) -> dict[str, Any]:
    n = len(pairs)
    errors = [p - g for g, p in pairs]
    kappa = weighted_kappa({str(i): g for i, (g, _) in enumerate(pairs)}, {str(i): p for i, (_, p) in enumerate(pairs)},
                           categories=list(SEVERITY_LEVELS), weights="quadratic") if n else {"kappa": None}
    return {"n": n,
            "mae": _ratio(sum(abs(e) for e in errors), n),
            "exact": _ratio(sum(e == 0 for e in errors), n),
            "qwk": round(kappa["kappa"], 4) if kappa["kappa"] is not None else None,
            "within_one": _ratio(sum(abs(e) <= 1 for e in errors), n),
            "mean_signed_error": _ratio(sum(errors), n)}


def severity_metrics(videos: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Severity on the ordinal 1-5 scale (``severity-v2-mae``): MAE primary, exact accuracy and QWK secondary.

    All three are computed over the same valid pairs (:func:`severity_status`), so they share one denominator
    (``n``). Runs without a valid pair are never dropped silently: they are counted by status and by outcome, and
    ``coverage`` is ``n / attempted``. MAE is in severity levels (lower is better); exact and QWK are higher-better.
    ``by_category`` repeats the three metrics per ground-truth incident type.
    """
    statuses: Counter[str] = Counter()
    outcomes_without: Counter[str] = Counter()
    pairs: list[tuple[int, int]] = []
    by_category: dict[str, list[tuple[int, int]]] = {}
    category_attempts: Counter[str] = Counter()
    for video in videos:
        status, expected, predicted = severity_status(video)
        statuses[status] += 1
        category = _category(video)
        category_attempts[category] += 1
        if status != "valid":
            outcomes_without[str(video.get("outcome"))] += 1
            continue
        assert expected is not None and predicted is not None
        pairs.append((expected, predicted))
        by_category.setdefault(category, []).append((expected, predicted))
    errors = [p - g for g, p in pairs]
    core = _severity_core(pairs)
    return {
        "metric_version": SEVERITY_METRIC_VERSION, "primary": "mae",
        "direction": {"mae": "lower_is_better", "exact": "higher_is_better", "qwk": "higher_is_better"},
        **core, "valid": core["n"], "attempted": len(videos), "coverage": _ratio(core["n"], len(videos)),
        "counts": {status: statuses[status] for status in SEVERITY_STATUSES},
        "outcomes_without_valid_severity": dict(outcomes_without),
        "over_rated": sum(e > 0 for e in errors), "under_rated": sum(e < 0 for e in errors),
        "abs_error_histogram": {str(k): sum(abs(e) == k for e in errors) for k in range(len(SEVERITY_LEVELS))},
        "by_category": {category: {**_severity_core(by_category.get(category, [])),
                                   "attempted": category_attempts[category]}
                        for category in sorted(category_attempts)},
    }


def with_failed_runs(stage_videos: list[Mapping[str, Any]], p1_videos: list[Mapping[str, Any]],
                     stage: str) -> list[Mapping[str, Any]]:
    """A Track B stage's rows plus the runs that never reached it (no usable reply: only a ``p1`` row exists).

    Those runs keep their outcome, so severity counts them as ``no_model_output`` or ``invalid_output`` and every
    stage shares the same ``attempted``: a failed run is never dropped from a later stage's denominator.
    """
    present = {(v.get("incident_id"), v.get("repeat")) for v in stage_videos}
    missing = [{**dict(v), "stage": stage} for v in p1_videos if (v.get("incident_id"), v.get("repeat")) not in present]
    return [*stage_videos, *missing]


def _category(video: Mapping[str, Any]) -> str:
    """The clip's ground-truth incident type (canonical), the category of the per-category breakdowns."""
    entry = (video.get("incident_field_scores") or {}).get("type") or {}
    gt = entry.get("expected") or ((video.get("ground_truth") or {}).get("incident") or {}).get("type")
    return v1.canonical_incident_type(gt) or "unknown"


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
