# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Resource-side observation: Elasticsearch counts, Prometheus, pod logs.

Everything here is best-effort. A run's result is its latency and throughput;
these collectors add context. When one cannot answer it says so explicitly --
``None``, an ``error`` key, a ``scan_status`` -- rather than returning a zero
that reads like a measurement.
"""

from __future__ import annotations

import json
import logging
import os
import re
import resource
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Elasticsearch
# ---------------------------------------------------------------------------


def capture_es_index_counts(
    elasticsearch_url: str,
    index_patterns: list[str],
    timeout: int = 30,
) -> dict[str, Any]:
    """Record document counts per index pattern.

    This is what pins the corpus: two runs taken days apart are only comparable
    if they searched the same documents, and these counts are the evidence.

    ``_cat/count`` is used rather than ``_cat/indices``, because the latter's
    ``docs.count`` includes nested subdocuments. On this corpus the two disagree
    by roughly 2x, and by 16x on ``mdx-raw``, so mixing them makes a corpus look
    like it changed when it did not.
    """
    if not elasticsearch_url or not index_patterns:
        return {"enabled": False}

    base = elasticsearch_url.rstrip("/")
    counts: dict[str, Any] = {"enabled": True, "elasticsearch_url": base, "indices": {}}

    for pattern in index_patterns:
        try:
            resp = requests.get(
                f"{base}/_cat/count/{pattern}",
                params={"format": "json"},
                timeout=timeout,
            )
            if resp.status_code != 200:
                counts["indices"][pattern] = {"error": f"HTTP {resp.status_code}"}
                continue
            rows = resp.json()
            total = sum(int(row.get("count", 0)) for row in rows if isinstance(row, dict))
            counts["indices"][pattern] = {"doc_count": total, "rows": rows}
        except (requests.RequestException, ValueError, TypeError) as e:
            counts["indices"][pattern] = {"error": str(e)}

    return counts


# ---------------------------------------------------------------------------
# Prometheus
# ---------------------------------------------------------------------------


def query_prometheus_range(
    prometheus_url: str,
    queries: dict[str, str],
    start_epoch: float,
    end_epoch: float,
    step_sec: int = 15,
    timeout: int = 30,
) -> dict[str, Any]:
    """Evaluate several PromQL expressions over the run window."""
    if not prometheus_url or not queries:
        return {}

    results: dict[str, Any] = {}
    base = prometheus_url.rstrip("/")

    for name, query in queries.items():
        if not query.strip():
            continue
        params = {
            "query": query,
            "start": start_epoch,
            "end": end_epoch,
            "step": step_sec,
        }
        try:
            resp = requests.get(
                f"{base}/api/v1/query_range?{urlencode(params)}", timeout=timeout
            )
            results[name] = resp.json()
        except Exception as e:  # noqa: BLE001 - a collector must not fail a run
            results[name] = {"error": str(e)}

    return results


def query_prometheus_instant(
    prometheus_url: str,
    query: str,
    timeout: int = 15,
) -> float | None:
    """Evaluate one PromQL expression now, returning the largest scalar in it.

    ``None`` means the question could not be answered -- Prometheus unreachable,
    the expression broken, or no series matching. Callers must not read that as
    zero: an absent series and an idle pod look identical here, and only the
    caller knows which of the two is safe to assume.
    """
    if not prometheus_url or not query.strip():
        return None

    url = f"{prometheus_url.rstrip('/')}/api/v1/query?{urlencode({'query': query})}"
    try:
        payload = requests.get(url, timeout=timeout).json()
    except Exception as e:  # noqa: BLE001
        logger.debug("instant query failed: %s", e)
        return None

    if payload.get("status") != "success":
        return None

    values = []
    for item in payload.get("data", {}).get("result", []):
        sample = item.get("value") or []
        if len(sample) == 2:
            try:
                values.append(float(sample[1]))
            except (TypeError, ValueError):
                continue
    return max(values) if values else None


def _series_key(labels: dict[str, Any]) -> str:
    """Name a series after the workload it describes.

    DCGM publishes the pod holding the GPU as ``exported_pod`` -- plain ``pod``
    is the exporter itself -- and one pod can hold several devices, so the GPU
    index is part of the key.
    """
    name = labels.get("exported_pod") or labels.get("pod") or "unlabelled"
    gpu = labels.get("gpu")
    return f"{name}/gpu{gpu}" if gpu is not None else str(name)


def _sample_values(item: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for sample in item.get("values", []):
        if len(sample) >= 2:
            try:
                v = float(sample[1])
                if v == v:  # NaN check
                    values.append(v)
            except (TypeError, ValueError):
                pass
    return values


def summarize_prometheus_metric(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract avg, peak and a per-series breakdown from a range query result.

    The overall avg/peak pools every series, which for a GPU query dilutes the
    busy devices with the idle ones. ``series`` keeps each device separate so a
    metric can be attributed to the service that caused it.

    ``samples`` is reported because it is the difference between a figure and a
    coin flip: a run shorter than the scrape interval yields one sample, and an
    average over one sample says nothing about the run.
    """
    empty: dict[str, Any] = {"avg": None, "peak": None, "samples": 0, "series": {}}
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return empty

    values: list[float] = []
    series: dict[str, dict[str, float | int]] = {}

    for item in payload.get("data", {}).get("result", []):
        item_values = _sample_values(item)
        if not item_values:
            continue
        values.extend(item_values)
        key = _series_key(item.get("metric", {}))
        # Two series can collapse onto one key when a pod is replaced mid-window;
        # they describe the same device, so they are merged rather than dropped.
        previous = series.get(key)
        peak = max(item_values)
        if previous:
            peak = max(peak, float(previous["peak"]))
            item_values.extend([previous["avg"]] * int(previous["samples"]))
        series[key] = {
            "avg": sum(item_values) / len(item_values),
            "peak": peak,
            "samples": len(item_values),
        }

    if not values:
        return empty
    return {
        "avg": sum(values) / len(values),
        "peak": max(values),
        "samples": len(values),
        "series": series,
    }


