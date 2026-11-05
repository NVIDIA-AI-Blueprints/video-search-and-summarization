#!/usr/bin/env python3
"""Segment-level profiling of CosmosReasonReranker.score_pairs (V1 fast decode).

Reimplements score_pairs verbatim with per-segment timers so the residue
between parallel_map and _score_batch is attributed exactly.
"""

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import cosmos_reranker.reranking.cosmos_reason as cr
from cosmos_reranker.reranking import Candidate, CosmosReasonReranker, RerankerConfig
from cosmos_reranker.reranking.base import Segment
from cosmos_reranker.reranking.common import homogeneous_candidate_modality
from cr3_opt_probe import clip_meta, fast_sampled_video_array


def timed_score_pairs(self, pairs):
    t_pairs0 = time.perf_counter()
    if not pairs:
        return []
    t0 = time.perf_counter()
    modality = homogeneous_candidate_modality([c for _, c in pairs])
    mm_processor_kwargs = (
        self.video_mm_processor_kwargs if modality == "video" else self.image_mm_processor_kwargs
    )
    t_check = time.perf_counter() - t0

    t0 = time.perf_counter()
    prepared = self._prepare_messages(pairs)
    t_map = time.perf_counter() - t0

    t0 = time.perf_counter()
    grouped: dict[float, list] = {}
    for index, item in enumerate(prepared):
        messages, sample_fps = item
        grouped.setdefault(sample_fps, []).append((index, messages))
    t_group = time.perf_counter() - t0

    results: list = [None] * len(pairs)
    seg_setfps = 0.0
    seg_batch = 0.0
    seg_collect = 0.0
    group_log = []
    for sample_fps, items in grouped.items():
        t0 = time.perf_counter()
        self._set_video_fps(sample_fps)
        seg_setfps += time.perf_counter() - t0
        t0 = time.perf_counter()
        scored = self._score_batch(
            [messages for _, messages in items], mm_processor_kwargs=mm_processor_kwargs
        )
        seg_batch += time.perf_counter() - t0
        t0 = time.perf_counter()
        for (index, _messages), result in zip(items, scored):
            results[index] = result
        seg_collect += time.perf_counter() - t0
        group_log.append((len(items), round(sample_fps, 4)))
    if any(r is None for r in results):
        raise RuntimeError("vLLM returned an incomplete video score batch")
    t_pairs = time.perf_counter() - t_pairs0
    seg_other = t_pairs - t_check - t_map - t_group - seg_setfps - seg_batch - seg_collect
    print(
        f"  pairs {t_pairs:.3f}s = check {t_check:.3f} | prepare {t_map:.3f} | group {t_group:.3f} "
        f"| setfps {seg_setfps:.3f} | batch {seg_batch:.3f} | collect {seg_collect:.3f} "
        f"| other {seg_other:.3f} | groups={group_log}"
    )
    return self.candidate_scores([r for r in results if r is not None])


def install_prepare(self, orig_map):
    def prepare(pairs):
        def one(item):
            return self._messages(item[0], item[1])

        return orig_map(one, pairs)

    self._prepare_messages = prepare


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
        row = []
        for rank, idx in enumerate(picks):
            frames, vfps = metas[idx]
            duration = frames / vfps if vfps > 0 else 0.0
            length = min(float(rng.uniform(8.0, 12.0)), duration) if duration > 0 else 0.0
            start = float(rng.uniform(0.0, duration - length)) if duration > 0 else 0.0
            row.append((str(clips[idx]), clips[idx].stem, start, start + length, -float(rank)))
        plan.append(row)

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
    install_prepare(reranker, cr.parallel_map)
    reranker.score_pairs = timed_score_pairs.__get__(reranker)

    def run(query, row):
        cands = [
            Candidate(Segment(i, c[1], c[2], c[3]), stage1_score=c[4], video_path=Path(c[0]))
            for i, c in enumerate(row)
        ]
        t0 = time.perf_counter()
        reranker.rerank(query, cands)
        return time.perf_counter() - t0

    for qi in range(2):
        run(queries[qi], plan[qi])
    print("--- timed ---")
    totals = []
    for qi, query in enumerate(queries):
        total = run(query, plan[qi])
        totals.append(total)
        print(f"q{qi + 1}: rerank {total:.3f}s")
    import statistics as st

    print(f"\nmean rerank: {st.fmean(totals):.3f}s over {len(totals)} queries")


if __name__ == "__main__":
    main()
