# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Merge a Cosmos-RL LoRA export into a standalone HF CR3 checkpoint."""

from __future__ import annotations

import argparse

import torch
from peft import PeftConfig, PeftModel
from transformers import AutoModelForImageTextToText, AutoProcessor


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-model", default="/models/Cosmos3-Nano-VLM")
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--merge-dtype",
        choices=("float32", "float16", "bfloat16"),
        default="bfloat16",
        help="Dtype used while adding the LoRA delta to base weights.",
    )
    parser.add_argument(
        "--save-dtype",
        choices=("float32", "float16", "bfloat16"),
        default=None,
        help="Optional output dtype after merging (defaults to merge dtype).",
    )
    args = parser.parse_args()

    dtypes = {
        "float32": torch.float32,
        "float16": torch.float16,
        "bfloat16": torch.bfloat16,
    }
    merge_dtype = dtypes[args.merge_dtype]
    save_dtype = dtypes[args.save_dtype or args.merge_dtype]

    model = AutoModelForImageTextToText.from_pretrained(
        args.base_model,
        dtype=merge_dtype,
        device_map={"": "cpu"},
        local_files_only=True,
    )
    adapter_config = PeftConfig.from_pretrained(args.adapter)
    adapter_config.alpha_pattern = adapter_config.alpha_pattern or {}
    adapter_config.rank_pattern = adapter_config.rank_pattern or {}
    model = PeftModel.from_pretrained(
        model, args.adapter, config=adapter_config, is_trainable=False
    )
    model = model.merge_and_unload(safe_merge=True)
    if save_dtype != merge_dtype:
        model = model.to(dtype=save_dtype)
    # vLLM's `dtype=auto` follows this field.  This matters for FP16 output:
    # recasting the checkpoint to BF16 at load time would discard the small
    # visual LoRA updates that the high-precision merge is meant to preserve.
    model.config.dtype = str(save_dtype).removeprefix("torch.")
    model.save_pretrained(args.output, safe_serialization=True, max_shard_size="5GB")
    AutoProcessor.from_pretrained(args.base_model, local_files_only=True).save_pretrained(
        args.output
    )
    print(args.output)


if __name__ == "__main__":
    main()
