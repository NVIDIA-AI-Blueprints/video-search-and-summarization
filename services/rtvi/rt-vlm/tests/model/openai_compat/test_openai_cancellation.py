# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Request cancellation must drain the transport without closing another request's client."""

import asyncio
import concurrent.futures
import gc
import json
import weakref
from threading import Event

import httpx
import pytest
from langchain_openai import AzureChatOpenAI
from openai import AsyncOpenAI, OpenAI

from common.service_exception import ServiceException
from models.base_vlm_model import VlmGenerationConfig
from models.openai_compat import openai_compat_model as adapter


@pytest.fixture(params=["openai", "azure"])
def model(request, monkeypatch):
    monkeypatch.setenv("REMOTE_VIDEO_INPUT", "false")
    instance = adapter.CompOpenAIModel.__new__(adapter.CompOpenAIModel)

    started = Event()
    cleanup_started = Event()
    release_cleanup = Event()
    cleaned = Event()
    calls = []

    async def handle(http_request):
        body = json.loads(http_request.content)
        calls.append(body)
        if "failure" in str(body["messages"]):
            return httpx.Response(400, json={"error": {"message": "invalid request"}})
        if "orphan" in str(body["messages"]):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                while not release_cleanup.is_set():
                    await asyncio.sleep(0.001)
                cleaned.set()
        return httpx.Response(
            200,
            json={
                "id": "test-response",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "healthy"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            },
        )

    def reject_sync(http_request):
        raise AssertionError("Generation must use the cancellable async transport")

    sync_http = httpx.Client(transport=httpx.MockTransport(reject_sync))
    async_http = httpx.AsyncClient(transport=httpx.MockTransport(handle))

    def initialize(self):
        self._model_name = "test-model"
        self._nvSecretConfigured = False
        if request.param == "azure":
            self._model = AzureChatOpenAI(
                model="test-model",
                azure_deployment="test-model",
                api_key="test-key",
                azure_endpoint="https://test.invalid",
                api_version="2024-02-01",
                http_async_client=async_http,
                http_client=sync_http,
            )
        else:
            self._client = OpenAI(api_key="test-key", http_client=sync_http)
            self._async_client = AsyncOpenAI(api_key="test-key", http_client=async_http)

    monkeypatch.setattr(adapter.CompOpenAIModel, "init_gpt_4", initialize)
    instance._initialize_model()
    yield instance, started, cleanup_started, release_cleanup, cleaned, calls, async_http
    release_cleanup.set()
    instance._shutdown_model()
    assert not instance._request_thread.is_alive()
    assert instance._request_loop.is_closed()
    instance._shutdown_model()  # Repeated cleanup must be harmless.
    assert async_http.is_closed
    assert sync_http.is_closed


def generate(instance, query):
    return instance.generate(query, chunks=[], video_frames=[[]], video_frames_times=[[]])


def test_cancel_running_transport_drains_and_keeps_healthy_request(model):
    instance, started, cleanup_started, release_cleanup, cleaned, calls, async_http = model
    orphan = generate(instance, "orphan")
    assert started.wait(5)
    assert not orphan.cancel()
    assert cleanup_started.wait(5)
    assert not orphan.done()
    assert not cleaned.is_set()

    assert generate(instance, "healthy").result(timeout=5)[0].output == "healthy"
    assert not async_http.is_closed
    release_cleanup.set()
    with pytest.raises(concurrent.futures.CancelledError):
        orphan.result(timeout=5)
    assert cleaned.is_set()
    assert orphan.cancelled()
    assert len(calls) == 2
    assert generate(instance, "after cancellation").result(timeout=5)[0].output == "healthy"


def test_cancel_during_preprocessing_never_submits_http(model, monkeypatch):
    instance, _, _, _, _, calls, _ = model
    started = Event()
    release = Event()

    def encode(*args):
        started.set()
        assert release.wait(5)
        return "test-jpeg"

    monkeypatch.setattr(adapter, "tensor_to_base64_jpeg", encode)
    orphan = instance.generate("orphan", chunks=[], video_frames=[[]], video_frames_times=[[0]])
    try:
        assert started.wait(5)
        assert not orphan.cancel()
        assert not orphan.done()
    finally:
        release.set()
    with pytest.raises(concurrent.futures.CancelledError):
        orphan.result(timeout=5)
    assert not calls
    assert generate(instance, "healthy").result(timeout=5)[0].output == "healthy"


def test_async_generation_preserves_error_mapping_and_request_options(model, monkeypatch):
    instance, _, _, _, _, calls, _ = model
    monkeypatch.setenv("VIA_VLM_OPENAI_TOKEN_PARAM", "max_completion_tokens")
    config = VlmGenerationConfig(
        max_new_tokens=17,
        temperature=0.2,
        seed=42,
        top_p=0.7,
        min_tokens=3,
        ignore_eos=True,
        system_prompt="test system",
    )
    result = instance.generate(
        "healthy",
        chunks=[],
        video_frames=[[]],
        video_frames_times=[[]],
        generation_config=config,
    ).result(timeout=5)
    assert result[0].output == "healthy"
    assert calls[0]["max_completion_tokens"] == 17
    assert calls[0]["temperature"] == 0.2
    assert calls[0]["seed"] == 42
    assert calls[0]["top_p"] == 0.7
    assert calls[0]["min_tokens"] == 3
    assert calls[0]["ignore_eos"] is True
    assert calls[0]["messages"][0] == {"role": "system", "content": "test system"}
    with pytest.raises(ServiceException) as error:
        generate(instance, "failure").result(timeout=5)
    assert error.value.status_code == 400


@pytest.mark.parametrize("collect_on_request_thread", [False, True])
def test_model_collection_stops_owned_loop(monkeypatch, collect_on_request_thread):
    monkeypatch.setattr(adapter.CompOpenAIModel, "init_gpt_4", lambda self: None)
    instance = adapter.CompOpenAIModel.__new__(adapter.CompOpenAIModel)
    instance._initialize_model()
    request_loop = instance._request_loop
    request_thread = instance._request_thread
    reference = weakref.ref(instance)
    holder = [instance]
    del instance
    if collect_on_request_thread:
        request_loop.call_soon_threadsafe(holder.clear)
    else:
        holder.clear()
    gc.collect()
    request_thread.join(timeout=5)
    assert reference() is None
    assert not request_thread.is_alive()
    assert request_loop.is_closed()


def test_shutdown_after_partial_initialization_is_harmless():
    instance = adapter.CompOpenAIModel.__new__(adapter.CompOpenAIModel)
    instance._shutdown_model()
    instance._shutdown_model()
