# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Strict VideoMME-v2 Parquet loader and converter."""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from benchmark.domain import (
    BenchmarkCase,
    BenchmarkDataset,
    BenchmarkGroup,
    Choice,
    ChoiceAnswer,
    GroupStructure,
    GroupType,
    MultipleChoiceTask,
    VideoReference,
)

REQUIRED_COLUMNS = (
    "video_id", "url", "group_type", "group_structure", "question_id", "question",
    "options", "answer", "level", "second_head", "third_head",
)
OPTION_RE = re.compile(r"^([A-H])\.\s+(.+)$")
SUPPORTED_LOGIC_STRUCTURES: tuple[GroupStructure, ...] = (
    [1, 2, 3, 4],
    [1, [2, 3], 4],
    [[1, 2], 3, 4],
)


class VideoMMEv2FormatError(ValueError):
    pass


def _group_structure(
    raw: str,
    *,
    video_id: str,
    group_type: GroupType,
) -> GroupStructure:
    try:
        structure = ast.literal_eval(raw)
    except (SyntaxError, ValueError) as exc:
        raise VideoMMEv2FormatError(
            f"video {video_id}: invalid group_structure {raw!r}"
        ) from exc

    is_list_structure = (
        isinstance(structure, list)
        and bool(structure)
        and all(
            type(item) is int
            or (
                isinstance(item, list)
                and bool(item)
                and all(type(child) is int for child in item)
            )
            for item in structure
        )
    )
    if not is_list_structure:
        raise VideoMMEv2FormatError(
            f"video {video_id}: invalid group_structure {raw!r}"
        )
    if group_type is GroupType.LOGIC and structure not in SUPPORTED_LOGIC_STRUCTURES:
        raise VideoMMEv2FormatError(
            f"video {video_id}: unsupported logic group_structure {structure}"
        )
    return structure


def _choices(raw: str, question_id: str) -> tuple[Choice, ...]:
    choices: list[Choice] = []
    for line in raw.splitlines():
        match = OPTION_RE.fullmatch(line.strip())
        if not match:
            raise VideoMMEv2FormatError(f"{question_id}: invalid option line {line!r}")
        choices.append(Choice(match.group(1), match.group(2)))
    if not 2 <= len(choices) <= 8:
        raise VideoMMEv2FormatError(f"{question_id}: expected 2-8 options")
    if [choice.label for choice in choices] != [chr(ord("A") + index) for index in range(len(choices))]:
        raise VideoMMEv2FormatError(f"{question_id}: option labels must be consecutive from A")
    return tuple(choices)


def load_video_mme_v2(path: Path) -> BenchmarkDataset:
    table = pq.read_table(path)
    missing = set(REQUIRED_COLUMNS) - set(table.column_names)
    extra = set(table.column_names) - set(REQUIRED_COLUMNS)
    if missing or extra:
        raise VideoMMEv2FormatError(f"invalid columns; missing={sorted(missing)}, extra={sorted(extra)}")
    non_string = [field.name for field in table.schema if not pa.types.is_string(field.type)]
    if non_string:
        raise VideoMMEv2FormatError(f"columns must use native Parquet string type: {non_string}")

    rows = table.select(REQUIRED_COLUMNS).to_pylist()
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    order: list[str] = []
    closed_video_ids: set[str] = set()
    previous_video_id: str | None = None
    for row_number, row in enumerate(rows, 1):
        if any(value is None for value in row.values()):
            raise VideoMMEv2FormatError(f"row {row_number}: null values are not allowed")
        video_id = str(row["video_id"])
        if video_id != previous_video_id:
            if previous_video_id is not None:
                closed_video_ids.add(previous_video_id)
            if video_id in closed_video_ids:
                raise VideoMMEv2FormatError(f"video {video_id}: its four rows must be consecutive")
            previous_video_id = video_id
        if video_id not in grouped:
            order.append(video_id)
        grouped[video_id].append({key: str(value) for key, value in row.items()})

    groups: list[BenchmarkGroup] = []
    for video_id in order:
        group_rows = grouped[video_id]
        if len(group_rows) != 4:
            raise VideoMMEv2FormatError(f"video {video_id}: expected exactly four questions")
        shared = {(row["url"], row["group_type"], row["group_structure"]) for row in group_rows}
        if len(shared) != 1:
            raise VideoMMEv2FormatError(f"video {video_id}: inconsistent group metadata")
        url, raw_type, raw_structure = shared.pop()
        try:
            group_type = GroupType(raw_type)
        except ValueError as exc:
            raise VideoMMEv2FormatError(f"video {video_id}: invalid group_type {raw_type!r}") from exc
        structure = _group_structure(
            raw_structure,
            video_id=video_id,
            group_type=group_type,
        )

        cases = []
        for ordinal, row in enumerate(group_rows, 1):
            expected_id = f"{video_id}-{ordinal}"
            if row["question_id"] != expected_id:
                raise VideoMMEv2FormatError(
                    f"video {video_id}: expected question_id {expected_id}, got {row['question_id']}"
                )
            choices = _choices(row["options"], row["question_id"])
            labels = {choice.label for choice in choices}
            if row["answer"] not in labels:
                raise VideoMMEv2FormatError(f"{row['question_id']}: answer is not an option")
            task = MultipleChoiceTask(
                case_id=row["question_id"],
                question=row["question"],
                choices=choices,
                attributes={
                    "level": row["level"],
                    "second_head": row["second_head"],
                    "third_head": row["third_head"],
                },
            )
            cases.append(
                BenchmarkCase(task, ChoiceAnswer(label=row["answer"]))
            )
        groups.append(BenchmarkGroup(video_id, VideoReference(video_id, url), group_type, structure, tuple(cases)))
    return BenchmarkDataset(tuple(groups))
