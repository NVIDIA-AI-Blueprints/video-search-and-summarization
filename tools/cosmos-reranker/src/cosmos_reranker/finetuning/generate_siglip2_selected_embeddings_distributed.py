#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Build distributed SigLIP2 train embeddings for a fixed query selection.

The image array is complete because hard-negative mining scores each selected
query against its full dataset gallery.  Text shard arrays retain the original
pairs-file row layout, but only selected query rows are encoded.  This keeps
the downstream miner's global-row contract without redundantly encoding every
caption in the roughly two-million-row PAS train export.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.distributed as dist


from cosmos_reranker.evaluation.generate_embeddings import (
    _file_identity,
    _jsonable,
    _l2_normalize,
    load_pas_rows,
)
from cosmos_reranker.evaluation.embedders.siglip_v2_checkpoint import (
    SigLIP2CheckpointEmbedder,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs-file", type=Path, required=True)
    parser.add_argument("--image-root", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--tokenizer-dir", type=Path, required=True)
    parser.add_argument("--query-indices", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--image-batch-size", type=int, default=128)
    parser.add_argument("--text-batch-size", type=int, default=512)
    parser.add_argument("--dtype", choices=("float16", "bfloat16", "float32"), default="float16")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--text-only",
        action="store_true",
        help="Encode only selected text rows and reuse a separately verified image array.",
    )
    return parser.parse_args()


def bounds(count: int, rank: int, world_size: int) -> tuple[int, int]:
    return count * rank // world_size, count * (rank + 1) // world_size


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(_jsonable(value), indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def encoder_metadata(
    encoder: SigLIP2CheckpointEmbedder,
    args: argparse.Namespace,
) -> dict[str, Any]:
    return {
        "class": "SigLIP2CheckpointEmbedder",
        "checkpoint_metadata": encoder.checkpoint_metadata,
        "mapping_report": encoder.mapping_report,
        "device": str(encoder.device),
        "dtype": str(encoder.dtype),
        "tokenizer_dir": str(args.tokenizer_dir),
        "image_batch_size": args.image_batch_size,
        "text_batch_size": args.text_batch_size,
        "text_attention_mask": "all_ones",
        "image_preprocess": "rgb_resize_256_bilinear_normalize_mean_std_0.5",
    }


def prepare(path: Path, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} exists; pass --overwrite")
    path.unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    args.pairs_file = args.pairs_file.expanduser().resolve()
    args.image_root = args.image_root.expanduser().resolve()
    args.checkpoint = args.checkpoint.expanduser().resolve()
    args.tokenizer_dir = args.tokenizer_dir.expanduser().resolve()
    args.query_indices = args.query_indices.expanduser().resolve()
    args.output_dir = args.output_dir.expanduser().resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for path in (
        args.pairs_file,
        args.image_root,
        args.checkpoint,
        args.tokenizer_dir,
        args.query_indices,
    ):
        if not path.exists():
            raise FileNotFoundError(path)

    dist.init_process_group("nccl")
    rank = dist.get_rank()
    world_size = dist.get_world_size()
    local_rank = int(os.environ["LOCAL_RANK"])
    torch.cuda.set_device(local_rank)
    # The CUDA 13.2 compatibility stack cannot select a working cuDNN Conv3d
    # engine for this SigLIP2 checkpoint. The eager fallback is numerically
    # identical to the validated validation-embedding path.
    torch.backends.cudnn.enabled = False

    images, queries = load_pas_rows(args.pairs_file, args.image_root)
    selected = {int(value) for value in json.loads(args.query_indices.read_text(encoding="utf-8"))}
    if not selected:
        raise ValueError("query selection is empty")
    if min(selected) < 0 or max(selected) >= len(queries):
        raise IndexError("query selection contains an out-of-range row")

    image_start, image_end = bounds(len(images), rank, world_size)
    text_start, text_end = bounds(len(queries), rank, world_size)
    local_text_rows = sorted(index for index in selected if text_start <= index < text_end)
    print(
        f"rank={rank} images={image_start}:{image_end}/{len(images)} "
        f"selected_text={len(local_text_rows)} text_range={text_start}:{text_end}/{len(queries)}",
        flush=True,
    )

    encoder = SigLIP2CheckpointEmbedder(
        args.checkpoint,
        tokenizer_dir=args.tokenizer_dir,
        device=f"cuda:{local_rank}",
        image_batch_size=args.image_batch_size,
        text_batch_size=args.text_batch_size,
        dtype=args.dtype,
    )
    dim = int(encoder.embedding_dim)
    common = {
        "format_version": 1,
        "dtype": "float32",
        "checkpoint": _file_identity(args.checkpoint),
        "pairs_file": str(args.pairs_file),
        "encoder": encoder_metadata(encoder, args),
    }

    if not args.text_only:
        image_array_path = args.output_dir / (
            f"image_embeddings.shard_{rank:05d}_of_{world_size:05d}.npy"
        )
        prepare(image_array_path, args.overwrite)
        image_array = np.lib.format.open_memmap(
            image_array_path,
            mode="w+",
            dtype=np.float32,
            shape=(image_end - image_start, dim),
        )
        for global_start in range(image_start, image_end, args.image_batch_size):
            global_end = min(global_start + args.image_batch_size, image_end)
            paths = [Path(row["path"]) for row in images[global_start:global_end]]
            missing = next((path for path in paths if not path.is_file()), None)
            if missing is not None:
                raise FileNotFoundError(missing)
            values = _l2_normalize(encoder.embed_images(paths))
            image_array[global_start - image_start : global_end - image_start] = values
        image_array.flush()
        image_manifest_path = image_array_path.with_suffix(".json")
        atomic_json(
            image_manifest_path,
            {
                **common,
                "array": str(image_array_path),
                "shape": [image_end - image_start, dim],
                "row_order": "contiguous shard of first-seen unique image rows",
                "shard": {
                    "index": rank,
                    "count": world_size,
                    "global_count": len(images),
                    "global_start": image_start,
                    "global_end": image_end,
                },
            },
        )

    text_array_path = args.output_dir / (
        f"text_embeddings.shard_{rank:05d}_of_{world_size:05d}.npy"
    )
    prepare(text_array_path, args.overwrite)
    text_array = np.lib.format.open_memmap(
        text_array_path,
        mode="w+",
        dtype=np.float32,
        shape=(text_end - text_start, dim),
    )
    for offset in range(0, len(local_text_rows), args.text_batch_size):
        global_rows = local_text_rows[offset : offset + args.text_batch_size]
        captions = [str(queries[index]["caption"]) for index in global_rows]
        values = _l2_normalize(encoder.embed_text(captions))
        text_array[np.asarray(global_rows) - text_start] = values
    text_array.flush()
    text_manifest_path = text_array_path.with_suffix(".json")
    atomic_json(
        text_manifest_path,
        {
            **common,
            "array": str(text_array_path),
            "shape": [text_end - text_start, dim],
            "row_order": "global train_pairs.json query row; only query_indices rows populated",
            "query_indices": str(args.query_indices),
            "populated_rows": len(local_text_rows),
            "shard": {
                "index": rank,
                "count": world_size,
                "global_count": len(queries),
                "global_start": text_start,
                "global_end": text_end,
            },
        },
    )

    del encoder, text_array
    if not args.text_only:
        del image_array
    torch.cuda.empty_cache()
    dist.barrier()
    if rank == 0 and not args.text_only:
        merged_path = args.output_dir / "image_embeddings.npy"
        prepare(merged_path, args.overwrite)
        merged = np.lib.format.open_memmap(
            merged_path,
            mode="w+",
            dtype=np.float32,
            shape=(len(images), dim),
        )
        for shard_rank in range(world_size):
            start, end = bounds(len(images), shard_rank, world_size)
            shard_path = args.output_dir / (
                f"image_embeddings.shard_{shard_rank:05d}_of_{world_size:05d}.npy"
            )
            shard = np.load(shard_path, mmap_mode="r")
            if shard.shape != (end - start, dim):
                raise ValueError(f"unexpected image shard shape: {shard_path}: {shard.shape}")
            merged[start:end] = shard
        merged.flush()
        atomic_json(
            args.output_dir / "image_embeddings.json",
            {
                **common,
                "array": str(merged_path),
                "shape": [len(images), dim],
                "row_order": "first-seen unique <dataset>\\t<image_path> in train_pairs.json order",
                "shard": None,
            },
        )
        atomic_json(
            args.output_dir / "selected_embedding_build.json",
            {
                "pairs_file": str(args.pairs_file),
                "query_indices": str(args.query_indices),
                "selected_query_count": len(selected),
                "image_count": len(images),
                "query_count": len(queries),
                "world_size": world_size,
                "checkpoint": _file_identity(args.checkpoint),
                "text_only": args.text_only,
            },
        )
        print(f"merged {len(images)} image embeddings into {merged_path}", flush=True)
    elif rank == 0:
        atomic_json(
            args.output_dir / "selected_embedding_build.json",
            {
                "pairs_file": str(args.pairs_file),
                "query_indices": str(args.query_indices),
                "selected_query_count": len(selected),
                "image_count": len(images),
                "query_count": len(queries),
                "world_size": world_size,
                "checkpoint": _file_identity(args.checkpoint),
                "text_only": True,
            },
        )
        print(f"encoded {len(selected)} selected text rows (text-only)", flush=True)
    dist.barrier()
    dist.destroy_process_group()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
