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

"""``ground-truth-v1.0`` and ``split-v2`` for V2, read through V1's own readers and pinned by the registry.

V2 never re-implements the ground-truth format: :func:`load_gt_v1` resolves the directory (every file's SHA-256
checked against the registry), then calls V1's ``eval_gt_schema.load_frozen_gt_index``, which verifies the files
against ``MANIFEST.json`` and refuses anything not frozen. The registry also pins the manifest's
``records_sha256``, so a re-frozen directory with new content cannot pass under the old id.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from evalv2.datasets.registry import EVAL_DIR, DatasetError, resolve

GT_V1 = "ground-truth-v1.0"
SPLIT_V2 = "split-v2"
HALVES = ("dev", "test")


def _frozen_gt_reader() -> Any:
    scripts = str(EVAL_DIR / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import eval_gt_schema

    return eval_gt_schema.load_frozen_gt_index


def load_gt_v1(*, data_root: Path | str | None = None, registry: dict[str, Any] | None = None,
               reader: Any = None) -> dict[str, Any]:
    """V1's frozen ground-truth index (``by_incident_id``, ``by_category``, ``split``, ``manifest``), verified.

    ``reader`` replaces V1's loader in tests; it receives the verified directory.
    """
    dataset = resolve(GT_V1, data_root_path=data_root, registry=registry)
    index: dict[str, Any] = (reader or _frozen_gt_reader())(dataset.path)
    manifest = index.get("manifest") or {}
    if manifest.get("files") != dataset.files:
        raise DatasetError(f"{GT_V1}: MANIFEST.json files differ from the registry's pinned hashes")
    pinned = dataset.entry.get("records_sha256")
    if pinned and manifest.get("records_sha256") != pinned:
        raise DatasetError(f"{GT_V1}: MANIFEST.json records_sha256 {manifest.get('records_sha256')} is not the "
                           f"registered {pinned}")
    return index


def load_gt_v1_sheets(*, data_root: Path | str | None = None,
                      registry: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    """The frozen ground truth's workbook-shaped sheets (for V1's scoring store), verified like :func:`load_gt_v1`."""
    load_gt_v1(data_root=data_root, registry=registry)
    dataset = resolve(GT_V1, data_root_path=data_root, registry=registry)
    if str(EVAL_DIR) not in sys.path:
        sys.path.insert(0, str(EVAL_DIR))
    import ground_truth_freeze

    sheets: dict[str, list[dict[str, Any]]] = ground_truth_freeze.read_frozen(dataset.path)["sheets"]
    return sheets


def load_split_v2(*, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """The committed ``split-v2``, verified: counts as registered, halves disjoint, categories cover both halves."""
    dataset = resolve(SPLIT_V2, registry=registry)
    split: dict[str, Any] = json.loads(dataset.path.read_text(encoding="utf-8"))
    problems = split_problems(split, dataset.entry.get("counts"))
    if problems:
        raise DatasetError(f"{SPLIT_V2}: " + "; ".join(problems))
    return split


def split_problems(split: dict[str, Any], counts: dict[str, int] | None = None) -> list[str]:
    problems: list[str] = []
    dev, test = set(split.get("dev", [])), set(split.get("test", []))
    if counts and (len(dev), len(test)) != (counts["dev"], counts["test"]):
        problems.append(f"{len(dev)} dev / {len(test)} test, registered {counts['dev']} / {counts['test']}")
    if dev & test:
        problems.append(f"in both halves: {sorted(dev & test)}")
    by_category = split.get("by_category", {})
    for half, members in (("dev", dev), ("test", test)):
        listed = {clip for category in by_category.values() for clip in category.get(half, [])}
        if by_category and listed != members:
            problems.append(f"by_category {half} differs from {half}: {sorted(listed ^ members)}")
    return problems


def consistency_with_ground_truth(split: dict[str, Any], gt_split: dict[str, Any]) -> list[str]:
    """The committed split against the split frozen with the ground truth: equal once exclusions are removed."""
    excluded = set(gt_split.get("excluded", []))
    problems = []
    for half in HALVES:
        committed = [clip for clip in split[half] if clip not in excluded]
        if sorted(committed) != sorted(gt_split[half]):
            problems.append(f"{half} differs from the ground truth's split-v2")
    return problems


def half(split: dict[str, Any], name: str, *, gt_split: dict[str, Any] | None = None) -> list[str]:
    """The clips of one half, sorted; the ground truth's exclusions removed when its split is given."""
    if name not in HALVES:
        raise DatasetError(f"unknown split half {name!r}; expected one of {HALVES}")
    excluded = set((gt_split or {}).get("excluded", []))
    return sorted(clip for clip in split[name] if clip not in excluded)
