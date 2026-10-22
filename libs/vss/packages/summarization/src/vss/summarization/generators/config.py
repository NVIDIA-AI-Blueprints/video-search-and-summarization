# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Validated configuration for OpenAI-compatible summary generation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal, cast
from urllib.parse import urlsplit

from ..exceptions import SummaryConfigurationError

OpenAIAPIType = Literal["responses", "chat_completions"]

_RESOURCE_SUFFIXES: tuple[tuple[str, OpenAIAPIType], ...] = (
    ("/responses", "responses"),
    ("/chat/completions", "chat_completions"),
)


@dataclass(frozen=True, slots=True)
class OpenAIConfig:
    """Immutable OpenAI-compatible provider settings."""

    model: str
    base_url: str | None = None
    api_key: str | None = field(default=None, repr=False)
    api_type: OpenAIAPIType = "responses"
    max_output_tokens: int = 1024
    timeout_s: float = 120.0

    def __post_init__(self) -> None:
        model = self.model.strip()
        if not model:
            raise SummaryConfigurationError("model must not be empty")
        object.__setattr__(self, "model", model)

        if self.api_type not in {"responses", "chat_completions"}:
            raise SummaryConfigurationError(
                "api_type must be 'responses' or 'chat_completions'"
            )
        if self.max_output_tokens < 1:
            raise SummaryConfigurationError("max_output_tokens must be at least 1")
        if self.timeout_s <= 0:
            raise SummaryConfigurationError("timeout_s must be positive")

        normalized_url, inferred_api_type = _normalize_base_url(self.base_url)
        object.__setattr__(self, "base_url", normalized_url)
        if inferred_api_type is not None:
            object.__setattr__(self, "api_type", inferred_api_type)

    @classmethod
    def resolve(
        cls,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        api_type: OpenAIAPIType | None = None,
        max_output_tokens: int = 1024,
        timeout_s: float = 120.0,
    ) -> OpenAIConfig:
        """Resolve explicit values over VSS and OpenAI environment values."""

        resolved_base_url = base_url or os.getenv("VSS_SUMMARIZATION_BASE_URL")
        resolved_api_key = (
            api_key
            or os.getenv("VSS_SUMMARIZATION_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        )
        if resolved_base_url is None and resolved_api_key is None:
            raise SummaryConfigurationError(
                "summarization LLM is not configured; set "
                "VSS_SUMMARIZATION_BASE_URL, "
                "VSS_SUMMARIZATION_MODEL, and "
                "VSS_SUMMARIZATION_API_KEY"
            )
        return cls(
            model=model or os.getenv("VSS_SUMMARIZATION_MODEL") or "gpt-4o-mini",
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            api_type=api_type or _environment_api_type() or "responses",
            max_output_tokens=max_output_tokens,
            timeout_s=timeout_s,
        )


def _environment_api_type() -> OpenAIAPIType | None:
    value = os.getenv("VSS_SUMMARIZATION_API")
    if value is None:
        return None
    if value not in {"responses", "chat_completions"}:
        raise SummaryConfigurationError(
            "VSS_SUMMARIZATION_API must be 'responses' or 'chat_completions'"
        )
    return cast(OpenAIAPIType, value)


def _normalize_base_url(
    base_url: str | None,
) -> tuple[str | None, OpenAIAPIType | None]:
    if base_url is None:
        return None, None
    normalized = base_url.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SummaryConfigurationError("base_url must be an absolute HTTP(S) URL")

    for suffix, api_type in _RESOURCE_SUFFIXES:
        if parsed.path.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            return normalized, api_type
    return normalized, None
