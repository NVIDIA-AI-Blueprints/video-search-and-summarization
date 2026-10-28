# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Rerank a finished raw clip evaluation with Cosmos 3 Nano and score both orders.

Runs in the tools/cosmos-reranker eval environment against the output of
run_raw_clip_eval.py. Each query's saved hits are the stage-1 candidates, so
metrics with and without the reranker come from the same retrieval, for every
mode and selected route. The VSS deployment is never contacted. One model load
serves every fps x max-frames setting.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Protocol

import report_raw_clip_eval as report
import run_raw_clip_eval as runner
from flows import clip_metrics

DOMAINS = ("event", "pas", "overall")


@dataclass(frozen=True)
class Window:
    """One stage-1 hit as a clip-relative video window."""

    index: int
    sensor_id: str
    video_path: Path
    start_s: float
    end_s: float
    stage1_score: float
    outside_clip: bool = False


class Scorer(Protocol):
    def score(
        self, query: str, windows: list[Window], fps: float, max_frames: int
    ) -> list[tuple[float | None, dict | None]]: ...

    def frames(self, window: Window, fps: float, max_frames: int) -> int | None: ...


class IdentityScorer:
    """Keeps stage-1 order; reproduces the original metrics exactly."""

    def score(self, query, windows, fps, max_frames):
        return [(-float(w.index), None) for w in windows]

    def frames(self, window, fps, max_frames):
        return None


class CosmosScorer:
    """Pointwise Cosmos 3 Nano video-window scores from the upstream package."""

    def __init__(self, args):
        from cosmos_reranker.reranking import CosmosReasonReranker, RerankerConfig

        options = {
            "model_id": args.model_id,
            "jpeg_quality": args.jpeg_quality,
            "gpu_memory_utilization": args.gpu_memory_utilization,
            "tensor_parallel_size": args.tensor_parallel_size,
            "max_model_len": args.max_model_len,
            "enforce_eager": args.enforce_eager,
            "max_lora_rank": args.max_lora_rank,
        }
        if args.lora_path:
            options["lora_path"] = str(args.lora_path)
        self.reranker = CosmosReasonReranker(
            RerankerConfig(
                name="cosmos_reason",
                rerank_depth=args.rerank_depth,
                fps=args.fps[0],
                max_frames=args.max_frames[0],
                options=options,
            )
        )
        self._probes: dict[Path, tuple[int, float]] = {}

    def _candidates(self, windows):
        from cosmos_reranker.reranking import Candidate, Segment

        return [
            Candidate(
                Segment(w.index, w.sensor_id, w.start_s, w.end_s),
                w.stage1_score,
                video_path=w.video_path,
            )
            for w in windows
        ]

    def _score(self, query, candidates):
        pairs = [(query, c) for c in candidates]
        return [(r.score, r.meta) for r in self.reranker.score_pairs(pairs)]

    def _score_one(self, query, candidate):
        from cosmos_reranker.reranking.frames import VideoSamplingError

        try:
            return self._score(query, [candidate])[0]
        except VideoSamplingError as error:
            return None, {"error": f"{type(error).__name__}: {error}"}

    def score(self, query, windows, fps, max_frames):
        from cosmos_reranker.reranking.frames import VideoSamplingError

        # The upstream sampler reads the requested fps and cap per candidate;
        # the reranker passes each sample's actual spacing to vLLM for timestamps.
        self.reranker.fps, self.reranker.max_frames = float(fps), int(max_frames)
        candidates = self._candidates(windows)
        try:
            return self._score(query, candidates)
        except VideoSamplingError:
            # One undecodable window fails the whole upstream batch; rescore
            # one by one so only that candidate falls back to stage-1 order.
            return [self._score_one(query, c) for c in candidates]

    def frames(self, window, fps, max_frames):
        import cv2
        from cosmos_reranker.reranking.frames import (
            VideoSamplingError,
            controlled_frame_indices,
        )

        if window.video_path not in self._probes:
            cap = cv2.VideoCapture(str(window.video_path))
            try:
                self._probes[window.video_path] = (
                    int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0),
                    float(cap.get(cv2.CAP_PROP_FPS) or 0.0),
                )
            finally:
                cap.release()
        total, video_fps = self._probes[window.video_path]
        (candidate,) = self._candidates([window])
        try:
            return len(
                controlled_frame_indices(
                    candidate,
                    fps=fps,
                    max_frames=max_frames,
                    total_frames=total,
                    video_fps=video_fps,
                )
            )
        except VideoSamplingError:
            return None


def utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return (
        parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    )


def hit_window(hit, index, clips, origin, pad_s) -> Window:
    """Map a hit's absolute bounds onto its clip file, padded and clamped."""
    clip = clips.get(hit.get("sensor_id"))
    if clip is None:
        raise ValueError(f"Hit {index} names a sensor outside the gallery")
    if not hit.get("start_time") or not hit.get("end_time"):
        raise ValueError(f"Hit {index} has no time bounds to rerank")
    start = (utc(hit["start_time"]) - origin).total_seconds()
    end = (utc(hit["end_time"]) - origin).total_seconds()
    if not (math.isfinite(start) and math.isfinite(end)) or end < start:
        raise ValueError(f"Hit {index} has invalid time bounds")
    duration = clip["duration_s"]
    outside = start > duration or end < 0
    start = min(max(0.0, start - pad_s), duration)
    end = min(max(0.0, end + pad_s), duration)
    return Window(
        index=index,
        sensor_id=hit["sensor_id"],
        video_path=clip["path"],
        start_s=start,
        end_s=end,
        stage1_score=float(hit.get("similarity") or 0.0),
        outside_clip=outside,
    )


def order_by_scores(windows: list[Window], scores: list[float | None]) -> list[int]:
    """Scored windows first, best first; failures keep stage-1 rank after them.

    Ties break on stage-1 score, then stage-1 rank, as the upstream
    ScoringReranker does. Upstream orders failures by stage-1 score; VSS rank
    order is kept here because a route's rank need not follow its similarity.
    """
    if len(scores) != len(windows):
        raise ValueError(f"got {len(scores)} scores for {len(windows)} candidates")
    scored = [(s, w) for s, w in zip(scores, windows) if s is not None]
    scored.sort(key=lambda item: (-item[0], -item[1].stage1_score, item[1].index))
    failed = [w for s, w in zip(scores, windows) if s is None]
    return [w.index for _, w in scored] + [w.index for w in failed]


def zscores(values: list[float]) -> list[float]:
    spread = statistics.pstdev(values) if len(values) > 1 else 0.0
    center = statistics.fmean(values)
    return [(v - center) / spread if spread > 0 else 0.0 for v in values]


def fused_scores(
    windows: list[Window], scores: list[float | None], alpha: float
) -> list[float | None]:
    """alpha * z(stage-1) + (1 - alpha) * z(reranker), z within this query."""
    kept = [i for i, s in enumerate(scores) if s is not None]
    if not kept:
        return list(scores)
    stage1 = zscores([windows[i].stage1_score for i in kept])
    rerank = zscores([float(scores[i]) for i in kept])
    out: list[float | None] = [None] * len(scores)
    for i, a, b in zip(kept, stage1, rerank):
        out[i] = alpha * a + (1 - alpha) * b
    return out


def variant_name(alpha: float) -> str:
    return f"fused-a{alpha:.2f}"


def setting_name(fps: float, max_frames: int) -> str:
    return f"fps-{fps:g}_frames-{max_frames}"


def capture_path(run_dir: Path, mode: str, k: int, multi: bool, qid: str) -> Path:
    return run_dir / "captures" / mode / (f"top-k-{k}" if multi else "") / f"{qid}.json"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def recorded_hash(recorded: dict[str, str], *parts: str) -> str | None:
    found = [v for k, v in recorded.items() if Path(k).parts[-len(parts) :] == parts]
    return found[0] if len(found) == 1 else None


