#!/usr/bin/env python3
"""V6: warm frame-store + uniform windows — the <500ms attempt on this host.

Two ideas:
1. Uniform whole-clip windows (as real ES chunk hits are uniform) make every
   candidate share the same sampled fps -> ONE engine call per query instead
   of 2-4 (removes per-call overhead).
2. A warm frame store: frames are extracted once per clip and served from RAM
   with pixels bit-identical to the live path (decode -> JPEG q95 -> PIL
   decode -> cached array + cached base64 part), so the media stage becomes a
   memory read. This mirrors serving reranks from ingest-time extracted frames.

Variants on identical seeded candidates:
  V0w: live path (seek decode + encode) on whole-clip windows  [reference]
  V6:  warm frame store + predecoded handoff + single engine call
Parity: V6 vs V0w must sit at the engine's own rerun noise floor.
"""

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import base64
import json
import math
import random
import statistics
import time
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

import cosmos_reranker.reranking.cosmos_reason as cr
from cosmos_reranker.reranking import Candidate, CosmosReasonReranker, RerankerConfig
from cosmos_reranker.reranking.base import Segment
from cosmos_reranker.reranking.frames import (
    VideoSamplingError,
    controlled_frame_indices,
)
from cr3_opt_probe import clip_meta, patch_vllm_video_loader

FRAME_CACHE: dict[tuple[str, int], tuple[str, np.ndarray]] = {}
CACHE_LOCK = __import__("threading").Lock()


def build_frame_store(clips: list[Path], fps: float, max_frames: int) -> None:
    """One-time: decode each clip once; cache (b64 jpeg part, decoded array)
    for every frame the whole-clip sampler can request. Pixels are bit-identical
    to the live encode path (same cv2 JPEG q95 bytes -> same PIL decode)."""
    t0 = time.perf_counter()
    for path in clips:
        total, vfps = clip_meta(path)
        probe = Candidate(Segment(0, path.stem, None, None), 0.0, video_path=path)
        indices = controlled_frame_indices(
            probe, fps=fps, max_frames=max_frames, total_frames=total, video_fps=vfps
        )
        want = sorted({int(i) for i in indices})
        cap = cv2.VideoCapture(str(path))
        got: dict[int, np.ndarray] = {}
        pos = 0
        try:
            while pos <= want[-1]:
                if not cap.grab():
                    break
                if pos in want:
                    ok, frame = cap.retrieve()
                    if ok and frame is not None:
                        got[pos] = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pos += 1
        finally:
            cap.release()
        for idx in want:
            ok, buf = cv2.imencode(
                ".jpg",
                cv2.cvtColor(got[idx], cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_JPEG_QUALITY, 95],
            )
            if not ok:
                raise SystemExit("encode failure in frame store build")
            raw = buf.tobytes()
            FRAME_CACHE[(str(path), idx)] = (
                base64.b64encode(raw).decode("ascii"),
                np.asarray(Image.open(BytesIO(raw))),
            )
    print(f"frame store: {len(FRAME_CACHE)} frames in {time.perf_counter() - t0:.1f}s")


def cached_video_data_url(self, candidate):
    """Warm-path _video_data_url: serve cached (b64 part, decoded array)."""
    if candidate.video_path is None:
        raise ValueError("Candidate has no video_path")
    total, vfps = clip_meta(candidate.video_path)
    indices = controlled_frame_indices(
        candidate, fps=self.fps, max_frames=self.max_frames, total_frames=total, video_fps=vfps
    )
    parts = []
    decoded = []
    for idx in indices:
        entry = FRAME_CACHE.get((str(candidate.video_path), int(idx)))
        if entry is None:
            raise VideoSamplingError(f"frame store miss: {candidate.video_path}#{idx}")
        parts.append(entry[0])
        decoded.append(entry[1])
    return "data:video/jpeg;base64," + ",".join(parts), (len(decoded) - 1) * vfps / (
        indices[-1] - indices[0]
    )


