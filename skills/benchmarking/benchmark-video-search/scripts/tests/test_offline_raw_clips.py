# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Canonical and legacy saved captures must support the same offline scorer."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import score_raw_clips


def fixture_args(tmp_path, monkeypatch, capture, cutoff):
    (tmp_path / "captures").mkdir()

    def write(path, data):
        (tmp_path / path).write_text(json.dumps(data))

    write(
        "captures/q.json",
        {
            "exit_code": 0,
            "data": [{"sensor_id": s} for s in ["b", "c", "a"]],
            **capture,
        },
    )
    write(
        "queries.json",
        {
            "queries": [
                {
                    "query_id": "q",
                    "query_domain": "pas",
                    "relevant_clip_ids": ["a", "c"],
                }
            ]
        },
    )
    write(
        "ingestion.json",
        {"clips": [{"dataset_id": s, "sensor_id": s} for s in ["a", "b", "c"]]},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "score_raw_clips",
            "--queries",
            str(tmp_path / "queries.json"),
            "--ingestion-manifest",
            str(tmp_path / "ingestion.json"),
            "--capture-dir",
            str(tmp_path / "captures"),
            "--output",
            str(tmp_path / "score.json"),
            "--top-k",
            str(cutoff),
        ],
    )


@pytest.mark.parametrize(
    "capture", [{"query_id": "q"}, {"metadata": {"query_id": "q"}}]
)
@pytest.mark.parametrize(("cutoff", "ap"), [(2, 0.25), (3, 7 / 12), (10, 7 / 12)])
def test_rescore_exact_saved_ranking_at_requested_cutoff(
    tmp_path, monkeypatch, capture, cutoff, ap
):
    fixture_args(tmp_path, monkeypatch, capture, cutoff)
    score_raw_clips.main()
    result = json.loads((tmp_path / "score.json").read_text())
    assert result["summary"]["overall"][f"mAP@{cutoff}_percent"] == pytest.approx(
        100 * ap
    )


@pytest.mark.parametrize(
    "capture",
    [{"query_id": "q", "metadata": {"query_id": "other"}}, {"query_id": ""}, {}],
)
def test_bad_capture_identity_cannot_be_silently_scored(tmp_path, monkeypatch, capture):
    fixture_args(tmp_path, monkeypatch, capture, 10)
    with pytest.raises(ValueError):
        score_raw_clips.main()
    assert not (tmp_path / "score.json").exists()