def load_run(args):
    run_dir = args.run_dir
    identity = runner.read(run_dir / "identity.json")
    complete = run_dir / "complete.json"
    if not complete.is_file() or not runner.read(complete).get("all_modes_complete"):
        raise ValueError("Rerank only a completed run_raw_clip_eval.py run")
    recorded = identity["input_sha256"]
    for path, expected in (
        # The runner records the ingestion manifest first.
        (args.ingestion_manifest, next(iter(recorded.values()), None)),
        (
            args.dataset_root / "gt/queries_gt.json",
            recorded_hash(recorded, "gt", "queries_gt.json"),
        ),
        (
            args.dataset_root / "additional_gt/clips_gt.json",
            recorded_hash(recorded, "additional_gt", "clips_gt.json"),
        ),
    ):
        if sha256(path) != expected:
            raise ValueError(f"{path} differs from the inputs the run recorded")
    _, manifest, sensors, _ = runner.load_inputs(
        args.dataset_root, args.ingestion_manifest, None
    )
    for name, expected in (manifest.get("file_sha256") or {}).items():
        if sha256(args.dataset_root / "clips" / name) != expected:
            raise ValueError(f"Clip {name} differs from the ingested file")
    queries = runner.read(run_dir / "queries.json")["queries"]
    modes = args.modes or identity["modes"]
    if not set(modes) <= set(identity["modes"]):
        raise ValueError("Requested modes were not evaluated in this run")
    clips = {
        c["sensor_id"]: {
            "path": (args.dataset_root / "clips" / c["filename"]).resolve(),
            "duration_s": float(c["duration_s"]),
        }
        for c in manifest["clips"]
    }
    return {
        "identity": identity,
        "queries": queries,
        "sensors": sensors,
        "clips": clips,
        "origin": utc(manifest["upload_timestamp"]),
        "modes": modes,
        "ks": sorted(identity["top_k_values"]),
        "multi": bool(identity["multi_k"]),
    }


def query_text(query, record, choice):
    if choice == "original":
        return query["query"]
    payload = record["payload"]
    return payload.get("query") or payload["attributes"][0]


def ms_stats(values):
    if not values:
        return None
    ordered = sorted(values)
    return {
        "mean_ms": statistics.fmean(values) * 1000,
        "median_ms": statistics.median(values) * 1000,
        "p95_ms": ordered[math.ceil(0.95 * len(ordered)) - 1] * 1000,
    }


def mean_ap(rows, variant, k, keep):
    values = [r["ap"][variant][str(k)] for r in rows if keep(r)]
    return 100 * statistics.fmean(values) if values else None


def summarize(rows, variants, ks):
    """mAP per variant and depth, by domain and selected route, plus cost."""
    routes = sorted({r["path"] for r in rows})
    by_k = {}
    for k in ks:
        by_k[str(k)] = {
            variant: {
                **{
                    domain: mean_ap(
                        rows,
                        variant,
                        k,
                        lambda r, d=domain: d == "overall" or r["domain"] == d,
                    )
                    for domain in DOMAINS
                },
                "by_route": {
                    route: mean_ap(rows, variant, k, lambda r, p=route: r["path"] == p)
                    for route in routes
                },
            }
            for variant in variants
        }
    frames = [f for r in rows for f in r["frames"] if f is not None]
    # Queries without hits score nothing; keep them out of reranker latency.
    timed = [r for r in rows if r["candidates"]]
    return {
        "queries": len(rows),
        "domains": {d: sum(r["domain"] == d for r in rows) for d in ("event", "pas")},
        "routes": {p: sum(r["path"] == p for r in rows) for p in routes},
        "map_percent_by_top_k": by_k,
        "rerank_latency": ms_stats([r["rerank_s"] for r in timed]),
        "rerank_latency_by_route": {
            p: ms_stats([r["rerank_s"] for r in timed if r["path"] == p])
            for p in routes
        },
        "queries_without_candidates": len(rows) - len(timed),
        "candidates_scored": sum(r["candidates"] for r in rows),
        "score_failures": sum(r["failures"] for r in rows),
        "windows_outside_clip": sum(
            w["outside_clip"] for r in rows for w in r["windows"]
        ),
        "mean_frames_per_candidate": statistics.fmean(frames) if frames else None,
    }


def stage1_pool(query, mode, run, args):
    """The deepest saved capture and its top hits as reranker windows."""
    path = capture_path(
        args.run_dir, mode, run["ks"][-1], run["multi"], query["query_id"]
    )
    pool = runner.read(path)
    windows = [
        hit_window(hit, i, run["clips"], run["origin"], args.window_pad_s)
        for i, hit in enumerate(pool["data"][: args.rerank_depth])
    ]
    return pool, windows


