#!/usr/bin/env python3
"""cProfile the V6 warm-store rerank path to attribute the ~1.6s mystery chunk."""

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import cProfile
import io
import json
import pstats
import random
import time
from pathlib import Path

import cosmos_reranker.reranking.cosmos_reason as cr
from cosmos_reranker.reranking import Candidate, CosmosReasonReranker, RerankerConfig
from cosmos_reranker.reranking.base import Segment
from cr3_opt_probe import patch_vllm_video_loader
from cr3_v6_probe import build_frame_store, cached_video_data_url


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--merged-model", required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--clips", type=Path, required=True)
    parser.add_argument("--queries-gt", type=Path, required=True)
    parser.add_argument("--queries", type=int, default=5)
    parser.add_argument("--depth", type=int, default=20)
    parser.add_argument("--seed", type=int, default=880821)
    args = parser.parse_args()

    clips = sorted(p for p in args.clips.iterdir() if p.suffix == ".mp4")
    gt = json.loads(args.queries_gt.read_text())
    queries = [q["query"] for q in gt["queries"]][: args.queries]
    rng = random.Random(args.seed)
    plan = [rng.sample(range(len(clips)), args.depth) for _ in queries]

    build_frame_store(clips, 1.0, 32)

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
    cr.CosmosReasonReranker._video_data_url = cached_video_data_url
    patch_vllm_video_loader()

    def row(picks):
        return [
            Candidate(
                Segment(i, clips[j].stem, None, None), stage1_score=-float(i), video_path=clips[j]
            )
            for i, j in enumerate(picks)
        ]

    for qi in range(2):
        reranker.rerank(queries[qi], row(plan[qi]))

    profiler = cProfile.Profile()
    profiler.enable()
    t0 = time.perf_counter()
    for qi, query in enumerate(queries):
        reranker.rerank(query, row(plan[qi]))
    total = time.perf_counter() - t0
    profiler.disable()
    print(f"\nprofiled {len(queries)} queries in {total:.2f}s -> {total / len(queries):.2f}s/query")

    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(28)
    print(stream.getvalue())


if __name__ == "__main__":
    main()
