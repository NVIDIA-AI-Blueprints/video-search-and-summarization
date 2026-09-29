# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validated, local benchmark dataset contract for harness-managed Jobs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .dataset import DATASET_META, DATASETS, DatasetMeta

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".avi"}


def load_dataset_spec(path: Path, data_dir: Path) -> dict[str, Any]:
    """Register a benchmark dataset after checking every referenced local file.

    The spec is separate from the query ground truth: name, task, subsets
    (subset name to ground-truth filename), and video filenames. All filenames
    are relative to ``<data-dir>/<name>``.
    """
    spec = json.loads(path.read_text())
    if not isinstance(spec, dict):
        raise TypeError("dataset spec must be an object")
    name = spec.get("name")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("dataset spec needs a safe name")
    task = spec.get("task")
    if task not in ("segment", "clip"):
        raise ValueError("dataset task must be segment or clip")
    subsets = spec.get("subsets")
    if not isinstance(subsets, dict) or not subsets:
        raise ValueError("dataset subsets must be a nonempty object")
    root = (data_dir / name).resolve()
    paths: dict[str, str] = {}
    for subset, filename in subsets.items():
        if not isinstance(subset, str) or (subset and not _NAME.fullmatch(subset)):
            raise ValueError(f"invalid subset: {subset!r}")
        if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".json"):
            raise ValueError(f"invalid ground truth filename: {filename!r}")
        file = root / filename
        if not file.is_file():
            raise ValueError(f"ground truth missing: {file}")
        data = json.loads(file.read_text())
        if not isinstance(data, dict) or not data:
            raise ValueError(f"ground truth must contain queries: {file}")
        paths[subset] = f"{name}/{filename}"
    videos = spec.get("videos")
    if not isinstance(videos, list) or not videos or len(videos) != len(set(map(str, videos))):
        raise ValueError("videos must be a nonempty list of unique filenames")
    normalized_stems: set[str] = set()
    for filename in videos:
        if not isinstance(filename, str) or Path(filename).name != filename or Path(filename).suffix.lower() not in _VIDEO_SUFFIXES:
            raise ValueError(f"invalid video filename: {filename!r}")
        normalized_stem = Path(filename).stem.lower().replace("_", "-")
        if normalized_stem in normalized_stems:
            raise ValueError(f"duplicate video stem: {filename}")
        normalized_stems.add(normalized_stem)
        video = root / "videos" / filename
        if not video.is_file() or video.stat().st_size == 0:
            raise ValueError(f"video missing or empty: {video}")
    hit_ks = spec.get("hit_ks", [1, 3, 5, 10] if task == "segment" else [1, 5, 10])
    if not isinstance(hit_ks, list) or not hit_ks or any(type(k) is not int or k < 1 for k in hit_ks):
        raise ValueError("hit_ks must contain positive integers")
    DATASETS[name] = paths
    DATASET_META[name] = DatasetMeta(task=task, hit_ks=tuple(hit_ks))
    return spec
