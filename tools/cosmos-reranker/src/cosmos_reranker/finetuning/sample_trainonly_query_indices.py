#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sample a split-safe PAS training selection without validation stratification.

The optional exclusion file is used solely as a caption denylist so a repeated
caption cannot appear in both training and the held-out validation split.  No
validation labels, query types, datasets, ranks, scores, or metrics are read.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path

try:
    from cosmos_reranker.finetuning.prepare_annotations import as_pair, iter_json_records
except ModuleNotFoundError:
    from prepare_annotations import as_pair, iter_json_records


QUERY_TYPES = ("easy", "medium", "hard")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--per-type",
        type=int,
        help="Reservoir-sample exactly this many eligible rows per query type.",
    )
    selection.add_argument(
        "--all-eligible",
        action="store_true",
        help=(
            "Select every eligible row after the index/caption exclusions. "
            "Output remains in pairs-file row order and is deterministic."
        ),
    )
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument(
        "--include-indices-file",
        type=Path,
        help=(
            "Optional JSON list restricting eligibility to these zero-based "
            "pair-row indices, for example rows with precomputed embeddings."
        ),
    )
    parser.add_argument(
        "--exclude-indices-file",
        type=Path,
        help=(
            "Optional JSON list of zero-based pair-row indices to exclude. "
            "This supports reproducible, query-disjoint train-only diagnostic "
            "samples without consulting validation labels or metrics."
        ),
    )
    parser.add_argument(
        "--exclude-caption-file",
        type=Path,
        help="Optional JSON query list; only its nonempty caption strings are read.",
    )
    args = parser.parse_args()
    if args.per_type is not None and args.per_type <= 0:
        parser.error("--per-type must be positive")

    included_indices: set[int] | None = None
    if args.include_indices_file is not None:
        raw_indices = json.loads(args.include_indices_file.read_text(encoding="utf-8"))
        if not isinstance(raw_indices, list):
            raise ValueError("--include-indices-file must contain a JSON list")
        included_indices = {int(index) for index in raw_indices}
        if len(included_indices) != len(raw_indices):
            raise ValueError("--include-indices-file contains duplicate indices")
        if any(index < 0 for index in included_indices):
            raise ValueError("--include-indices-file contains a negative index")

    excluded_indices: set[int] = set()
    if args.exclude_indices_file is not None:
        raw_indices = json.loads(args.exclude_indices_file.read_text(encoding="utf-8"))
        if not isinstance(raw_indices, list):
            raise ValueError("--exclude-indices-file must contain a JSON list")
        excluded_indices = {int(index) for index in raw_indices}
        if len(excluded_indices) != len(raw_indices):
            raise ValueError("--exclude-indices-file contains duplicate indices")
        if any(index < 0 for index in excluded_indices):
            raise ValueError("--exclude-indices-file contains a negative index")

    excluded_captions: set[str] = set()
    if args.exclude_caption_file is not None:
        for row in json.loads(args.exclude_caption_file.read_text(encoding="utf-8")):
            caption = str(row.get("caption", "")).strip()
            if caption:
                excluded_captions.add(caption)

    rng = random.Random(args.seed)
    reservoirs: dict[str, list[int]] = {query_type: [] for query_type in QUERY_TYPES}
    eligible = Counter()
    excluded = Counter()
    scanned = 0
    for index, raw in enumerate(iter_json_records(args.pairs_file)):
        scanned += 1
        if included_indices is not None and index not in included_indices:
            continue
        if index in excluded_indices:
            continue
        pair = as_pair(raw)
        query_type = pair.query_type
        if query_type not in reservoirs or not pair.caption:
            continue
        if pair.caption.strip() in excluded_captions:
            excluded[query_type] += 1
            continue
        eligible[query_type] += 1
        reservoir = reservoirs[query_type]
        if args.all_eligible:
            reservoir.append(index)
        else:
            assert args.per_type is not None
            if len(reservoir) < args.per_type:
                reservoir.append(index)
            else:
                replacement = rng.randrange(eligible[query_type])
                if replacement < args.per_type:
                    reservoir[replacement] = index

    if args.per_type is not None:
        shortages = {
            query_type: eligible[query_type]
            for query_type in QUERY_TYPES
            if len(reservoirs[query_type]) != args.per_type
        }
        if shortages:
            raise ValueError(f"insufficient eligible train queries: {shortages}")
    indices = sorted(index for reservoir in reservoirs.values() for index in reservoir)
    expected = sum(eligible.values()) if args.all_eligible else 3 * int(args.per_type)
    if len(indices) != expected or len(indices) != len(set(indices)):
        raise AssertionError("sample size or uniqueness invariant failed")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(indices, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "pairs_file": str(args.pairs_file.resolve()),
        "selection": (
            "train_only_all_eligible"
            if args.all_eligible
            else "train_only_equal_query_type_reservoir"
        ),
        "seed": args.seed,
        "per_type": args.per_type,
        "all_eligible": args.all_eligible,
        "total_queries": len(indices),
        "scanned_rows": scanned,
        "eligible_train_queries": dict(eligible),
        "excluded_duplicate_caption_queries": dict(excluded),
        "excluded_query_indices": len(excluded_indices),
        "included_query_indices": (len(included_indices) if included_indices is not None else None),
        "query_index_allowlist_source": (
            str(args.include_indices_file.resolve())
            if args.include_indices_file is not None
            else None
        ),
        "query_index_denylist_source": (
            str(args.exclude_indices_file.resolve())
            if args.exclude_indices_file is not None
            else None
        ),
        "caption_denylist_source": (
            str(args.exclude_caption_file.resolve())
            if args.exclude_caption_file is not None
            else None
        ),
        "validation_labels_or_metrics_read": False,
        "output": str(args.output.resolve()),
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