def warm_up(scorer, run, args, fps, max_frames):
    """Score the first queries once, untimed, so one-off startup cost is excluded."""
    remaining = args.warmup_queries
    for query in run["queries"]:
        if remaining <= 0:
            return
        pool, windows = stage1_pool(query, run["modes"][0], run, args)
        if windows:
            text = query_text(query, pool, args.query_text)
            scorer.score(text, windows, fps, max_frames)
            remaining -= 1


def rerank_query(query, mode, run, scorer, args, fps, max_frames):
    qid = query["query_id"]
    pool_k = run["ks"][-1]
    pool, windows = stage1_pool(query, mode, run, args)
    hits = pool["data"]
    text = query_text(query, pool, args.query_text)
    started = time.perf_counter()
    results = scorer.score(text, windows, fps, max_frames) if windows else []
    rerank_s = time.perf_counter() - started
    scores = [score for score, _ in results]
    orders = {"reranked": order_by_scores(windows, scores)}
    for alpha in args.fusion_alphas:
        orders[variant_name(alpha)] = order_by_scores(
            windows, fused_scores(windows, scores, alpha)
        )
    tail = hits[len(windows) :]
    ranked = {
        variant: [hits[i] for i in order] + tail for variant, order in orders.items()
    }
    ap = {"baseline": {}, "stage1-pool": {}, **{v: {} for v in ranked}}
    for k in run["ks"]:
        original = runner.read(capture_path(args.run_dir, mode, k, run["multi"], qid))
        for variant, data in (
            ("baseline", original["data"]),
            ("stage1-pool", hits),
            *ranked.items(),
        ):
            score = clip_metrics.score_query(query, data, run["sensors"], k=k)
            ap[variant][str(k)] = score[f"ap_at_{k}"]
    frames = [scorer.frames(w, fps, max_frames) for w in windows]
    return {
        "query_id": qid,
        "domain": query["query_domain"],
        "mode": mode,
        "path": pool["path"],
        "query_text": text,
        "stage1_k": pool_k,
        "candidates": len(windows),
        "failures": sum(s is None for s in scores),
        "rerank_s": rerank_s,
        "frames": frames,
        "windows": [
            {
                "stage1_rank": w.index,
                "sensor_id": w.sensor_id,
                "clip_id": run["sensors"][w.sensor_id],
                "start_s": w.start_s,
                "end_s": w.end_s,
                "stage1_score": w.stage1_score,
                "outside_clip": w.outside_clip,
                "rerank_score": score,
                "frames": n,
                "meta": meta,
            }
            for w, (score, meta), n in zip(windows, results, frames)
        ],
        "orders": orders,
        "rankings": {
            variant: clip_metrics.score_query(
                query, data, run["sensors"], k=run["ks"][-1]
            )["ranking"]
            for variant, data in ranked.items()
        },
        "ap": ap,
    }


def comparison_rows(name, summaries, ks):
    rows = []
    for setting, by_mode in summaries.items():
        for mode, summary in by_mode.items():
            for k in ks:
                variants = summary["map_percent_by_top_k"][str(k)]
                pool = variants["stage1-pool"]["overall"]
                for variant, values in variants.items():
                    rows.append(
                        {
                            "output": name,
                            "setting": setting,
                            "mode": mode,
                            "k": k,
                            "variant": variant,
                            **{d: values[d] for d in DOMAINS},
                            "delta_overall_vs_stage1_pool": None
                            if values["overall"] is None or pool is None
                            else values["overall"] - pool,
                            "by_route": values["by_route"],
                            "rerank_latency": summary["rerank_latency"],
                            "mean_frames_per_candidate": summary[
                                "mean_frames_per_candidate"
                            ],
                            "score_failures": summary["score_failures"],
                        }
                    )
    return rows


def package_versions():
    found = {}
    for name in (
        "cosmos-nano-reranker",
        "vllm",
        "transformers",
        "opencv-python-headless",
    ):
        try:
            found[name] = version(name)
        except PackageNotFoundError:
            found[name] = None
    return found


