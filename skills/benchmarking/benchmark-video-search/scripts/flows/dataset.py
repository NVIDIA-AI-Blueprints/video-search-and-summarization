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

"""Dataset discovery, DSS download and ingest-stat aggregation.

Vendored from ``run_eval.py`` so this flow owns them and that script can be
deleted independently. Behaviour is unchanged: the registry, the on-disk layout
and the aggregate shapes all have to keep matching what existing datasets and
result readers expect.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .ingest_readiness import raw_coverage

#: Where DSS downloads land unless --data-dir says otherwise.
#:
#: Deliberately NOT /tmp, which run_eval.py used and which is world-writable.
#: These evals run on shared deployment boxes, where any other user can
#: pre-create the directory and plant dataset files -- and this path holds the
#: ground truth every metric is scored against, so poisoning it is not a
#: hypothetical inconvenience but a way to make the eval report whatever the
#: attacker chose. Under the user's own cache dir the OS enforces ownership.
DEFAULT_DATA_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "vss-devx-search"

#: The DSS dataset holding every eval fixture.
DSS_DATASET_NAME = "vss-devx-search"

# ---------------------------------------------------------------------------
# Dataset registry -- maps (dataset, subset) to a path under --data-dir.
#
# Convention: <dataset>/<subset file>.json alongside <dataset>/videos/.
# All subsets of a dataset share ONE videos/ directory; only the JSON differs.
# ---------------------------------------------------------------------------

DATASETS: dict[str, dict[str, str]] = {
    "warehouse": {
        "": "warehouse/dataset.json",
    },
    "vad-r1": {
        "": "vad-r1/dataset.json",
        "easy": "vad-r1/dataset_easy.json",
        "medium": "vad-r1/dataset_medium.json",
        "hard": "vad-r1/dataset_hard.json",
    },
    "vad-r1-v2": {
        "": "vad-r1-v2/dataset.json",
        "easy": "vad-r1-v2/dataset_easy.json",
        "medium": "vad-r1-v2/dataset_medium.json",
        "hard": "vad-r1-v2/dataset_hard.json",
        "benchmark": "vad-r1-v2/dataset_benchmark.json",
    },
    # Built by flows_preprocessing/make_physicalai_devset.py (seed 0, source
    # balanced) and published to DSS, so it downloads like any other dataset.
    # One file per retrieval path, so a slice is chosen by name rather than by
    # flags and two runs share byte-identical input. `fusion` is absent from the
    # default file on purpose: its 195 queries repeat query strings already in
    # `embed`, and this mapping is keyed by query text, so merging them would
    # silently overwrite those 195 entries.
    "physicalai-dev": {
        "": "physicalai-dev/dataset.json",
        "embed": "physicalai-dev/dataset_embed.json",
        "attribute": "physicalai-dev/dataset_attribute.json",
        "fusion": "physicalai-dev/dataset_fusion.json",
    },
    "kpi-search-v3": {
        "": "kpi-search-v3/dataset.json",
        "easy": "kpi-search-v3/dataset_easy.json",
        "medium": "kpi-search-v3/dataset_medium.json",
        "hard": "kpi-search-v3/dataset_hard.json",
        "anomaly_ce1_style": "kpi-search-v3/dataset_anomaly_ce1_style.json",
    },
}


def download_from_dss(data_dir: Path, dataset: str | None = None) -> None:
    """Download eval data from DSS (nvdataset) to local directory."""
    try:
        from nvdataset import load_dataset
    except ImportError:
        print(
            "ERROR: nvdataset is not installed. Install it with:\n"
            "  pip install --extra-index-url "
            "https://artifactory.pdx.nvidia.com/artifactory/api/pypi/"
            "sw-ngc-data-platform-pypi-local/simple nvdataset\n"
            "Or run with --skip-download if data is already local.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Loading DSS dataset: {DSS_DATASET_NAME}")
    ds = load_dataset(name=DSS_DATASET_NAME)
    sc = ds.to_storage_client(read_only=True)

    # List all files in the dataset (File.datum.key holds the path)
    all_files = [f.datum.key for f in ds.list_files()]
    print(f"  Found {len(all_files)} files in DSS dataset")

    # Filter to requested dataset if specified
    if dataset:
        prefix = f"{dataset}/"
        all_files = [f for f in all_files if f.startswith(prefix)]
        print(f"  Filtered to {len(all_files)} files for dataset '{dataset}'")

    if not all_files:
        print("ERROR: No files found in DSS dataset.", file=sys.stderr)
        sys.exit(1)

    data_dir.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    skipped = 0
    for remote_path in all_files:
        local_path = data_dir / remote_path
        if local_path.exists():
            skipped += 1
            continue
        local_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"  Downloading: {remote_path}")
        sc.download_file(remote_path, str(local_path))
        downloaded += 1

    print(f"  Download complete: {downloaded} new, {skipped} already present")


def load_dataset_file(data_dir: Path, dataset: str, subset: str) -> dict:
    """Load dataset JSON (queries + annotations) from local data dir."""
    rel_path = DATASETS.get(dataset, {}).get(subset)
    if rel_path is None:
        available_subsets = list(DATASETS.get(dataset, {}).keys())
        print(
            f"ERROR: Unknown dataset/subset: {dataset}/{subset or '(default)'}. Available subsets: {available_subsets}",
            file=sys.stderr,
        )
        sys.exit(1)

    fpath = data_dir / rel_path
    if not fpath.exists():
        print(f"ERROR: Dataset file not found: {fpath}", file=sys.stderr)
        print("Run without --skip-download to fetch from DSS.", file=sys.stderr)
        sys.exit(1)

    with open(fpath) as f:
        data = json.load(f)

    return data


def aggregate_upload_stats(per_file: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-file upload results into latency/throughput stats.

    Mirrors the shape produced by eval/data/upload_latency.py so JSON consumers
    can use the same keys regardless of which entrypoint produced them.
    """
    successful = [r for r in per_file if r.get("success")]
    failed = [r for r in per_file if not r.get("success")]

    stats: dict[str, Any] = {
        "total_uploads": len(per_file),
        "successful": len(successful),
        "failed": len(failed),
        "success_rate": round(len(successful) / len(per_file), 4) if per_file else 0.0,
        "per_file": per_file,
    }

    if successful:
        latencies_s = sorted(r["upload_latency_s"] for r in successful)
        total_size_mb = sum(r.get("file_size_mb") or 0.0 for r in successful)
        total_chunks = sum(r.get("chunks_processed") or 0 for r in successful)
        n = len(latencies_s)

        # Match search-latency percentile convention used elsewhere in this file
        # (sorted_lat[int(len * pct)]) so both blocks are directly comparable.
        stats["latency"] = {
            "mean_s": round(statistics.mean(latencies_s), 3),
            "median_s": round(statistics.median(latencies_s), 3),
            "min_s": round(latencies_s[0], 3),
            "max_s": round(latencies_s[-1], 3),
            "p90_s": round(latencies_s[min(int(n * 0.9), n - 1)], 3),
            "p95_s": round(latencies_s[min(int(n * 0.95), n - 1)], 3),
            "std_dev_s": round(statistics.stdev(latencies_s), 3) if n > 1 else 0.0,
            "total_s": round(sum(latencies_s), 3),
        }
        stats["throughput"] = {
            "total_size_mb": round(total_size_mb, 2),
            "avg_upload_speed_mbps": (
                round((total_size_mb * 8) / sum(latencies_s), 2) if sum(latencies_s) > 0 else None
            ),
        }
        if total_chunks > 0:
            stats["chunks"] = {
                "total_processed": total_chunks,
                "avg_per_video": round(total_chunks / len(successful), 1),
                "avg_latency_per_chunk_s": round(sum(latencies_s) / total_chunks, 3),
            }

    if failed:
        stats["errors"] = [
            {"video": r.get("video_name"), "error": r.get("error")} for r in failed
        ]

    return stats


