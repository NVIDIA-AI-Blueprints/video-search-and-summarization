# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Dependency-injection protocols for VLM consumers."""

from __future__ import annotations

from typing import Literal
from typing import Protocol
from typing import runtime_checkable


@runtime_checkable
class VLMAnalyzer(Protocol):
    """Analyze a video interval and return a model-generated response."""

    async def analyze(
        self,
        *,
        sensor_id: str,
        start_timestamp: str,
        end_timestamp: str,
        prompt: str,
        time_format: Literal["iso", "offset"] = "iso",
    ) -> str: ...
