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

"""The datasets one run config names, resolved and verified, as the ``datasets`` argument of every task's plan.

The result always holds the verified split, the config's half and its clips (the ground truth's exclusions
removed when a ground truth is loaded), each named dataset under its role, and ``inputs``: every file's SHA-256
keyed ``<dataset>/<file>``, ready for the run manifest.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evalv2.datasets.ground_truth import GT_V1, SPLIT_V2, consistency_with_ground_truth, half, load_gt_v1, load_split_v2
from evalv2.datasets.records import load_records
from evalv2.datasets.registry import REFERENCE_KINDS, DatasetError, entry, resolve

RECORDS_FILE = "records.jsonl"


def load_for_config(config: Any, *, data_root: Path | str | None = None, registry: dict[str, Any] | None = None,
                    gt_reader: Any = None) -> dict[str, Any]:
    """``{"split", "half", "clips", <role>: ..., "inputs"}`` for ``config`` (a ``RunConfig``)."""
    if config.split.version != SPLIT_V2:
        raise DatasetError(f"only {SPLIT_V2} is registered; the config names {config.split.version!r}")
    split = load_split_v2(registry=registry)
    split_entry = entry(SPLIT_V2, registry)
    inputs = {f"{SPLIT_V2}/{name}": digest for name, digest in split_entry["files"].items()}
    out: dict[str, Any] = {"split": split, "half": config.split.half}
    gt_split: dict[str, Any] | None = None
    for role, dataset_id in sorted(config.datasets.items()):
        item = entry(dataset_id, registry)
        if item["kind"] == "ground_truth":
            if dataset_id != GT_V1:
                raise DatasetError(f"no reader for ground truth {dataset_id!r}")
            index = load_gt_v1(data_root=data_root, registry=registry, reader=gt_reader)
            gt_split = index["split"]
            out[role] = index
        elif item["kind"] in REFERENCE_KINDS:
            dataset = resolve(dataset_id, data_root_path=data_root, registry=registry)
            records = load_records(dataset.file(RECORDS_FILE))
            out[role] = {"dataset": dataset_id, "kind": item["kind"], "records": records}
        else:
            dataset = resolve(dataset_id, data_root_path=data_root, registry=registry)
            out[role] = json.loads(dataset.path.read_text(encoding="utf-8")) if dataset.path.is_file() else dataset
        inputs.update({f"{dataset_id}/{name}": digest for name, digest in item["files"].items()})
    if gt_split is not None:
        problems = consistency_with_ground_truth(split, gt_split)
        if problems:
            raise DatasetError(f"{SPLIT_V2}: " + "; ".join(problems))
    out["clips"] = half(split, config.split.half, gt_split=gt_split)
    out["inputs"] = dict(sorted(inputs.items()))
    return out
