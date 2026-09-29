# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The custom NemoClaw image must retain the selected local NIM endpoint."""

import importlib.util
import json
import re
from pathlib import Path
from urllib.parse import urlunsplit


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / ".openclaw" / "apply-onboard-config.py"
DOCKERFILE = REPO_ROOT / ".openclaw" / "Dockerfile"


def test_custom_image_preserves_onboarded_inference_endpoint(tmp_path):
    spec = importlib.util.spec_from_file_location("apply_onboard_config", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    config = tmp_path / "openclaw.json"
    config.write_text(json.dumps({
        "agents": {"defaults": {"model": {"primary": "inference/default"}}},
        "models": {"providers": {"inference": {
            "baseUrl": "https://inference.local/v1",
            "models": [{"id": "default", "name": "inference/default"}],
        }}},
    }))
    endpoint = urlunsplit(("http", "10.229.20.2:18410", "/v1", "", ""))
    module.apply(str(config), {
        "NEMOCLAW_MODEL": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "NEMOCLAW_INFERENCE_BASE_URL": endpoint,
    })
    provider = json.loads(config.read_text())["models"]["providers"]["inference"]
    assert provider["baseUrl"] == endpoint
    assert provider["models"][0]["id"] == "nvidia/nemotron-3.5-lightning-30b-a3b"

    dockerfile = DOCKERFILE.read_text()
    first_from = re.search(r"^FROM ", dockerfile, re.M).start()
    first_arg = re.search(r"^ARG NEMOCLAW_INFERENCE_BASE_URL=", dockerfile, re.M)
    assert first_arg and first_arg.start() < first_from
    assert '/onboard/NEMOCLAW_INFERENCE_BASE_URL' in dockerfile
    assert '/etc/vss-onboard-args/NEMOCLAW_INFERENCE_BASE_URL' in dockerfile
