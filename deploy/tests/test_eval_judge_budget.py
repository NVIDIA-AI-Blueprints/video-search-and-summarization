# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Reasoning judges need room to finish a score after their reasoning output."""

from pathlib import Path

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("runtime", ["docker", "helm"])
@pytest.mark.parametrize("profile", ["base", "lvs"])
@pytest.mark.parametrize("filename", ["config.yml", "config_rag.yml"])
def test_eval_judge_retains_reasoning_with_stock_model_output_budget(
    runtime, profile, filename
):
    root = DEPLOY / runtime / "developer-profiles" / f"dev-profile-{profile}"
    path = (
        root / "vss-agent/configs" / filename
        if runtime == "docker"
        else root / "configs/vss-agent" / filename
    )
    config = yaml.safe_load(path.read_text())
    llms = config["llms"]
    assert (
        llms["eval_llm_judge"]["max_tokens"] == llms["nim_llm"]["max_tokens"] == 32768
    )
    evaluators = config["eval"]["evaluators"]
    names = (
        ("qa_evaluator", "trajectory_evaluator")
        if profile == "base"
        else ("trajectory_evaluator",)
    )
    for name in names:
        assert evaluators[name].get("llm_judge_reasoning", True) is True
        assert evaluators[name]["llm_name"] == "eval_llm_judge"
