# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Side-effect-free validation shared by Embed preflight and execution."""

from pathlib import Path

import yaml


DEFAULTS_PATH = Path(__file__).resolve().parents[1] / "configs/defaults.yaml"
API_SECTIONS = ("generate_captions_params", "generate_video_embeddings_params")


def load_defaults(path=None):
    path = Path(path) if path is not None else DEFAULTS_PATH
    if not path.is_file():
        raise FileNotFoundError(f"Required benchmark defaults file not found: {path}")
    try:
        defaults = yaml.safe_load(path.read_text())
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid YAML in benchmark defaults: {path}") from error
    if not isinstance(defaults, dict):
        raise ValueError(f"Benchmark defaults must be a mapping: {path}")
    for section in API_SECTIONS:
        if not isinstance(defaults.get(section), dict):
            raise ValueError(f"Benchmark defaults require mapping '{section}': {path}")
    return defaults


def validate_benchmark_config(config, scenario=None):
    """Validate inputs without initializing monitoring, GPUs or HTTP clients."""
    load_defaults()
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a dictionary")
    for section in ("global", "test_scenarios"):
        if not isinstance(config.get(section), dict):
            raise ValueError(f"Configuration requires mapping '{section}'")
    global_config = config["global"]
    if "vlm_gpus" not in global_config:
        raise ValueError("Missing required global config field: vlm_gpus")
    for section in (*API_SECTIONS, "chat_completions_params"):
        if section in global_config and not isinstance(global_config[section], dict):
            raise ValueError(f"global.{section} must be a mapping")
    scenarios = config["test_scenarios"]
    if scenario is not None and scenario not in scenarios:
        raise ValueError(f"Scenario '{scenario}' not found in configuration")
    selected = {scenario: scenarios[scenario]} if scenario is not None else scenarios
    for name, settings in selected.items():
        if not isinstance(settings, dict):
            raise ValueError(f"Scenario '{name}' must be a mapping")
