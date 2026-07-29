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

"""End-to-end tests for ``_process_single_message_async`` with real helpers.

``test_event_loop_pipeline_mixin.py`` covers each stage of the pipeline in
isolation, which necessarily mocks the handler's own helpers. That leaves a
gap: wrong arguments between stages, a missing message mutation, an incorrect
failure classification, or broken ordering would all still pass.

This file closes that gap. The host is a real ``AnomalyEnhancer`` with its
``__init__`` bypassed, so every helper the pipeline calls
(``_compute_fingerprint``, ``_transform_video_urls``, ``_apply_vlm_response``,
``_apply_vlm_exception``, ``_apply_vlm_parse_failure``,
``_handle_media_collection_failure``, ``_handle_url_validation_failure``,
``_classify_vst_failure``, ``_complete_event_after_publish``) is the
production implementation. Only the process boundaries are mocked: VST, the
VLM runtime, the sink, the prompt manager and the verdict store.

Assertions are on observable outcomes — what reaches the sink, how the message
was mutated, which failure reason was recorded — not on "was my own helper
called".
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai import APITimeoutError

from enhance_alert_with_vlm import AnomalyEnhancer
from vst.exceptions import VSTTimeoutError

MESSAGE = {
    "sensorId": "cam-1",
    "category": "collision",
    "timestamp": "2021-01-01T00:00:00.000Z",
    "end": "2021-01-01T00:00:10.000Z",
    "objectIds": ["obj-1"],
}

VST_RESULT = ("http://vst/clip.mp4", "2021-01-01T00:00:00.000Z", "2021-01-01T00:00:10.000Z")

# Cosmos Reason wire format the built-in parser understands.
VLM_OK = "<think>two vehicles collided</think><answer>yes</answer>"


class _Pipeline(AnomalyEnhancer):
    """A real AnomalyEnhancer with construction bypassed.

    ``AnomalyEnhancer.__init__`` builds Kafka, Elasticsearch and VLM clients
    from config.yaml. Skipping it keeps the real methods while letting the
    test wire mocked boundaries in their place.
    """

    def __init__(self, sink, **overrides):  # noqa: D107 - deliberately not calling super()
        self.config = {"vst_config": {}, "vlm": {}, "alert_agent": {}}
        self._global_vlm_config = {
            "model": "nvidia/cosmos-reason2-8b",
            "num_frames": 10,
            "max_retries": 0,
        }

        self.prompt_manager = MagicMock()
        self.prompt_manager.get_prompts_for_message.return_value = ("user prompt", "system prompt")
        self.prompt_manager.alert_config_loader = None

        self.redis_handler = None
        self.vlm_enhanced_event_sink = sink

        self._vst_handler = MagicMock()
        self._vst_handler.get_video_stream_url_async = AsyncMock(return_value=VST_RESULT)

        self.async_vlm_runtime = MagicMock()
        self.async_vlm_runtime.analyze_video_url_async = AsyncMock(
            return_value=SimpleNamespace(content=VLM_OK)
        )
        self.async_vlm_runtime.analyze_video_with_base64_async = AsyncMock(
            return_value=SimpleNamespace(content=VLM_OK)
        )

        self.vlm_client = SimpleNamespace(
            model="nvidia/cosmos-reason2-8b", base_url="http://vlm:8080/v1", config={}
        )

        self.enrichment_processor = MagicMock()
        self.enrichment_processor.is_enabled_for.return_value = False
        self.enrichment_processor.process_async = AsyncMock(return_value=None)
        self._pluggable_parser = None
        self._alert_config_store = None
        self.url_transform_enabled = False
        self.include_latency_info = False
        self.vlm_media_source_using_base64 = False
        self.async_elastic_enabled = False
        self._vlm_capacity = None
        self._vst_capacity = None
        self._event_loop_http_client = MagicMock()

        self.__dict__.update(overrides)


def _valid_url_response(status_code=200, content_length="2000"):
    response = MagicMock()
    response.status_code = status_code
    response.headers = {"content-type": "video/mp4", "content-length": content_length}
    stream = MagicMock()
    stream.__aenter__ = AsyncMock(return_value=response)
    stream.__aexit__ = AsyncMock(return_value=False)
    client = MagicMock()
    client.stream.return_value = stream
    return client


@pytest.fixture
def sink():
    sink = MagicMock()
    sink.publish_success_async = AsyncMock()
    sink.publish_error_async = AsyncMock()
    return sink


@pytest.fixture
def pipeline(sink):
    pipeline = _Pipeline(sink)
    pipeline._event_loop_http_client = _valid_url_response()
    return pipeline


async def run(pipeline, message):
    """Drive one message through the whole coroutine pipeline."""
    await pipeline._process_single_message_async(worker_id=0, message=message)


def published_document(sink):
    """Return the message the sink actually received.

    The VLM outcome goes through the awaited ``*_async`` sink methods, while
    the pre-VLM failure paths (media collection, URL validation) publish
    through the synchronous sink API.
    """
    for mock in (sink.publish_success_async, sink.publish_error_async):
        if mock.await_args is not None:
            return mock.await_args.args[0]
    for mock in (sink.publish_success, sink.publish_error):
        if mock.call_args is not None:
            return mock.call_args.args[0]
    return None


def error_published(sink):
    """True when the message was published on any error path."""
    return sink.publish_error_async.await_args is not None or (
        sink.publish_error.call_args is not None
    )


def success_published(sink):
    return sink.publish_success_async.await_args is not None or (
        sink.publish_success.call_args is not None
    )


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_a_verified_event_reaches_publish_success(self, pipeline, sink):
        await run(pipeline, dict(MESSAGE))

        sink.publish_success_async.assert_awaited_once()
        sink.publish_error_async.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_published_document_carries_the_vlm_verdict(self, pipeline, sink):
        await run(pipeline, dict(MESSAGE))

        document = published_document(sink)
        assert document["info"]["verdict"]
        assert str(document["info"]["verificationResponseCode"]) == "200"

    @pytest.mark.asyncio
    async def test_the_fingerprint_is_stamped_before_publishing(self, pipeline, sink):
        await run(pipeline, dict(MESSAGE))

        assert published_document(sink)["Id"]

    @pytest.mark.asyncio
    async def test_the_resolved_video_url_is_recorded_on_the_document(self, pipeline, sink):
        await run(pipeline, dict(MESSAGE))

        assert published_document(sink)["info"]["videoSource"] == "http://vst/clip.mp4"

    @pytest.mark.asyncio
    async def test_the_prompts_reach_the_vlm_and_the_sink(self, pipeline, sink):
        await run(pipeline, dict(MESSAGE))

        vlm_args = pipeline.async_vlm_runtime.analyze_video_url_async.await_args.args
        assert vlm_args[1] == "user prompt"
        assert vlm_args[2] == "system prompt"
        assert sink.publish_success_async.await_args.args[1] == "user prompt"

    @pytest.mark.asyncio
    async def test_the_vst_window_comes_from_the_message(self, pipeline):
        await run(pipeline, dict(MESSAGE))

        args = pipeline._vst_handler.get_video_stream_url_async.await_args.args
        assert args[2] == MESSAGE["timestamp"]
        assert args[3] == MESSAGE["end"]


class TestMessagesMissingTimeFields:
    """A message without ``timestamp``/``end`` must not crash the pipeline.

    The VST call reads both keys unguarded, so the KeyError has to be absorbed
    and turned into a media-collection failure — this is the outcome the
    service depends on, and it was previously only reached implicitly.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("missing", ["timestamp", "end"])
    async def test_a_missing_time_field_does_not_raise(self, pipeline, sink, missing):
        message = dict(MESSAGE)
        del message[missing]

        await run(pipeline, message)

        assert error_published(sink)
        assert not success_published(sink)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("missing", ["timestamp", "end"])
    async def test_a_missing_time_field_is_reported_as_a_media_failure(
        self, pipeline, sink, missing
    ):
        message = dict(MESSAGE)
        del message[missing]

        await run(pipeline, message)

        info = published_document(sink)["info"]
        assert str(info["verificationResponseCode"]) == "404"
        assert "No video recording found" in info["verificationResponseStatus"]

    @pytest.mark.asyncio
    async def test_a_message_with_neither_time_field_does_not_raise(self, pipeline, sink):
        message = {"sensorId": "cam-1", "category": "collision"}

        await run(pipeline, message)

        assert error_published(sink)

    @pytest.mark.asyncio
    async def test_the_vlm_is_never_called_without_a_video(self, pipeline):
        message = dict(MESSAGE)
        del message["timestamp"]

        await run(pipeline, message)

        pipeline.async_vlm_runtime.analyze_video_url_async.assert_not_awaited()


