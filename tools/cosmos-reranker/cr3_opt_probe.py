#!/usr/bin/env python3
"""Incremental reranker-pipeline optimization probe.

Loads the fine-tuned CR3 once, then measures three variants over the SAME
seeded candidates (15 queries x 20 random candidates, 1 FPS):

  V0 baseline: upstream score_pairs path (seek-decode -> JPEG q95 -> base64
     -> vLLM PIL decode -> processor), with stage timers.
  V1 fast decode: sequential grab/retrieve instead of per-frame keyframe
     seeks (bit-exact frames; scores must be identical).
  V2 raw frames: also removes the JPEG encode + base64 + PIL decode round
     trip inside vLLM (frames reach the processor losslessly; scores shift
     slightly - reports ranking agreement vs V0).

Reports per-stage decomposition, prefill token counts (from vLLM prompt ids),
and score/ranking parity between variants. No upstream files are modified.
"""

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

import cosmos_reranker.reranking.cosmos_reason as cr
from cosmos_reranker.reranking import Candidate, CosmosReasonReranker, RerankerConfig
from cosmos_reranker.reranking.base import Segment
from cosmos_reranker.reranking.frames import (
    SampledVideoArray,
    VideoSamplingError,
    controlled_frame_indices,
)

RAW_FRAMES: dict[str, np.ndarray] = {}
RAW_LOCK = threading.Lock()
META_CACHE: dict[str, tuple[int, float]] = {}


def clip_meta(path: Path) -> tuple[int, float]:
    if str(path) in META_CACHE:
        return META_CACHE[str(path)]
    cap = cv2.VideoCapture(str(path))
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    finally:
        cap.release()
    META_CACHE[str(path)] = (frames, fps)
    return frames, fps


def fast_sampled_video_array(candidate, fps: float, max_frames: int) -> SampledVideoArray:
    """Sequential grab/retrieve decode; bit-exact with seek-based sampling."""
    if candidate.video_path is None:
        raise ValueError("Candidate has no video_path")
    total, video_fps = clip_meta(candidate.video_path)
    if total <= 0:
        raise VideoSamplingError("Cannot sample video with no frames")
    indices = controlled_frame_indices(
        candidate, fps=fps, max_frames=max_frames, total_frames=total, video_fps=video_fps
    )
    want = sorted({int(i) for i in indices})
    cap = cv2.VideoCapture(str(candidate.video_path))
    try:
        if not cap.isOpened():
            raise VideoSamplingError(f"Could not open video for frame sampling: {candidate.video_path}")
        got: dict[int, np.ndarray] = {}
        pos = 0
        last = want[-1]
        while pos <= last:
            if not cap.grab():
                break
            if pos in want:
                ok, frame = cap.retrieve()
                if not ok or frame is None:
                    raise VideoSamplingError(f"Could not read frame {pos}")
                got[pos] = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pos += 1
    finally:
        cap.release()
    missing = {i for i in want if i not in got}
    if missing:
        raise VideoSamplingError(f"Could not read frames {missing[:3]}")
    frames = [got[i] for i in want]
    if video_fps <= 0.0:
        video_fps = max(1.0, float(fps))
    if len(indices) <= 1:
        duration = 1.0 / video_fps
        sample_fps = video_fps
    else:
        frame_span = want[-1] - want[0]
        duration = max(1e-6, (frame_span + 1) / video_fps)
        sample_fps = (len(frames) - 1) * video_fps / frame_span
    return SampledVideoArray(
        frames=np.stack(frames, axis=0),
        frame_indices=[int(i) for i in indices],
        source_fps=video_fps,
        sample_fps=sample_fps,
        duration=duration,
    )



def predecode_video_data_url(self, candidate):
    """V3: identical JPEG bytes as upstream, but we PIL-decode each JPEG once
    (in the parallel map pool) and hand vLLM the exact arrays it would produce —
    removing vLLM's serial PIL decode. Pixels are bit-identical to V0."""
    import base64
    from io import BytesIO

    from PIL import Image

    sample = fast_sampled_video_array(candidate, self.fps, self.max_frames)
    parts = []
    for frame in sample.frames:
        ok, buffer = cv2.imencode(
            ".jpg",
            cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
            [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality],
        )
        if not ok:
            raise VideoSamplingError("Could not JPEG-encode CR3 sampled frame")
        raw = buffer.tobytes()
        part = base64.b64encode(raw).decode("ascii")
        decoded = np.asarray(Image.open(BytesIO(raw)))
        with RAW_LOCK:
            RAW_FRAMES[part] = decoded
        parts.append(part)
    return "data:video/jpeg;base64," + ",".join(parts), sample.sample_fps


