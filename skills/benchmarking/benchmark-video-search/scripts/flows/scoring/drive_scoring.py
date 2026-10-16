# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Original Drive metrics applied to complete CLI clip rankings, in returned order.

This is a pure scoring library (ported from the eval-replication bundle's
``flows/drive_scoring.py``): it loads the original Drive ``evaluate_domain_test.py``
and applies its exact metric functions to a retrieval artifact. The retrieval
itself is the pipeline's job (the bundle's retrieval-driving ``run()`` is not
ported -- our ``run_retrieve`` produces the artifact this scores).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from pathlib import Path
from typing import Any


def load_scorer(directory: str):
    """Dynamically load the original Drive scorer, fingerprinted for integrity.

    Reusing the original module's metric functions (rather than reimplementing
    them) is what guarantees the numbers are comparable with the Drive release's
    own baselines. The SHA256 fingerprint is recorded and rechecked after scoring.
    """
    path = Path(directory).resolve() / "evaluate_domain_test.py"
    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
    spec = importlib.util.spec_from_file_location("drive_retrieval_scorer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in (
        "load_gt",
        "average_precision",
        "ndcg_at_k",
        "auc_score",
        "aggregate",
        "K_VALUES",
    ):
        if not hasattr(module, name):
            raise ValueError(f"Original Drive scorer lacks {name}")
    if tuple(module.K_VALUES) != (1, 5, 10):
        raise ValueError("Expected original Drive K_VALUES=(1, 5, 10)")
    return module, path, fingerprint


def prepare(data_path: str, ingestion_path: str, scripts_dir: str) -> dict[str, Any]:
    """Load the Drive ground truth + the ingestion manifest, and validate them.

    The gallery (clip ids) and the ingestion manifest (clip -> sensor/source) must
    match exactly -- the whole scoring rests on that identity mapping.
    """
    scorer, script, fingerprint = load_scorer(scripts_dir)
    gt = scorer.load_gt(data_path)
    gallery = [row["chunk_id"] for row in gt["gallery"]]
    if not gallery or len(gallery) > 1000 or len(set(gallery)) != len(gallery):
        raise ValueError("Drive scoring requires 1–1000 unique gallery clip IDs")
    if not isinstance(gt["queries"], list):
        raise TypeError("Drive ground truth must preserve queries as a list")
    ingestion = json.loads(Path(ingestion_path).read_text())
    if gt.get("meta", {}).get("subset_fingerprint") and (
        ingestion.get("subset_fingerprint") != gt["meta"]["subset_fingerprint"]
    ):
        raise ValueError("Ingestion and Drive subset fingerprints differ")
    clips = ingestion["clips"]
    by_clip = {row["dataset_id"]: row for row in clips}
    if len(by_clip) != len(clips) or set(by_clip) != set(gallery):
        raise ValueError("Ingestion identities must exactly match the Drive gallery")
    sensors = [by_clip[cid].get("sensor_id") for cid in gallery]
    if not all(isinstance(s, str) and s for s in sensors) or len(set(sensors)) != len(
        sensors
    ):
        raise ValueError("Missing or duplicate ingestion sensor IDs")
    sources = [Path(by_clip[cid]["filename"]).stem for cid in gallery]
    if len(set(sources)) != len(sources):
        raise ValueError("Duplicate registered source names")
    return {
        "scorer": scorer,
        "script": script,
        "script_sha256": fingerprint,
        "gt": gt,
        "gallery": gallery,
        "sensor_to_clip": dict(zip(sensors, gallery)),
        "sources": sources,
    }


def eligible_indices(
    gt: dict[str, Any], gallery: list[str]
) -> tuple[list[int], dict[str, int]]:
    """Scorable query indices, plus the exclusion tallies (TAO's skip rules)."""
    eligible = []
    n_near_universal = n_missing_home = n_no_relevance = 0
    gallery_set = set(gallery)
    for qi, query in enumerate(gt["queries"]):
        if query.get("near_universal"):
            n_near_universal += 1
            continue
        if query.get("chunk_id") not in gallery_set:
            n_missing_home += 1
        if not gallery_set.intersection(query["relevant_clip_ids"]):
            n_no_relevance += 1
            continue
        eligible.append(qi)
    return eligible, {
        "n_near_universal": n_near_universal,
        "n_missing_home": n_missing_home,
        "n_no_relevance": n_no_relevance,
    }


