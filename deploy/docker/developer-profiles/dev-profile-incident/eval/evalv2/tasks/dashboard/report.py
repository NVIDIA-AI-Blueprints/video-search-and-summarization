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

"""Score a C1c run: every stored chart reply against its dashboard reference, per subject, offline.

Each final item of the run is one row:

- **No reply** (an HTTP error or a non-completion after the retries): counted as ``request_failed`` and left out of
  every rate (the console would show an error, not a chart). ``coverage`` is answered items over items.
- **A reply** is scored with :func:`scoring.score_reply` on the console's parse of it. An *invalid reply*
  (``parse_failure``: no text, no JSON object, or an object that is neither an allowlisted spec nor the refusal)
  is kept apart from a *wrong spec* (``valid_but_wrong``, ``false_accept``) and a wrong refusal (``false_refusal``).

Per subject the summary gives, with every denominator: exact-spec accuracy (over supported requests answered),
per-field accuracy over valid specs (``metric``, ``dimension``, ``period``, ``incidentType``, ``severity`` and
``filters``, both filters right), refusal precision (over refusals) and recall (over unsupported requests), overall
accuracy (exact or a correct refusal, over every answered request), the invalid-reply rate and, when figures are
computed, figure accuracy: the reply's chart against the reference's chart on the frozen corpus snapshot
(:func:`scoring.figure_outcome`, bar charts, the snapshot's clock, UTC).

**Intervals** are a record-level bootstrap: requests are resampled with replacement (a request's repeats stay
together), 10,000 resamples, seed 20261027 (V1's), 95% percentile intervals. The video-level cluster bootstrap does
not apply: a dashboard request is not tied to a video, and the corpus is shared by every request.

Results are reported per reference tier (``scoring.aggregate``); RB2's records are ``ai_validated`` until X6 review.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np

from evalv2.core.artifacts import ArtifactRef, ArtifactStore
from evalv2.core.hashing import sha256_json
from evalv2.references.dashboard import SPEC_KEYS
from evalv2.tasks.dashboard.runner import REPLY_KINDS, TASK
from evalv2.tasks.dashboard.scoring import RIGHT_FIGURES, aggregate, score_reply

REPORT_VERSION = "evalv2-dashboard-report-v1"
RESAMPLES, SEED, CONFIDENCE = 10_000, 20261027, 0.95
CHART_TYPE, TIMEZONE = "bar", "UTC"
# Per record: the counts every interval metric is a ratio of (numerator, denominator).
_COUNTS = ("spec", "exact", "unsupported", "refusal_correct", "refused", "answered", "right", "parse_failure",
           "figured", "figures_right")
INTERVALS = {"exact_spec_accuracy": ("exact", "spec"), "refusal_precision": ("refusal_correct", "refused"),
             "refusal_recall": ("refusal_correct", "unsupported"), "overall_accuracy": ("right", "answered"),
             "invalid_reply_rate": ("parse_failure", "answered"), "figure_accuracy": ("figures_right", "figured")}
FiguresOf = Callable[[Mapping[str, Any] | None], Mapping[str, Any]]


def cached_figures(compute: FiguresOf) -> FiguresOf:
    """One chart per distinct spec: every subject's identical replies share it."""
    cache: dict[str, Mapping[str, Any]] = {}

    def figures_of(spec: Mapping[str, Any] | None) -> Mapping[str, Any]:
        key = sha256_json(None if spec is None else dict(spec))
        if key not in cache:
            cache[key] = compute(spec)
        return cache[key]

    return figures_of


def _record_counts(row: Mapping[str, Any]) -> Counter[str]:
    counts: Counter[str] = Counter(answered=1)
    supported = row["class"] == "spec"
    counts["spec" if supported else "unsupported"] += 1
    counts["exact"] += row["outcome"] == "exact"
    counts["refusal_correct"] += row["outcome"] == "refusal_correct"
    counts["refused"] += row["outcome"] in ("refusal_correct", "false_refusal")
    counts["right"] += row["outcome"] in ("exact", "refusal_correct")
    counts["parse_failure"] += row["outcome"] == "parse_failure"
    if row.get("figures") is not None:
        counts["figured"] += 1
        counts["figures_right"] += row["figures"] in RIGHT_FIGURES
    return counts


def _ratio(num: Any, den: Any) -> Any:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)


