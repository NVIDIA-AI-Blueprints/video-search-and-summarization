#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Merge sharded PAS three-mode evaluator outputs.

The evaluator shards query items and writes ordinary per-shard
``nvidia_pas_metrics.csv`` files. Mean-valued metrics merge exactly by weighting rows by ``num_queries``.
``First Pos`` medians merge exactly when shards include the compact rank
histograms written by the current evaluator.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from cosmos_reranker.evaluation.evaluate import (
    PAS_GROUND_TRUTH_MODES,
    PAS_QUERY_TYPES,
    _aggregate_rows,
    _format_combined_terminal_table,
    _write_combined_weighted_csv,
    _write_first_pos_histograms,
    _write_metrics_csv,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--k", type=int, required=True)
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=PAS_GROUND_TRUTH_MODES,
        default=["scalar_plus_accessories"],
    )
    parser.add_argument(
        "--query-types",
        nargs="+",
        choices=PAS_QUERY_TYPES,
        default=list(PAS_QUERY_TYPES),
    )
    parser.add_argument("shard_dirs", nargs="+", type=Path)
    return parser.parse_args()


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _row_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("Dataset", "")),
        str(row.get("QueryType", "")),
        str(row.get("EasyAttribute", "")),
    )


def _required_float(row: Mapping[str, Any], key: str) -> float:
    value = row.get(key, "")
    if value == "":
        raise ValueError(f"Missing required metric {key!r} in row {_row_key(row)!r}")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid metric {key!r}={value!r} in row {_row_key(row)!r}") from error
    if not math.isfinite(parsed):
        raise ValueError(f"Non-finite metric {key!r} in row {_row_key(row)!r}")
    return parsed


