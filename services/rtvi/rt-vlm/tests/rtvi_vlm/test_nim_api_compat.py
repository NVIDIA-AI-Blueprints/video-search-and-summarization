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
"""Tests for NIM API compatibility — media_io_kwargs and mm_processor_kwargs passthrough."""

import os
import subprocess
import sys
import textwrap
from uuid import uuid4

import pytest
from pydantic import ValidationError

from api_models.captions import VlmQuery
from api_models.common import MAX_GENERATION_TOKENS, MAX_GENERATION_TOKENS_ENV
from api_models.nim_compat import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionUsage,
    ChatMessage,
    CompletionRequest,
)
from utils.media_io_kwargs import get_frame_sampling_params_from_media_io_kwargs
from vlm_pipeline.vlm_pipeline import VlmRequestParams


def _vlm_query(**overrides):
    data = {
        "id": uuid4(),
        "prompt": "Describe the video.",
        "model": "test-model",
    }
    data.update(overrides)
    return VlmQuery(**data)


class TestVlmQueryValidation:
    """Test NIM-compatible values accepted by VlmQuery."""

    def test_system_prompt_accepts_10240_chars(self):
        """system_prompt should support CR3 prompts up to 10,240 chars."""
        system_prompt = "x" * 10240

        query = _vlm_query(system_prompt=system_prompt)

        assert query.system_prompt == system_prompt

    def test_system_prompt_rejects_above_10240_chars(self):
        """system_prompt should reject payloads above the documented limit."""
        with pytest.raises(ValidationError, match="10240"):
            _vlm_query(system_prompt="x" * 10241)

    def test_system_prompt_limit_can_be_raised_with_env(self, monkeypatch):
        """VLM_SYSTEM_PROMPT_MAX_LENGTH should allow longer CR3 system prompts."""
        monkeypatch.setenv("VLM_SYSTEM_PROMPT_MAX_LENGTH", "10241")

        query = _vlm_query(system_prompt="x" * 10241)

        assert len(query.system_prompt) == 10241

    def test_system_prompt_limit_error_mentions_env_var(self, monkeypatch):
        """Oversized system_prompt errors should point users at the env override."""
        monkeypatch.delenv("VLM_SYSTEM_PROMPT_MAX_LENGTH", raising=False)

        with pytest.raises(ValidationError) as exc_info:
            _vlm_query(system_prompt="x" * 10241)

        message = str(exc_info.value)
        assert "10240" in message
        assert "VLM_SYSTEM_PROMPT_MAX_LENGTH" in message

    def test_prompt_accepts_10240_chars(self):
        """prompt should support large dense-caption prompts up to 10,240 chars."""
        prompt = "x" * 10240

        query = _vlm_query(prompt=prompt)

        assert query.prompt == prompt

    def test_prompt_rejects_above_10240_chars(self):
        """prompt should reject payloads above the documented limit."""
        with pytest.raises(ValidationError, match="10240"):
            _vlm_query(prompt="x" * 10241)

    def test_prompt_rejects_empty_string(self):
        """prompt should reject empty strings."""
        with pytest.raises(ValidationError, match="prompt must not be empty"):
            _vlm_query(prompt="")

    def test_prompt_rejects_whitespace_only(self):
        """prompt should reject whitespace-only values."""
        with pytest.raises(ValidationError, match="prompt must not be empty"):
            _vlm_query(prompt=" \n\t")

    def test_prompt_limit_can_be_raised_with_env(self, monkeypatch):
        """VLM_PROMPT_MAX_LENGTH should allow longer prompts."""
        monkeypatch.setenv("VLM_PROMPT_MAX_LENGTH", "10241")

        query = _vlm_query(prompt="x" * 10241)

        assert len(query.prompt) == 10241

    def test_prompt_limit_error_mentions_env_var(self, monkeypatch):
        """Oversized prompt errors should point users at the env override."""
        monkeypatch.delenv("VLM_PROMPT_MAX_LENGTH", raising=False)

        with pytest.raises(ValidationError) as exc_info:
            _vlm_query(prompt="x" * 10241)

        message = str(exc_info.value)
        assert "10240" in message
        assert "VLM_PROMPT_MAX_LENGTH" in message

    def test_media_io_num_frames_minus_one_is_valid(self):
        """NIM/vLLM accepts num_frames=-1 to select all decoded frames."""
        query = _vlm_query(media_io_kwargs={"video": {"num_frames": -1}})

        assert query.media_io_kwargs == {"video": {"num_frames": -1}}

    def test_preserve_reasoning_tags_reaches_generation_config(self):
        """Internal chat/completions flag should reach model generation config."""
        query = _vlm_query(preserve_reasoning_tags=True)

        params = VlmRequestParams.from_vlm_query(query)

        assert params.vlm_generation_config.preserve_reasoning_tags is True

    def test_media_io_num_frames_zero_is_invalid(self):
        """num_frames must be positive unless it is the NIM -1 sentinel."""
        with pytest.raises(ValidationError, match="or -1"):
            _vlm_query(media_io_kwargs={"video": {"num_frames": 0}})

    def test_chat_completion_request_accepts_num_frames_minus_one(self):
        """Chat completions should accept the NIM-compatible num_frames=-1 value."""
        request = ChatCompletionRequest(
            model="test-model",
            messages=[ChatMessage(role="user", content="Describe this video.")],
            id=uuid4(),
            media_io_kwargs={"video": {"num_frames": -1}},
        )

        assert request.media_io_kwargs == {"video": {"num_frames": -1}}

    def test_chat_completion_request_accepts_direct_frame_count_minus_one(self):
        """Chat completions should accept -1 through the RTVI frame count field."""
        request = ChatCompletionRequest(
            model="test-model",
            messages=[ChatMessage(role="user", content="Describe this video.")],
            id=uuid4(),
            num_frames_per_second_or_fixed_frames_chunk=-1,
            use_fps_for_chunking=False,
        )

        assert request.num_frames_per_second_or_fixed_frames_chunk == -1

    def test_fixed_frame_count_minus_one_is_valid_for_all_frames(self):
        """The local frame selector uses -1 as the all-frames sentinel."""
        query = _vlm_query(
            num_frames_per_second_or_fixed_frames_chunk=-1,
            use_fps_for_chunking=False,
        )

        assert query.num_frames_per_second_or_fixed_frames_chunk == -1

    def test_fixed_frame_count_minus_one_rejects_fps_mode(self):
        """The -1 sentinel is only meaningful for fixed-frame selection."""
        with pytest.raises(ValidationError, match="use_fps_for_chunking"):
            _vlm_query(
                num_frames_per_second_or_fixed_frames_chunk=-1,
                use_fps_for_chunking=True,
            )

    def test_vlm_query_accepts_16k_generation_tokens(self):
        """Cosmos Reason2 can request 16K output tokens through generate_captions."""
        query = _vlm_query(max_tokens=MAX_GENERATION_TOKENS, min_tokens=MAX_GENERATION_TOKENS)

        assert query.max_tokens == MAX_GENERATION_TOKENS
        assert query.min_tokens == MAX_GENERATION_TOKENS

    def test_vlm_query_rejects_generation_tokens_above_16k(self):
        with pytest.raises(ValidationError):
            _vlm_query(max_tokens=MAX_GENERATION_TOKENS + 1)

    def test_chat_completion_request_accepts_16k_generation_tokens(self):
        request = ChatCompletionRequest(
            model="test-model",
            messages=[ChatMessage(role="user", content="Describe this video.")],
            id=uuid4(),
            max_tokens=MAX_GENERATION_TOKENS,
            max_completion_tokens=MAX_GENERATION_TOKENS,
            min_tokens=MAX_GENERATION_TOKENS,
        )

        assert request.max_tokens == MAX_GENERATION_TOKENS
        assert request.max_completion_tokens == MAX_GENERATION_TOKENS
        assert request.min_tokens == MAX_GENERATION_TOKENS

    def test_completion_request_accepts_16k_generation_tokens(self):
        request = CompletionRequest(
            model="test-model",
            prompt="Describe this video.",
            max_tokens=MAX_GENERATION_TOKENS,
        )

        assert request.max_tokens == MAX_GENERATION_TOKENS

    def test_generation_token_limit_can_be_raised_with_env_at_startup(self):
        """VLM_MAX_GENERATION_TOKENS is read when API models are imported."""
        env = os.environ.copy()
        env[MAX_GENERATION_TOKENS_ENV] = "20000"
        env.setdefault("LOG_FILE_PATH", "/tmp/rtvi-test.log")
        code = textwrap.dedent(
            """
            from uuid import uuid4

            from pydantic import ValidationError

            from api_models.captions import VlmQuery
            from api_models.common import MAX_GENERATION_TOKENS
            from api_models.nim_compat import ChatCompletionRequest, ChatMessage

            assert MAX_GENERATION_TOKENS == 20000
            VlmQuery(id=uuid4(), prompt="p", model="m", max_tokens=20000)
            ChatCompletionRequest(
                model="m",
                messages=[ChatMessage(role="user", content="p")],
                max_tokens=20000,
            )
            try:
                VlmQuery(id=uuid4(), prompt="p", model="m", max_tokens=20001)
            except ValidationError:
                pass
            else:
                raise AssertionError("expected max_tokens above env cap to fail")
            """
        )

        subprocess.run([sys.executable, "-c", code], check=True, env=env)