def stats(values: list[float]) -> dict | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "n": len(values),
        "mean_ms": statistics.fmean(values) * 1000,
        "median_ms": statistics.median(values) * 1000,
        "p95_ms": ordered[math.ceil(0.95 * len(ordered)) - 1] * 1000,
        "min_ms": ordered[0] * 1000,
        "max_ms": ordered[-1] * 1000,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--merged-model", required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--clips", type=Path, required=True)
    parser.add_argument("--queries-gt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--queries", type=int, default=15)
    parser.add_argument("--depth", type=int, default=20)
    parser.add_argument("--fps", type=float, default=1.0)
    parser.add_argument("--max-frames", type=int, default=32)
    parser.add_argument("--seed", type=int, default=880821)
    args = parser.parse_args()

    clips = sorted(p for p in args.clips.iterdir() if p.suffix == ".mp4")
    gt = json.loads(args.queries_gt.read_text())
    queries = [q["query"] for q in gt["queries"]][: args.queries]
    rng = random.Random(args.seed)

    # uniform whole-clip candidates, identical across variants
    plan = []
    for _ in queries:
        picks = rng.sample(range(len(clips)), args.depth)
        plan.append([(str(clips[i]), clips[i].stem) for i in picks])

    def fresh(row):
        return [
            Candidate(Segment(i, stem, None, None), stage1_score=-float(i), video_path=Path(p))
            for i, (p, stem) in enumerate(row)
        ]

    build_frame_store(clips, args.fps, args.max_frames)

    gpu_s: list[float] = []
    media_s: list[float] = []
    orig_batch = CosmosReasonReranker._score_batch
    orig_map = cr.parallel_map

    def timed_map(fn, items, workers: int = 8):
        t0 = time.perf_counter()
        out = orig_map(fn, items, workers)
        media_s.append(time.perf_counter() - t0)
        return out

    def timed_batch(self_, messages_list, **kwargs):
        t0 = time.perf_counter()
        out = orig_batch(self_, messages_list, **kwargs)
        gpu_s.append(time.perf_counter() - t0)
        return out

    cr.parallel_map = timed_map
    CosmosReasonReranker._score_batch = timed_batch

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

    def run(name: str) -> dict:
        media_s.clear()
        gpu_s.clear()
        per_query: list[float] = []
        scores: dict[int, list] = {}
        for qi in range(2):
            reranker.rerank(queries[qi], fresh(plan[qi]))
        media_s.clear()
        gpu_s.clear()
        for qi, query in enumerate(queries):
            cands = fresh(plan[qi])
            t0 = time.perf_counter()
            reranker.rerank(query, cands)
            per_query.append(time.perf_counter() - t0)
            scores[qi] = [c.rerank_score for c in cands]
            print(
                f"[{name}] q{qi + 1}/{len(queries)}: {per_query[-1]:.3f}s "
                f"(media {media_s[-1]:.3f}s, engine {gpu_s[-1]:.3f}s, calls {len(gpu_s)})"
            )
        return {
            "per_query": stats(per_query),
            "media": stats(media_s),
            "engine": stats(gpu_s),
            "engine_calls": len(gpu_s),
            "scores": scores,
        }

    results = {}
    results["V0w_live_wholeclip"] = run("V0w")

    cr.CosmosReasonReranker._video_data_url = cached_video_data_url
    patch_vllm_video_loader()
    results["V6_warm_store"] = run("V6")
    # engine-calls counter: reset per variant

    # determinism control: rerun V6 unchanged
    results["V6_rerun"] = run("V6b")

    def compare(a, b):
        A, B = results[a]["scores"], results[b]["scores"]
        same = sum(
            1
            for qi in A
            if sorted(range(len(A[qi])), key=lambda i: -(A[qi][i] or -9e9))
            == sorted(range(len(B[qi])), key=lambda i: -(B[qi][i] or -9e9))
        )
        md = max(
            max(abs((x or 0) - (y or 0)) for x, y in zip(A[qi], B[qi])) for qi in A
        )
        return same / len(A), md

    for a, b in (("V6_warm_store", "V0w_live_wholeclip"), ("V6_warm_store", "V6_rerun")):
        agree, md = compare(a, b)
        print(f"{a} vs {b}: identical order {agree:.0%}; max |delta| {md:.4f}")

    # V7: parallelize vLLM's serial per-request chat preprocessing (the HF
    # video processor resizes frames one request at a time ~90ms each).
    from concurrent.futures import ThreadPoolExecutor

    from vllm.entrypoints.offline_utils import conversation_to_seq

    llm_cls = type(reranker.llm)

    def parallel_add(self, messages, params, *, use_tqdm=True, lora_request=None, priority=None,
                     chat_template=None, chat_template_content_format="auto", add_generation_prompt=True,
                     continue_final_message=False, tools=None, chat_template_kwargs=None,
                     tokenization_kwargs=None, mm_processor_kwargs=None):
        seq_convs = conversation_to_seq(messages)
        seq_params = self._params_to_seq(params, len(seq_convs))
        seq_lora_requests = self._lora_request_to_seq(lora_request, len(seq_convs))
        seq_priority = self._priority_to_seq(priority, len(seq_convs))
        needs_parsing = (chat_template_kwargs and chat_template_kwargs.get("enable_thinking")) or tools
        if needs_parsing:
            self._adjust_params_for_parsing(seq_params)

        def one(conversation):
            return self._preprocess_chat_one(
                conversation,
                chat_template=chat_template,
                chat_template_content_format=chat_template_content_format,
                chat_template_kwargs=chat_template_kwargs,
                add_generation_prompt=add_generation_prompt,
                continue_final_message=continue_final_message,
                tools=tools,
                tokenization_kwargs=tokenization_kwargs,
                mm_processor_kwargs=mm_processor_kwargs,
            )

        with ThreadPoolExecutor(max_workers=min(8, len(seq_convs))) as pool:
            prompts = list(pool.map(one, seq_convs))
        return self._render_and_add_requests(
            prompts=prompts, params=seq_params, lora_requests=seq_lora_requests, priorities=seq_priority,
        )

    llm_cls._add_chat_requests = parallel_add
    results["V7_parallel_preproc"] = run("V7")
    agree7, md7 = compare("V7_parallel_preproc", "V6_warm_store")
    print(f"V7 vs V6: identical order {agree7:.0%}; max |delta| {md7:.4f}")

    payload = {
        "started": datetime.now(UTC).isoformat(),
        "gpu": "NVIDIA L40S (4 vCPU host)",
        "queries": args.queries,
        "depth": args.depth,
        "fps": args.fps,
        "max_frames": args.max_frames,
        "uniform_windows": "whole-clip",
        "frame_store_frames": len(FRAME_CACHE),
        "variants": {k: {kk: vv for kk, vv in v.items() if kk != "scores"} for k, v in results.items()},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2))
    for name, r in results.items():
        p, m, g = r["per_query"], r["media"], r["engine"]
        print(
            f"{name}: mean {p['mean_ms']:.0f}ms median {p['median_ms']:.0f}ms p95 {p['p95_ms']:.0f}ms "
            f"| media {m['mean_ms'] if m else 0:.0f}ms | engine {g['mean_ms'] if g else 0:.0f}ms "
            f"| calls {r['engine_calls']}"
        )
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
