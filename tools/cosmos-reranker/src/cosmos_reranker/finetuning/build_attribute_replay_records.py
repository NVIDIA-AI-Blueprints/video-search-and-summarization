#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Recreate the all-train PAS attribute replay records used by the K20 run."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from cosmos_reranker.finetuning.prepare_annotations import iter_json_records


def collect(path: Path, excluded: set[str] | None = None) -> tuple[list[dict], int]:
    seen: set[str] = set()
    records: list[dict] = []
    overlap = 0
    for row in iter_json_records(path):
        key = f"{row.get('dataset', '')}\t{row['image_path']}"
        if key in seen:
            continue
        seen.add(key)
        if excluded is not None and key in excluded:
            overlap += 1
            continue
        records.append(
            {
                "dataset": row.get("dataset", ""),
                "image_path": row["image_path"],
                "unique_name": row["unique_name"],
                "image_attr_values": [int(value) for value in row["image_attr_values"]],
                "embedding_index": len(records),
            }
        )
    return records, overlap


def counts(records: list[dict]) -> list[dict[str, int]]:
    width = len(records[0]["image_attr_values"])
    return [
        dict(sorted(Counter(str(row["image_attr_values"][i]) for row in records).items()))
        for i in range(width)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-pairs", type=Path, required=True)
    parser.add_argument("--val-pairs", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    val, _ = collect(args.val_pairs)
    val_keys = {f"{row['dataset']}\t{row['image_path']}" for row in val}
    train, overlap = collect(args.train_pairs, excluded=val_keys)
    train_keys = {f"{row['dataset']}\t{row['image_path']}" for row in train}
    if len(train_keys) != len(train) or train_keys & val_keys:
        raise AssertionError("train/validation image identities overlap")

    for name, records in (("train", train), ("val", val)):
        path = args.output_dir / f"{name}_records.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(records, separators=(",", ":")) + "\n")
        temporary.replace(path)

    manifest = {
        "train_pairs": str(args.train_pairs.resolve()),
        "val_pairs": str(args.val_pairs.resolve()),
        "selection": "train_only_unique_image_reservoir",
        "seed": 160826,
        "train_images": len(train),
        "val_images": len(val),
        "unique_train_images": len(train),
        "eligible_train_images": len(train),
        "excluded_train_val_image_overlap": overlap,
        "train_label_counts": counts(train),
        "val_label_counts": counts(val),
        "validation_labels_used_for_training_selection": False,
        "train_records": str((args.output_dir / "train_records.json").resolve()),
        "val_records": str((args.output_dir / "val_records.json").resolve()),
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"train images: {len(train)}; validation images: {len(val)}; overlap excluded: {overlap}")


if __name__ == "__main__":
    main()
