# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.

# Copyright 2025 The vLLM team.
# Copyright 2025 The Qwen Team.
# Copyright 2025 The HuggingFace Inc. team.
# All rights reserved.
#
# This code is based on EleutherAI's GPT-NeoX library and the GPT-NeoX
# and OPT implementations in this library. It has been modified from its
# original forms to accommodate minor architectural differences compared
# to GPT-NeoX and OPT used by the Meta AI team that trained the model.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Inference-only Qwen3VL model compatible with HuggingFace weights."""

# EVS post-processing (_get_expanded_positions) does a boolean-indexed
# assignment. In encoder-only + remote-PD mode, vLLM's execute_model path
# runs the encoder under a context that dispatches tensor indexing to the
# meta kernel, which invokes torch.nonzero()'s meta impl — unimplemented
# for data-dependent shapes by default. Enabling this flag makes the meta
# kernel return an over-approximation (assume all nonzero); real tensor
# execution is unaffected. Setting it at module import guarantees the
# flag is active in every subprocess that imports this model.
import torch.fx.experimental._config as _fx_config

_fx_config.meta_nonzero_assume_all_nonzero = True

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import replace as dataclass_replace
from functools import lru_cache, partial
from itertools import islice
from typing import Any

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from transformers import BatchFeature
from transformers.models.qwen2_vl import Qwen2VLImageProcessorFast
from transformers.models.qwen2_vl.image_processing_qwen2_vl import (
    smart_resize as image_smart_resize,
)
from transformers.models.qwen3_vl import Qwen3VLProcessor, Qwen3VLVideoProcessor
from transformers.models.qwen3_vl.configuration_qwen3_vl import (
    Qwen3VLConfig,
    Qwen3VLVisionConfig,
)
from transformers.models.qwen3_vl.video_processing_qwen3_vl import (
    smart_resize as video_smart_resize,
)
from transformers.video_utils import VideoMetadata

from vllm.compilation.decorators import support_torch_compile
from vllm.config import VllmConfig
from vllm.config.multimodal import BaseDummyOptions, VideoDummyOptions
from vllm.distributed import get_pp_group, parallel_state
from vllm.logger import init_logger
from vllm.model_executor.layers.activation import _ACTIVATION_REGISTRY
from vllm.model_executor.layers.attention.mm_encoder_attention import (
    MMEncoderAttention,
)
from vllm.model_executor.layers.conv import Conv3dLayer
from vllm.model_executor.layers.linear import (
    ColumnParallelLinear,
    RowParallelLinear,
)
from vllm.model_executor.layers.logits_processor import LogitsProcessor
from vllm.model_executor.layers.quantization import QuantizationConfig
from vllm.model_executor.layers.rotary_embedding import get_rope
from vllm.model_executor.layers.vocab_parallel_embedding import ParallelLMHead
from vllm.model_executor.model_loader.weight_utils import default_weight_loader
from vllm.model_executor.models.module_mapping import MultiModelKeys
from vllm.multimodal import MULTIMODAL_REGISTRY
from vllm.multimodal.inputs import (
    MultiModalDataDict,
    MultiModalFeatureSpec,
    MultiModalFieldConfig,
    MultiModalFieldElem,
    MultiModalInputs,
    MultiModalKwargsItem,
    MultiModalKwargsItems,
    PlaceholderRange,
    VideoItem,
)
from vllm.multimodal.parse import ImageSize, MultiModalDataItems
from vllm.multimodal.processing import (
    BaseDummyInputsBuilder,
    BaseMultiModalProcessor,
    PromptReplacement,
    PromptUpdate,
    PromptUpdateDetails,
)
from vllm.multimodal.processing.context import TimingContext
from vllm.multimodal.processing.inputs import ProcessorInputs
from vllm.sequence import IntermediateTensors
from vllm.tokenizers.protocol import TokenizerLike
from vllm.tokenizers.registry import cached_tokenizer_from_config
from vllm.utils.collection_utils import is_list_of
from vllm.utils.math_utils import round_up

from .interfaces import (
    MultiModalEmbeddingOutput,
    MultiModalEmbeddingReturn,
    MultiModalEmbeddings,
    SupportsEagle3,
    SupportsLoRA,
    SupportsMRoPE,
    SupportsMultiModal,
    SupportsMultiModalPruning,
    SupportsPP,
    _require_is_multimodal,
)
from .qwen2_5_vl import (
    Qwen2_5_VisionAttention,
    Qwen2_5_VLImageEmbeddingInputs,
    Qwen2_5_VLImageInputs,
    Qwen2_5_VLImagePixelInputs,
    Qwen2_5_VLVideoEmbeddingInputs,
    Qwen2_5_VLVideoInputs,
    Qwen2_5_VLVideoPixelInputs,
)
from .qwen2_vl import (
    Qwen2VLMultiModalDataParser,
    Qwen2VLProcessingInfo,
    _create_qwen2vl_field_factory,
)
from .qwen3 import Qwen3ForCausalLM, Qwen3Model
from .utils import (
    AutoWeightsLoader,
    PPMissingLayer,
    WeightsMapper,
    _merge_multimodal_embeddings,
    maybe_prefix,
)
from .vision import (
    get_vit_attn_backend,
    is_vit_use_data_parallel,
    run_dp_sharded_mrope_vision_model,
)

logger = init_logger(__name__)


# We use 2048 dummy video frames that would generate vision embeddings
# of the maximum size.
DUMMY_VIDEO_NUM_FRAMES = 2048


