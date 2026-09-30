#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Mine reproducible PAS-labelled retrieval pools with fine-tuned SigLIP2.

Run with ``torchrun``.  Every rank consumes its row-aligned text-embedding
shard, retrieves the top candidates from the complete gallery, labels those
candidates with PAS metadata, and emits deterministic fixed-size rotating views
per selected train query.  Retriever scores are used only for candidate ordering;
they are never training targets.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import torch
import torch.distributed as dist

try:
    from cosmos_reranker.finetuning.prepare_annotations import (
        PROMPT,
        Pair,
        as_pair,
        iter_json_records,
        metadata_match,
    )
except ModuleNotFoundError:
    from prepare_annotations import (
        PROMPT,
        Pair,
        as_pair,
        iter_json_records,
        metadata_match,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs-file", type=Path, required=True)
    parser.add_argument("--image-embeddings", type=Path, required=True)
    parser.add_argument("--text-embedding-shards", type=Path, required=True)
    parser.add_argument("--query-indices", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--topk", type=int, default=50)
    parser.add_argument("--group-size", type=int, default=20)
    parser.add_argument(
        "--deployed-topk-group",
        action="store_true",
        help=(
            "Keep the retrieved top-k unchanged, requiring both labels. "
            "This reproduces the first 750k-query K20 annotation stage."
        ),
    )
    parser.add_argument(
        "--views-per-query",
        type=int,
        default=1,
        help=(
            "Number of deterministic candidate views per query. Every view keeps "
            "the earliest positive and negative; other slots rotate across top-k."
        ),
    )
    parser.add_argument("--log-every", type=int, default=10_000)
    parser.add_argument(
        "--ground-truth",
        choices=("scalar", "scalar_plus_accessories"),
        default="scalar_plus_accessories",
    )
    args = parser.parse_args()
    if (
        min(
            args.batch_size,
            args.topk,
            args.group_size,
            args.views_per_query,
            args.log_every,
        )
        <= 0
    ):
        parser.error("batch, top-k, group, and log sizes must be positive")
    if args.group_size < 2:
        parser.error("--group-size must be at least 2")
    if args.topk < args.group_size:
        parser.error("--topk must be at least --group-size")
    if args.deployed_topk_group and args.topk != args.group_size:
        parser.error("--deployed-topk-group requires --topk == --group-size")
    return args


def image_key(row: Mapping[str, Any]) -> str:
    return f"{str(row.get('dataset') or '')}\t{str(row.get('image_path') or '')}"


def load_text_shard(
    directory: Path,
    rank: int,
    world_size: int,
) -> tuple[np.ndarray, int, int, int, Path]:
    manifest_path = directory / (f"text_embeddings.shard_{rank:05d}_of_{world_size:05d}.json")
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    shard = manifest["shard"]
    if int(shard["index"]) != rank or int(shard["count"]) != world_size:
        raise ValueError(f"text shard metadata disagrees with torchrun: {manifest_path}")
    start, end = int(shard["global_start"]), int(shard["global_end"])
    array = np.load(Path(manifest["array"]), mmap_mode="r")
    if array.shape[0] != end - start:
        raise ValueError(f"text shard shape disagrees with manifest: {array.shape}")
    return array, start, end, int(shard["global_count"]), manifest_path


def load_pairs(
    path: Path,
    local_start: int,
    local_end: int,
) -> tuple[
    dict[str, list[Pair]],
    dict[str, list[int]],
    list[tuple[int, Pair]],
    int,
    int,
]:
    """Load gallery order globally and the current rank's contiguous queries."""

    galleries: dict[str, list[Pair]] = defaultdict(list)
    gallery_image_rows: dict[str, list[int]] = defaultdict(list)
    gallery_indices: dict[str, dict[str, int]] = defaultdict(dict)
    local_queries: list[tuple[int, Pair]] = []
    global_image_row = 0
    row_count = 0
    for row_count, raw in enumerate(iter_json_records(path), start=1):
        global_row = row_count - 1
        if "idx" in raw and int(raw["idx"]) != global_row:
            raise ValueError(f"pair idx mismatch at row {global_row}: {raw['idx']}")
        pair = as_pair(raw)
        key = image_key(raw)
        indices = gallery_indices[pair.dataset]
        if key not in indices:
            indices[key] = len(galleries[pair.dataset])
            galleries[pair.dataset].append(pair)
            gallery_image_rows[pair.dataset].append(global_image_row)
            global_image_row += 1
        if local_start <= global_row < local_end:
            if not pair.caption or not pair.text_attr_values:
                raise ValueError(f"invalid query metadata at pair row {global_row}")
            local_queries.append((global_row, pair))
    if len(local_queries) != local_end - local_start:
        raise ValueError(
            f"loaded {len(local_queries)} local queries, expected {local_end - local_start}"
        )
    return (
        dict(galleries),
        dict(gallery_image_rows),
        local_queries,
        global_image_row,
        row_count,
    )


def build_rotating_deployed_topk_views(
    candidate_indices: list[int],
    labels: list[int],
    *,
    group_size: int,
    views_per_query: int = 1,
    rotation_offset: int = 0,
) -> list[list[tuple[int, int, int]]] | None:
    """Build deterministic fixed-size views spanning a labelled retrieval pool.

    Each tuple is ``(candidate_index, retriever_rank, label)``.  The earliest
    positive and negative anchor every view.  Remaining slots sample evenly
    through the full retrieval depth, with a deterministic query-row phase.
    ``views_per_query`` remains an internal helper argument for unit testing;
    the production miner intentionally emits exactly one view.
    """

    if len(candidate_indices) != len(labels):
        raise ValueError("candidate_indices and labels must have equal length")
    if group_size < 2 or views_per_query <= 0:
        raise ValueError("group_size >= 2 and views_per_query > 0 are required")
    ranked = [
        (int(candidate_index), rank, int(label))
        for rank, (candidate_index, label) in enumerate(
            zip(candidate_indices, labels, strict=True), start=1
        )
    ]
    positive_anchor = next((item for item in ranked if item[2] == 1), None)
    negative_anchor = next((item for item in ranked if item[2] == 0), None)
    if positive_anchor is None or negative_anchor is None:
        return None
    if group_size > len(ranked):
        raise ValueError("group_size cannot exceed retrieval pool size")

    anchors = [positive_anchor, negative_anchor]
    anchor_indices = {positive_anchor[0], negative_anchor[0]}
    remaining = [item for item in ranked if item[0] not in anchor_indices]
    capacity = views_per_query * (group_size - 2)
    if capacity < len(remaining):
        width = len(remaining)
        phase = int(rotation_offset) % width
        selected_positions = {
            (int((slot + 0.5) * width / capacity) + phase) % width for slot in range(capacity)
        }
        if len(selected_positions) != capacity:
            raise ValueError("rank-stratified rotating selection produced duplicates")
        remaining = [remaining[index] for index in sorted(selected_positions)]

    views: list[list[tuple[int, int, int]]] = [list(anchors) for _ in range(views_per_query)]
    for offset, item in enumerate(remaining):
        view = views[offset % views_per_query]
        if len(view) < group_size:
            view.append(item)
    for view_index, view in enumerate(views):
        present = {item[0] for item in view}
        start = (view_index * max(1, group_size - 2)) % max(1, len(ranked))
        for offset in range(len(ranked)):
            if len(view) >= group_size:
                break
            item = ranked[(start + offset) % len(ranked)]
            if item[0] not in present:
                view.append(item)
                present.add(item[0])
        if len(view) != group_size:
            raise ValueError("could not fill rotating deployed-top-k view")
        view.sort(key=lambda item: item[1])
    return views


def annotation_record(
    query: Pair,
    candidate: Pair,
    *,
    global_query_index: int,
    label: int,
    position: int,
    retriever_rank: int,
    retriever_score: float,
    checkpoint: str,
    retrieval_pool_size: int,
    mining_method: str,
    view_index: int = 0,
    view_count: int = 1,
) -> dict[str, Any]:
    group_id = f"query_{global_query_index:08d}_view_{view_index:02d}"
    response = "<answer>yes</answer>" if label else "<answer>no</answer>"
    return {
        "id": f"{group_id}_candidate_{position:02d}",
        "group_id": group_id,
        "label": label,
        "dataset": query.dataset,
        "query_type": query.query_type,
        "query": query.caption,
        "source_image_key": candidate.image_key,
        "image": candidate.unique_name,
        "mining_method": mining_method,
        "retriever_rank": retriever_rank,
        "retriever_score": retriever_score,
        "retriever_checkpoint": checkpoint,
        "retrieval_pool_size": retrieval_pool_size,
        "candidate_view_index": view_index,
        "candidate_view_count": view_count,
        "conversations": [
            {"from": "human", "value": "<image>\n" + PROMPT.format(query=query.caption)},
            {"from": "gpt", "value": response},
        ],
    }


def write_fragment_record(handle, record: Mapping[str, Any]) -> None:
    handle.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False))
    handle.write("\n")