class TestMediaIoKwargsMapping:
    """Test media_io_kwargs → RTVI frame extraction param mapping."""

    def test_fps_maps_to_use_fps_for_chunking(self):
        """media_io_kwargs.video.fps should set use_fps_for_chunking=True."""
        media_io_kwargs = {"video": {"fps": 3.0}}
        params = get_frame_sampling_params_from_media_io_kwargs(media_io_kwargs)
        assert params["num_frames_per_second_or_fixed_frames_chunk"] == 3.0
        assert params["use_fps_for_chunking"] is True

    def test_num_frames_maps_to_fixed_frames(self):
        """media_io_kwargs.video.num_frames should set use_fps_for_chunking=False."""
        media_io_kwargs = {"video": {"num_frames": 16}}
        params = get_frame_sampling_params_from_media_io_kwargs(media_io_kwargs)
        assert params["num_frames_per_second_or_fixed_frames_chunk"] == 16
        assert params["use_fps_for_chunking"] is False

    def test_num_frames_minus_one_maps_to_all_frames(self):
        """media_io_kwargs.video.num_frames=-1 should select all decoded frames."""
        media_io_kwargs = {"video": {"num_frames": -1}}

        params = get_frame_sampling_params_from_media_io_kwargs(media_io_kwargs)

        assert params["num_frames_per_second_or_fixed_frames_chunk"] == -1
        assert params["use_fps_for_chunking"] is False

    def test_fps_and_num_frames_mutually_exclusive(self):
        """Only fps OR num_frames should be used, not both."""
        with pytest.raises(ValidationError, match="mutually exclusive"):
            _vlm_query(media_io_kwargs={"video": {"fps": 3.0, "num_frames": 16}})

    def test_empty_media_io_kwargs(self):
        """Empty media_io_kwargs should not affect frame params."""
        media_io_kwargs = {}
        video_io = media_io_kwargs.get("video", {})
        assert video_io.get("fps") is None
        assert video_io.get("num_frames") is None

    def test_none_media_io_kwargs(self):
        """None media_io_kwargs should be handled gracefully."""
        media_io_kwargs = None
        assert media_io_kwargs is None

    def test_fps_value_types(self):
        """fps should work with int and float values."""
        for fps_val in [1, 2.0, 3.5, 0.5]:
            media_io_kwargs = {"video": {"fps": fps_val}}
            assert float(media_io_kwargs["video"]["fps"]) == float(fps_val)


