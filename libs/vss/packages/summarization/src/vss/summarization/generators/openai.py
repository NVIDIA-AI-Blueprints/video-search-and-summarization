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

"""OpenAI-compatible summary generator."""

from __future__ import annotations

import json
from typing import Any, Protocol

from vss.observability import metric_scope, record

from ..exceptions import SummaryConfigurationError, SummaryGenerationError
from .base import GeneratedSummary, SummaryGenerator, register_generator
from .config import OpenAIAPIType, OpenAIConfig

DEFAULT_SUMMARY_PROMPT = (
    "Summarize the following ordered video events. Be concise, factual, "
    "and do not invent details that are absent from the events."
)


class OpenAIClient(Protocol):
    """Subset of the async OpenAI client used by this adapter."""

    @property
    def responses(self) -> Any:
        """Responses API resource."""

    @property
    def chat(self) -> Any:
        """Chat API resource."""

    async def close(self) -> None:
        """Close provider transport resources."""


class OpenAISummaryGenerator(SummaryGenerator):
    """Generate summaries through an OpenAI-compatible async API."""

    name = "openai"

    def __init__(
        self,
        *,
        client: OpenAIClient | None = None,
        config: OpenAIConfig | None = None,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        api_type: OpenAIAPIType | None = None,
        max_output_tokens: int = 1024,
        timeout_s: float = 120.0,
    ) -> None:
        if config is not None and (
            any(value is not None for value in (model, base_url, api_key, api_type))
            or max_output_tokens != 1024
            or timeout_s != 120.0
        ):
            raise SummaryConfigurationError(
                "config cannot be combined with individual provider settings"
            )
        if config is not None:
            self._config = config
        elif client is not None:
            self._config = OpenAIConfig(
                model=model or "gpt-4o-mini",
                base_url=base_url,
                api_key=api_key,
                api_type=api_type or "responses",
                max_output_tokens=max_output_tokens,
                timeout_s=timeout_s,
            )
        else:
            self._config = OpenAIConfig.resolve(
                model=model,
                base_url=base_url,
                api_key=api_key,
                api_type=api_type,
                max_output_tokens=max_output_tokens,
                timeout_s=timeout_s,
            )
        self._owns_client = client is None
        if client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as error:
                raise RuntimeError(
                    "OpenAI summary generation requires pip install "
                    '"vss-summarization[openai]"'
                ) from error
            client = AsyncOpenAI(
                base_url=self._config.base_url,
                api_key=self._config.api_key or "not-set",
                timeout=self._config.timeout_s,
            )
        assert client is not None
        self._client: OpenAIClient = client

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.close()

    async def generate(
        self,
        events: list[dict[str, Any]],
        *,
        prompt: str | None = None,
        model: str | None = None,
    ) -> GeneratedSummary:
        selected_model = model or self._config.model
        prompt_events = [
            {
                key: event[key]
                for key in ("start_time", "end_time", "type", "description")
                if key in event
            }
            for event in events
        ]
        user_input = json.dumps(
            {"events": prompt_events},
            separators=(",", ":"),
            ensure_ascii=False,
        )
        try:
            if self._config.api_type == "responses":
                return await self._generate_response(selected_model, prompt, user_input)
            return await self._generate_chat_completion(
                selected_model, prompt, user_input
            )
        except SummaryGenerationError:
            raise
        except Exception as error:
            raise SummaryGenerationError(
                "summary generation failed using "
                f"{self._config.api_type} for model {selected_model!r}"
            ) from error

    async def _generate_response(
        self,
        model: str,
        prompt: str | None,
        user_input: str,
    ) -> GeneratedSummary:
        response = await self._client.responses.create(
            model=model,
            instructions=prompt or DEFAULT_SUMMARY_PROMPT,
            input=[{"role": "user", "content": user_input}],
            max_output_tokens=self._config.max_output_tokens,
        )
        usage = response.usage
        _record_usage(
            usage,
            "input_tokens",
            "output_tokens",
            getattr(response, "model", model),
        )
        incomplete = getattr(response, "incomplete_details", None)
        return GeneratedSummary(
            text=response.output_text or "",
            model=getattr(response, "model", model),
            prompt_tokens=getattr(usage, "input_tokens", 0) if usage is not None else 0,
            completion_tokens=getattr(usage, "output_tokens", 0)
            if usage is not None
            else 0,
            finish_reason=(
                "length"
                if getattr(incomplete, "reason", None) == "max_output_tokens"
                else "stop"
            ),
        )

    async def _generate_chat_completion(
        self,
        model: str,
        prompt: str | None,
        user_input: str,
    ) -> GeneratedSummary:
        response = await self._client.chat.completions.create(
            model=model,
            max_tokens=self._config.max_output_tokens,
            messages=[
                {
                    "role": "system",
                    "content": prompt or DEFAULT_SUMMARY_PROMPT,
                },
                {"role": "user", "content": user_input},
            ],
        )
        choice = response.choices[0]
        usage = response.usage
        _record_usage(
            usage,
            "prompt_tokens",
            "completion_tokens",
            getattr(response, "model", model),
        )
        return GeneratedSummary(
            text=choice.message.content or "",
            model=getattr(response, "model", model),
            prompt_tokens=getattr(usage, "prompt_tokens", 0)
            if usage is not None
            else 0,
            completion_tokens=getattr(usage, "completion_tokens", 0)
            if usage is not None
            else 0,
            finish_reason=choice.finish_reason or "stop",
        )


def _record_usage(usage: Any, input_field: str, output_field: str, model: str) -> None:
    with metric_scope(model=model, backend="openai"):
        for metric, field in (
            ("input_tokens", input_field),
            ("output_tokens", output_field),
        ):
            value = getattr(usage, field, None)
            record(
                "summarization." + metric,
                value,
                library="summarization",
                origin="provider",
                availability="measured" if value is not None else "not_reported",
            )


register_generator(OpenAISummaryGenerator.name, OpenAISummaryGenerator)
