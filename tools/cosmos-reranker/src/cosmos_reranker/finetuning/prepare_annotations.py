# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared PAS row parsing and metadata relevance labels."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping


PROMPT = (
    'Query: "{query}"\n'
    "Does the person in the image fully match the query? "
    "Answer with <answer>yes</answer> or <answer>no</answer>."
)


@dataclass(frozen=True)
class Pair:
    dataset: str
    query_type: str
    caption: str
    unique_name: str
    image_path: str
    image_attr_values: tuple[int, ...]
    text_attr_values: tuple[int, ...]
    image_accessory_ids: tuple[int, ...]
    text_accessory_ids: tuple[int, ...]

    @property
    def image_key(self) -> str:
        return f"{self.dataset}\t{self.image_path}"


def iter_json_records(path: Path) -> Iterator[dict]:
    """Read either a JSON array or the PAS exporter's compact-line array."""

    with path.open(encoding="utf-8") as handle:
        handle.readline()
        second = handle.readline()
    compact = second.lstrip().startswith("{") and second.rstrip().rstrip(",").endswith("}")
    if compact:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                value = line.strip()
                if not value or value in {"[", "]"}:
                    continue
                yield json.loads(value.removesuffix(","))
        return
    with path.open(encoding="utf-8") as handle:
        yield from json.load(handle)


def as_pair(row: Mapping) -> Pair:
    return Pair(
        dataset=str(row.get("dataset") or ""),
        query_type=str(row.get("query_type") or ""),
        caption=str(row.get("caption") or ""),
        unique_name=str(row.get("unique_name") or ""),
        image_path=str(row.get("image_path") or ""),
        image_attr_values=tuple(int(value) for value in row.get("image_attr_values") or ()),
        text_attr_values=tuple(int(value) for value in row.get("text_attr_values") or ()),
        image_accessory_ids=tuple(int(value) for value in row.get("image_accessory_ids") or ()),
        text_accessory_ids=tuple(int(value) for value in row.get("text_accessory_ids") or ()),
    )


def cache_image_names(pairs_file: Path) -> list[str]:
    """Return first-seen exported image names used by retrieval and training."""
    from cosmos_reranker.evaluation.generate_embeddings import image_key

    names = {}
    for index, row in enumerate(iter_json_records(pairs_file)):
        name = str(row.get("unique_name") or "").strip()
        if not name:
            raise ValueError(f"Pair row {index} has no unique_name")
        names.setdefault(image_key(row), name)
    return list(names.values())


def metadata_match(query: Pair, candidate: Pair, *, accessories: bool) -> bool:
    """Return the PAS relevance label for one query/candidate pair."""

    if not query.text_attr_values or len(query.text_attr_values) != len(
        candidate.image_attr_values
    ):
        return False
    if not all(
        wanted < 0 or wanted == actual
        for wanted, actual in zip(query.text_attr_values, candidate.image_attr_values, strict=True)
    ):
        return False
    return not accessories or set(query.text_accessory_ids).issubset(candidate.image_accessory_ids)
