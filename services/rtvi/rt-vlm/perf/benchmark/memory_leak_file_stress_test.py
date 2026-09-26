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
CUDA memory leak stress test for RTVI VLM file processing.

Repeatedly uploads a video file, runs generate_captions (full VLM
pipeline: decode → CUDA tensors → inference), deletes the file, and checks
if GPU memory is reclaimed. If memory grows across cycles, there's a leak.

Usage:
    # Basic: 20 cycles with a 10s video
    python3 memory_leak_file_stress_test.py --video /path/to/video.mp4

    # Aggressive: 50 cycles, 4 concurrent files per cycle
    python3 memory_leak_file_stress_test.py --video /path/to/video.mp4 \
        --cycles 50 --files-per-cycle 4

    # With custom backend and long video
    python3 memory_leak_file_stress_test.py --video /path/to/60min.mp4 \
        --backend http://localhost:8010 --request-timeout 900
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# ── GPU memory query (pynvml NVML library, NOT CUDA runtime) ─────────────────

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
    """Run a custom command that outputs GPU memory CSV and parse it.

    Expected output format (one line per GPU):
        index, memory_used_mib, memory_total_mib
    """
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


# ── HTTP session ─────────────────────────────────────────────────────────────

_backend_url = "http://localhost:8000"


def create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry, pool_connections=100, pool_maxsize=100)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


# ── File management ──────────────────────────────────────────────────────────


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


def upload_file(session: requests.Session, video_path: str) -> Optional[str]:
    """Upload a video file via path-based mode. Returns file ID or None.

    NOTE: Uses path-based upload — the server must have access to the same
    filesystem (e.g. Docker shared volume). The video_path is sent as an
    absolute path string, not as file content bytes.
    """
    try:
        files = {
            "filename": (None, os.path.abspath(video_path)),
            "purpose": (None, "vision"),
            "media_type": (None, "video"),
        }
        resp = session.post(f"{_backend_url}/v1/files", files=files, timeout=(10, 60))
        resp.raise_for_status()
        return resp.json().get("id")
    except Exception as e:
        logger.warning("Failed to upload file: %s", e)
        return None


def process_file(
    session: requests.Session,
    file_id: str,
    model_name: str,
    chunk_duration: int = 10,
    max_tokens: int = 100,
    request_timeout: int = 300,
) -> Dict[str, Any]:
    """Run generate_captions on a file (synchronous, waits for completion).

    This triggers the full VLM pipeline: file decode → CUDA tensors → VLM inference.
    Returns {success, processing_time_sec, error}.
    """
    payload = {
        "id": [file_id],
        "model": model_name,
        "prompt": "Describe what is happening in the video",
        "response_format": {"type": "text"},
        "chunk_duration": chunk_duration,
        "temperature": 0.4,
        "max_tokens": max_tokens,
    }
    try:
        t0 = time.time()
        resp = session.post(
            f"{_backend_url}/v1/generate_captions",
            json=payload,
            timeout=(10, request_timeout),
        )
        elapsed = time.time() - t0
        if resp.ok:
            return {"success": True, "processing_time_sec": round(elapsed, 2)}
        return {
            "success": False,
            "processing_time_sec": round(elapsed, 2),
            "error": resp.text[:200],
        }
    except Exception as e:
        return {"success": False, "processing_time_sec": 0, "error": str(e)[:200]}


def delete_file(session: requests.Session, file_id: str) -> bool:
    """Delete a file from the server."""
    try:
        resp = session.delete(f"{_backend_url}/v1/files/{file_id}", timeout=(10, 60))
        return resp.ok
    except Exception as e:
        logger.warning("Failed to delete file %s: %s", file_id, e)
        return False


def get_active_file_count(session: requests.Session) -> int:
    """Query server for current file count."""
    try:
        resp = session.get(
            f"{_backend_url}/v1/files", params={"purpose": "vision"}, timeout=(5, 10)
        )
        return len(resp.json().get("data", [])) if resp.ok else -1
    except Exception:
        return -1


# ── Stress test ──────────────────────────────────────────────────────────────


