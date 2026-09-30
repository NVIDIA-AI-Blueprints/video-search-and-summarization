# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for the frozen CR3 visual-feature cache."""

from __future__ import annotations

import hashlib
import json
import os
from collections import OrderedDict
from pathlib import Path
from typing import Iterable

import torch
from safetensors import safe_open


def canonical_feature_layout(layout: str) -> str:
    """Normalize caches built before the generic prefix-block field existed."""

    if layout == "block24_prefix_plus_2_deepstack":
        return "visual_prefix_plus_deepstack"
    return layout


def visual_cache_key(image_path: str | os.PathLike[str]) -> str:
    """Identify aliases of the same PAS image by their canonical target path."""

    canonical = os.path.realpath(os.fspath(image_path))
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


class VisualCacheReader:
    """Read concatenated BF16 cache shards with a small per-process LRU."""

    def __init__(
        self,
        manifests: Iterable[str | os.PathLike[str]],
        resident_shards: int = 2,
        slice_reads: bool = False,
    ):
        self.entries: dict[str, dict] = {}
        self.preprocessing: dict | None = None
        self.feature_layout: str | None = None
        self.prefix_block: int | None = None
        self.resident_shards = max(1, int(resident_shards))
        self.slice_reads = bool(slice_reads)
        self._resident: OrderedDict[tuple[Path, str], torch.Tensor] = OrderedDict()
        for manifest_path in manifests:
            manifest_path = Path(manifest_path)
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            preprocessing = payload.get("preprocessing")
            feature_layout = canonical_feature_layout(str(payload.get("feature_layout", "")))
            if feature_layout != "visual_prefix_plus_deepstack":
                raise ValueError(
                    f"Only trainable visual-prefix caches are supported; got {feature_layout!r}"
                )
            prefix_block = int(payload.get("prefix_block", 24))
            if self.preprocessing is None:
                self.preprocessing = preprocessing
            elif preprocessing != self.preprocessing:
                raise ValueError("Visual cache manifests use different preprocessing")
            if self.feature_layout is None:
                self.feature_layout = feature_layout
            elif feature_layout != self.feature_layout:
                raise ValueError("Visual cache manifests use different feature layouts")
            if self.prefix_block is None:
                self.prefix_block = prefix_block
            elif prefix_block != self.prefix_block:
                raise ValueError("Visual cache manifests use different prefix blocks")
            for key, value in payload["entries"].items():
                shard = (manifest_path.parent / value["shard"]).resolve()
                entry = {
                    "shard": shard,
                    "offset": int(value["offset"]),
                    "length": int(value["length"]),
                    "deepstack_offset": int(value["deepstack_offset"]),
                    "deepstack_length": int(value["deepstack_length"]),
                    "grid": tuple(int(item) for item in value["grid_thw"]),
                }
                previous = self.entries.setdefault(key, entry)
                if previous != entry:
                    raise ValueError(f"Conflicting visual cache entry for {key}")

    def grid(self, key: str) -> tuple[int, int, int]:
        try:
            return self.entries[key]["grid"]
        except KeyError as error:
            raise KeyError(f"Visual cache has no entry for {key}") from error

    def _load_shard(self, path: Path, tensor_name: str) -> torch.Tensor:
        cache_key = (path, tensor_name)
        value = self._resident.pop(cache_key, None)
        if value is None:
            with safe_open(path, framework="pt", device="cpu") as handle:
                value = handle.get_tensor(tensor_name)
            while len(self._resident) >= self.resident_shards:
                self._resident.popitem(last=False)
        self._resident[cache_key] = value
        return value

    def get(self, key: str) -> tuple[torch.Tensor, tuple[int, int, int]]:
        try:
            entry = self.entries[key]
        except KeyError as error:
            raise KeyError(f"Visual cache has no entry for {key}") from error
        path = entry["shard"]
        offset = entry["offset"]
        length = entry["length"]
        grid = entry["grid"]
        if self.slice_reads:
            with safe_open(path, framework="pt", device="cpu") as handle:
                prefix_slice = handle.get_slice("prefix_features")
                deepstack_slice = handle.get_slice("deepstack_features")
                deep_offset = entry["deepstack_offset"]
                deep_length = entry["deepstack_length"]
                return (
                    prefix_slice[offset : offset + length],
                    deepstack_slice[deep_offset : deep_offset + deep_length],
                ), grid
        prefix = self._load_shard(path, "prefix_features")
        deepstack = self._load_shard(path, "deepstack_features")
        deep_offset = entry["deepstack_offset"]
        deep_length = entry["deepstack_length"]
        return (
            prefix[offset : offset + length],
            deepstack[deep_offset : deep_offset + deep_length],
        ), grid
