######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
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
