# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Text embedding boundary."""

from collections.abc import Sequence
from typing import Protocol


class EmbeddingProvider(Protocol):
    @property
    def model(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...
