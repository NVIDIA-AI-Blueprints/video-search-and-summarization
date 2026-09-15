######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
######################################################################################################

"""Shared VLM API request helpers for perf benchmarks."""

import copy
import os
from typing import Any, Dict, List, Tuple

GENERATE_CAPTIONS_API = "generate_captions"
CHAT_COMPLETIONS_API = "chat_completions"

_GENERATE_ALIASES = {
    "generate_captions",
    "generate-caption",
    "generate-captions",
    "captions",
    "/generate_captions",
    "/v1/generate_captions",
}
_CHAT_ALIASES = {
    "chat_completions",
    "chat-completions",
    "chat_completion",
    "chat-completion",
    "chat/completions",
    "/chat/completions",
    "/v1/chat/completions",
    "openai_chat_completions",
}

_COMMON_OPTIONAL_FIELDS = [
    "enable_audio",
    "vlm_input_width",
    "vlm_input_height",
    "chunk_overlap_duration",
    "num_frames_per_second_or_fixed_frames_chunk",
    "use_fps_for_chunking",
    "top_p",
    "top_k",
    "seed",
    "enable_reasoning",
    "ignore_eos",
    "min_tokens",
    "mm_processor_kwargs",
    "media_io_kwargs",
]


def resolve_vlm_api_mode(video_config: Dict, benchmark_config: Dict) -> str:
    """Resolve which VLM generation API a benchmark should call."""
    raw_mode = (
        video_config.get("vlm_api_mode")
        or video_config.get("vlm_api")
        or benchmark_config.get("vlm_api_mode")
        or benchmark_config.get("vlm_api")
        or os.environ.get("RTVI_VLM_API_MODE")
        or GENERATE_CAPTIONS_API
    )
    mode = str(raw_mode).strip().lower()
    if mode in _GENERATE_ALIASES:
        return GENERATE_CAPTIONS_API
    if mode in _CHAT_ALIASES:
        return CHAT_COMPLETIONS_API
    raise ValueError(
        f"Unsupported VLM API mode '{raw_mode}'. "
        f"Use '{GENERATE_CAPTIONS_API}' or '{CHAT_COMPLETIONS_API}'."
    )


def _normalize_endpoint(endpoint: str, default: str) -> str:
    endpoint = str(endpoint or default).strip() or default
    if endpoint.startswith("/v1/"):
        endpoint = endpoint[3:]
    if not endpoint.startswith("/"):
        endpoint = f"/{endpoint}"
    return endpoint


def resolve_vlm_endpoint(video_config: Dict, benchmark_config: Dict, api_mode: str) -> str:
    """Resolve the endpoint path without the /v1 API prefix."""
    if api_mode == CHAT_COMPLETIONS_API:
        endpoint = (
            video_config.get("chat_completions_endpoint")
            or benchmark_config.get("chat_completions_endpoint")
            or os.environ.get("RTVI_VLM_CHAT_COMPLETIONS_ENDPOINT")
            or "/chat/completions"
        )
        return _normalize_endpoint(endpoint, "/chat/completions")

    endpoint = (
        video_config.get("generate_captions_endpoint")
        or benchmark_config.get("generate_captions_endpoint")
        or os.environ.get("RTVI_VLM_GENERATE_CAPTIONS_ENDPOINT")
        or "/generate_captions"
    )
    return _normalize_endpoint(endpoint, "/generate_captions")


def vlm_params_key(video_config: Dict, benchmark_config: Dict) -> str:
    """Return the video-level parameter key for the selected VLM API."""
    if resolve_vlm_api_mode(video_config, benchmark_config) == CHAT_COMPLETIONS_API:
        return "chat_completions_params"
    return "generate_captions_params"


