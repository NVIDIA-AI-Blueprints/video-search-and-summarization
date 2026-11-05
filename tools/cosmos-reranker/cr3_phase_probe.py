#!/usr/bin/env python3
"""Phase-exclusive profiling of the reranker path to locate unaccounted time.

Runs V1 (fast decode) only. Wraps: rerank total, score_pairs, parallel_map
(media build), _score_batch (vLLM). Reports the residue per phase.
"""

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import numpy as np

import cosmos_reranker.reranking.cosmos_reason as cr
from cosmos_reranker.reranking import Candidate, CosmosReasonReranker, RerankerConfig
from cosmos_reranker.reranking.base import Segment
from cr3_opt_probe import clip_meta, fast_sampled_video_array


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--merged-model", required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--clips", type=Path, required=True)
    parser.add_argument("--queries-gt", type=Path, required=True)
    parser.add_argument("--queries", type=int, default=12)
    parser.add_argument("--depth", type=int, default=20)
    parser.add_argument("--seed", type=int, default=880821)
    args = parser.parse_args()

    clips = sorted(p for p in args.clips.iterdir() if p.suffix == ".mp4")
    gt = json.loads(args.queries_gt.read_text())
    queries = [q["query"] for q in gt["queries"]][: args.queries]
    metas = [clip_meta(p) for p in clips]
    rng = random.Random(args.seed)

    plan = []
    for _ in queries:
        picks = rng.sample(range(len(clips)), args.depth)
        cands = []
        for rank, idx in enumerate(picks):
            frames, vfps = metas[idx]
            duration = frames / vfps if vfps > 0 else 0.0
            length = min(float(rng.uniform(8.0, 12.0)), duration) if duration > 0 else 0.0
            start = float(rng.uniform(0.0, duration - length)) if duration > 0 else 0.0
            cands.append((str(clips[idx]), clips[idx].stem, start, start + length, -float(rank)))
        plan.append(cands)

    phases = {k: [] for k in ("rerank", "score_pairs", "map", "score_batch", "map_n", "score_n")}
    output_types: set[str] = set()

    orig_map = cr.parallel_map

    def timed_map(fn, items, workers: int = 8):
        t0 = time.perf_counter()
        out = orig_map(fn, items, workers)
        phases["map"].append(time.perf_counter() - t0)
        phases["map_n"].append(len(items))
        return out

    orig_batch = CosmosReasonReranker._score_batch

    def timed_batch(self, messages_list, **kwargs):
        t0 = time.perf_counter()
        outputs = orig_batch(self, messages_list, **kwargs)
        phases["score_batch"].append(time.perf_counter() - t0)
        phases["score_n"].append(len(messages_list))
        for o in list(outputs)[:1]:
            output_types.add(type(o).__name__)
            if phases["score_batch"] and len(phases["score_batch"]) == 1:
                try:
                    print("  prompt_token_ids sample len:", len(outputs[0].prompt_token_ids or []))
                except Exception as e:
                    print("  prompt_token_ids unavailable:", type(e).__name__)
        return outputs

    orig_pairs = CosmosReasonReranker.score_pairs

    def timed_pairs(self, pairs):
        t0 = time.perf_counter()
        out = orig_pairs(self, pairs)
        phases["score_pairs"].append(time.perf_counter() - t0)
        return out

    cr.parallel_map = timed_map
    CosmosReasonReranker._score_batch = timed_batch
    CosmosReasonReranker.score_pairs = timed_pairs

    reranker = CosmosReasonReranker(
        RerankerConfig(
            rerank_depth=args.depth,
            fps=1.0,
            max_frames=32,
            options={
                "model_id": args.merged_model,
                "lora_path": str(args.adapter / "language"),
                "max_lora_rank": 32,
                "max_model_len": 32768,
            },
        )
    )
    cr.sampled_video_array = fast_sampled_video_array

    def run(query, cands):
        fresh = [
            Candidate(Segment(i, c[1], c[2], c[3]), stage1_score=c[4], video_path=Path(c[0]))
            for i, c in enumerate(cands)
        ]
        t0 = time.perf_counter()
        reranker.rerank(query, fresh)
        phases["rerank"].append(time.perf_counter() - t0)

    for qi in range(2):
        run(queries[qi], plan[qi])
    for k in phases:
        phases[k].clear()

    for qi, query in enumerate(queries):
        run(query, plan[qi])
        i = len(phases["rerank"]) - 1
        n_map = len(phases["map"]) - 1
        n_bat = len(phases["score_batch"]) - 1
        n_pairs = len(phases["score_pairs"]) - 1
        print(
            f"q{qi + 1}: rerank {phases['rerank'][-1]:.3f}s | pairs {phases['score_pairs'][n_pairs]:.3f}s "
            f"| map {phases['map'][n_map]:.3f}s | batch {phases['score_batch'][n_bat]:.3f}s "
            f"| pairs_resid {phases['score_pairs'][n_pairs] - phases['map'][n_map] - phases['score_batch'][n_bat]:.3f}s "
            f"| rerank_resid {phases['rerank'][-1] - phases['score_pairs'][n_pairs]:.3f}s"
        )

    import statistics as st

    n = len(phases["rerank"])
    print("\nmeans over", n, "queries:")
    for k in ("rerank", "score_pairs", "map", "score_batch"):
        print(f"  {k}: {st.fmean(phases[k]):.3f}s")
    resid_pairs = [p - m - b for p, m, b in zip(phases["score_pairs"], phases["map"], phases["score_batch"])]
    resid_rerank = [r - p for r, p in zip(phases["rerank"], phases["score_pairs"])]
    print(f"  pairs residue: {st.fmean(resid_pairs):.3f}s  rerank residue: {st.fmean(resid_rerank):.3f}s")
    print(f"  batch calls/query: {len(phases['score_batch']) / n:.1f}  map calls/query: {len(phases['map']) / n:.1f}")
    print("  output types:", output_types)


if __name__ == "__main__":
    main()
