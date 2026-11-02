# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Video-level cluster bootstrap for Track A (methodology v2.0 §5): intervals and paired differences.

The resampling unit is the **video**: a clip's repeats move together, so two repeats of one clip are never treated
as independent evidence. Seed, resamples and confidence are V1's (``methodology.BOOTSTRAP_*``); intervals are
percentile intervals.

- **Reliability** (``contract_valid``) is computed over every planned run, **conditional** metrics over contract-valid
  runs only (severity MAE, exact, QWK; type accuracy; start/end absolute error; entity F1). The two are reported
  side by side and never combined, so a model is never ranked by conditional accuracy alone.
- **Paired differences** (A − B) use the clips both models were run on. A conditional metric uses only the
  (clip, repeat) runs where **both** are valid, so both sides are scored on identical inputs; ``contract_valid``
  uses every run. The difference's interval resamples clips once for both models.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np

from evalv2.tasks.incident_objects.aggregate import SEVERITY_LEVELS, severity_status

RESAMPLES, SEED, CONFIDENCE = 10_000, 20261027, 0.95  # = eval/methodology.py BOOTSTRAP_* (V1), asserted in tests
CONDITIONAL = ("severity_mae", "severity_exact", "severity_qwk", "type_accuracy", "start_mae_s", "end_mae_s",
               "entities_f1")
METRICS = ("contract_valid", *CONDITIONAL)
LOWER_IS_BETTER = frozenset({"severity_mae", "start_mae_s", "end_mae_s"})
K = len(SEVERITY_LEVELS)
# Per-clip sufficient statistics (summed over the clip's runs): index -> meaning
_FIELDS = ("runs", "valid", "sev_n", "sev_abs", "sev_exact", "type_n", "type_pass", "start_n", "start_abs",
           "end_n", "end_abs", "tp", "fp", "fn")
_IX = {name: i for i, name in enumerate(_FIELDS)}


