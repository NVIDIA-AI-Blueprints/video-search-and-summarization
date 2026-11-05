#!/usr/bin/env python3
"""Latency-only probe for the fine-tuned CR3 reranker on the 80-clip subset.

Aligned with skills/benchmarking/benchmark-video-search/scripts/
rerank_raw_clip_eval.py: scores one query's candidates through
CosmosReasonReranker.score_pairs (CosmosScorer style, with the per-candidate
VideoSamplingError fallback), times each query after an untimed warm-up, and
reports latency with the upstream ms_stats shape and latency_definition.
Candidates are random windows from the 80-clip subset instead of real
stage-1 hits — latency does not depend on candidate relevance.
"""

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import cv2

from cosmos_reranker.reranking import (
    Candidate,
    CosmosReasonReranker,
    RerankerConfig,
)
from cosmos_reranker.reranking.base import Segment
from cosmos_reranker.reranking.frames import controlled_frame_indices


def clip_meta(path: Path) -> tuple[int, float]:
    cap = cv2.VideoCapture(str(path))
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    finally:
        cap.release()
    return frames, fps


def ms_stats(values):
    """Upstream rerank_raw_clip_eval.py shape: mean / median / p95 in ms."""
    if not values:
        return None
    ordered = sorted(values)
    return {
        "mean_ms": statistics.fmean(values) * 1000,
        "median_ms": statistics.median(values) * 1000,
        "p95_ms": ordered[math.ceil(0.95 * len(ordered)) - 1] * 1000,
    }


def score(reranker, query: str, candidates):
    """CosmosScorer.score: score_pairs on all candidates, falling back to
    per-candidate rescoring when one undecodable window fails the batch."""
    from cosmos_reranker.reranking.frames import VideoSamplingError

    pairs = [(query, c) for c in candidates]
    try:
        return [r.score for r in reranker.score_pairs(pairs)]
    except VideoSamplingError:
        results = []
        for candidate in candidates:
            try:
                (scored,) = [r.score for r in reranker.score_pairs([(query, candidate)])]
                results.append(scored)
            except VideoSamplingError:
                results.append(None)
        return results


def candidates_for(rng: random.Random, clips, metas, *, whole: bool, depth: int):
    picks = rng.sample(range(len(clips)), depth)
    out = []
    for rank, idx in enumerate(picks):
        frames, fps = metas[idx]
        duration = frames / fps if fps > 0 else 0.0
        if whole or duration <= 0:
            start, end = None, None
        else:
            length = float(rng.uniform(8.0, 12.0))
            length = min(length, duration)
            start = float(rng.uniform(0.0, duration - length))
            end = start + length
        out.append(
            Candidate(
                Segment(rank, clips[idx].stem, start, end),
                stage1_score=-float(rank),
                video_path=clips[idx],
            )
        )
    return out


def frames_per_candidate(cands, metas_by_name, *, fps: float, max_frames: int):
    counts, rates = [], set()
    for c in cands:
        frames, vfps = metas_by_name[c.segment.video]
        indices = controlled_frame_indices(
            c, fps=fps, max_frames=max_frames, total_frames=frames, video_fps=vfps
        )
        counts.append(len(indices))
        rates.add(round(vfps, 3))
    return counts, sorted(rates)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--merged-model", required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--clips", type=Path, required=True)
    parser.add_argument("--queries-gt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--queries", type=int, default=30)
    parser.add_argument("--depth", type=int, default=20)
    parser.add_argument("--fps", type=float, default=1.0)
    parser.add_argument("--max-frames", type=int, default=32)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--seed", type=int, default=880821)
    args = parser.parse_args()

    clips = sorted(p for p in args.clips.iterdir() if p.suffix == ".mp4")
    gt = json.loads(args.queries_gt.read_text())
    queries = [q["query"] for q in gt["queries"]][: args.queries]
    if len(queries) < args.queries:
        raise SystemExit("not enough queries in ground truth")
    print(f"{len(clips)} clips, {len(queries)} queries")

    metas = [clip_meta(p) for p in clips]
    metas_by_name = {p.stem: m for p, m in zip(clips, metas)}
    rng = random.Random(args.seed)

    reranker = CosmosReasonReranker(
        RerankerConfig(
            rerank_depth=args.depth,
            fps=args.fps,
            max_frames=args.max_frames,
            options={
                "model_id": args.merged_model,
                "lora_path": str(args.adapter / "language"),
                "max_lora_rank": 32,
                "max_model_len": 32768,
            },
        )
    )

    settings = {}
    for whole in (False, True):
        name = "whole_clip" if whole else "windows_8_12s"
        per_query: list[float] = []
        frame_counts: list[int] = []
        failures_total = 0
        for i in range(args.warmup):
            warm = candidates_for(rng, clips, metas, whole=whole, depth=args.depth)
            score(reranker, queries[i], warm)
        for qi, query in enumerate(queries):
            cands = candidates_for(rng, clips, metas, whole=whole, depth=args.depth)
            counts, _ = frames_per_candidate(
                cands, metas_by_name, fps=args.fps, max_frames=args.max_frames
            )
            frame_counts.extend(counts)
            started = time.perf_counter()
            results = score(reranker, query, cands)
            per_query.append(time.perf_counter() - started)
            failed = sum(1 for s in results if s is None)
            failures_total += failed
            print(f"[{name}] query {qi + 1}/{len(queries)}: {per_query[-1]:.3f}s, failures={failed}")
        settings[name] = {
            "per_query_s": per_query,
            "per_query_latency": ms_stats(per_query),
            "per_candidate_latency": ms_stats([t / args.depth for t in per_query]),
            "candidates_scored": len(frame_counts),
            "score_failures": failures_total,
            "frames_per_candidate": {
                "mean": statistics.fmean(frame_counts),
                "min": min(frame_counts),
                "max": max(frame_counts),
            },
        }

    result = {
        "started": datetime.now(UTC).isoformat(),
        "model": "finetuned-cr3",
        "merged_model": args.merged_model,
        "lora_path": str(args.adapter / "language"),
        "clips": str(args.clips),
        "n_clips": len(clips),
        "n_queries": len(queries),
        "rerank_depth": args.depth,
        "fps": args.fps,
        "max_frames": args.max_frames,
        "gpu": "NVIDIA L40S",
        "latency_definition": "Wall time to sample, encode and score one query's candidates grouped by sampled frame rate, after an untimed warm-up; excludes stage-1 retrieval and routing and queries without hits",
        "candidates": "random windows over the 80-clip subset (relevance does not affect latency)",
        "settings": settings,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v["per_query_latency"] for k, v in settings.items()}, indent=2))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
