# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for recorded-file and live LVS deployment data paths."""

from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOYMENT_CONFIGS = (
    REPO_ROOT / "deploy/docker/services/video-summarization/configs/config.yaml",
    REPO_ROOT / "deploy/helm/services/video-summarization/configs/config.yaml",
)


class EnvSafeLoader(yaml.SafeLoader):
    """Load test configuration while preserving scalar ``!ENV`` expressions."""


def _construct_env_scalar(loader: EnvSafeLoader, node: yaml.Node) -> str:
    """Return an unresolved ``!ENV`` value as a scalar string for assertions."""
    return loader.construct_scalar(node)


EnvSafeLoader.add_constructor("!ENV", _construct_env_scalar)


@pytest.mark.parametrize("config_path", DEPLOYMENT_CONFIGS)
def test_file_and_live_summarization_use_separate_data_paths(config_path: Path) -> None:
    """Keep file summaries synchronous and live summaries Kafka-backed."""
    config = yaml.load(config_path.read_text(), Loader=EnvSafeLoader)
    functions = config["functions"]

    file_summary = functions["summarization"]
    assert file_summary["type"] == "vlm_structured_summarization"
    assert file_summary["params"]["kafka_enabled"] is False

    live_summary = functions["summarization_online"]
    assert live_summary["type"] == "vlm_structured_summarization_online"
    assert live_summary["params"]["kafka_enabled"] is True