def process_single_file(
    session: requests.Session,
    video_path: str,
    model_name: str,
    file_idx: int,
    chunk_duration: int,
    max_tokens: int,
    request_timeout: int,
) -> Dict[str, Any]:
    """Upload, process (blocking), return result. File NOT deleted here."""
    file_id = upload_file(session, video_path)
    if not file_id:
        return {"success": False, "file_id": None, "error": "upload failed"}

    result = process_file(session, file_id, model_name, chunk_duration, max_tokens, request_timeout)
    result["file_id"] = file_id
    result["file_idx"] = file_idx
    return result


def start_file_captions(
    session: requests.Session,
    file_id: str,
    model_name: str,
    chunk_duration: int = 10,
    max_tokens: int = 100,
) -> bool:
    """Fire generate_captions for a file (non-blocking SSE).

    Returns True if the POST succeeded. The server processes async.
    """
    payload = {
        "id": [file_id],
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
        resp = session.post(
            f"{_backend_url}/v1/generate_captions",
            json=payload,
            timeout=(10, 30),
            stream=True,
        )
        if resp.ok:
            resp.close()
            return True
        logger.warning("generate_captions failed for file %s: %s", file_id[:8], resp.status_code)
    except Exception as e:
        logger.warning("generate_captions failed for file %s: %s", file_id[:8], e)
    return False


def sample_gpu_during_soak(
    duration: float,
    interval: float,
    gpu_indices: Optional[List[int]],
    csv_writer,
    cycle: int,
) -> List[Dict[str, Any]]:
    """Sample GPU memory periodically during soak and write to CSV.

    Returns list of all samples taken.
    """
    samples = []
    elapsed = 0.0
    sample_num = 0
    while elapsed < duration:
        mem = get_gpu_memory_info(gpu_indices)
        if mem:
            sample_num += 1
            _log_mem(f"    Soak sample {sample_num} ({elapsed:.0f}s)", mem)
            samples.append({"elapsed": round(elapsed, 1), "mem": mem})
            if csv_writer:
                row = [cycle, datetime.now().isoformat(), f"soak_{elapsed:.0f}s", "", "", "", ""]
                for g in mem:
                    row.extend([g["used_mb"], g["used_pct"]])
                csv_writer.writerow(row)
        sleep_time = min(interval, duration - elapsed)
        if sleep_time <= 0:
            break
        time.sleep(sleep_time)
        elapsed += sleep_time
    return samples


def process_files_concurrent(
    session: requests.Session,
    video_path: str,
    count: int,
    model_name: str,
    chunk_duration: int,
    max_tokens: int,
    soak_seconds: float,
    gpu_sample_interval: float = 0,
    gpu_indices: Optional[List[int]] = None,
    csv_writer=None,
    cycle: int = 0,
) -> List[Dict[str, Any]]:
    """Upload N files, fire captions on all concurrently, soak, return results.

    1. Upload all files sequentially (fast, no GPU work)
    2. Fire generate_captions on all (non-blocking SSE)
    3. Soak for soak_seconds while all files process on GPU concurrently
       — with periodic GPU memory sampling if gpu_sample_interval > 0
    4. Return file IDs and status
    """
    file_ids = []
    for i in range(count):
        fid = upload_file(session, video_path)
        if fid:
            file_ids.append(fid)
            logger.debug("  Uploaded file %d: %s", i, fid[:8])
        else:
            logger.warning("  Upload failed for file %d", i)

    if not file_ids:
        return [{"success": False, "file_id": None, "error": "all uploads failed"}]

    # Fire all captions concurrently (non-blocking)
    captions_started = 0
    for fid in file_ids:
        if start_file_captions(session, fid, model_name, chunk_duration, max_tokens):
            captions_started += 1
    logger.info("  Started captions on %d/%d files concurrently", captions_started, len(file_ids))

    # Soak — all files processing on GPU at the same time
    if gpu_sample_interval > 0:
        logger.info(
            "  Soaking %.0fs with GPU sampling every %.0fs...",
            soak_seconds,
            gpu_sample_interval,
        )
        sample_gpu_during_soak(soak_seconds, gpu_sample_interval, gpu_indices, csv_writer, cycle)
    else:
        logger.info("  Soaking %.0fs (concurrent GPU processing)...", soak_seconds)
        time.sleep(soak_seconds)

    return [{"success": True, "file_id": fid, "file_idx": i} for i, fid in enumerate(file_ids)]


def run_stress_test(
    video_path: str,
    cycles: int = 20,
    files_per_cycle: int = 1,
    concurrent: bool = False,
    soak_seconds: float = 30.0,
    cooldown_seconds: float = 10.0,
    chunk_duration: int = 10,
    max_tokens: int = 100,
    request_timeout: int = 300,
    gpu_sample_interval: float = 0,
    gpu_indices: Optional[List[int]] = None,
    stop_on_oom: bool = True,
    output_dir: str = "memory_leak_file_report",
) -> Dict[str, Any]:
    """Run the file-based memory leak stress test.

    For each cycle:
    1. Record baseline GPU memory
    2. Upload + process files_per_cycle video files (full VLM pipeline)
       - Sequential mode (default): upload+process one at a time
       - Concurrent mode (--concurrent): upload all, fire captions on all,
         soak while all process on GPU simultaneously
    3. Record peak GPU memory
    4. Delete all files
    5. Cooldown (let CUDA free memory)
    6. Record post-delete GPU memory
    """
    session = create_session()

    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, "memory_leak_file_data.csv")
    summary_path = os.path.join(output_dir, "memory_leak_file_summary.json")

    # Verify server
    try:
        resp = session.get(f"{_backend_url}/v1/health/ready", timeout=5)
        if not resp.ok:
            logger.error("Server not ready: %s", resp.status_code)
            sys.exit(1)
    except Exception:
        logger.exception("Cannot reach server at %s", _backend_url)
        sys.exit(1)

    # Verify video file exists
    if not os.path.isfile(video_path):
        logger.error("Video file not found: %s", video_path)
        sys.exit(1)

    model_name = get_model_name(session)
    if not model_name:
        logger.error("No model available — exiting")
        sys.exit(1)
    logger.info("Using model: %s", model_name)

    # Clean up pre-existing files
    existing = get_active_file_count(session)
    if existing > 0:
        logger.warning("Found %d pre-existing files", existing)

    # Baseline
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
            "files_processed",
            "files_succeeded",
            "process_time_sec",
            "delete_time_sec",
        ]
        if baseline_mem:
            for g in baseline_mem:
                header.extend([f"gpu{g['index']}_used_mb", f"gpu{g['index']}_used_pct"])
        writer.writerow(header)

        for cycle in range(1, cycles + 1):
            logger.info("=" * 60)
            logger.info("CYCLE %d / %d  (%d files)", cycle, cycles, files_per_cycle)
            logger.info("=" * 60)

            # Phase 1: pre-process baseline
            pre_mem = get_gpu_memory_info(gpu_indices)
            _log_mem("  Pre-process", pre_mem)

            # Phase 2: upload + process files
            t0 = time.time()
            file_results = []
            if concurrent:
                # Concurrent: upload all, fire captions on all, soak
                file_results = process_files_concurrent(
                    session,
                    video_path,
                    files_per_cycle,
                    model_name,
                    chunk_duration,
                    max_tokens,
                    soak_seconds,
                    gpu_sample_interval=gpu_sample_interval,
                    gpu_indices=gpu_indices,
                    csv_writer=writer,
                    cycle=cycle,
                )
            elif files_per_cycle == 1:
                r = process_single_file(
                    session, video_path, model_name, 0, chunk_duration, max_tokens, request_timeout
                )
                file_results.append(r)
            else:
                # Sequential with ThreadPoolExecutor (each file blocks independently)
                with ThreadPoolExecutor(max_workers=files_per_cycle) as executor:
                    futures = {
                        executor.submit(
                            process_single_file,
                            session,
                            video_path,
                            model_name,
                            i,
                            chunk_duration,
                            max_tokens,
                            request_timeout,
                        ): i
                        for i in range(files_per_cycle)
                    }
                    for future in as_completed(futures):
                        file_results.append(future.result())

            process_time = time.time() - t0
            succeeded = sum(1 for r in file_results if r.get("success"))
            file_ids = [r["file_id"] for r in file_results if r.get("file_id")]

            logger.info(
                "  Processed %d/%d files in %.1fs",
                succeeded,
                files_per_cycle,
                process_time,
            )
            for r in file_results:
                status = "OK" if r.get("success") else "FAIL"
                fid = (r.get("file_id") or "")[:8]
                pt = r.get("processing_time_sec", 0)
                err = r.get("error", "")
                logger.info("    file %s: %s (%.1fs) %s", fid, status, pt, err[:80] if err else "")

            if succeeded == 0:
                logger.error("  All files failed — possible OOM")
                oom_detected = True
                if stop_on_oom:
                    break

            # Record peak memory after processing
            peak_mem = get_gpu_memory_info(gpu_indices)
            _log_mem("  Peak (post-process)", peak_mem)

            # Phase 3: delete all files
            t0 = time.time()
            deleted = 0
            for fid in file_ids:
                if delete_file(session, fid):
                    deleted += 1
            delete_time = time.time() - t0
            logger.info("  Deleted %d/%d files in %.1fs", deleted, len(file_ids), delete_time)

            # Phase 4: cooldown
            logger.info("  Cooldown %.0fs...", cooldown_seconds)
            time.sleep(cooldown_seconds)
            post_mem = get_gpu_memory_info(gpu_indices)
            _log_mem("  Post-delete", post_mem)

            cycle_data = {
                "cycle": cycle,
                "files_processed": files_per_cycle,
                "files_succeeded": succeeded,
                "process_time_sec": round(process_time, 1),
                "delete_time_sec": round(delete_time, 1),
                "pre_process_mem": pre_mem,
                "peak_mem": peak_mem,
                "post_delete_mem": post_mem,
            }
            results.append(cycle_data)

            # CSV rows
            for phase, mem in [
                ("pre_process", pre_mem),
                ("peak", peak_mem),
                ("post_delete", post_mem),
            ]:
                row = [
                    cycle,
                    datetime.now().isoformat(),
                    phase,
                    files_per_cycle,
                    succeeded if phase == "peak" else "",
                    round(process_time, 1) if phase == "peak" else "",
                    round(delete_time, 1) if phase == "post_delete" else "",
                ]
                for g in mem:
                    row.extend([g["used_mb"], g["used_pct"]])
                writer.writerow(row)
            csvfile.flush()

            # Leak detection: check all GPUs
            if len(results) >= 3:
                recent = [r["post_delete_mem"] for r in results[-3:]]
                if recent[0] and all(recent):
                    for gpu_idx in range(len(recent[0])):
                        trend = [m[gpu_idx]["used_mb"] for m in recent]
                        gpu_id = recent[0][gpu_idx]["index"]
                        if trend[2] > trend[0] + 500:
                            logger.warning(
                                "  LEAK DETECTED: GPU%d post-delete memory grew "
                                "%.0fMB over 3 cycles (%.0f -> %.0f -> %.0fMB)",
                                gpu_id,
                                trend[2] - trend[0],
                                *trend,
                            )

    # Summary
    summary = _build_summary(
        results, baseline_mem, oom_detected, cycles, files_per_cycle, video_path
    )
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    logger.info("")
    logger.info("=" * 60)
    logger.info("FILE STRESS TEST COMPLETE")
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
    files_per_cycle: int,
    video_path: str,
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "test": "cuda_memory_leak_file_stress_test",
        "timestamp": datetime.now().isoformat(),
        "config": {
            "total_cycles": total_cycles,
            "files_per_cycle": files_per_cycle,
            "video_path": video_path,
            "cycles_completed": len(results),
        },
        "oom_detected": oom_detected,
        "baseline_gpu_memory": baseline_mem,
    }

    if results:
        post_delete_series = []
        for r in results:
            if r["post_delete_mem"]:
                post_delete_series.append(
                    {f"gpu{g['index']}_mb": g["used_mb"] for g in r["post_delete_mem"]}
                )
        summary["post_delete_memory_trend"] = post_delete_series

        if len(post_delete_series) >= 2:
            first = post_delete_series[0]
            last = post_delete_series[-1]
            drifts = {k: round(last[k] - first[k], 1) for k in first}
            summary["memory_drift_mb"] = drifts
            summary["leak_likely"] = any(v > 500 for v in drifts.values())
        else:
            summary["leak_likely"] = False

        process_times = [r["process_time_sec"] for r in results]
        summary["timing"] = {
            "avg_process_time_sec": round(sum(process_times) / len(process_times), 1),
            "total_files_processed": sum(r["files_succeeded"] for r in results),
        }

    return summary


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    global _backend_url

    parser = argparse.ArgumentParser(
        description="CUDA memory leak stress test — repeated file upload/process/delete cycles",
    )
    parser.add_argument("--video", required=True, help="Path to video file (inside container)")
    parser.add_argument(
        "--backend",
        default=os.environ.get("RTVI_BACKEND", "http://localhost:8000"),
        help="RTVI backend URL (default: $RTVI_BACKEND or http://localhost:8000)",
    )
    parser.add_argument("--cycles", type=int, default=20, help="Number of cycles (default: 20)")
    parser.add_argument(
        "--files-per-cycle", type=int, default=1, help="Files to process per cycle (default: 1)"
    )
    parser.add_argument(
        "--concurrent",
        action="store_true",
        help="Process all files concurrently on GPU (upload all, fire captions on all, soak)",
    )
    parser.add_argument(
        "--soak-seconds",
        type=float,
        default=30.0,
        help="Soak time for concurrent mode — let all files process on GPU (default: 30)",
    )
    parser.add_argument(
        "--cooldown-seconds", type=float, default=10.0, help="Cooldown after deletion (default: 10)"
    )
    parser.add_argument(
        "--chunk-duration", type=int, default=10, help="VLM chunk duration seconds (default: 10)"
    )
    parser.add_argument("--max-tokens", type=int, default=100, help="VLM max tokens (default: 100)")
    parser.add_argument(
        "--request-timeout", type=int, default=300, help="HTTP timeout for VLM call (default: 300s)"
    )
    parser.add_argument(
        "--gpu-sample-interval",
        type=float,
        default=0,
        help="Sample GPU memory every N seconds during soak (0=disabled, try 5)",
    )
    parser.add_argument(
        "--gpu-indices", type=int, nargs="+", default=None, help="GPU indices to monitor"
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
        help="Stop on first OOM (default: True)",
    )
    parser.add_argument("--output-dir", default="memory_leak_file_report", help="Output directory")
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

    logger.info("CUDA Memory Leak File Stress Test")
    logger.info("  Backend:          %s", _backend_url)
    logger.info("  Video:            %s", args.video)
    logger.info("  Cycles:           %d", args.cycles)
    logger.info("  Files/cycle:      %d", args.files_per_cycle)
    logger.info("  Concurrent:       %s", args.concurrent)
    logger.info("  Soak (concurrent):%.0fs", args.soak_seconds)
    logger.info("  Chunk duration:   %ds", args.chunk_duration)
    logger.info("  Max tokens:       %d", args.max_tokens)
    logger.info("  Cooldown:         %.0fs", args.cooldown_seconds)
    logger.info("  Total files:      %d", args.cycles * args.files_per_cycle)
    logger.info("")

    summary = run_stress_test(
        video_path=args.video,
        cycles=args.cycles,
        files_per_cycle=args.files_per_cycle,
        concurrent=args.concurrent,
        soak_seconds=args.soak_seconds,
        cooldown_seconds=args.cooldown_seconds,
        chunk_duration=args.chunk_duration,
        max_tokens=args.max_tokens,
        request_timeout=args.request_timeout,
        gpu_sample_interval=args.gpu_sample_interval,
        gpu_indices=args.gpu_indices,
        stop_on_oom=args.stop_on_oom,
        output_dir=args.output_dir,
    )

    sys.exit(1 if summary.get("leak_likely") or summary.get("oom_detected") else 0)


if __name__ == "__main__":
    main()
