# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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


import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_llm_reasoning_bind_kwargs(llm: Any, llm_reasoning: bool | None) -> dict:
    """
    Get bind kwargs for LLM reasoning.

    Args:
        llm: The LLM model instance
        llm_reasoning: Whether reasoning mode is enabled

    Returns:
        Dict with reasoning parameters if applicable, empty dict otherwise
    """
    model_name = getattr(llm, "model_name", "") or getattr(llm, "model", "")
    model_name = model_name.lower()

    if type(llm).__name__ == "ChatNVIDIA":
        if "gpt-oss" in model_name and llm_reasoning is not None:
            return {"reasoning_effort": "low"} if llm_reasoning is False else {"reasoning_effort": "medium"}

        if "nemotron-3" in model_name and llm_reasoning is not None:
            return {"chat_template_kwargs": {"enable_thinking": llm_reasoning}}
    elif type(llm).__name__ == "ChatOpenAI":
        return {"reasoning": {"effort": "medium", "summary": "auto"}} if llm_reasoning else {}
    else:
        logger.warning(f"models using {type(llm).__name__} is not supported for reasoning binding")
        return {}

    logger.warning(f"No reasoning binding for {model_name} (llm_reasoning={llm_reasoning})")
    return {}


# Reference: https://github.com/NVIDIA/NeMo-Agent-Toolkit/blob/develop/src/nat/data_models/thinking_mixin.py

# The keys are the fields that are used to determine if the model supports thinking
_MODEL_KEYS = ("model_name", "model", "azure_deployment")


def get_thinking_tag(llm: Any, thinking: bool | None) -> str | None:
    """
    Returns the system prompt to use for thinking.
    For NVIDIA Nemotron, returns "/think" if enabled, else "/no_think".
    For Llama Nemotron v1.5, returns "/think" if enabled, else "/no_think".
    For Llama Nemotron v1.0 or v1.1, returns "detailed thinking on" if enabled, else "detailed thinking off".

    Args:
        llm: The LLM object.
        thinking: Whether to enable thinking (True, False, or None).

    Returns:
        str | None: The thinking tag to append to the system prompt, or None if not applicable.

    Raises:
        ValueError: If thinking is not supported on the model but thinking is True.
    """
    if thinking is None:
        return None

    for key in _MODEL_KEYS:
        model = getattr(llm, key, None)
        if not isinstance(model, str) or model is None:
            continue

        # Normalize name to reduce checks
        model = model.lower().translate(str.maketrans("_.", "--"))

        if model.startswith("nvidia/nvidia"):
            if "nemotron-3" in model:
                return None  # Nemotron 3 Nano does not need thinking tag

            return "/think" if thinking else "/no_think"

        if model.startswith("nvidia/llama"):
            if "v1-0" in model or "v1-1" in model or model.endswith("v1"):
                return f"detailed thinking {'on' if thinking else 'off'}"

            if "v1-5" in model:
                # v1.5 models are updated to use the /think and /no_think system prompts
                return "/think" if thinking else "/no_think"

            # Assume any other model is a newer model that uses the /think and /no_think system prompts
            return "/think" if thinking else "/no_think"

    # Unknown model
    return None
