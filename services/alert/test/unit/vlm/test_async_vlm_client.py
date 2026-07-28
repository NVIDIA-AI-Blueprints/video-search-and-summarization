# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Unit tests for ``AsyncVLMClient`` and ``AsyncVLMRuntime``.

``AsyncVLMClient`` mirrors the sync client but differs in two ways that are
easy to get wrong and are pinned here: it defaults the prompt instead of
rejecting an empty one (except on the base64 path, which still requires it),
and every blocking call — file reads, downloads, ``os.path.getsize``, cleanup
— is pushed through ``asyncio.to_thread`` so the loop is never stalled.

``AsyncVLMRuntime`` owns a single background thread and event loop shared by
the whole process. Its contract is what the tests below target:

* the loop starts lazily on first submit and is reused afterwards;
* a coroutine that is never scheduled must be explicitly closed — otherwise
  Python emits "coroutine was never awaited" and the caller leaks it. Three
  paths can fail this way (startup failure, dead/stopping loop, a rejecting
  ``run_coroutine_threadsafe``) and each is covered;
* ``stop()`` is idempotent, safe before startup, and resets state so a later
  submit can start a fresh loop.

Every test that starts a runtime stops it in teardown, so no thread outlives
the test.
"""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vlm.vlm_client import AsyncVLMClient, AsyncVLMRuntime

COSMOS = "nvidia/cosmos-reason2-8b"


def make_completion(content="verdict: yes"):
    message = MagicMock()
    message.content = content
    response = MagicMock()
    response.choices = [MagicMock(message=message)]
    return response


def make_async_client(**config):
    config.setdefault("model", COSMOS)
    config.setdefault("base_url", "http://vlm:8080/v1")
    with patch("vlm.vlm_client.AsyncOpenAI"):
        client = AsyncVLMClient(config)
    client.client.chat.completions.create = AsyncMock(return_value=make_completion())
    return client


@pytest.fixture
def client():
    return make_async_client(max_tokens=256)


@pytest.fixture
def video_file(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00\x00\x00\x1cftypisom")
    return str(path)


@pytest.fixture
def image_file(tmp_path):
    path = tmp_path / "frame.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    return str(path)


def sent_kwargs(client):
    return client.client.chat.completions.create.call_args.kwargs


class TestConstruction:
    def test_async_openai_receives_base_url_and_timeout(self):
        with patch("vlm.vlm_client.AsyncOpenAI") as async_openai:
            AsyncVLMClient({"base_url": "http://vlm:9000/v1", "request_timeout": 42})

        assert async_openai.call_args.kwargs["base_url"] == "http://vlm:9000/v1"
        assert async_openai.call_args.kwargs["timeout"] == 42

    def test_shares_the_base_payload_helpers(self, client):
        assert client._get_model_type().value == "cosmos-reason"


class TestAnalyzeImageUrl:
    @pytest.mark.asyncio
    async def test_returns_the_first_choice_message(self, client):
        result = await client.analyze_image_url("http://host/f.png", "what is this?")
        assert result.content == "verdict: yes"

    @pytest.mark.asyncio
    async def test_blank_prompt_falls_back_to_the_default(self, client):
        await client.analyze_image_url("http://host/f.png", user_prompt="")

        content = sent_kwargs(client)["messages"][0]["content"]
        assert content[-1]["text"] == "What is in this image?"

    @pytest.mark.asyncio
    async def test_image_request_has_no_sampling_block(self, client):
        await client.analyze_image_url("http://host/f.png", "p")
        assert "media_io_kwargs" not in sent_kwargs(client).get("extra_body", {})


class TestAnalyzeVideoUrl:
    @pytest.mark.asyncio
    async def test_returns_the_first_choice_message(self, client):
        assert (await client.analyze_video_url("http://host/v.mp4", "p")).content == "verdict: yes"

    @pytest.mark.asyncio
    async def test_blank_prompt_falls_back_to_the_default(self, client):
        await client.analyze_video_url("http://host/v.mp4", user_prompt=None)

        content = sent_kwargs(client)["messages"][0]["content"]
        assert content[-1]["text"] == "What is in this video?"

    @pytest.mark.asyncio
    async def test_num_frames_and_overrides_reach_the_request(self, client):
        await client.analyze_video_url(
            "http://host/v.mp4", "p", num_frames=3,
            config_overrides={"model": "meta/llama-3.2-11b-vision", "temperature": 0.4},
        )
        kwargs = sent_kwargs(client)

        assert kwargs["model"] == "meta/llama-3.2-11b-vision"
        assert kwargs["temperature"] == 0.4
        assert kwargs["extra_body"]["media_io_kwargs"] == {"video": {"num_frames": 3}}


class TestUploadMediaFile:
    @pytest.mark.asyncio
    async def test_sends_a_base64_data_url(self, client, video_file):
        await client.upload_media_file(video_file, "p")

        content = sent_kwargs(client)["messages"][0]["content"]
        assert content[0]["video_url"]["url"].startswith("data:video/mp4;base64,")

    @pytest.mark.asyncio
    async def test_prompt_default_depends_on_the_detected_media_type(self, client, image_file):
        await client.upload_media_file(image_file, user_prompt=None)

        content = sent_kwargs(client)["messages"][0]["content"]
        assert content[-1]["text"] == "What is in this image?"

    @pytest.mark.asyncio
    async def test_video_default_prompt(self, client, video_file):
        await client.upload_media_file(video_file, user_prompt=None)
        assert sent_kwargs(client)["messages"][0]["content"][-1]["text"] == "What is in this video?"

    @pytest.mark.asyncio
    async def test_file_read_happens_off_the_event_loop(self, client, video_file):
        with patch("asyncio.to_thread", wraps=asyncio.to_thread) as to_thread:
            await client.upload_media_file(video_file, "p")

        assert to_thread.call_args_list[0].args[0] == client._prepare_local_media

    @pytest.mark.asyncio
    async def test_missing_file_raises(self, client, tmp_path):
        with pytest.raises(FileNotFoundError):
            await client.upload_media_file(str(tmp_path / "nope.mp4"), "p")

    @pytest.mark.asyncio
    async def test_analyze_local_video_delegates(self, client, video_file):
        await client.analyze_local_video(video_file, "p", num_frames=2)
        assert sent_kwargs(client)["extra_body"]["media_io_kwargs"] == {"video": {"num_frames": 2}}


class TestMultipleImages:
    @pytest.mark.asyncio
    async def test_paths_become_data_urls(self, client, image_file):
        await client.analyze_multiple_images([image_file, image_file], "compare")

        content = sent_kwargs(client)["messages"][0]["content"]
        assert [c["type"] for c in content] == ["image_url", "image_url", "text"]

    @pytest.mark.asyncio
    async def test_empty_path_list_raises(self, client):
        with pytest.raises(ValueError, match="image_paths cannot be empty"):
            await client.analyze_multiple_images([], "p")

    @pytest.mark.asyncio
    async def test_blank_prompt_falls_back(self, client, image_file):
        await client.analyze_multiple_images([image_file], user_prompt="")
        assert sent_kwargs(client)["messages"][0]["content"][-1]["text"] == "Analyze these images."

    @pytest.mark.asyncio
    async def test_urls_are_sent_verbatim(self, client):
        await client.analyze_multiple_image_urls(["http://host/a.png"], "compare")
        content = sent_kwargs(client)["messages"][0]["content"]
        assert content[0]["image_url"] == {"url": "http://host/a.png"}

    @pytest.mark.asyncio
    async def test_empty_url_list_raises(self, client):
        with pytest.raises(ValueError, match="image_urls cannot be empty"):
            await client.analyze_multiple_image_urls([], "p")

    @pytest.mark.asyncio
    async def test_url_variant_falls_back_to_the_default_prompt(self, client):
        await client.analyze_multiple_image_urls(["http://host/a.png"], user_prompt=None)
        assert sent_kwargs(client)["messages"][0]["content"][-1]["text"] == "Analyze these images."


class TestAnalyzeMediaWithBase64:
    @pytest.fixture
    def downloader(self, video_file):
        with patch("handlers.direct_media.media_downloader.MediaDownloader") as cls, patch(
            "handlers.direct_media.media_downloader.DownloadConfig"
        ) as config_cls:
            cls.return_value.download.return_value = video_file
            yield cls, config_cls

    @pytest.mark.asyncio
    async def test_downloads_then_uploads_then_cleans_up(self, client, downloader, video_file):
        cls, _config_cls = downloader

        result = await client.analyze_media_with_base64("http://host/v.mp4", "p")

        cls.return_value.download.assert_called_once_with("http://host/v.mp4", 0)
        cls.cleanup.assert_called_once_with(video_file)
        assert result.content == "verdict: yes"

    @pytest.mark.asyncio
    async def test_download_config_comes_from_the_client_config(self, video_file):
        client = make_async_client(
            media_download={"download_dir": "/data", "timeout_seconds": 5, "max_size_mb": 7}
        )
        with patch("handlers.direct_media.media_downloader.MediaDownloader") as cls, patch(
            "handlers.direct_media.media_downloader.DownloadConfig"
        ) as config_cls:
            cls.return_value.download.return_value = video_file
            await client.analyze_media_with_base64("http://host/v.mp4", "p")

        assert config_cls.call_args.kwargs["download_dir"] == "/data"
        assert config_cls.call_args.kwargs["allow_private_urls"] is True

    @pytest.mark.asyncio
    async def test_missing_prompt_raises_before_downloading(self, client, downloader):
        cls, _config_cls = downloader

        with pytest.raises(ValueError, match="user_prompt is required"):
            await client.analyze_media_with_base64("http://host/v.mp4", "")

        cls.return_value.download.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_media_type_raises_before_downloading(self, client, downloader):
        cls, _config_cls = downloader

        with pytest.raises(ValueError, match="media_type must be"):
            await client.analyze_media_with_base64("http://host/v.mp4", "p", media_type="audio")

        cls.return_value.download.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_download_raises_and_skips_cleanup(self, client, downloader):
        cls, _config_cls = downloader
        cls.return_value.download.return_value = None

        with pytest.raises(ValueError, match="Failed to download video"):
            await client.analyze_media_with_base64("http://host/v.mp4", "p")

        cls.cleanup.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_runs_even_when_the_upload_fails(self, client, downloader, video_file):
        cls, _config_cls = downloader
        client.client.chat.completions.create.side_effect = RuntimeError("VLM down")

        with pytest.raises(RuntimeError, match="VLM down"):
            await client.analyze_media_with_base64("http://host/v.mp4", "p")

        cls.cleanup.assert_called_once_with(video_file)

    @pytest.mark.asyncio
    async def test_video_alias_delegates(self, client, downloader, video_file):
        await client.analyze_video_with_base64("http://host/v.mp4", "p", num_frames=6)
        assert sent_kwargs(client)["extra_body"]["media_io_kwargs"] == {"video": {"num_frames": 6}}


class TestAclose:
    @pytest.mark.asyncio
    async def test_awaits_an_async_close(self, client):
        client.client.close = AsyncMock()
        await client.aclose()
        client.client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_calls_a_sync_close(self, client):
        client.client.close = MagicMock()
        await client.aclose()
        client.client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_client_without_close_is_tolerated(self, client):
        client.client = object()
        await client.aclose()


@pytest.fixture
def runtime():
    """A runtime whose AsyncVLMClient is stubbed out; always stopped after."""
    with patch("vlm.vlm_client.AsyncVLMClient") as client_cls:
        client_cls.return_value.aclose = AsyncMock()
        rt = AsyncVLMRuntime({"model": COSMOS})
        rt._client_cls = client_cls
        try:
            yield rt
        finally:
            rt.stop(timeout=5)


class TestRuntimeLifecycle:
    def test_no_thread_before_first_use(self, runtime):
        assert runtime._thread is None
        assert runtime._loop is None

    def test_first_submit_starts_the_loop_thread(self, runtime):
        assert runtime.run_coroutine(asyncio.sleep(0)) is None

        assert runtime._thread is not None
        assert runtime._thread.is_alive()
        assert runtime._thread.name == "ab-vlm-async-runtime"
        assert runtime._thread.daemon is True

    def test_the_loop_thread_is_reused(self, runtime):
        runtime.run_coroutine(asyncio.sleep(0))
        first = runtime._thread
        runtime.run_coroutine(asyncio.sleep(0))

        assert runtime._thread is first

    def test_work_runs_off_the_calling_thread(self, runtime):
        async def which_thread():
            return threading.current_thread().name

        assert runtime.run_coroutine(which_thread()) == "ab-vlm-async-runtime"

    def test_run_coroutine_propagates_the_result(self, runtime):
        async def answer():
            return 42

        assert runtime.run_coroutine(answer()) == 42

    def test_run_coroutine_propagates_exceptions(self, runtime):
        async def boom():
            raise ValueError("inside the loop")

        with pytest.raises(ValueError, match="inside the loop"):
            runtime.run_coroutine(boom())

    def test_submit_coroutine_returns_a_future(self, runtime):
        async def answer():
            return "done"

        future = runtime.submit_coroutine(answer())
        assert future.result(timeout=5) == "done"

    def test_sleep_runs_on_the_runtime_loop(self, runtime):
        runtime.sleep(0.01)
        assert runtime._thread.is_alive()

    def test_submit_to_thread_runs_blocking_work(self, runtime):
        future = runtime.submit_to_thread(lambda a, b=0: a + b, 1, b=2)
        assert future.result(timeout=5) == 3

    def test_submit_to_thread_uses_a_worker_thread(self, runtime):
        future = runtime.submit_to_thread(lambda: threading.current_thread().name)
        assert future.result(timeout=5) != "ab-vlm-async-runtime"

    def test_submit_to_thread_propagates_exceptions(self, runtime):
        def boom():
            raise ValueError("in the worker")

        with pytest.raises(ValueError, match="in the worker"):
            runtime.submit_to_thread(boom).result(timeout=5)


class TestRuntimeStartupFailure:
    def test_client_construction_failure_is_reported(self):
        with patch("vlm.vlm_client.AsyncVLMClient", side_effect=RuntimeError("bad base_url")):
            rt = AsyncVLMRuntime({})
            try:
                with pytest.raises(RuntimeError, match="Failed to start async VLM runtime"):
                    rt.run_coroutine(asyncio.sleep(0))
            finally:
                rt.stop(timeout=5)

    def test_the_original_error_is_chained(self):
        original = RuntimeError("bad base_url")
        with patch("vlm.vlm_client.AsyncVLMClient", side_effect=original):
            rt = AsyncVLMRuntime({})
            try:
                with pytest.raises(RuntimeError) as exc_info:
                    rt.submit_coroutine(asyncio.sleep(0))
                assert exc_info.value.__cause__ is original
            finally:
                rt.stop(timeout=5)

    def test_the_unscheduled_coroutine_is_closed(self):
        """Otherwise Python warns "coroutine was never awaited" and it leaks."""
        with patch("vlm.vlm_client.AsyncVLMClient", side_effect=RuntimeError("nope")):
            rt = AsyncVLMRuntime({})
            coroutine = asyncio.sleep(0)
            try:
                with pytest.raises(RuntimeError):
                    rt.submit_coroutine(coroutine)
                assert coroutine.cr_running is False
                assert coroutine.cr_frame is None  # closed
            finally:
                rt.stop(timeout=5)


class TestRuntimeStop:
    def test_stop_before_startup_is_a_noop(self):
        rt = AsyncVLMRuntime({})
        rt.stop()
        assert rt._thread is None

    def test_stop_joins_the_thread_and_clears_state(self, runtime):
        runtime.run_coroutine(asyncio.sleep(0))
        thread = runtime._thread

        runtime.stop(timeout=5)

        assert not thread.is_alive()
        assert runtime._thread is None
        assert runtime._loop is None
        assert runtime._stopping is False

    def test_stop_is_idempotent(self, runtime):
        runtime.run_coroutine(asyncio.sleep(0))
        runtime.stop(timeout=5)
        runtime.stop(timeout=5)
        assert runtime._thread is None

    def test_the_client_is_closed_on_shutdown(self, runtime):
        runtime.run_coroutine(asyncio.sleep(0))
        client = runtime._client

        runtime.stop(timeout=5)

        client.aclose.assert_awaited_once()

    def test_a_failing_aclose_does_not_block_shutdown(self, runtime):
        runtime.run_coroutine(asyncio.sleep(0))
        runtime._client.aclose = AsyncMock(side_effect=RuntimeError("close failed"))

        runtime.stop(timeout=5)

        assert runtime._thread is None

    def test_a_new_submit_restarts_the_runtime(self, runtime):
        runtime.run_coroutine(asyncio.sleep(0))
        first = runtime._thread
        runtime.stop(timeout=5)

        assert runtime.run_coroutine(asyncio.sleep(0)) is None
        assert runtime._thread is not first

    def test_submitting_while_stopping_is_refused(self, runtime):
        runtime.run_coroutine(asyncio.sleep(0))
        coroutine = asyncio.sleep(0)
        with runtime._lock:
            runtime._stopping = True
        try:
            with pytest.raises(RuntimeError, match="Async VLM runtime is stopping"):
                runtime.submit_coroutine(coroutine)
            assert coroutine.cr_frame is None  # closed
        finally:
            with runtime._lock:
                runtime._stopping = False


class TestRuntimeVLMDelegation:
    def test_analyze_video_url_delegates_to_the_client(self, runtime):
        runtime.run_coroutine(asyncio.sleep(0))
        runtime._client.analyze_video_url = AsyncMock(return_value="message")

        result = runtime.analyze_video_url("http://host/v.mp4", "p", "sys", num_frames=4)

        assert result == "message"
        runtime._client.analyze_video_url.assert_awaited_once_with(
            "http://host/v.mp4", "p", "sys", num_frames=4, config_overrides=None
        )

    def test_analyze_video_with_base64_delegates_to_the_client(self, runtime):
        runtime.run_coroutine(asyncio.sleep(0))
        runtime._client.analyze_video_with_base64 = AsyncMock(return_value="message")

        overrides = {"model": "other"}
        result = runtime.analyze_video_with_base64(
            "http://host/v.mp4", "p", None, config_overrides=overrides
        )

        assert result == "message"
        runtime._client.analyze_video_with_base64.assert_awaited_once_with(
            "http://host/v.mp4", "p", None, num_frames=10, config_overrides=overrides
        )

    @pytest.mark.asyncio
    async def test_async_helpers_reject_an_uninitialised_client(self):
        rt = AsyncVLMRuntime({})
        with pytest.raises(RuntimeError, match="Async VLM client is not initialized"):
            await rt.analyze_video_url_async("http://host/v.mp4", "p", None)

    @pytest.mark.asyncio
    async def test_base64_helper_rejects_an_uninitialised_client(self):
        rt = AsyncVLMRuntime({})
        with pytest.raises(RuntimeError, match="Async VLM client is not initialized"):
            await rt.analyze_video_with_base64_async("http://host/v.mp4", "p", None)