def _seconds(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def run_stats(row: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """One run's sufficient statistics and its severity confusion cell (5x5, all zero unless valid)."""
    stats, confusion = np.zeros(len(_FIELDS)), np.zeros((K, K))
    stats[_IX["runs"]] = 1
    if row.get("outcome") != "scored":
        return stats, confusion
    stats[_IX["valid"]] = 1
    fields = row.get("incident_field_scores") or {}
    status, expected, predicted = severity_status(row)
    if status == "valid" and expected is not None and predicted is not None:
        stats[_IX["sev_n"]] += 1
        stats[_IX["sev_abs"]] += abs(predicted - expected)
        stats[_IX["sev_exact"]] += predicted == expected
        confusion[expected - 1, predicted - 1] += 1
    type_pass = (fields.get("type") or {}).get("pass")
    if type_pass is not None:
        stats[_IX["type_n"]] += 1
        stats[_IX["type_pass"]] += bool(type_pass)
    for name, (n_key, abs_key) in (("start_timestamp", ("start_n", "start_abs")),
                                   ("end_timestamp", ("end_n", "end_abs"))):
        entry = fields.get(name) or {}
        gt, pred = _seconds(entry.get("expected")), _seconds(entry.get("predicted"))
        if gt is not None and pred is not None:
            stats[_IX[n_key]] += 1
            stats[_IX[abs_key]] += abs(pred - gt)
    entities = row.get("entities") or {}
    stats[_IX["tp"]] += len(entities.get("matches") or [])
    stats[_IX["fp"]] += len(entities.get("unmatched_model") or [])
    stats[_IX["fn"]] += len(entities.get("unmatched_gt") or [])
    return stats, confusion


def _qwk(confusion: np.ndarray) -> np.ndarray:
    """Quadratic-weighted κ of a stack of confusion matrices (..., K, K); NaN where undefined."""
    weights = (np.subtract.outer(np.arange(K), np.arange(K)) ** 2) / (K - 1) ** 2
    n = confusion.sum(axis=(-1, -2))
    rows, cols = confusion.sum(axis=-1), confusion.sum(axis=-2)
    with np.errstate(invalid="ignore", divide="ignore"):
        expected = np.einsum("...i,...j->...ij", rows, cols) / n[..., None, None]
        observed_w = (weights * confusion).sum(axis=(-1, -2))
        expected_w = (weights * expected).sum(axis=(-1, -2))
        return 1 - observed_w / expected_w


def metrics_from(stats: np.ndarray, confusion: np.ndarray) -> dict[str, np.ndarray]:
    """Every metric from summed statistics (works on one row or a stack of bootstrap rows)."""
    s = {name: stats[..., i] for i, name in enumerate(_FIELDS)}
    with np.errstate(invalid="ignore", divide="ignore"):
        f1 = 2 * s["tp"] / (2 * s["tp"] + s["fp"] + s["fn"])
        return {"contract_valid": s["valid"] / s["runs"], "severity_mae": s["sev_abs"] / s["sev_n"],
                "severity_exact": s["sev_exact"] / s["sev_n"], "severity_qwk": _qwk(confusion),
                "type_accuracy": s["type_pass"] / s["type_n"], "start_mae_s": s["start_abs"] / s["start_n"],
                "end_mae_s": s["end_abs"] / s["end_n"], "entities_f1": f1}


def _clip_table(rows: Iterable[Mapping[str, Any]], keep: set[tuple[str, int]] | None = None
                ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Per clip, the summed statistics of its runs (only the (clip, repeat) runs in ``keep`` when given)."""
    table: dict[str, list[np.ndarray]] = defaultdict(lambda: [np.zeros(len(_FIELDS)), np.zeros((K, K))])
    for row in rows:
        if keep is not None and (row["incident_id"], row["repeat"]) not in keep:
            continue
        stats, confusion = run_stats(row)
        entry = table[row["incident_id"]]
        entry[0] += stats
        entry[1] += confusion
    return {clip: (entry[0], entry[1]) for clip, entry in table.items()}


def _interval(samples: np.ndarray) -> list[float] | None:
    finite = samples[np.isfinite(samples)]
    if finite.size < 0.9 * samples.size:  # undefined in too many resamples (tiny subsets): no interval
        return None
    alpha = (1 - CONFIDENCE) / 2
    low, high = np.quantile(finite, [alpha, 1 - alpha])
    return [round(float(low), 4), round(float(high), 4)]


def _point(value: Any) -> float | None:
    value = float(value)
    return round(value, 4) if np.isfinite(value) else None


def _resample_index(n_clips: int, resamples: int, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, n_clips, size=(resamples, n_clips))


def subject_intervals(rows: Sequence[Mapping[str, Any]], *, resamples: int = RESAMPLES, seed: int = SEED
                      ) -> dict[str, Any]:
    """Point estimate and cluster-bootstrap interval of every metric for one subject's runs."""
    table = _clip_table(rows)
    clips = sorted(table)
    stats = np.stack([table[c][0] for c in clips])
    confusion = np.stack([table[c][1] for c in clips])
    point = metrics_from(stats.sum(axis=0), confusion.sum(axis=0))
    index = _resample_index(len(clips), resamples, seed)
    boot = metrics_from(stats[index].sum(axis=1), confusion[index].sum(axis=1))
    return {"clips": len(clips), "runs": int(stats[:, _IX["runs"]].sum()), "valid_runs": int(stats[:, _IX["valid"]].sum()),
            "metrics": {m: {"estimate": _point(point[m]), "ci95": _interval(boot[m])} for m in METRICS}}


def paired_difference(rows_a: Sequence[Mapping[str, Any]], rows_b: Sequence[Mapping[str, Any]], *,
                      resamples: int = RESAMPLES, seed: int = SEED) -> dict[str, Any]:
    """A − B with a clip-level paired bootstrap (see the module docstring for which runs each metric uses)."""
    valid_a = {(r["incident_id"], r["repeat"]) for r in rows_a if r.get("outcome") == "scored"}
    valid_b = {(r["incident_id"], r["repeat"]) for r in rows_b if r.get("outcome") == "scored"}
    both = valid_a & valid_b
    clips = sorted({r["incident_id"] for r in rows_a} & {r["incident_id"] for r in rows_b})
    index = _resample_index(len(clips), resamples, seed)
    out: dict[str, Any] = {"clips": len(clips), "both_valid_runs": len(both), "metrics": {}}
    for metrics, keep in ((("contract_valid",), None), (CONDITIONAL, both)):
        ta, tb = _clip_table(rows_a, keep), _clip_table(rows_b, keep)
        zero = (np.zeros(len(_FIELDS)), np.zeros((K, K)))
        sa = np.stack([ta.get(c, zero)[0] for c in clips])
        ca = np.stack([ta.get(c, zero)[1] for c in clips])
        sb = np.stack([tb.get(c, zero)[0] for c in clips])
        cb = np.stack([tb.get(c, zero)[1] for c in clips])
        pa, pb = metrics_from(sa.sum(axis=0), ca.sum(axis=0)), metrics_from(sb.sum(axis=0), cb.sum(axis=0))
        ba, bb = metrics_from(sa[index].sum(axis=1), ca[index].sum(axis=1)), metrics_from(sb[index].sum(axis=1),
                                                                                         cb[index].sum(axis=1))
        for m in metrics:
            diff = ba[m] - bb[m]
            ci = _interval(diff)
            out["metrics"][m] = {"a": _point(pa[m]), "b": _point(pb[m]), "difference": _point(pa[m] - pb[m]),
                                 "ci95": ci, "excludes_zero": None if ci is None else (ci[0] > 0 or ci[1] < 0),
                                 "lower_is_better": m in LOWER_IS_BETTER}
    return out


def analysis(rows: Sequence[Mapping[str, Any]], pairs: Sequence[tuple[str, str]], *, resamples: int = RESAMPLES,
             seed: int = SEED) -> dict[str, Any]:
    by_subject: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_subject[row["subject"]].append(row)
    return {"method": {"unit": "video (clip); repeats resampled together", "resamples": resamples, "seed": seed,
                       "confidence": CONFIDENCE, "interval": "percentile",
                       "conditional_runs": "contract-valid only; paired: runs where both are valid"},
            "subjects": {s: subject_intervals(r, resamples=resamples, seed=seed) for s, r in sorted(by_subject.items())},
            "paired": {f"{a} - {b}": paired_difference(by_subject[a], by_subject[b], resamples=resamples, seed=seed)
                       for a, b in pairs if a in by_subject and b in by_subject}}