def print_upload_summary(stats: dict[str, Any]) -> None:
    """Print upload latency/throughput block in the same style as RESULTS SUMMARY."""
    if not stats or not stats.get("total_uploads"):
        return

    print(f"\n{'=' * 60}")
    print("UPLOAD LATENCY SUMMARY")
    print(f"{'=' * 60}")
    print(f"Total Uploads:      {stats['total_uploads']}")
    print(f"Successful:         {stats['successful']}")
    print(f"Failed:             {stats['failed']}")
    print(f"Success Rate:       {stats['success_rate'] * 100:.1f}%")

    lat = stats.get("latency")
    if lat:
        print("\nUpload Latency:")
        print(f"  Mean:             {lat['mean_s']:.3f}s")
        print(f"  Median:           {lat['median_s']:.3f}s")
        print(f"  Min:              {lat['min_s']:.3f}s")
        print(f"  Max:              {lat['max_s']:.3f}s")
        print(f"  P90:              {lat['p90_s']:.3f}s")
        print(f"  P95:              {lat['p95_s']:.3f}s")
        print(f"  Std Dev:          {lat['std_dev_s']:.3f}s")
        print(f"  Total Time:       {lat['total_s']:.3f}s")

    tp = stats.get("throughput")
    if tp:
        print("\nThroughput:")
        print(f"  Total Size:       {tp['total_size_mb']:.2f} MB")
        if tp.get("avg_upload_speed_mbps") is not None:
            print(f"  Avg Speed:        {tp['avg_upload_speed_mbps']:.2f} Mbps")

    chunks = stats.get("chunks")
    if chunks:
        print("\nChunk Processing:")
        print(f"  Total Chunks:     {chunks['total_processed']}")
        print(f"  Avg per Video:    {chunks['avg_per_video']}")
        print(f"  Avg Latency/Chunk:{chunks['avg_latency_per_chunk_s']:.3f}s")

    errs = stats.get("errors")
    if errs:
        print("\nFailed Uploads:")
        for err in errs:
            print(f"  - {err['video']}: {err['error']}")
    print(f"{'=' * 60}\n")