def score_query(
    context: dict[str, Any], qi: int, results: list[dict[str, Any]], latency: float
) -> dict:
    """Score one query's full-gallery ranking against the Drive relevance labels."""
    import numpy as np

    gt, gallery, scorer = context["gt"], context["gallery"], context["scorer"]
    q = gt["queries"][qi]
    positions = {cid: i for i, cid in enumerate(gallery)}
    relevant = {positions[cid] for cid in q["relevant_clip_ids"] if cid in positions}
    ranked_ids, scores = [], []
    for hit in results:
        sensor = hit.get("sensor_id")
        cid = context["sensor_to_clip"].get(sensor)
        if cid is None:
            raise ValueError(f"Query {qi}: unexpected sensor {sensor!r}")
        value = hit.get("similarity", hit.get("similarity_score"))
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"Query {qi}: missing or nonfinite cosine score")
        ranked_ids.append(cid)
        scores.append(value)
    if len(ranked_ids) != len(gallery) or len(set(ranked_ids)) != len(gallery):
        raise ValueError(
            f"Query {qi}: expected each of {len(gallery)} gallery clips exactly once; "
            f"received {len(ranked_ids)} results, {len(set(ranked_ids))} unique. "
            "Drive scoring needs a full-gallery (dense) retrieval -- one hit per clip."
        )
    # Relevance ranks follow the CLI response. Never sort its reported scores.
    order = np.asarray([positions[cid] for cid in ranked_ids])
    labels = np.isin(order, list(relevant)).astype(np.float64)
    row_scores = np.asarray(scores, dtype=np.float64)
    m = {
        "query_index": qi,
        "query_id": q.get("query_id", f"row:{qi}"),
        "query": q["query"],
        "slice": q.get("slice", "unknown"),
        "ap": scorer.average_precision(labels),
        "rank": int(np.flatnonzero(labels)[0]) + 1,
        "auc": scorer.auc_score(row_scores, labels),
        "top": [(cid, float(value)) for cid, value in zip(ranked_ids, scores)],
        "latency_s": latency,
    }
    for k in scorer.K_VALUES:
        hits = labels[:k].sum()
        m[f"recall@{k}"] = float(hits / len(relevant))
        m[f"hit@{k}"] = 1.0 if hits > 0 else 0.0
        m[f"ndcg@{k}"] = scorer.ndcg_at_k(labels, k)
    return m


def aggregate_report(
    rows: list[dict],
    gt: dict[str, Any],
    gallery: list[str],
    scorer: Any,
    exclusions: dict[str, int],
) -> dict[str, Any]:
    """Per-slice + overall + event-only aggregation, using the original Drive math."""

    def aggregate(group):
        return scorer.aggregate(group, len(gallery)) if group else {}

    by_slice: dict[str, list] = {}
    event_slices: dict[str, list] = {}
    events = []
    for row in rows:
        by_slice.setdefault(row["slice"], []).append(row)
        if gt["queries"][row["query_index"]].get("query_domain") == "event":
            events.append(row)
            event_slices.setdefault(row["slice"], []).append(row)
    report = {
        "gallery_size": len(gallery),
        "n_queries_total": len(gt["queries"]),
        "n_near_universal_excluded": exclusions["n_near_universal"],
        "n_missing_home_clip": exclusions["n_missing_home"],
        "slices": {s: aggregate(group) for s, group in sorted(by_slice.items())},
        "overall": aggregate(rows),
    }
    # The original event summary includes only event-domain rows, without altering overall scoring.
    event_report = {
        "gallery_size": len(gallery),
        "n_queries_total": len(gt["queries"]),
        "n_near_universal_excluded": exclusions["n_near_universal"],
        "n_missing_home_clip": exclusions["n_missing_home"],
        "groups": {"event_only": aggregate(events)},
        "slices": {s: aggregate(group) for s, group in sorted(event_slices.items())},
    }
    return {
        "metrics": report,
        "event_summary": event_report,
        "overall": report["overall"],
    }


def verify_scorer_unchanged(context: dict[str, Any]) -> None:
    """Re-fingerprint the Drive scorer after scoring; a change mid-run invalidates it."""
    if (
        hashlib.sha256(context["script"].read_bytes()).hexdigest()
        != context["script_sha256"]
    ):
        raise ValueError("Original Drive scorer changed during evaluation")