class TestVstFailures:
    @pytest.mark.asyncio
    async def test_a_vst_timeout_is_classified_as_504(self, pipeline, sink):
        pipeline._vst_handler.get_video_stream_url_async = AsyncMock(
            side_effect=VSTTimeoutError("timed out", category="timeout")
        )

        await run(pipeline, dict(MESSAGE))

        info = published_document(sink)["info"]
        assert str(info["verificationResponseCode"]) == "504"
        assert "timed out" in info["verificationResponseStatus"].lower()

    @pytest.mark.asyncio
    async def test_no_recording_is_classified_as_404(self, pipeline, sink):
        pipeline._vst_handler.get_video_stream_url_async = AsyncMock(
            return_value=(None, None, None)
        )

        await run(pipeline, dict(MESSAGE))

        assert str(published_document(sink)["info"]["verificationResponseCode"]) == "404"

    @pytest.mark.asyncio
    async def test_a_url_that_fails_validation_is_reported_as_400(self, pipeline, sink):
        pipeline._event_loop_http_client = _valid_url_response(content_length="0")

        await run(pipeline, dict(MESSAGE))

        info = published_document(sink)["info"]
        assert str(info["verificationResponseCode"]) == "400"
        assert pipeline.async_vlm_runtime.analyze_video_url_async.await_count == 0


