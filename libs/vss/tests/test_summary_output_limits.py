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

"""Verify real SDK wire caps and provider truncation, without external calls."""

import json

import httpx
import pytest
from openai import AsyncOpenAI

from vss.summarization import OpenAISummaryGenerator, SummaryGenerationError


@pytest.mark.parametrize("api_type", ["chat_completions", "responses"])
async def test_configured_cap_reaches_wire_and_truncation_is_preserved(api_type):
    captured = []

    async def respond(request):
        captured.append(json.loads(request.content))
        if api_type == "chat_completions":
            body = {
                "id": "chatcmpl-fixture",
                "created": 1,
                "object": "chat.completion",
                "model": "bounded-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Partial."},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 17},
            }
        else:
            body = {
                "id": "resp-fixture",
                "created_at": 1,
                "object": "response",
                "model": "bounded-model",
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [
                    {
                        "id": "msg-fixture",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Partial.",
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 17},
            }
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), trust_env=False
    ) as transport:
        sdk = AsyncOpenAI(
            api_key="controlled-test-key",
            base_url="https://fixture.invalid/v1",
            http_client=transport,
            max_retries=0,
        )
        generator = OpenAISummaryGenerator(
            client=sdk,
            model="bounded-model",
            api_type=api_type,
            max_output_tokens=17,
        )
        result = await generator.generate([{"description": "A person enters."}])
        assert result.finish_reason == "length"
        assert result.completion_tokens == 17
        field = "max_tokens" if api_type == "chat_completions" else "max_output_tokens"
        assert captured[0][field] == 17
        assert len(captured) == 1
        await generator.aclose()
        assert not transport.is_closed  # Injected client remains caller-owned.
        await sdk.close()


async def test_unsupported_chat_cap_is_not_silently_removed_or_retried():
    captured = []

    async def reject(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            400,
            json={"error": {"message": "Unsupported max_tokens", "type": "invalid"}},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(reject), trust_env=False
    ) as transport:
        async with AsyncOpenAI(
            api_key="controlled-test-key",
            base_url="https://fixture.invalid/v1",
            http_client=transport,
            max_retries=0,
        ) as sdk:
            generator = OpenAISummaryGenerator(
                client=sdk,
                model="bounded-model",
                api_type="chat_completions",
                max_output_tokens=23,
            )
            with pytest.raises(SummaryGenerationError):
                await generator.generate([{"description": "A person enters."}])
            assert len(captured) == 1
            assert captured[0]["max_tokens"] == 23
