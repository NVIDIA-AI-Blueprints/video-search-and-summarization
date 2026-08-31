# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the NAT-free ``vss vlm`` QA benchmark (no live VLM)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark_vlm_qa import is_qa_item
from benchmark_vlm_qa import judge_completions_url
from benchmark_vlm_qa import latency_stats
from benchmark_vlm_qa import parse_score
from benchmark_vlm_qa import parse_vlm_stdout
from benchmark_vlm_qa import select_qa_items
from benchmark_vlm_qa import strip_think_tags


def test_parse_vlm_stdout_skips_completion_marker() -> None:
    stdout = (
        json.dumps({"job_id": "vlm-1", "status": "completed", "answer": "Yes, PPE is worn."})
        + "\n"
        + json.dumps({"event": "vss_job_completed", "job_id": "vlm-1", "status": "completed", "exit_hint": 0})
        + "\n"
    )
    assert parse_vlm_stdout(stdout)["answer"] == "Yes, PPE is worn."


def test_parse_vlm_stdout_rejects_marker_only() -> None:
    stdout = json.dumps({"event": "vss_job_completed", "job_id": "vlm-1", "status": "completed"})
    with pytest.raises(ValueError, match="no JSON object"):
        parse_vlm_stdout(stdout)


def test_parse_score_uses_last_line() -> None:
    text = "The answers match semantically.\n1.0\n"
    score, reasoning = parse_score(text)
    assert score == 1.0
    assert "semantically" in reasoning


def test_parse_score_strips_think_blocks() -> None:
    text = "<think>ponder</think>\n0.8\n"
    score, _reasoning = parse_score(text)
    assert score == 0.8


def test_parse_score_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="could not extract"):
        parse_score("the score is 12")


def test_strip_think_tags() -> None:
    assert strip_think_tags("<agent-think>plan</agent-think>Yes") == "Yes"


def test_is_qa_item_skips_report_and_trajectory() -> None:
    assert is_qa_item({"evaluation_method": ["qa"], "ground_truth": "Yes"})
    assert not is_qa_item({"evaluation_method": ["trajectory"], "ground_truth": "Yes"})
    assert not is_qa_item({"evaluation_method": ["report"], "ground_truth": "gt/dev_base_001_report.json"})
    assert not is_qa_item({"evaluation_method": ["qa"], "ground_truth": ""})


def test_select_qa_items_matches_video_stem(tmp_path: Path) -> None:
    video = tmp_path / "dev_base_001.mp4"
    video.write_bytes(b"not-a-real-video")
    items = [
        {
            "id": "qa_001",
            "query": "Did a worker drop any boxes in the video dev_base_001?",
            "ground_truth": "Yes, one box.",
            "evaluation_method": ["qa", "trajectory"],
        },
        {
            "id": "rep_001",
            "query": "Generate a report for the video dev_base_001",
            "ground_truth": "gt/dev_base_001_report.json",
            "evaluation_method": ["report"],
        },
    ]
    selected = select_qa_items(items, {video.stem: video, video.name: video})
    assert [item.id for item in selected] == ["qa_001"]
    assert selected[0].video_path == video


def test_select_qa_items_honors_explicit_video_field(tmp_path: Path) -> None:
    video = tmp_path / "warehouse.mp4"
    video.write_bytes(b"x")
    items = [
        {
            "id": "qa_002",
            "query": "What color is the vest?",
            "ground_truth": "Orange",
            "evaluation_method": ["qa"],
            "video_name": "warehouse",
        }
    ]
    selected = select_qa_items(items, {video.stem: video, video.name: video})
    assert selected[0].video_path == video


def test_judge_url_normalizes_base() -> None:
    assert judge_completions_url("http://llm:8000") == "http://llm:8000/v1/chat/completions"
    assert judge_completions_url("http://llm:8000/v1") == "http://llm:8000/v1/chat/completions"
    assert judge_completions_url("http://llm:8000/v1/chat/completions") == "http://llm:8000/v1/chat/completions"


def test_latency_stats() -> None:
    stats = latency_stats([10.0, 20.0, 30.0, 40.0])
    assert stats["n"] == 4
    assert stats["mean"] == 25.0
    assert stats["p50"] == 25.0
    assert stats["min"] == 10.0
    assert stats["max"] == 40.0
