# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Original Drive metrics applied to complete CLI clip rankings, in returned order."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any


def load_scorer(directory: str):
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
    scorer, script, fingerprint = load_scorer(scripts_dir)
    gt = scorer.load_gt(data_path)
    gallery = [row["chunk_id"] for row in gt["gallery"]]
    if not gallery or len(gallery) > 1000 or len(set(gallery)) != len(gallery):
        raise ValueError("Drive scoring requires 1–1000 unique gallery clip IDs")
    if not isinstance(gt["queries"], list):
        raise ValueError("Drive ground truth must preserve queries as a list")
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


def score_query(
    context: dict[str, Any], qi: int, results: list[dict[str, Any]], latency: float
) -> dict:
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
            f"received {len(ranked_ids)} results, {len(set(ranked_ids))} unique"
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


def run(
    context: dict[str, Any], backend: Any, output_file: str, concurrency: int = 1
) -> dict:
    import numpy as np

    gt, gallery, scorer = context["gt"], context["gallery"], context["scorer"]
    backend.search_path = "embed"
    backend.top_k = len(gallery)
    backend.min_cosine_similarity = -1.0
    backend.source_type = "video_file"
    backend.merge_adjacent = False
    backend.decompositions = {}
    backend.video_sources = context["sources"]
    # The CLI's optional critic cannot affect these metrics. Bound its work
    # rather than verifying every clip in a full-gallery request.
    backend.critic_eval_count = 1
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

    def evaluate(qi):
        results, latency = backend.search(gt["queries"][qi]["query"])
        row = score_query(context, qi, results, latency)
        print(f"[{qi + 1}/{len(gt['queries'])}] AP={row['ap']:.4f} rank={row['rank']}")
        return row

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        rows = list(pool.map(evaluate, eligible))

    # Use the original aggregate function, including its NumPy mean/median arithmetic.
    def aggregate(group):
        return scorer.aggregate(group, len(gallery)) if group else {}

    by_slice = {}
    event_slices = {}
    events = []
    for row in rows:
        by_slice.setdefault(row["slice"], []).append(row)
        if gt["queries"][row["query_index"]].get("query_domain") == "event":
            events.append(row)
            event_slices.setdefault(row["slice"], []).append(row)
    report = {
        "gallery_size": len(gallery),
        "n_queries_total": len(gt["queries"]),
        "n_near_universal_excluded": n_near_universal,
        "n_missing_home_clip": n_missing_home,
        "slices": {s: aggregate(group) for s, group in sorted(by_slice.items())},
        "overall": aggregate(rows),
    }
    # The original event summary includes only event-domain rows, without altering overall scoring.
    event_report = {
        "gallery_size": len(gallery),
        "n_queries_total": len(gt["queries"]),
        "n_near_universal_excluded": n_near_universal,
        "n_missing_home_clip": n_missing_home,
        "groups": {"event_only": aggregate(events)},
        "slices": {s: aggregate(group) for s, group in sorted(event_slices.items())},
    }
    if (
        hashlib.sha256(context["script"].read_bytes()).hexdigest()
        != context["script_sha256"]
    ):
        raise ValueError("Original Drive scorer changed during evaluation")
    output = {
        "summary": report["overall"],
        "metrics": report,
        "event_summary": event_report,
        "query_results": rows,
        "config": {
            "scoring": "drive",
            "search_path": "embed",
            "top_k": len(gallery),
            "min_cosine_similarity": -1.0,
            "merge_adjacent": False,
            "critic_eval_count": 1,
            "concurrency": concurrency,
            "scorer": str(context["script"]),
            "scorer_sha256": context["script_sha256"],
            "numpy_version": np.__version__,
            "ranking": "CLI response order",
            "n_no_gallery_relevance_excluded": n_no_relevance,
            "wall_clock_s": time.monotonic() - started,
        },
    }
    out = Path(output_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, indent=2, allow_nan=False) + "\n")
    print(f"Drive metrics: {report['overall']}\nResults saved to: {out.resolve()}")
    return output
