# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from benchmark.video_mme_v2 import VideoMMEv2FormatError, load_video_mme_v2


def _write_dataset(path: Path, group_structure: str) -> None:
    rows = [
        {
            "video_id": "video-1",
            "url": "https://example.invalid/video-1",
            "group_type": "logic",
            "group_structure": group_structure,
            "question_id": f"video-1-{ordinal}",
            "question": f"Question {ordinal}?",
            "options": "A. First\nB. Second",
            "answer": "A",
            "level": "1",
            "second_head": "Frame-Only",
            "third_head": "Introspection",
        }
        for ordinal in range(1, 5)
    ]
    schema = pa.schema((column, pa.string()) for column in rows[0])
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)


def test_converter_parses_group_structure_into_a_list(tmp_path: Path) -> None:
    dataset_path = tmp_path / "questions.parquet"
    _write_dataset(dataset_path, "[1, [2, 3], 4]")

    dataset = load_video_mme_v2(dataset_path)

    assert dataset.groups[0].group_structure == [1, [2, 3], 4]


@pytest.mark.parametrize(
    "group_structure",
    (
        "[1, 2, 4, 3]",
        "not valid Python",
    ),
)
def test_converter_rejects_invalid_logic_structure(
    tmp_path: Path,
    group_structure: str,
) -> None:
    dataset_path = tmp_path / "questions.parquet"
    _write_dataset(dataset_path, group_structure)

    with pytest.raises(VideoMMEv2FormatError, match="group_structure"):
        load_video_mme_v2(dataset_path)