def _mean_p90(values: list[float]) -> dict[str, float] | None:
    """Mean and P90 by the same ``sorted[int(n * pct)]`` rule as upload latency."""
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    return {"mean": round(statistics.mean(ordered), 3), "p90": round(ordered[min(int(n * 0.9), n - 1)], 3)}


def aggregate_ingest_stats(readiness: dict[str, Any]) -> dict[str, Any]:
    """Batch ingest figures from the ingest gate's per-video reports.

    Shaped to fill the docs' upload table directly. Chunks come from the
    embedding index, so they are filled on ``vst-direct`` too, where the
    upload itself reports none. Timings cover only videos uploaded by this
    run; a ``--skip-existing`` source has nothing to time.
    """
    per_video = readiness.get("per_video") or []
    uploaded = [r for r in per_video if r.get("uploaded_this_run")]
    durations = [r["duration_s"] for r in per_video if r.get("duration_s")]
    chunk_counts = [r["counts"]["embed"] for r in per_video if "embed" in (r.get("counts") or {})]

    wall_s = None
    finished = [
        (r["upload_start_mono"], r["upload_start_mono"] + r["ingest_s"])
        for r in uploaded
        if r.get("upload_start_mono") is not None and r.get("ingest_s") is not None
    ]
    if finished and len(finished) == len(uploaded):
        wall_s = round(max(end for _, end in finished) - min(start for start, _ in finished), 3)
    uploaded_duration = sum(r.get("duration_s") or 0.0 for r in uploaded)

    return {
        "outcome": readiness.get("outcome"),
        "videos": len(per_video),
        "uploaded_this_run": len(uploaded),
        "duration_range_s": [min(durations), max(durations)] if durations else None,
        "total_size_mb": round(sum(r.get("file_size_mb") or 0.0 for r in per_video), 2),
        "chunks": (
            {"total": sum(chunk_counts), "avg_per_video": round(sum(chunk_counts) / len(chunk_counts), 1)}
            if chunk_counts else None
        ),
        "embed_done_s": _mean_p90(
            [r["per_index_done_s"]["embed"] for r in uploaded
             if (r.get("per_index_done_s") or {}).get("embed") is not None]
        ),
        "ingest_s": _mean_p90([r["ingest_s"] for r in uploaded if r.get("ingest_s") is not None]),
        "wall_s": wall_s,
        "video_min_per_min": round(uploaded_duration / wall_s, 3) if wall_s else None,
        "poll_interval_s": readiness.get("poll_interval_s"),
        "quiet_s": readiness.get("quiet_s"),
        "behavior_check": readiness.get("behavior_check"),
        "per_video": [
            {k: r.get(k) for k in (
                "sensor", "video_name", "uploaded_this_run", "duration_s", "fps", "upload_s",
                "per_index_done_s", "ingest_s", "counts", "targets", "raw_last_s",
                "raw_coverage", "raw_check", "over_target", "warnings", "outcome", "causes",
            )}
            for r in per_video
        ],
    }


