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

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from vss.summarization import (
    OpenAIConfig,
    OpenAISummaryGenerator,
    SummaryConfigurationError,
    SummaryGenerationError,
    SummaryGenerator,
    available_generators,
    get_generator,
)


class FakeCompletions:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return SimpleNamespace(
            model="served-model",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="A short summary."),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=12,
                completion_tokens=4,
            ),
        )


async def test_openai_compatible_generator_adapter() -> None:
    completions = FakeCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    generator = OpenAISummaryGenerator(
        client=client,
        model="configured-model",
        api_type="chat_completions",
    )

    result = await generator.generate(
        [{"description": "A person enters."}],
        prompt="Summarize accurately.",
    )

    assert result.text == "A short summary."
    assert result.model == "served-model"
    assert result.prompt_tokens == 12
    assert result.completion_tokens == 4
    assert completions.kwargs["model"] == "configured-model"
    assert completions.kwargs["max_tokens"] == 1024
    assert completions.kwargs["messages"][0]["content"] == ("Summarize accurately.")
    payload = json.loads(completions.kwargs["messages"][1]["content"])
    assert payload["events"][0]["description"] == "A person enters."


async def test_openai_responses_generator_adapter() -> None:
    captured: dict[str, Any] = {}

    async def create(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(
            output_text="A Responses API summary.",
            model="responses-model",
            usage=SimpleNamespace(input_tokens=8, output_tokens=3),
            incomplete_details=None,
        )

    client = SimpleNamespace(
        responses=SimpleNamespace(create=create),
    )
    generator = OpenAISummaryGenerator(
        client=client,
        model="configured-model",
    )

    result = await generator.generate(
        [{"description": "A person enters."}],
        prompt="Summarize accurately.",
    )

    assert result.text == "A Responses API summary."
    assert result.model == "responses-model"
    assert result.prompt_tokens == 8
    assert result.completion_tokens == 3
    assert captured["instructions"] == "Summarize accurately."
    assert captured["max_output_tokens"] == 1024
    payload = json.loads(captured["input"][0]["content"])
    assert payload["events"][0]["description"] == "A person enters."


def test_openai_generator_is_registered() -> None:
    assert "openai" in available_generators()
    assert isinstance(
        get_generator("openai", client=SimpleNamespace()),
        SummaryGenerator,
    )


def test_openai_generator_requires_default_endpoint_credentials(
    monkeypatch: Any,
) -> None:
    for name in (
        "VSS_SUMMARIZATION_BASE_URL",
        "VSS_SUMMARIZATION_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(
        SummaryConfigurationError,
        match="VSS_SUMMARIZATION_API_KEY",
    ):
        OpenAISummaryGenerator()


def test_openai_config_normalizes_resource_endpoint() -> None:
    config = OpenAIConfig(
        model="test-model",
        base_url="https://example.test/v1/responses/",
        api_type="chat_completions",
    )

    assert config.base_url == "https://example.test/v1"
    assert config.api_type == "responses"
    assert "api_key" not in repr(OpenAIConfig(model="test-model", api_key="secret"))


async def test_provider_errors_use_stable_exception() -> None:
    async def create(**_: Any) -> Any:
        raise OSError("provider unavailable")

    generator = OpenAISummaryGenerator(
        client=SimpleNamespace(
            responses=SimpleNamespace(create=create),
        ),
        model="test-model",
    )

    with pytest.raises(SummaryGenerationError) as captured:
        await generator.generate([{"description": "event"}])

    assert isinstance(captured.value.__cause__, OSError)
