# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from benchmark.spec import BenchmarkSpec, SetupStep
from pydantic import ValidationError


def test_setup_step_defaults_to_ten_minute_agent_timeout() -> None:
    step = SetupStep(name="Deploy", query="Deploy LVS.", checks=("LVS is healthy.",))

    assert step.inputs == ()
    assert step.agent_timeout_sec == 600


def test_setup_step_accepts_declarative_inputs_and_timeout() -> None:
    step = SetupStep(
        name="Summarize",
        query="Summarize every supplied video.",
        checks=("Every summary persisted.",),
        inputs=("dataset_video_ids", "summarization_config"),
        agent_timeout_sec=1800,
    )

    assert step.inputs == ("dataset_video_ids", "summarization_config")
    assert step.agent_timeout_sec == 1800


def test_setup_step_rejects_duplicate_inputs() -> None:
    with pytest.raises(ValidationError, match="setup inputs must be unique"):
        SetupStep(
            name="Summarize",
            query="Summarize every supplied video.",
            checks=("Every summary persisted.",),
            inputs=("dataset_video_ids", "dataset_video_ids"),
        )


def test_benchmark_spec_rejects_removed_memory_contract() -> None:
    with pytest.raises(ValidationError):
        BenchmarkSpec.model_validate(
            {
                "agent": {"name": "openclaw"},
                "skills": ["benchmark-unified-memory", "vss-ask-video"],
                "resources": {"platforms": {"RTXPRO6000BW": {"gpu_count": 1}}},
                "setup": [
                    {"name": str(index), "query": "query", "checks": ["check"]}
                    for index in range(3)
                ],
                "dataset": {"path": "data.parquet", "format": "video-mme-v2"},
                "memory": {"directory": "memory"},
                "scoring": {"metric": "overall_group_score", "minimum": 0.75},
            }
        )
