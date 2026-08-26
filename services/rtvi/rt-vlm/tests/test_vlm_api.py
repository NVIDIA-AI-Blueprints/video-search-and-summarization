######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

# isort: skip_file

import sys
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parents[1] / "perf" / "benchmark"
sys.path.insert(0, str(BENCHMARK_DIR))

from vlm_api import (  # noqa: E402
    CHAT_COMPLETIONS_API,
    GENERATE_CAPTIONS_API,
    build_vlm_generation_request,
    merge_vlm_params,
    resolve_vlm_api_mode,
    resolve_vlm_endpoint,
)


def test_default_vlm_api_mode_uses_generate_captions(monkeypatch):
    monkeypatch.delenv("RTVI_VLM_API_MODE", raising=False)

    assert resolve_vlm_api_mode({}, {}) == GENERATE_CAPTIONS_API
    assert resolve_vlm_endpoint({}, {}, GENERATE_CAPTIONS_API) == "/generate_captions"


def test_chat_completion_request_uses_messages_and_endpoint():
    video_config = {"vlm_api_mode": "chat_completions", "prompt": "What is happening?"}
    benchmark_config = {
        "prompt": "Describe the video.",
        "system_prompt": "You are concise.",
        "api_params": {"temperature": 0.1, "max_tokens": 2},
    }

    api_mode, endpoint, request_data, response_label = build_vlm_generation_request(
        video_config=video_config,
        benchmark_config=benchmark_config,
        params={"temperature": 0.1, "max_tokens": 2},
        model_name="test-model",
        asset_id="123e4567-e89b-12d3-a456-426614174000",
        chunk_size=10,
        stream=True,
    )

    assert api_mode == CHAT_COMPLETIONS_API
    assert endpoint == "/chat/completions"
    assert response_label == "chat_completions"
    assert request_data["id"] == ["123e4567-e89b-12d3-a456-426614174000"]
    assert request_data["stream"] is True
    assert request_data["stream_options"] == {"include_usage": True}
    assert request_data["messages"] == [
        {"role": "system", "content": "You are concise."},
        {"role": "user", "content": "What is happening?"},
    ]
    assert request_data["max_tokens"] == 2


def test_chat_completion_params_fall_back_to_generate_caption_video_params():
    video_config = {
        "vlm_api_mode": "chat-completions",
        "generate_captions_params": {"max_tokens": 1},
    }
    benchmark_config = {
        "api_params": {"temperature": 0.7, "max_tokens": 100},
        "chat_completions_params": {"temperature": 0.0},
    }

    merged = merge_vlm_params(
        lambda user, defaults: {**defaults, **user}, video_config, benchmark_config
    )

    assert merged == {"temperature": 0.0, "max_tokens": 1}


def test_chat_completion_inherited_params_keep_scenario_generate_overrides():
    video_config = {"vlm_api_mode": "chat_completions"}
    benchmark_config = {
        "api_params": {"temperature": 0.7, "max_tokens": 1},
        "chat_completions_params": {"temperature": 0.0, "max_tokens": 100},
        "chat_completions_params_inherited": True,
    }

    merged = merge_vlm_params(
        lambda user, defaults: {**defaults, **user}, video_config, benchmark_config
    )

    assert merged == {"temperature": 0.7, "max_tokens": 1}


def test_chat_completion_explicit_params_override_scenario_generate_defaults():
    video_config = {"vlm_api_mode": "chat_completions"}
    benchmark_config = {
        "api_params": {"temperature": 0.7, "max_tokens": 1},
        "chat_completions_params": {"temperature": 0.0, "max_tokens": 100},
        "chat_completions_params_inherited": False,
    }

    merged = merge_vlm_params(
        lambda user, defaults: {**defaults, **user}, video_config, benchmark_config
    )

    assert merged == {"temperature": 0.0, "max_tokens": 100}


def test_generate_caption_request_preserves_legacy_payload_shape():
    api_mode, endpoint, request_data, response_label = build_vlm_generation_request(
        video_config={"prompt": "Summarize."},
        benchmark_config={"api_params": {"temperature": 0.3, "max_tokens": 8}},
        params={"temperature": 0.3, "max_tokens": 8},
        model_name="test-model",
        asset_id="123e4567-e89b-12d3-a456-426614174000",
        chunk_size=10,
        stream=False,
    )

    assert api_mode == GENERATE_CAPTIONS_API
    assert endpoint == "/generate_captions"
    assert response_label == "generate_captions"
    assert request_data == {
        "id": ["123e4567-e89b-12d3-a456-426614174000"],
        "model": "test-model",
        "stream": False,
        "chunk_duration": 10,
        "response_format": {"type": "text"},
        "temperature": 0.3,
        "max_tokens": 8,
        "prompt": "Summarize.",
    }
