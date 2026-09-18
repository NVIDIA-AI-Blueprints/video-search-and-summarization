######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
######################################################################################################
"""Regression coverage for Qwen3-VL multimodal encoder-cache reuse."""

from types import SimpleNamespace

import pytest


@pytest.mark.no_gpu
def test_qwen3_vl_skips_cache_reused_feature_without_metadata():
    """A cache-reused feature has no data after vLLM frees its metadata."""
    from vllm.model_executor.models.qwen3_vl import Qwen3VLForConditionalGeneration

    reused_video_feature = SimpleNamespace(
        modality="video",
        data=None,
        mm_position=SimpleNamespace(offset=0),
    )

    assert list(
        Qwen3VLForConditionalGeneration._iter_mm_grid_hw(
            input_tokens=[],
            mm_features=[reused_video_feature],
            video_token_id=1,
            vision_start_token_id=2,
            vision_end_token_id=3,
            spatial_merge_size=2,
        )
    ) == []
