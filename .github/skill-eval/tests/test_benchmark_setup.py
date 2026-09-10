# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from benchmark.setup import render_setup_prompt


AVAILABLE_INPUTS = {
    "dataset_video_ids": ("warehouse_sample", "sample-sim-traffic"),
    "summarization_config": {
        "scenario": "Physical AI environment monitoring",
        "events": ["Identify notable actions"],
        "creation_time": "2025-01-01T00:00:00.000Z",
    },
}


def test_render_setup_prompt_projects_only_requested_safe_inputs() -> None:
    prompt = render_setup_prompt(
        preamble="Harness preamble.",
        query="Ingest every supplied video.",
        requested_inputs=("dataset_video_ids",),
        available_inputs=AVAILABLE_INPUTS,
    )

    assert "warehouse_sample" in prompt
    assert "sample-sim-traffic" in prompt
    assert "summarization_config" not in prompt
    assert "question" not in prompt
    assert "answer" not in prompt


def test_render_setup_prompt_includes_summarization_config_when_requested() -> None:
    prompt = render_setup_prompt(
        preamble="Harness preamble.",
        query="Summarize every supplied video.",
        requested_inputs=("dataset_video_ids", "summarization_config"),
        available_inputs=AVAILABLE_INPUTS,
    )

    assert '"scenario": "Physical AI environment monitoring"' in prompt
    assert '"creation_time": "2025-01-01T00:00:00.000Z"' in prompt


def test_render_setup_prompt_rejects_missing_requested_input() -> None:
    with pytest.raises(ValueError, match="unavailable setup inputs"):
        render_setup_prompt(
            preamble="Harness preamble.",
            query="Summarize every supplied video.",
            requested_inputs=("summarization_config",),
            available_inputs={"dataset_video_ids": ("warehouse_sample",)},
        )
