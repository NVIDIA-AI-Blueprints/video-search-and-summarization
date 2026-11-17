# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Credential selection for Alert Bridge's synchronous and async VLM clients."""

from unittest.mock import patch

import pytest

from vlm.vlm_client import AsyncVLMClient, VLMClient


@pytest.mark.parametrize(
    ("client_type", "openai_factory"),
    [(VLMClient, "OpenAI"), (AsyncVLMClient, "AsyncOpenAI")],
)
@pytest.mark.parametrize(
    ("base_url", "vlm_key", "nvidia_key", "config_key", "expected"),
    [
        ("https://inference-api.nvidia.com/v1", None, None, None, "not-used"),
        ("https://inference-api.nvidia.com/v1", None, "hosted-key", None, "hosted-key"),
        ("https://inference-api.nvidia.com/v1", "vlm-key", "hosted-key", None, "vlm-key"),
        ("https://other.example/v1", "vlm-key", "hosted-key", None, "vlm-key"),
        ("https://other.example/v1", None, "hosted-key", None, "not-used"),
        ("http://rtvi-vlm:8000/v1", None, "hosted-key", None, "not-used"),
        ("http://inference-api.nvidia.com/v1", None, "hosted-key", None, "not-used"),
        ("https://inference-api.nvidia.com.evil.example/v1", None, "hosted-key", None, "not-used"),
        ("https://other.example/v1", None, None, "config-key", "config-key"),
    ],
)
def test_vlm_api_key_selection(
    monkeypatch, client_type, openai_factory, base_url, vlm_key,
    nvidia_key, config_key, expected,
):
    for name, value in (("VLM_API_KEY", vlm_key), ("NVIDIA_API_KEY", nvidia_key)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    config = {"base_url": base_url}
    if config_key is not None:
        config["api_key"] = config_key

    with patch(f"vlm.vlm_client.{openai_factory}") as factory:
        client = client_type(config)

    assert client.api_key == expected
    assert factory.call_args.kwargs["api_key"] == expected
