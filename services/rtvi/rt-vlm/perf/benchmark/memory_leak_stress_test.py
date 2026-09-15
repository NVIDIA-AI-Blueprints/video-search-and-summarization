#!/usr/bin/env python3
######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################
"""
CUDA memory leak stress test for RTVI VLM live streams.

Repeatedly adds and deletes batches of live streams to detect CUDA memory
fragmentation / leaks. After each add-delete cycle, records GPU memory usage.
If memory grows monotonically across cycles, there's a leak.

Usage:
    # Basic: 10 cycles of 20 streams each
    python3 memory_leak_stress_test.py --rtsp-url rtsp://host:port/live/video

    # Aggressive: 50 cycles of 50 streams, 5s soak
    python3 memory_leak_stress_test.py --rtsp-url rtsp://host:port/live/video \
        --cycles 50 --streams-per-cycle 50 --soak-seconds 5

    # Custom backend
    python3 memory_leak_stress_test.py --rtsp-url rtsp://host:port/live/video \
        --backend http://localhost:8010

    # Stop on OOM (default) or keep going
    python3 memory_leak_stress_test.py --rtsp-url rtsp://host:port/live/video \
        --no-stop-on-oom
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# ── GPU memory query (pynvml NVML library, NOT CUDA runtime) ─────────────────
# pynvml uses the NVML management library which is separate from the CUDA
# runtime — it does not create CUDA contexts or allocate GPU memory.
# Init once at module load to avoid repeated nvmlInit calls.

try:
    import pynvml

    pynvml.nvmlInit()
    _NVML_AVAILABLE = True
except Exception:
    _NVML_AVAILABLE = False


_gpu_query_cmd: Optional[str] = None


def get_gpu_memory_info(gpu_indices: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """Query GPU memory via local pynvml or a custom remote command.

    If --gpu-query-cmd is set, runs that command and parses CSV output.
    Otherwise uses local pynvml.
    """
    if _gpu_query_cmd:
        return _get_gpu_memory_via_cmd(_gpu_query_cmd, gpu_indices)
    if not _NVML_AVAILABLE:
        return []
    try:
        count = pynvml.nvmlDeviceGetCount()
        indices = gpu_indices or list(range(count))
        result = []
        for i in indices:
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            result.append(
                {
                    "index": i,
                    "used_mb": round(mem.used / (1024**2), 1),
                    "total_mb": round(mem.total / (1024**2), 1),
                    "used_pct": round(mem.used / mem.total * 100, 2),
                }
            )
        return result
    except Exception as e:
        logger.warning("pynvml GPU memory query failed: %s", e)
        return []


def _get_gpu_memory_via_cmd(
    cmd: str, gpu_indices: Optional[List[int]] = None
) -> List[Dict[str, Any]]:
    """Run a custom command that outputs GPU memory CSV and parse it."""
    import shlex
    import subprocess

    try:
        out = subprocess.check_output(shlex.split(cmd), text=True, timeout=15)
        result = []
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            idx = int(parts[0])
            if gpu_indices and idx not in gpu_indices:
                continue
            used_mb = float(parts[1])
            total_mb = float(parts[2])
            result.append(
                {
                    "index": idx,
                    "used_mb": round(used_mb, 1),
                    "total_mb": round(total_mb, 1),
                    "used_pct": round(used_mb / total_mb * 100, 2) if total_mb > 0 else 0.0,
                }
            )
        return result
    except Exception as e:
        logger.warning("GPU query command failed: %s", e)
        return []


def get_pytorch_cuda_memory() -> Dict[str, float]:
    """Check server /metrics endpoint for CUDA memory stats if exposed."""
    global _backend_url  # noqa: F824 — explicit dependency on module-level URL
    metrics_url = f"{_backend_url}/metrics"
    try:
        resp = requests.get(metrics_url, timeout=5)
        if resp.ok:
            for line in resp.text.splitlines():
                if "cuda_memory" in line.lower() and not line.startswith("#"):
                    logger.debug("  metric: %s", line.strip())
    except Exception:
        logger.debug("Failed to fetch metrics from %s", metrics_url, exc_info=True)
    return {}


# ── HTTP session ─────────────────────────────────────────────────────────────

_backend_url = "http://localhost:8000"


def create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry, pool_connections=100, pool_maxsize=100)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


# ── Stream management ────────────────────────────────────────────────────────


def get_model_name(session: requests.Session) -> str:
    """Get the first available model name from the server."""
    try:
        resp = session.get(f"{_backend_url}/v1/models", timeout=(5, 10))
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if data and "id" in data[0]:
            return data[0]["id"]
        logger.warning("No models found in response")
        return ""
    except Exception as e:
        logger.warning("Failed to get model name: %s", e)
        return ""


def add_stream(session: requests.Session, rtsp_url: str, idx: int) -> Optional[str]:
    """Add a single live stream. Returns stream ID or None."""
    try:
        payload = {"streams": [{"liveStreamUrl": rtsp_url, "description": f"leak_test_{idx}"}]}
        resp = session.post(f"{_backend_url}/v1/streams/add", json=payload, timeout=(10, 60))
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if results:
            return results[0].get("id", "")
    except Exception as e:
        logger.warning("Failed to add stream %d: %s", idx, e)
    return None


def start_captions(
    session: requests.Session,
    stream_id: str,
    model_name: str,
    chunk_duration: int = 10,
    max_tokens: int = 100,
) -> bool:
    """Start generate_captions for a stream (SSE, non-blocking).

    This triggers the full VLM pipeline: decode → CUDA tensors → VLM inference.
    Without this call, no CUDA memory is allocated for the stream.
    """
    payload = {
        "id": [stream_id],
        "model": model_name,
        "prompt": "Describe what is happening in the video",
        "response_format": {"type": "text"},
        "stream": True,
        "stream_options": {"include_usage": True},
        "chunk_duration": chunk_duration,
        "temperature": 0.4,
        "max_tokens": max_tokens,
    }
    try:
        # SSE stream — just POST and close immediately, server keeps processing
        resp = session.post(
            f"{_backend_url}/v1/generate_captions",
            json=payload,
            timeout=(10, 30),
            stream=True,
        )
        if resp.ok:
            resp.close()  # close SSE connection, server continues processing
            return True
        logger.warning("generate_captions failed for %s: %s", stream_id, resp.status_code)
    except Exception as e:
        logger.warning("generate_captions failed for %s: %s", stream_id, e)
    return False


def add_streams(
    session: requests.Session,
    rtsp_url: str,
    count: int,
    model_name: str,
    chunk_duration: int = 10,
    max_tokens: int = 100,
    inter_delay: float = 0.5,
) -> List[str]:
    """Add N live streams and start VLM captions on each. Returns stream IDs."""
    stream_ids = []
    for i in range(count):
        sid = add_stream(session, rtsp_url, i)
        if not sid:
            logger.warning("Stream add failed at %d, stopping batch", i)
            break
        stream_ids.append(sid)
        # Start VLM processing — this is what allocates CUDA memory
        ok = start_captions(session, sid, model_name, chunk_duration, max_tokens)
        if ok:
            logger.debug("  Stream %d: %s — captions started", i, sid[:8])
        else:
            logger.warning("  Stream %d: %s — captions failed", i, sid[:8])
        if inter_delay > 0 and i < count - 1:
            time.sleep(inter_delay)
    return stream_ids


def delete_streams(
    session: requests.Session, stream_ids: List[str], inter_delay: float = 1.0
) -> int:
    """Delete streams sequentially. Returns count of successful deletions."""
    deleted = 0
    for sid in stream_ids:
        try:
            resp = session.delete(
                f"{_backend_url}/v1/streams/delete/{sid}",
                timeout=(10, 180),
            )
            if resp.ok:
                deleted += 1
        except Exception as e:
            logger.warning("Failed to delete stream %s: %s", sid, e)
        if inter_delay > 0:
            time.sleep(inter_delay)
    return deleted


def get_active_stream_count(session: requests.Session) -> int:
    """Query server for current active stream count."""
    try:
        resp = session.get(f"{_backend_url}/v1/streams/get-stream-info", timeout=(5, 10))
        return len(resp.json()) if resp.ok else -1
    except Exception:
        return -1


# ── Stress test ──────────────────────────────────────────────────────────────


def run_stress_test(
    rtsp_url: str,
    cycles: int = 10,
    streams_per_cycle: int = 20,
    soak_seconds: float = 10.0,
    cooldown_seconds: float = 15.0,
    chunk_duration: int = 10,
    max_tokens: int = 100,
    inter_add_delay: float = 0.5,
    inter_delete_delay: float = 1.0,
    gpu_indices: Optional[List[int]] = None,
    stop_on_oom: bool = True,
    output_dir: str = "memory_leak_report",
) -> Dict[str, Any]:
    """Run the memory leak stress test.

    For each cycle:
    1. Record baseline GPU memory
    2. Add streams + start generate_captions (triggers VLM pipeline)
    3. Soak for soak_seconds (frames decoded → CUDA tensors → VLM inference)
    4. Record peak GPU memory
    5. Delete all streams
    6. Wait cooldown_seconds (let CUDA free memory)
    7. Record post-delete GPU memory

    If post-delete memory grows across cycles, there's a leak.
    """
    session = create_session()

    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, "memory_leak_data.csv")
    summary_path = os.path.join(output_dir, "memory_leak_summary.json")

    # Verify server is reachable
    try:
        resp = session.get(f"{_backend_url}/v1/health/ready", timeout=5)
        if not resp.ok:
            logger.error("Server not ready: %s", resp.status_code)
            sys.exit(1)
    except Exception:
        logger.exception("Cannot reach server at %s", _backend_url)
        sys.exit(1)

    # Get model name for generate_captions calls
    model_name = get_model_name(session)
    if model_name:
        logger.info("Using model: %s", model_name)
    else:
        logger.error("No model available — cannot run captions, exiting")
        sys.exit(1)

    # Clean up any pre-existing streams
    active = get_active_stream_count(session)
    if active > 0:
        logger.warning("Found %d pre-existing streams, cleaning up...", active)
        try:
            resp = session.get(f"{_backend_url}/v1/streams/get-stream-info", timeout=10)
            for s in resp.json():
                delete_streams(session, [s["id"]], inter_delay=0.5)
        except Exception:
            logger.warning("Pre-existing stream cleanup failed", exc_info=True)
        time.sleep(cooldown_seconds)

    # Initial baseline
    baseline_mem = get_gpu_memory_info(gpu_indices)
    if baseline_mem:
        logger.info(
            "Baseline GPU memory: %s",
            ", ".join(
                f"GPU{g['index']}: {g['used_mb']:.0f}MB ({g['used_pct']:.1f}%%)"
                for g in baseline_mem
            ),
        )

    results = []
    oom_detected = False

    with open(csv_path, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        header = [
            "cycle",
            "timestamp",
            "phase",
            "streams_added",
            "streams_active",
            "add_time_sec",
            "delete_time_sec",
        ]
        if baseline_mem:
            for g in baseline_mem:
                header.extend([f"gpu{g['index']}_used_mb", f"gpu{g['index']}_used_pct"])
        writer.writerow(header)

        for cycle in range(1, cycles + 1):
            logger.info("=" * 60)
            logger.info("CYCLE %d / %d  (%d streams)", cycle, cycles, streams_per_cycle)
            logger.info("=" * 60)

            # Phase 1: pre-add baseline
            pre_mem = get_gpu_memory_info(gpu_indices)
            _log_mem("  Pre-add", pre_mem)

            # Phase 2: add streams + start VLM captions
            t0 = time.time()
            stream_ids = add_streams(
                session,
                rtsp_url,
                streams_per_cycle,
                model_name,
                chunk_duration,
                max_tokens,
                inter_add_delay,
            )
            add_time = time.time() - t0
            n_added = len(stream_ids)
            active_count = get_active_stream_count(session)
            logger.info(
                "  Added %d/%d streams in %.1fs (active: %d)",
                n_added,
                streams_per_cycle,
                add_time,
                active_count,
            )

            if n_added == 0:
                logger.error("  Failed to add any streams — possible OOM")
                oom_detected = True
                if stop_on_oom:
                    logger.error("  Stopping (--stop-on-oom). Use --no-stop-on-oom to continue.")
                    break

            # Phase 3: soak
            logger.info("  Soaking for %.0fs...", soak_seconds)
            time.sleep(soak_seconds)
            peak_mem = get_gpu_memory_info(gpu_indices)
            _log_mem("  Peak (soak)", peak_mem)

            # Phase 4: delete all streams
            t0 = time.time()
            deleted = delete_streams(session, stream_ids, inter_delete_delay)
            delete_time = time.time() - t0
            logger.info("  Deleted %d/%d streams in %.1fs", deleted, n_added, delete_time)

            # Phase 5: cooldown
            logger.info("  Cooldown %.0fs...", cooldown_seconds)
            time.sleep(cooldown_seconds)
            post_mem = get_gpu_memory_info(gpu_indices)
            _log_mem("  Post-delete", post_mem)
            remaining = get_active_stream_count(session)
            logger.info("  Active streams remaining: %d", remaining)

            # Record cycle data
            cycle_data = {
                "cycle": cycle,
                "streams_added": n_added,
                "streams_active_peak": active_count,
                "streams_remaining": remaining,
                "add_time_sec": round(add_time, 1),
                "delete_time_sec": round(delete_time, 1),
                "pre_add_mem": pre_mem,
                "peak_mem": peak_mem,
                "post_delete_mem": post_mem,
            }
            results.append(cycle_data)

            # Write CSV rows for each phase
            for phase, mem in [("pre_add", pre_mem), ("peak", peak_mem), ("post_delete", post_mem)]:
                row = [
                    cycle,
                    datetime.now().isoformat(),
                    phase,
                    n_added,
                    active_count if phase == "peak" else remaining,
                    round(add_time, 1) if phase == "peak" else "",
                    round(delete_time, 1) if phase == "post_delete" else "",
                ]
                for g in mem:
                    row.extend([g["used_mb"], g["used_pct"]])
                writer.writerow(row)
            csvfile.flush()

            # Check for leak: compare post-delete memory across all GPUs
            if len(results) >= 3:
                recent_post = [r["post_delete_mem"] for r in results[-3:]]
                if recent_post[0] and all(recent_post):
                    for gpu_idx in range(len(recent_post[0])):
                        gpu_trend = [m[gpu_idx]["used_mb"] for m in recent_post]
                        gpu_id = recent_post[0][gpu_idx]["index"]
                        if gpu_trend[2] > gpu_trend[0] + 500:  # 500MB growth
                            logger.warning(
                                "  LEAK DETECTED: GPU%d post-delete memory grew "
                                "%.0fMB over 3 cycles (%.0f -> %.0f -> %.0fMB)",
                                gpu_id,
                                gpu_trend[2] - gpu_trend[0],
                                *gpu_trend,
                            )

    # Generate summary
    summary = _build_summary(results, baseline_mem, oom_detected, cycles, streams_per_cycle)
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    logger.info("")
    logger.info("=" * 60)
    logger.info("STRESS TEST COMPLETE")
    logger.info("=" * 60)
    logger.info("Cycles completed: %d / %d", len(results), cycles)
    logger.info("OOM detected: %s", oom_detected)
    if baseline_mem and results:
        first_post = results[0]["post_delete_mem"]
        last_post = results[-1]["post_delete_mem"]
        if first_post and last_post:
            for g_first, g_last in zip(first_post, last_post, strict=True):
                delta = g_last["used_mb"] - g_first["used_mb"]
                logger.info(
                    "GPU%d memory drift: %+.0fMB (%.1f%% -> %.1f%%)",
                    g_first["index"],
                    delta,
                    g_first["used_pct"],
                    g_last["used_pct"],
                )
    logger.info("CSV:     %s", csv_path)
    logger.info("Summary: %s", summary_path)

    return summary


def _log_mem(label: str, mem_info: List[Dict]) -> None:
    if mem_info:
        parts = [f"GPU{g['index']}: {g['used_mb']:.0f}MB ({g['used_pct']:.1f}%%)" for g in mem_info]
        logger.info("%s: %s", label, ", ".join(parts))


def _build_summary(
    results: List[Dict],
    baseline_mem: List[Dict],
    oom_detected: bool,
    total_cycles: int,
    streams_per_cycle: int,
) -> Dict[str, Any]:
    """Build a JSON summary of the stress test."""
    summary: Dict[str, Any] = {
        "test": "cuda_memory_leak_stress_test",
        "timestamp": datetime.now().isoformat(),
        "config": {
            "total_cycles": total_cycles,
            "streams_per_cycle": streams_per_cycle,
            "cycles_completed": len(results),
        },
        "oom_detected": oom_detected,
        "baseline_gpu_memory": baseline_mem,
    }

    if results:
        # Memory trend: post-delete GPU memory across cycles
        post_delete_series = []
        for r in results:
            if r["post_delete_mem"]:
                post_delete_series.append(
                    {f"gpu{g['index']}_mb": g["used_mb"] for g in r["post_delete_mem"]}
                )

        summary["post_delete_memory_trend"] = post_delete_series

        # Leak detection
        if len(post_delete_series) >= 2:
            first = post_delete_series[0]
            last = post_delete_series[-1]
            drifts = {}
            for key in first:
                drifts[key] = round(last[key] - first[key], 1)
            summary["memory_drift_mb"] = drifts
            summary["leak_likely"] = any(v > 500 for v in drifts.values())
        else:
            summary["leak_likely"] = False

        # Timing stats
        add_times = [r["add_time_sec"] for r in results]
        delete_times = [r["delete_time_sec"] for r in results]
        summary["timing"] = {
            "avg_add_time_sec": round(sum(add_times) / len(add_times), 1),
            "avg_delete_time_sec": round(sum(delete_times) / len(delete_times), 1),
        }

    return summary


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    global _backend_url

    parser = argparse.ArgumentParser(
        description="CUDA memory leak stress test — repeated stream add/delete cycles",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--rtsp-url",
        required=True,
        help="RTSP stream URL (e.g. rtsp://host:port/live/video)",
    )
    parser.add_argument(
        "--backend",
        default=os.environ.get("RTVI_BACKEND", "http://localhost:8000"),
        help="RTVI backend URL (default: $RTVI_BACKEND or http://localhost:8000)",
    )
    parser.add_argument(
        "--cycles", type=int, default=10, help="Number of add/delete cycles (default: 10)"
    )
    parser.add_argument(
        "--streams-per-cycle",
        type=int,
        default=20,
        help="Streams to add per cycle (default: 20)",
    )
    parser.add_argument(
        "--soak-seconds",
        type=float,
        default=10.0,
        help="Seconds to let streams process before deleting (default: 10)",
    )
    parser.add_argument(
        "--cooldown-seconds",
        type=float,
        default=15.0,
        help="Seconds to wait after deletion for CUDA cleanup (default: 15)",
    )
    parser.add_argument(
        "--chunk-duration",
        type=int,
        default=10,
        help="VLM chunk duration in seconds (default: 10)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=100,
        help="VLM max output tokens per chunk (default: 100)",
    )
    parser.add_argument(
        "--inter-add-delay",
        type=float,
        default=0.5,
        help="Delay between stream adds (default: 0.5s)",
    )
    parser.add_argument(
        "--inter-delete-delay",
        type=float,
        default=1.0,
        help="Delay between stream deletes (default: 1.0s)",
    )
    parser.add_argument(
        "--gpu-indices",
        type=int,
        nargs="+",
        default=None,
        help="GPU indices to monitor (default: all)",
    )
    parser.add_argument(
        "--gpu-query-cmd",
        type=str,
        default=None,
        help=(
            "Custom command to query remote GPU memory. Expected CSV columns: "
            "index,memory_used_mib,memory_total_mib. Prefer a PyNVML-based command."
        ),
    )
    parser.add_argument(
        "--stop-on-oom",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Stop on first OOM (default: True, use --no-stop-on-oom to continue)",
    )
    parser.add_argument(
        "--output-dir",
        default="memory_leak_report",
        help="Output directory for CSV and summary (default: memory_leak_report)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    _backend_url = args.backend.rstrip("/")
    if not _backend_url.startswith("http"):
        _backend_url = f"http://{_backend_url}"

    global _gpu_query_cmd
    _gpu_query_cmd = args.gpu_query_cmd

    logger.info("CUDA Memory Leak Stress Test")
    logger.info("  Backend:          %s", _backend_url)
    logger.info("  RTSP URL:         %s", args.rtsp_url)
    logger.info("  Cycles:           %d", args.cycles)
    logger.info("  Streams/cycle:    %d", args.streams_per_cycle)
    logger.info("  Soak:             %.0fs", args.soak_seconds)
    logger.info("  Cooldown:         %.0fs", args.cooldown_seconds)
    logger.info("  Chunk duration:   %ds", args.chunk_duration)
    logger.info("  Max tokens:       %d", args.max_tokens)
    logger.info("  Total streams:    %d", args.cycles * args.streams_per_cycle)
    logger.info("")

    summary = run_stress_test(
        rtsp_url=args.rtsp_url,
        cycles=args.cycles,
        streams_per_cycle=args.streams_per_cycle,
        soak_seconds=args.soak_seconds,
        cooldown_seconds=args.cooldown_seconds,
        chunk_duration=args.chunk_duration,
        max_tokens=args.max_tokens,
        inter_add_delay=args.inter_add_delay,
        inter_delete_delay=args.inter_delete_delay,
        gpu_indices=args.gpu_indices,
        stop_on_oom=args.stop_on_oom,
        output_dir=args.output_dir,
    )

    sys.exit(1 if summary.get("leak_likely") or summary.get("oom_detected") else 0)


if __name__ == "__main__":
    main()
