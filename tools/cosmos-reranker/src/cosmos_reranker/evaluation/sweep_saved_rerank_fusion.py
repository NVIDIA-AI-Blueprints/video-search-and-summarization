"""Sweep normalized SigLIP2/CR3 score fusion from a saved PAS ranking trace."""

from __future__ import annotations
import json
from pathlib import Path
import numpy as np

QUERY_TYPES = ("easy", "medium", "hard")
METRICS = ("mAP", "Rank-1", "Rank-5")


def load_trace(path: Path) -> dict[str, np.ndarray]:
    stage1, cr3, labels, num_gt, tail_ap, query_types = ([], [], [], [], [], [])
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            row = json.loads(line)
            candidates = sorted(row["candidates"], key=lambda item: item["stage1_rank"])
            if len(candidates) != 20:
                raise ValueError(f"line {line_number}: expected depth 20, got {len(candidates)}")
            y = np.asarray([bool(item["is_gt"]) for item in candidates], dtype=np.bool_)
            ranks = np.arange(1, 21, dtype=np.float64)
            head_precision_sum = float(np.sum((np.cumsum(y) / ranks)[y]))
            n_gt = int(row["num_gt"])
            stage1.append([float(item["stage1_score"]) for item in candidates])
            cr3.append([float(item["rerank_score"]) for item in candidates])
            labels.append(y)
            num_gt.append(n_gt)
            tail_ap.append(float(row["retriever_ap"]) * n_gt - head_precision_sum)
            query_types.append(row["query_type"])
    return {
        "stage1": np.asarray(stage1, dtype=np.float64),
        "cr3": np.asarray(cr3, dtype=np.float64),
        "labels": np.asarray(labels, dtype=np.bool_),
        "num_gt": np.asarray(num_gt, dtype=np.float64),
        "tail_ap": np.asarray(tail_ap, dtype=np.float64),
        "query_type": np.asarray(query_types),
    }


def evaluate(data: dict[str, np.ndarray], alpha: float) -> dict[str, float]:
    scores = alpha * data["stage1"] + (1.0 - alpha) * data["cr3"]
    order = np.argsort(-scores, axis=1, kind="stable")
    ranked_labels = np.take_along_axis(data["labels"], order, axis=1)
    ranks = np.arange(1, 21, dtype=np.float64)
    precision = np.cumsum(ranked_labels, axis=1) / ranks[None, :]
    head_ap_sum = np.sum(precision * ranked_labels, axis=1)
    ap = (head_ap_sum + data["tail_ap"]) / data["num_gt"]
    rank1 = ranked_labels[:, 0].astype(np.float64)
    rank5 = np.any(ranked_labels[:, :5], axis=1).astype(np.float64)
    result: dict[str, float] = {"alpha": float(alpha)}
    for query_type in QUERY_TYPES:
        mask = data["query_type"] == query_type
        result[f"{query_type}_num_queries"] = int(mask.sum())
        result[f"{query_type}_mAP"] = float(ap[mask].mean())
        result[f"{query_type}_Rank-1"] = float(rank1[mask].mean())
        result[f"{query_type}_Rank-5"] = float(rank5[mask].mean())
    result["overall_num_queries"] = int(len(ap))
    result["overall_mAP"] = float(ap.mean())
    result["overall_Rank-1"] = float(rank1.mean())
    result["overall_Rank-5"] = float(rank5.mean())
    return result


def unique_grid(values: np.ndarray) -> list[float]:
    return sorted({float(np.clip(value, 0.0, 1.0)) for value in values})