def patch_vllm_video_loader():
    """Serve stashed raw frames inside vLLM instead of base64-decoding JPEGs."""
    from vllm.multimodal.media.video import VideoMediaIO

    original = VideoMediaIO.load_base64

    def patched(self, media_type: str, data: str):
        if media_type.lower() != "video/jpeg" or "," not in data:
            return original(self, media_type, data)
        num_frames = self.num_frames
        parts = data.split(",", num_frames)[:num_frames] if num_frames > 0 else data.split(",")
        with RAW_LOCK:
            cached = {p: RAW_FRAMES.pop(p) for p in parts if p in RAW_FRAMES}
        if not cached:
            return original(self, media_type, data)

        def load_frame(part: str) -> np.ndarray:
            if part in cached:
                return cached[part]
            return np.asarray(self.image_io.load_base64("image/jpeg", part))

        frames = np.stack([load_frame(p) for p in parts])
        total = int(frames.shape[0])
        fps = float(self.kwargs.get("fps", 1))
        frames_indices = self.kwargs.get("frames_indices")
        if frames_indices is not None and len(frames_indices) != total:
            raise ValueError("frames_indices length mismatch")
        total_num_frames = self.kwargs.get("total_num_frames", total)
        if total_num_frames < total:
            raise ValueError("total_num_frames < frames sent")
        duration = self.kwargs.get("duration")
        if duration is None:
            duration = total_num_frames / fps if fps > 0 else 0.0
        metadata = {
            "total_num_frames": total_num_frames,
            "fps": fps,
            "duration": duration,
            "video_backend": "jpeg_sequence",
            "frames_indices": frames_indices or list(range(total)),
            "do_sample_frames": self.kwargs.get("do_sample_frames", False),
        }
        return frames, metadata

    VideoMediaIO.load_base64 = patched