class TestVlmFailures:
    @pytest.mark.asyncio
    async def test_a_vlm_timeout_is_classified_and_published_as_an_error(self, pipeline, sink):
        pipeline.async_vlm_runtime.analyze_video_url_async = AsyncMock(
            side_effect=APITimeoutError(request=MagicMock())
        )

        await run(pipeline, dict(MESSAGE))

        sink.publish_error_async.assert_awaited_once()
        assert str(published_document(sink)["info"]["verificationResponseCode"]) == "504"

    @pytest.mark.asyncio
    async def test_an_unparseable_response_is_published_as_an_error(self, pipeline, sink):
        pipeline.async_vlm_runtime.analyze_video_url_async = AsyncMock(
            return_value=SimpleNamespace(content="this is not a verdict")
        )

        await run(pipeline, dict(MESSAGE))

        sink.publish_error_async.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_response_object_without_content_does_not_crash(self, pipeline, sink):
        """A malformed VLM response must degrade, not raise."""
        pipeline.async_vlm_runtime.analyze_video_url_async = AsyncMock(
            return_value=SimpleNamespace()
        )

        await run(pipeline, dict(MESSAGE))

        sink.publish_error_async.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_none_response_does_not_crash(self, pipeline, sink):
        pipeline.async_vlm_runtime.analyze_video_url_async = AsyncMock(return_value=None)

        await run(pipeline, dict(MESSAGE))

        sink.publish_error_async.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_transient_failure_is_retried_before_giving_up(self, pipeline, sink):
        pipeline._global_vlm_config = dict(pipeline._global_vlm_config, max_retries=1)
        pipeline.async_vlm_runtime.analyze_video_url_async = AsyncMock(
            side_effect=[
                APITimeoutError(request=MagicMock()),
                SimpleNamespace(content=VLM_OK),
            ]
        )

        with patch("asyncio.sleep", AsyncMock()):
            await run(pipeline, dict(MESSAGE))

        assert pipeline.async_vlm_runtime.analyze_video_url_async.await_count == 2
        sink.publish_success_async.assert_awaited_once()


class TestSkipAndPromptGating:
    @pytest.mark.asyncio
    async def test_a_confirmed_verdict_skips_the_whole_pipeline(self, pipeline, sink):
        handler = MagicMock()
        handler.is_verdict_confirmed_async = AsyncMock(return_value=True)
        pipeline.redis_handler = handler

        await run(pipeline, dict(MESSAGE))

        pipeline._vst_handler.get_video_stream_url_async.assert_not_awaited()
        sink.publish_success_async.assert_not_awaited()
        sink.publish_error_async.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_message_without_a_prompt_is_dropped_before_vst(self, pipeline, sink):
        pipeline.prompt_manager.get_prompts_for_message.return_value = (None, None)

        await run(pipeline, dict(MESSAGE))

        pipeline._vst_handler.get_video_stream_url_async.assert_not_awaited()
        sink.publish_success_async.assert_not_awaited()
