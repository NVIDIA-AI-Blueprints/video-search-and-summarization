#!/usr/bin/env python3
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

"""Utilities for generating perf config identifiers."""

from __future__ import annotations

import re
from typing import Any, Mapping


def _sanitize_id_field(value: str) -> str:
    """Normalize one ID field: lowercase, keep '-', convert '_' to '-'."""
    text = (value or "").strip().lower()
    text = text.replace("_", "-")
    text = re.sub(r"\s+", "-", text)
    return text


def _is_integrated_vlm(compose_path: str | None) -> bool:
    """Infer whether VLM is integrated from compose filename."""
    return "integrated" in (compose_path or "").lower()


def _vlm_id_segment(vlm_name: str, compose_path: str | None) -> str:
    """Build VLM segment and append '-integrated' when applicable."""
    normalized = _sanitize_id_field(vlm_name)
    if _is_integrated_vlm(compose_path) and not normalized.endswith("-integrated"):
        return f"{normalized}-integrated"
    return normalized


def _first_gpu_model_name(gpu_model: str) -> str:
    """Return the first token from the GPU model and normalize it."""
    first_token = re.split(r"[\s_/]+", (gpu_model or "").strip())[0]
    return _sanitize_id_field(first_token)


def parse_gpu_list(gpu_indices: str | None) -> list[str]:
    """Parse comma-separated GPU indices into a cleaned list."""
    if not gpu_indices:
        return []
    return [idx.strip() for idx in str(gpu_indices).split(",") if idx.strip()]


def infer_total_gpu_count(
    compose_path: str | None, vlm_gpus: list[str], llm_gpus: list[str]
) -> int:
    """Infer total GPU count from compose filename, fallback to distinct configured GPUs."""
    match = re.search(r"_(\d+)gpu(?:_|\.|$)", compose_path or "")
    if match:
        return int(match.group(1))
    return len(set(vlm_gpus + llm_gpus))


def generate_perf_config_id(
    *,
    gpu_model: str,
    total_gpus: int,
    vlm_gpu_count: int,
    llm_gpu_count: int,
    vlm_segment: str,
    llm_name: str,
    vision_input_tokens: str | int,
) -> str:
    """Build ID: gpu_firstname_total_vlmxllm_vlm_llm_visiontokens."""
    token_value = _sanitize_id_field(str(vision_input_tokens))
    digits = "".join(ch for ch in token_value if ch.isdigit())
    if digits:
        token_part = f"{digits}k"
    elif token_value.endswith("k"):
        token_part = token_value
    else:
        token_part = f"{token_value}k" if token_value else "9k"

    parts = [
        _first_gpu_model_name(gpu_model),
        str(total_gpus),
        f"{vlm_gpu_count}x{llm_gpu_count}",
        vlm_segment,
        _sanitize_id_field(llm_name),
        token_part,
    ]
    return "_".join(parts)


def generate_perf_config_id_from_config(
    config: Mapping[str, Any],
    default_vision_input_tokens: str | int = "9k",
) -> str:
    """Generate an ID from one perf config map."""
    vlm_gpus = parse_gpu_list(config.get("vlmGpus"))
    llm_gpus = parse_gpu_list(config.get("llmGpus"))

    total_gpus = infer_total_gpu_count(config.get("composePath"), vlm_gpus, llm_gpus)
    compose_path = str(config.get("composePath", ""))

    return generate_perf_config_id(
        gpu_model=str(config.get("nodeLabel", "")),
        total_gpus=total_gpus,
        vlm_gpu_count=len(vlm_gpus),
        llm_gpu_count=len(llm_gpus),
        vlm_segment=_vlm_id_segment(str(config.get("vlmModel", "")), compose_path),
        llm_name=str(config.get("llmModel", "")),
        vision_input_tokens=config.get(
            "vision_input_tokens",
            config.get("maxTokens", default_vision_input_tokens),
        ),
    )
