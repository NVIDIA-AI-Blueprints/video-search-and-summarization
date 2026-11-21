# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Recompute PAS V3.1 three-mode retrieval metrics from embedding caches.

This is a standalone evaluator for TAO-FT PAS ``test_pairs.json`` exports. It
does not import TAO or load a model. Instead it consumes:

* a TAO-FT PAS pairs file
* a source-image embedding pickle keyed by ``"<dataset>\\t<image_path>"``
* a text embedding pickle keyed by exact caption text

The default paths recreate the recorded SigLIP2 zero-shot PAS V3.1 result:

``paired_caption/easy mAP = 0.2408204678``.

Added rerankers support.
"""

from __future__ import annotations
import argparse
import csv
import json
import logging
import math
import os
import pickle
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional, Sequence, Set, TextIO, Tuple
import numpy as np

try:
    from tqdm import tqdm
except Exception:

    def tqdm(iterable, **_kwargs):
        return iterable


from cosmos_reranker.reranking.config import RerankerConfig
from cosmos_reranker.reranking.data import Segment
from cosmos_reranker.reranking import build_reranker
from cosmos_reranker.reranking.base import Candidate, Reranker

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PAIRS_FILE = REPO_ROOT / "artifacts" / "pas_v31_test_tao" / "test_pairs.json"
DEFAULT_EMBEDDING_CACHE = REPO_ROOT / "artifacts" / "pas_v31_test_tao"
DEFAULT_IMAGE_EMBEDDINGS = DEFAULT_EMBEDDING_CACHE / "test_pairs_source_image_embeddings.pkl"
DEFAULT_TEXT_EMBEDDINGS = DEFAULT_EMBEDDING_CACHE / "test_pairs_text_embeddings_lower.pkl"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "results" / "pas_v31_step01953" / "rerank_three_modes"
DEFAULT_REFERENCE_CSV = Path()
PAS_QUERY_TYPES: Tuple[str, ...] = ("easy", "medium", "hard")
PAS_GROUND_TRUTH_MODES: Tuple[str, ...] = (
    "paired_caption",
    "scalar_attributes",
    "scalar_plus_accessories",
)
PAS_RERANKER_ALIASES = {"identity": "identity", "cosmos_reason": "cosmos_reason"}
PAS_EVAL_QUERY_BATCH = int(os.environ.get("PAS_EVAL_QUERY_BATCH", "512"))
WILDCARD_VALUE = -1
MISSING_MATCH_LABELS = {"not visible"}
PAS_SCALAR_FIELD_NAMES: Tuple[str, ...] = (
    "top_outer_color",
    "top_outer_type",
    "bottom_color",
    "bottom_type",
    "shoe_color",
    "shoe_type",
    "viewpoint",
)


@dataclass(frozen=True)
class PasPair:
    dataset: str
    query_type: str
    caption: str
    unique_name: str
    image_path: str
    image_attr_values: Tuple[int, ...] = ()
    text_attr_values: Tuple[int, ...] = ()
    image_accessory_ids: Tuple[int, ...] = ()
    text_accessory_ids: Tuple[int, ...] = ()


@dataclass(frozen=True)
class RerankTrace:
    """The scored stage-1 head before and after one reranker invocation."""

    stage1_head: Tuple[Candidate, ...]
    reranked_head: Tuple[Candidate, ...]


class QueryRankingWriter:
    """Stream inspectable per-query before/after rankings to JSONL."""

    FORMAT_VERSION = 1

    def __init__(self, path: Path, *, shard_count: int, shard_index: int) -> None:
        self.path = path
        self.shard_count = shard_count
        self.shard_index = shard_index
        self.num_queries = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle: TextIO = path.open("w", encoding="utf-8")

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    def write(
        self,
        *,
        mode: str,
        dataset: str,
        query_type: str,
        caption: str,
        gt_indices: Sequence[int],
        gallery_names: Sequence[str],
        gallery_image_paths: Sequence[Path],
        trace: RerankTrace,
        retriever_metrics: Mapping[str, float],
        reranked_metrics: Mapping[str, float],
    ) -> None:
        gt = {int(index) for index in gt_indices}
        stage1_rank = {
            int(candidate.segment.segment_id): rank
            for rank, candidate in enumerate(trace.stage1_head, start=1)
        }
        reranked_rank = {
            int(candidate.segment.segment_id): rank
            for rank, candidate in enumerate(trace.reranked_head, start=1)
        }
        candidates = []
        for candidate in trace.stage1_head:
            local_index = int(candidate.segment.segment_id)
            candidates.append(
                {
                    "local_index": local_index,
                    "image_key": gallery_names[local_index],
                    "image_path": str(gallery_image_paths[local_index]),
                    "is_gt": local_index in gt,
                    "stage1_rank": stage1_rank[local_index],
                    "rerank_rank": reranked_rank[local_index],
                    "stage1_score": float(candidate.stage1_score),
                    "rerank_score": None
                    if candidate.rerank_score is None
                    else float(candidate.rerank_score),
                    "rerank_meta": _jsonable(candidate.rerank_meta),
                }
            )
        self.num_queries += 1
        payload = {
            "format_version": self.FORMAT_VERSION,
            "shard_count": self.shard_count,
            "shard_index": self.shard_index,
            "shard_query_number": self.num_queries,
            "mode": mode,
            "dataset": dataset,
            "query_type": query_type,
            "caption": caption,
            "num_gt": len(gt),
            "retriever_first_gt_rank": int(retriever_metrics["first_pos"]),
            "reranker_first_gt_rank": int(reranked_metrics["first_pos"]),
            "retriever_ap": float(retriever_metrics["ap"]),
            "reranker_ap": float(reranked_metrics["ap"]),
            "stage1_head": [int(candidate.segment.segment_id) for candidate in trace.stage1_head],
            "reranked_head": [
                int(candidate.segment.segment_id) for candidate in trace.reranked_head
            ],
            "gt_indices_in_head": sorted(gt.intersection(stage1_rank)),
            "candidates": candidates,
        }
        self._handle.write(
            json.dumps(_jsonable(payload), ensure_ascii=True, separators=(",", ":")) + "\n"
        )


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
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


def _install_numpy_pickle_compat() -> None:
    """Allow NumPy-2 pickles to load in older NumPy-style environments."""
    try:
        import numpy as _np

        sys.modules.setdefault("numpy._core", _np.core)
        sys.modules.setdefault("numpy._core.numeric", _np.core.numeric)
        sys.modules.setdefault("numpy._core.multiarray", _np.core.multiarray)
    except Exception:
        return


def _load_pickle_dict(path: Path) -> Dict:
    _install_numpy_pickle_compat()
    with open(path, "rb") as f:
        data = pickle.load(f)
    if not isinstance(data, dict):
        raise TypeError(f"Expected a dict pickle at {path}, got {type(data)}")
    return data


def load_embedding_dict(path: Path, pairs_file: Path, *, images: bool) -> Dict:
    """Accept keyed caches or row-aligned arrays from the shared encoder."""
    if path.suffix != ".npy":
        return _load_pickle_dict(path)
    rows = list(_iter_json_records(pairs_file))
    if images:
        keys = list(
            dict.fromkeys(
                f"{_infer_dataset(str(row['image_path']), str(row.get('dataset') or ''))}\t"
                f"{str(row['image_path']).replace(chr(92), '/').strip()}"
                for row in rows
            )
        )
    else:
        keys = [str(row["caption"]).strip() for row in rows]
    array = np.load(path, mmap_mode="r", allow_pickle=False)
    if array.ndim != 2 or len(array) != len(keys):
        raise ValueError(f"Embedding rows do not match pair order: {path}")
    cache = {}
    for key, vector in zip(keys, array):
        value = np.asarray(vector, dtype=np.float32)
        if key in cache:
            if not np.allclose(cache[key], value, atol=1e-4, rtol=1e-4):
                raise ValueError(f"Duplicate caption has different embeddings: {key!r}")
        else:
            cache[key] = value
    return cache


def _iter_json_records(path: Path) -> Iterator[Dict]:
    """Iterate compact-line or ordinary JSON-array pair files."""
    with open(path, "r", encoding="utf-8") as f:
        _first = f.readline()
        second = f.readline()
    compact_line_mode = second.lstrip().startswith("{") and second.rstrip().rstrip(",").endswith(
        "}"
    )
    if compact_line_mode:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s in ("[", "]"):
                    continue
                if s.endswith(","):
                    s = s[:-1]
                if s:
                    yield json.loads(s)
        return
    with open(path, "r", encoding="utf-8") as f:
        for row in json.load(f):
            yield row


def _normalize_label(label: object) -> str:
    return " ".join(str(label).strip().lower().replace("_", " ").split())


def _load_missing_match_value_ids(pairs_file: Path) -> Tuple[Set[int], ...]:
    """Load per-attribute scalar IDs that should match as wildcards."""
    vocab_path = pairs_file.with_name("attribute_vocab.json")
    if not vocab_path.is_file():
        return ()
    with open(vocab_path, "r", encoding="utf-8") as f:
        vocab = json.load(f)
    attributes = vocab.get("attributes") or []
    value_to_id = vocab.get("value_to_id") or {}
    id_to_value = vocab.get("id_to_value") or {}
    missing_ids = []
    for attr_name in attributes:
        ids: Set[int] = set()
        by_value = value_to_id.get(attr_name) or {}
        if isinstance(by_value, Mapping):
            for label, value_id in by_value.items():
                if _normalize_label(label) in MISSING_MATCH_LABELS:
                    ids.add(int(value_id))
        by_id = id_to_value.get(attr_name) or {}
        if isinstance(by_id, list):
            for value_id, label in enumerate(by_id):
                if _normalize_label(label) in MISSING_MATCH_LABELS:
                    ids.add(int(value_id))
        elif isinstance(by_id, Mapping):
            for value_id, label in by_id.items():
                if _normalize_label(label) in MISSING_MATCH_LABELS:
                    ids.add(int(value_id))
        missing_ids.append(ids)
    return tuple(missing_ids)


def _attr_tuple(value, missing_ids_by_attr: Sequence[Iterable[int]]) -> Tuple[int, ...]:
    if value is None or not isinstance(value, (list, tuple)):
        return ()
    normalized = [int(v) for v in value]
    for attr_idx, missing_ids in enumerate(missing_ids_by_attr or ()):
        if attr_idx >= len(normalized):
            break
        if normalized[attr_idx] in missing_ids:
            normalized[attr_idx] = WILDCARD_VALUE
    return tuple(normalized)


def _accessory_tuple(value) -> Tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    out = tuple((int(v) for v in value if not isinstance(v, bool)))
    if tuple(sorted(set(out))) != out:
        raise ValueError(f"Accessory IDs must be sorted and unique, got {value!r}")
    if any((v <= 0 for v in out)):
        raise ValueError(f"Accessory IDs must be positive, got {value!r}")
    return out


def _infer_dataset(image_path: str, explicit_dataset: str = "") -> str:
    if explicit_dataset:
        return str(explicit_dataset).strip()
    normalized = str(image_path).replace("\\", "/")
    parts = [p for p in normalized.split("/") if p]
    for marker in ("images", "data"):
        if marker in parts:
            idx = parts.index(marker)
            if idx + 1 < len(parts):
                return parts[idx + 1].strip()
    if len(parts) > 1:
        return parts[0].strip()
    return ""


def load_pairs(pairs_file: Path, query_types: Sequence[str]) -> list[PasPair]:
    missing_ids_by_attr = _load_missing_match_value_ids(pairs_file)
    keep_query_types = set(query_types)
    pairs: list[PasPair] = []
    for row_idx, row in enumerate(
        tqdm(_iter_json_records(pairs_file), desc=f"Loading {pairs_file.name}", unit="pair"),
        start=1,
    ):
        caption = str(row.get("caption") or "").strip()
        query_type = str(row.get("query_type") or "").strip()
        unique_name = str(row.get("unique_name") or "").strip()
        image_path = str(row.get("image_path") or "").strip()
        if (
            not caption
            or not query_type
            or (not unique_name)
            or (query_type not in keep_query_types)
        ):
            continue
        dataset = _infer_dataset(image_path, str(row.get("dataset") or ""))
        if not dataset:
            raise ValueError(
                f"{pairs_file}:{row_idx}: could not infer dataset from image_path={image_path!r}"
            )
        pairs.append(
            PasPair(
                dataset=dataset,
                query_type=query_type,
                caption=caption,
                unique_name=unique_name,
                image_path=image_path,
                image_attr_values=_attr_tuple(row.get("image_attr_values"), missing_ids_by_attr),
                text_attr_values=_attr_tuple(row.get("text_attr_values"), missing_ids_by_attr),
                image_accessory_ids=_accessory_tuple(row.get("image_accessory_ids")),
                text_accessory_ids=_accessory_tuple(row.get("text_accessory_ids")),
            )
        )
    return pairs


def _dataset_names_from_pairs(pairs: Sequence[PasPair]) -> Tuple[str, ...]:
    seen: Set[str] = set()
    ordered = []
    for pair in pairs:
        if pair.dataset and pair.dataset not in seen:
            seen.add(pair.dataset)
            ordered.append(pair.dataset)
    return tuple(ordered)


def _evaluation_dataset_names(pairs: Sequence[PasPair]) -> Tuple[str, ...]:
    names = list(_dataset_names_from_pairs(pairs))
    names.sort(key=lambda name: 0 if name == "RSTPReid" else 1)
    return tuple(names)


def _pair_image_key(pair: PasPair) -> str:
    image_path = str(pair.image_path).replace("\\", "/").strip()
    return f"{pair.dataset}\t{image_path}"


def _pair_row_key(pair: PasPair) -> Tuple[str, str, str, str, str]:
    return (pair.dataset, pair.query_type, pair.caption, pair.unique_name, pair.image_path)


def _query_subset_from_full_pairs(
    full_pairs: Sequence[PasPair], subset_pairs: Sequence[PasPair], subset_path: Path
) -> list[PasPair]:
    full_keys = {_pair_row_key(pair) for pair in full_pairs}
    seen: Set[Tuple[str, str, str, str, str]] = set()
    query_pairs: list[PasPair] = []
    missing: list[Tuple[str, str, str, str, str]] = []
    for pair in subset_pairs:
        key = _pair_row_key(pair)
        if key in seen:
            continue
        seen.add(key)
        if key not in full_keys:
            missing.append(key)
            continue
        query_pairs.append(pair)
    if missing:
        preview = ", ".join((repr(key) for key in missing[:3]))
        raise ValueError(
            f"{len(missing)} query rows from {subset_path} are absent from full pairs; first missing: {preview}"
        )
    return query_pairs


def _gallery_by_dataset(
    pairs: Sequence[PasPair], image_embeddings: Mapping[str, np.ndarray]
) -> Dict[str, list[str]]:
    gallery: Dict[str, list[str]] = {dataset: [] for dataset in _dataset_names_from_pairs(pairs)}
    seen_by_dataset: Dict[str, Set[str]] = {dataset: set() for dataset in gallery}
    for pair in pairs:
        image_key = _pair_image_key(pair)
        if image_key not in image_embeddings:
            continue
        gallery.setdefault(pair.dataset, [])
        seen_by_dataset.setdefault(pair.dataset, set())
        if image_key in seen_by_dataset[pair.dataset]:
            continue
        seen_by_dataset[pair.dataset].add(image_key)
        gallery[pair.dataset].append(image_key)
    return gallery


def _compute_query_metrics(
    similarities: np.ndarray, gt_indices: Sequence[int], k: int
) -> Optional[Dict[str, float]]:
    if not gt_indices:
        return None
    n_gallery = len(similarities)
    gt = np.array(sorted(set((int(i) for i in gt_indices))), dtype=np.int64)
    if gt.size == 0:
        return None
    pos_sims = similarities[gt]
    pos_ranks = np.array(
        [1 + np.count_nonzero(similarities > score) for score in pos_sims], dtype=np.float64
    )
    pos_ranks_sorted = np.sort(pos_ranks)
    precision_at_hits = np.arange(1, len(pos_ranks_sorted) + 1, dtype=np.float64) / pos_ranks_sorted
    matches_in_top_k = int(np.count_nonzero(pos_ranks <= k))
    n_pos = len(pos_ranks)
    n_neg = n_gallery - n_pos
    if n_neg == 0:
        auc = 1.0
    else:
        ascending_pos_ranks = n_gallery - pos_ranks + 1
        pos_rank_sum = float(np.sum(ascending_pos_ranks))
        auc = (pos_rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)
    return {
        "ap": float(np.mean(precision_at_hits)),
        "rank1": float(pos_ranks_sorted[0] <= 1),
        "rank5": float(pos_ranks_sorted[0] <= 5),
        "auc": float(auc),
        "top_k_rate": float(matches_in_top_k / min(k, len(pos_ranks))),
        "zero_match": float(matches_in_top_k == 0),
        "first_pos": float(pos_ranks_sorted[0]),
        "num_gt": float(n_pos),
    }


def _finalize_metrics(
    raw_metrics: Sequence[Mapping[str, float]], gallery_size: int, k: int
) -> Optional[Dict[str, float]]:
    if not raw_metrics:
        return None
    total_gt = sum((float(m["num_gt"]) for m in raw_metrics))
    return {
        "num_queries": len(raw_metrics),
        "gallery_size": gallery_size,
        "avg_gt_per_query": total_gt / len(raw_metrics),
        "mAP": float(np.mean([m["ap"] for m in raw_metrics])),
        "Rank-1": float(np.mean([m["rank1"] for m in raw_metrics])),
        "Rank-5": float(np.mean([m["rank5"] for m in raw_metrics])),
        "Separability": float(np.mean([m["auc"] for m in raw_metrics])),
        f"Match@{k}": float(np.mean([m["top_k_rate"] for m in raw_metrics])),
        f"Zero@{k}": float(np.mean([m["zero_match"] for m in raw_metrics])),
        "First Pos": float(np.median([m["first_pos"] for m in raw_metrics])),
    }


@dataclass
class _RunningMetricSums:
    """Constant-memory metric accumulator used only for live progress logs."""

    num_queries: int = 0
    ap: float = 0.0
    rank1: float = 0.0
    rank5: float = 0.0
    auc: float = 0.0
    top_k_rate: float = 0.0
    zero_match: float = 0.0

    def add(self, metric: Mapping[str, float]) -> None:
        self.num_queries += 1
        self.ap += float(metric["ap"])
        self.rank1 += float(metric["rank1"])
        self.rank5 += float(metric["rank5"])
        self.auc += float(metric["auc"])
        self.top_k_rate += float(metric["top_k_rate"])
        self.zero_match += float(metric["zero_match"])

    def mean(self, field: str) -> float:
        return float(getattr(self, field)) / self.num_queries


def _log_running_metrics(
    *,
    mode: str,
    dataset: str,
    query_type: str,
    shard_index: int,
    shard_count: int,
    total_queries: int,
    retriever: _RunningMetricSums,
    reranked: _RunningMetricSums,
    reranker_name: str,
    k: int,
) -> None:
    if retriever.num_queries == 0 or reranked.num_queries == 0:
        return
    logging.info(
        "RUNNING %s shard %s/%s through %s/%s | queries=%s/%s | retriever: queries=%s mAP=%.6f Rank-1=%.6f Rank-5=%.6f Match@%s=%.6f Zero@%s=%.6f Sep=%.6f | %s: queries=%s mAP=%.6f Rank-1=%.6f Rank-5=%.6f Match@%s=%.6f Zero@%s=%.6f Sep=%.6f | delta mAP=%+.6f Rank-1=%+.6f Rank-5=%+.6f",
        mode,
        shard_index,
        shard_count,
        dataset,
        query_type,
        reranked.num_queries,
        total_queries,
        retriever.num_queries,
        retriever.mean("ap"),
        retriever.mean("rank1"),
        retriever.mean("rank5"),
        k,
        retriever.mean("top_k_rate"),
        k,
        retriever.mean("zero_match"),
        retriever.mean("auc"),
        reranker_name,
        reranked.num_queries,
        reranked.mean("ap"),
        reranked.mean("rank1"),
        reranked.mean("rank5"),
        k,
        reranked.mean("top_k_rate"),
        k,
        reranked.mean("zero_match"),
        reranked.mean("auc"),
        reranked.mean("ap") - retriever.mean("ap"),
        reranked.mean("rank1") - retriever.mean("rank1"),
        reranked.mean("rank5") - retriever.mean("rank5"),
    )


def _should_log_running(processed: int, last_logged: int, total: int, every: int) -> bool:
    if every <= 0 or processed <= last_logged:
        return False
    return processed == total or processed // every > last_logged // every


def _first_pos_histogram(raw_metrics: Sequence[Mapping[str, float]]) -> Dict[str, int]:
    """Return a compact, exactly mergeable histogram of first-positive ranks."""
    counts = Counter((int(metric["first_pos"]) for metric in raw_metrics))
    return {str(rank): int(counts[rank]) for rank in sorted(counts)}


def _embedding_stack(keys: Sequence[str], embeddings: Mapping[str, np.ndarray]) -> np.ndarray:
    return np.stack([np.asarray(embeddings[key]) for key in keys])


def _image_unique_names(pairs: Sequence[PasPair]) -> Dict[str, str]:
    names: Dict[str, str] = {}
    for pair in pairs:
        key = _pair_image_key(pair)
        if key not in names:
            names[key] = pair.unique_name
    return names


def _select_shard(items: Sequence[Tuple[str, Sequence[int]]], shard_count: int, shard_index: int):
    if shard_count <= 1:
        return list(items)
    return [
        item for item_index, item in enumerate(items) if item_index % shard_count == shard_index
    ]


def _build_reranker(args: argparse.Namespace) -> Reranker:
    if args.reranker in PAS_RERANKER_ALIASES:
        cfg = RerankerConfig(
            name=PAS_RERANKER_ALIASES[args.reranker],
            rerank_depth=args.rerank_depth,
            fps=args.fps,
            max_frames=args.max_frames,
            options={
                "score_chunk_size": args.reranker_score_chunk_size,
                "reasoning": args.reasoning,
                "max_think_tokens": args.max_think_tokens,
                "output_format": args.output_format,
                "image_prompt_mode": args.image_prompt_mode,
                "tensor_parallel_size": args.tensor_parallel_size,
                "gpu_memory_utilization": args.gpu_memory_utilization,
                "enforce_eager": args.enforce_eager,
                "max_lora_rank": args.max_lora_rank,
                "max_model_len": args.max_model_len,
                "jpeg_quality": args.jpeg_quality,
                "batch_size": args.reranker_batch_size,
                **(
                    {"image_min_pixels": args.image_min_pixels}
                    if args.image_min_pixels is not None
                    else {}
                ),
                **(
                    {"image_max_pixels": args.image_max_pixels}
                    if args.image_max_pixels is not None
                    else {}
                ),
                **({"model_id": args.model_id} if args.model_id else {}),
                **({"lora_path": str(args.lora_path)} if args.lora_path else {}),
                **{},
                **{},
            },
        )
        return build_reranker(cfg)
    raise KeyError(
        f"Unknown reranker {args.reranker!r}. Available: {', '.join(sorted(PAS_RERANKER_ALIASES))}"
    )


def _rerank_similarity_batch(
    captions: Sequence[str],
    similarities_batch: np.ndarray,
    gallery_names: Sequence[str],
    gallery_image_paths: Sequence[Path],
    reranker: Reranker,
    reranker_name: str,
    rerank_depth: int,
    trace_output: list[RerankTrace] | None = None,
) -> np.ndarray:
    similarities_batch = np.asarray(similarities_batch)
    expected_shape = (len(captions), len(gallery_names))
    if similarities_batch.shape != expected_shape:
        raise ValueError(
            f"Similarity matrix has shape {similarities_batch.shape}; expected {expected_shape}"
        )
    if len(gallery_image_paths) != len(gallery_names):
        raise ValueError("Gallery names and image paths must have the same length")
    if not np.all(np.isfinite(similarities_batch)):
        raise ValueError("Similarity matrix contains non-finite values")
    if reranker_name == "identity" or rerank_depth == 0:
        return similarities_batch
    stage1_orders = [
        np.argsort(-similarities, kind="stable").astype(np.int64).tolist()
        for similarities in similarities_batch
    ]
    depth = len(gallery_names) if rerank_depth < 0 else min(rerank_depth, len(gallery_names))
    heads: list[list[Candidate]] = []
    tails: list[list[int]] = []
    for order, similarities in zip(stage1_orders, similarities_batch):
        head_indices = order[:depth]
        tails.append(order[depth:])
        heads.append(
            [
                Candidate(
                    segment=Segment(
                        segment_id=int(local_idx),
                        video=gallery_names[int(local_idx)],
                        start=None,
                        end=None,
                    ),
                    stage1_score=float(similarities[int(local_idx)]),
                    image_path=gallery_image_paths[int(local_idx)],
                )
                for local_idx in head_indices
            ]
        )
    reranked_heads = reranker.rerank_batch(list(captions), heads)
    if len(reranked_heads) != len(heads):
        raise ValueError(
            f"Reranker returned {len(reranked_heads)} query results for {len(heads)} queries"
        )
    reranked = similarities_batch.astype(np.float64, copy=True)
    for row_index, (original_head, reranked_head, tail) in enumerate(
        zip(heads, reranked_heads, tails)
    ):
        expected_ids = [int(candidate.segment.segment_id) for candidate in original_head]
        reranked_ids = [int(candidate.segment.segment_id) for candidate in reranked_head]
        if len(reranked_ids) != len(set(reranked_ids)) or set(reranked_ids) != set(expected_ids):
            raise ValueError(
                f"Reranker must return every input candidate exactly once; expected={expected_ids}, returned={reranked_ids}"
            )
        if trace_output is not None:
            trace_output.append(RerankTrace(tuple(original_head), tuple(reranked_head)))
        tail_max = (
            float(np.max(reranked[row_index, np.asarray(tail, dtype=np.int64)])) if tail else 0.0
        )
        reranked[row_index, np.asarray(reranked_ids, dtype=np.int64)] = tail_max + np.arange(
            len(reranked_ids), 0, -1, dtype=np.float64
        )
    return reranked


def _group_paired_caption_queries(pairs: Sequence[PasPair]) -> Dict[Tuple[str, str, str], Set[str]]:
    groups: Dict[Tuple[str, str, str], Set[str]] = defaultdict(set)
    for pair in pairs:
        groups[pair.dataset, pair.query_type, pair.caption].add(_pair_image_key(pair))
    return groups


def _paired_query_items_by_group(
    pairs: Sequence[PasPair],
    query_pairs: Sequence[PasPair],
    image_embeddings: Mapping[str, np.ndarray],
    text_embeddings: Mapping[str, np.ndarray],
    query_types: Sequence[str],
    shard_count: int,
    shard_index: int,
) -> Dict[Tuple[str, str], list[Tuple[str, Sequence[int]]]]:
    query_groups = _group_paired_caption_queries(query_pairs)
    gallery = _gallery_by_dataset(pairs, image_embeddings)
    prepared: Dict[Tuple[str, str], list[Tuple[str, Sequence[int]]]] = {}
    for dataset in _evaluation_dataset_names(pairs):
        gallery_names = gallery.get(dataset, [])
        gallery_index = {name: idx for idx, name in enumerate(gallery_names)}
        for query_type in query_types:
            query_items = []
            for (query_dataset, query_kind, caption), image_keys in query_groups.items():
                if query_dataset != dataset or query_kind != query_type:
                    continue
                if caption not in text_embeddings:
                    continue
                gt = [gallery_index[name] for name in image_keys if name in gallery_index]
                if gt:
                    query_items.append((caption, gt))
            prepared[dataset, query_type] = _select_shard(query_items, shard_count, shard_index)
    return prepared


def evaluate_paired_caption(
    pairs: Sequence[PasPair],
    query_pairs: Sequence[PasPair],
    image_embeddings: Mapping[str, np.ndarray],
    text_embeddings: Mapping[str, np.ndarray],
    query_types: Sequence[str],
    image_root: Path,
    reranker: Reranker,
    reranker_name: str,
    rerank_depth: int,
    query_batch_size: int,
    shard_count: int,
    shard_index: int,
    k: int,
    running_log_every: int = 0,
    ranking_writer: QueryRankingWriter | None = None,
) -> list[Dict]:
    gallery = _gallery_by_dataset(pairs, image_embeddings)
    unique_names = _image_unique_names(pairs)
    query_items_by_group = _paired_query_items_by_group(
        pairs, query_pairs, image_embeddings, text_embeddings, query_types, shard_count, shard_index
    )
    total_queries = sum((len(items) for items in query_items_by_group.values()))
    retriever_running = _RunningMetricSums()
    reranked_running = _RunningMetricSums()
    last_running_log = 0
    rows = []
    for dataset in _evaluation_dataset_names(pairs):
        gallery_names = gallery.get(dataset, [])
        if not gallery_names:
            continue
        gallery_emb = _embedding_stack(gallery_names, image_embeddings)
        gallery_image_paths = [image_root / unique_names[name] for name in gallery_names]
        for query_type in query_types:
            query_items = query_items_by_group.get((dataset, query_type), [])
            raw_metrics = []
            for start in tqdm(
                range(0, len(query_items), query_batch_size),
                desc=f"paired_caption {dataset}/{query_type}",
                leave=False,
            ):
                batch_items = query_items[start : start + query_batch_size]
                query_emb = _embedding_stack(
                    [caption for caption, _ in batch_items], text_embeddings
                )
                retriever_similarities = query_emb @ gallery_emb.T
                traces: list[RerankTrace] | None = [] if ranking_writer is not None else None
                reranked_similarities = _rerank_similarity_batch(
                    [caption for caption, _ in batch_items],
                    retriever_similarities,
                    gallery_names,
                    gallery_image_paths,
                    reranker,
                    reranker_name,
                    rerank_depth,
                    traces,
                )
                if traces is not None and len(traces) != len(batch_items):
                    raise RuntimeError(
                        "Per-query ranking output requires a non-identity reranker with a positive rerank depth"
                    )
                for batch_index, (retriever_scores, reranked_scores, (caption, gt)) in enumerate(
                    zip(retriever_similarities, reranked_similarities, batch_items)
                ):
                    retriever_metrics = _compute_query_metrics(retriever_scores, gt, k)
                    reranked_metrics = _compute_query_metrics(reranked_scores, gt, k)
                    if retriever_metrics and reranked_metrics:
                        if ranking_writer is not None:
                            assert traces is not None
                            ranking_writer.write(
                                mode="paired_caption",
                                dataset=dataset,
                                query_type=query_type,
                                caption=caption,
                                gt_indices=gt,
                                gallery_names=gallery_names,
                                gallery_image_paths=gallery_image_paths,
                                trace=traces[batch_index],
                                retriever_metrics=retriever_metrics,
                                reranked_metrics=reranked_metrics,
                            )
                        retriever_running.add(retriever_metrics)
                        reranked_running.add(reranked_metrics)
                        raw_metrics.append(reranked_metrics)
                processed = reranked_running.num_queries
                if _should_log_running(
                    processed, last_running_log, total_queries, running_log_every
                ):
                    _log_running_metrics(
                        mode="paired_caption",
                        dataset=dataset,
                        query_type=query_type,
                        shard_index=shard_index,
                        shard_count=shard_count,
                        total_queries=total_queries,
                        retriever=retriever_running,
                        reranked=reranked_running,
                        reranker_name=reranker_name,
                        k=k,
                    )
                    last_running_log = processed
            metrics = _finalize_metrics(raw_metrics, len(gallery_names), k)
            if metrics is None:
                logging.info("paired_caption %s/%s: no valid queries", dataset, query_type)
                continue
            row = {
                "Dataset": dataset,
                "QueryType": query_type,
                "EasyAttribute": "",
                "_first_pos_histogram": _first_pos_histogram(raw_metrics),
                **metrics,
            }
            rows.append(row)
            logging.info(
                "paired_caption %s/%s: queries=%s mAP=%.10f Rank-1=%.10f Rank-5=%.10f",
                dataset,
                query_type,
                metrics["num_queries"],
                metrics["mAP"],
                metrics["Rank-1"],
                metrics["Rank-5"],
            )
    return rows


def _metadata_gt_indices(
    pair: PasPair,
    gallery_attrs: np.ndarray,
    gallery_indices_by_accessory: Mapping[int, Set[int]],
    accessory_required: bool,
) -> list[int]:
    if not pair.text_attr_values or len(pair.text_attr_values) != gallery_attrs.shape[1]:
        return []
    mask = np.ones(gallery_attrs.shape[0], dtype=bool)
    for attr_idx, value in enumerate(pair.text_attr_values):
        if value < 0:
            continue
        mask &= gallery_attrs[:, attr_idx] == value
        if not mask.any():
            return []
    gt = set(np.flatnonzero(mask).tolist())
    if accessory_required:
        for accessory_id in pair.text_accessory_ids:
            gt.intersection_update(gallery_indices_by_accessory.get(accessory_id, set()))
            if not gt:
                return []
    return sorted(gt)


def evaluate_metadata_mode(
    pairs: Sequence[PasPair],
    query_pairs: Sequence[PasPair],
    image_embeddings: Mapping[str, np.ndarray],
    text_embeddings: Mapping[str, np.ndarray],
    query_types: Sequence[str],
    image_root: Path,
    reranker: Reranker,
    reranker_name: str,
    rerank_depth: int,
    query_batch_size: int,
    shard_count: int,
    shard_index: int,
    k: int,
    mode: str,
    running_log_every: int = 0,
    ranking_writer: QueryRankingWriter | None = None,
    deduplicate_queries: bool = False,
) -> list[Dict]:
    if mode not in {"scalar_attributes", "scalar_plus_accessories"}:
        raise ValueError(f"Unsupported metadata mode: {mode}")
    accessory_required = mode == "scalar_plus_accessories"
    keep_query_types = set(query_types)
    gallery = _gallery_by_dataset(pairs, image_embeddings)
    unique_names = _image_unique_names(pairs)
    image_attr_by_key: Dict[str, Tuple[int, ...]] = {}
    image_accessories_by_key: Dict[str, Tuple[int, ...]] = {}
    for pair in pairs:
        image_key = _pair_image_key(pair)
        if pair.image_attr_values and image_key not in image_attr_by_key:
            image_attr_by_key[image_key] = pair.image_attr_values
        if image_key not in image_accessories_by_key:
            image_accessories_by_key[image_key] = pair.image_accessory_ids
    query_items_by_group: Dict[Tuple[str, str], list[Tuple[str, Sequence[int]]]] = {}
    for dataset in _evaluation_dataset_names(pairs):
        gallery_names = [name for name in gallery.get(dataset, []) if name in image_attr_by_key]
        if not gallery_names:
            continue
        gallery_attrs = np.asarray(
            [image_attr_by_key[name] for name in gallery_names], dtype=np.int64
        )
        gallery_indices_by_accessory: Dict[int, Set[int]] = defaultdict(set)
        if accessory_required:
            for gallery_idx, name in enumerate(gallery_names):
                for accessory_id in image_accessories_by_key.get(name, ()):
                    gallery_indices_by_accessory[accessory_id].add(gallery_idx)
        for query_type in query_types:
            query_items = []
            eligible_pairs = [
                pair
                for pair in query_pairs
                if pair.dataset == dataset
                and pair.query_type == query_type
                and (pair.query_type in keep_query_types)
                and (pair.caption in text_embeddings)
            ]
            if deduplicate_queries:
                groups: Dict[Tuple[str, str, str], list[PasPair]] = defaultdict(list)
                for pair in eligible_pairs:
                    groups[pair.dataset, pair.query_type, pair.caption].append(pair)
                scalar_conflict_groups = 0
                accessory_conflict_groups = 0
                for (_query_dataset, _query_type, caption), group in groups.items():
                    scalar_conflict_groups += int(
                        len({pair.text_attr_values for pair in group}) > 1
                    )
                    accessory_conflict_groups += int(
                        len({pair.text_accessory_ids for pair in group}) > 1
                    )
                    gt: Set[int] = set()
                    for pair in group:
                        gt.update(
                            _metadata_gt_indices(
                                pair,
                                gallery_attrs,
                                gallery_indices_by_accessory,
                                accessory_required,
                            )
                        )
                    if gt:
                        query_items.append((caption, sorted(gt)))
                logging.info(
                    "%s %s/%s deduplication: rows=%s queries=%s merged=%s scalar_conflicts=%s accessory_conflicts=%s",
                    mode,
                    dataset,
                    query_type,
                    len(eligible_pairs),
                    len(query_items),
                    len(eligible_pairs) - len(query_items),
                    scalar_conflict_groups,
                    accessory_conflict_groups,
                )
            else:
                for pair in eligible_pairs:
                    gt = _metadata_gt_indices(
                        pair, gallery_attrs, gallery_indices_by_accessory, accessory_required
                    )
                    if gt:
                        query_items.append((pair.caption, gt))
            query_items_by_group[dataset, query_type] = _select_shard(
                query_items, shard_count, shard_index
            )
    total_queries = sum((len(items) for items in query_items_by_group.values()))
    retriever_running = _RunningMetricSums()
    reranked_running = _RunningMetricSums()
    last_running_log = 0
    rows = []
    for dataset in _evaluation_dataset_names(pairs):
        gallery_names = [name for name in gallery.get(dataset, []) if name in image_attr_by_key]
        if not gallery_names:
            continue
        gallery_emb = _embedding_stack(gallery_names, image_embeddings)
        gallery_image_paths = [image_root / unique_names[name] for name in gallery_names]
        for query_type in query_types:
            query_items = query_items_by_group.get((dataset, query_type), [])
            raw_metrics = []
            for start in tqdm(
                range(0, len(query_items), query_batch_size),
                desc=f"{mode} {dataset}/{query_type}",
                leave=False,
            ):
                batch_items = query_items[start : start + query_batch_size]
                query_emb = _embedding_stack(
                    [caption for caption, _ in batch_items], text_embeddings
                )
                retriever_similarities = query_emb @ gallery_emb.T
                traces: list[RerankTrace] | None = [] if ranking_writer is not None else None
                reranked_similarities = _rerank_similarity_batch(
                    [caption for caption, _ in batch_items],
                    retriever_similarities,
                    gallery_names,
                    gallery_image_paths,
                    reranker,
                    reranker_name,
                    rerank_depth,
                    traces,
                )
                if traces is not None and len(traces) != len(batch_items):
                    raise RuntimeError(
                        "Per-query ranking output requires a non-identity reranker with a positive rerank depth"
                    )
                for batch_index, (retriever_scores, reranked_scores, (caption, gt)) in enumerate(
                    zip(retriever_similarities, reranked_similarities, batch_items)
                ):
                    retriever_metrics = _compute_query_metrics(retriever_scores, gt, k)
                    reranked_metrics = _compute_query_metrics(reranked_scores, gt, k)
                    if retriever_metrics and reranked_metrics:
                        if ranking_writer is not None:
                            assert traces is not None
                            ranking_writer.write(
                                mode=mode,
                                dataset=dataset,
                                query_type=query_type,
                                caption=caption,
                                gt_indices=gt,
                                gallery_names=gallery_names,
                                gallery_image_paths=gallery_image_paths,
                                trace=traces[batch_index],
                                retriever_metrics=retriever_metrics,
                                reranked_metrics=reranked_metrics,
                            )
                        retriever_running.add(retriever_metrics)
                        reranked_running.add(reranked_metrics)
                        raw_metrics.append(reranked_metrics)
                processed = reranked_running.num_queries
                if _should_log_running(
                    processed, last_running_log, total_queries, running_log_every
                ):
                    _log_running_metrics(
                        mode=mode,
                        dataset=dataset,
                        query_type=query_type,
                        shard_index=shard_index,
                        shard_count=shard_count,
                        total_queries=total_queries,
                        retriever=retriever_running,
                        reranked=reranked_running,
                        reranker_name=reranker_name,
                        k=k,
                    )
                    last_running_log = processed
            metrics = _finalize_metrics(raw_metrics, len(gallery_names), k)
            if metrics is None:
                logging.info("%s %s/%s: no valid queries", mode, dataset, query_type)
                continue
            row = {
                "Dataset": dataset,
                "QueryType": query_type,
                "EasyAttribute": "",
                "_first_pos_histogram": _first_pos_histogram(raw_metrics),
                **metrics,
            }
            rows.append(row)
            logging.info(
                "%s %s/%s: queries=%s mAP=%.10f Rank-1=%.10f Rank-5=%.10f",
                mode,
                dataset,
                query_type,
                metrics["num_queries"],
                metrics["mAP"],
                metrics["Rank-1"],
                metrics["Rank-5"],
            )
    return rows


def _metric_fieldnames(k: int) -> list[str]:
    return [
        "Dataset",
        "QueryType",
        "EasyAttribute",
        "num_queries",
        "gallery_size",
        "avg_gt_per_query",
        "mAP",
        "Rank-1",
        "Rank-5",
        "Separability",
        f"Match@{k}",
        f"Zero@{k}",
        "First Pos",
    ]


def _format_value(value) -> str:
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        return f"{value:.16g}"
    return str(value)


def _write_metrics_csv(path: Path, rows: Sequence[Mapping], k: int) -> None:
    fieldnames = _metric_fieldnames(k)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _format_value(row.get(key, "")) for key in fieldnames})
    logging.info("Wrote %s", path)


def _write_first_pos_histograms(path: Path, rows: Sequence[Mapping]) -> None:
    """Write exact first-positive rank counts used by the shard merger."""
    records = []
    for row in rows:
        histogram = row.get("_first_pos_histogram")
        if not isinstance(histogram, Mapping):
            raise ValueError("Metric row is missing its first-positive rank histogram")
        count = sum((int(value) for value in histogram.values()))
        if count != int(row["num_queries"]):
            raise ValueError(
                f"First-position histogram count {count} != num_queries {row['num_queries']}"
            )
        records.append(
            {
                "Dataset": row["Dataset"],
                "QueryType": row["QueryType"],
                "EasyAttribute": row.get("EasyAttribute", ""),
                "counts": {str(key): int(value) for key, value in histogram.items()},
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"format_version": 1, "rows": records}, indent=2) + "\n", encoding="utf-8"
    )
    logging.info("Wrote %s", path)


def _weighted_mean(values: Sequence[Tuple[float, float]]) -> float:
    total = sum((weight for _, weight in values))
    if not values or total <= 0:
        return float("nan")
    return float(sum((value * weight for value, weight in values)) / total)


def _row_weight(row: Mapping) -> float:
    try:
        return float(row.get("num_queries") or 0)
    except (TypeError, ValueError):
        return 0.0


def _aggregate_rows(
    rows: Sequence[Mapping],
    query_types: Sequence[str],
    k: int,
    dataset_names: Sequence[str],
    weighted: bool,
) -> list[Dict]:
    prefix = "WAVG" if weighted else "AVG"
    metric_keys = _metric_fieldnames(k)[3:]
    out = []
    for query_type in query_types:
        subset = [
            row
            for row in rows
            if row.get("Dataset") in dataset_names and row.get("QueryType") == query_type
        ]
        if not subset:
            continue
        aggregate = {
            "Dataset": f"{prefix}_{len(dataset_names)}_DATASETS",
            "QueryType": query_type,
            "EasyAttribute": "",
        }
        for key in metric_keys:
            if weighted and key == "num_queries":
                aggregate[key] = sum((_row_weight(row) for row in subset))
                continue
            if weighted and key == "First Pos":
                aggregate[key] = float("nan")
                continue
            values = []
            weighted_values = []
            for row in subset:
                try:
                    value = float(row.get(key))
                except (TypeError, ValueError):
                    continue
                if weighted:
                    weight = _row_weight(row)
                    if weight > 0:
                        weighted_values.append((value, weight))
                else:
                    values.append(value)
            aggregate[key] = (
                _weighted_mean(weighted_values)
                if weighted
                else float(sum(values) / len(values))
                if values
                else float("nan")
            )
        out.append(aggregate)
    return out


def _combined_fieldnames() -> list[str]:
    return ["Run", "Mode", "QueryType", "num_queries", "mAP", "Rank-1", "Rank-5", "Separability"]


def _combined_value(value) -> str:
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        if value.is_integer():
            return str(int(value))
        return str(value)
    return str(value)


def _format_combined_terminal_table(rows: Sequence[Mapping]) -> str:
    """Format the final weighted summary for concise terminal display."""
    fieldnames = _combined_fieldnames()
    metric_fields = {"mAP", "Rank-1", "Rank-5", "Separability"}
    rendered = []
    for row in rows:
        values = []
        for field in fieldnames:
            value = row.get(field, "")
            if field in metric_fields and value != "":
                values.append(f"{100.0 * float(value):.2f}%")
            elif field == "num_queries" and value != "":
                values.append(str(int(float(value))))
            else:
                values.append(str(value))
        rendered.append(values)
    widths = [len(field) for field in fieldnames]
    for values in rendered:
        widths = [max(width, len(value)) for width, value in zip(widths, values)]
    lines = ["  ".join((field.ljust(width) for field, width in zip(fieldnames, widths)))]
    lines.extend(
        (
            "  ".join((value.ljust(width) for value, width in zip(values, widths)))
            for values in rendered
        )
    )
    return "\n".join(lines)


def _write_combined_weighted_csv(
    path: Path, run_name: str, weighted_rows_by_mode: Mapping[str, Sequence[Mapping]]
) -> list[Dict]:
    rows = []
    for mode in PAS_GROUND_TRUTH_MODES:
        for row in weighted_rows_by_mode.get(mode, []):
            rows.append(
                {
                    "Run": run_name,
                    "Mode": mode,
                    "QueryType": row.get("QueryType", ""),
                    "num_queries": row.get("num_queries", ""),
                    "mAP": row.get("mAP", ""),
                    "Rank-1": row.get("Rank-1", ""),
                    "Rank-5": row.get("Rank-5", ""),
                    "Separability": row.get("Separability", ""),
                }
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = _combined_fieldnames()
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _combined_value(row.get(key, "")) for key in fieldnames})
    logging.info("Wrote %s", path)
    return rows


def _read_csv_rows(path: Path) -> list[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _compare_combined_csv(generated: Path, reference: Path) -> None:
    if not reference.is_file():
        logging.warning("Reference CSV not found, skipping comparison: %s", reference)
        return
    gen_rows = _read_csv_rows(generated)
    ref_rows = _read_csv_rows(reference)
    gen_by_key = {(r["Run"], r["Mode"], r["QueryType"]): r for r in gen_rows}
    ref_by_key = {(r["Run"], r["Mode"], r["QueryType"]): r for r in ref_rows}
    missing = sorted(set(ref_by_key) - set(gen_by_key))
    extra = sorted(set(gen_by_key) - set(ref_by_key))
    if missing or extra:
        raise AssertionError(f"CSV row keys differ. missing={missing} extra={extra}")
    max_diff = 0.0
    max_key = None
    max_col = None
    for key, ref_row in ref_by_key.items():
        gen_row = gen_by_key[key]
        for col in ("num_queries", "mAP", "Rank-1", "Rank-5", "Separability"):
            ref_val = float(ref_row[col])
            gen_val = float(gen_row[col])
            diff = abs(ref_val - gen_val)
            if diff > max_diff:
                max_diff = diff
                max_key = key
                max_col = col
    logging.info("Reference comparison: max abs diff %.12g at %s/%s", max_diff, max_key, max_col)
    easy_key = ("zero_shot", "paired_caption", "easy")
    if easy_key in gen_by_key:
        logging.info(
            "paired_caption/easy mAP: generated=%s reference=%s",
            gen_by_key[easy_key]["mAP"],
            ref_by_key.get(easy_key, {}).get("mAP", "missing"),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs-file", type=Path, default=DEFAULT_PAIRS_FILE)
    parser.add_argument(
        "--query-subset-file",
        type=Path,
        default=None,
        help="Optional PAS pairs-format query subset; --pairs-file remains the full gallery.",
    )
    parser.add_argument("--image-root", type=Path, default=None)
    parser.add_argument("--image-embeddings", type=Path, default=DEFAULT_IMAGE_EMBEDDINGS)
    parser.add_argument("--text-embeddings", type=Path, default=DEFAULT_TEXT_EMBEDDINGS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--run-name", default=None)
    parser.add_argument(
        "--modes", nargs="+", choices=PAS_GROUND_TRUTH_MODES, default=["scalar_plus_accessories"]
    )
    parser.add_argument(
        "--query-types", nargs="+", choices=PAS_QUERY_TYPES, default=list(PAS_QUERY_TYPES)
    )
    parser.add_argument(
        "--deduplicate-scalar-plus-accessories",
        action="store_true",
        help="Evaluate each exact (dataset, query_type, caption) once in scalar_plus_accessories mode, unioning its valid ground-truth sets.",
    )
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument(
        "--limit-pairs",
        type=int,
        default=0,
        help="Debug only: keep at most this many full pair rows after query-type filtering.",
    )
    parser.add_argument("--reranker", choices=tuple(PAS_RERANKER_ALIASES), default="identity")
    parser.add_argument("--model-id", default=None)
    parser.add_argument(
        "--lora-path",
        type=Path,
        default=None,
        help="Optional language-only PEFT adapter loaded dynamically by vLLM.",
    )
    parser.add_argument("--rerank-depth", type=int, default=50)
    parser.add_argument("--reranker-batch-size", type=int, default=16)
    parser.add_argument("--reranker-score-chunk-size", type=int, default=256)
    parser.add_argument("--query-batch-size", type=int, default=PAS_EVAL_QUERY_BATCH)
    parser.add_argument(
        "--running-log-every",
        type=int,
        default=0,
        help="Log cumulative retriever/reranker metrics every N scored queries; 0 disables.",
    )
    parser.add_argument(
        "--save-query-rankings",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Write every query's stage-1 and reranked heads, scores, GT labels, and reranker diagnostics to MODE/query_rankings.jsonl.",
    )
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--fps", type=float, default=2.0)
    parser.add_argument("--max-frames", type=int, default=32)
    parser.add_argument("--reasoning", choices=("none", "cot"), default="none")
    parser.add_argument("--max-think-tokens", type=int, default=256)
    parser.add_argument(
        "--output-format",
        choices=(
            "logit_delta",
            "verbalized_yes_no",
            "digit_expected",
            "relevance_1to5_expected",
            "verbalized_numeric",
            "credibility_1to5",
        ),
        default="logit_delta",
    )
    parser.add_argument("--tensor-parallel-size", type=int, default=1)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.85)
    parser.add_argument(
        "--enforce-eager",
        action="store_true",
        help="Disable vLLM CUDA graphs to reduce evaluation GPU memory.",
    )
    parser.add_argument(
        "--max-lora-rank",
        type=int,
        default=16,
        help="Maximum PEFT LoRA rank accepted by vLLM for dynamic adapters.",
    )
    parser.add_argument("--max-model-len", type=int, default=32768)
    parser.add_argument("--jpeg-quality", type=int, default=95)
    parser.add_argument(
        "--image-prompt-mode",
        choices=("default", "exact_attributes"),
        default="default",
        help="Optional image-scoring instruction variant; default preserves prior runs.",
    )
    parser.add_argument(
        "--image-min-pixels",
        type=int,
        default=None,
        help="Optional CR3/Qwen image pixel floor passed to the multimodal processor. This is required to enlarge low-resolution person crops; increasing only --image-max-pixels does not enlarge them.",
    )
    parser.add_argument(
        "--image-max-pixels",
        type=int,
        default=None,
        help="Optional CR3/Qwen image pixel ceiling passed to the multimodal processor. Use the training value to align train and inference grids.",
    )
    parser.add_argument(
        "--reference-csv", type=Path, default=None, help="Optional weighted CSV to compare against."
    )
    return parser.parse_args()


def _validate_args(args: argparse.Namespace) -> None:
    if args.k <= 0:
        raise ValueError("--k must be positive")
    if args.limit_pairs < 0:
        raise ValueError("--limit-pairs must be non-negative")
    if args.rerank_depth < -1:
        raise ValueError("--rerank-depth must be -1, 0, or positive")
    if args.reranker_batch_size <= 0:
        raise ValueError("--reranker-batch-size must be positive")
    if args.reranker_score_chunk_size <= 0:
        raise ValueError("--reranker-score-chunk-size must be positive")
    if args.query_batch_size <= 0:
        raise ValueError("--query-batch-size must be positive")
    if args.running_log_every < 0:
        raise ValueError("--running-log-every must be non-negative")
    if args.save_query_rankings and (args.reranker == "identity" or args.rerank_depth == 0):
        raise ValueError(
            "--save-query-rankings requires a non-identity reranker and positive rerank depth"
        )
    if args.shard_count < 1:
        raise ValueError("--shard-count must be >= 1")
    if not 0 <= args.shard_index < args.shard_count:
        raise ValueError("--shard-index must be in [0, shard-count)")
    if not 1 <= args.jpeg_quality <= 100:
        raise ValueError("--jpeg-quality must be in [1, 100]")
    if args.image_min_pixels is not None and args.image_min_pixels <= 0:
        raise ValueError("--image-min-pixels must be positive")
    if args.image_max_pixels is not None and args.image_max_pixels <= 0:
        raise ValueError("--image-max-pixels must be positive")
    if (
        args.image_min_pixels is not None
        and args.image_max_pixels is not None
        and (args.image_min_pixels > args.image_max_pixels)
    ):
        raise ValueError("--image-min-pixels cannot exceed --image-max-pixels")
    if not 0.0 < args.gpu_memory_utilization <= 1.0:
        raise ValueError("--gpu-memory-utilization must be in (0, 1]")
    if args.max_lora_rank <= 0:
        raise ValueError("--max-lora-rank must be positive")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    _validate_args(args)
    args.pairs_file = args.pairs_file.expanduser().resolve()
    args.query_subset_file = (
        args.query_subset_file.expanduser().resolve()
        if args.query_subset_file is not None
        else None
    )
    args.image_embeddings = args.image_embeddings.expanduser().resolve()
    args.text_embeddings = args.text_embeddings.expanduser().resolve()
    args.output_dir = args.output_dir.expanduser().resolve()
    args.image_root = (
        args.image_root.expanduser().resolve()
        if args.image_root is not None
        else args.pairs_file.parent / "images"
    )
    run_name = args.run_name or f"{args.reranker}_depth{args.rerank_depth}"
    if not args.pairs_file.is_file():
        raise FileNotFoundError(f"Pairs file not found: {args.pairs_file}")
    if args.query_subset_file is not None and (not args.query_subset_file.is_file()):
        raise FileNotFoundError(f"Query subset file not found: {args.query_subset_file}")
    if not args.image_embeddings.is_file():
        raise FileNotFoundError(f"Image embeddings not found: {args.image_embeddings}")
    if not args.text_embeddings.is_file():
        raise FileNotFoundError(f"Text embeddings not found: {args.text_embeddings}")
    if args.reranker != "identity" and (not args.image_root.is_dir()):
        raise FileNotFoundError(f"Image root not found: {args.image_root}")
    logging.info("Pairs: %s", args.pairs_file)
    if args.query_subset_file is not None:
        logging.info("Query subset: %s", args.query_subset_file)
    logging.info("Image embeddings: %s", args.image_embeddings)
    logging.info("Text embeddings: %s", args.text_embeddings)
    logging.info("Output: %s", args.output_dir)
    logging.info("Reranker: %s depth=%s", args.reranker, args.rerank_depth)
    if args.shard_count > 1:
        logging.info("Shard: %s/%s", args.shard_index, args.shard_count)
    pairs = load_pairs(args.pairs_file, args.query_types)
    if args.limit_pairs > 0:
        pairs = pairs[: args.limit_pairs]
    if not pairs:
        raise ValueError(f"No PAS pair rows loaded from {args.pairs_file}")
    query_pairs = pairs
    if args.query_subset_file is not None:
        subset_pairs = load_pairs(args.query_subset_file, args.query_types)
        query_pairs = _query_subset_from_full_pairs(pairs, subset_pairs, args.query_subset_file)
        if not query_pairs:
            raise ValueError(f"No PAS query rows selected from {args.query_subset_file}")
    dataset_names = _evaluation_dataset_names(pairs)
    logging.info("Loaded %s gallery pair rows", f"{len(pairs):,}")
    logging.info("Loaded %s query pair rows", f"{len(query_pairs):,}")
    logging.info("Datasets: %s", ", ".join(dataset_names))
    image_embeddings = load_embedding_dict(args.image_embeddings, args.pairs_file, images=True)
    text_embeddings = load_embedding_dict(args.text_embeddings, args.pairs_file, images=False)
    logging.info("Loaded %s image embeddings", f"{len(image_embeddings):,}")
    logging.info("Loaded %s text embeddings", f"{len(text_embeddings):,}")
    reranker = _build_reranker(args)
    weighted_rows_by_mode: Dict[str, list[Mapping]] = {}
    for mode in args.modes:
        mode_dir = args.output_dir / mode
        ranking_writer = (
            QueryRankingWriter(
                mode_dir / "query_rankings.jsonl",
                shard_count=args.shard_count,
                shard_index=args.shard_index,
            )
            if args.save_query_rankings
            else None
        )
        try:
            if mode == "paired_caption":
                rows = evaluate_paired_caption(
                    pairs,
                    query_pairs,
                    image_embeddings,
                    text_embeddings,
                    args.query_types,
                    args.image_root,
                    reranker,
                    args.reranker,
                    args.rerank_depth,
                    args.query_batch_size,
                    args.shard_count,
                    args.shard_index,
                    args.k,
                    args.running_log_every,
                    ranking_writer,
                )
            else:
                rows = evaluate_metadata_mode(
                    pairs,
                    query_pairs,
                    image_embeddings,
                    text_embeddings,
                    args.query_types,
                    args.image_root,
                    reranker,
                    args.reranker,
                    args.rerank_depth,
                    args.query_batch_size,
                    args.shard_count,
                    args.shard_index,
                    args.k,
                    mode,
                    args.running_log_every,
                    ranking_writer,
                    deduplicate_queries=mode == "scalar_plus_accessories"
                    and args.deduplicate_scalar_plus_accessories,
                )
        finally:
            if ranking_writer is not None:
                ranking_writer.close()
        if ranking_writer is not None:
            logging.info(
                "Wrote %s per-query before/after rankings to %s",
                f"{ranking_writer.num_queries:,}",
                ranking_writer.path,
            )
        aggregate_rows = _aggregate_rows(
            rows, args.query_types, args.k, dataset_names, weighted=False
        )
        weighted_rows = _aggregate_rows(
            rows, args.query_types, args.k, dataset_names, weighted=True
        )
        _write_metrics_csv(mode_dir / "nvidia_pas_metrics.csv", rows, args.k)
        _write_first_pos_histograms(mode_dir / "first_pos_histograms.json", rows)
        _write_metrics_csv(mode_dir / "nvidia_pas_metrics_aggregate.csv", aggregate_rows, args.k)
        _write_metrics_csv(
            mode_dir / "nvidia_pas_metrics_weighted_aggregate.csv", weighted_rows, args.k
        )
        weighted_rows_by_mode[mode] = weighted_rows
    combined_path = args.output_dir / f"{run_name}_three_modes_weighted.csv"
    combined_rows = _write_combined_weighted_csv(combined_path, run_name, weighted_rows_by_mode)
    metadata = {
        "data_root": str(args.pairs_file.parent),
        "pairs_file": str(args.pairs_file),
        "query_subset_file": str(args.query_subset_file) if args.query_subset_file else None,
        "image_root": str(args.image_root),
        "cache_dir": str(args.image_embeddings.parent),
        "image_embeddings": str(args.image_embeddings),
        "text_embeddings": str(args.text_embeddings),
        "run": run_name,
        "reranker": args.reranker,
        "rerank_depth": args.rerank_depth,
        "model_id": getattr(reranker, "model_id", args.model_id),
        "lora_path": getattr(reranker, "lora_path", None),
        "output_format": args.output_format,
        "reasoning": args.reasoning,
        "image_min_pixels": args.image_min_pixels,
        "image_max_pixels": args.image_max_pixels,
        "dataset_names": list(dataset_names),
        "query_types": list(args.query_types),
        "modes": list(args.modes),
        "aggregation": "dataset metrics weighted by num_queries",
        "deduplicate_scalar_plus_accessories": args.deduplicate_scalar_plus_accessories,
        "scalar_plus_accessories_deduplication_key": ["dataset", "query_type", "caption"]
        if args.deduplicate_scalar_plus_accessories
        else None,
        "scalar_plus_accessories_deduplication_ground_truth": "union"
        if args.deduplicate_scalar_plus_accessories
        else None,
        "k": args.k,
        "shard_count": args.shard_count,
        "shard_index": args.shard_index,
        "running_log_every": args.running_log_every,
        "save_query_rankings": args.save_query_rankings,
        "query_rankings": {
            mode: str(args.output_dir / mode / "query_rankings.jsonl") for mode in args.modes
        }
        if args.save_query_rankings
        else None,
    }
    metadata_path = args.output_dir / "run_metadata.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    logging.info("Wrote %s", metadata_path)
    if combined_rows:
        logging.info("Results summary:\n%s", _format_combined_terminal_table(combined_rows))
    if args.reference_csv is not None:
        _compare_combined_csv(combined_path, args.reference_csv)


if __name__ == "__main__":
    main()
