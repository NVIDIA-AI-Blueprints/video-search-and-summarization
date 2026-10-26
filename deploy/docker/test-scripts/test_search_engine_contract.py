# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Validate the default search pipeline's engine-cache and precision contract."""

import configparser
import re
from pathlib import Path

import yaml


def test_default_pipeline_engine_matches_inference_batch_and_precision():
    root = (
        Path(__file__).parents[1]
        / "developer-profiles/dev-profile-search/video-analytics-2d-app/deepstream/configs"
    )
    pipeline = configparser.ConfigParser(strict=False)
    pipeline.read(root / "ds-main-config.txt")
    config = yaml.safe_load((root / "ds-pgie-config.yml").read_text())["property"]
    batch = pipeline.getint("primary-gie", "batch-size")
    assert batch == pipeline.getint("streammux", "batch-size")
    engine = Path(config["model-engine-file"]).name
    assert re.search(rf"_b{batch}_gpu{config['gpu-id']}_fp16\.engine$", engine)
    assert engine.startswith(Path(config["onnx-file"]).name)
    assert config["network-mode"] == 2
    # TensorRT rejects explicit FP16 builder flags on a strongly typed network.
    assert not config["strongly-typed"]