class Qwen3_VisionPatchEmbed(nn.Module):
    def __init__(
        self,
        patch_size: int = 14,
        temporal_patch_size: int = 2,
        in_channels: int = 3,
        hidden_size: int = 1152,
    ) -> None:
        super().__init__()
        self.patch_size = patch_size
        self.temporal_patch_size = temporal_patch_size
        self.hidden_size = hidden_size

        kernel_size = (temporal_patch_size, patch_size, patch_size)
        self.proj = Conv3dLayer(
            in_channels,
            hidden_size,
            kernel_size=kernel_size,
            stride=kernel_size,
            bias=True,
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        L, C = x.shape
        x = x.view(L, -1, self.temporal_patch_size, self.patch_size, self.patch_size)
        x = self.proj(x).view(L, self.hidden_size)
        return x


class Qwen3_VisionMLP(nn.Module):
    def __init__(
        self,
        in_features: int,
        hidden_features: int,
        bias: bool = False,
        act_fn: Callable[[torch.Tensor], torch.Tensor] = F.silu,
        quant_config: QuantizationConfig | None = None,
        prefix: str = "",
    ):
        super().__init__()
        use_data_parallel = is_vit_use_data_parallel()
        self.linear_fc1 = ColumnParallelLinear(
            in_features,
            hidden_features,
            bias=bias,
            quant_config=quant_config,
            return_bias=False,
            prefix=f"{prefix}.linear_fc1",
            disable_tp=use_data_parallel,
        )
        self.linear_fc2 = RowParallelLinear(
            hidden_features,
            in_features,
            bias=bias,
            quant_config=quant_config,
            return_bias=False,
            prefix=f"{prefix}.linear_fc2",
            disable_tp=use_data_parallel,
        )
        self.act_fn = act_fn

    def forward(self, x: torch.Tensor):
        mlp_output = self.linear_fc2(self.act_fn(self.linear_fc1(x)))
        return mlp_output


class Qwen3_VisionBlock(nn.Module):
    def __init__(
        self,
        dim: int,
        num_heads: int,
        mlp_hidden_dim: int,
        act_fn: Callable[[torch.Tensor], torch.Tensor] = F.silu,
        norm_layer: Callable[[int], nn.Module] | None = None,
        quant_config: QuantizationConfig | None = None,
        prefix: str = "",
    ) -> None:
        super().__init__()
        if norm_layer is None:
            norm_layer = partial(nn.LayerNorm, eps=1e-6)
        self.norm1 = norm_layer(dim)
        self.norm2 = norm_layer(dim)
        self.attn = Qwen2_5_VisionAttention(
            embed_dim=dim,
            num_heads=num_heads,
            projection_size=dim,
            quant_config=quant_config,
            prefix=f"{prefix}.attn",
        )
        self.mlp = Qwen3_VisionMLP(
            dim,
            mlp_hidden_dim,
            act_fn=act_fn,
            bias=True,
            quant_config=quant_config,
            prefix=f"{prefix}.mlp",
        )

    def forward(
        self,
        x: torch.Tensor,
        cu_seqlens: torch.Tensor,
        rotary_pos_emb_cos: torch.Tensor,
        rotary_pos_emb_sin: torch.Tensor,
        max_seqlen: torch.Tensor,  # Only used for Flash Attention
        sequence_lengths: torch.Tensor,  # Only used for FlashInfer CuDNN backend
    ) -> torch.Tensor:
        x = x + self.attn(
            self.norm1(x),
            cu_seqlens=cu_seqlens,
            rotary_pos_emb_cos=rotary_pos_emb_cos,
            rotary_pos_emb_sin=rotary_pos_emb_sin,
            max_seqlen=max_seqlen,
            sequence_lengths=sequence_lengths,
        )

        x = x + self.mlp(self.norm2(x))
        return x


class Qwen3_VisionPatchMerger(nn.Module):
    def __init__(
        self,
        d_model: int,
        context_dim: int,
        norm_layer: Callable[[int], nn.Module] | None = None,
        spatial_merge_size: int = 2,
        use_postshuffle_norm: bool = False,
        quant_config: QuantizationConfig | None = None,
        prefix: str = "",
    ) -> None:
        super().__init__()
        use_data_parallel = is_vit_use_data_parallel()
        self.hidden_size = context_dim * (spatial_merge_size**2)

        self.use_postshuffle_norm = use_postshuffle_norm
        if self.use_postshuffle_norm:
            context_dim = self.hidden_size

        if norm_layer is None:
            norm_layer = partial(nn.LayerNorm, eps=1e-6)
        self.norm = norm_layer(context_dim)
        self.linear_fc1 = ColumnParallelLinear(
            self.hidden_size,
            self.hidden_size,
            bias=True,
            quant_config=quant_config,
            prefix=f"{prefix}.linear_fc1",
            disable_tp=use_data_parallel,
        )
        self.act_fn = nn.GELU()
        self.linear_fc2 = RowParallelLinear(
            self.hidden_size,
            d_model,
            bias=True,
            quant_config=quant_config,
            prefix=f"{prefix}.linear_fc2",
            disable_tp=use_data_parallel,
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.use_postshuffle_norm:
            x = self.norm(x.view(-1, self.hidden_size))
        else:
            x = self.norm(x).view(-1, self.hidden_size)

        x_parallel, _ = self.linear_fc1(x)
        x_parallel = self.act_fn(x_parallel)
        out, _ = self.linear_fc2(x_parallel)
        return out


class Qwen3_VisionTransformer(nn.Module):
    def __init__(
        self,
        vision_config: Qwen3VLVisionConfig,
        norm_eps: float = 1e-6,
        quant_config: QuantizationConfig | None = None,
        prefix: str = "",
    ) -> None:
        super().__init__()
        self.hidden_size = vision_config.hidden_size
        self.num_heads = vision_config.num_heads
        self.num_position_embeddings = vision_config.num_position_embeddings
        self.patch_size = vision_config.patch_size
        self.spatial_merge_size = vision_config.spatial_merge_size
        self.spatial_merge_unit = self.spatial_merge_size**2
        self.temporal_patch_size = vision_config.temporal_patch_size
        self.deepstack_visual_indexes = (
            vision_config.deepstack_visual_indexes
            if hasattr(vision_config, "deepstack_visual_indexes")
            else []
        )
        self.num_grid_per_side = int(self.num_position_embeddings**0.5)

        use_data_parallel = is_vit_use_data_parallel()
        self.tp_size = (
            1
            if use_data_parallel
            else parallel_state.get_tensor_model_parallel_world_size()
        )

        # NOTE: This is used for creating empty tensor for all_gather for
        # DP ViT. Here out_hidden_size is enlarged due to deepstack
        self.out_hidden_size = vision_config.out_hidden_size * (
            1 + len(self.deepstack_visual_indexes)
        )

        self.patch_embed = Qwen3_VisionPatchEmbed(
            patch_size=self.patch_size,
            temporal_patch_size=self.temporal_patch_size,
            in_channels=vision_config.in_channels,
            hidden_size=self.hidden_size,
        )

        self.pos_embed = nn.Embedding(self.num_position_embeddings, self.hidden_size)

        norm_layer = partial(nn.LayerNorm, eps=norm_eps)
        head_dim = self.hidden_size // self.num_heads
        self.rotary_pos_emb = get_rope(
            head_size=head_dim,
            max_position=8192,
            is_neox_style=True,
            rope_parameters={"partial_rotary_factor": 0.5},
        )

        self.merger = Qwen3_VisionPatchMerger(
            d_model=vision_config.out_hidden_size,
            context_dim=self.hidden_size,
            norm_layer=norm_layer,
            spatial_merge_size=self.spatial_merge_size,
            quant_config=quant_config,
            prefix=f"{prefix}.merger",
        )

        self.deepstack_merger_list = nn.ModuleList(
            [
                Qwen3_VisionPatchMerger(
                    d_model=vision_config.out_hidden_size,
                    context_dim=self.hidden_size,
                    spatial_merge_size=self.spatial_merge_size,
                    use_postshuffle_norm=True,
                    norm_layer=norm_layer,
                    quant_config=quant_config,
                    prefix=f"{prefix}.deepstack_merger_list.{layer_idx}",
                )
                for layer_idx in range(len(self.deepstack_visual_indexes))
            ]
        )

        self.attn_backend = get_vit_attn_backend(
            head_size=head_dim,
            dtype=torch.get_default_dtype(),
        )

        self.blocks = nn.ModuleList(
            [
                Qwen3_VisionBlock(
                    dim=self.hidden_size,
                    num_heads=self.num_heads,
                    mlp_hidden_dim=vision_config.intermediate_size,
                    act_fn=_ACTIVATION_REGISTRY[vision_config.hidden_act],
                    norm_layer=norm_layer,
                    quant_config=quant_config,
                    prefix=f"{prefix}.blocks.{layer_idx}",
                )
                for layer_idx in range(vision_config.depth)
            ]
        )

    @property
    def dtype(self) -> torch.dtype:
        return self.patch_embed.proj.weight.dtype

    @property
    def device(self) -> torch.device:
        return self.patch_embed.proj.weight.device

    @staticmethod
    @lru_cache(maxsize=1024)
    def rot_pos_ids(h: int, w: int, spatial_merge_size: int) -> torch.Tensor:
        hpos_ids = np.broadcast_to(np.arange(h).reshape(h, 1), (h, w))
        h_div = h // spatial_merge_size
        w_div = w // spatial_merge_size
        hpos_ids = hpos_ids.reshape(
            h_div,
            spatial_merge_size,
            w_div,
            spatial_merge_size,
        )
        hpos_ids = hpos_ids.transpose(0, 2, 1, 3)
        hpos_ids = hpos_ids.flatten()

        wpos_ids = np.broadcast_to(np.arange(w).reshape(1, w), (h, w))
        wpos_ids = wpos_ids.reshape(
            h_div,
            spatial_merge_size,
            w_div,
            spatial_merge_size,
        )
        wpos_ids = wpos_ids.transpose(0, 2, 1, 3)
        wpos_ids = wpos_ids.flatten()

        return torch.from_numpy(np.stack([hpos_ids, wpos_ids], axis=-1))

    def rot_pos_emb(self, grid_thw: list[list[int]]):
        max_grid_size = max(max(h, w) for _, h, w in grid_thw)
        pos_ids = [
            self.rot_pos_ids(h, w, self.spatial_merge_size)
            if t == 1
            else self.rot_pos_ids(h, w, self.spatial_merge_size).repeat(t, 1)
            for t, h, w in grid_thw
        ]
        pos_ids = torch.cat(pos_ids, dim=0).to(self.device, non_blocking=True)

        # Use pre-computed cos_sin_cache from RotaryEmbedding
        cos, sin = self.rotary_pos_emb.get_cos_sin(max_grid_size)

        cos_combined = cos[pos_ids].flatten(1)
        sin_combined = sin[pos_ids].flatten(1)

        return cos_combined, sin_combined

    def fast_pos_embed_interpolate(self, grid_thw: list[list[int]]) -> torch.Tensor:
        num_grid_per_side = self.num_grid_per_side
        m_size = self.spatial_merge_size
        hidden_dim = self.pos_embed.embedding_dim

        outputs = []
        for t, h, w in grid_thw:
            h_idxs = torch.linspace(
                0, num_grid_per_side - 1, h, dtype=torch.float32, device=self.device
            )
            w_idxs = torch.linspace(
                0, num_grid_per_side - 1, w, dtype=torch.float32, device=self.device
            )

            h_floor = h_idxs.to(torch.long)
            w_floor = w_idxs.to(torch.long)
            h_ceil = torch.clamp(h_floor + 1, max=num_grid_per_side - 1)
            w_ceil = torch.clamp(w_floor + 1, max=num_grid_per_side - 1)

            dh = h_idxs - h_floor
            dw = w_idxs - w_floor

            # Create meshgrid view for all h, w vars
            dh_grid, dw_grid = torch.meshgrid(dh, dw, indexing="ij")
            h_floor_grid, w_floor_grid = torch.meshgrid(h_floor, w_floor, indexing="ij")
            h_ceil_grid, w_ceil_grid = torch.meshgrid(h_ceil, w_ceil, indexing="ij")

            # original computation of weights
            # w00 = (1 - dh_grid) * (1 - dw_grid)
            # w01 = (1 - dh_grid) * dw_grid
            # w10 = dh_grid * (1 - dw_grid)
            # w11 = dh_grid * dw_grid
            # we reuse w11 here to avoid duplicate
            # dh_grid * dw_grid computation
            w11 = dh_grid * dw_grid
            w10 = dh_grid - w11
            w01 = dw_grid - w11
            w00 = 1 - dh_grid - w01

            h_grid = torch.stack([h_floor_grid, h_floor_grid, h_ceil_grid, h_ceil_grid])
            w_grid = torch.stack([w_floor_grid, w_ceil_grid, w_floor_grid, w_ceil_grid])
            h_grid_idx = h_grid * num_grid_per_side

            indices = (h_grid_idx + w_grid).reshape(4, -1)
            weights = torch.stack([w00, w01, w10, w11], dim=0).reshape(4, -1, 1)
            weights = weights.to(dtype=self.dtype)

            embeds = self.pos_embed(indices)
            embeds *= weights
            combined = embeds.sum(dim=0)

            combined = combined.reshape(
                h // m_size, m_size, w // m_size, m_size, hidden_dim
            )
            combined = combined.permute(0, 2, 1, 3, 4).reshape(1, -1, hidden_dim)
            repeated = combined.expand(t, -1, -1).reshape(-1, hidden_dim)
            outputs.append(repeated)

        return torch.cat(outputs, dim=0)

    def forward(
        self,
        x: torch.Tensor,
        grid_thw: torch.Tensor | list[list[int]],
    ) -> torch.Tensor:
        hidden_states = x.to(device=self.device, dtype=self.dtype, non_blocking=True)
        hidden_states = self.patch_embed(hidden_states)

        if isinstance(grid_thw, list):
            grid_thw_list = grid_thw
            grid_thw = np.array(grid_thw, dtype=np.int32)
        else:
            grid_thw_list = grid_thw.tolist()
            grid_thw = grid_thw.numpy()

        pos_embeds = self.fast_pos_embed_interpolate(grid_thw_list)
        hidden_states = hidden_states + pos_embeds
        rotary_pos_emb_cos, rotary_pos_emb_sin = self.rot_pos_emb(grid_thw_list)

        cu_seqlens = np.repeat(grid_thw[:, 1] * grid_thw[:, 2], grid_thw[:, 0]).cumsum(
            axis=0, dtype=np.int32
        )
        cu_seqlens = np.concatenate([np.zeros(1, dtype=np.int32), cu_seqlens])
        sequence_lengths = MMEncoderAttention.maybe_compute_sequence_lengths(
            self.attn_backend, cu_seqlens
        )
        if sequence_lengths is not None:
            sequence_lengths = torch.from_numpy(sequence_lengths).to(
                self.device, non_blocking=True
            )
        max_seqlen = torch.tensor(
            MMEncoderAttention.compute_max_seqlen(self.attn_backend, cu_seqlens),
            dtype=torch.int32,
            device=self.device,
        )
        cu_seqlens = MMEncoderAttention.maybe_recompute_cu_seqlens(
            self.attn_backend,
            cu_seqlens,
            self.hidden_size,
            self.tp_size,
        )
        cu_seqlens = torch.from_numpy(cu_seqlens).to(self.device, non_blocking=True)
        hidden_states = hidden_states.unsqueeze(1)

        deepstack_feature_lists = []
        for layer_num, blk in enumerate(self.blocks):
            hidden_states = blk(
                hidden_states,
                cu_seqlens=cu_seqlens,
                rotary_pos_emb_cos=rotary_pos_emb_cos,
                rotary_pos_emb_sin=rotary_pos_emb_sin,
                max_seqlen=max_seqlen,
                sequence_lengths=sequence_lengths,
            )
            if layer_num in self.deepstack_visual_indexes:
                deepstack_merger_idx = self.deepstack_visual_indexes.index(layer_num)
                deepstack_feature = self.deepstack_merger_list[deepstack_merger_idx](
                    hidden_states
                )
                deepstack_feature_lists.append(deepstack_feature)
        hidden_states = self.merger(hidden_states)
        hidden_states = torch.cat(
            [hidden_states] + deepstack_feature_lists, dim=1
        )  # [seq_len, hidden_size * (1 + depth_of_deepstack)]
        return hidden_states

    def load_weights(self, weights: Iterable[tuple[str, torch.Tensor]]) -> set[str]:
        stacked_params_mapping = [
            # (param_name, shard_name, shard_id)
            ("attn.qkv.", "attn.q.", "q"),
            ("attn.qkv.", "attn.k.", "k"),
            ("attn.qkv.", "attn.v.", "v"),
        ]
        params_dict = dict(self.named_parameters(remove_duplicate=False))
        loaded_params: set[str] = set()

        for name, loaded_weight in weights:
            for param_name, weight_name, shard_id in stacked_params_mapping:
                if weight_name not in name:
                    continue
                name = name.replace(weight_name, param_name)

                param = params_dict[name]
                weight_loader = param.weight_loader
                weight_loader(param, loaded_weight, shard_id)
                break
            else:
                param = params_dict[name]
                weight_loader = getattr(param, "weight_loader", default_weight_loader)
                weight_loader(param, loaded_weight)
            loaded_params.add(name)
        return loaded_params


class Qwen3VLProcessingInfo(Qwen2VLProcessingInfo):
    def get_hf_config(self):
        return self.ctx.get_hf_config(Qwen3VLConfig)

    def get_hf_processor(self, **kwargs: object) -> Qwen3VLProcessor:
        return self.ctx.get_hf_processor(
            Qwen3VLProcessor,
            use_fast=kwargs.pop("use_fast", True),
            **kwargs,
        )

    def get_image_processor(self, **kwargs: object) -> Qwen2VLImageProcessorFast:
        return self.get_hf_processor(**kwargs).image_processor

    def get_video_processor(self, **kwargs: object) -> Qwen3VLVideoProcessor:
        return self.get_hf_processor(**kwargs).video_processor

    def get_data_parser(self):
        return Qwen2VLMultiModalDataParser(
            self.get_hf_config().vision_config.spatial_merge_size,
            video_needs_metadata=True,
            expected_hidden_size=self._get_expected_hidden_size(),
        )

    def _get_vision_info(
        self,
        *,
        image_width: int,
        image_height: int,
        num_frames: int = 2,
        do_resize: bool = True,
        image_processor: Qwen2VLImageProcessorFast | Qwen3VLVideoProcessor,
        mm_kwargs: Mapping[str, object],
    ) -> tuple[ImageSize, int]:
        is_video = isinstance(image_processor, Qwen3VLVideoProcessor)

        hf_config = self.get_hf_config()
        vision_config = hf_config.vision_config
        patch_size = vision_config.patch_size
        merge_size = vision_config.spatial_merge_size
        temporal_patch_size = vision_config.temporal_patch_size

        mm_kwargs = self.ctx.get_merged_mm_kwargs(mm_kwargs)
        size = image_processor.size
        if override_size := mm_kwargs.get("size"):
            size = size | override_size
        if (override_min_pixels := mm_kwargs.get("min_pixels")) is not None:
            size = size | {"shortest_edge": override_min_pixels}
        if (override_max_pixels := mm_kwargs.get("max_pixels")) is not None:
            size = size | {"longest_edge": override_max_pixels}

        if do_resize:
            if is_video:
                smart_resize = video_smart_resize
                extra_kwargs = {
                    "num_frames": num_frames,
                    "temporal_factor": temporal_patch_size,
                }
            else:
                smart_resize = image_smart_resize
                extra_kwargs = {}

            resized_height, resized_width = smart_resize(
                height=image_height,
                width=image_width,
                factor=patch_size * merge_size,
                min_pixels=size["shortest_edge"],
                max_pixels=size["longest_edge"],
                **extra_kwargs,
            )
            preprocessed_size = ImageSize(width=resized_width, height=resized_height)
        else:
            preprocessed_size = ImageSize(width=image_width, height=image_height)

        padded_num_frames = round_up(num_frames, temporal_patch_size)

        grid_t = max(padded_num_frames // temporal_patch_size, 1)
        grid_h = preprocessed_size.height // patch_size
        grid_w = preprocessed_size.width // patch_size

        num_patches = grid_t * grid_h * grid_w
        num_vision_tokens = num_patches // (merge_size**2)

        return preprocessed_size, num_vision_tokens

    def _get_max_video_frames(self, max_tokens: int, start_num_frames: int = 2) -> int:
        return super()._get_max_video_frames(
            max_tokens, start_num_frames=start_num_frames
        )

    def get_num_frames_with_most_features(
        self,
        seq_len: int,
        mm_counts: Mapping[str, int],
    ) -> int:
        return super().get_num_frames_with_most_features(
            seq_len, mm_counts, max_frames_per_video=DUMMY_VIDEO_NUM_FRAMES
        )

    def get_max_video_tokens(
        self,
        seq_len: int,
        mm_counts: Mapping[str, int],
    ) -> int:
        video_processor = self.get_video_processor()

        mm_kwargs = self.ctx.get_merged_mm_kwargs({})
        video_size = mm_kwargs.get("size", video_processor.size)
        temporal_patch_size = mm_kwargs.get(
            "temporal_patch_size", video_processor.temporal_patch_size
        )

        # video_max_pixels contains the temporal compression factor,
        # so we divide by 2 to get the maximum number of image pixels.
        video_max_pixels = video_size["longest_edge"]
        target_width, target_height = self.get_image_size_with_most_features(
            max_pixels=video_max_pixels // temporal_patch_size
        )
        num_video_soft_tokens = self.get_num_video_tokens(
            image_width=target_width,
            image_height=target_height,
            num_frames=2,
            image_processor=video_processor,
            mm_kwargs={},
        )
        return num_video_soft_tokens

    def _calculate_timestamps(
        self, indices: list[int] | torch.Tensor, video_fps: float, merge_size: int
    ):
        if not isinstance(indices, list):
            indices = indices.tolist()
        if len(indices) % merge_size != 0:
            # don't update metadata's frames_indices directly
            indices = indices + [indices[-1]] * (merge_size - len(indices) % merge_size)
        timestamps = [idx / video_fps for idx in indices]
        timestamps = [
            (timestamps[i] + timestamps[i + merge_size - 1]) / 2
            for i in range(0, len(timestamps), merge_size)
        ]
        return timestamps

    def _get_video_second_idx(
        self,
        metadata: dict[str, Any],
        do_sample_frames: bool | None = None,
        sampled_fps: float | None = None,
    ) -> list[int]:
        video_processor = self.get_video_processor()
        merge_size = video_processor.merge_size
        indices = metadata["frames_indices"]
        if indices is None:
            # VideoMetadata leaves this optional, so a caller may omit it. The
            # do_sample_frames branch below would recompute the indices, but
            # EVS pins that off (its frames come pre-paired with timestamps),
            # leaving nothing to fall back on. Assume plain frame order rather
            # than failing the clip in _calculate_timestamps.
            indices = list(range(metadata["total_num_frames"]))

        # metadata["fps"] refers to the true fps of the input video.
        video_fps = metadata["fps"]
        if do_sample_frames is None:
            do_sample_frames = metadata.get("do_sample_frames", False)

        # If video frames are sampled in HF processor (instead of vLLM
        # video loader), we need to re-calculate the indices from original
        # metadata.
        if do_sample_frames:
            # here video_fps is the fps of the sampled video, and
            # metadata["fps"] refers to the fps of the original video.
            sampled_fps = sampled_fps if sampled_fps else video_processor.fps
            total_num_frames = metadata["total_num_frames"]
            num_frames = int(total_num_frames / metadata["fps"] * sampled_fps)
            num_frames = min(
                min(
                    max(num_frames, video_processor.min_frames),
                    video_processor.max_frames,
                ),
                total_num_frames,
            )
            indices = (
                np.linspace(0, total_num_frames - 1, num_frames)
                .round()
                .astype(int)
                .tolist()
            )
        timestamps = self._calculate_timestamps(indices, video_fps, merge_size)
        return timestamps


class Qwen3VLDummyInputsBuilder(BaseDummyInputsBuilder[Qwen3VLProcessingInfo]):
    def get_dummy_text(self, mm_counts: Mapping[str, int]) -> str:
        num_images = mm_counts.get("image", 0)
        num_videos = mm_counts.get("video", 0)

        image_token = "<|vision_start|><|image_pad|><|vision_end|>"
        video_token = "<|vision_start|><|video_pad|><|vision_end|>"

        return image_token * num_images + video_token * num_videos

    def get_dummy_mm_data(
        self,
        seq_len: int,
        mm_counts: Mapping[str, int],
        mm_options: Mapping[str, BaseDummyOptions],
    ) -> MultiModalDataDict:
        num_images = mm_counts.get("image", 0)
        num_videos = mm_counts.get("video", 0)
        image_overrides = mm_options.get("image")
        video_overrides = mm_options.get("video")

        target_image_width, target_image_height = (
            self.info.get_image_size_with_most_features()
        )

        # treat videos as special images
        target_num_frames = 2
        if video_overrides:
            assert isinstance(video_overrides, VideoDummyOptions)
            num_frames_override = video_overrides.num_frames
            if num_frames_override:
                if num_frames_override > target_num_frames:
                    logger.warning(
                        "video.num_frames override (%d) exceeds model's "
                        "maximum number of frames (%d), will be ignored",
                        num_frames_override,
                        target_num_frames,
                    )
                if num_frames_override < 2:
                    logger.warning(
                        "video.num_frames override (%d) cannot be less "
                        "than 2, will be ignored",
                        num_frames_override,
                    )
                target_num_frames = min(target_num_frames, num_frames_override)
        target_num_frames = max(target_num_frames, 2)

        video_processor = self.info.get_video_processor()

        mm_kwargs = self.info.ctx.get_merged_mm_kwargs({})
        video_size = mm_kwargs.get("size", video_processor.size)
        temporal_patch_size = mm_kwargs.get(
            "temporal_patch_size", video_processor.temporal_patch_size
        )

        # video_max_pixels contains the temporal compression factor,
        # so we divide by 2 to get the maximum number of image pixels.
        video_max_pixels = video_size["longest_edge"]
        target_video_width, target_video_height = (
            self.info.get_image_size_with_most_features(
                max_pixels=video_max_pixels // temporal_patch_size
            )
        )
        target_video_size, _ = self.info._get_vision_info(
            image_width=target_video_width,
            image_height=target_video_height,
            num_frames=target_num_frames,
            image_processor=video_processor,
            mm_kwargs={},
        )
        # NOTE: we need to do this check here since Qwen3-VL resizes video
        # frames depending on how many frames there are.
        target_video_width, target_video_height = (
            target_video_size.width,
            target_video_size.height,
        )
        if video_overrides:
            assert isinstance(video_overrides, VideoDummyOptions)
            width_override = video_overrides.width
            if width_override:
                if width_override > target_video_width:
                    logger.warning(
                        "video.width override (%d) exceeds model's "
                        "maximum width (%d), will be ignored",
                        width_override,
                        target_video_width,
                    )
                target_video_width = min(target_video_width, width_override)
            height_override = video_overrides.height
            if height_override:
                if height_override > target_video_height:
                    logger.warning(
                        "video.height override (%d) exceeds model's "
                        "maximum height (%d), will be ignored",
                        height_override,
                        target_video_height,
                    )
                target_video_height = min(target_video_height, height_override)

        return {
            "image": self._get_dummy_images(
                width=target_image_width,
                height=target_image_height,
                num_images=num_images,
                overrides=image_overrides,
            ),
            "video": self._get_dummy_videos(
                width=target_video_width,
                height=target_video_height,
                num_frames=target_num_frames,
                num_videos=num_videos,
            ),
        }

    def _get_dummy_videos(
        self,
        *,
        width: int,
        height: int,
        num_frames: int,
        num_videos: int,
    ) -> list[VideoItem]:
        video = np.full((num_frames, width, height, 3), 255, dtype=np.uint8)
        video_items = []
        for i in range(num_videos):
            video_metadata = {
                "fps": 2.0,
                "duration": num_frames / 2.0,
                "total_num_frames": num_frames,
                "frames_indices": [i for i in range(num_frames)],
                "video_backend": "opencv",
                "do_sample_frames": False,
            }
            video_item = (video.copy(), video_metadata)
            video_items.append(video_item)
        return video_items


class Qwen3VLMultiModalProcessor(BaseMultiModalProcessor[Qwen3VLProcessingInfo]):
    def _call_hf_processor(
        self,
        prompt: str,
        mm_data: Mapping[str, object],
        mm_kwargs: Mapping[str, object],
        tok_kwargs: Mapping[str, object],
    ) -> BatchFeature:
        mm_data = dict(mm_data)
        # Extract evs_stream_id before passing to HF processor
        # (which rejects unknown kwargs).
        mm_kwargs = dict(mm_kwargs)
        evs_stream_id = mm_kwargs.pop("evs_stream_id", None)
        mm_kwargs.pop("evs_actual_total_tokens", None)
        num_tokens_per_frame = mm_kwargs.pop("num_tokens_per_frame", None)
        evs_timestamps = mm_kwargs.pop("evs_timestamps", None)
        processor = self.info.get_hf_processor(**mm_kwargs)

        # Separate video processing from image processing. Because the videos
        # are processed into several image patches
        if videos := mm_data.pop("videos", []):
            video_grid_thw_lst = []
            pixel_values_videos_lst = []
            timestamps_per_video = []

            for item in videos:
                video_array, metadata = item

                # NOTE: @JJJYmmm new attr metadata.frames_indices indicates
                # the sampled frames indices of pre-sampled videos, which is
                # used to calculate the timestamps. Make sure that
                # do_sample_frames in mm_kwargs is false for presampled videos.

                # NOTE: a copy of is created to update do_sample_frames,
                # otherwise mm_hash for the object will be incorrect.
                video_mm_kwargs = dict(**mm_kwargs)
                if "do_sample_frames" not in video_mm_kwargs:
                    # qwen_vl_utils already has "do_sample_frames" in
                    # mm_kwargs, don't overwrite it.
                    video_mm_kwargs["do_sample_frames"] = metadata.get(
                        "do_sample_frames", False
                    )

                metadata = VideoMetadata(
                    **{k: metadata[k] for k in metadata if k != "do_sample_frames"}
                )

                # Compute timestamps here where we have access to metadata
                timestamps = self.info._get_video_second_idx(
                    metadata=metadata,
                    do_sample_frames=video_mm_kwargs["do_sample_frames"],
                    sampled_fps=video_mm_kwargs.get("fps"),
                )
                timestamps_per_video.append(timestamps)

                video_mm_data = dict()
                video_mm_data["videos"] = [[video_array]]
                video_mm_data["video_metadata"] = [[metadata]]

                video_outputs = super()._call_hf_processor(
                    prompt="<|vision_start|><|video_pad|><|vision_end|>",
                    mm_data=video_mm_data,
                    mm_kwargs=video_mm_kwargs,
                    tok_kwargs=tok_kwargs,
                )

                merge_size = processor.video_processor.merge_size
                # Get video grid info for EVS calculation.
                video_grid_thw = video_outputs["video_grid_thw"]
                num_frames = int(video_grid_thw[0, 0])
                tokens_per_frame_base = int(video_grid_thw[0, 1:].prod()) // (
                    merge_size**2
                )

                # Apply EVS if enabled.
                video_pruning_rate = self.info.ctx.get_mm_config().video_pruning_rate
                if video_pruning_rate is not None and video_pruning_rate > 0.0:
                    from vllm.multimodal.evs_qwen3_ops import (
                        compute_retained_tokens_count,
                    )

                    num_tokens = compute_retained_tokens_count(
                        tokens_per_frame=tokens_per_frame_base,
                        num_frames=num_frames,
                        q=video_pruning_rate,
                    )
                    # Here we just need placeholders that won't actually be replaced -
                    # we just need to make sure the total number of tokens is correct
                    # assign all tokens to the first frame.
                    tokens_per_frame = [num_tokens] + [0] * (num_frames - 1)
                    select_token_id = False
                else:
                    tokens_per_frame = [tokens_per_frame_base] * num_frames
                    select_token_id = True

                # Generate the video replacement with EVS-adjusted token counts
                tokenizer = self.info.get_tokenizer()
                hf_config = self.info.get_hf_config()
                video_repl = Qwen3VLMultiModalProcessor.get_video_repl(
                    tokens_per_frame=tokens_per_frame,
                    timestamps=timestamps,
                    tokenizer=tokenizer,
                    vision_start_token_id=hf_config.vision_start_token_id,
                    vision_end_token_id=hf_config.vision_end_token_id,
                    video_token_id=hf_config.video_token_id,
                    select_token_id=select_token_id,
                )

                # Convert token IDs to text for the HF processor flow.
                # NOTE: use the EVS-adjusted (lumped, when pruning) layout from
                # ``video_repl`` here. Do NOT overwrite it with the per-clip HF
                # processor's full uniform expansion — the prompt handed to vLLM
                # must match the layout produced by ``_get_prompt_updates`` or
                # ``_validate_mm_placeholders`` finds 0 placeholders (the per-clip
                # ``input_ids`` is discarded when ``video_outputs`` is rebuilt
                # below, so there is nothing to pop).
                video_placeholder = tokenizer.decode(
                    video_repl.full, skip_special_tokens=False
                )
                prompt = prompt.replace(
                    "<|vision_start|><|video_pad|><|vision_end|>",
                    video_placeholder,
                    1,
                )

                video_grid_thw_lst.append(video_outputs["video_grid_thw"])
                pixel_values_videos_lst.append(video_outputs["pixel_values_videos"])
            num_videos = len(video_grid_thw_lst)
            video_outputs = dict(
                pixel_values_videos=torch.cat(pixel_values_videos_lst),
                video_grid_thw=torch.cat(video_grid_thw_lst),
                timestamps=timestamps_per_video,
            )
            if evs_stream_id is not None:
                video_outputs["evs_stream_id"] = [evs_stream_id] * num_videos
            if num_tokens_per_frame is not None:
                # num_tokens_per_frame is a list of per-video lists,
                # e.g. [[943,412,297,...], [943,410,280,...], ...].
                # Each inner list = per-frame token counts for one video.
                if (
                    isinstance(num_tokens_per_frame, list)
                    and num_tokens_per_frame
                    and isinstance(num_tokens_per_frame[0], list)
                ):
                    video_outputs["num_tokens_per_frame"] = num_tokens_per_frame
                else:
                    # Single flat list — wrap for one video.
                    video_outputs["num_tokens_per_frame"] = [num_tokens_per_frame]
            if evs_timestamps is not None:
                if (
                    isinstance(evs_timestamps, list)
                    and evs_timestamps
                    and isinstance(evs_timestamps[0], list)
                ):
                    video_outputs["evs_timestamps"] = evs_timestamps
                else:
                    video_outputs["evs_timestamps"] = [evs_timestamps]
        else:
            video_outputs = dict()

        processed_outputs = super()._call_hf_processor(
            prompt=prompt,
            mm_data=mm_data,
            mm_kwargs=mm_kwargs,
            tok_kwargs=tok_kwargs,
        )
        combined_outputs = dict(
            processed_outputs,
            **video_outputs,
        )
        return BatchFeature(combined_outputs)

    def _get_mm_fields_config(
        self,
        hf_inputs: BatchFeature,
        hf_processor_mm_kwargs: Mapping[str, object],
    ) -> Mapping[str, MultiModalFieldConfig]:
        base = _create_qwen2vl_field_factory(
            self.info.get_hf_config().vision_config.spatial_merge_size
        )(hf_inputs)
        extra: dict[str, MultiModalFieldConfig] = {}

        # For precomputed EVS video_embeds, use actual tensor size
        # (pruned) instead of the grid-based unpruned estimate.
        video_embeds = hf_inputs.get("video_embeds")
        if video_embeds is not None and isinstance(video_embeds, torch.Tensor):
            actual_size = torch.tensor([video_embeds.shape[0]], dtype=torch.long)
            extra["video_embeds"] = MultiModalFieldConfig.flat_from_sizes(
                "video", actual_size
            )
            if "token_positions" in hf_inputs:
                extra["token_positions"] = MultiModalFieldConfig.flat_from_sizes(
                    "video", actual_size
                )

        if "evs_stream_id" in hf_inputs:
            extra["evs_stream_id"] = MultiModalFieldConfig.batched("video")
        if "num_tokens_per_frame" in hf_inputs:
            extra["num_tokens_per_frame"] = MultiModalFieldConfig.batched(
                "video", keep_on_cpu=True
            )
        if "evs_timestamps" in hf_inputs:
            extra["evs_timestamps"] = MultiModalFieldConfig.batched(
                "video", keep_on_cpu=True
            )
        return {**base, **extra}

    def _get_prompt_updates(
        self,
        mm_items: MultiModalDataItems,
        hf_processor_mm_kwargs: Mapping[str, Any],
        out_mm_kwargs: MultiModalKwargsItems,
    ) -> Sequence[PromptUpdate]:
        # Strip EVS keys before passing to get_hf_processor (which
        # caches by kwargs hash — lists are unhashable).
        clean_kwargs = {
            k: v
            for k, v in hf_processor_mm_kwargs.items()
            if k
            not in (
                "evs_stream_id",
                "evs_actual_total_tokens",
                "num_tokens_per_frame",
                "evs_timestamps",
            )
        }
        hf_processor = self.info.get_hf_processor(**clean_kwargs)
        image_processor = self.info.get_image_processor(**clean_kwargs)
        tokenizer = self.info.get_tokenizer()
        hf_config = self.info.get_hf_config()

        video_token_id = hf_config.video_token_id
        vision_start_token_id = hf_config.vision_start_token_id
        vision_end_token_id = hf_config.vision_end_token_id

        merge_length = image_processor.merge_size**2

        def get_image_replacement_qwen3vl(item_idx: int):
            out_item = out_mm_kwargs["image"][item_idx]
            grid_thw = out_item["image_grid_thw"].data
            assert isinstance(grid_thw, torch.Tensor)

            num_tokens = int(grid_thw.prod()) // merge_length
            return [hf_processor.image_token_id] * num_tokens

        def get_video_replacement_qwen3vl(item_idx: int):
            out_item = out_mm_kwargs["video"][item_idx]
            video_pruning_rate = self.info.ctx.get_mm_config().video_pruning_rate
            has_evs_layout = (
                out_item.get("num_tokens_per_frame") is not None
                or out_item.get("evs_timestamps") is not None
                or (video_pruning_rate is not None and video_pruning_rate > 0.0)
            )
            if has_evs_layout:
                from vllm.multimodal.evs_qwen3_ops import (
                    get_video_replacement_qwen3vl as get_evs_video_replacement,
                )

                return get_evs_video_replacement(
                    self,
                    item_idx,
                    out_mm_kwargs,
                    hf_processor_mm_kwargs,
                    tokenizer,
                    hf_config,
                    hf_processor,
                    merge_length,
                )

            grid_thw = out_item["video_grid_thw"].data
            assert isinstance(grid_thw, torch.Tensor)

            timestamps = out_item["timestamps"].data
            assert len(timestamps) == grid_thw[0], (
                f"The timestamps length({len(timestamps)}) should be equal "
                f"video length ({grid_thw[0]})."
            )

            num_frames = int(grid_thw[0])
            tokens_per_frame_base = int(grid_thw[1:].prod()) // merge_length
            return Qwen3VLMultiModalProcessor.get_video_repl(
                tokens_per_frame=[tokens_per_frame_base] * num_frames,
                timestamps=timestamps,
                tokenizer=tokenizer,
                vision_start_token_id=vision_start_token_id,
                vision_end_token_id=vision_end_token_id,
                video_token_id=video_token_id,
                select_token_id=True,
            )

        return [
            PromptReplacement(
                modality="image",
                target=hf_processor.image_token,
                replacement=get_image_replacement_qwen3vl,
            ),
            # NOTE: We match string on purpose since searching sequence of
            # token ids takes more time.
            PromptReplacement(
                modality="video",
                target="<|vision_start|><|video_pad|><|vision_end|>",
                replacement=get_video_replacement_qwen3vl,
            ),
        ]

    def apply(
        self,
        inputs: ProcessorInputs,
        timing_ctx: TimingContext,
    ) -> MultiModalInputs:
        result = super().apply(inputs, timing_ctx)

        pruning_rate = self.info.ctx.get_mm_config().video_pruning_rate
        if not pruning_rate or pruning_rate <= 0.0:
            return result

        video_ranges = result.get("mm_placeholders", {}).get("video")
        if not video_ranges:
            return result

        mm_kwargs = result.get("mm_kwargs", {})
        video_items = mm_kwargs.get("video")
        if not video_items:
            return result

        merge_size = (
            self.info.get_hf_config().vision_config.spatial_merge_size
        )
        merge_area = merge_size ** 2

        # Annotate each video PlaceholderRange with the unpruned encoder
        # input size so the scheduler budgets ViT compute correctly.
        # Skip for precomputed embeddings (video_embeds) — no ViT runs.
        updated = []
        for idx, pr in enumerate(video_ranges):
            if idx < len(video_items) and video_items[idx] is not None:
                item = video_items[idx]
                has_precomputed = item.get("video_embeds") is not None
                grid_thw_field = item.get("video_grid_thw")
                if grid_thw_field is not None and not has_precomputed:
                    grid_thw = grid_thw_field.data
                    unpruned = int(grid_thw.prod()) // merge_area
                    pr = dataclass_replace(
                        pr, encoder_compute_size=unpruned,
                    )
            updated.append(pr)
        result["mm_placeholders"]["video"] = updated
        return result

    @staticmethod
    def get_video_repl(
        *,
        tokens_per_frame: list[int],
        timestamps: list[float | int],
        tokenizer: TokenizerLike,
        vision_start_token_id: int,
        vision_end_token_id: int,
        video_token_id: int,
        select_token_id: bool = False,
    ) -> PromptUpdateDetails[list[int]]:
        """Build prompt replacement for a video in Qwen3VL format.

        The replacement structure for each frame is:
        timestamp_tokens + vision_start_token + video_tokens + vision_end_token

        Args:
            tokens_per_frame: Number of video tokens per frame (can vary per frame for
                EVS).
            timestamps: List of timestamps in seconds for each frame
            tokenizer: Tokenizer to encode timestamp strings
            vision_start_token_id: Token ID for vision start marker
            vision_end_token_id: Token ID for vision end marker
            video_token_id: Token ID for video content

        Returns:
            PromptUpdateDetails with full token sequence
        """
        assert len(timestamps) == len(tokens_per_frame), (
            "timestamps and tokens_per_frame must have the same length"
        )

        # Tokenize timestamp strings independently to avoid tokenizer merging
        # tokens across boundaries.
        # TODO: switch to `_seq2tokens` which has some caching.
        timestamp_token_ids = [
            tokenizer.encode(f"<{timestamp:.1f} seconds>", add_special_tokens=False)
            for timestamp in timestamps
        ]

        # Build the full token sequence
        all_token_ids = []
        for frame_timestamp_ids, num_tokens in zip(
            timestamp_token_ids, tokens_per_frame
        ):
            # Add timestamp tokens
            all_token_ids.extend(frame_timestamp_ids)

            # Add vision tokens: vision_start + video_tokens + vision_end
            all_token_ids.append(vision_start_token_id)
            all_token_ids.extend([video_token_id] * num_tokens)
            all_token_ids.append(vision_end_token_id)

        if select_token_id:
            return PromptUpdateDetails.select_token_id(all_token_ids, video_token_id)

        # NOTE: we use `from_seq` instead of `select_token_id` because we want all
        # tokens in the placeholder to be initially marked as candidates. Then
        # in `get_input_embeddings``, we refine the mask to only replace
        # `video_token_id` / `image_token_id`` positions with video/image embeddings,
        # keeping text embeddings for timestamps and structural tokens.
        return PromptUpdateDetails.from_seq(all_token_ids)


@support_torch_compile(
    dynamic_arg_dims={
        "input_ids": 0,
        # positions is of shape (3, seq_len) if mrope is enabled for qwen2-vl,
        # otherwise (seq_len, ).
        "positions": -1,
        "intermediate_tensors": 0,
        "inputs_embeds": 0,
        # the same shape as input_embeds
        "deepstack_input_embeds": 0,
    }
)
class Qwen3LLMModel(Qwen3Model):
    def forward(
        self,
        input_ids: torch.Tensor | None,
        positions: torch.Tensor,
        intermediate_tensors: IntermediateTensors | None = None,
        inputs_embeds: torch.Tensor | None = None,
        # args for deepstack
        deepstack_input_embeds: IntermediateTensors | None = None,
    ) -> torch.Tensor | IntermediateTensors:
        if get_pp_group().is_first_rank:
            if inputs_embeds is not None:
                hidden_states = inputs_embeds
            else:
                hidden_states = self.embed_input_ids(input_ids)
            residual = None
        else:
            assert intermediate_tensors is not None
            hidden_states = intermediate_tensors["hidden_states"]
            residual = intermediate_tensors["residual"]

        aux_hidden_states = []
        for layer_idx, layer in islice(
            enumerate(self.layers), self.start_layer, self.end_layer
        ):
            if layer_idx in self.aux_hidden_state_layers:
                aux_hidden_states.append(hidden_states + residual)

            hidden_states, residual = layer(
                positions,
                hidden_states,
                residual,
            )

            if deepstack_input_embeds is not None and layer_idx in range(
                0, len(deepstack_input_embeds)
            ):
                hidden_states = (
                    hidden_states
                    + deepstack_input_embeds[f"deepstack_input_embeds_{layer_idx}"]
                )

        if not get_pp_group().is_last_rank:
            return IntermediateTensors(
                {"hidden_states": hidden_states, "residual": residual}
            )
        hidden_states, _ = self.norm(hidden_states, residual)

        if len(aux_hidden_states) > 0:
            return hidden_states, aux_hidden_states
        return hidden_states


class Qwen3LLMForCausalLM(Qwen3ForCausalLM):
    def __init__(self, *, vllm_config: VllmConfig, prefix: str = ""):
        super(Qwen3ForCausalLM, self).__init__()
        config = vllm_config.model_config.hf_config
        quant_config = vllm_config.quant_config

        self.config = config

        self.quant_config = quant_config
        self.model = Qwen3LLMModel(
            vllm_config=vllm_config, prefix=maybe_prefix(prefix, "model")
        )

        if get_pp_group().is_last_rank:
            if config.tie_word_embeddings:
                self.lm_head = self.model.embed_tokens
            else:
                self.lm_head = ParallelLMHead(
                    config.vocab_size,
                    config.hidden_size,
                    quant_config=quant_config,
                    prefix="lm_head",
                )
        else:
            self.lm_head = PPMissingLayer()

        self.logits_processor = LogitsProcessor(config.vocab_size)

        self.make_empty_intermediate_tensors = (
            self.model.make_empty_intermediate_tensors
        )


@MULTIMODAL_REGISTRY.register_processor(
    Qwen3VLMultiModalProcessor,
    info=Qwen3VLProcessingInfo,
    dummy_inputs=Qwen3VLDummyInputsBuilder,
)
class Qwen3VLForConditionalGeneration(
    nn.Module,
    SupportsMultiModal,
    SupportsLoRA,
    SupportsPP,
    SupportsMRoPE,
    SupportsEagle3,
    SupportsMultiModalPruning,
):
    packed_modules_mapping = {
        "qkv_proj": [
            "q_proj",
            "k_proj",
            "v_proj",
        ],
        "gate_up_proj": [
            "gate_proj",
            "up_proj",
        ],
        "qkv": ["qkv"],  # For vision tower's already-packed QKV
    }

    supports_encoder_tp_data = True

    # To ensure correct weight loading and mapping.
    hf_to_vllm_mapper = WeightsMapper(
        orig_to_new_prefix={
            "model.visual.": "visual.",
            "lm_head.": "language_model.lm_head.",
            "model.language_model.": "language_model.model.",
        }
    )

    @classmethod
    def get_placeholder_str(cls, modality: str, i: int) -> str | None:
        if modality.startswith("image"):
            return "<|vision_start|><|image_pad|><|vision_end|>"
        if modality.startswith("video"):
            return "<|vision_start|><|video_pad|><|vision_end|>"

        raise ValueError("Only image or video modality is supported")

    def __init__(self, *, vllm_config: VllmConfig, prefix: str = "model"):
        super().__init__()
        config: Qwen3VLConfig = vllm_config.model_config.hf_config
        quant_config = vllm_config.quant_config
        multimodal_config = vllm_config.model_config.multimodal_config

        self.config = config
        self._tokenizer = cached_tokenizer_from_config(vllm_config.model_config)
        self.multimodal_config = multimodal_config
        self.use_data_parallel = multimodal_config.mm_encoder_tp_mode == "data"
        self.video_pruning_rate = multimodal_config.video_pruning_rate
        self.is_multimodal_pruning_enabled = (
            multimodal_config.is_multimodal_pruning_enabled()
        )
        self.evs_skip_threshold = multimodal_config.evs_skip_threshold
        self.evs_similarity_threshold = multimodal_config.evs_similarity_threshold
        # Per-clip EVS cache is only needed when EVS pruning is enabled.
        self._evs_clip_cache = None
        if self.video_pruning_rate is not None and self.video_pruning_rate > 0.0:
            from vllm.multimodal.evs_qwen3_ops import make_clip_evs_cache

            self._evs_clip_cache = make_clip_evs_cache()

        self.use_deepstack = hasattr(config.vision_config, "deepstack_visual_indexes")
        self.deepstack_num_level = (
            len(config.vision_config.deepstack_visual_indexes)
            if self.use_deepstack
            else 0
        )
        self.visual_dim = config.vision_config.out_hidden_size
        self.multiscale_dim = self.visual_dim * self.deepstack_num_level

        with self._mark_tower_model(vllm_config, {"image", "video"}):
            self.visual = Qwen3_VisionTransformer(
                config.vision_config,
                norm_eps=getattr(config, "rms_norm_eps", 1e-6),
                quant_config=quant_config,
                prefix=maybe_prefix(prefix, "visual"),
            )

            # register buffer for deepstack
            if self.use_deepstack:
                self.deepstack_input_embeds = [
                    torch.zeros(
                        vllm_config.scheduler_config.max_num_batched_tokens,
                        config.text_config.hidden_size,
                    )
                    for _ in range(self.deepstack_num_level)
                ]

        with self._mark_language_model(vllm_config):
            self.language_model = Qwen3LLMForCausalLM(
                vllm_config=vllm_config.with_hf_config(config.text_config),
                prefix=maybe_prefix(prefix, "language_model"),
            )

        if not get_pp_group().is_first_rank and hasattr(
            config.vision_config, "deepstack_visual_indexes"
        ):
            assert self.language_model.start_layer >= len(
                config.vision_config.deepstack_visual_indexes
            ), (
                "start_layer should be greater than or equal to "
                "len(deepstack_visual_indexes)"
            )

        self.make_empty_intermediate_tensors = (
            self.language_model.make_empty_intermediate_tensors
        )

    def set_aux_hidden_state_layers(self, layers: tuple[int, ...]) -> None:
        self.language_model.model.aux_hidden_state_layers = layers

    def get_eagle3_aux_hidden_state_layers(self) -> tuple[int, ...]:
        num_layers = len(self.language_model.model.layers)
        return (2, num_layers // 2, num_layers - 3)

    def _get_deepstack_input_embeds(
        self,
        num_tokens: int,
    ) -> IntermediateTensors | None:
        if not getattr(self, "deepstack_input_embeds", None):
            return None  # If vision tower is skipped

        # get deepstack_input_embeds from buffer, and clear the buffer
        return IntermediateTensors(
            {
                f"deepstack_input_embeds_{idx}": self.deepstack_input_embeds[idx][
                    :num_tokens
                ]
                for idx in range(self.deepstack_num_level)
            }
        )

    def _set_deepstack_input_embeds(self, deepstack_input_embeds: torch.Tensor) -> None:
        if not getattr(self, "deepstack_input_embeds", None):
            return

        # set deepstack_input_embeds to buffer
        num_tokens = deepstack_input_embeds.size(1)
        if num_tokens > self.deepstack_input_embeds[0].size(0):
            self.deepstack_input_embeds = [
                torch.zeros(
                    num_tokens,
                    self.config.text_config.hidden_size,
                    device=self.deepstack_input_embeds[0].device,
                    dtype=self.deepstack_input_embeds[0].dtype,
                )
                for _ in range(self.deepstack_num_level)
            ]
        for idx in range(self.deepstack_num_level):
            self.deepstack_input_embeds[idx][:num_tokens].copy_(
                deepstack_input_embeds[idx]
            )

    def _clear_deepstack_input_embeds(self, num_tokens: int) -> None:
        if not getattr(self, "deepstack_input_embeds", None):
            return

        # clear deepstack_input_embeds in buffer
        if num_tokens > 0:
            for idx in range(self.deepstack_num_level):
                self.deepstack_input_embeds[idx][:num_tokens].zero_()

    def _parse_and_validate_image_input(
        self, **kwargs: object
    ) -> Qwen2_5_VLImageInputs | None:
        pixel_values = kwargs.pop("pixel_values", None)
        image_embeds = kwargs.pop("image_embeds", None)
        image_grid_thw = kwargs.pop("image_grid_thw", None)

        if pixel_values is None and image_embeds is None:
            return None

        if pixel_values is not None:
            return Qwen2_5_VLImagePixelInputs(
                type="pixel_values",
                pixel_values=pixel_values,
                image_grid_thw=image_grid_thw,
            )

        if image_embeds is not None:
            return Qwen2_5_VLImageEmbeddingInputs(
                type="image_embeds",
                image_embeds=image_embeds,
                image_grid_thw=image_grid_thw,
            )

    def _parse_and_validate_video_input(
        self, **kwargs: object
    ) -> Qwen2_5_VLVideoInputs | None:
        pixel_values_videos = kwargs.pop("pixel_values_videos", None)
        video_embeds = kwargs.pop("video_embeds", None)
        video_grid_thw = kwargs.pop("video_grid_thw", None)
        second_per_grid_ts = kwargs.pop("second_per_grid_ts", None)
        timestamps = kwargs.pop("timestamps", None)
        evs_stream_id = kwargs.pop("evs_stream_id", None)
        token_positions = kwargs.pop("token_positions", None)
        num_tokens_per_frame = kwargs.pop("num_tokens_per_frame", None)

        if pixel_values_videos is None and video_embeds is None:
            return None

        if pixel_values_videos is not None:
            return Qwen2_5_VLVideoPixelInputs(
                type="pixel_values_videos",
                pixel_values_videos=pixel_values_videos,
                video_grid_thw=video_grid_thw,
                second_per_grid_ts=second_per_grid_ts,
                timestamps=timestamps,
                evs_stream_id=evs_stream_id,
            )

        if video_embeds is not None:
            return Qwen2_5_VLVideoEmbeddingInputs(
                type="video_embeds",
                video_embeds=video_embeds,
                video_grid_thw=video_grid_thw,
                timestamps=timestamps,
                evs_stream_id=evs_stream_id,
                token_positions=token_positions,
                num_tokens_per_frame=num_tokens_per_frame,
            )

    def _process_image_input(
        self, image_input: Qwen2_5_VLImageInputs
    ) -> tuple[torch.Tensor, ...]:
        grid_thw = image_input["image_grid_thw"]
        assert grid_thw.ndim == 2

        if image_input["type"] == "image_embeds":
            image_embeds = image_input["image_embeds"].type(self.visual.dtype)
        else:
            pixel_values = image_input["pixel_values"].type(self.visual.dtype)
            if self.use_data_parallel:
                return run_dp_sharded_mrope_vision_model(
                    self.visual, pixel_values, grid_thw.tolist(), rope_type="rope_3d"
                )
            else:
                image_embeds = self.visual(pixel_values, grid_thw=grid_thw)

        # Split concatenated embeddings for each image item.
        merge_size = self.visual.spatial_merge_size
        sizes = (grid_thw.prod(-1) // merge_size // merge_size).tolist()
        return image_embeds.split(sizes)

    def _process_video_input(
        self, video_input: Qwen2_5_VLVideoInputs
    ) -> tuple[torch.Tensor, ...]:
        grid_thw = video_input["video_grid_thw"]
        assert grid_thw.ndim == 2

        if video_input["type"] == "video_embeds":
            raw_embeds = video_input["video_embeds"]
            video_embeds = raw_embeds.type(self.visual.dtype)
            num_videos = grid_thw.shape[0]
            if num_videos == 1:
                return (video_embeds,)
            num_tpf = getattr(video_input, "num_tokens_per_frame", None)
            if num_tpf is not None:
                # Batching may produce nested lists, a list of tensors,
                # or a stacked 2-D tensor. Normalize to a flat list.
                if isinstance(num_tpf, torch.Tensor):
                    num_tpf = num_tpf.flatten().tolist()
                elif num_tpf and isinstance(num_tpf[0], torch.Tensor):
                    num_tpf = [
                        n for t in num_tpf for n in t.flatten().tolist()
                    ]
                elif num_tpf and isinstance(num_tpf[0], (list, tuple)):
                    num_tpf = [n for sublist in num_tpf for n in sublist]
                sizes = []
                offset = 0
                for i in range(num_videos):
                    t = int(grid_thw[i][0])
                    sizes.append(sum(num_tpf[offset:offset + t]))
                    offset += t
            else:
                per_video = video_embeds.shape[0] // num_videos
                sizes = [per_video] * num_videos
                sizes[-1] = video_embeds.shape[0] - per_video * (num_videos - 1)
            return video_embeds.split(sizes)

        pixel_values_videos = video_input["pixel_values_videos"].type(self.visual.dtype)
        if self.use_data_parallel:
            grid_thw_list = grid_thw.tolist()
            return run_dp_sharded_mrope_vision_model(
                self.visual, pixel_values_videos, grid_thw_list, rope_type="rope_3d"
            )
        else:
            video_embeds = self.visual(pixel_values_videos, grid_thw=grid_thw)

        # Split concatenated embeddings for each video item.
        merge_size = self.visual.spatial_merge_size
        sizes = (grid_thw.prod(-1) // merge_size // merge_size).tolist()
        return video_embeds.split(sizes)

    def _postprocess_image_embeds_evs(
        self,
        image_embeds_split: tuple[torch.Tensor, ...],
        image_input: Qwen2_5_VLImageInputs,
    ) -> tuple[torch.Tensor, ...]:
        from vllm.multimodal.evs_qwen3_ops import postprocess_image_embeds_evs

        return postprocess_image_embeds_evs(self, image_embeds_split, image_input)

    def _postprocess_video_embeds_evs(
        self,
        video_embeds_split: tuple[torch.Tensor, ...],
        video_input: Qwen2_5_VLVideoInputs,
    ) -> tuple[tuple[torch.Tensor, ...], tuple[torch.Tensor | None, ...]]:
        from vllm.multimodal.evs_qwen3_ops import postprocess_video_embeds_evs

        return postprocess_video_embeds_evs(self, video_embeds_split, video_input)

    def _create_final_video_embeddings(
        self,
        video_embeddings: torch.Tensor,
        num_tokens_per_frame: list[int],
        timestamps: list[float],
        video_grid_thw: list[int],
        retention_mask: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor | None]:
        from vllm.multimodal.evs_qwen3_ops import create_final_video_embeddings

        return create_final_video_embeddings(
            self,
            video_embeddings,
            num_tokens_per_frame,
            timestamps,
            video_grid_thw,
            retention_mask,
        )

    def _get_expanded_positions(
        self,
        device,
        seq_len,
        video_grid_thw,
        num_tokens_per_frame,
        timestamps,
        is_video_embed,
        is_vision_start,
        retention_mask,
    ):
        from vllm.multimodal.evs_qwen3_ops import get_expanded_positions

        return get_expanded_positions(
            self,
            device,
            seq_len,
            video_grid_thw,
            num_tokens_per_frame,
            timestamps,
            is_video_embed,
            is_vision_start,
            retention_mask,
        )

    def _parse_and_validate_multimodal_inputs(self, **kwargs: object) -> dict:
        mm_input_by_modality = {}
        for input_key in kwargs:
            if (
                input_key in ("pixel_values", "image_embeds")
                and "image" not in mm_input_by_modality
            ):
                mm_input_by_modality["image"] = self._parse_and_validate_image_input(
                    **kwargs
                )
            if (
                input_key in ("pixel_values_videos", "video_embeds")
                and "video" not in mm_input_by_modality
            ):
                mm_input_by_modality["video"] = self._parse_and_validate_video_input(
                    **kwargs
                )
        return mm_input_by_modality

    @staticmethod
    def _iter_mm_grid_hw(
        input_tokens: list[int],
        mm_features: list[MultiModalFeatureSpec],
        video_token_id: int,
        vision_start_token_id: int,
        vision_end_token_id: int,
        spatial_merge_size: int,
    ) -> Iterator[tuple[int, int, int, int]]:
        """Iterate over multimodal features and yield position info.

        Args:
            input_tokens: List of token IDs in the input sequence.
            mm_features: List of multimodal feature specifications containing
                image/video data and position information.
            video_token_id: Token ID used for video tokens.
            vision_start_token_id: Token ID marking the start of a vision sequence.
            vision_end_token_id: Token ID marking the end of a vision sequence.
            spatial_merge_size: Size of the spatial merge operation used to
                compute logical grid dimensions from the original feature grid.

        Yields:
            offset: Position of the first video/image token in the sequence.
            llm_grid_h: Logical grid height (may not match actual token count with EVS).
            llm_grid_w: Logical grid width (may not match actual token count with EVS).
            actual_num_tokens: Actual number of video/image tokens in the placeholder.
        """
        for mm_feature in sorted(mm_features, key=lambda f: f.mm_position.offset):
            offset = mm_feature.mm_position.offset
            if mm_feature.data is None:
                continue
            if mm_feature.modality == "image":
                t, h, w = mm_feature.data["image_grid_thw"].data.tolist()
                assert t == 1, f"Image must have 1 frame, got {t}"
                llm_grid_h = h // spatial_merge_size
                llm_grid_w = w // spatial_merge_size
                yield offset, llm_grid_h, llm_grid_w, llm_grid_h * llm_grid_w
            elif mm_feature.modality == "video":
                t, h, w = mm_feature.data["video_grid_thw"].data.tolist()
                llm_grid_h = h // spatial_merge_size
                llm_grid_w = w // spatial_merge_size

                for _ in range(t):
                    # When EVS is enabled, some frames may have 0 video tokens in the
                    # placeholder. We use `vision_start_token_id` to locate each frame
                    # since it is always present for every frame.
                    # We then look for the first `video_token_id` after
                    # `vision_start_token_id` and before `vision_end_token_id`.
                    offset = input_tokens.index(vision_start_token_id, offset)
                    vision_end_offset = input_tokens.index(vision_end_token_id, offset)

                    try:
                        actual_num_tokens = 0
                        video_offset = input_tokens.index(
                            video_token_id, offset, vision_end_offset
                        )
                        # NOTE: looking at the
                        # `Qwen3VLMultiModalProcessor.get_video_repl` code, we can
                        # see that we can use the below formula to get the token
                        # count, since everything in between `video_offset` and
                        # `vision_end_offset` is populated as `video_token_id`.
                        # This saves us from manually counting the number tokens
                        # that match `video_token_id` in between.
                        actual_num_tokens += vision_end_offset - video_offset
                    except ValueError:
                        # No `video_token_id` in this frame (EVS with 0 tokens for
                        # this frame) -> use `offset + 1`` to move past
                        # `vision_start_token_id`.
                        video_offset = offset + 1

                    yield video_offset, llm_grid_h, llm_grid_w, actual_num_tokens
                    # Move offset past this frame for next iteration.
                    offset = vision_end_offset + 1
            else:
                raise ValueError(f"Unsupported modality: {mm_feature.modality}")

    def _get_evs_mask_segments(
        self, mm_position: PlaceholderRange, expected_frames: int
    ) -> list[torch.Tensor] | None:
        from vllm.multimodal.evs_qwen3_ops import get_evs_mask_segments

        return get_evs_mask_segments(mm_position, expected_frames)

    def _extract_frame_offsets_from_mask(
        self, mm_position: PlaceholderRange, expected_frames: int
    ) -> list[int] | None:
        from vllm.multimodal.evs_qwen3_ops import extract_frame_offsets_from_mask

        return extract_frame_offsets_from_mask(mm_position, expected_frames)

    def _get_actual_frame_token_counts(
        self, mm_position: PlaceholderRange, expected_frames: int
    ) -> list[int] | None:
        from vllm.multimodal.evs_qwen3_ops import get_actual_frame_token_counts

        return get_actual_frame_token_counts(mm_position, expected_frames)

    def get_mrope_input_positions(
        self,
        input_tokens: list[int],
        mm_features: list[MultiModalFeatureSpec],
    ) -> tuple[torch.Tensor, int]:
        return self._get_mrope_input_positions(
            input_tokens=input_tokens,
            mm_features=mm_features,
            config=self.config,
        )

    @staticmethod
    def _get_mrope_input_positions(
        input_tokens: list[int],
        mm_features: list[MultiModalFeatureSpec],
        config: Qwen3VLConfig,
    ):
        llm_pos_ids_list = []
        st = 0
        for (
            offset,
            llm_grid_h,
            llm_grid_w,
            actual_num_tokens,
        ) in Qwen3VLForConditionalGeneration._iter_mm_grid_hw(
            input_tokens,
            mm_features,
            video_token_id=config.video_token_id,
            vision_start_token_id=config.vision_start_token_id,
            vision_end_token_id=config.vision_end_token_id,
            spatial_merge_size=config.vision_config.spatial_merge_size,
        ):
            # Skip frames with 0 tokens (EVS placeholder with tokens lumped elsewhere)
            if actual_num_tokens == 0:
                continue

            text_len = offset - st
            st_idx = llm_pos_ids_list[-1].max() + 1 if len(llm_pos_ids_list) > 0 else 0
            llm_pos_ids_list.append(
                np.broadcast_to(np.arange(text_len), (3, text_len)) + st_idx
            )

            # Check if this is a "lumped placeholder" (all tokens from multiple frames
            # assigned to the 0-th frame - see
            # `Qwen3VLMultiModalProcessor.get_video_repl`.
            expected_tokens_per_frame = llm_grid_h * llm_grid_w
            if actual_num_tokens > expected_tokens_per_frame:
                # Lumped placeholder: create grid positions for all "logical" frames
                # represented.
                num_logical_frames = actual_num_tokens // expected_tokens_per_frame
                remainder = actual_num_tokens % expected_tokens_per_frame

                # Create positions for complete frames.
                for _ in range(num_logical_frames):
                    grid_indices = np.indices((1, llm_grid_h, llm_grid_w)).reshape(
                        3, -1
                    )
                    llm_pos_ids_list.append(grid_indices + text_len + st_idx)
                    st_idx = llm_pos_ids_list[-1].max() + 1
                    text_len = 0  # No text between frames within the lump

                # Handle remainder tokens if any (partial frame).
                # NOTE: this should never be the case. Should we have an assert?
                if remainder > 0:
                    full_grid = np.indices((1, llm_grid_h, llm_grid_w)).reshape(3, -1)
                    grid_indices = full_grid[:, :remainder]
                    llm_pos_ids_list.append(grid_indices + text_len + st_idx)
            else:
                # Normal case: frame has exactly the expected tokens (after actual EVS
                # pruning).
                grid_indices = np.indices((1, llm_grid_h, llm_grid_w)).reshape(3, -1)
                llm_pos_ids_list.append(grid_indices + text_len + st_idx)

            st = offset + actual_num_tokens

        if st < len(input_tokens):
            st_idx = llm_pos_ids_list[-1].max() + 1 if len(llm_pos_ids_list) > 0 else 0
            text_len = len(input_tokens) - st
            llm_pos_ids_list.append(
                np.broadcast_to(np.arange(text_len), (3, text_len)) + st_idx
            )

        llm_positions = np.concatenate(llm_pos_ids_list, axis=1).reshape(3, -1)
        mrope_position_delta = (llm_positions.max() + 1 - len(input_tokens)).item()
        return torch.from_numpy(llm_positions), mrope_position_delta

    def recompute_mrope_positions(
        self,
        input_ids: list[int],
        multimodal_embeddings: MultiModalEmbeddings,
        multimodal_positions: Sequence[torch.Tensor] | None,
        mrope_positions: torch.LongTensor,
        num_computed_tokens: int,
        multimodal_offsets: Sequence[int | None] | None = None,
    ) -> tuple[MultiModalEmbeddings, torch.Tensor, int]:
        """
        Update part of input mrope positions (starting with
        num_computed_tokens index). Original mrope_positions are computed
        for unpruned sequence and becomes incorrect once pruning occurs,
        so once we prune media tokens we should reflect this in the
        mrope_positions before we feed it to LLM.

        Args:
            input_ids: (N,) All input tokens of the prompt containing
                entire sequence.
            multimodal_embeddings: Tuple of multimodal embeddings that
                fits into the prefill chunk that is being processed.
            multimodal_positions: Separate position tensors (long) for
                each embedding item, or None to strip from embeddings.
            mrope_positions: Existing mrope positions (3, N) for entire
                sequence
            num_computed_tokens: A number of computed tokens so far.
            multimodal_offsets: Where each item's placeholder begins in the
                prompt, aligned with the embeddings. Required to place an item
                that a prefill chunk split.

        Returns:
            Tuple of (multimodal_embeddings, mrope_positions,
                mrope_position_delta).
        """
        return self._recompute_mrope_positions(
            input_ids=input_ids,
            multimodal_embeddings=multimodal_embeddings,
            multimodal_positions=multimodal_positions,
            mrope_positions=mrope_positions,
            num_computed_tokens=num_computed_tokens,
            image_token_id=self.config.image_token_id,
            video_token_id=self.config.video_token_id,
            vision_start_token_id=self.config.vision_start_token_id,
            multimodal_offsets=multimodal_offsets,
        )

    @staticmethod
    def _recompute_mrope_positions(
        input_ids: list[int],
        multimodal_embeddings: MultiModalEmbeddings,
        multimodal_positions: Sequence[torch.Tensor] | None,
        mrope_positions: torch.LongTensor,
        num_computed_tokens: int,
        vision_start_token_id: int,
        image_token_id: int,
        video_token_id: int,
        multimodal_offsets: Sequence[int | None] | None = None,
    ) -> tuple[MultiModalEmbeddings, torch.Tensor, int]:
        from vllm.multimodal.evs_qwen3_ops import recompute_mrope_positions_evs

        return recompute_mrope_positions_evs(
            input_ids=input_ids,
            multimodal_embeddings=multimodal_embeddings,
            multimodal_positions=multimodal_positions,
            mrope_positions=mrope_positions,
            num_computed_tokens=num_computed_tokens,
            vision_start_token_id=vision_start_token_id,
            image_token_id=image_token_id,
            video_token_id=video_token_id,
            multimodal_offsets=multimodal_offsets,
        )

    def embed_multimodal(
        self,
        **kwargs: object,
    ) -> MultiModalEmbeddingReturn:
        mm_input_by_modality = self._parse_and_validate_multimodal_inputs(**kwargs)
        if not mm_input_by_modality:
            return None

        # The result multimodal_embeddings is tuple of tensors, with each
        # tensor corresponding to a multimodal data item (image or video).
        multimodal_embeddings: list[torch.Tensor] = []
        multimodal_positions: list[torch.Tensor | None] = []

        # NOTE: It is important to iterate over the keys in this dictionary
        # to preserve the order of the modalities.
        for modality in mm_input_by_modality:
            multimodal_input = mm_input_by_modality[modality]
            if modality == "image":
                image_embeddings = self._process_image_input(multimodal_input)
                if self.is_multimodal_pruning_enabled:
                    image_embeddings = self._postprocess_image_embeds_evs(
                        image_embeddings, multimodal_input
                    )
                multimodal_embeddings.extend(image_embeddings)
                multimodal_positions.extend([None] * len(image_embeddings))
            if modality == "video":
                is_precomputed = multimodal_input["type"] == "video_embeds"
                if is_precomputed and self.is_multimodal_pruning_enabled:
                    video_embeddings = self._process_video_input(multimodal_input)
                    video_embeddings, video_positions = (
                        self._postprocess_precomputed_evs(
                            video_embeddings, multimodal_input
                        )
                    )
                    multimodal_embeddings.extend(video_embeddings)
                    multimodal_positions.extend(video_positions)
                else:
                    video_embeddings = self._process_video_input(multimodal_input)
                    if self.is_multimodal_pruning_enabled:
                        video_embeddings, video_positions = (
                            self._postprocess_video_embeds_evs(
                                video_embeddings, multimodal_input
                            )
                        )
                        multimodal_embeddings.extend(video_embeddings)
                        multimodal_positions.extend(video_positions)
                    else:
                        multimodal_embeddings.extend(video_embeddings)
                        multimodal_positions.extend([None] * len(video_embeddings))

        embeddings_tuple = tuple(multimodal_embeddings)
        positions_tuple = tuple(multimodal_positions)
        has_positions = any(pos is not None for pos in positions_tuple)

        if has_positions:
            return MultiModalEmbeddingOutput(
                embeddings=embeddings_tuple,
                positions=positions_tuple,
            )
        return embeddings_tuple

    def _postprocess_precomputed_evs(
        self,
        video_embeds_split: tuple[torch.Tensor, ...],
        video_input: Qwen2_5_VLVideoInputs,
    ) -> tuple[tuple[torch.Tensor, ...], tuple[torch.Tensor | None, ...]]:
        from vllm.multimodal.evs_qwen3_ops import postprocess_precomputed_evs

        return postprocess_precomputed_evs(self, video_embeds_split, video_input)

    def _compute_deepstack_embeds(
        self,
        inputs_embeds: torch.Tensor,
        multimodal_embeddings: MultiModalEmbeddings,
        is_multimodal: torch.Tensor,
    ) -> tuple[torch.Tensor, MultiModalEmbeddings]:
        visual_lens = [len(x) for x in multimodal_embeddings]
        multimodal_embeddings_cat = torch.cat(multimodal_embeddings, dim=0)

        (
            multimodal_embeddings_main,
            multimodal_embeddings_multiscale,
        ) = torch.split(
            multimodal_embeddings_cat,
            [self.visual_dim, self.multiscale_dim],
            dim=-1,
        )

        multimodal_embeddings = torch.split(
            multimodal_embeddings_main, visual_lens, dim=0
        )
        multimodal_embeddings_multiscale = torch.split(
            multimodal_embeddings_multiscale, visual_lens, dim=0
        )

        deepstack_input_embeds = inputs_embeds.new_zeros(
            inputs_embeds.size(0), self.deepstack_num_level * inputs_embeds.size(1)
        )

        deepstack_input_embeds = _merge_multimodal_embeddings(
            inputs_embeds=deepstack_input_embeds,
            multimodal_embeddings=multimodal_embeddings_multiscale,
            is_multimodal=is_multimodal,
        )
        deepstack_input_embeds = deepstack_input_embeds.view(
            inputs_embeds.shape[0], self.deepstack_num_level, self.visual_dim
        )
        deepstack_input_embeds = deepstack_input_embeds.permute(1, 0, 2)

        return deepstack_input_embeds, multimodal_embeddings

    def embed_input_ids(
        self,
        input_ids: torch.Tensor,
        multimodal_embeddings: MultiModalEmbeddings | None = None,
        *,
        is_multimodal: torch.Tensor | None = None,
        handle_oov_mm_token: bool = False,
    ) -> torch.Tensor:
        inputs_embeds = self._embed_text_input_ids(
            input_ids,
            self.language_model.embed_input_ids,
            is_multimodal=is_multimodal,
            handle_oov_mm_token=handle_oov_mm_token,
        )

        if multimodal_embeddings is None or len(multimodal_embeddings) == 0:
            return inputs_embeds

        is_multimodal = _require_is_multimodal(is_multimodal)

        if self.use_deepstack:
            (
                deepstack_input_embeds,
                multimodal_embeddings,
            ) = self._compute_deepstack_embeds(
                inputs_embeds=inputs_embeds,
                multimodal_embeddings=multimodal_embeddings,
                is_multimodal=is_multimodal,
            )
        else:
            deepstack_input_embeds = None

        inputs_embeds = _merge_multimodal_embeddings(
            inputs_embeds=inputs_embeds,
            multimodal_embeddings=multimodal_embeddings,
            is_multimodal=is_multimodal,
        )

        if deepstack_input_embeds is not None:
            self._set_deepstack_input_embeds(deepstack_input_embeds)

        return inputs_embeds

    def forward(
        self,
        input_ids: torch.Tensor | None,
        positions: torch.Tensor,
        intermediate_tensors: IntermediateTensors | None = None,
        inputs_embeds: torch.Tensor | None = None,
        **kwargs: object,
    ) -> torch.Tensor | IntermediateTensors:
        """Run forward pass for Qwen3VL.

        Args:
            input_ids: Flattened (concatenated) input_ids corresponding to a
                batch.
            positions: Flattened (concatenated) position ids corresponding to a
                batch.
                **NOTE**: If mrope is enabled (default setting for Qwen3VL
                opensource models), the shape will be `(3, seq_len)`,
                otherwise it will be `(seq_len,).
            intermediate_tensors: Intermediate tensors from previous pipeline
                stages.
            inputs_embeds: Pre-computed input embeddings.
            **kwargs: Additional keyword arguments including:
                - pixel_values: Pixel values to be fed to a model.
                    `None` if no images are passed.
                - image_grid_thw: Tensor `(n_images, 3)` of image 3D grid in
                    LLM. `None` if no images are passed.
                - pixel_values_videos: Pixel values of videos to be fed to a
                    model. `None` if no videos are passed.
                - video_grid_thw: Tensor `(n_videos, 3)` of video 3D grid in
                    LLM. `None` if no videos are passed.
        """

        if intermediate_tensors is not None:
            inputs_embeds = None

        if inputs_embeds is not None and get_pp_group().is_first_rank:
            deepstack_input_embeds = self._get_deepstack_input_embeds(
                inputs_embeds.size(0)
            )
        else:
            deepstack_input_embeds = None

        hidden_states = self.language_model.model(
            input_ids=input_ids,
            positions=positions,
            intermediate_tensors=intermediate_tensors,
            inputs_embeds=inputs_embeds,
            # args for deepstack
            deepstack_input_embeds=deepstack_input_embeds,
        )

        if inputs_embeds is not None and get_pp_group().is_first_rank:
            self._clear_deepstack_input_embeds(inputs_embeds.size(0))

        return hidden_states

    def compute_logits(
        self,
        hidden_states: torch.Tensor,
    ) -> torch.Tensor | None:
        return self.language_model.compute_logits(hidden_states)

    def load_weights(self, weights: Iterable[tuple[str, torch.Tensor]]) -> set[str]:
        loader = AutoWeightsLoader(self)
        return loader.load_weights(weights, mapper=self.hf_to_vllm_mapper)

    def get_mm_mapping(self) -> MultiModelKeys:
        """
        Get the module prefix in multimodal models
        """
        return MultiModelKeys.from_string_field(
            language_model="language_model",
            connector=["visual.merger", "visual.deepstack_merger_list"],
            tower_model="visual.",
        )

    def get_num_mm_encoder_tokens(
        self,
        num_image_tokens: int,
    ) -> int:
        hf_config = self.config
        vision_config = hf_config.vision_config
        merge_size = vision_config.spatial_merge_size

        return num_image_tokens * merge_size**2

    def get_num_mm_connector_tokens(
        self,
        num_vision_tokens: int,
    ) -> int:
        hf_config = self.config
        vision_config = hf_config.vision_config
        merge_size = vision_config.spatial_merge_size
        return num_vision_tokens // merge_size**2


@lru_cache
def _cached_tensor(x, device) -> torch.Tensor:
    return torch.tensor(x, device=device)


def _make_dummy_mm_feature(
    video_grid_thw: list[int] | tuple[int, ...],
    length: int,
) -> MultiModalFeatureSpec:
    return MultiModalFeatureSpec(
        data=MultiModalKwargsItem(
            {
                "video_grid_thw": MultiModalFieldElem(
                    data=torch.tensor(video_grid_thw),
                    field=None,
                ),
            }
        ),
        modality="video",
        identifier="DUMMY",
        mm_position=PlaceholderRange(offset=0, length=length),
    )
