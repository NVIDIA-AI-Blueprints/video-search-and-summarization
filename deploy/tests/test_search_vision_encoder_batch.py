# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Keep cold SigLIP engine builds within the shared search GPU budget."""

import configparser
from pathlib import Path
from textwrap import dedent

import pytest

DEPLOY = Path(__file__).resolve().parents[1]
CONFIGS = (
    DEPLOY / "docker/developer-profiles/dev-profile-search/"
    "video-analytics-2d-app/deepstream/configs"
)


@pytest.mark.parametrize("name", ["ds-main-config.txt", "ds-main-redis-config.txt"])
def test_search_vision_encoder_batch_matches_engine(name):
    config = configparser.ConfigParser(strict=False, interpolation=None)
    config.read(CONFIGS / name)
    # A batch-128 cold build exhausted memory alongside RT-VLM on the L40.
    assert config["visionencoder"].getint("batch-size") == 16
    assert config["visionencoder"]["tensorrt-engine"].endswith("_batch16.plan")


def test_helm_search_vision_encoder_matches_compose():
    chart = DEPLOY / "helm/developer-profiles/dev-profile-search/templates"
    template = (chart / "vss-rtvi-cv-configmap.yaml").read_text()
    section = template.split("    [visionencoder]", 1)[1].split("    [secondary-gie0]", 1)[0]
    config = configparser.ConfigParser(strict=False, interpolation=None)
    config.read_string("[visionencoder]\n" + dedent(section))
    assert config["visionencoder"].getint("batch-size") == 16
