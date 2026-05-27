# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Measure CPU and memory usage of storage-ms container during clip download API calls.

Uses cgroups for fast, low-overhead readings. Polls at 50ms intervals to capture
short-lived downloads (100ms–3s).
"""

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

from nvml_monitor import NvmlMonitor, GpuSnapshot

# Defaults
DEFAULT_BASE_URL = "http://localhost:30888"
DEFAULT_ITERATIONS = 1
POLL_INTERVAL_SEC = 0.05  # 50ms for fast capture
API_TIMEOUT_SEC = 60
DELAY_BETWEEN_ITERATIONS_SEC = 2  # Gap for system to stabilize


def GetCgroupPath(containerName: str) -> Optional[str]:
    """Resolve cgroup path for a Docker container by name.

    Args:
        containerName: Docker container name (e.g. 'storage-ms').

    Returns:
        Absolute path to cgroup directory, or None if not found.
    """
    try:
        result = subprocess.run(
            ["docker", "ps", "-q", "-f", f"name={containerName}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None

        containerId = result.stdout.strip().split("\n")[0]
        inspectResult = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Pid}}", containerId],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if inspectResult.returncode != 0 or not inspectResult.stdout.strip():
            return None

        pid = inspectResult.stdout.strip()
        cgroupPath = Path(f"/proc/{pid}/cgroup")
        if not cgroupPath.exists():
            return None

        # cgroups v2: single line "0::/path"
        with open(cgroupPath) as f:
            for line in f:
                parts = line.strip().split(":")
                if len(parts) >= 3 and parts[2]:
                    return str(Path("/sys/fs/cgroup") / parts[2].lstrip("/"))
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def ReadCgroupStats(cgroupPath: str) -> Optional[Tuple[int, int]]:
    """Read current memory (bytes) and CPU usage (microseconds) from cgroup.

    Memory is computed as memory.current minus inactive_file (page cache),
    matching the formula used by ``docker stats``.

    Args:
        cgroupPath: Path to container cgroup directory.

    Returns:
        (memory_bytes, cpu_usage_usec) or None on error.
    """
    try:
        cgroupDir = Path(cgroupPath)
        memPath = cgroupDir / "memory.current"
        memStatPath = cgroupDir / "memory.stat"
        cpuPath = cgroupDir / "cpu.stat"

        memBytes = int(memPath.read_text().strip()) if memPath.exists() else 0

        inactiveFileBytes = 0
        if memStatPath.exists():
            for line in memStatPath.read_text().splitlines():
                if line.startswith("inactive_file "):
                    inactiveFileBytes = int(line.split()[1])
                    break

        memBytes = max(memBytes - inactiveFileBytes, 0)

        cpuUsec = 0
        if cpuPath.exists():
            for line in cpuPath.read_text().splitlines():
                if line.startswith("usage_usec "):
                    cpuUsec = int(line.split()[1])
                    break

        return (memBytes, cpuUsec)
    except (OSError, ValueError):
        return None


def CalcTimestamps(offsetMs: int, durationMs: int) -> Tuple[str, str]:
    """Compute startTime and endTime for clip download (ISO 8601 with ms).

    Clip ends at (now - offsetMs), starts at (now - offsetMs - durationMs).
    offsetMs = how many ms ago the clip ends.

    Returns:
        (startTime, endTime) as ISO 8601 strings.
    """
    nowMs = int(time.time() * 1000)
    endMs = nowMs - offsetMs
    startMs = endMs - durationMs

    def FormatMs(ms: int) -> str:
        sec = ms // 1000
        frac = ms % 1000
        dt = datetime.fromtimestamp(sec, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{frac:03d}Z"

    return FormatMs(startMs), FormatMs(endMs)


def DownloadClip(
    baseUrl: str,
    streamUuid: str,
    startTime: str,
    endTime: str,
    mode: str,
    outDir: Path,
) -> Tuple[bool, float, Optional[Path]]:
    """Call clip download API and return success, latency in ms, and path to saved file."""
    url = f"{baseUrl}/vst/api/v1/storage/file/{streamUuid}"
    params = {
        "startTime": startTime,
        "endTime": endTime,
        "container": "mp4",
    }
    if mode == "transcode":
        params["transcode"] = "full"
    elif mode == "overlay":
        params["configuration"] = json.dumps(
            {
                "overlay": {
                    "bbox": {"showAll": True, "objectId": []},
                    "color": "green",
                    "thickness": 5,
                    "debug": True,
                    "opacity": 254,
                }
            },
            separators=(",", ":"),
        )

    query = urllib.parse.urlencode(params)
    fullUrl = f"{url}?{query}"

    startNs = time.time_ns()
    try:
        req = Request(fullUrl, method="GET")
        with urlopen(req, timeout=API_TIMEOUT_SEC) as resp:
            data = resp.read()
            name = None
            contentDisp = resp.headers.get("Content-Disposition")
            if contentDisp and "filename=" in contentDisp:
                for part in contentDisp.split(";"):
                    part = part.strip()
                    if part.lower().startswith("filename="):
                        name = part[9:].strip().strip('"\'')
                        name = Path(name).name
                        if name in (".", ".."):
                            name = None
                        break
            outFile = outDir / (name or "clip.mp4")
        outDir.mkdir(parents=True, exist_ok=True)
        outFile.write_bytes(data)
        latencyMs = (time.time_ns() - startNs) / 1_000_000
        return True, latencyMs, outFile
    except (URLError, HTTPError, OSError) as e:
        latencyMs = (time.time_ns() - startNs) / 1_000_000
        print(f"  API error: {e}", file=sys.stderr)
        return False, latencyMs, None


def PollCgroups(
    cgroupPath: str,
    gpuMonitor: NvmlMonitor,
    stopEvent: threading.Event,
    samples: List[Dict],
) -> None:
    """Background thread: poll cgroups + GPU at POLL_INTERVAL_SEC until stopEvent is set."""
    prevCpuUsec = None
    prevWallMs = None

    while not stopEvent.is_set():
        stats = ReadCgroupStats(cgroupPath)
        gpuSnap = gpuMonitor.ReadSnapshot()
        wallclockMs = time.time_ns() // 1_000_000

        if stats:
            memBytes, cpuUsec = stats
            cpuPct = None
            if prevCpuUsec is not None and prevWallMs is not None:
                deltaCpu = cpuUsec - prevCpuUsec
                deltaWallUs = (wallclockMs - prevWallMs) * 1000
                if deltaWallUs > 0:
                    cpuPct = (deltaCpu / deltaWallUs) * 100.0

            samples.append(
                {
                    "phase": "during",
                    "wallclock_ms": wallclockMs,
                    "mem_bytes": memBytes,
                    "cpu_usec": cpuUsec,
                    "cpu_pct": cpuPct,
                    "gpu_mem_used_bytes": gpuSnap.gpu_mem_used_bytes,
                    "container_gpu_mem_bytes": gpuSnap.container_gpu_mem_bytes,
                    "gpu_utilization_pct": gpuSnap.gpu_utilization_pct,
                    "enc_utilization_pct": gpuSnap.enc_utilization_pct,
                    "dec_utilization_pct": gpuSnap.dec_utilization_pct,
                }
            )
            prevCpuUsec = cpuUsec
            prevWallMs = wallclockMs

        stopEvent.wait(timeout=POLL_INTERVAL_SEC)


def RunSingleDownload(
    baseUrl: str,
    streamUuid: str,
    offsetMs: int,
    durationMs: int,
    mode: str,
    cgroupPath: str,
    gpuMonitor: NvmlMonitor,
    outDir: Path,
    iteration: int,
    keepClips: bool,
) -> Tuple[bool, float, Dict]:
    """Run one download, collect baseline + during + post cgroup/GPU readings."""
    startTime, endTime = CalcTimestamps(offsetMs, durationMs)

    baseline = ReadCgroupStats(cgroupPath)
    gpuBaseline = gpuMonitor.ReadSnapshot()
    wallclockMs = time.time_ns() // 1_000_000
    baselineSample = (
        {
            "phase": "before",
            "wallclock_ms": wallclockMs,
            "mem_bytes": baseline[0],
            "cpu_usec": baseline[1],
            "cpu_pct": None,
            "container_gpu_mem_bytes": gpuBaseline.container_gpu_mem_bytes,
            "gpu_mem_used_bytes": gpuBaseline.gpu_mem_used_bytes,
            "gpu_utilization_pct": gpuBaseline.gpu_utilization_pct,
            "enc_utilization_pct": gpuBaseline.enc_utilization_pct,
            "dec_utilization_pct": gpuBaseline.dec_utilization_pct,
        }
        if baseline
        else None
    )

    samples: List[Dict] = []
    stopEvent = threading.Event()
    pollThread = threading.Thread(
        target=PollCgroups,
        args=(cgroupPath, gpuMonitor, stopEvent, samples),
        daemon=True,
    )
    pollThread.start()

    success, latencyMs, outFile = DownloadClip(
        baseUrl, streamUuid, startTime, endTime, mode, outDir
    )

    stopEvent.set()
    pollThread.join(timeout=2.0)

    if not keepClips and outFile and outFile.exists():
        outFile.unlink(missing_ok=True)

    postStats = ReadCgroupStats(cgroupPath)
    gpuPost = gpuMonitor.ReadSnapshot()
    wallclockMs = time.time_ns() // 1_000_000
    postSample = (
        {
            "phase": "after",
            "wallclock_ms": wallclockMs,
            "mem_bytes": postStats[0],
            "cpu_usec": postStats[1],
            "cpu_pct": None,
            "container_gpu_mem_bytes": gpuPost.container_gpu_mem_bytes,
            "gpu_mem_used_bytes": gpuPost.gpu_mem_used_bytes,
            "gpu_utilization_pct": gpuPost.gpu_utilization_pct,
            "enc_utilization_pct": gpuPost.enc_utilization_pct,
            "dec_utilization_pct": gpuPost.dec_utilization_pct,
        }
        if postStats
        else None
    )

    collected = {
        "baseline": baselineSample,
        "samples": samples,
        "post": postSample,
    }
    return success, latencyMs, collected


def SummarizeCollected(collected: Dict) -> Dict:
    """Compute baseline, during, post, and rise for memory, CPU, and GPU."""
    baseline = collected.get("baseline")
    samples = collected.get("samples", [])
    post = collected.get("post")

    memBaselineMb = (baseline["mem_bytes"] / (1024 * 1024)) if baseline else 0.0
    memPostMb = (post["mem_bytes"] / (1024 * 1024)) if post else 0.0
    cpuBaselineUsec = baseline["cpu_usec"] if baseline else 0
    cpuPostUsec = post["cpu_usec"] if post else 0

    memValues = [s["mem_bytes"] for s in samples]
    memPeakMb = max(memValues) / (1024 * 1024) if memValues else 0.0
    memAvgMb = sum(memValues) / len(memValues) / (1024 * 1024) if memValues else 0.0

    cpuValues = [s["cpu_pct"] for s in samples if s.get("cpu_pct") is not None]
    cpuMaxPct = max(cpuValues) if cpuValues else 0.0
    cpuAvgPct = sum(cpuValues) / len(cpuValues) if cpuValues else 0.0

    memPeakRiseMb = memPeakMb - memBaselineMb
    memPostRiseMb = memPostMb - memBaselineMb

    toMb = 1024 * 1024
    gpuBaselineMb = (baseline.get("container_gpu_mem_bytes", 0) / toMb) if baseline else 0.0
    gpuPostMb = (post.get("container_gpu_mem_bytes", 0) / toMb) if post else 0.0

    gpuMemValues = [s.get("container_gpu_mem_bytes", 0) for s in samples]
    gpuPeakMb = max(gpuMemValues) / toMb if gpuMemValues else 0.0

    gpuUtilValues = [s.get("gpu_utilization_pct", 0.0) for s in samples]
    gpuUtilMax = max(gpuUtilValues) if gpuUtilValues else 0.0
    gpuUtilAvg = sum(gpuUtilValues) / len(gpuUtilValues) if gpuUtilValues else 0.0

    encUtilValues = [s.get("enc_utilization_pct", 0.0) for s in samples]
    encUtilMax = max(encUtilValues) if encUtilValues else 0.0
    encUtilAvg = sum(encUtilValues) / len(encUtilValues) if encUtilValues else 0.0

    decUtilValues = [s.get("dec_utilization_pct", 0.0) for s in samples]
    decUtilMax = max(decUtilValues) if decUtilValues else 0.0
    decUtilAvg = sum(decUtilValues) / len(decUtilValues) if decUtilValues else 0.0

    return {
        "mem_baseline_mb": round(memBaselineMb, 2),
        "mem_peak_mb": round(memPeakMb, 2),
        "mem_peak_rise_mb": round(memPeakRiseMb, 2),
        "mem_post_mb": round(memPostMb, 2),
        "mem_post_rise_mb": round(memPostRiseMb, 2),
        "mem_avg_during_mb": round(memAvgMb, 2),
        "cpu_max_pct": round(cpuMaxPct, 2),
        "cpu_avg_pct": round(cpuAvgPct, 2),
        "cpu_total_usec": cpuPostUsec - cpuBaselineUsec,
        "sample_count": len(samples),
        "gpu_mem_baseline_mb": round(gpuBaselineMb, 2),
        "gpu_mem_peak_mb": round(gpuPeakMb, 2),
        "gpu_mem_peak_rise_mb": round(gpuPeakMb - gpuBaselineMb, 2),
        "gpu_mem_post_mb": round(gpuPostMb, 2),
        "gpu_mem_post_rise_mb": round(gpuPostMb - gpuBaselineMb, 2),
        "gpu_util_max_pct": round(gpuUtilMax, 2),
        "gpu_util_avg_pct": round(gpuUtilAvg, 2),
        "enc_util_max_pct": round(encUtilMax, 2),
        "enc_util_avg_pct": round(encUtilAvg, 2),
        "dec_util_max_pct": round(decUtilMax, 2),
        "dec_util_avg_pct": round(decUtilAvg, 2),
    }


def ParseArgs() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Measure storage-ms CPU/memory during clip downloads (cgroups-based)"
    )
    parser.add_argument("uuid", help="Stream UUID")
    parser.add_argument(
        "offset_ms",
        type=int,
        help="Clip ends this many ms ago (e.g. 5000 = clip ends 5 seconds ago)",
    )
    parser.add_argument("duration_ms", type=int, help="Clip duration (ms)")
    parser.add_argument(
        "iterations",
        type=int,
        nargs="?",
        default=DEFAULT_ITERATIONS,
        help=f"Number of API calls (default: {DEFAULT_ITERATIONS})",
    )
    modeGroup = parser.add_mutually_exclusive_group()
    modeGroup.add_argument(
        "--transcode",
        action="store_const",
        dest="mode",
        const="transcode",
        help="Use transcode=full",
    )
    modeGroup.add_argument(
        "--overlay",
        action="store_const",
        dest="mode",
        const="overlay",
        help="Use overlay configuration",
    )
    parser.set_defaults(mode="none")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"VST API base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--container",
        default="storage-ms",
        help="Container name to monitor (default: storage-ms)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Directory for downloaded clips and CSV report",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Write per-iteration CSV report",
    )
    parser.add_argument(
        "--keep-clips",
        action="store_true",
        help="Keep downloaded clip files (default: delete after each iteration)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DELAY_BETWEEN_ITERATIONS_SEC,
        help=f"Seconds between iterations for system to stabilize (default: {DELAY_BETWEEN_ITERATIONS_SEC})",
    )
    return parser.parse_args()


def Main() -> int:
    """Entry point."""
    args = ParseArgs()

    cgroupPath = GetCgroupPath(args.container)
    if not cgroupPath:
        print(f"Error: Could not find cgroup path for container '{args.container}'", file=sys.stderr)
        return 1

    baseUrl = args.base_url.rstrip("/")
    runTimestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    outDir = args.output_dir / "cpu_mem_clips" / runTimestamp
    outDir.mkdir(parents=True, exist_ok=True)

    gpuMonitor = NvmlMonitor(args.container)

    print(f"Container: {args.container}")
    print(f"Output:    {outDir}" + (" (clips kept)" if args.keep_clips else " (clips deleted after each iteration)"))
    print(f"Cgroup:    {cgroupPath}")
    print(f"Base URL:  {baseUrl}")
    print(f"Stream:    {args.uuid}")
    print(f"Offset:    {args.offset_ms} ms (clip ends this many ms ago)")
    print(f"Duration:  {args.duration_ms} ms")
    print(f"Iterations: {args.iterations}")
    print(f"Mode:      {args.mode}")
    print()

    beforeStats = ReadCgroupStats(cgroupPath)
    gpuBefore = gpuMonitor.ReadSnapshot()
    gpuBeforeMb = gpuBefore.container_gpu_mem_bytes / (1024 * 1024)
    if beforeStats:
        memBeforeMb = beforeStats[0] / (1024 * 1024)
        cpuBeforeUsec = beforeStats[1]
        print(f"Before (program start): mem={memBeforeMb:.2f} MB, gpu_mem={gpuBeforeMb:.2f} MB")
    else:
        memBeforeMb = 0.0
        cpuBeforeUsec = 0
    print()

    results: List[Dict] = []
    for i in range(1, args.iterations + 1):
        print(f"--- Iteration {i}/{args.iterations} ---")
        success, latencyMs, collected = RunSingleDownload(
            baseUrl,
            args.uuid,
            args.offset_ms,
            args.duration_ms,
            args.mode,
            cgroupPath,
            gpuMonitor,
            outDir,
            i,
            args.keep_clips,
        )
        summary = SummarizeCollected(collected)
        summary["iteration"] = i
        summary["success"] = success
        summary["latency_ms"] = round(latencyMs, 2)
        summary["collected"] = collected
        results.append(summary)

        status = "OK" if success else "FAIL"
        print(f"  Status: {status}, Latency: {latencyMs:.0f} ms")
        print(
            f"  mem_rise={summary['mem_peak_rise_mb']:.2f} MB (peak), "
            f"mem_rise={summary['mem_post_rise_mb']:.2f} MB (post), "
            f"cpu_max={summary['cpu_max_pct']:.2f}%, cpu_avg={summary['cpu_avg_pct']:.2f}%, "
            f"cpu_total={summary['cpu_total_usec']/1000:.1f} ms"
        )
        print(
            f"  gpu_mem_rise={summary['gpu_mem_peak_rise_mb']:.2f} MB (peak), "
            f"gpu_mem_rise={summary['gpu_mem_post_rise_mb']:.2f} MB (post), "
            f"gpu={summary['gpu_util_max_pct']:.0f}%, "
            f"enc={summary['enc_util_max_pct']:.0f}%, "
            f"dec={summary['dec_util_max_pct']:.0f}%"
        )

        time.sleep(args.delay)
        settledStats = ReadCgroupStats(cgroupPath)
        gpuSettled = gpuMonitor.ReadSnapshot()
        if settledStats:
            settledMemMb = settledStats[0] / (1024 * 1024)
            settledRiseMb = settledMemMb - summary["mem_baseline_mb"]
            gpuSettledMb = gpuSettled.container_gpu_mem_bytes / (1024 * 1024)
            gpuSettledRiseMb = gpuSettledMb - summary["gpu_mem_baseline_mb"]
            summary["mem_settled_mb"] = round(settledMemMb, 2)
            summary["mem_settled_rise_mb"] = round(settledRiseMb, 2)
            summary["gpu_mem_settled_mb"] = round(gpuSettledMb, 2)
            summary["gpu_mem_settled_rise_mb"] = round(gpuSettledRiseMb, 2)
            collected["settled"] = {
                "phase": "settled",
                "wallclock_ms": time.time_ns() // 1_000_000,
                "mem_bytes": settledStats[0],
                "cpu_usec": settledStats[1],
                "cpu_pct": None,
                "container_gpu_mem_bytes": gpuSettled.container_gpu_mem_bytes,
                "gpu_mem_used_bytes": gpuSettled.gpu_mem_used_bytes,
                "gpu_utilization_pct": gpuSettled.gpu_utilization_pct,
                "enc_utilization_pct": gpuSettled.enc_utilization_pct,
                "dec_utilization_pct": gpuSettled.dec_utilization_pct,
            }
            print(
                f"  mem_rise={settledRiseMb:.2f} MB (settled after {args.delay}s delay), "
                f"gpu_mem_rise={gpuSettledRiseMb:.2f} MB (settled)"
            )
        else:
            summary["mem_settled_mb"] = None
            summary["mem_settled_rise_mb"] = None
            summary["gpu_mem_settled_mb"] = None
            summary["gpu_mem_settled_rise_mb"] = None
            collected["settled"] = None
        print()

    time.sleep(1)
    afterStats = ReadCgroupStats(cgroupPath)
    gpuAfter = gpuMonitor.ReadSnapshot()
    gpuAfterMb = gpuAfter.container_gpu_mem_bytes / (1024 * 1024)
    gpuRiseMb = gpuAfterMb - gpuBeforeMb
    if afterStats:
        memAfterMb = afterStats[0] / (1024 * 1024)
        cpuAfterUsec = afterStats[1]
        memRiseMb = memAfterMb - memBeforeMb
        cpuTotalMs = (cpuAfterUsec - cpuBeforeUsec) / 1000
        runAfter = {
            "mem_mb": round(memAfterMb, 2), "mem_rise_mb": round(memRiseMb, 2),
            "cpu_total_ms": round(cpuTotalMs, 1),
            "gpu_mem_mb": round(gpuAfterMb, 2), "gpu_mem_rise_mb": round(gpuRiseMb, 2),
        }
        print(
            f"After (program end): mem={memAfterMb:.2f} MB, rise={memRiseMb:.2f} MB, "
            f"cpu_total={cpuTotalMs:.1f} ms, gpu_mem={gpuAfterMb:.2f} MB, gpu_rise={gpuRiseMb:.2f} MB"
        )
    else:
        runAfter = None
    runBefore = {
        "mem_mb": round(memBeforeMb, 2),
        "gpu_mem_mb": round(gpuBeforeMb, 2),
    } if beforeStats else None
    print()

    # Aggregate summary across iterations (when > 1)
    if args.iterations > 1:
        successful = [r for r in results if r["success"]]
        n = len(successful)
        if n > 0:
            agg = {
                "latency_ms_avg": round(sum(r["latency_ms"] for r in successful) / n, 2),
                "latency_ms_min": round(min(r["latency_ms"] for r in successful), 2),
                "latency_ms_max": round(max(r["latency_ms"] for r in successful), 2),
                "mem_baseline_mb_avg": round(sum(r["mem_baseline_mb"] for r in successful) / n, 2),
                "mem_peak_mb_avg": round(sum(r["mem_peak_mb"] for r in successful) / n, 2),
                "mem_peak_rise_mb_avg": round(sum(r["mem_peak_rise_mb"] for r in successful) / n, 2),
                "mem_peak_rise_mb_min": round(min(r["mem_peak_rise_mb"] for r in successful), 2),
                "mem_peak_rise_mb_max": round(max(r["mem_peak_rise_mb"] for r in successful), 2),
                "mem_post_mb_avg": round(sum(r["mem_post_mb"] for r in successful) / n, 2),
                "mem_post_rise_mb_avg": round(sum(r["mem_post_rise_mb"] for r in successful) / n, 2),
                "mem_post_rise_mb_min": round(min(r["mem_post_rise_mb"] for r in successful), 2),
                "mem_post_rise_mb_max": round(max(r["mem_post_rise_mb"] for r in successful), 2),
                "mem_settled_rise_mb_avg": round(
                    sum(r["mem_settled_rise_mb"] for r in successful if r["mem_settled_rise_mb"] is not None) /
                    max(sum(1 for r in successful if r["mem_settled_rise_mb"] is not None), 1), 2
                ),
                "mem_settled_rise_mb_min": round(
                    min((r["mem_settled_rise_mb"] for r in successful if r["mem_settled_rise_mb"] is not None), default=0.0), 2
                ),
                "mem_settled_rise_mb_max": round(
                    max((r["mem_settled_rise_mb"] for r in successful if r["mem_settled_rise_mb"] is not None), default=0.0), 2
                ),
                "cpu_max_pct_avg": round(sum(r["cpu_max_pct"] for r in successful) / n, 2),
                "cpu_max_pct_max": round(max(r["cpu_max_pct"] for r in successful), 2),
                "cpu_avg_pct_avg": round(sum(r["cpu_avg_pct"] for r in successful) / n, 2),
                "cpu_total_usec_avg": round(sum(r["cpu_total_usec"] for r in successful) / n, 0),
                "cpu_total_ms_avg": round(sum(r["cpu_total_usec"] for r in successful) / n / 1000, 1),
                "gpu_mem_peak_rise_mb_avg": round(sum(r["gpu_mem_peak_rise_mb"] for r in successful) / n, 2),
                "gpu_mem_peak_rise_mb_min": round(min(r["gpu_mem_peak_rise_mb"] for r in successful), 2),
                "gpu_mem_peak_rise_mb_max": round(max(r["gpu_mem_peak_rise_mb"] for r in successful), 2),
                "gpu_mem_post_rise_mb_avg": round(sum(r["gpu_mem_post_rise_mb"] for r in successful) / n, 2),
                "gpu_mem_post_rise_mb_min": round(min(r["gpu_mem_post_rise_mb"] for r in successful), 2),
                "gpu_mem_post_rise_mb_max": round(max(r["gpu_mem_post_rise_mb"] for r in successful), 2),
                "gpu_mem_settled_rise_mb_avg": round(
                    sum(r["gpu_mem_settled_rise_mb"] for r in successful if r.get("gpu_mem_settled_rise_mb") is not None) /
                    max(sum(1 for r in successful if r.get("gpu_mem_settled_rise_mb") is not None), 1), 2
                ),
                "gpu_mem_settled_rise_mb_min": round(
                    min((r["gpu_mem_settled_rise_mb"] for r in successful if r.get("gpu_mem_settled_rise_mb") is not None), default=0.0), 2
                ),
                "gpu_mem_settled_rise_mb_max": round(
                    max((r["gpu_mem_settled_rise_mb"] for r in successful if r.get("gpu_mem_settled_rise_mb") is not None), default=0.0), 2
                ),
                "gpu_util_max_pct_avg": round(sum(r["gpu_util_max_pct"] for r in successful) / n, 2),
                "enc_util_max_pct_avg": round(sum(r["enc_util_max_pct"] for r in successful) / n, 2),
                "dec_util_max_pct_avg": round(sum(r["dec_util_max_pct"] for r in successful) / n, 2),
            }
            print("--- Aggregate (all iterations) ---")
            print(
                f"  Latency: avg={agg['latency_ms_avg']:.0f} ms, "
                f"min={agg['latency_ms_min']:.0f} ms, max={agg['latency_ms_max']:.0f} ms"
            )
            print(
                f"  mem_peak_rise: avg={agg['mem_peak_rise_mb_avg']:.2f} MB, "
                f"min={agg['mem_peak_rise_mb_min']:.2f} MB, max={agg['mem_peak_rise_mb_max']:.2f} MB"
            )
            print(
                f"  mem_post_rise: avg={agg['mem_post_rise_mb_avg']:.2f} MB, "
                f"min={agg['mem_post_rise_mb_min']:.2f} MB, max={agg['mem_post_rise_mb_max']:.2f} MB"
            )
            print(
                f"  mem_settled_rise: avg={agg['mem_settled_rise_mb_avg']:.2f} MB, "
                f"min={agg['mem_settled_rise_mb_min']:.2f} MB, max={agg['mem_settled_rise_mb_max']:.2f} MB"
            )
            print(
                f"  cpu_max_avg={agg['cpu_max_pct_avg']:.2f}%, cpu_avg_avg={agg['cpu_avg_pct_avg']:.2f}%, "
                f"cpu_total_avg={agg['cpu_total_ms_avg']:.1f} ms"
            )
            print(
                f"  gpu_mem_peak_rise: avg={agg['gpu_mem_peak_rise_mb_avg']:.2f} MB, "
                f"min={agg['gpu_mem_peak_rise_mb_min']:.2f} MB, max={agg['gpu_mem_peak_rise_mb_max']:.2f} MB"
            )
            print(
                f"  gpu_mem_post_rise: avg={agg['gpu_mem_post_rise_mb_avg']:.2f} MB, "
                f"min={agg['gpu_mem_post_rise_mb_min']:.2f} MB, max={agg['gpu_mem_post_rise_mb_max']:.2f} MB"
            )
            print(
                f"  gpu_mem_settled_rise: avg={agg['gpu_mem_settled_rise_mb_avg']:.2f} MB, "
                f"min={agg['gpu_mem_settled_rise_mb_min']:.2f} MB, max={agg['gpu_mem_settled_rise_mb_max']:.2f} MB"
            )
            print(
                f"  gpu_util_max_avg={agg['gpu_util_max_pct_avg']:.0f}%, "
                f"enc_util_max_avg={agg['enc_util_max_pct_avg']:.0f}%, "
                f"dec_util_max_avg={agg['dec_util_max_pct_avg']:.0f}%"
            )
            print()
        else:
            agg = None
    else:
        agg = None

    # Save result file with summary and raw samples
    # run_timestamp: UTC when run started (YYYYMMDDTHHMMSS), used for unique output dir/filename
    resultPath = args.output_dir / f"cpu_mem_stats_{runTimestamp}.json"
    resultData = {
        "run_timestamp": runTimestamp,
        "stream_uuid": args.uuid,
        "offset_ms": args.offset_ms,
        "duration_ms": args.duration_ms,
        "mode": args.mode,
        "run_before": runBefore,
        "run_after": runAfter,
        "aggregate": agg,
        "iterations": [
            {
                "iteration": r["iteration"],
                "success": r["success"],
                "latency_ms": r["latency_ms"],
                "mem_baseline_mb": r["mem_baseline_mb"],
                "mem_peak_mb": r["mem_peak_mb"],
                "mem_peak_rise_mb": r["mem_peak_rise_mb"],
                "mem_post_mb": r["mem_post_mb"],
                "mem_post_rise_mb": r["mem_post_rise_mb"],
                "mem_settled_mb": r.get("mem_settled_mb"),
                "mem_settled_rise_mb": r.get("mem_settled_rise_mb"),
                "mem_avg_during_mb": r["mem_avg_during_mb"],
                "cpu_max_pct": r["cpu_max_pct"],
                "cpu_avg_pct": r["cpu_avg_pct"],
                "cpu_total_usec": r["cpu_total_usec"],
                "gpu_mem_baseline_mb": r["gpu_mem_baseline_mb"],
                "gpu_mem_peak_mb": r["gpu_mem_peak_mb"],
                "gpu_mem_peak_rise_mb": r["gpu_mem_peak_rise_mb"],
                "gpu_mem_post_mb": r["gpu_mem_post_mb"],
                "gpu_mem_post_rise_mb": r["gpu_mem_post_rise_mb"],
                "gpu_mem_settled_mb": r.get("gpu_mem_settled_mb"),
                "gpu_mem_settled_rise_mb": r.get("gpu_mem_settled_rise_mb"),
                "gpu_util_max_pct": r["gpu_util_max_pct"],
                "gpu_util_avg_pct": r["gpu_util_avg_pct"],
                "enc_util_max_pct": r["enc_util_max_pct"],
                "enc_util_avg_pct": r["enc_util_avg_pct"],
                "dec_util_max_pct": r["dec_util_max_pct"],
                "dec_util_avg_pct": r["dec_util_avg_pct"],
                "sample_count": r["sample_count"],
                "baseline": r["collected"]["baseline"],
                "samples": r["collected"]["samples"],
                "post": r["collected"]["post"],
                "settled": r["collected"].get("settled"),
            }
            for r in results
        ],
    }
    with open(resultPath, "w") as f:
        json.dump(resultData, f, indent=2)
    print(f"Result file: {resultPath}")

    if args.csv:
        csvPath = args.output_dir / f"cpu_mem_stats_{runTimestamp}.csv"
        with open(csvPath, "w") as f:
            f.write(
                "iteration,success,latency_ms,mem_baseline_mb,"
                "mem_peak_mb,mem_peak_rise_mb,"
                "mem_post_mb,mem_post_rise_mb,"
                "mem_settled_mb,mem_settled_rise_mb,"
                "mem_avg_during_mb,"
                "cpu_max_pct,cpu_avg_pct,cpu_total_usec,"
                "gpu_mem_baseline_mb,gpu_mem_peak_mb,gpu_mem_peak_rise_mb,"
                "gpu_mem_post_mb,gpu_mem_post_rise_mb,"
                "gpu_mem_settled_mb,gpu_mem_settled_rise_mb,"
                "gpu_util_max_pct,gpu_util_avg_pct,"
                "enc_util_max_pct,enc_util_avg_pct,"
                "dec_util_max_pct,dec_util_avg_pct,"
                "sample_count\n"
            )
            for r in results:
                settledMb = r.get("mem_settled_mb", "")
                settledRiseMb = r.get("mem_settled_rise_mb", "")
                gpuSettledMb = r.get("gpu_mem_settled_mb", "")
                gpuSettledRiseMb = r.get("gpu_mem_settled_rise_mb", "")
                f.write(
                    f"{r['iteration']},{r['success']},{r['latency_ms']},"
                    f"{r['mem_baseline_mb']},{r['mem_peak_mb']},{r['mem_peak_rise_mb']},"
                    f"{r['mem_post_mb']},{r['mem_post_rise_mb']},"
                    f"{settledMb},{settledRiseMb},"
                    f"{r['mem_avg_during_mb']},"
                    f"{r['cpu_max_pct']},{r['cpu_avg_pct']},{r['cpu_total_usec']},"
                    f"{r['gpu_mem_baseline_mb']},{r['gpu_mem_peak_mb']},{r['gpu_mem_peak_rise_mb']},"
                    f"{r['gpu_mem_post_mb']},{r['gpu_mem_post_rise_mb']},"
                    f"{gpuSettledMb},{gpuSettledRiseMb},"
                    f"{r['gpu_util_max_pct']},{r['gpu_util_avg_pct']},"
                    f"{r['enc_util_max_pct']},{r['enc_util_avg_pct']},"
                    f"{r['dec_util_max_pct']},{r['dec_util_avg_pct']},"
                    f"{r['sample_count']}\n"
                )
            if agg:
                f.write(
                    f"aggregate,,{agg['latency_ms_avg']},"
                    f"{agg['mem_baseline_mb_avg']},{agg['mem_peak_mb_avg']},"
                    f"{agg['mem_peak_rise_mb_avg']},"
                    f"{agg['mem_post_mb_avg']},{agg['mem_post_rise_mb_avg']},"
                    f",{agg['mem_settled_rise_mb_avg']},,"
                    f"{agg['cpu_max_pct_avg']},{agg['cpu_avg_pct_avg']},"
                    f"{int(agg['cpu_total_usec_avg'])},"
                    f",,"
                    f"{agg['gpu_mem_peak_rise_mb_avg']},"
                    f",{agg['gpu_mem_post_rise_mb_avg']},"
                    f",,,"
                    f"{agg['gpu_util_max_pct_avg']},,"
                    f"{agg['enc_util_max_pct_avg']},,"
                    f"{agg['dec_util_max_pct_avg']},,\n"
                )
        print(f"CSV report: {csvPath}")

    if not args.keep_clips and outDir.exists() and not any(outDir.iterdir()):
        outDir.rmdir()

    gpuMonitor.Shutdown()

    failed = sum(1 for r in results if not r["success"])
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(Main())
