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

from types import SimpleNamespace

import pytest
import torch
from torch import nn
from torchvision.transforms import functional as tvF
from transformers.image_utils import PILImageResampling, SizeDict

from vllm_cosmos3.edge_native import (
    Cosmos3EdgeAttention,
    Cosmos3EdgeTextRMSNorm,
    Cosmos3EdgeVisionEmbeddings,
    Cosmos3EdgeVisionModel,
)
from vllm_cosmos3.edge_processor import Cosmos3EdgeVideoProcessor


def _reference_rms_norm(value, weight, eps):
    normalized = value.float()
    variance = normalized.pow(2).mean(-1, keepdim=True)
    normalized = normalized * torch.rsqrt(variance + eps)
    return weight * normalized.to(value.dtype)


def test_text_rms_norm_matches_checkpoint_order_with_residual():
    norm = Cosmos3EdgeTextRMSNorm(4, eps=1e-6).to(torch.bfloat16)
    norm.weight.data.copy_(torch.tensor([0.5, 1.0, 1.5, 2.0]))
    hidden = torch.tensor([[1.25, -2.5, 3.75, -4.0]], dtype=torch.bfloat16)
    residual = torch.tensor([[0.5, 0.25, -0.5, 1.0]], dtype=torch.bfloat16)

    output, residual_output = norm(hidden, residual)
    expected_residual = hidden + residual

    assert torch.equal(residual_output, expected_residual)
    assert torch.equal(
        output,
        _reference_rms_norm(expected_residual, norm.weight, norm.variance_epsilon),
    )


def test_mrope_inverse_frequencies_are_initialized_on_cpu(monkeypatch):
    def initialize_attention(module, *_args, **_kwargs):
        nn.Module.__init__(module)
        module.head_dim = 128
        module.qkv_proj = SimpleNamespace(weight=torch.empty(1, device="cpu"))

    monkeypatch.setattr(
        "vllm_cosmos3.edge_native.NemotronHAttention.__init__",
        initialize_attention,
    )
    config = SimpleNamespace(
        rope_parameters={
            "rope_theta": 100000000.0,
            "mrope_section": [24, 20, 20],
        }
    )

    with torch.device("meta"):
        attention = Cosmos3EdgeAttention(config, layer_idx=0)

    base = 1.0 / (100000000.0 ** (torch.arange(0, 128, 2, dtype=torch.float, device="cpu") / 128))
    indices = torch.arange(64, device="cpu")
    height_mask = (indices % 3 == 1) & (indices < 60)
    width_mask = (indices % 3 == 2) & (indices < 60)
    expected = torch.stack(
        (base * ~(height_mask | width_mask), base * height_mask, base * width_mask)
    )

    assert attention.edge_inv_freq.device.type == "cpu"
    assert torch.equal(attention.edge_inv_freq, expected)


def test_position_embeddings_follow_block_major_patch_order():
    embeddings = object.__new__(Cosmos3EdgeVisionEmbeddings)
    embeddings.spatial_merge_size = 2
    positions = torch.arange(16, dtype=torch.float32).reshape(4, 4, 1)

    result = embeddings.resize_positional_embeddings_packed(
        positions,
        torch.tensor([[4, 4]]),
        [16],
    )

    assert result[:, 0].tolist() == [
        0,
        1,
        4,
        5,
        2,
        3,
        6,
        7,
        8,
        9,
        12,
        13,
        10,
        11,
        14,
        15,
    ]


class _Encoder(nn.Module):
    dtype = torch.float32

    def encode(self, _pixel_values, _grid_thw):
        return torch.arange(16, dtype=torch.float32).reshape(16, 1)


class _Projector(nn.Module):
    input_hidden_size = 1

    def __init__(self):
        super().__init__()
        self.input = None

    def forward(self, value):
        self.input = value
        return value


def test_projector_groups_existing_consecutive_patch_blocks():
    model = object.__new__(Cosmos3EdgeVisionModel)
    nn.Module.__init__(model)
    model.spatial_merge_size = 2
    model.encoder = _Encoder()
    model.projector = _Projector()

    model(torch.empty(0), [[1, 4, 4]])

    assert model.projector.input.tolist() == [
        [[0], [1], [2], [3]],
        [[4], [5], [6], [7]],
        [[8], [9], [10], [11]],
        [[12], [13], [14], [15]],
    ]


def test_video_resize_forwards_bicubic_interpolation(monkeypatch):
    captured = {}
    resize = tvF.resize

    def capture_resize(*args, **kwargs):
        captured.update(kwargs)
        return resize(*args, **kwargs)

    monkeypatch.setattr(tvF, "resize", capture_resize)
    processor = Cosmos3EdgeVideoProcessor()
    processor._preprocess(
        videos=[torch.zeros((2, 3, 64, 96), dtype=torch.uint8)],
        size=SizeDict(shortest_edge=4096, longest_edge=2 * 64 * 96),
        resample=PILImageResampling.BICUBIC,
        do_rescale=False,
        do_normalize=False,
        patch_size=16,
    )

    assert captured["interpolation"] == PILImageResampling.BICUBIC
    assert captured["antialias"] is True


def test_video_processor_emits_merge_block_major_patches():
    processor = Cosmos3EdgeVideoProcessor()
    video = torch.zeros((1, 3, 64, 64), dtype=torch.uint8)
    for row in range(4):
        for column in range(4):
            video[:, :, row * 16 : (row + 1) * 16, column * 16 : (column + 1) * 16] = (
                row * 4 + column
            )

    patches = processor._preprocess(
        videos=[video],
        do_resize=False,
        do_rescale=False,
        do_normalize=False,
        patch_size=16,
    )["pixel_values_videos"]

    assert patches[:, 0].tolist() == [
        0,
        1,
        4,
        5,
        2,
        3,
        6,
        7,
        8,
        9,
        12,
        13,
        10,
        11,
        14,
        15,
    ]


def test_video_processor_rejects_unaligned_no_resize_input():
    processor = Cosmos3EdgeVideoProcessor()

    with pytest.raises(ValueError, match=r"divisible by patch_size \* merge_size"):
        processor._preprocess(
            videos=[torch.zeros((1, 3, 64, 65), dtype=torch.uint8)],
            do_resize=False,
            do_rescale=False,
            do_normalize=False,
            patch_size=16,
        )
