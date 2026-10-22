"""KPI analysis for AOI ChangeNet inference results — pure stdlib.

The AOI deployment KPI is the false-alarm rate at a recall operating point:
pick the score threshold that still catches `recall` of all true defects,
then FAR = fraction of PASS samples flagged at that threshold. This module
ports the threshold-sweep logic of the DEFT skill's `analyze_kpi.py`
(label + siamese_score columns; higher score = more defect-like) without a
pandas/numpy dependency so the service image stays lean.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass
class KpiAnalysis:
    n_pass: int
    n_defect: int
    threshold_100: float          # threshold catching 100% of defects
    far_pct_100: float            # FAR (%) at that threshold
    far_pct_95: float             # FAR (%) at 95% recall
    auc: float                    # rank AUC, defect vs PASS
    best_f1: float
    best_f1_threshold: float
    best_f1_far_pct: float

    def metrics(self) -> dict[str, float]:
        return {
            "FAR_pct": round(self.far_pct_100, 4),
            "FAR95_pct": round(self.far_pct_95, 4),
            "threshold": round(self.threshold_100, 6),
            "AUC": round(self.auc, 4),
            "best_f1": round(self.best_f1, 4),
            "n_defects": float(self.n_defect),
        }


def read_scores(csv_path: Path, *, label_col: str = "label", score_col: str = "siamese_score",
                pass_label: str = "PASS") -> tuple[list[float], list[float]]:
    """Return (pass_scores, defect_scores) from an inference CSV."""
    pass_scores: list[float] = []
    defect_scores: list[float] = []
    with open(csv_path, newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or label_col not in reader.fieldnames or score_col not in reader.fieldnames:
            raise ValueError(
                f"{csv_path} must have '{label_col}' and '{score_col}' columns; found {reader.fieldnames}"
            )
        for row in reader:
            try:
                score = float(row[score_col])
            except (TypeError, ValueError):
                continue
            (pass_scores if row[label_col] == pass_label else defect_scores).append(score)
    return pass_scores, defect_scores


def far_at_recall(pass_scores: list[float], defect_scores: list[float], recall: float) -> tuple[float, float]:
    """Threshold catching `recall` of defects (score >= t == flagged) and the FAR there (fraction)."""
    if not defect_scores:
        raise ValueError("no defect samples — FAR@recall is undefined")
    ds = sorted(defect_scores)
    idx = 0 if recall >= 1.0 else max(0, int(math.ceil((1 - recall) * len(ds))) - 1)
    threshold = ds[idx]
    far = sum(1 for s in pass_scores if s >= threshold) / max(1, len(pass_scores))
    return threshold, far


def rank_auc(pass_scores: list[float], defect_scores: list[float]) -> float:
    """Mann-Whitney rank AUC: P(defect score > PASS score), ties = 0.5."""
    if not pass_scores or not defect_scores:
        return float("nan")
    ranked = sorted(((s, 1) for s in defect_scores), key=lambda x: x[0])
    merged = sorted([(s, 0) for s in pass_scores] + [(s, 1) for s in defect_scores], key=lambda x: x[0])
    # average ranks with tie handling
    rank_sum_defect = 0.0
    i = 0
    while i < len(merged):
        j = i
        while j < len(merged) and merged[j][0] == merged[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0  # ranks are 1-based; block spans i+1..j
        rank_sum_defect += avg_rank * sum(1 for k in range(i, j) if merged[k][1] == 1)
        i = j
    n1, n0 = len(defect_scores), len(pass_scores)
    u = rank_sum_defect - n1 * (n1 + 1) / 2.0
    del ranked
    return u / (n1 * n0)


def analyze(csv_path: Path, *, label_col: str = "label", score_col: str = "siamese_score",
            pass_label: str = "PASS") -> KpiAnalysis:
    pass_scores, defect_scores = read_scores(
        csv_path, label_col=label_col, score_col=score_col, pass_label=pass_label
    )
    if not defect_scores:
        raise ValueError(f"{csv_path}: no defect (non-{pass_label}) rows — cannot compute FAR@recall")
    t100, far100 = far_at_recall(pass_scores, defect_scores, 1.0)
    _, far95 = far_at_recall(pass_scores, defect_scores, 0.95)
    auc = rank_auc(pass_scores, defect_scores)

    # best-F1 sweep over candidate thresholds (defect = positive class)
    best_f1, best_t, best_far = 0.0, t100, far100
    candidates = sorted(set(defect_scores + pass_scores))
    step = max(1, len(candidates) // 200)  # cap sweep cost
    for t in candidates[::step]:
        tp = sum(1 for s in defect_scores if s >= t)
        fp = sum(1 for s in pass_scores if s >= t)
        fn = len(defect_scores) - tp
        if tp == 0:
            continue
        precision = tp / (tp + fp)
        recall = tp / (tp + fn)
        f1 = 2 * precision * recall / (precision + recall)
        if f1 > best_f1:
            best_f1, best_t, best_far = f1, t, fp / max(1, len(pass_scores))
    return KpiAnalysis(
        n_pass=len(pass_scores),
        n_defect=len(defect_scores),
        threshold_100=t100,
        far_pct_100=far100 * 100.0,
        far_pct_95=far95 * 100.0,
        auc=auc,
        best_f1=best_f1,
        best_f1_threshold=best_t,
        best_f1_far_pct=best_far * 100.0,
    )
