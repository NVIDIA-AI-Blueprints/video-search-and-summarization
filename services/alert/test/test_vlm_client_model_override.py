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

"""Regression tests for VLMClient model-override payload shaping.

When ``vlm_params.model`` switches an alert type between model families
(e.g. CR2 default → CR1 override) the request body — both the message
content layout and ``extra_body`` shape — must be rebuilt for the
overridden model. Otherwise the wire payload is incompatible with the
model the API call actually targets.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models.responses import ModelType
from vlm.vlm_client import VLMClient


def _make_client(default_model: str) -> VLMClient:
    config = {
        "base_url": "http://localhost/v1",
        "model": default_model,
        "use_vlm_media_defaults": False,
        "min_pixels": 1568,
        "max_pixels": 345600,
        "enable_sampling": False,
    }
    client = VLMClient.__new__(VLMClient)
    client.config = config
    client.base_url = config["base_url"]
    client.model = config["model"]
    client.max_tokens = None
    client.temperature = None
    client.stream = False
    client.api_key = "not-used"
    client.request_timeout = 5
    client.use_vlm_media_defaults = False
    client.client = MagicMock()
    client.client.chat.completions.create = MagicMock(return_value=MagicMock(
        choices=[MagicMock(message=MagicMock(content="ok"))],
        usage=None,
    ))
    return client


class TestModelOverrideShapesPayload:

    def test_get_model_type_uses_override(self):
        # detect_model_type now returns the unified COSMOS_REASON for both
        # cosmos-reason1 and cosmos-reason2 prefixes (CR1/CR2 are kept as
        # legacy aliases but never returned by detection).
        client = _make_client("nvidia/cosmos-reason2-8b")
        assert client._get_model_type() == ModelType.COSMOS_REASON
        assert (
            client._get_model_type("nvidia/cosmos-reason1-7b")
            == ModelType.COSMOS_REASON
        )

    @pytest.mark.skip(
        reason="CR1/CR2 layout was unified to COSMOS_REASON. detect_model_type "
        "no longer returns CR1, so the videos_kwargs branch is unreachable."
    )
    def test_extra_body_shape_uses_overridden_model(self):
        client = _make_client("nvidia/cosmos-reason2-8b")  # default = CR2 family
        body = client._build_extra_body(
            video=True,
            num_frames=8,
            config_overrides={"model": "nvidia/cosmos-reason1-7b"},  # CR1
        )
        # CR1 layout uses ``videos_kwargs`` instead of ``size``.
        assert "mm_processor_kwargs" in body
        assert "videos_kwargs" in body["mm_processor_kwargs"]
        assert "size" not in body["mm_processor_kwargs"]

    def test_extra_body_shape_default_model_unchanged(self):
        client = _make_client("nvidia/cosmos-reason2-8b")
        body = client._build_extra_body(video=True, num_frames=8)
        # Default CR2 family uses the ``size`` envelope.
        assert "size" in body["mm_processor_kwargs"]

    @pytest.mark.skip(
        reason="CR1/CR2 message layouts were unified. detect_model_type no "
        "longer returns CR1, so the text-first override branch is unreachable."
    )
    def test_messages_layout_swaps_for_cr1_override(self):
        client = _make_client("nvidia/cosmos-reason2-8b")
        cr2_messages = client._build_messages_with_media(
            "video", "https://example/video.mp4", "user prompt"
        )
        cr1_messages = client._build_messages_with_media(
            "video", "https://example/video.mp4", "user prompt",
            model_override="nvidia/cosmos-reason1-7b",
        )

        cr2_content = cr2_messages[-1]["content"]
        cr1_content = cr1_messages[-1]["content"]
        # CR2: media first, text last. CR1: text first, media last.
        assert cr2_content[0]["type"] == "video_url"
        assert cr2_content[-1]["type"] == "text"
        assert cr1_content[0]["type"] == "text"
        assert cr1_content[-1]["type"] == "video_url"

    def test_create_chat_sends_overridden_model_to_api(self):
        client = _make_client("nvidia/cosmos-reason2-8b")
        client._create_chat(
            messages=[{"role": "user", "content": []}],
            video=True,
            config_overrides={"model": "nvidia/cosmos-reason1-7b"},
        )
        kwargs = client.client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "nvidia/cosmos-reason1-7b"
