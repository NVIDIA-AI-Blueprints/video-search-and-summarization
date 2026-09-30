# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Candidate identity and optional video time bounds for PAS reranking."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    segment_id: int
    video: str
    start: float | None
    end: float | None

    @property
    def has_time_bounds(self) -> bool:
        return self.start is not None and self.end is not None