def print_ingest_summary(stats: dict[str, Any]) -> None:
    """Print the ingest gate's figures in the same style as the upload block."""
    if not stats or not stats.get("videos"):
        return

    def _pair(block: dict[str, float] | None) -> str:
        return f"mean {block['mean']:.1f}s  P90 {block['p90']:.1f}s" if block else "n/a"

    print(f"\n{'=' * 60}")
    print("INGESTION SUMMARY")
    print(f"{'=' * 60}")
    print(f"Outcome:            {stats['outcome']}")
    print(f"Videos:             {stats['videos']} ({stats['uploaded_this_run']} uploaded this run)")
    if stats.get("chunks"):
        print(f"Chunks:             {stats['chunks']['total']} (avg {stats['chunks']['avg_per_video']}/video)")
    print(f"Embeddings done:    {_pair(stats.get('embed_done_s'))}")
    print(f"Full ingestion:     {_pair(stats.get('ingest_s'))}")
    if stats.get("wall_s") is not None:
        print(f"Batch wall clock:   {stats['wall_s']:.1f}s  ({stats['video_min_per_min']} video-min/min)")
    print(f"Poll interval:      {stats['poll_interval_s']}s (timings late by at most this)")
    if stats.get("behavior_check"):
        print(f"Behavior check:     {stats['behavior_check']} ({stats['quiet_s']}s, heuristic)")

    def _s(value: float | None) -> str:
        return f"{value}s" if value is not None else "n/a"

    for r in stats["per_video"]:
        done = "  ".join(f"{k}={_s(v)}" for k, v in (r.get("per_index_done_s") or {}).items())
        flag = f"  OVER TARGET: {r['over_target']}" if r.get("over_target") else ""
        print(f"  {r['sensor']}: {r['outcome']}  ingest={_s(r.get('ingest_s'))}  {done}{flag}")
        if "raw" in (r.get("counts") or {}):
            frames = (r.get("targets") or {}).get("raw")
            print(
                f"    raw={raw_coverage(r['counts']['raw'], frames)}  last detection "
                f"{_s(r.get('raw_last_s'))} of {_s(r.get('duration_s'))}"
            )
        for warning in r.get("warnings") or []:
            print(f"    WARNING: {warning}")
    print(f"{'=' * 60}\n")


def ingress_url_for(endpoint: str, ingress_port: int = 7777) -> str:
    """The unified ingress on the agent's host, which routes /elasticsearch and /rtvi-cv.

    The ingest gate reads perception's output through it. Same derivation as
    :func:`vst_url_for`, for the same reason: one host, a known port.
    """
    parsed = urlparse(endpoint)
    return f"{parsed.scheme or 'http'}://{parsed.hostname}:{ingress_port}"


def vst_url_for(endpoint: str, vst_port: int = 30888) -> str:
    """Derive VST URL from the agent endpoint (same host, VST port)."""
    parsed = urlparse(endpoint)
    return f"{parsed.scheme}://{parsed.hostname}:{vst_port}"


def sidecar_decompositions_for(data_dir: Path, dataset: str) -> Path | None:
    """The dataset's own decomposition answer key, when it ships one.

    ``devset_provenance.json`` carries ``expected_decomposition`` for every
    query. It is deliberately not in the dataset files -- routing is decided at
    query time, and a stored decomposition would compete with that -- but when
    the decomposer cannot be reached it is the difference between exercising all
    four paths and measuring one.
    """
    path = data_dir / dataset / "devset_provenance.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return None
    return path if isinstance(payload.get("expected_decomposition"), dict) else None


def llm_url_for(endpoint: str, llm_port: int = 30081) -> str:
    """Derive the decomposition LLM origin from the agent endpoint.

    Same host, NIM port. The LLM is not routed through the unified origin the
    other services share, so it is derived from ``--endpoint`` rather than read
    off the HAProxy prefix map -- the same reasoning as :func:`vst_url_for`.

    Deriving it is what makes live decomposition the default: an eval that
    silently falls back to one fixed path measures a flow the product does not
    run, and that failure is invisible in the metrics.
    """
    parsed = urlparse(endpoint)
    return f"{parsed.scheme}://{parsed.hostname}:{llm_port}"