def run(args, scorer: Scorer | None = None):
    loaded = load_run(args)
    out = args.run_dir / "rerank" / args.output_name
    out.mkdir(parents=True, exist_ok=False)
    settings = [(fps, frames) for fps in args.fps for frames in args.max_frames]
    if args.reranker == "identity":
        settings = settings[:1]
    variants = [
        "baseline",
        "stage1-pool",
        "reranked",
        *(variant_name(a) for a in args.fusion_alphas),
    ]
    identity = {
        "run_id": loaded["identity"]["run_id"],
        "run_source_commit": loaded["identity"]["source_commit"],
        "source_commit": runner.source_commit(),
        "script_sha256": sha256(Path(__file__)),
        "reranker": args.reranker,
        "model_id": args.model_id if args.reranker != "identity" else None,
        "lora_path": str(args.lora_path) if args.lora_path else None,
        "model_options": {
            key: getattr(args, key)
            for key in (
                "jpeg_quality",
                "gpu_memory_utilization",
                "tensor_parallel_size",
                "max_model_len",
                "max_lora_rank",
                "enforce_eager",
            )
        }
        if args.reranker != "identity"
        else None,
        "rerank_depth": args.rerank_depth,
        "stage1_k": loaded["ks"][-1],
        "rerank_depth_limited_by_stage1": loaded["ks"][-1] < args.rerank_depth,
        "warmup_queries_per_setting": args.warmup_queries,
        "top_k_values": loaded["ks"],
        "modes": loaded["modes"],
        "settings": [setting_name(f, m) for f, m in settings],
        "fps": args.fps,
        "max_frames": args.max_frames,
        "window_pad_s": args.window_pad_s,
        "query_text": args.query_text,
        "fusion_alphas": args.fusion_alphas,
        "variants": variants,
        "variant_definitions": {
            "baseline": "The run's own capture at each k (the numbers in summary.json)",
            "stage1-pool": "The top k unique clips of the deepest capture's hits, in stage-1 order: the reranker's input",
            "reranked": "Reranker score only over the top rerank_depth hits; deeper hits keep stage-1 order",
            "fused-aX": "alpha * z(stage-1) + (1 - alpha) * z(reranker), z within each query's reranked hits",
        },
        "latency_definition": "Wall time to sample, encode and score one query's candidates grouped by sampled frame rate, after an untimed warm-up; excludes stage-1 retrieval and routing and queries without hits",
        "versions": {"python": sys.version, **package_versions()},
        "input_sha256": {
            str(p): sha256(p)
            for p in (
                args.ingestion_manifest,
                args.dataset_root / "gt/queries_gt.json",
                args.dataset_root / "additional_gt/clips_gt.json",
            )
        },
        "started": datetime.now(UTC).isoformat(),
    }
    runner.save(out / "identity.json", identity)
    if identity["rerank_depth_limited_by_stage1"]:
        print(
            f"warning: the deepest stage-1 capture has k={loaded['ks'][-1]}, so at most "
            f"{loaded['ks'][-1]} hits per query are reranked, not {args.rerank_depth}",
            file=sys.stderr,
        )
    try:
        scorer = scorer or (
            IdentityScorer() if args.reranker == "identity" else CosmosScorer(args)
        )
        summaries = {}
        for fps, max_frames in settings:
            name = setting_name(fps, max_frames)
            summaries[name] = {}
            warm_up(scorer, loaded, args, fps, max_frames)
            for mode in loaded["modes"]:
                rows = []
                for index, query in enumerate(loaded["queries"]):
                    row = rerank_query(
                        query, mode, loaded, scorer, args, fps, max_frames
                    )
                    runner.save(
                        out / name / "captures" / mode / f"{query['query_id']}.json",
                        row,
                    )
                    rows.append(row)
                    print(
                        f"{name} {mode} {index + 1}/{len(loaded['queries'])} "
                        f"{row['query_id']} {row['path']} {row['rerank_s']:.3f}s",
                        flush=True,
                    )
                summaries[name][mode] = summarize(rows, variants, loaded["ks"])
            runner.save(out / name / "summary.json", summaries[name])
            # Rewritten after every setting so a failed sweep keeps its results.
            rows = comparison_rows(args.output_name, summaries, loaded["ks"])
            runner.save(out / "comparison.json", {"identity": identity, "rows": rows})
        runner.save(
            out / "complete.json",
            {
                "settings": identity["settings"],
                "finished": datetime.now(UTC).isoformat(),
            },
        )
    except BaseException as error:
        runner.save(
            out / "failed.json",
            {"type": type(error).__name__, "message": str(error), "complete": False},
        )
        raise
    print_table(rows)
    return out


