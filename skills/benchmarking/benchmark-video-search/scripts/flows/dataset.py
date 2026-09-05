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
import statistics
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

#: Where DSS downloads land unless --data-dir says otherwise.
DEFAULT_DATA_DIR = Path("/tmp/vss-devx-search")

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
    # Built locally by flows_preprocessing/make_physicalai_devset.py, not on DSS
    # -- run with --skip-download. One file per retrieval path, so a slice is
    # chosen by name rather than by flags and two runs share byte-identical
    # input. `fusion` is absent from the default file on purpose: it carries the
    # same query strings as `embed`, and this mapping is keyed by query text, so
    # merging them would silently drop 501 entries.
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


def vst_url_for(endpoint: str, vst_port: int = 30888) -> str:
    """Derive VST URL from the agent endpoint (same host, VST port)."""
    parsed = urlparse(endpoint)
    return f"{parsed.scheme}://{parsed.hostname}:{vst_port}"
