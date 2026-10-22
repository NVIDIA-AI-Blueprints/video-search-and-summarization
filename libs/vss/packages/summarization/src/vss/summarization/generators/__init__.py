# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pluggable summary generators."""

from .base import (
    GeneratedSummary,
    SummaryGenerator,
    available_generators,
    get_generator,
    register_generator,
)
from .config import OpenAIAPIType, OpenAIConfig
from .openai import DEFAULT_SUMMARY_PROMPT, OpenAISummaryGenerator

__all__ = [
    "DEFAULT_SUMMARY_PROMPT",
    "GeneratedSummary",
    "OpenAIAPIType",
    "OpenAIConfig",
    "OpenAISummaryGenerator",
    "SummaryGenerator",
    "available_generators",
    "get_generator",
    "register_generator",
]
