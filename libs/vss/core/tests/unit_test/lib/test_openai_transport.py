# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared OpenAI-compatible chat transport regression tests."""

from __future__ import annotations

import httpx
import pytest

from vss_core import openai as openai_mod
from vss_core._foundation.errors import BackendUnreachableError
from vss_core._foundation.errors import ConfigurationError
from vss_core._foundation.retry import create_retry_strategy
from vss_core.openai import OpenAIChatTransport
from vss_core.openai import extract_chat_content


@pytest.mark.asyncio
async def test_chat_transport_normalizes_url_retries_and_places_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) < 3:
            return httpx.Response(503, request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}, request=request)

    monkeypatch.setattr(
        openai_mod,
        "create_retry_strategy",
        lambda retries, exceptions: create_retry_strategy(retries, delay=0, exceptions=exceptions),
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = OpenAIChatTransport(
        base_url="https://models.example.test",
        api_key="secret-token",
        attempts=4,
        client=client,
    )
    try:
        body = await transport.complete({"model": "m", "messages": []})
    finally:
        await client.aclose()

    assert extract_chat_content(body) == "ok"
    assert len(requests) == 3
    assert str(requests[-1].url) == "https://models.example.test/v1/chat/completions"
    assert requests[-1].headers["authorization"] == "Bearer secret-token"


@pytest.mark.asyncio
async def test_chat_transport_has_safe_diagnostics_and_classifies_rejections() -> None:
    def rejected(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="Bearer secret-token", request=request)

    rejected_client = httpx.AsyncClient(transport=httpx.MockTransport(rejected))
    rejected_transport = OpenAIChatTransport(base_url="https://models.example.test/v1", client=rejected_client)
    try:
        with pytest.raises(ConfigurationError, match="HTTP 400") as rejected_error:
            await rejected_transport.complete({"model": "m", "messages": []})
    finally:
        await rejected_client.aclose()
    assert "secret-token" not in str(rejected_error.value)

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Bearer secret-token", request=request)

    unreachable_client = httpx.AsyncClient(transport=httpx.MockTransport(unreachable))
    unreachable_transport = OpenAIChatTransport(
        base_url="https://models.example.test/v1/chat/completions",
        attempts=1,
        client=unreachable_client,
    )
    try:
        with pytest.raises(BackendUnreachableError) as unreachable_error:
            await unreachable_transport.complete({"model": "m", "messages": []})
    finally:
        await unreachable_client.aclose()
    assert "secret-token" not in str(unreachable_error.value)


@pytest.mark.asyncio
async def test_chat_transport_exposes_attempt_count_and_custom_retry_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        status = 408 if len(requests) == 1 else 200
        return httpx.Response(status, json={"choices": [{"message": {"content": "ok"}}]}, request=request)

    monkeypatch.setattr(
        openai_mod,
        "create_retry_strategy",
        lambda retries, exceptions: create_retry_strategy(retries, delay=0, exceptions=exceptions),
    )
    transport = OpenAIChatTransport(
        base_url="https://models.example.test/v1",
        attempts=2,
        transport=httpx.MockTransport(handler),
        retry_statuses={408},
    )
    try:
        response, attempts = await transport.post_with_attempts({"model": "m", "messages": []})
    finally:
        await transport.aclose()

    assert response.status_code == 200
    assert attempts == 2
    assert len(requests) == 2

    unlisted_requests: list[httpx.Request] = []

    def unlisted(request: httpx.Request) -> httpx.Response:
        unlisted_requests.append(request)
        return httpx.Response(501, request=request)

    unlisted_transport = OpenAIChatTransport(
        base_url="https://models.example.test/v1",
        attempts=4,
        transport=httpx.MockTransport(unlisted),
        retry_statuses={408},
    )
    try:
        with pytest.raises(openai_mod.OpenAIChatRequestError) as unlisted_error:
            await unlisted_transport.post_with_attempts({"model": "m", "messages": []})
    finally:
        await unlisted_transport.aclose()
    assert len(unlisted_requests) == 1
    assert unlisted_error.value.attempts == 1


def test_extract_chat_content_accepts_ordered_text_parts() -> None:
    payload = {"choices": [{"message": {"content": [{"text": "one"}, {"ignored": True}, {"text": "two"}]}}]}

    assert extract_chat_content(payload) == "one\ntwo"
    assert extract_chat_content({"choices": [{"message": {"content": None}}]}, none_as_empty=True) == ""