def _read_first_pos_histograms(path: Path) -> dict[tuple[str, str, str], dict[int, int]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format_version") != 1 or not isinstance(payload.get("rows"), list):
        raise ValueError(f"Unsupported first-position histogram file: {path}")

    result: dict[tuple[str, str, str], dict[int, int]] = {}
    for row in payload["rows"]:
        key = _row_key(row)
        if key in result:
            raise ValueError(f"Duplicate histogram row {key!r} in {path}")
        raw_counts = row.get("counts")
        if not isinstance(raw_counts, Mapping):
            raise ValueError(f"Missing histogram counts for {key!r} in {path}")
        counts: dict[int, int] = {}
        for raw_rank, raw_count in raw_counts.items():
            rank = int(raw_rank)
            count = int(raw_count)
            if rank <= 0 or count <= 0:
                raise ValueError(
                    f"Invalid first-position histogram entry {raw_rank!r}: {raw_count!r}"
                )
            counts[rank] = counts.get(rank, 0) + count
        result[key] = counts
    return result


def _median_from_histogram(counts: Mapping[int, int]) -> float:
    total = sum(int(count) for count in counts.values())
    if total <= 0:
        return float("nan")
    targets = ((total - 1) // 2, total // 2)
    values: list[int] = []
    cumulative = 0
    for rank in sorted(counts):
        cumulative += int(counts[rank])
        while len(values) < 2 and targets[len(values)] < cumulative:
            values.append(int(rank))
    if len(values) != 2:
        raise ValueError("First-position histogram does not contain its declared observations")
    return float(sum(values) / 2.0)


def _merge_metric_rows(
    rows: Sequence[Mapping[str, Any]],
    k: int,
    first_pos_histograms: Mapping[tuple[str, str, str], Mapping[int, int]] | None = None,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        groups.setdefault(_row_key(row), []).append(row)

    metric_keys = [
        "avg_gt_per_query",
        "mAP",
        "Rank-1",
        "Rank-5",
        "Separability",
        f"Match@{k}",
        f"Zero@{k}",
    ]
    merged: list[dict[str, Any]] = []
    for (dataset, query_type, easy_attribute), selected in sorted(groups.items()):
        query_counts = [_required_float(row, "num_queries") for row in selected]
        if any(not count.is_integer() or count <= 0 for count in query_counts):
            raise ValueError(f"Invalid num_queries for {(dataset, query_type, easy_attribute)!r}")
        total_queries = int(sum(query_counts))
        gallery_sizes = {_required_float(row, "gallery_size") for row in selected}
        if len(gallery_sizes) != 1:
            raise ValueError(
                f"Shard gallery sizes differ for {(dataset, query_type, easy_attribute)!r}: "
                f"{sorted(gallery_sizes)}"
            )

        key = (dataset, query_type, easy_attribute)
        histogram = dict(first_pos_histograms.get(key, {})) if first_pos_histograms else {}
        if histogram and sum(histogram.values()) != total_queries:
            raise ValueError(
                f"First-position histogram has {sum(histogram.values())} observations for "
                f"{key!r}, expected {total_queries}"
            )
        out: dict[str, Any] = {
            "Dataset": dataset,
            "QueryType": query_type,
            "EasyAttribute": easy_attribute,
            "num_queries": total_queries,
            "gallery_size": int(next(iter(gallery_sizes))),
            "First Pos": _median_from_histogram(histogram) if histogram else float("nan"),
            "_first_pos_histogram": {str(rank): count for rank, count in sorted(histogram.items())},
        }
        for metric_key in metric_keys:
            weighted_sum = sum(
                _required_float(row, metric_key) * query_count
                for row, query_count in zip(selected, query_counts)
            )
            out[metric_key] = weighted_sum / total_queries
        merged.append(out)

    if first_pos_histograms and set(first_pos_histograms) != set(groups):
        raise ValueError(
            "First-position histogram groups do not match metric groups: "
            f"{sorted(set(first_pos_histograms) ^ set(groups))}"
        )
    return merged


def _dataset_names(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    names = sorted(
        {
            str(row.get("Dataset"))
            for row in rows
            if row.get("Dataset") and not str(row.get("Dataset")).startswith(("AVG_", "WAVG_"))
        }
    )
    names.sort(key=lambda name: 0 if name == "RSTPReid" else 1)
    return names


def _merge_query_rankings(
    output_path: Path,
    shard_paths: Sequence[Path],
) -> int:
    """Concatenate complete per-shard JSONL traces into one inspectable artifact."""

    present = [path.is_file() for path in shard_paths]
    if not any(present):
        return 0
    if not all(present):
        missing = [str(path) for path, exists in zip(shard_paths, present) if not exists]
        raise FileNotFoundError(
            "Only some shards contain per-query ranking logs; missing: " + ", ".join(missing)
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_path.open("w", encoding="utf-8") as destination:
        for shard_path in shard_paths:
            with shard_path.open(encoding="utf-8") as source:
                for line_number, line in enumerate(source, start=1):
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError as error:
                        raise ValueError(
                            f"Invalid query ranking JSON at {shard_path}:{line_number}"
                        ) from error
                    destination.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
                    destination.write("\n")
                    count += 1
    logging.info("Wrote %s merged per-query rankings to %s", f"{count:,}", output_path)
    return count


def _validate_shard_metadata(args: argparse.Namespace) -> list[dict[str, Any]]:
    resolved_dirs = [path.expanduser().resolve() for path in args.shard_dirs]
    if len(set(resolved_dirs)) != len(resolved_dirs):
        raise ValueError("Shard directories must be unique")

    metadata = []
    for shard_dir in resolved_dirs:
        path = shard_dir / "run_metadata.json"
        if not path.is_file():
            raise FileNotFoundError(f"Missing shard metadata: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        metadata.append(payload)

    common_fields = (
        "pairs_file",
        "query_subset_file",
        "image_root",
        "image_embeddings",
        "text_embeddings",
        "reranker",
        "rerank_depth",
        "model_id",
        "lora_path",
        "output_format",
        "reasoning",
        "image_min_pixels",
        "image_max_pixels",
        "dataset_names",
        "query_types",
        "modes",
        "k",
        "shard_count",
    )
    baseline = metadata[0]
    for field in common_fields:
        values = [payload.get(field) for payload in metadata]
        if any(value != values[0] for value in values[1:]):
            raise ValueError(f"Shard metadata disagree on {field!r}: {values!r}")

    shard_count = int(baseline.get("shard_count", 0))
    indices = sorted(int(payload.get("shard_index", -1)) for payload in metadata)
    if shard_count != len(resolved_dirs) or indices != list(range(shard_count)):
        raise ValueError(f"Expected exactly shard indices 0..{shard_count - 1}, got {indices}")
    if int(baseline.get("k", -1)) != args.k:
        raise ValueError(f"--k={args.k} does not match shard metadata k={baseline.get('k')}")
    if list(baseline.get("modes", [])) != list(args.modes):
        raise ValueError("--modes do not match shard metadata")
    if list(baseline.get("query_types", [])) != list(args.query_types):
        raise ValueError("--query-types do not match shard metadata")

    args.shard_dirs = resolved_dirs
    return metadata


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    _validate_shard_metadata(args)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    weighted_rows_by_mode: dict[str, list[Mapping]] = {}
    query_ranking_counts: dict[str, int] = {}
    used_exact_first_pos = True
    for mode in args.modes:
        shard_rows: list[dict[str, str]] = []
        histogram_paths = [
            shard_dir / mode / "first_pos_histograms.json" for shard_dir in args.shard_dirs
        ]
        present_histograms = [path.is_file() for path in histogram_paths]
        if any(present_histograms) and not all(present_histograms):
            raise FileNotFoundError(
                f"Only some shards contain first-position histograms for {mode}"
            )

        merged_histograms: dict[tuple[str, str, str], dict[int, int]] = {}
        for shard_dir, histogram_path in zip(args.shard_dirs, histogram_paths):
            metrics_path = shard_dir / mode / "nvidia_pas_metrics.csv"
            if not metrics_path.is_file():
                raise FileNotFoundError(f"Missing shard metrics: {metrics_path}")
            current_rows = _read_csv_rows(metrics_path)
            current_keys = [_row_key(row) for row in current_rows]
            if len(current_keys) != len(set(current_keys)):
                raise ValueError(f"Duplicate metric rows in {metrics_path}")
            shard_rows.extend(current_rows)

            if all(present_histograms):
                for key, counts in _read_first_pos_histograms(histogram_path).items():
                    destination = merged_histograms.setdefault(key, {})
                    for rank, count in counts.items():
                        destination[rank] = destination.get(rank, 0) + count

        if not all(present_histograms):
            used_exact_first_pos = False
            logging.warning(
                "Shard outputs for %s predate exact First Pos histograms; merged First Pos will be blank",
                mode,
            )
        rows = _merge_metric_rows(
            shard_rows,
            args.k,
            merged_histograms if all(present_histograms) else None,
        )
        dataset_names = _dataset_names(rows)
        aggregate_rows = _aggregate_rows(
            rows,
            args.query_types,
            args.k,
            dataset_names,
            weighted=False,
        )
        weighted_rows = _aggregate_rows(
            rows,
            args.query_types,
            args.k,
            dataset_names,
            weighted=True,
        )
        mode_dir = args.output_dir / mode
        _write_metrics_csv(mode_dir / "nvidia_pas_metrics.csv", rows, args.k)
        if all(present_histograms):
            _write_first_pos_histograms(mode_dir / "first_pos_histograms.json", rows)
        _write_metrics_csv(mode_dir / "nvidia_pas_metrics_aggregate.csv", aggregate_rows, args.k)
        _write_metrics_csv(
            mode_dir / "nvidia_pas_metrics_weighted_aggregate.csv",
            weighted_rows,
            args.k,
        )
        weighted_rows_by_mode[mode] = weighted_rows
        query_ranking_counts[mode] = _merge_query_rankings(
            mode_dir / "query_rankings.jsonl",
            [shard_dir / mode / "query_rankings.jsonl" for shard_dir in args.shard_dirs],
        )

    combined_rows = _write_combined_weighted_csv(
        args.output_dir / f"{args.run_name}_three_modes_weighted.csv",
        args.run_name,
        weighted_rows_by_mode,
    )
    metadata = {
        "run": args.run_name,
        "k": args.k,
        "modes": list(args.modes),
        "query_types": list(args.query_types),
        "shard_dirs": [str(path) for path in args.shard_dirs],
        "query_ranking_counts": query_ranking_counts,
        "first_pos_note": (
            "Merged exactly from per-shard first-position histograms."
            if used_exact_first_pos
            else "Unavailable because these shard outputs predate first-position histograms."
        ),
    }
    (args.output_dir / "merged_shards_metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )
    if combined_rows:
        logging.info("Results summary:\n%s", _format_combined_terminal_table(combined_rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
