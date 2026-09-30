# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reranker registry.

Factories import their concrete (often heavy) implementation lazily, so listing
or using one reranker never imports torch/vLLM for the others.
"""

from __future__ import annotations

from typing import Callable

from .config import RerankerConfig
from .data import Segment
from .cosmos_reason import CosmosReasonReranker
from .base import (
    Candidate,
    CandidateScore,
    IdentityReranker,
    Reranker,
    ScoringReranker,
)

_REGISTRY: dict[str, Callable[[RerankerConfig], Reranker]] = {}


def register_reranker(name: str, factory: Callable[[RerankerConfig], Reranker]) -> None:
    _REGISTRY[name] = factory


def available_rerankers() -> list[str]:
    return sorted(_REGISTRY)


def build_reranker(cfg: RerankerConfig) -> Reranker:
    if cfg.name not in _REGISTRY:
        raise KeyError(
            f"Unknown reranker {cfg.name!r}. Available: {', '.join(available_rerankers())}"
        )
    return _REGISTRY[cfg.name](cfg)


register_reranker("identity", lambda cfg: IdentityReranker())


def _cosmos_reason(cfg: RerankerConfig) -> Reranker:
    from .cosmos_reason import CosmosReasonReranker

    return CosmosReasonReranker(cfg)


register_reranker("cosmos_reason", _cosmos_reason)

__all__ = [
    "Candidate",
    "Segment",
    "RerankerConfig",
    "CosmosReasonReranker",
    "CandidateScore",
    "Reranker",
    "IdentityReranker",
    "ScoringReranker",
    "build_reranker",
    "register_reranker",
    "available_rerankers",
]