def summarize_counter_metric(
    payload: dict[str, Any],
    window_sec: float,
) -> dict[str, Any]:
    """Average a counter over the run from its own endpoints, not from ``rate()``.

    ``rate(counter[30s])`` answers "how fast did this grow over the last 30
    seconds", which is the wrong question for a run shorter than that window:
    the window still holds the previous run's work, so a short run reports its
    predecessor's CPU as its own.

    Differencing the counter over the window cannot borrow from a neighbour:
    what it returns is the CPU-seconds the pod actually billed between the first
    and last scrape inside the window. ``covered_sec`` and ``coverage_ratio`` say
    how much of the run that spans, because the honest answer for a run shorter
    than a couple of scrape intervals is "not enough to tell", and ``mean_rate``
    is ``None`` there rather than a number that reads as measured.

    Counters reset to zero when a container restarts, which would otherwise show
    up as a large negative delta; those steps are summed back in and counted, so
    a restart mid-run is visible instead of silently halving the figure.
    """
    empty: dict[str, Any] = {
        "mean_rate": None,
        "delta": None,
        "covered_sec": 0.0,
        "coverage_ratio": 0.0,
        "samples": 0,
        "resets": 0,
        "series": {},
    }
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return empty

    series: dict[str, dict[str, Any]] = {}
    total_delta = 0.0
    total_resets = 0
    spans: list[tuple[float, float]] = []

    for item in payload.get("data", {}).get("result", []):
        points: list[tuple[float, float]] = []
        for sample in item.get("values", []):
            if len(sample) < 2:
                continue
            try:
                ts, value = float(sample[0]), float(sample[1])
            except (TypeError, ValueError):
                continue
            if value == value:  # NaN check
                points.append((ts, value))
        if len(points) < 2:
            continue

        points.sort()
        delta = 0.0
        resets = 0
        for (_, previous), (_, current) in zip(points, points[1:]):
            if current >= previous:
                delta += current - previous
            else:
                # A restart: the counter began again from zero, so everything it
                # had accumulated since the last scrape is what `current` holds.
                delta += current
                resets += 1

        covered = points[-1][0] - points[0][0]
        if covered <= 0:
            continue

        key = _series_key(item.get("metric", {}))
        series[key] = {
            "mean_rate": delta / covered,
            "delta": delta,
            "covered_sec": covered,
            "samples": len(points),
            "resets": resets,
        }
        total_delta += delta
        total_resets += resets
        spans.append((points[0][0], points[-1][0]))

    if not spans:
        return empty

    covered = max(end for _, end in spans) - min(start for start, _ in spans)
    if covered <= 0:
        return empty

    return {
        "mean_rate": total_delta / covered,
        "delta": total_delta,
        "covered_sec": covered,
        "coverage_ratio": (covered / window_sec) if window_sec > 0 else 0.0,
        "samples": sum(int(s["samples"]) for s in series.values()),
        "resets": total_resets,
        "series": series,
    }


