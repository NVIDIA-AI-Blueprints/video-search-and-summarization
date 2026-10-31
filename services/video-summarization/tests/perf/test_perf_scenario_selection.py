# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Scenario selection for EngSQA single_file_test + file_burst_test."""

from pathlib import Path

import yaml


def _load_benchmark_config():
    config_path = Path(__file__).resolve().parents[2] / "perf" / "configs" / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def test_engsqa_scenarios_exist():
    scenarios = _load_benchmark_config()["test_scenarios"]
    assert "single_file_test" in scenarios
    assert "file_burst_test" in scenarios


def test_engsqa_scenario_modes():
    scenarios = _load_benchmark_config()["test_scenarios"]
    assert scenarios["single_file_test"]["benchmark_mode"] == "single_file"
    assert scenarios["file_burst_test"]["benchmark_mode"] == "file_burst"


def test_multi_scenario_argparse_names():
    """Names passed as separate --scenario args must all resolve in config."""
    selected = ["single_file_test", "file_burst_test"]
    scenarios = _load_benchmark_config()["test_scenarios"]
    invalid = [name for name in selected if name not in scenarios]
    assert invalid == []