def bootstrap(rows: Sequence[Mapping[str, Any]], *, resamples: int = RESAMPLES, seed: int = SEED
              ) -> dict[str, dict[str, Any]]:
    """Point estimate, interval and denominator of every :data:`INTERVALS` metric (record-level resampling)."""
    by_record: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        by_record[row["record_id"]].update(_record_counts(row))
    records = sorted(by_record)
    if not records:
        return {}
    table = np.array([[by_record[r][name] for name in _COUNTS] for r in records], dtype=float)
    index = np.random.default_rng(seed).integers(0, len(records), size=(resamples, len(records)))
    sums = table[index].sum(axis=1)  # (resamples, counts)
    total = table.sum(axis=0)
    column = {name: i for i, name in enumerate(_COUNTS)}
    out: dict[str, dict[str, Any]] = {}
    alpha = (1 - CONFIDENCE) / 2
    for metric, (num, den) in INTERVALS.items():
        n, d = total[column[num]], total[column[den]]
        if d == 0:
            out[metric] = {"value": None, "interval": None, "numerator": 0, "denominator": 0}
            continue
        samples = _ratio(sums[:, column[num]], sums[:, column[den]])
        finite = samples[np.isfinite(samples)]
        interval = (None if finite.size < 0.9 * samples.size else
                    [round(float(x), 4) for x in np.quantile(finite, [alpha, 1 - alpha])])
        out[metric] = {"value": round(float(n / d), 4), "interval": interval, "numerator": int(n),
                       "denominator": int(d)}
    return out


def _field_accuracy(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    fielded = [r for r in rows if r.get("fields") is not None]
    if not fielded:
        return {"denominator": 0}
    out: dict[str, Any] = {key: round(sum(1 for r in fielded if r["fields"][key]) / len(fielded), 4)
                           for key in SPEC_KEYS}
    out["filters"] = round(sum(1 for r in fielded if r["fields"]["incidentType"] and r["fields"]["severity"])
                           / len(fielded), 4)
    out["denominator"] = len(fielded)
    return out


def subject_summary(rows: Sequence[Mapping[str, Any]], failed: int, kinds: Counter[str],
                    over_timeout: int) -> dict[str, Any]:
    outcomes = Counter(r["outcome"] for r in rows)
    answered = len(rows)
    return {
        "items": answered + failed, "answered": answered, "request_failed": failed,
        "coverage": round(answered / (answered + failed), 4) if answered + failed else None,
        "metrics": bootstrap(rows),
        "field_accuracy": _field_accuracy(rows),
        "errors": {"invalid_reply": outcomes["parse_failure"],
                   "invalid_reply_kinds": {k: kinds[k] for k in REPLY_KINDS if k not in ("spec", "refusal") and kinds[k]},
                   "wrong_spec": outcomes["valid_but_wrong"], "false_accept": outcomes["false_accept"],
                   "false_refusal": outcomes["false_refusal"]},
        "outcomes": dict(sorted(outcomes.items())),
        "reply_kinds": dict(sorted(kinds.items())),
        "figure_outcomes": dict(sorted(Counter(r["figures"] for r in rows if r.get("figures")).items())) or None,
        "over_console_timeout": over_timeout,
        "by_tier": aggregate(rows),
    }


def score_rows(final_rows: Sequence[Mapping[str, Any]], store: ArtifactStore, records: Sequence[Mapping[str, Any]],
               allowlist: Mapping[str, Any], *, figures_of: FiguresOf | None = None
               ) -> dict[str, Any]:
    """``{"subjects": {id: summary}, "items": [...]}`` for a run's final rows (``score_stage.final_predictions``)."""
    by_id = {r["record_id"]: r for r in records}
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    failed: Counter[str] = Counter()
    kinds: dict[str, Counter[str]] = defaultdict(Counter)
    over: Counter[str] = Counter()
    models: dict[str, str] = {}
    items: list[dict[str, Any]] = []
    for row in (r for r in final_rows if r["task"] == TASK):
        subject = row["subject"]
        stage = ((row.get("provenance") or {}).get("stages") or {}).get("chart") or {}
        models.setdefault(subject, stage.get("model"))
        record = by_id.get(row["item_id"])
        if record is None:
            raise ValueError(f"the run scores {row['item_id']}, which the reference dataset does not hold")
        if not row.get("prediction_ref"):
            failed[subject] += 1
            items.append({"subject": subject, "record_id": row["item_id"], "repeat": row["repeat"],
                          "status": "request_failed", "outcome": row["outcome"]})
            continue
        artifact = store.get(ArtifactRef.parse(row["prediction_ref"]))
        kinds[subject][artifact["reply_kind"]] += 1
        over[subject] += bool(artifact.get("over_console_timeout"))
        scored = score_reply(record, artifact["reply"], allowlist, repeat=row["repeat"], figures_of=figures_of)
        if scored["status"] != "scored":
            continue
        scored["reply_kind"] = artifact["reply_kind"]
        rows[subject].append(scored)
        items.append({"subject": subject, **scored})
    subjects = {subject: {"model": models.get(subject),
                          **subject_summary(rows[subject], failed[subject], kinds[subject], over[subject])}
                for subject in sorted(set(rows) | set(failed))}
    return {"subjects": subjects, "items": items}