def collect_run_metrics(
    prometheus_url: str,
    range_queries: dict[str, str],
    start_epoch: float,
    end_epoch: float,
    step_sec: int = 15,
    counter_queries: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Collect and summarize Prometheus metrics for one run's window.

    ``range_queries`` carry their own time window inside the expression, so they
    describe the shape of the run over time -- use them for timelines and peaks.
    ``counter_queries`` are bare counters, differenced over the window by
    :func:`summarize_counter_metric`; those are the ones to quote as a run's
    mean.
    """
    raw = query_prometheus_range(
        prometheus_url, range_queries, start_epoch, end_epoch, step_sec
    )

    summary: dict[str, Any] = {"raw": raw, "summary": {}}
    for name, payload in raw.items():
        summary["summary"][name] = summarize_prometheus_metric(payload)

    if counter_queries:
        raw_counters = query_prometheus_range(
            prometheus_url, counter_queries, start_epoch, end_epoch, step_sec
        )
        window_sec = max(end_epoch - start_epoch, 0.0)
        summary["raw_counters"] = raw_counters
        summary["window_mean"] = {
            name: summarize_counter_metric(payload, window_sec)
            for name, payload in raw_counters.items()
        }

    return summary


# ---------------------------------------------------------------------------
# Pod logs
# ---------------------------------------------------------------------------


def pod_is_selected(pod_item: dict[str, Any], pod_prefixes: list[str]) -> bool:
    """Whether a pod belongs to one of the configured workloads.

    A substring test on the name is not selective enough: ``vss-agent`` is also
    a prefix of ``vss-agent-ui``, so the UI's errors would be counted against
    the search pipeline. The chart labels each workload with
    ``app.kubernetes.io/name``, which distinguishes them exactly, so pods
    carrying that label are judged on it alone and only unlabelled pods fall
    back to a name match.
    """
    metadata = pod_item.get("metadata", {})
    workload = (metadata.get("labels") or {}).get("app.kubernetes.io/name")
    if workload:
        return workload in pod_prefixes
    return any(prefix in metadata.get("name", "") for prefix in pod_prefixes)


def select_pods(
    pods_data: dict[str, Any], pod_prefixes: list[str]
) -> list[dict[str, Any]]:
    """Pods belonging to the configured workloads, warning about the misses.

    Because the label match is exact, a stale entry collects nothing at all, and
    the absence then reads as "that service logged nothing" rather than as a bad
    config -- so it is called out.
    """
    items = pods_data.get("items", [])
    selected = [item for item in items if pod_is_selected(item, pod_prefixes)]

    matched = {
        (item.get("metadata", {}).get("labels") or {}).get("app.kubernetes.io/name")
        for item in selected
    }
    unmatched = [
        prefix for prefix in pod_prefixes
        if prefix not in matched
        and not any(
            prefix in item.get("metadata", {}).get("name", "") for item in selected
        )
    ]
    if unmatched:
        logger.warning(
            "pod_log_scan: no pod matches %s -- check the app.kubernetes.io/name "
            "labels in the namespace, these workloads are being skipped",
            ", ".join(sorted(unmatched)),
        )

    return selected


def list_pods(namespace: str) -> dict[str, Any] | None:
    try:
        proc = subprocess.run(
            ["kubectl", "get", "pods", "-n", namespace, "-o", "json"],
            capture_output=True, text=True, check=False, timeout=30,
        )
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError, OSError):
        return None


class PodLogStreamer:
    """Follow pod logs to disk for the whole run instead of fetching them at the end.

    ``kubectl logs --since-time`` can only return what the kubelet still has.
    The kubelet rotates a container's log at ``containerLogMaxSize`` and
    ``kubectl logs`` reads the current file, so a service that starts screaming
    loses the beginning of its own incident -- exactly the part worth reading.

    Following from the start writes each line as it is produced, which rotation
    cannot take back. ``kubectl logs -f`` ends when the pod restarts; the run
    then keeps whatever was written up to that point, which is the interesting
    part anyway, and :func:`collect_pod_logs` still runs afterwards to fill in
    any pod this missed.
    """

    def __init__(
        self,
        namespace: str,
        pod_prefixes: list[str],
        since_time: datetime,
        output_dir: Path,
    ) -> None:
        self._namespace = namespace
        self._pod_prefixes = pod_prefixes
        self._since_time = since_time
        self._output_dir = output_dir
        self._procs: list[tuple[subprocess.Popen, Any]] = []
        self.streamed: set[str] = set()

    def __enter__(self) -> PodLogStreamer:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        since_str = self._since_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        pods_data = list_pods(self._namespace)
        if pods_data is None:
            logger.warning("Log streaming disabled: could not list pods")
            return self

        for pod_item in select_pods(pods_data, self._pod_prefixes):
            pod_name = pod_item.get("metadata", {}).get("name", "")
            for container in pod_item.get("spec", {}).get("containers", []):
                name = container.get("name", "")
                if not name:
                    continue
                log_file = self._output_dir / f"{pod_name}__{name}.log"
                handle = None
                try:
                    handle = log_file.open("w", encoding="utf-8")
                    child = subprocess.Popen(
                        ["kubectl", "logs", pod_name, "-n", self._namespace,
                         "-c", name, "--since-time", since_str,
                         "--timestamps", "--follow"],
                        stdout=handle, stderr=subprocess.DEVNULL,
                    )
                except OSError as e:
                    if handle is not None:
                        handle.close()
                    logger.warning("Could not follow %s/%s: %s", pod_name, name, e)
                    continue
                self._procs.append((child, handle))
                self.streamed.add(log_file.name)
        return self

    def __exit__(self, *_exc: Any) -> None:
        for child, handle in self._procs:
            try:
                child.terminate()
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
            except OSError:
                pass
            finally:
                handle.close()
        # An empty file is worse than none: every reader here globs for a log and
        # treats a match as data, so a pod that said nothing would look scanned.
        for name in list(self.streamed):
            path = self._output_dir / name
            if path.exists() and path.stat().st_size == 0:
                path.unlink()
                self.streamed.discard(name)


def collect_pod_logs(
    namespace: str,
    pod_prefixes: list[str],
    since_time: datetime,
    output_dir: Path,
    tail_lines: int = 50000,
    skip_files: set[str] | None = None,
) -> dict[str, Any]:
    """Collect pod logs for the benchmark window into the run directory.

    ``skip_files`` names logs a :class:`PodLogStreamer` already wrote. Those are
    strictly better -- they survived rotation -- so this must not overwrite
    them, and only fills in pods the streamer never reached.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    scan: dict[str, Any] = {"pods_scanned": 0, "files_written": [], "errors": []}

    pods_data = list_pods(namespace)
    if pods_data is None:
        scan["errors"].append("kubectl get pods failed")
        return scan

    since_str = since_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    for pod_item in select_pods(pods_data, pod_prefixes):
        pod_name = pod_item.get("metadata", {}).get("name", "")
        for container_spec in pod_item.get("spec", {}).get("containers", []):
            container = container_spec.get("name", "")
            if not container:
                continue
            if skip_files and f"{pod_name}__{container}.log" in skip_files:
                continue
            try:
                proc = subprocess.run(
                    ["kubectl", "logs", pod_name, "-n", namespace, "-c", container,
                     "--since-time", since_str, "--tail", str(tail_lines),
                     "--timestamps"],
                    capture_output=True, text=True, check=False, timeout=120,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    log_file = output_dir / f"{pod_name}__{container}.log"
                    log_file.write_text(proc.stdout, encoding="utf-8")
                    scan["files_written"].append(log_file.name)
                    scan["pods_scanned"] += 1
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
                scan["errors"].append(f"{pod_name}/{container}: {e}")

    return scan


def scan_pod_log_errors(
    log_dir: Path,
    patterns: list[str],
    exclude_patterns: list[str] | None = None,
) -> dict[str, Any]:
    """Count error-pattern matches in the pod logs this run wrote to ``log_dir``.

    Scans the files rather than fetching the logs again: the streamed files
    survived log rotation, and a second ``kubectl logs`` would not.
    """
    compiled = [re.compile(p) for p in patterns]
    excludes = [re.compile(p) for p in (exclude_patterns or [])]
    result: dict[str, Any] = {"total_errors": 0, "per_pod": {}, "scan_status": "ok"}
    files = sorted(log_dir.glob("*.log")) if log_dir.is_dir() else []
    if not files:
        result["scan_status"] = "no_logs"
        return result

    for log_file in files:
        count = 0
        with log_file.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if any(exc.search(line) for exc in excludes):
                    continue
                if any(pat.search(line) for pat in compiled):
                    count += 1
        if count:
            result["per_pod"][log_file.stem.replace("__", "/", 1)] = count
            result["total_errors"] += count
    return result


def capture_wedge_state(
    namespace: str,
    pod_prefixes: list[str],
    output_dir: Path,
    reason: str,
) -> dict[str, Any]:
    """Photograph a stuck cluster before anyone restarts it.

    A wedge survives for hours, but the pod is usually restarted before anyone
    takes a snapshot, and the restart is what destroys the evidence. This runs
    on the abort path so the snapshot exists by default.

    Everything is best-effort and individually timed out: failing to collect a
    nice-to-have must not mask the abort itself.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"reason": reason, "captured": [], "failed": []}

    def run(name: str, cmd: list[str], timeout: int = 60) -> None:
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, check=False, timeout=timeout
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            result["failed"].append(f"{name}: {e}")
            return
        body = proc.stdout + (f"\n--- stderr ---\n{proc.stderr}" if proc.stderr else "")
        (output_dir / f"{name}.txt").write_text(
            f"$ {' '.join(cmd)}\n\n{body}", encoding="utf-8"
        )
        if proc.returncode != 0:
            # The file is still written -- its stderr is the finding -- but a
            # non-zero exit is reported rather than counted as collected.
            result["failed"].append(
                f"{name}: exit {proc.returncode}: {proc.stderr.strip()[:120]}"
            )
            return
        result["captured"].append(name)

    run("kubectl_pods", ["kubectl", "get", "pods", "-n", namespace, "-o", "wide"])
    run("kubectl_events",
        ["kubectl", "get", "events", "-n", namespace, "--sort-by=.lastTimestamp"])
    run("kubectl_top_pods", ["kubectl", "top", "pods", "-n", namespace])

    pods_data = list_pods(namespace)
    pods = (
        [p.get("metadata", {}).get("name", "") for p in select_pods(pods_data, pod_prefixes)]
        if pods_data
        else []
    )
    if pods_data is None:
        result["failed"].append("pod list: kubectl get pods failed")

    for pod in filter(None, pods):
        run(f"describe__{pod}", ["kubectl", "describe", "pod", pod, "-n", namespace])
        # Grabbed now rather than at the end of the sweep, because rotation is
        # what costs the evidence.
        run(f"logs_tail__{pod}",
            ["kubectl", "logs", pod, "-n", namespace, "--all-containers=true",
             "--tail", "4000", "--timestamps"], timeout=120)

    for pod in [p for p in pods if "rtvi-vlm" in p]:
        run(f"nvidia_smi__{pod}",
            ["kubectl", "exec", pod, "-n", namespace, "--", "nvidia-smi"])

    (output_dir / "capture.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    return result


# ---------------------------------------------------------------------------
# RT-VLM's own histograms
# ---------------------------------------------------------------------------
#
# The CLI sends the critic's clips as ``video_url``, so RT-VLM downloads and
# decodes each clip itself, inside what the CLI can only time as
# ``vlm: inference``. RT-VLM publishes the split on ``/v1/metrics``, which
# Prometheus does not scrape in this profile, so it is read directly at the
# edges of the measured window and differenced.

#: Step name -> RT-VLM histogram. One observation per verified clip.
RT_VLM_HISTOGRAMS = {
    "download clip (RT-VLM)": "asset_download_duration_seconds",
    "decode clip (RT-VLM)": "decode_latency_seconds",
    "vlm inference (RT-VLM)": "vlm_latency_seconds",
    "clip total (RT-VLM)": "chunk_latency_seconds",
}

_SAMPLE = re.compile(r'^([A-Za-z_:][\w:]*)(?:\{(.*)\})?\s+(\S+)')
_LE = re.compile(r'le="([^"]+)"')


def scrape_rt_vlm_histograms(url: str, timeout_sec: float = 3.0) -> dict[str, Any] | None:
    """Cumulative ``sum``, ``count`` and ``buckets`` per histogram; None if unreadable.

    Series that differ only in labels other than ``le`` are added together.
    """
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=timeout_sec)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("RT-VLM metrics at %s unreadable: %s", url, e)
        return None
    wanted = set(RT_VLM_HISTOGRAMS.values())
    out: dict[str, Any] = {
        name: {"sum": 0.0, "count": 0.0, "buckets": {}} for name in wanted
    }
    out["asset_download_bytes_total"] = 0.0
    for line in resp.text.splitlines():
        match = _SAMPLE.match(line)
        if match is None:
            continue
        name, labels, raw = match.groups()
        try:
            value = float(raw)
        except ValueError:
            continue
        if name == "asset_download_bytes_total":
            out[name] += value
            continue
        for suffix in ("_sum", "_count", "_bucket"):
            base = name[: -len(suffix)]
            if name.endswith(suffix) and base in wanted:
                if suffix == "_bucket":
                    le = _LE.search(labels or "")
                    if le:
                        buckets = out[base]["buckets"]
                        buckets[le.group(1)] = buckets.get(le.group(1), 0.0) + value
                else:
                    out[base][suffix[1:]] += value
    return out


def _bucket_quantile(buckets: list[tuple[float, float]], q: float) -> float | None:
    """``histogram_quantile`` over cumulative ``(upper bound, count)`` pairs."""
    if not buckets or buckets[-1][1] <= 0:
        return None
    rank = q * buckets[-1][1]
    lower, below = 0.0, 0.0
    for upper, cumulative in buckets:
        if cumulative >= rank:
            if upper == float("inf"):
                return lower
            inside = cumulative - below
            return lower + (upper - lower) * ((rank - below) / inside if inside else 0.0)
        lower, below = upper, cumulative
    return lower


def rt_vlm_window_stats(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Per step: clips observed in the window, and seconds per clip.

    Server-wide: it counts every client of RT-VLM in the window, which on a
    dedicated deployment is this sweep alone.
    """
    if not before or not after:
        return {}
    stats: dict[str, dict[str, Any]] = {}
    for step, name in RT_VLM_HISTOGRAMS.items():
        b, a = before.get(name) or {}, after.get(name) or {}
        count = a.get("count", 0.0) - b.get("count", 0.0)
        if count <= 0:
            continue
        total = a.get("sum", 0.0) - b.get("sum", 0.0)
        bounds = sorted(
            (float(le), a["buckets"][le] - b.get("buckets", {}).get(le, 0.0))
            for le in a.get("buckets", {})
        )
        stats[step] = {
            "clips": count,
            "sum": total,
            "per_clip_mean": total / count,
            "per_clip_p50": _bucket_quantile(bounds, 0.50),
            "per_clip_p95": _bucket_quantile(bounds, 0.95),
        }
    download = stats.get("download clip (RT-VLM)")
    if download:
        moved = after.get("asset_download_bytes_total", 0.0) - before.get(
            "asset_download_bytes_total", 0.0)
        download["bytes_per_clip"] = moved / download["clips"]
    return stats


# ---------------------------------------------------------------------------
# The load generator itself
# ---------------------------------------------------------------------------
#
# Every measured request is a CLI process on this host, outside any pod, so
# cAdvisor never sees it. Two readings cover it: the CPU the CLI processes
# actually used (getrusage over reaped children -- exact, and nothing else
# this script spawns during the window), and the whole host's CPU and memory
# from /proc, sampled across the window.

#: Service name the load generator's rows carry in ``resources_summary.csv``.
LOAD_GENERATOR = "load-generator (vss CLI)"
HOST = "host (whole node)"


def _proc_cpu() -> tuple[float, float] | None:
    """(busy, total) jiffies since boot, from /proc/stat."""
    try:
        with open("/proc/stat", encoding="ascii") as f:
            fields = [float(x) for x in f.readline().split()[1:]]
    except (OSError, ValueError):
        return None
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0.0)
    return sum(fields) - idle, sum(fields)


def _proc_mem_used() -> float | None:
    """Bytes in use (MemTotal - MemAvailable), from /proc/meminfo."""
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            info = {line.split(":")[0]: float(line.split()[1]) * 1024 for line in f}
        return info["MemTotal"] - info["MemAvailable"]
    except (OSError, ValueError, KeyError, IndexError):
        return None


class HostSampler:
    """Samples this host between :meth:`start` and :meth:`stop`."""

    def __init__(self, interval_sec: float = 2.0) -> None:
        import threading

        self._interval = interval_sec
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._cpu: list[float] = []
        self._mem: list[float] = []
        self._started = 0.0
        self._children_cpu = 0.0
        self._cores = float(os.cpu_count() or 1)

    @staticmethod
    def _children_cpu_sec() -> float:
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        return usage.ru_utime + usage.ru_stime

    def _loop(self) -> None:
        last = _proc_cpu()
        while not self._stop.wait(self._interval):
            now = _proc_cpu()
            if last and now and now[1] > last[1]:
                self._cpu.append((now[0] - last[0]) / (now[1] - last[1]))
            last = now
            mem = _proc_mem_used()
            if mem is not None:
                self._mem.append(mem)

    def start(self) -> None:
        self._started = time.perf_counter()
        self._children_cpu = self._children_cpu_sec()
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        """Figures over the sampled window; {} if never started."""
        if not self._started:
            return {}
        elapsed = time.perf_counter() - self._started
        cli_cpu = self._children_cpu_sec() - self._children_cpu
        self._stop.set()
        self._thread.join(timeout=self._interval + 1)
        out: dict[str, Any] = {
            "window_sec": elapsed,
            "cli_cpu_cores": cli_cpu / elapsed if elapsed > 0 else None,
            # Largest single CLI process this script has ever reaped, not only
            # this window's: getrusage keeps a high-water mark.
            "cli_peak_rss_bytes": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024,
            "host_cores": self._cores,
        }
        if self._cpu:
            out["host_cpu_cores_avg"] = sum(self._cpu) / len(self._cpu) * self._cores
            out["host_cpu_cores_peak"] = max(self._cpu) * self._cores
        if self._mem:
            out["host_memory_used_avg"] = sum(self._mem) / len(self._mem)
            out["host_memory_used_peak"] = max(self._mem)
        return out


def host_resource_rows(host: dict[str, Any]) -> list[dict[str, Any]]:
    """``resources_summary.csv`` rows for the load generator and its host.

    The CLI's CPU is filed under the same metric as the pods' window-mean CPU,
    so the per-service CPU plot shows the load generator beside the services
    it is loading.
    """
    if not host:
        return []
    rows = []
    if host.get("cli_cpu_cores") is not None:
        rows.append({"metric": "cpu_core_seconds_total:mean_rate", "service": LOAD_GENERATOR,
                     "series": LOAD_GENERATOR, "avg": host["cli_cpu_cores"], "peak": ""})
        rows.append({"metric": "cli_peak_rss_bytes", "service": LOAD_GENERATOR,
                     "series": LOAD_GENERATOR, "avg": host["cli_peak_rss_bytes"], "peak": ""})
    if "host_cpu_cores_avg" in host:
        rows.append({"metric": "host_cpu_cores", "service": HOST, "series": HOST,
                     "avg": host["host_cpu_cores_avg"], "peak": host["host_cpu_cores_peak"]})
    if "host_memory_used_avg" in host:
        rows.append({"metric": "host_memory_used_bytes", "service": HOST, "series": HOST,
                     "avg": host["host_memory_used_avg"], "peak": host["host_memory_used_peak"]})
    return rows