def print_table(rows):
    def cell(value):
        return "—" if value is None else f"{value:.2f}"

    print(
        f"{'setting':<22} {'mode':<15} {'k':>4} {'variant':<12} event   pas  overall  delta"
    )
    for r in rows:
        print(
            f"{r['setting']:<22} {r['mode']:<15} {r['k']:>4} {r['variant']:<12} "
            f"{cell(r['event']):>5} {cell(r['pas']):>5} {cell(r['overall']):>8} "
            f"{cell(r['delta_overall_vs_stage1_pool']):>6}"
        )


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--dataset-root", type=Path, required=True)
    p.add_argument("--ingestion-manifest", type=Path, required=True)
    p.add_argument(
        "--output-name",
        required=True,
        help="Results go to <run-dir>/rerank/<name>; an existing name is never overwritten",
    )
    p.add_argument("--modes", choices=runner.MODES, nargs="+")
    p.add_argument(
        "--reranker", choices=("cosmos_reason", "identity"), default="cosmos_reason"
    )
    p.add_argument("--model-id", default="nvidia/Cosmos3-Nano")
    p.add_argument(
        "--lora-path",
        type=Path,
        help="Language LoRA adapter; pair it with the merged-vision --model-id",
    )
    p.add_argument("--rerank-depth", type=int, default=20)
    p.add_argument(
        "--fps",
        type=float,
        nargs="+",
        default=[2.0],
        help="Frame sampling rates to sweep; frames per window = duration x fps, clamped",
    )
    p.add_argument(
        "--max-frames",
        type=int,
        nargs="+",
        default=[32],
        help="Frame caps to sweep; with a high --fps this fixes frames per window",
    )
    p.add_argument(
        "--window-pad-s",
        type=float,
        default=0.0,
        help="Context added before and after each hit, clamped to the clip",
    )
    p.add_argument(
        "--query-text",
        choices=("original", "request"),
        default="original",
        help="original: the user query; request: the text the stage-1 request searched",
    )
    p.add_argument("--fusion-alphas", type=float, nargs="*", default=[])
    p.add_argument("--jpeg-quality", type=int, default=95)
    p.add_argument("--gpu-memory-utilization", type=float, default=0.85)
    p.add_argument("--tensor-parallel-size", type=int, default=1)
    p.add_argument("--max-model-len", type=int, default=32768)
    p.add_argument("--enforce-eager", action="store_true")
    p.add_argument(
        "--warmup-queries",
        type=int,
        default=1,
        help="Untimed queries scored before each setting",
    )
    p.add_argument(
        "--max-lora-rank",
        type=int,
        default=32,
        help="Must cover the adapter rank; the bundled fine-tuned adapter is rank 32",
    )
    p.add_argument(
        "--no-report",
        action="store_true",
        help="Do not regenerate report.html in the run directory after reranking",
    )
    args = p.parse_args(argv)
    if Path(args.output_name).name != args.output_name or args.output_name in {
        ".",
        "..",
    }:
        p.error("--output-name must be a single directory name")
    if args.rerank_depth < 1:
        p.error("--rerank-depth must be positive")
    if len(set(args.fps)) != len(args.fps) or any(
        not math.isfinite(f) or f <= 0 for f in args.fps
    ):
        p.error("--fps values must be unique, finite and positive")
    if len(set(args.max_frames)) != len(args.max_frames) or any(
        m < 2 for m in args.max_frames
    ):
        p.error("--max-frames values must be unique and at least 2")
    if not math.isfinite(args.window_pad_s) or args.window_pad_s < 0:
        p.error("--window-pad-s must be finite and non-negative")
    if len(set(args.fusion_alphas)) != len(args.fusion_alphas) or any(
        not 0 <= a <= 1 for a in args.fusion_alphas
    ):
        p.error("--fusion-alphas must be unique values between 0 and 1")
    if args.reranker == "identity" and args.lora_path:
        p.error("--lora-path needs the cosmos_reason reranker")
    if args.warmup_queries < 0:
        p.error("--warmup-queries must not be negative")
    if not 1 <= args.jpeg_quality <= 100:
        p.error("--jpeg-quality must be between 1 and 100")
    return args


def main(argv=None):
    args = parse_args(argv)
    run(args)
    if not args.no_report:
        print(f"Wrote {report.write_report(args.run_dir)}")


if __name__ == "__main__":
    main()