class TestMmProcessorKwargs:
    """Test mm_processor_kwargs for image/video preprocessing."""

    def test_shortest_edge_config(self):
        """shortest_edge should be passed through."""
        kwargs = {"size": {"shortest_edge": 1568, "longest_edge": 262144}}
        assert kwargs["size"]["shortest_edge"] == 1568
        assert kwargs["size"]["longest_edge"] == 262144

    def test_default_cosmos_values(self):
        """Default Cosmos Reason2 values per NIM docs."""
        kwargs = {"size": {"shortest_edge": 3136, "longest_edge": 12845056}}
        assert kwargs["size"]["shortest_edge"] == 3136

    def test_chain_of_thought_for_cosmos(self):
        """Cosmos Reason1 adds chain_of_thought to mm_processor_kwargs."""
        mm_kwargs = {}
        model_type = "cosmos-reason1"
        if model_type == "cosmos-reason1":
            mm_kwargs["chain_of_thought"] = True
        # User kwargs should merge (not override)
        user_kwargs = {"size": {"shortest_edge": 1568}}
        mm_kwargs.update(user_kwargs)
        assert mm_kwargs["chain_of_thought"] is True
        assert mm_kwargs["size"]["shortest_edge"] == 1568


class TestNimApiRequestFormat:
    """Test that the full NIM API request body is supported."""

    def test_full_nim_request_fields(self):
        """All NIM Cosmos Reason2 API fields should be representable."""
        request = {
            "model": "nvidia/cosmos-reason2-2b",
            "messages": [{"role": "user", "content": "Describe this video."}],
            "max_tokens": 256,
            "stream": False,
            "temperature": 0.3,
            "top_p": 0.3,
            "mm_processor_kwargs": {"size": {"shortest_edge": 1568, "longest_edge": 262144}},
            "media_io_kwargs": {"video": {"fps": 3.0}},
        }
        assert request["temperature"] == 0.3
        assert request["mm_processor_kwargs"]["size"]["shortest_edge"] == 1568
        assert request["media_io_kwargs"]["video"]["fps"] == 3.0

    def test_media_io_kwargs_merges_into_mm_processor_kwargs(self):
        """media_io_kwargs should merge into mm_processor_kwargs for vLLM engine."""
        mm_processor_kwargs = {"chain_of_thought": True}
        media_io_kwargs = {"video": {"fps": 3.0}}
        # Merge (same as vllm_compatible_model.py line 632)
        mm_processor_kwargs.update(media_io_kwargs)
        assert mm_processor_kwargs["chain_of_thought"] is True
        assert mm_processor_kwargs["video"]["fps"] == 3.0


class TestChatCompletionResponseFormat:
    """Test NIM-compatible chat completion response extensions."""

    def test_chat_completion_response_accepts_reasoning_description(self):
        """Assistant messages should expose parsed reasoning text when present."""
        response = ChatCompletionResponse(
            id="chatcmpl-test",
            created=0,
            model="test-model",
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content="final answer",
                        reasoning_description="parsed reasoning",
                    ),
                    finish_reason="stop",
                )
            ],
            usage=ChatCompletionUsage(
                prompt_tokens=1,
                completion_tokens=2,
                total_tokens=3,
            ),
        )

        dumped = response.model_dump(mode="json")

        assert response.choices[0].message.reasoning_description == "parsed reasoning"
        assert dumped["choices"][0]["message"]["reasoning_description"] == "parsed reasoning"
