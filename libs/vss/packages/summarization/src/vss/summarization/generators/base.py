# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Summary generator interface and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class GeneratedSummary(BaseModel):
    """Provider-neutral output from a summary generator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    text: str
    model: str = Field(min_length=1)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    finish_reason: str = "stop"


class SummaryGenerator(ABC):
    """Interface for generating a natural-language event summary."""

    async def __aenter__(self) -> SummaryGenerator:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Release resources owned by the generator."""

        return None

    @abstractmethod
    async def generate(
        self,
        events: list[dict[str, Any]],
        *,
        prompt: str | None = None,
        model: str | None = None,
    ) -> GeneratedSummary:
        """Generate a summary from normalized events."""


_GENERATOR_REGISTRY: dict[str, type[SummaryGenerator]] = {}


def register_generator(name: str, generator_type: type[SummaryGenerator]) -> None:
    """Register a summary generator implementation."""

    if not name:
        raise ValueError("generator name must not be empty")
    if not issubclass(generator_type, SummaryGenerator):
        raise TypeError("generator_type must implement SummaryGenerator")
    _GENERATOR_REGISTRY[name] = generator_type


def get_generator(name: str, **kwargs: Any) -> SummaryGenerator:
    """Construct a registered summary generator by name."""

    try:
        generator_type = _GENERATOR_REGISTRY[name]
    except KeyError as error:
        available = ", ".join(sorted(_GENERATOR_REGISTRY)) or "none"
        raise KeyError(
            f"unknown summary generator {name!r}; available: {available}"
        ) from error
    return generator_type(**kwargs)


def available_generators() -> tuple[str, ...]:
    """Return registered generator names."""

    return tuple(sorted(_GENERATOR_REGISTRY))
