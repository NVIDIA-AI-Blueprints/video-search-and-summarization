# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Configuration for the PAS identity and Cosmos/CR3 Nano rerankers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RerankerConfig:
    name: str = "identity"
    rerank_depth: int = 20
    fps: float = 2.0
    max_frames: int = 32
    options: dict[str, Any] = field(default_factory=dict)
