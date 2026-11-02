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

"""The dataset registry: every dataset a V2 run may read, where it lives, and the hashes that pin it.

A run names datasets by id (``split-v2``, ``ground-truth-v1.0``, later the reference sets) and :func:`resolve`
finds them:

- committed datasets (``root: eval``) live under ``eval/``;
- private datasets (``root: data``: ground truth and references, whose text stays local by precedent) live under
  the data root, which is ``--data-root``, else ``EVALV2_DATA_ROOT``, else ``eval/eval_data``.

Resolution checks every pinned file's SHA-256, so an edited file can never be read under an old id. The registry
is append-only (:func:`check_append_only`): a changed dataset is a new id. Only hashes, counts and locations are
committed here, never ground-truth or reference text.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evalv2.core.hashing import sha256_file

EVAL_DIR = Path(__file__).resolve().parents[2]
REGISTRY_PATH = Path(__file__).with_name("registry.json")
DATA_ROOT_ENV = "EVALV2_DATA_ROOT"
DEFAULT_DATA_ROOT = EVAL_DIR / "eval_data"
KINDS = ("clips", "split", "ground_truth", "box_references", "chat_references", "dashboard_references",
         "matching_labels")
REFERENCE_KINDS = frozenset({"box_references", "chat_references", "dashboard_references", "matching_labels"})
ROOTS = ("eval", "data")
ENTRY_KEYS = frozenset({"id", "kind", "description", "location", "files", "verification", "committed", "created"})
OPTIONAL_KEYS = frozenset({"counts", "records_sha256", "parent", "generation", "notes"})
ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9.]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DatasetError(ValueError):
    """An unknown, missing or changed dataset, or an inconsistent registry."""


class DatasetMissingError(DatasetError):
    """A registered dataset (or one of its files) is not on this machine."""


@dataclass(frozen=True)
class ResolvedDataset:
    """A dataset found on disk with every pinned file verified."""

    id: str
    kind: str
    path: Path
    files: dict[str, str]
    entry: dict[str, Any] = field(repr=False)

    def file(self, name: str) -> Path:
        """The path of one pinned file (the dataset itself when it is a single file)."""
        if name not in self.files:
            raise DatasetError(f"{self.id} pins no file named {name!r}")
        return self.path if self.path.is_file() else self.path / name


def data_root(explicit: Path | str | None = None) -> Path:
    """Where private datasets live: the explicit path, else ``EVALV2_DATA_ROOT``, else ``eval/eval_data``."""
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get(DATA_ROOT_ENV, "").strip()
    return Path(env) if env else DEFAULT_DATA_ROOT


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    registry: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    return registry


def validate_registry(registry: dict[str, Any]) -> list[str]:
    """Problems with the registry's shape: ids, kinds, roots, hashes and the committed flag."""
    problems: list[str] = []
    seen: set[str] = set()
    for number, entry in enumerate(registry.get("datasets", []), start=1):
        label = entry.get("id", f"entry {number}")
        missing, extra = ENTRY_KEYS - set(entry), set(entry) - ENTRY_KEYS - OPTIONAL_KEYS
        if missing or extra:
            problems.append(f"{label}: missing keys {sorted(missing)}, unknown keys {sorted(extra)}")
            continue
        if not ID_PATTERN.match(entry["id"]):
            problems.append(f"{label}: id does not match {ID_PATTERN.pattern}")
        if entry["id"] in seen:
            problems.append(f"{label}: duplicate id")
        seen.add(entry["id"])
        if entry["kind"] not in KINDS:
            problems.append(f"{label}: unknown kind {entry['kind']!r}")
        root = entry["location"].get("root")
        if root not in ROOTS or not entry["location"].get("path"):
            problems.append(f"{label}: location needs a root in {ROOTS} and a path")
        if entry["committed"] != (root == "eval"):
            problems.append(f"{label}: committed must be true exactly for root 'eval'")
        if entry["kind"] in ("ground_truth", *REFERENCE_KINDS) and root != "data":
            problems.append(f"{label}: ground truth and references are private (root 'data'), never committed")
        if not entry["files"] or not all(_SHA256.match(str(h)) for h in entry["files"].values()):
            problems.append(f"{label}: files must map each name to a SHA-256")
        if "records_sha256" in entry and not _SHA256.match(str(entry["records_sha256"])):
            problems.append(f"{label}: records_sha256 is not a SHA-256")
        if entry.get("parent") is not None and entry["parent"] not in seen:
            problems.append(f"{label}: parent {entry['parent']!r} is not an earlier entry")
    return problems


def check_append_only(earlier: dict[str, Any], later: dict[str, Any]) -> list[str]:
    """Entries may only be appended: never edited, reordered or removed."""
    old, new = earlier.get("datasets", []), later.get("datasets", [])
    if len(new) < len(old):
        return [f"{len(old) - len(new)} entries removed"]
    pairs = enumerate(zip(old, new, strict=False))
    return [f"entry {i + 1} ({a.get('id')}) was edited or reordered" for i, (a, b) in pairs if a != b]


def entry(dataset_id: str, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    for item in (registry or load_registry())["datasets"]:
        if item["id"] == dataset_id:
            found: dict[str, Any] = item
            return found
    raise DatasetError(f"no dataset {dataset_id!r} in the registry")


def resolve(dataset_id: str, *, data_root_path: Path | str | None = None,
            registry: dict[str, Any] | None = None) -> ResolvedDataset:
    """The dataset on disk with every pinned file's SHA-256 verified; :class:`DatasetError` otherwise."""
    item = entry(dataset_id, registry)
    root = EVAL_DIR if item["location"]["root"] == "eval" else data_root(data_root_path)
    path = root / item["location"]["path"]
    if not path.exists():
        hint = "" if item["committed"] else (f"; private datasets live under the data root (--data-root or "
                                             f"{DATA_ROOT_ENV}), here {root}")
        raise DatasetMissingError(f"{dataset_id}: {path} not found{hint}")
    resolved = ResolvedDataset(dataset_id, item["kind"], path, dict(item["files"]), item)
    for name, pinned in item["files"].items():
        target = resolved.file(name)
        if path.is_file() and target.name != name:
            raise DatasetError(f"{dataset_id}: a single-file dataset must pin its own name, not {name!r}")
        if not target.is_file():
            raise DatasetMissingError(f"{dataset_id}: {target} is missing")
        actual = sha256_file(target)
        if actual != pinned:
            raise DatasetError(f"{dataset_id}: {target.name} changed since it was registered "
                               f"(sha256 {actual}, registry has {pinned})")
    return resolved


def append(new_entry: dict[str, Any], path: Path = REGISTRY_PATH) -> dict[str, Any]:
    """Append one entry after validating the result; refuses anything that is not a pure append."""
    registry = load_registry(path)
    updated = {**registry, "datasets": [*registry["datasets"], new_entry]}
    problems = validate_registry(updated) + check_append_only(registry, updated)
    if problems:
        raise DatasetError("; ".join(problems))
    Path(path).write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return updated
