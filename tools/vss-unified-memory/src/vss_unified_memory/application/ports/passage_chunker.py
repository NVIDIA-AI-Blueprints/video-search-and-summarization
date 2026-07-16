# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Boundary for deterministic searchable-passage construction."""

from typing import Protocol

from vss_unified_memory.application.models import TextPassage


class PassageChunker(Protocol):
    @property
    def version(self) -> str: ...

    def chunk(self, record_id: str, text: str) -> tuple[TextPassage, ...]: ...
