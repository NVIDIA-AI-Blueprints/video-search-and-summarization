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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

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

#: Base upload timestamp every existing segment dataset's ground truth is
#: offset from. Clip datasets ignore the anchor (scoring is by video name).
DEFAULT_UPLOAD_TIMESTAMP = "2025-01-01T00:00:00"

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
    # A clip-level retrieval release (DSS dataset ``physicalAI-event-videos-test``):
    # 393 short event clips, 6,040 queries (3,040 event + 3,000 pas), whole-clip
    # relevance (no time bounds). The on-disk ``dataset.json`` in benchmark
    # shape is produced from the raw DSS layout (clips/ + gt/queries_gt.json +
    # manifest.json) by :func:`flows.preprocess.make_clip_dataset`, run once
    # after download. ``event``/``pas`` subsets select a single query_domain.
    "physicalAI-event-videos-test": {
        "": "physicalAI-event-videos-test/dataset.json",
        "event": "physicalAI-event-videos-test/dataset_event.json",
        "pas": "physicalAI-event-videos-test/dataset_pas.json",
    },
    "kpi-search-v3": {
        "": "kpi-search-v3/dataset.json",
        "easy": "kpi-search-v3/dataset_easy.json",
        "medium": "kpi-search-v3/dataset_medium.json",
        "hard": "kpi-search-v3/dataset_hard.json",
        "anomaly_ce1_style": "kpi-search-v3/dataset_anomaly_ce1_style.json",
    },
}


# ---------------------------------------------------------------------------
# Per-dataset metadata that the subset map above cannot carry without breaking
# its shape parity with run_eval.py's registry (which the drift test asserts
# equals a ``dict[str, dict[str, str]]``). DSS source, retrieval task, the
# reported HIT@k set, and the ingest anchor all live here.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DatasetMeta:
    """Per-dataset wiring that is not the subset map."""

    #: DSS dataset to download from. Umbrella datasets (``vss-devx-search``)
    #: hold several eval fixtures and filter to ``<dataset>/``; standalone DSS
    #: datasets are downloaded whole.
    dss: str = DSS_DATASET_NAME
    #: ``segment`` = time-bounded retrieval within long videos (overlap score);
    #: ``clip`` = whole-clip retrieval (clip-match score).
    task: Literal["segment", "clip"] = "segment"
    #: Filter DSS files by ``f"{dataset}/"`` (umbrella) or not (standalone).
    prefix_filter: bool = True
    #: k values reported as HIT@k. Segment keeps the historical [1, 3, 5, 10];
    #: clip has no segment expansion so k > --top-k is unmeasurable and the set
    #: is capped at the retrieval depth.
    hit_ks: tuple[int, ...] = (1, 3, 5, 10)
    #: Upload timestamp the ground-truth offsets are relative to. Ignored for
    #: ``clip`` (scoring is by video name, not time).
    upload_ts: str = DEFAULT_UPLOAD_TIMESTAMP


DATASET_META: dict[str, DatasetMeta] = {
    "physicalAI-event-videos-test": DatasetMeta(
        dss="physicalAI-event-videos-test",
        task="clip",
        prefix_filter=False,
        hit_ks=(1, 5, 10),
    ),
}


def dataset_meta(dataset: str) -> DatasetMeta:
    """Per-dataset wiring, defaulting to a segment umbrella dataset."""
    return DATASET_META.get(dataset, DatasetMeta())


def download_from_dss(data_dir: Path, dataset: str | None = None) -> None:
    """Download eval data from DSS (nvdataset) to local directory.

    The DSS source is per-dataset metadata (:func:`dataset_meta`): umbrella
    datasets (``vss-devx-search``) filter to ``<dataset>/``; standalone DSS
    datasets download whole. ``clip``-task datasets are then materialized into
    the benchmark's on-disk ``dataset.json`` shape by the preprocessing
    adapter, so the rest of the flow sees one layout regardless of task.
    """
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

    meta = dataset_meta(dataset) if dataset else DatasetMeta()
    print(f"Loading DSS dataset: {meta.dss}")
    ds = load_dataset(name=meta.dss)
    sc = ds.to_storage_client(read_only=True)

    # List all files in the dataset (File.datum.key holds the path)
    all_files = [f.datum.key for f in ds.list_files()]
    print(f"  Found {len(all_files)} files in DSS dataset")

    # Umbrella datasets hold many fixtures under <dataset>/ prefixes; standalone
    # datasets are already this dataset's files, so filtering would drop them.
    if dataset and meta.prefix_filter:
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

    if meta.task == "clip" and dataset:
        _ensure_clip_dataset(data_dir, dataset)


def _ensure_clip_dataset(data_dir: Path, dataset: str) -> None:
    """Materialize a ``clip``-task DSS layout into the benchmark's on-disk format.

    Idempotent: skipped when ``dataset.json`` is already newer than the raw
    ``queries_gt.json`` it is built from, so re-runs do no work.
    """
    try:
        from .preprocess.make_clip_dataset import make_clip_dataset
    except ImportError as e:  # pragma: no cover - import guard
        print(
            f"ERROR: clip dataset adapter unavailable "
            f"(flows/preprocess/make_clip_dataset.py): {e}",
            file=sys.stderr,
        )
        sys.exit(1)
    make_clip_dataset(data_dir / dataset, dataset)


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
