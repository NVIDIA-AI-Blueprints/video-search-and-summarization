# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

"""Deterministic Flash SDPA for single-request Cosmos3 Edge inference."""

from torch.nn import functional as F
from torch.nn.attention import SDPBackend, sdpa_kernel
from vllm.v1.attention.backends.triton_attn import (
    TritonAttentionBackend,
    TritonAttentionImpl,
)


class EdgeFlashAttentionImpl(TritonAttentionImpl):
    def forward(
        self,
        layer,
        query,
        key,
        value,
        kv_cache,
        attn_metadata,
        output=None,
        output_scale=None,
        output_block_scale=None,
    ):
        supported = (
            attn_metadata is not None
            and output is not None
            and output_scale is None
            and output_block_scale is None
            and attn_metadata.query_start_loc.shape[0] == 2
            and self.kv_cache_dtype == "auto"
            and self.sliding_window == (-1, -1)
            and self.alibi_slopes is None
            and self.logits_soft_cap == 0
        )
        if supported and attn_metadata.max_query_len > 1:
            count = attn_metadata.num_actual_tokens
            if attn_metadata.max_query_len == attn_metadata.max_seq_len:
                with sdpa_kernel(SDPBackend.FLASH_ATTENTION):
                    result = F.scaled_dot_product_attention(
                        query[:count].contiguous().unsqueeze(0).transpose(1, 2),
                        key[:count].contiguous().unsqueeze(0).transpose(1, 2),
                        value[:count].contiguous().unsqueeze(0).transpose(1, 2),
                        dropout_p=0.0,
                        is_causal=True,
                        scale=self.scale,
                        enable_gqa=self.num_heads > self.num_kv_heads,
                    )
                output[:count].copy_(result.squeeze(0).transpose(0, 1))
                return output

        if supported and attn_metadata.max_query_len == 1:
            sequence_length = attn_metadata.max_seq_len
            key_cache, value_cache = kv_cache.unbind(1)
            block_size = key_cache.shape[1]
            block_count = (sequence_length + block_size - 1) // block_size
            block_ids = attn_metadata.block_table[0, :block_count].long()
            cached_key = key_cache[block_ids].reshape(-1, self.num_kv_heads, self.head_size)[
                :sequence_length
            ]
            cached_value = value_cache[block_ids].reshape(-1, self.num_kv_heads, self.head_size)[
                :sequence_length
            ]
            with sdpa_kernel(SDPBackend.FLASH_ATTENTION):
                result = F.scaled_dot_product_attention(
                    query[:1].contiguous().unsqueeze(0).transpose(1, 2),
                    cached_key.transpose(0, 1).unsqueeze(0).contiguous(),
                    cached_value.transpose(0, 1).unsqueeze(0).contiguous(),
                    dropout_p=0.0,
                    is_causal=False,
                    scale=self.scale,
                    enable_gqa=self.num_heads > self.num_kv_heads,
                )
            output[:1].copy_(result.squeeze(0).transpose(0, 1))
            return output

        return super().forward(
            layer,
            query,
            key,
            value,
            kv_cache,
            attn_metadata,
            output,
            output_scale,
            output_block_scale,
        )


class EdgeFlashAttentionBackend(TritonAttentionBackend):
    @staticmethod
    def get_name() -> str:
        return "CUSTOM"

    @staticmethod
    def get_impl_cls() -> type[EdgeFlashAttentionImpl]:
        return EdgeFlashAttentionImpl
