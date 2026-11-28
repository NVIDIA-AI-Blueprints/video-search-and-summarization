#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Generate row-aligned PAS SigLIP2 image and text embedding arrays."""

from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import numpy as np
from tqdm import tqdm


from cosmos_reranker.evaluation.embedders.siglip_v2_checkpoint import (
    SigLIP2CheckpointEmbedder,
)

FORMAT_VERSION = 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--pairs-file", type=Path, required=True)
    parser.add_argument(
        "--image-root",
        type=Path,
        default=None,
        help="Image symlink directory. Defaults to PAIRS_FILE parent / images.",
    )
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--tokenizer-dir", type=Path, default=os.environ.get("SIGLIP_TOKENIZER"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", default="auto", help="auto, cpu, cuda, or cuda:N")
    parser.add_argument(
        "--dtype",
        choices=("auto", "float32", "float16", "bfloat16"),
        default="auto",
    )
    parser.add_argument("--image-batch-size", type=int, default=16)
    parser.add_argument("--text-batch-size", type=int, default=64)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing image_embeddings.npy/text_embeddings.npy outputs.",
    )
    parser.add_argument(
        "--skip-images",
        action="store_true",
        help="Do not generate image_embeddings.npy.",
    )
    parser.add_argument(
        "--skip-text",
        action="store_true",
        help="Do not generate text_embeddings.npy.",
    )
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING"), default="INFO")
    args = parser.parse_args(argv)

    if args.image_batch_size <= 0:
        parser.error("--image-batch-size must be positive")
    if args.text_batch_size <= 0:
        parser.error("--text-batch-size must be positive")
    if args.skip_images and args.skip_text:
        parser.error("At least one of image or text embeddings must be generated")
    return args


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if type(value).__module__.startswith("torch"):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(_jsonable(payload), handle, indent=2, sort_keys=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _file_identity(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def iter_json_records(path: Path) -> Iterator[Mapping[str, Any]]:
    """Iterate ordinary JSON arrays and compact one-record-per-line arrays."""

    with path.open("r", encoding="utf-8") as handle:
        first = handle.readline()
        second = handle.readline()
    _ = first

    compact_line_mode = second.lstrip().startswith("{") and second.rstrip().rstrip(",").endswith(
        "}"
    )
    if compact_line_mode:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped in {"[", "]"}:
                    continue
                if stripped.endswith(","):
                    stripped = stripped[:-1]
                if stripped:
                    yield json.loads(stripped)
        return

    with path.open("r", encoding="utf-8") as handle:
        records = json.load(handle)
    if not isinstance(records, list):
        raise TypeError(f"Expected JSON array in {path}, got {type(records).__name__}")
    for record in records:
        if not isinstance(record, Mapping):
            raise TypeError(f"Expected pair record dict in {path}, got {type(record).__name__}")
        yield record


def infer_dataset(image_path: str, explicit_dataset: str = "") -> str:
    if explicit_dataset:
        return explicit_dataset.strip()
    normalized = image_path.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    for marker in ("images", "data"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts):
                return parts[index + 1].strip()
    if len(parts) > 1:
        return parts[0].strip()
    return ""


def image_key(record: Mapping[str, Any]) -> str:
    image_path = str(record.get("image_path") or "").replace("\\", "/").strip()
    dataset = infer_dataset(image_path, str(record.get("dataset") or ""))
    if not dataset or not image_path:
        raise ValueError(f"Cannot build image key from record: {record!r}")
    return f"{dataset}\t{image_path}"


def load_pas_rows(
    pairs_file: Path,
    image_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(unique_images, query_rows)`` in the row order used by caches."""

    unique_images: list[dict[str, Any]] = []
    seen_images: set[str] = set()
    query_rows: list[dict[str, Any]] = []

    for query_id, record in enumerate(iter_json_records(pairs_file)):
        key = image_key(record)
        if key not in seen_images:
            seen_images.add(key)
            unique_name = str(record.get("unique_name") or "").strip()
            if not unique_name:
                raise ValueError(f"Pair row {query_id} has no unique_name")
            unique_images.append(
                {
                    "image_id": len(unique_images),
                    "key": key,
                    "dataset": infer_dataset(
                        str(record.get("image_path") or ""),
                        str(record.get("dataset") or ""),
                    ),
                    "image_path": str(record.get("image_path") or "").replace("\\", "/").strip(),
                    "unique_name": unique_name,
                    "path": image_root / unique_name,
                }
            )

        caption = str(record.get("caption") or "").strip()
        if not caption:
            raise ValueError(f"Pair row {query_id} has no caption")
        query_rows.append(
            {
                "query_id": query_id,
                "caption": caption,
                "query_type": str(record.get("query_type") or "").strip(),
                "image_key": key,
            }
        )

    return unique_images, query_rows


def _l2_normalize(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    if values.ndim != 2:
        raise ValueError(f"Encoder returned a non-matrix embedding shape: {values.shape}")
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    if not np.all(np.isfinite(values)) or np.any(norms <= 0):
        raise ValueError("Encoder returned non-finite or zero-norm embeddings")
    return values / norms


def _prepare_output(path: Path, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} exists; pass --overwrite to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)


def _write_array_manifest(
    path: Path,
    *,
    array_path: Path,
    count: int,
    embedding_dim: int,
    checkpoint: Path,
    pairs_file: Path,
    row_order: str,
    encoder: SigLIP2CheckpointEmbedder,
    args: argparse.Namespace,
) -> None:
    _atomic_write_json(
        path,
        {
            "format_version": FORMAT_VERSION,
            "array": str(array_path),
            "shape": [count, embedding_dim],
            "dtype": "float32",
            "checkpoint": _file_identity(checkpoint),
            "pairs_file": str(pairs_file),
            "row_order": row_order,
            "encoder": {
                "class": "SigLIP2CheckpointEmbedder",
                "checkpoint_metadata": encoder.checkpoint_metadata,
                "mapping_report": encoder.mapping_report,
                "device": encoder.device,
                "dtype": encoder.dtype,
                "tokenizer_dir": str(encoder.tokenizer_dir),
                "image_batch_size": args.image_batch_size,
                "text_batch_size": args.text_batch_size,
                "text_attention_mask": "all_ones",
                "image_preprocess": "rgb_resize_256_bilinear_normalize_mean_std_0.5",
            },
        },
    )


def generate_image_embeddings(
    encoder: SigLIP2CheckpointEmbedder,
    images: Sequence[Mapping[str, Any]],
    output_dir: Path,
    args: argparse.Namespace,
) -> Path:
    output_path = output_dir / "image_embeddings.npy"
    _prepare_output(output_path, args.overwrite)
    array = np.lib.format.open_memmap(
        output_path,
        mode="w+",
        dtype=np.float32,
        shape=(len(images), int(encoder.embedding_dim)),
    )

    for start in tqdm(range(0, len(images), args.image_batch_size), desc="Embedding PAS images"):
        batch = images[start : start + args.image_batch_size]
        paths = [Path(row["path"]) for row in batch]
        missing = [str(path) for path in paths if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"Missing exported image path: {missing[0]}")
        embeddings = _l2_normalize(encoder.embed_images(paths))
        end = start + len(batch)
        if embeddings.shape != (len(batch), encoder.embedding_dim):
            raise ValueError(
                f"Image encoder returned {embeddings.shape}; expected "
                f"{(len(batch), encoder.embedding_dim)}"
            )
        array[start:end] = embeddings
        array.flush()

    _write_array_manifest(
        output_dir / "image_embeddings.json",
        array_path=output_path,
        count=len(images),
        embedding_dim=int(encoder.embedding_dim),
        checkpoint=args.checkpoint,
        pairs_file=args.pairs_file,
        row_order="first-seen unique <dataset>\\t<image_path> in test_pairs.json order",
        encoder=encoder,
        args=args,
    )
    return output_path


def canonicalize_caption_rows(
    array: np.ndarray, queries: Sequence[Mapping[str, Any]]
) -> None:
    """Reuse the first vector for repeated captions despite batch rounding drift."""
    first_rows: dict[str, int] = {}
    for index, query in enumerate(queries):
        caption = str(query["caption"]).strip()
        first = first_rows.setdefault(caption, index)
        if first != index:
            array[index] = array[first]


def generate_text_embeddings(
    encoder: SigLIP2CheckpointEmbedder,
    queries: Sequence[Mapping[str, Any]],
    output_dir: Path,
    args: argparse.Namespace,
) -> Path:
    output_path = output_dir / "text_embeddings.npy"
    _prepare_output(output_path, args.overwrite)
    array = np.lib.format.open_memmap(
        output_path,
        mode="w+",
        dtype=np.float32,
        shape=(len(queries), int(encoder.embedding_dim)),
    )

    for start in tqdm(range(0, len(queries), args.text_batch_size), desc="Embedding PAS captions"):
        batch = queries[start : start + args.text_batch_size]
        captions = [str(row["caption"]) for row in batch]
        embeddings = _l2_normalize(encoder.embed_text(captions))
        end = start + len(batch)
        if embeddings.shape != (len(batch), encoder.embedding_dim):
            raise ValueError(
                f"Text encoder returned {embeddings.shape}; expected "
                f"{(len(batch), encoder.embedding_dim)}"
            )
        array[start:end] = embeddings
        array.flush()

    canonicalize_caption_rows(array, queries)
    array.flush()
    _write_array_manifest(
        output_dir / "text_embeddings.json",
        array_path=output_path,
        count=len(queries),
        embedding_dim=int(encoder.embedding_dim),
        checkpoint=args.checkpoint,
        pairs_file=args.pairs_file,
        row_order="one row per test_pairs.json query row",
        encoder=encoder,
        args=args,
    )
    return output_path


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    args.pairs_file = args.pairs_file.expanduser().resolve()
    args.checkpoint = args.checkpoint.expanduser().resolve()
    args.output_dir = args.output_dir.expanduser().resolve()
    args.image_root = (
        args.image_root.expanduser().resolve()
        if args.image_root is not None
        else args.pairs_file.parent / "images"
    )
    if args.tokenizer_dir is not None:
        args.tokenizer_dir = args.tokenizer_dir.expanduser().resolve()

    if not args.pairs_file.is_file():
        raise FileNotFoundError(f"Pairs file not found: {args.pairs_file}")
    if not args.checkpoint.is_file():
        raise FileNotFoundError(f"Checkpoint not found: {args.checkpoint}")
    if not args.image_root.is_dir():
        raise FileNotFoundError(f"Image root not found: {args.image_root}")

    images, queries = load_pas_rows(args.pairs_file, args.image_root)
    if not images or not queries:
        raise ValueError("PAS export must contain at least one image and one query")
    logging.info(
        "Loaded %s unique images and %s query rows", f"{len(images):,}", f"{len(queries):,}"
    )

    encoder = SigLIP2CheckpointEmbedder(
        args.checkpoint,
        tokenizer_dir=args.tokenizer_dir,
        device=args.device,
        image_batch_size=args.image_batch_size,
        text_batch_size=args.text_batch_size,
        dtype=args.dtype,
    )
    logging.info("Mapped %s checkpoint tensors", encoder.mapping_report.mapped_keys)

    if not args.skip_images:
        image_path = generate_image_embeddings(encoder, images, args.output_dir, args)
        logging.info("Wrote %s", image_path)
    if not args.skip_text:
        text_path = generate_text_embeddings(encoder, queries, args.output_dir, args)
        logging.info("Wrote %s", text_path)

    logging.info("Done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