def merge_fragments(output: Path, fragments: list[Path]) -> None:
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as target:
        target.write("[\n")
        first = True
        for fragment in fragments:
            with fragment.open(encoding="utf-8") as source:
                for line in source:
                    value = line.strip()
                    if not value:
                        continue
                    if not first:
                        target.write(",\n")
                    target.write(value)
                    first = False
        target.write("\n]\n")
    temporary.replace(output)


def main() -> int:
    args = parse_args()
    dist.init_process_group("nccl")
    rank = dist.get_rank()
    world_size = dist.get_world_size()
    local_rank = int(os.environ["LOCAL_RANK"])
    torch.cuda.set_device(local_rank)
    device = torch.device("cuda", local_rank)
    torch.backends.cuda.matmul.allow_tf32 = True

    for path in (
        args.pairs_file,
        args.image_embeddings,
        args.text_embedding_shards,
        args.query_indices,
        args.checkpoint,
    ):
        if not path.exists():
            raise FileNotFoundError(path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fragment_dir = args.output.parent / f".{args.output.stem}_fragments"
    fragment_dir.mkdir(parents=True, exist_ok=True)

    text_embeddings, start, end, global_count, text_manifest = load_text_shard(
        args.text_embedding_shards, rank, world_size
    )
    parse_start = time.perf_counter()
    galleries, gallery_rows, local_queries, image_count, pair_count = load_pairs(
        args.pairs_file, start, end
    )
    if pair_count != global_count:
        raise ValueError(f"pair rows {pair_count} != text rows {global_count}")
    selected_indices = {
        int(value) for value in json.loads(args.query_indices.read_text(encoding="utf-8"))
    }
    local_queries = [value for value in local_queries if value[0] in selected_indices]
    source_local_queries = len(local_queries)

    accessories = args.ground_truth == "scalar_plus_accessories"
    if not args.deployed_topk_group:
        local_queries = [
            (global_row, query)
            for global_row, query in local_queries
            if any(value >= 0 for value in query.text_attr_values)
            or (accessories and query.text_accessory_ids)
        ]
    skipped_no_constraint = source_local_queries - len(local_queries)

    image_embeddings = np.load(args.image_embeddings, mmap_mode="r")
    if image_embeddings.shape[0] != image_count:
        raise ValueError(
            f"image embedding rows {image_embeddings.shape[0]} != {image_count} images"
        )
    print(
        f"rank={rank} parsed_pairs={global_count} selected_queries={source_local_queries} "
        f"images={image_count} seconds={time.perf_counter() - parse_start:.1f}",
        flush=True,
    )

    checkpoint = str(args.checkpoint.expanduser().resolve())
    mining_method = (
        "finetuned_siglip2_deployed_topk_metadata_group_k20_distributed"
        if args.deployed_topk_group
        else f"finetuned_siglip2_top{args.topk}_rotating_k{args.group_size}_pas_metadata"
    )
    fragment = fragment_dir / f"rank{rank:02d}.jsonl"
    skipped_single_class = 0
    emitted = 0
    processed = 0
    mine_start = time.perf_counter()
    gallery_matrices = {
        dataset: torch.as_tensor(
            np.asarray(image_embeddings[rows], dtype=np.float32), device=device
        )
        for dataset, rows in gallery_rows.items()
    }

    with fragment.open("w", encoding="utf-8") as handle:
        offset = 0
        while offset < len(local_queries):
            dataset = local_queries[offset][1].dataset
            stop = offset
            while (
                stop < len(local_queries)
                and stop - offset < args.batch_size
                and local_queries[stop][1].dataset == dataset
            ):
                stop += 1
            batch_queries = local_queries[offset:stop]
            candidates = galleries[dataset]
            matrix = gallery_matrices[dataset]
            if matrix.shape[0] < args.topk:
                raise ValueError(
                    f"dataset {dataset} has {matrix.shape[0]} gallery images, below topk={args.topk}"
                )
            query_rows = [global_row - start for global_row, _ in batch_queries]
            query_matrix = torch.as_tensor(
                np.asarray(text_embeddings[query_rows], dtype=np.float32), device=device
            )
            scores = query_matrix @ matrix.T
            top_values, top_index_tensor = torch.topk(
                scores, k=args.topk, dim=1, largest=True, sorted=True
            )
            top_indices = top_index_tensor.cpu().tolist()
            top_scores = top_values.float().cpu().tolist()

            for batch_row, (global_row, query) in enumerate(batch_queries):
                deployed = top_indices[batch_row]
                labels = [
                    int(metadata_match(query, candidates[index], accessories=accessories))
                    for index in deployed
                ]
                if args.deployed_topk_group:
                    views = (
                        [
                            [
                                (candidate_index, position + 1, label)
                                for position, (candidate_index, label) in enumerate(
                                    zip(deployed, labels, strict=True)
                                )
                            ]
                        ]
                        if 0 in labels and 1 in labels
                        else None
                    )
                else:
                    views = build_rotating_deployed_topk_views(
                        deployed,
                        labels,
                        group_size=args.group_size,
                        views_per_query=args.views_per_query,
                        rotation_offset=global_row,
                    )
                if views is None:
                    skipped_single_class += 1
                    continue
                for view_index, view in enumerate(views):
                    for position, (candidate_index, candidate_rank, label) in enumerate(view):
                        record = annotation_record(
                            query,
                            candidates[candidate_index],
                            global_query_index=global_row,
                            label=label,
                            position=position,
                            retriever_rank=int(candidate_rank),
                            retriever_score=float(top_scores[batch_row][candidate_rank - 1]),
                            checkpoint=checkpoint,
                            retrieval_pool_size=args.topk,
                            mining_method=mining_method,
                            view_index=view_index,
                            view_count=len(views),
                        )
                        if args.deployed_topk_group:
                            group_id = f"query_{global_row:08d}"
                            record["id"] = f"{group_id}_candidate_{position:02d}"
                            record["group_id"] = group_id
                            record.pop("retrieval_pool_size")
                            record.pop("candidate_view_index")
                            record.pop("candidate_view_count")
                        write_fragment_record(handle, record)
                    emitted += 1

            processed += len(batch_queries)
            offset = stop
            if processed % args.log_every < len(batch_queries):
                elapsed = time.perf_counter() - mine_start
                print(
                    f"rank={rank} queries={processed}/{len(local_queries)} "
                    f"qps={processed / elapsed:.1f} emitted={emitted}",
                    flush=True,
                )
            del scores, query_matrix

    local_stats = {
        "rank": rank,
        "start": start,
        "end": end,
        "source_queries": source_local_queries,
        "queries": emitted,
        "examples": emitted * args.group_size,
        "skipped_no_constraint": skipped_no_constraint,
        "skipped_single_class": skipped_single_class,
        "fragment": str(fragment),
        "text_manifest": str(text_manifest),
        "seconds": time.perf_counter() - mine_start,
    }
    all_stats: list[dict[str, Any] | None] = [None] * world_size
    dist.all_gather_object(all_stats, local_stats)
    dist.barrier()
    if rank == 0:
        typed_stats = [value for value in all_stats if value is not None]
        merge_fragments(args.output, [Path(value["fragment"]) for value in typed_stats])
        manifest = {
            "output": str(args.output.resolve()),
            "queries": sum(int(value["queries"]) for value in typed_stats),
            "examples": sum(int(value["examples"]) for value in typed_stats),
            "source_queries": sum(int(value["source_queries"]) for value in typed_stats),
            "skipped_no_constraint": sum(
                int(value["skipped_no_constraint"]) for value in typed_stats
            ),
            "skipped_single_class": sum(
                int(value["skipped_single_class"]) for value in typed_stats
            ),
            "pairs_file": str(args.pairs_file.resolve()),
            "query_indices": str(args.query_indices.resolve()),
            "image_embeddings": str(args.image_embeddings.resolve()),
            "text_embedding_shards": str(args.text_embedding_shards.resolve()),
            "checkpoint": checkpoint,
            "ground_truth": args.ground_truth,
            "mining_method": mining_method,
            "retriever_usage": "candidate_order_only_not_training_target",
            "positive_selection": "earliest_anchor_plus_rank_spanning_pool_candidates",
            "negative_selection": "earliest_anchor_plus_rank_spanning_pool_candidates",
            "world_size": world_size,
            "batch_size_per_rank": args.batch_size,
            "retrieval_topk": args.topk,
            "group_size": args.group_size,
            "views_per_query": args.views_per_query,
            "ranks": typed_stats,
        }
        if args.deployed_topk_group:
            manifest["skipped_no_negative"] = manifest.pop("skipped_single_class")
            manifest.pop("skipped_no_constraint")
            manifest["positive_selection"] = "all_metadata_positives_in_deployed_topk"
            manifest["negative_selection"] = "deployed_topk_metadata_group"
            manifest["initial_topk"] = manifest.pop("retrieval_topk")
            manifest.pop("views_per_query")
            manifest.pop("retriever_usage")
            manifest["max_positives"] = args.group_size - 1
            manifest["attribute_vocab"] = None
            manifest["explicit_confusion_counts"] = {}
            manifest["negative_offset"] = 0
            manifest["negative_rank_bands"] = None
            for rank_stats in typed_stats:
                rank_stats["skipped_no_negative"] = rank_stats.pop("skipped_single_class")
                rank_stats.pop("skipped_no_constraint")
                rank_stats["fallback_full_sorts"] = 0
                rank_stats["band_fallbacks"] = 0
                rank_stats["explicit_confusion_counts"] = {}
        manifest_path = args.output.with_suffix(".manifest.json")
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(manifest, indent=2), flush=True)
    dist.barrier()
    dist.destroy_process_group()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