def merge_vlm_params(merge_func, video_config: Dict, benchmark_config: Dict) -> Dict:
    """Merge global/scenario params with video-level params for the selected VLM API."""
    api_mode = resolve_vlm_api_mode(video_config, benchmark_config)
    base_params = benchmark_config.get("api_params", {})
    if api_mode == CHAT_COMPLETIONS_API and benchmark_config.get("chat_completions_params"):
        if benchmark_config.get("chat_completions_params_inherited", False):
            base_params = merge_func(base_params, benchmark_config["chat_completions_params"])
        else:
            base_params = merge_func(benchmark_config["chat_completions_params"], base_params)

    video_params = video_config.get(vlm_params_key(video_config, benchmark_config))
    if video_params is None and api_mode == CHAT_COMPLETIONS_API:
        # Backward-compatible shorthand: existing configs can switch APIs without
        # renaming their generate_captions_params blocks.
        video_params = video_config.get("generate_captions_params", {})
    return merge_func(video_params or {}, base_params)


def _copy_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [copy.deepcopy(message) for message in messages]


def build_chat_messages(video_config: Dict, benchmark_config: Dict, params: Dict) -> List[Dict]:
    """Build OpenAI-compatible messages for /chat/completions."""
    configured_messages = (
        video_config.get("chat_messages")
        or video_config.get("messages")
        or params.get("chat_messages")
        or params.get("messages")
        or benchmark_config.get("chat_messages")
        or benchmark_config.get("messages")
    )
    if configured_messages:
        return _copy_messages(configured_messages)

    system_prompt = (
        video_config.get("system_prompt")
        or params.get("system_prompt")
        or benchmark_config.get("system_prompt")
        or ""
    )
    prompt = (
        video_config.get("prompt")
        or params.get("prompt")
        or benchmark_config.get("prompt")
        or "Describe the video."
    )

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return messages


def build_vlm_generation_request(
    video_config: Dict,
    benchmark_config: Dict,
    params: Dict,
    model_name: str,
    asset_id: str,
    chunk_size: int,
    stream: bool,
) -> Tuple[str, str, Dict[str, Any], str]:
    """Build an RTVI VLM request for generate_captions or chat_completions.

    Returns:
        Tuple of (api_mode, endpoint, request_data, response_label).
    """
    api_mode = resolve_vlm_api_mode(video_config, benchmark_config)
    endpoint = resolve_vlm_endpoint(video_config, benchmark_config, api_mode)

    request_data: Dict[str, Any] = {
        "id": [asset_id],
        "model": model_name,
        "stream": stream,
        "chunk_duration": chunk_size,
    }
    if stream:
        request_data["stream_options"] = {"include_usage": True}

    response_format = params.get("response_format", {"type": "text"})
    if response_format is not None:
        request_data["response_format"] = response_format

    for key in _COMMON_OPTIONAL_FIELDS:
        if key in params:
            request_data[key] = params[key]

    if "chunk_overlap_duration" in video_config:
        request_data["chunk_overlap_duration"] = video_config["chunk_overlap_duration"]

    if api_mode == CHAT_COMPLETIONS_API:
        request_data["messages"] = build_chat_messages(video_config, benchmark_config, params)
        if "temperature" in params:
            request_data["temperature"] = params["temperature"]
        if "max_completion_tokens" in params:
            request_data["max_completion_tokens"] = params["max_completion_tokens"]
        elif "max_tokens" in params:
            request_data["max_tokens"] = params["max_tokens"]
        return api_mode, endpoint, request_data, "chat_completions"

    if "temperature" in params:
        request_data["temperature"] = params["temperature"]
    if "max_tokens" in params:
        request_data["max_tokens"] = params["max_tokens"]

    video_prompt = video_config.get("prompt", "")
    global_prompt = benchmark_config.get("prompt", "")
    if video_prompt or global_prompt:
        request_data["prompt"] = video_prompt or global_prompt

    system_prompt = video_config.get("system_prompt") or benchmark_config.get("system_prompt")
    if system_prompt:
        request_data["system_prompt"] = system_prompt

    return api_mode, endpoint, request_data, "generate_captions"