def stats(values: list[float]) -> dict:
    ordered = sorted(values)
    if not values:
        return None
    return {
        "n": len(values),
        "mean_ms": statistics.fmean(values) * 1000,
        "median_ms": statistics.median(values) * 1000,
        "p95_ms": ordered[math.ceil(0.95 * len(ordered)) - 1] * 1000,
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
    metas = [clip_meta(p) for p in clips]
    rng = random.Random(args.seed)

    plan: list[list[dict]] = []
    for _ in queries:
        picks = rng.sample(range(len(clips)), args.depth)
        cands = []
        for rank, idx in enumerate(picks):
            frames, vfps = metas[idx]
            duration = frames / vfps if vfps > 0 else 0.0
            length = min(float(rng.uniform(8.0, 12.0)), duration) if duration > 0 else 0.0
            start = float(rng.uniform(0.0, duration - length)) if duration > 0 else 0.0
            cands.append(
                {
                    "video": str(clips[idx]),
                    "stem": clips[idx].stem,
                    "start": start,
                    "end": start + length,
                    "stage1": -float(rank),
                }
            )
        plan.append(cands)

    def fresh(cands: list[dict]) -> list[Candidate]:
        return [
            Candidate(
                Segment(i, c["stem"], c["start"], c["end"]),
                stage1_score=c["stage1"],
                video_path=Path(c["video"]),
            )
            for i, c in enumerate(cands)
        ]

    # ---- V1 sampler parity check (bit-exact frames, no model needed) ----
    orig_sampler = cr.sampled_video_array
    orig_data_url = cr.CosmosReasonReranker._video_data_url
    probe = Candidate(Segment(0, clips[0].stem, None, None), 0.0, video_path=clips[0])
    a = orig_sampler(probe, args.fps, args.max_frames).frames
    b = fast_sampled_video_array(probe, args.fps, args.max_frames).frames
    parity_exact = a.shape == b.shape and bool(np.array_equal(a, b))
    print(f"V1 sampler parity (bit-exact frames): {parity_exact}")
    if not parity_exact:
        raise SystemExit("fast sampler is not bit-exact; aborting")

    # ---- timing instrumentation ----
    media_s: list[float] = []
    gpu_s: list[float] = []
    prompt_tokens: list[int] = []

    timed_parallel_map = cr.parallel_map

    def instrumented_map(fn, items, workers: int = 8):
        t0 = time.perf_counter()
        out = timed_parallel_map(fn, items, workers)
        media_s.append(time.perf_counter() - t0)
        return out

    original_score_batch = CosmosReasonReranker._score_batch

    def timed_score_batch(self, messages_list, **kwargs):
        t0 = time.perf_counter()
        outputs = original_score_batch(self, messages_list, **kwargs)
        gpu_s.append(time.perf_counter() - t0)
        try:
            prompt_tokens.extend(len(o.prompt_token_ids) for o in outputs)
        except AttributeError:
            pass
        return outputs

    cr.parallel_map = instrumented_map
    CosmosReasonReranker._score_batch = timed_score_batch

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

    def run_variant(name: str, n: int) -> dict:
        media_s.clear()
        gpu_s.clear()
        prompt_tokens.clear()
        per_query: list[float] = []
        scores: dict[int, list[float | None]] = {}
        # warm-up (untimed): 2 queries
        for qi in range(2):
            reranker.rerank(queries[qi], fresh(plan[qi]))
        media_s.clear()
        gpu_s.clear()
        prompt_tokens.clear()
        for qi, query in enumerate(queries):
            cands = fresh(plan[qi])
            t0 = time.perf_counter()
            reranker.rerank(query, cands)
            per_query.append(time.perf_counter() - t0)
            scores[qi] = [c.rerank_score for c in cands]
            print(
                f"[{name}] query {qi + 1}/{len(queries)}: {per_query[-1]:.3f}s "
                f"(media {media_s[-1]:.3f}s, vllm {gpu_s[-1]:.3f}s)"
            )
        return {
            "per_query_s": per_query,
            "per_query": stats(per_query),
            "media_stage": stats(media_s),
            "vllm_stage": stats(gpu_s),
            "prompt_tokens_per_query": sum(prompt_tokens) / max(len(queries), 1),
            "scores": scores,
        }

    results: dict[str, dict] = {}

    # V0: baseline
    results["V0_baseline"] = run_variant("V0", args.queries)

    # V1: fast sequential decode
    cr.sampled_video_array = fast_sampled_video_array
    results["V1_fast_decode"] = run_variant("V1", args.queries)

    # V1 must be bit-exact with V0
    mism = sum(
        1
        for qi, scores in results["V0_baseline"]["scores"].items()
        if scores != results["V1_fast_decode"]["scores"][qi]
    )
    print(f"V1 score parity vs V0: {args.queries - mism}/{args.queries} queries identical")


    # V3: pre-decode handoff — JPEG bytes identical to V0, but each JPEG is
    # PIL-decoded once inside our parallel map pool and handed to vLLM as an
    # array, so vLLM does no serial PIL work. Pixels bit-identical to V0.
    cr.CosmosReasonReranker._video_data_url = predecode_video_data_url
    patch_vllm_video_loader()
    results["V3_predecode"] = run_variant("V3", args.queries)

    def rank_agreement(a: dict[int, list], b: dict[int, list]) -> tuple[float, float]:
        same_order = 0
        max_delta = 0.0
        for qi, scores_a in a.items():
            rb = [x if x is not None else float("-inf") for x in b[qi]]
            ra = [x if x is not None else float("-inf") for x in scores_a]
            order_a = sorted(range(len(ra)), key=lambda i: -ra[i])
            order_b = sorted(range(len(rb)), key=lambda i: -rb[i])
            if order_a == order_b:
                same_order += 1
            max_delta = max(max_delta, max(abs(x - y) for x, y in zip(ra, rb)))
        return same_order / len(a), max_delta

    agree, max_delta = rank_agreement(results["V0_baseline"]["scores"], results["V1_fast_decode"]["scores"])
    print(f"V1 vs V0: identical candidate order on {agree:.0%} of queries; max |delta score| = {max_delta:.3f}")

    # determinism control: rerun V0 unchanged
    cr.sampled_video_array = orig_sampler
    cr.CosmosReasonReranker._video_data_url = orig_data_url
    results["V0_rerun"] = run_variant("V0b", args.queries)
    cr.CosmosReasonReranker._video_data_url = predecode_video_data_url
    agree0, delta0 = rank_agreement(results["V0_baseline"]["scores"], results["V0_rerun"]["scores"])
    print(f"V0 rerun vs V0 (noise floor): identical order {agree0:.0%}; max |delta| {delta0:.4f}")
    cr.sampled_video_array = fast_sampled_video_array

    agree3, delta3 = rank_agreement(results["V0_baseline"]["scores"], results["V3_predecode"]["scores"])
    identical3 = sum(
        1
        for qi, scores in results["V0_baseline"]["scores"].items()
        if scores == results["V3_predecode"]["scores"][qi]
    )
    print(f"V3 vs V0: bit-identical scores on {identical3}/{args.queries}; identical order {agree3:.0%}; max |delta| {delta3:.4f}")

    # V4: V3 + chunked CPU/GPU overlap (prep next chunk while the engine scores the current one)
    from concurrent.futures import ThreadPoolExecutor

    orig_map = timed_parallel_map

    def chunked_score(query, cands, chunk_size=5):
        chunks = [cands[i : i + chunk_size] for i in range(0, len(cands), chunk_size)]

        def prepare(chunk):
            return orig_map(lambda c: reranker._messages(query, c), chunk)

        def score_prepared(prepared):
            grouped = {}
            for idx, (msgs, fps) in enumerate(prepared):
                grouped.setdefault(fps, []).append((idx, msgs))
            out = [None] * len(prepared)
            for fps, items in grouped.items():
                reranker._set_video_fps(fps)
                scored = reranker._score_batch(
                    [m for _, m in items], mm_processor_kwargs=reranker.video_mm_processor_kwargs
                )
                for (idx, _), r in zip(items, scored):
                    out[idx] = r
            return out

        pending = prepare(chunks[0])
        offset = 0
        with ThreadPoolExecutor(1) as pool:
            for chunk in chunks[1:]:
                future = pool.submit(score_prepared, pending)
                next_prepared = prepare(chunk)
                res = future.result()
                for c, s in zip(chunks[offset], res):
                    c.rerank_score = None if s is None else float(s[0])
                    c.rerank_meta = s[1] if s is not None else None
                offset += 1
                pending = next_prepared
            res = score_prepared(pending)
            for c, s in zip(chunks[offset], res):
                c.rerank_score = None if s is None else float(s[0])
                c.rerank_meta = s[1] if s is not None else None

    def run_overlap_variant(name, n, chunk_size=5):
        media_s.clear()
        gpu_s.clear()
        prompt_tokens.clear()
        per_query = []
        scores = {}
        for qi in range(2):
            chunked_score(queries[qi], fresh(plan[qi]), chunk_size)
        for qi, query in enumerate(queries):
            cands = fresh(plan[qi])
            t0 = time.perf_counter()
            chunked_score(query, cands, chunk_size)
            per_query.append(time.perf_counter() - t0)
            scores[qi] = [c.rerank_score for c in cands]
            print(f"[{name}] query {qi + 1}/{len(queries)}: {per_query[-1]:.3f}s")
        return {
            "per_query_s": per_query,
            "per_query": stats(per_query),
            "media_stage": stats(media_s),
            "vllm_stage": stats(gpu_s),
            "prompt_tokens_per_query": sum(prompt_tokens) / max(len(queries), 1),
            "scores": scores,
        }

    results["V4_overlap"] = run_overlap_variant("V4", args.queries)
    agree4, delta4 = rank_agreement(results["V0_baseline"]["scores"], results["V4_overlap"]["scores"])
    print(f"V4 vs V0: identical order {agree4:.0%}; max |delta| {delta4:.4f}")

    payload = {
        "started": datetime.now(UTC).isoformat(),
        "gpu": "NVIDIA L40S",
        "queries": args.queries,
        "depth": args.depth,
        "fps": args.fps,
        "max_frames": args.max_frames,
        "n_clips": len(clips),
        "v1_bit_exact": parity_exact,
        "v2_rank_agreement": agree,
        "v2_max_score_delta": max_delta,
        "v3_bit_identical_queries": identical3,
        "v4_rank_agreement": agree4,
        "v4_max_score_delta": delta4,
        "variants": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2))
    for name, r in results.items():
        media = r.get("media_stage") or {}
        vllm = r.get("vllm_stage") or {}
        print(
            f"{name}: query mean {r['per_query']['mean_ms']:.0f}ms | media "
            f"{media.get('mean_ms', float('nan')):.0f}ms | vllm "
            f"{vllm.get('mean_ms', float('nan')):.0f}ms"
        )
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
